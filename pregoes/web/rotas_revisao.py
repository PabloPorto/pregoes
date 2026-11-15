"""Telas de Revisão dos itens e de Mapeamento manual (etapa 2 do pregão).

Modelo de persistência (corrige o defeito do inventário 3.3): a grade da
revisão É `itens_edital`. Cada célula alterada vira um POST que troca aquele
campo daquele item, sob `trava(slug)`, e regrava as três listas com
`Pregao.definir_itens(itens_edital, marcados)`. Nada fica só na aba: fechar o
navegador, recarregar ou o colega abrir o mesmo pregão mostra o mesmo estado.

REGRA DOS ÍNDICES. Os resultados da busca, as escolhas, os "não usar" e os
ajustes de margem são guardados pela POSIÇÃO do item na lista de busca. Mudar
quais itens estão marcados, incluir ou excluir linhas desloca essas posições
e faria o preço de um item aparecer em outro. Por isso, num pregão que já tem
busca, essas três operações são RECUSADAS, a menos que o pedido traga
`confirmar: true` — e aí a busca é descartada antes (`descartar_busca`).
Corrigir descrição, unidade, quantidade ou referência não muda posição de
ninguém: nunca descarta nada. Com a busca RODANDO, nem com confirmação (a
tarefa gravaria os resultados nas posições antigas).

Cada célula enviada leva `n` (quantas linhas a página tinha). Se a lista
mudou de tamanho desde então (o colega excluiu uma linha, ou a página ficou
velha), o servidor recusa em vez de gravar na linha errada.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from pregoes import config, db, extrator, extrator_docling, mapeamento_manual
from pregoes.web import regras_revisao as regras
from pregoes.web import servicos, tarefas
from pregoes.web.nucleo import RAIZ, corpo_json, falha, ok, pagina, pedaco
from pregoes.web.pregao import Pregao, fornecedores_configurados, trava

_log = logging.getLogger("pregoes.web")

AVISO_TEM_BUSCA = (
    "Este pregão já tem preços buscados. Alterar quais itens estão marcados (ou "
    "incluir/excluir linhas) descarta a busca feita — os preços encontrados "
    "continuam na memória por 2 dias, então buscar de novo é rápido.")
_RECUSA_TEM_BUSCA = (
    "Este pregão já tem preços buscados: esta alteração descarta a busca feita. "
    "Recarregue a página e confirme para continuar.")
_RECUSA_BUSCA_RODANDO = (
    "A busca de preços deste pregão está em andamento. Espere terminar (ou cancele "
    "na etapa Preços) antes de marcar, desmarcar, incluir ou excluir itens.")
_RECUSA_LISTA_MUDOU = (
    "A lista de itens mudou desde que esta página foi aberta. Recarregue a página "
    "antes de continuar — nada foi alterado.")
_NAO_ENCONTRADO = "Pregão não encontrado."


# --------------------------------------------------------------------------
# Peças comuns
# --------------------------------------------------------------------------

def _acao(fn: Callable[[Request, str, dict], JSONResponse]):
    """Rota POST: lê o JSON e roda `fn(request, slug, dados)` fora do laço de
    eventos (abrir e gravar o rascunho é disco)."""
    async def rota(request: Request):
        dados = await corpo_json(request)
        return await run_in_threadpool(fn, request, request.path_params["slug"], dados)
    rota.__name__ = fn.__name__
    return rota


def _pagina_404(request: Request):
    return pagina(request, "erro.html", status=404, titulo="Pregão não encontrado",
                  mensagem="Este pregão não está mais na lista — pode ter sido descartado "
                           "ou já virou planilha (veja em Planilhas geradas).")


def _abrir(slug: str) -> Optional[Pregao]:
    """Abre o pregão já com as listas da revisão montadas. Chamar sob
    `trava(slug)` quando for gravar."""
    p = Pregao.abrir(slug)
    if p is not None:
        p.garantir_itens()
    return p


def _tem_busca(p: Pregao) -> bool:
    return p.tem_busca or bool(p.estado.get("selecoes"))


def _liberar_mudanca_de_posicoes(p: Pregao, dados: dict) -> tuple[Optional[JSONResponse], bool]:
    """Aplica a REGRA DOS ÍNDICES (docstring do módulo) antes de uma operação
    que desloca posições. Devolve `(recusa, descartou)`: com `recusa` a
    operação não pode seguir; `descartou` diz que a busca foi descartada (a
    página precisa recarregar: o aviso e as etapas mudam)."""
    if tarefas.ativa("busca", p.slug) is not None:
        return falha(_RECUSA_BUSCA_RODANDO, 409), False
    if not _tem_busca(p):
        return None, False
    if dados.get("confirmar") is not True:
        return falha(_RECUSA_TEM_BUSCA, 409), False
    p.descartar_busca()
    _log.info("REVISAO: pregão %s — busca descartada por mudança nos itens marcados", p.slug)
    return None, True


def _posicao(dados: dict, itens: list) -> tuple[Optional[int], Optional[JSONResponse]]:
    """Valida `i` (linha) e `n` (tamanho da lista que a página conhecia)."""
    i, n = dados.get("i"), dados.get("n")
    if n is not None and n != len(itens):
        return None, falha(_RECUSA_LISTA_MUDOU, 409)
    if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(itens):
        return None, falha(_RECUSA_LISTA_MUDOU, 409)
    return i, None


def _ctx_grade(p: Pregao, **extra) -> dict:
    """O que os pedaços da grade precisam para renderizar."""
    marcados = p.marcados()
    return {"pregao": p, "itens": p.itens_edital, "marcados": marcados,
            "c": regras.contagem(p.itens_edital, marcados),
            "n_tabelas": len(p.extracao.tabelas_brutas) if p.extracao is not None else 0,
            "valor": regras.valor_do_campo, **extra}


def _agora() -> str:
    return datetime.now(config.FUSO_HORARIO).strftime("%H:%M:%S")


def _html_contador(request: Request, p: Pregao, resumo: bool = False) -> dict:
    ctx = _ctx_grade(p, salvo_as=_agora())
    html = {"#rev-contador": pedaco(request, "revisao_contador.html", **ctx).strip()}
    if resumo:
        html["#rev-resumo"] = pedaco(request, "revisao_resumo.html", **ctx).strip()
    return html


def _resposta_grade(request: Request, p: Pregao, *, nova: Optional[int] = None,
                    recarregar: bool = False, aviso: Optional[str] = None) -> JSONResponse:
    """Resposta das operações que mexem em várias linhas: devolve o corpo da
    tabela inteiro (as posições das linhas mudam), o contador e as métricas."""
    if recarregar or not p.itens_edital:
        return ok(recarregar=True, **({"aviso": aviso} if aviso else {}))
    html = _html_contador(request, p, resumo=True)
    html["#rev-corpo"] = pedaco(request, "revisao_linhas.html",
                                **_ctx_grade(p, nova=nova)).strip()
    return ok(html=html, **({"aviso": aviso} if aviso else {}))


# --------------------------------------------------------------------------
# Revisão
# --------------------------------------------------------------------------

def revisao(request: Request):
    slug = request.path_params["slug"]
    p = Pregao.abrir(slug)
    if p is None:
        return _pagina_404(request)
    if p.extracao is None:      # o PDF ainda está sendo lido
        return RedirectResponse(f"{RAIZ}/pregao/{slug}/leitura", 303)
    if p.estado.get("itens_edital") is None:
        with trava(slug):
            p = _abrir(slug)
            if p is None:
                return _pagina_404(request)
            p.salvar()

    res = p.extracao
    rotulo, tipo, complemento = regras.selo_confianca(res.confianca_global)
    sem_itens_lidos = res.total_itens == 0
    escaneado = bool(sem_itens_lidos and p["pdf_path"]
                     and not extrator.tem_texto_extraivel(p["pdf_path"]))
    mapeado = request.query_params.get("mapeado")
    return pagina(
        request, "revisao.html", secao="pregoes", etapa=1,
        **_ctx_grade(p),
        res=res, confianca=(rotulo, tipo, complemento),
        avisos=[regras.limpar_aviso(a) for a in res.avisos],
        oferecer_mapeamento=(res.confianca_global < servicos.CONFIANCA_BOA
                             and bool(res.tabelas_brutas)),
        sem_itens_lidos=sem_itens_lidos, escaneado=escaneado,
        tem_busca=_tem_busca(p), aviso_tem_busca=AVISO_TEM_BUSCA,
        busca_rodando=tarefas.ativa("busca", slug) is not None,
        fornecedores=fornecedores_configurados(),
        mapeado=mapeado if mapeado in ("salvo", "aplicado") else None)


def _salvar_identificacao(request: Request, slug: str, dados: dict) -> JSONResponse:
    """Órgão e número continuam editáveis na revisão (app.py L553-567). Em
    branco é aceito, como lá — a geração da planilha é que barra."""
    campo = next((c for c in ("orgao", "numero") if c in dados), None)
    if campo is None:
        return falha("Nada para salvar.")
    valor = " ".join(str(dados[campo] or "").split())
    with trava(slug):
        p = Pregao.abrir(slug)
        if p is None:
            return falha(_NAO_ENCONTRADO, 404)
        p.estado[campo] = valor
        p.salvar()
    if not valor:
        nome = "O órgão" if campo == "orgao" else "O número do pregão"
        return ok(aviso=f"{nome} ficou em branco — preencha antes de gerar a planilha.",
                  tipo="erro")
    return ok(aviso="Identificação do pregão salva.")


def _salvar_item(request: Request, slug: str, dados: dict) -> JSONResponse:
    """Uma célula da grade: `{i, n, <campo>: valor}`; `<campo>` é um de
    `CAMPOS_EDITAVEIS` ou `marcado` (o "Buscar preço?")."""
    campo = next((c for c in (*regras.CAMPOS_EDITAVEIS, "marcado") if c in dados), None)
    if campo is None:
        return falha("Nada para salvar.")
    with trava(slug):
        p = _abrir(slug)
        if p is None:
            return falha(_NAO_ENCONTRADO, 404)
        itens = list(p.itens_edital)
        i, recusa = _posicao(dados, itens)
        if recusa is not None:
            return recusa
        era_duvidoso = itens[i].confianca == "duvidoso"
        marcados = p.marcados()
        descartou = False

        if campo == "marcado":
            marcar = dados["marcado"] is True
            if marcar != (i in marcados):
                recusa, descartou = _liberar_mudanca_de_posicoes(p, dados)
                if recusa is not None:
                    return recusa
                marcados = (marcados | {i}) if marcar else (marcados - {i})
            if marcar:      # marcou um duvidoso para buscar = conferiu
                itens[i] = regras.conferido(itens[i])
        else:
            try:
                itens[i] = regras.editar_item(itens[i], campo, dados[campo])
            except regras.EntradaInvalida as exc:
                return falha(str(exc))

        p.definir_itens(itens, sorted(marcados))
        p.salvar()

    if descartou:
        return ok(recarregar=True)
    return ok(html=_html_contador(request, p, resumo=era_duvidoso),
              duvidoso=itens[i].confianca == "duvidoso",
              **({} if campo == "marcado" else {"valor": regras.valor_do_campo(itens[i], campo)}))


def _marcar_todos(request: Request, slug: str, dados: dict) -> JSONResponse:
    """"Marcar todos" / "Desmarcar todos" (app.py `_marcar_todos` L659). As
    edições de célula não correm risco: já estão gravadas, uma a uma."""
    marcar = dados.get("valor") is True
    with trava(slug):
        p = _abrir(slug)
        if p is None:
            return falha(_NAO_ENCONTRADO, 404)
        itens = list(p.itens_edital)
        novos = set(range(len(itens))) if marcar else set()
        descartou = False
        if novos != p.marcados():
            recusa, descartou = _liberar_mudanca_de_posicoes(p, dados)
            if recusa is not None:
                return recusa
            p.definir_itens(itens, sorted(novos))
            p.salvar()
    return _resposta_grade(request, p, recarregar=descartou)


def _adicionar_item(request: Request, slug: str, dados: dict) -> JSONResponse:
    """Linha nova, em branco, no FIM do edital (o Streamlit não deixava
    incluir linhas). Nasce marcada para busca, como o padrão da coluna."""
    with trava(slug):
        p = _abrir(slug)
        if p is None:
            return falha(_NAO_ENCONTRADO, 404)
        if p.extracao is None:
            return falha("O PDF deste pregão ainda está sendo lido.")
        recusa, descartou = _liberar_mudanca_de_posicoes(p, dados)
        if recusa is not None:
            return recusa
        itens = list(p.itens_edital)
        itens.append(regras.item_em_branco(itens))
        p.definir_itens(itens, [*p.mapa_busca_edital, len(itens) - 1])
        p.salvar()
    # A 1ª linha de um edital vazio troca o estado da tela (aparece a grade).
    return _resposta_grade(request, p, nova=len(itens) - 1,
                           recarregar=descartou or len(itens) == 1)


def _excluir_item(request: Request, slug: str, dados: dict) -> JSONResponse:
    with trava(slug):
        p = _abrir(slug)
        if p is None:
            return falha(_NAO_ENCONTRADO, 404)
        itens = list(p.itens_edital)
        i, recusa = _posicao(dados, itens)
        if recusa is not None:
            return recusa
        recusa, descartou = _liberar_mudanca_de_posicoes(p, dados)
        if recusa is not None:
            return recusa
        del itens[i]
        p.definir_itens(itens, regras.marcados_apos_excluir(p.mapa_busca_edital, i))
        p.salvar()
    return _resposta_grade(request, p, recarregar=descartou, aviso="Linha excluída.")


def _buscar_precos(request: Request, slug: str, dados: dict) -> JSONResponse:
    """Validações do botão "Buscar preços" (app.py L731-741) + uma nova: item
    marcado sem descrição (só existe agora, que dá para incluir linha)."""
    p = _abrir(slug)
    if p is None:
        return falha(_NAO_ENCONTRADO, 404)
    if not p.itens_revisados:
        return falha("Nenhum item marcado para buscar preço — marque ao menos um.")
    if not fornecedores_configurados():
        return falha("Nenhum fornecedor configurado. Acesse Configurações para "
                     "cadastrar Almeida e/ou Dantas.")
    vazio = regras.marcado_sem_descricao(p.itens_edital, p.mapa_busca_edital)
    if vazio is not None:
        return falha(f"A linha {vazio + 1} está marcada para buscar preço, mas está sem "
                     "descrição. Preencha a descrição ou desmarque o item.")
    return ok(ir=f"/pregao/{slug}/precos")


# --------------------------------------------------------------------------
# Mapeamento manual
# --------------------------------------------------------------------------

def _indice_tabela(valor: Any, tabelas: list) -> Optional[int]:
    try:
        idx = int(valor)
    except (TypeError, ValueError):
        return None
    return idx if 0 <= idx < len(tabelas) else None


def mapeamento(request: Request):
    slug = request.path_params["slug"]
    p = Pregao.abrir(slug)
    if p is None:
        return _pagina_404(request)
    res = p.extracao
    rodando = tarefas.ativa("docling", slug)
    ultima = next((t for t in tarefas.do_pregao(slug)
                   if t.tipo == "docling" and t.estado != "rodando"), None)
    ctx: dict = {
        "pregao": p, "etapa": 1, "secao": "pregoes",
        "nada": res is None or not res.tabelas_brutas,
        "docling_tarefa": rodando.retrato() if rodando else None,
        "docling_erro": ultima.erro if (ultima and ultima.estado == "erro") else None,
        # Só logo depois de a tarefa terminar (a página volta com ?docling=1).
        "docling_fim": (ultima.resultado if (ultima and ultima.estado == "concluida"
                                             and request.query_params.get("docling")) else None),
        "usando_docling": bool(p["usando_tabelas_docling"]),
        "docling_disponivel": (extrator_docling.disponivel() and bool(p["pdf_path"])
                               and not p["usando_tabelas_docling"]),
        "tem_busca": _tem_busca(p),
    }
    if ctx["nada"] or rodando:
        return pagina(request, "mapeamento.html", **ctx)

    tabelas = res.tabelas_brutas
    idx = _indice_tabela(request.query_params.get("tabela"), tabelas)
    if idx is None:     # sem escolha na URL: a primeira tabela que tem conteúdo
        idx = next((i for i, t in enumerate(tabelas) if regras.n_colunas(t)), 0)
    tabela = tabelas[idx]
    n_cols = regras.n_colunas(tabela)
    ctx.update(opcoes=[regras.rotulo_tabela(i, t) for i, t in enumerate(tabelas)],
               idx=idx, n_cols=n_cols)
    if n_cols:
        with db.get_conn() as conn:
            mapa_salvo = mapeamento_manual.carregar_mapa(
                conn, mapeamento_manual.assinatura_cabecalho(tabela[0]))
        escolhas = regras.campos_iniciais(n_cols, mapa_salvo)
        mapa, repetidas = regras.montar_mapa(escolhas)
        ctx.update(previa=regras.previa_tabela(tabela), colunas=regras.rotulos_colunas(tabela),
                   mapa_salvo=bool(mapa_salvo), escolhas=escolhas, repetidas=repetidas,
                   faltando=regras.obrigatorios_faltando(mapa),
                   campos=mapeamento_manual.CAMPOS, rotulos=mapeamento_manual.ROTULOS_CAMPOS,
                   obrigatorios=regras.OBRIGATORIOS_MAPA)
    return pagina(request, "mapeamento.html", **ctx)


def _mapeamento_aplicar(request: Request, slug: str, dados: dict) -> JSONResponse:
    """"Aplicar mapeamento" (app.py L3585-3607): aplica em LOTE (a tabela
    escolhida + as continuações das outras páginas), troca a extração do
    pregão e refaz a lista da revisão a partir dela."""
    with trava(slug):
        p = Pregao.abrir(slug)
        if p is None:
            return falha(_NAO_ENCONTRADO, 404)
        res = p.extracao
        if res is None or not res.tabelas_brutas:
            return falha("Nada para mapear — volte e envie um PDF primeiro.")
        idx = _indice_tabela(dados.get("tabela"), res.tabelas_brutas)
        if idx is None:
            return falha("As tabelas deste PDF mudaram. Recarregue a página e escolha de novo.")
        tabela = res.tabelas_brutas[idx]
        n_cols = regras.n_colunas(tabela)
        if not n_cols:
            return falha("Tabela vazia — escolha outra.")
        escolhas = [str(dados.get(f"col_{c}") or "ignorar") for c in range(n_cols)]
        if any(e not in mapeamento_manual.CAMPOS for e in escolhas):
            return falha("Escolha um dos campos da lista para cada coluna.")
        mapa, _ = regras.montar_mapa(escolhas)
        faltando = regras.obrigatorios_faltando(mapa)
        if faltando:
            return falha(f"Mapeamento incompleto — faltam: {', '.join(faltando)}.")
        if tarefas.ativa("busca", slug) is not None:
            return falha(_RECUSA_BUSCA_RODANDO, 409)

        novo = mapeamento_manual.aplicar_mapa_em_lote(
            res.tabelas_brutas, idx, mapa, arquivo=res.arquivo)
        if novo.total_itens == 0:
            # Trocar a extração por uma vazia só apagaria o que já está na revisão.
            return falha("Com este mapeamento nenhum item foi lido da tabela. Confira se as "
                         "colunas de Descrição e Quantidade estão certas — nada foi alterado.")
        salvar = dados.get("salvar") is True
        if salvar:
            with db.get_conn() as conn:
                mapeamento_manual.salvar_mapa(
                    conn, mapeamento_manual.assinatura_cabecalho(tabela[0]), mapa)
        p.estado["extracao"] = novo
        p.estado.pop("itens_edital", None)      # garantir_itens() refaz a grade
        p.descartar_busca()
        p.garantir_itens()
        p.salvar()
    _log.info("MAPEAMENTO: pregão %s — tabela #%d, %d itens, confiança %.0f%%%s", slug,
              idx + 1, novo.total_itens, 100 * novo.confianca_global,
              " (mapa salvo)" if salvar else "")
    return ok(ir=f"/pregao/{slug}/revisao?mapeado={'salvo' if salvar else 'aplicado'}")


def _carregar_docling(slug: str, tarefa: tarefas.Tarefa) -> int:
    """Tarefa 'docling': a extração profunda (5–10 min) troca as tabelas
    brutas do pregão pelas que o Docling enxergou (app.py L3458-3475).
    Devolve quantas achou; com zero, as tabelas atuais ficam como estão."""
    p = Pregao.abrir(slug)
    if p is None or not p["pdf_path"]:
        raise RuntimeError("pregão sem PDF para a extração profunda")
    tarefa.atualizar(0.05, "Analisando o PDF com a extração profunda — costuma levar "
                           "de 5 a 10 minutos.")
    tabelas = extrator_docling.coletar_tabelas_brutas_docling(p["pdf_path"])
    tarefa.atualizar(0.95, f"{len(tabelas)} tabelas encontradas. Guardando…")
    if not tabelas:
        return 0
    with trava(slug):
        p = Pregao.abrir(slug)
        if p is None or p.extracao is None:     # descartado enquanto rodava
            return 0
        p.extracao.tabelas_brutas = tabelas
        p.estado["usando_tabelas_docling"] = True
        p.salvar()
    _log.info("DOCLING: pregão %s — %d tabelas", slug, len(tabelas))
    return len(tabelas)


def _mapeamento_docling(request: Request, slug: str, dados: dict) -> JSONResponse:
    if not extrator_docling.disponivel():
        return falha("A extração profunda (Docling) não está instalada neste computador.")
    p = Pregao.abrir(slug)
    if p is None:
        return falha(_NAO_ENCONTRADO, 404)
    if p.extracao is None or not p["pdf_path"]:
        return falha("Nada para mapear — volte e envie um PDF primeiro.")
    t = tarefas.iniciar("docling", slug, f"Extração profunda (Docling) — {p.titulo}",
                        lambda tarefa: _carregar_docling(slug, tarefa))
    return ok(tarefa=t.id, recarregar=True)


rotas: list[Route] = [
    Route("/pregao/{slug}/revisao", revisao),
    Route("/pregao/{slug}/revisao/identificacao", _acao(_salvar_identificacao), methods=["POST"]),
    Route("/pregao/{slug}/revisao/item", _acao(_salvar_item), methods=["POST"]),
    Route("/pregao/{slug}/revisao/marcar-todos", _acao(_marcar_todos), methods=["POST"]),
    Route("/pregao/{slug}/revisao/adicionar", _acao(_adicionar_item), methods=["POST"]),
    Route("/pregao/{slug}/revisao/excluir", _acao(_excluir_item), methods=["POST"]),
    Route("/pregao/{slug}/revisao/buscar", _acao(_buscar_precos), methods=["POST"]),
    Route("/pregao/{slug}/mapeamento", mapeamento),
    Route("/pregao/{slug}/mapeamento/aplicar", _acao(_mapeamento_aplicar), methods=["POST"]),
    Route("/pregao/{slug}/mapeamento/docling", _acao(_mapeamento_docling), methods=["POST"]),
]
