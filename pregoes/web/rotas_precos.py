"""Etapa 3 do pregão — Preços: busca nos fornecedores e escolha por item.

    GET  /pregao/<slug>/precos                          a tela (4 estados)
    POST /pregao/<slug>/precos/buscar                   inicia a tarefa de busca
                                                        ({"faltantes": true} = só os sem resultado)
    POST /pregao/<slug>/precos/refazer                  descarta a busca ({"destino": "precos"|"revisao"})
    POST /pregao/<slug>/precos/escolher                 {"i", "k"} escolhe uma opção
    POST /pregao/<slug>/precos/nao-usar                 {"i", "usar"} deixa sem preço / volta a usar
    GET  /pregao/<slug>/precos/item/<i>/procurar        diálogo das buscas manuais (uma barra por fornecedor)
    POST /pregao/<slug>/precos/item/<i>/catalogo        {"q"} procura no catálogo do cliente
    POST /pregao/<slug>/precos/item/<i>/catalogo/usar   {"produto_id", "fornecedor"} cota ao vivo e usa
    POST /pregao/<slug>/precos/item/<i>/site            {"termo", "fornecedor"} busca ao vivo no site
    POST /pregao/<slug>/precos/item/<i>/site/usar       usa um resultado da busca no site

Decisões:
- As regras (ranking, default, o que aparece, avisos) moram em
  `regras_selecao.py`; a busca em lote em `servico_busca.py`. Aqui só há
  leitura do pedido, gravação no rascunho sob `trava(slug)` e HTML.
- Cada ação devolve os pedaços que mudaram (o cartão do item, os números e a
  faixa do rodapé) — a página não recarrega nem perde a rolagem.
- Filtro, texto e página ficam na query string (não há sessão no servidor).
- O resultado da busca manual no site não é guardado no servidor: vai para a
  página e volta ASSINADO no clique em "Usar", para o preço gravado ser
  exatamente o que o site devolveu.
- Enquanto uma busca em lote do pregão roda, as escolhas ficam bloqueadas: a
  tarefa grava o resultado no fim e não pode encontrar o rascunho mexido.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import math
import secrets
from collections import Counter
from typing import Callable, Optional

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from pregoes import config, credenciais, db, erros, matcher, scraper
from pregoes.web import regras_selecao as regras
from pregoes.web import servico_busca, tarefas
from pregoes.web.nucleo import corpo_json, falha, modelos, ok, pagina, pedaco
from pregoes.web.pregao import Pregao, fornecedores_configurados, trava

_log = logging.getLogger("pregoes.web")

POR_PAGINA = 25
_SITUACOES_VALIDAS = {chave for chave, _ in regras.SITUACOES}

MSG_BUSCA_RODANDO = ("Há uma busca de preços em andamento neste pregão. Aguarde ela "
                     "terminar (ou cancele) para mexer nas escolhas.")


# --------------------------------------------------------------------------
# Apoio
# --------------------------------------------------------------------------

def _acao(fn: Callable) -> Callable:
    """Rota POST com corpo JSON: lê o corpo e roda `fn(request, dados)` fora
    do laço de eventos (as ações gravam em disco e algumas consultam o site)."""
    async def rota(request: Request):
        dados = await corpo_json(request)
        return await run_in_threadpool(fn, request, dados)
    rota.__name__ = fn.__name__
    return rota


def _inteiro(valor, padrao: Optional[int] = None) -> Optional[int]:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


def _indice(pregao: Pregao, valor) -> Optional[int]:
    """Posição válida de um item da busca, ou None."""
    i = _inteiro(valor)
    return i if i is not None and 0 <= i < len(pregao.itens_revisados) else None


def _visao(pregao: Pregao, i: int) -> dict:
    return regras.visao_item(pregao.itens_revisados[i], i, pregao.resultados_busca.get(i),
                             pregao.selecoes.get(i), i in pregao.itens_nao_usar)


def _situacoes(pregao: Pregao) -> list[str]:
    resultados, selecoes, nao_usar = (pregao.resultados_busca, pregao.selecoes,
                                      pregao.itens_nao_usar)
    return [regras.situacao_de(item, resultados.get(i), selecoes.get(i), i in nao_usar)
            for i, item in enumerate(pregao.itens_revisados)]


def _contagens(situacoes: list[str]) -> dict:
    contagens = Counter(situacoes)
    return {chave: (len(situacoes) if chave == "todos" else contagens.get(chave, 0))
            for chave, _ in regras.SITUACOES}


def _resumo(pregao: Pregao) -> dict:
    """Contexto de `precos_resumo.html` e `precos_rodape.html`."""
    n = len(pregao.itens_revisados)
    return {"pregao": pregao,
            "mt": regras.metricas(n, pregao.resultados_busca, pregao.selecoes),
            "n_faltantes": len(regras.faltantes(n, pregao.resultados_busca,
                                                pregao.itens_nao_usar))}


def _pedacos(request: Request, pregao: Pregao, i: int) -> dict:
    """O que muda na tela depois de mexer no item `i`."""
    resumo = _resumo(pregao)
    saida = {
        f"#item-{i}": pedaco(request, "precos_item.html", pregao=pregao, it=_visao(pregao, i)),
        "#precos-resumo": pedaco(request, "precos_resumo.html", **resumo),
        "#precos-rodape": pedaco(request, "precos_rodape.html", **resumo),
    }
    for chave, n in _contagens(_situacoes(pregao)).items():
        saida[f"#q-{chave}"] = f'<span class="q" id="q-{chave}">{n}</span>'
    return saida


def _nao_encontrado(request: Request):
    return pagina(request, "erro.html", status=404, titulo="Pregão não encontrado",
                  mensagem="Este pregão não está mais na lista — pode ter sido descartado "
                           "ou já virou planilha (veja em Planilhas geradas).")


def _aviso_da_ultima_busca(request: Request, slug: str) -> Optional[dict]:
    """O desfecho da última busca deste pregão, quando o operador precisa
    saber: falhou (sempre), descartou resultados (sempre), ou terminou /
    foi cancelada agora (a página chega aqui com `?fim=<tarefa>`)."""
    ultima = next((t for t in tarefas.do_pregao(slug) if t.tipo == "busca"), None)
    if ultima is None or ultima.estado == "rodando":
        return None
    veio_do_fim = request.query_params.get("fim") == ultima.id
    if ultima.estado == "erro":
        return {"tipo": "erro",
                "texto": f"A última busca não terminou: {ultima.erro} O que já tinha sido "
                         "consultado fica na memória de preços — é só buscar de novo."}
    if ultima.estado == "cancelada":
        if not veio_do_fim:
            return None
        return {"tipo": "info",
                "texto": "Busca cancelada. Nada foi alterado neste pregão; o que já tinha "
                         f"sido consultado fica na memória de preços por "
                         f"{config.CACHE_PRECO_DIAS} dias, então recomeçar é rápido."}
    desfecho = ultima.resultado if isinstance(ultima.resultado, dict) else None
    if desfecho and (desfecho.get("tipo") == "alerta" or veio_do_fim):
        return {"tipo": desfecho.get("tipo", "ok"), "texto": desfecho.get("texto", "")}
    return None


# --------------------------------------------------------------------------
# A tela
# --------------------------------------------------------------------------

def precos(request: Request):
    pregao = Pregao.abrir(request.path_params["slug"])
    if pregao is None:
        return _nao_encontrado(request)
    itens = pregao.itens_revisados
    fornecedores = fornecedores_configurados()
    bloqueio = "sem_itens" if not itens else ("sem_fornecedor" if not fornecedores else None)
    ctx: dict = {"pregao": pregao, "etapa": 2, "bloqueio": bloqueio,
                 "fornecedores": fornecedores, "n_itens": len(itens),
                 "cache_dias": config.CACHE_PRECO_DIAS, "tarefa": None,
                 "condicoes": [], "sem_condicao": [], "aviso_tarefa": None}
    if bloqueio:
        return pagina(request, "precos.html", **ctx)

    ctx["condicoes"], ctx["sem_condicao"] = regras.condicoes_pagamento(
        fornecedores, credenciais.carregar_preferencia_pagamento)
    ativa = tarefas.ativa("busca", pregao.slug)
    if ativa is not None:
        ctx["tarefa"] = ativa.retrato()
        # Leva em espera (limite de consultas dos sites): a página já nasce com
        # o aviso; depois o precos.js o mantém em dia pela consulta de progresso.
        espera_s = int(scraper.espera_da_janela())
        ctx["espera_s"], ctx["espera_min"] = espera_s, regras.espera_em_minutos(espera_s)
    else:
        ctx["aviso_tarefa"] = _aviso_da_ultima_busca(request, pregao.slug)
        if pregao.tem_busca:
            ctx.update(_contexto_da_lista(request, pregao))
        else:
            ctx["estimativa"] = regras.estimativa_busca(len(itens), len(fornecedores))
    return pagina(request, "precos.html", **ctx)


def _contexto_da_lista(request: Request, pregao: Pregao) -> dict:
    """Depois da busca: avisos, números, filtros e a página de itens."""
    itens = pregao.itens_revisados
    status = request.query_params.get("status", "todos")
    if status not in _SITUACOES_VALIDAS:
        status = "todos"
    q = (request.query_params.get("q") or "").strip()[:120]

    situacoes = _situacoes(pregao)
    selecoes = pregao.selecoes
    filtrados = [i for i, item in enumerate(itens)
                 if (status == "todos" or situacoes[i] == status)
                 and regras.casa_texto(q, item, i, selecoes.get(i))]
    n_paginas = max(1, math.ceil(len(filtrados) / POR_PAGINA))
    pagina_atual = min(max(_inteiro(request.query_params.get("pagina"), 1), 1), n_paginas)
    inicio = (pagina_atual - 1) * POR_PAGINA
    fatia = filtrados[inicio:inicio + POR_PAGINA]

    erros_busca = [
        {"numero": (regras.numero_item_edital(itens[e.indice_item], e.indice_item)
                    if 0 <= e.indice_item < len(itens) else None),
         "fornecedor": e.fornecedor, "descricao_resumo": e.descricao_resumo,
         "mensagem": e.mensagem}
        for e in (pregao["erros_busca"] or [])]
    avisos = list((pregao["avisos_pagamento"] or {}).values()) + list(
        pregao["avisos_lote_vazio"] or [])
    return {
        **_resumo(pregao), "avisos": avisos, "erros": erros_busca,
        "situacoes": regras.SITUACOES, "contagens": _contagens(situacoes),
        # ("status" é parâmetro de `pagina()` — no modelo o filtro chama-se `situacao`)
        "situacao": status, "q": q, "pagina": pagina_atual, "n_paginas": n_paginas,
        "n_filtrados": len(filtrados), "inicio": inicio, "fim": inicio + len(fatia),
        "cartoes": [_visao(pregao, i) for i in fatia],
    }


# --------------------------------------------------------------------------
# Busca em lote
# --------------------------------------------------------------------------

def _buscar(request: Request, dados: dict):
    slug = request.path_params["slug"]
    pregao = Pregao.abrir(slug)
    if pregao is None:
        return falha("Pregão não encontrado.", 404)
    itens = pregao.itens_revisados
    if not itens:
        return falha("Nenhum item para buscar. Volte para a revisão.")
    if not fornecedores_configurados():
        return falha("Nenhum fornecedor configurado. Vá em Configurações para cadastrar "
                     "Almeida e/ou Dantas.")
    if dados.get("faltantes"):
        if not pregao.tem_busca:
            return falha("Faça a busca de preços primeiro.")
        indices: Optional[list[int]] = regras.faltantes(
            len(itens), pregao.resultados_busca, pregao.itens_nao_usar)
        if not indices:
            return ok(aviso="Não há item sem resultado para buscar.", recarregar=True)
    elif pregao.tem_busca:
        # Busca completa por cima de uma existente trocaria os resultados sem
        # limpar as escolhas: o caminho é "Buscar preços novamente" (refazer).
        return ok(recarregar=True)
    else:
        indices = None
    tarefa = tarefas.iniciar("busca", slug, f"Buscando preços — {pregao.titulo}",
                             lambda t: servico_busca.executar_busca(slug, indices, t))
    return ok(tarefa=tarefa.id, recarregar=True)


def _refazer(request: Request, dados: dict):
    """Os dois caminhos destrutivos do pós-busca: descartam resultados e
    escolhas (as posições podem mudar se a revisão for editada)."""
    slug = request.path_params["slug"]
    if tarefas.ativa("busca", slug) is not None:
        return falha(MSG_BUSCA_RODANDO, 409)
    with trava(slug):
        pregao = Pregao.abrir(slug)
        if pregao is None:
            return falha("Pregão não encontrado.", 404)
        pregao.descartar_busca()
        pregao.salvar()
    destino = "revisao" if dados.get("destino") == "revisao" else "precos"
    return ok(ir=f"/pregao/{slug}/{destino}")


# --------------------------------------------------------------------------
# Escolhas por item
# --------------------------------------------------------------------------

def _alterar_item(request: Request, valor_i, alterar: Callable[[Pregao, int], Optional[str]],
                  **resposta):
    """Abre o pregão sob a trava, aplica `alterar(pregao, i)` (que devolve uma
    mensagem de erro, ou None) e responde com os pedaços atualizados."""
    slug = request.path_params["slug"]
    if tarefas.ativa("busca", slug) is not None:
        return falha(MSG_BUSCA_RODANDO, 409)
    with trava(slug):
        pregao = Pregao.abrir(slug)
        if pregao is None:
            return falha("Pregão não encontrado.", 404)
        if not pregao.tem_busca:
            return falha("Faça a busca de preços primeiro.")
        i = _indice(pregao, valor_i)
        if i is None:
            return falha("Este item não existe mais neste pregão — recarregue a página.")
        problema = alterar(pregao, i)
        if problema:
            return falha(problema)
        pregao.salvar()
        return ok(html=_pedacos(request, pregao, i), **resposta)


def _usar_opcao(pregao: Pregao, i: int, opcao, anexar: bool = False) -> None:
    """Grava a escolha. Usar uma opção SEMPRE tira o item de "não usar" —
    no Streamlit as buscas manuais esqueciam disso e a tela dizia "sem preço"
    para um item que saía com preço na planilha (inventário, ponto 9.4)."""
    if anexar:      # opção vinda de busca manual entra na lista do item
        pregao.estado.setdefault("resultados_busca", {}).setdefault(i, []).append(opcao)
    pregao.selecoes[i] = regras.selecao_de(opcao)
    pregao.itens_nao_usar.discard(i)


def _escolher(request: Request, dados: dict):
    def alterar(pregao: Pregao, i: int) -> Optional[str]:
        opcao = regras.opcao_por_token(pregao.resultados_busca.get(i), str(dados.get("k") or ""))
        if opcao is None:
            return "Esta opção não está mais disponível — recarregue a página."
        _usar_opcao(pregao, i, opcao)
        return None
    return _alterar_item(request, dados.get("i"), alterar)


def _nao_usar(request: Request, dados: dict):
    def alterar(pregao: Pregao, i: int) -> Optional[str]:
        if dados.get("usar"):
            # Só sai do conjunto: a escolha não é recriada (o operador escolhe).
            pregao.itens_nao_usar.discard(i)
        else:
            pregao.itens_nao_usar.add(i)
            pregao.selecoes.pop(i, None)
        return None
    return _alterar_item(request, dados.get("i"), alterar)


# --------------------------------------------------------------------------
# Buscas manuais ("Procurar de outro jeito")
# --------------------------------------------------------------------------

def _item_do_caminho(request: Request) -> tuple[Optional[Pregao], Optional[int]]:
    pregao = Pregao.abrir(request.path_params["slug"])
    if pregao is None:
        return None, None
    return pregao, _indice(pregao, request.path_params["i"])


def _resposta_procura(request: Request, pregao: Pregao, i: int, onde: str, *,
                      aviso: Optional[dict] = None, texto: str = "",
                      lista: Optional[dict] = None):
    """Troca a mensagem da barra `onde` (um fornecedor ou "catalogo") e,
    quando `lista` vem, também a lista dela. Cada barra do diálogo tem o seu
    espaço: buscar na Dantas não apaga o que a Almeida devolveu."""
    saida = {f"#procura-estado-{onde}": pedaco(
        request, "precos_procura_resultado.html", parte="estado", pregao=pregao, i=i,
        onde=onde, aviso=aviso, texto=texto)}
    if lista is not None:
        saida[f"#procura-resultado-{onde}"] = pedaco(
            request, "precos_procura_resultado.html", parte="lista", pregao=pregao, i=i,
            onde=onde, **lista)
    return ok(html=saida)


def procurar(request: Request):
    pregao, i = _item_do_caminho(request)
    if pregao is None or i is None:
        return falha("Este item não existe mais neste pregão — recarregue a página.", 404)
    with db.get_conn() as conn:
        n_catalogo = len(matcher.carregar_catalogo(conn))
    em_lote = any(t.tipo == "busca" for t in tarefas.rodando())
    return HTMLResponse(pedaco(
        request, "precos_procurar.html", pregao=pregao, it=_visao(pregao, i),
        n_catalogo=n_catalogo, fornecedores=fornecedores_configurados(),
        fila_ocupada=em_lote))


def _catalogo_buscar(request: Request, dados: dict):
    pregao, i = _item_do_caminho(request)
    if pregao is None or i is None:
        return falha("Este item não existe mais neste pregão — recarregue a página.", 404)
    consulta = str(dados.get("q") or "").strip()
    configurados = set(fornecedores_configurados())
    with db.get_conn() as conn:
        # Lido a cada pedido (no Streamlit era 1× por sessão e não via o que
        # fosse importado depois).
        catalogo = matcher.carregar_catalogo(conn)
        if not consulta:
            return _resposta_procura(
                request, pregao, i, "catalogo", lista={},
                texto=f"Digite palavras (ex.: 'tubo pvc 75') ou um código para procurar "
                      f"nos seus {len(catalogo)} produtos.")
        achados = regras.buscar_no_catalogo(catalogo, consulta)
        linhas = regras.linhas_do_catalogo(
            achados, lambda produto_id, forn: db.ultima_cotacao(conn, produto_id, forn))
    for linha in linhas:
        linha["configurado"] = linha["fornecedor"] in configurados
    if not linhas:
        return _resposta_procura(
            request, pregao, i, "catalogo", lista={},
            texto="Nenhum produto encontrado no seu catálogo. Tente outras palavras ou o "
                  "código do fornecedor.")
    return _resposta_procura(request, pregao, i, "catalogo", lista={"linhas": linhas})


def _catalogo_usar(request: Request, dados: dict):
    """Cota o produto do catálogo AO VIVO pelo código e usa. NUNCA grava o
    preço histórico: falhou a cotação, nada é gravado (app.py
    `_usar_do_catalogo`, L2905)."""
    pregao, i = _item_do_caminho(request)
    if pregao is None or i is None:
        return falha("Este item não existe mais neste pregão — recarregue a página.", 404)
    forn = str(dados.get("fornecedor") or "")
    produto_id = _inteiro(dados.get("produto_id"))
    with db.get_conn() as conn:
        produto = next((p for p in matcher.carregar_catalogo(conn)
                        if p.produto_id == produto_id), None)
    codigo = ""
    if produto is not None and forn in ("almeida", "dantas"):
        codigo = str((produto.codigo_almeida if forn == "almeida"
                      else produto.codigo_dantas) or "").strip()
    if not codigo:
        return falha("Este produto não está mais no catálogo com esse código — procure de novo.")
    try:
        ao_vivo = scraper.cotar_por_codigo(forn, codigo)
    except Exception as exc:
        _log.exception("falha ao cotar por código no catálogo")
        return _resposta_procura(request, pregao, i, "catalogo", aviso={"tipo": "alerta", "html": (
            "Não consegui consultar o preço atual agora "
            f"({html.escape(erros.mensagem_amigavel(exc))}). O preço do catálogo "
            "<b>não</b> é usado. Tente de novo em instantes.")})
    if ao_vivo is None:
        return _resposta_procura(request, pregao, i, "catalogo", aviso={"tipo": "alerta", "html": (
            f"O código <code>{html.escape(codigo)}</code> não foi encontrado agora no site "
            f"da {forn.capitalize()} — o produto pode ter saído de linha. Confira "
            "diretamente no site antes de usar.")})
    # Preço e marca vêm do site (atuais); o vínculo com o catálogo é mantido
    # para a pílula "no seu catálogo".
    novo = scraper.ResultadoBusca(
        descricao_encontrada=ao_vivo.descricao_encontrada or produto.descricao_canonica or "",
        marca=ao_vivo.marca or produto.marca or "", preco=ao_vivo.preco,
        codigo_fornecedor=codigo, confianca="exato", fornecedor=forn,
        url=getattr(ao_vivo, "url", None), no_catalogo=True,
        produto_catalogo_id=produto.produto_id, descricao_catalogo=produto.descricao_canonica)
    return _alterar_item(request, i, lambda p, pos: _usar_opcao(p, pos, novo, anexar=True),
                         fechar_modal=True,
                         aviso=f"Preço atual aplicado: {novo.marca or 'Sem marca'} · "
                               f"{modelos.env.filters['brl'](novo.preco)}")


def _site_buscar(request: Request, dados: dict):
    """Busca crua no site de UM fornecedor, por nome ou código (app.py
    `_busca_site_item`, L2953)."""
    pregao, i = _item_do_caminho(request)
    if pregao is None or i is None:
        return falha("Este item não existe mais neste pregão — recarregue a página.", 404)
    termo = str(dados.get("termo") or "").strip()
    forn = str(dados.get("fornecedor") or "")
    if forn not in fornecedores_configurados():
        return falha("Este fornecedor não está configurado — cadastre o acesso em Configurações.")
    if not termo:
        return _resposta_procura(request, pregao, i, forn, lista={},
                                 texto="Digite um nome ou código para buscar.")
    try:
        achados = scraper.buscar_no_site(forn, termo)
    except Exception as exc:
        if getattr(exc, "status", None) == scraper._STATUS_LIMITE:
            # Limite de consultas do site: não é erro nosso nem "não existe".
            texto = (f"O site da {forn.capitalize()} limitou as consultas por alguns "
                     "minutos (muitas buscas em pouco tempo). <b>Aguarde uns 5 minutos</b> "
                     "e busque de novo.")
        else:
            _log.exception("falha na busca manual no site")
            texto = ("Não consegui buscar no site agora "
                     f"({html.escape(erros.mensagem_amigavel(exc))}). Tente de novo em "
                     "instantes.")
        return _resposta_procura(request, pregao, i, forn, lista={},
                                 aviso={"tipo": "alerta", "html": texto})
    if not achados:
        return _resposta_procura(
            request, pregao, i, forn, lista={},
            texto=f"Nada encontrado na {forn.capitalize()} para esse termo. Tente outras "
                  "palavras ou o código. Se o produto deveria existir, o site do fornecedor "
                  "pode estar instável neste momento — tente de novo em alguns minutos.")
    return _resposta_procura(request, pregao, i, forn, lista={
        "fornecedor": forn,
        "achados": [{"r": r, "pacote": _embalar(r)} for r in achados]})


def _site_usar(request: Request, dados: dict):
    opcao = _desembalar(dados)
    if opcao is None:
        return falha("Esta busca não vale mais (o sistema foi reiniciado). Busque de novo "
                     "no site e clique em Usar.")
    return _alterar_item(
        request, request.path_params["i"],
        lambda p, pos: _usar_opcao(p, pos, opcao, anexar=True), fechar_modal=True,
        aviso=f"Opção do site aplicada: {opcao.marca or 'Sem marca'} · "
              f"{modelos.env.filters['brl'](opcao.preco)}")


# O resultado da busca no site vai para a página e volta no "Usar". A
# assinatura (segredo do processo) garante que volta igual ao que o site
# devolveu — ninguém grava preço digitado por fora.
_SEGREDO = secrets.token_bytes(32)
_CAMPOS_SITE = ("descricao_encontrada", "marca", "preco", "codigo_fornecedor",
                "confianca", "fornecedor", "ncm", "url")


def _assinar(campos: dict) -> str:
    normal = {c: campos.get(c) for c in _CAMPOS_SITE}
    normal["preco"] = float(normal["preco"])      # 10 e 10.0 assinam igual
    bruto = json.dumps(normal, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hmac.new(_SEGREDO, bruto, hashlib.sha256).hexdigest()


def _embalar(r) -> dict:
    campos = {c: getattr(r, c, None) for c in _CAMPOS_SITE}
    return {"r": campos, "a": _assinar(campos)}


def _desembalar(dados: dict):
    campos = dados.get("r")
    try:
        if not isinstance(campos, dict) or not hmac.compare_digest(
                _assinar(campos), str(dados.get("a") or "")):
            return None
        return scraper.ResultadoBusca(**{c: campos.get(c) for c in _CAMPOS_SITE
                                         if c != "preco"}, preco=float(campos["preco"]))
    except (KeyError, TypeError, ValueError):
        return None


rotas: list = [
    Route("/pregao/{slug}/precos", precos),
    Route("/pregao/{slug}/precos/buscar", _acao(_buscar), methods=["POST"]),
    Route("/pregao/{slug}/precos/refazer", _acao(_refazer), methods=["POST"]),
    Route("/pregao/{slug}/precos/escolher", _acao(_escolher), methods=["POST"]),
    Route("/pregao/{slug}/precos/nao-usar", _acao(_nao_usar), methods=["POST"]),
    Route("/pregao/{slug}/precos/item/{i}/procurar", procurar),
    Route("/pregao/{slug}/precos/item/{i}/catalogo", _acao(_catalogo_buscar), methods=["POST"]),
    Route("/pregao/{slug}/precos/item/{i}/catalogo/usar", _acao(_catalogo_usar), methods=["POST"]),
    Route("/pregao/{slug}/precos/item/{i}/site", _acao(_site_buscar), methods=["POST"]),
    Route("/pregao/{slug}/precos/item/{i}/site/usar", _acao(_site_usar), methods=["POST"]),
]
