"""Etapa 4 do pregão — "Planilha": tributação (ST/NCM), margem e geração.

No Streamlit isto era o rodapé da tela de preços e a geração era um bloco que
sumia no clique seguinte. Aqui é uma tela própria (`/pregao/<slug>/planilha`):
resumo do que vai para a planilha, Substituição Tributária, margem e UM botão
principal. As regras vêm de `regras_planilha.py` (portas puras do Streamlit).

Decisões desta tela:

- GERAR NÃO APAGA O RASCUNHO (o Streamlit apagava). O pregão some de
  "em andamento" porque passa a ter planilha no histórico
  (`pregao.em_andamento`), mas o arquivo fica: o operador pode reabrir,
  ajustar e gerar de novo. O rascunho é salvo com `pregao_gerado_hash`.
- O que mostrar depois de gerar vem do HISTÓRICO (`exports_log` do edital,
  casado por órgão+número) e de `Pregao.gerado_e_intocado` — nenhuma chave
  nova no rascunho. Planilha gerada e pregão alterado depois → aviso de
  "desatualizada" + "Gerar de novo".
- `?sem_precos=1` (vindo de "Gerar sem buscar" na revisão) — ou pregão sem
  nenhuma escolha de preço — mostra só o resumo e gera o EDITAL INTEIRO com
  `selecoes={}`.
- Identificar NCM é tarefa em segundo plano (tipo `ncm`); o aviso de falhas
  passageiras fica no registro da tarefa, não no rascunho.
- O download normal é da área Histórico (`/historico/planilha/<id>`). Só
  quando o histórico falhou ao gravar (não há `id`) o arquivo recém-gerado é
  servido daqui, para a falha não bloquear o download.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse
from starlette.routing import Route

from pregoes import backup, config, credenciais, db, erros, exportador
from pregoes.web import regras_planilha as regras
from pregoes.web import tarefas
from pregoes.web.nucleo import corpo_json, falha, ok, pagina, pedaco
from pregoes.web.pregao import Pregao, trava

_log = logging.getLogger("pregoes.web")

_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# --------------------------------------------------------------------------
# Leitura do estado para a tela
# --------------------------------------------------------------------------

def _nao_encontrado(request: Request):
    return pagina(request, "erro.html", status=404, titulo="Pregão não encontrado",
                  mensagem="Este pregão não está mais na lista — pode ter sido descartado. "
                           "As planilhas já geradas continuam em Planilhas geradas.")


def _verdadeiro(valor) -> bool:
    return valor is True or str(valor or "").strip().lower() in ("1", "true", "sim", "on")


def _itens_da_planilha(p: Pregao, sem_precos: bool):
    """`(itens, selecoes, markups, itens_com_st)` já no índice do EDITAL — o
    que `exportador.exportar` recebe. Sem preços: edital inteiro, nada mais."""
    if sem_precos:
        return (p.itens_edital or p.itens_revisados), {}, None, None
    cl = regras.classificar(p.selecoes, p.markup_overrides)
    return regras.remapear(p.itens_revisados, p.estado.get("itens_edital"),
                           p.estado.get("mapa_busca_edital"), p.selecoes,
                           cl.markups, cl.com_st)


def _ultima_planilha(orgao: str, numero: str) -> Optional[dict]:
    """A planilha mais recente registrada para este órgão+número (mesmo
    casamento `lower(trim())` do registro do edital). None se não há — ou se o
    banco não respondeu (a tela não cai por causa do histórico)."""
    if not orgao or not numero:
        return None
    try:
        with db.get_conn() as conn:
            linha = conn.execute(
                """
                SELECT x.id, x.arquivo, x.n_itens, x.n_selecoes, x.criado_em
                FROM exports_log x JOIN editais e ON e.id = x.edital_id
                WHERE lower(trim(e.orgao)) = lower(trim(?))
                  AND lower(trim(e.numero_pregao)) = lower(trim(?))
                ORDER BY x.id DESC LIMIT 1
                """, (orgao, numero)).fetchone()
    except Exception:
        _log.exception("falha ao consultar a última planilha do pregão")
        return None
    if linha is None:
        return None
    d = dict(linha)
    d["existe"] = (config.EXPORTS_DIR / d["arquivo"]).is_file()
    return d


def _arquivo_do_pregao(p: Pregao, nome: str) -> Optional[Path]:
    """Caminho de uma planilha em `data/exports` SE o nome for um arquivo
    simples (nada de subpasta) com o prefixo deste pregão. O nome vem da URL."""
    if not nome or nome != Path(nome).name or not nome.endswith(".xlsx"):
        return None
    if not nome.startswith(regras.prefixo_do_arquivo(p.orgao, p.numero)):
        return None
    caminho = config.EXPORTS_DIR / nome
    return caminho if caminho.is_file() else None


def _contexto_st(p: Pregao) -> dict:
    """Painel de Substituição Tributária (índices de BUSCA)."""
    selecoes, overrides = p.selecoes, p.markup_overrides
    cl = regras.classificar(selecoes, overrides)
    itens = p.itens_revisados
    linhas = []
    for off in cl.sem_ncm:
        item = itens[off] if 0 <= off < len(itens) else None
        sel = selecoes.get(off)
        linhas.append({
            "off": off,
            "numero": getattr(item, "item_numero", None) or off + 1,
            "descricao": regras.rotulo_item(itens, off),
            "marca": getattr(sel, "marca", None),
            "fornecedor": (getattr(sel, "fornecedor", None) or "").capitalize(),
            "com_st": overrides.get(off) == config.MARKUP_ST,
        })
    return {"resumo": cl.resumo, "st_manual": cl.st_manual, "sem_ncm": linhas,
            "n_identificaveis": len(regras.identificaveis(selecoes, cl.sem_ncm))}


def _situacao(p: Pregao, recem: Optional[Path], ultima: Optional[dict]) -> str:
    """O que o topo da tela mostra:
    'nova' (nunca gerou) · 'pronta' (gerada e intocada, com arquivo para baixar)
    · 'desatualizada' (mudou depois de gerar) · 'sem_registro' (gerada, mas o
    histórico não gravou) · 'sem_arquivo' (registrada, arquivo sumiu)."""
    if recem is not None:
        return "pronta"
    if not p.estado.get("pregao_gerado_hash"):
        return "nova"
    if not p.gerado_e_intocado:
        return "desatualizada"
    if ultima is None:
        return "sem_registro"
    return "pronta" if ultima["existe"] else "sem_arquivo"


# --------------------------------------------------------------------------
# Tela
# --------------------------------------------------------------------------

def tela(request: Request):
    p = Pregao.abrir(request.path_params["slug"])
    if p is None:
        return _nao_encontrado(request)
    p.garantir_itens()      # só em memória — abrir a tela não grava nada
    slug = p.slug
    q = request.query_params
    tem_selecoes = bool(p.selecoes)
    sem_precos = _verdadeiro(q.get("sem_precos")) or not tem_selecoes

    itens, selecoes, _mk, _st = _itens_da_planilha(p, sem_precos)
    total, com_preco = len(itens), len(selecoes)

    # Planilha recém-gerada cujo registro no histórico falhou: o nome vem na
    # URL (não há id de histórico) e é conferido contra este pregão.
    # Só vale enquanto o pregão segue como foi gerado.
    recem = (_arquivo_do_pregao(p, q.get("arquivo", ""))
             if q.get("arquivo") and p.gerado_e_intocado else None)
    ultima = None if recem is not None else _ultima_planilha(p.orgao, p.numero)
    situacao = _situacao(p, recem, ultima)

    pronta = None
    if situacao == "pronta":
        if recem is not None:
            pronta = {"arquivo": recem.name, "linhas": [], "no_historico": False,
                      "baixar": f"/pregao/{slug}/planilha/arquivo/{quote(recem.name)}"}
        else:
            pronta = {"arquivo": ultima["arquivo"], "no_historico": True,
                      "baixar": f"/historico/planilha/{ultima['id']}",
                      "linhas": regras.linhas_de_sucesso(
                          ultima["n_selecoes"] or 0, ultima["n_itens"] or 0,
                          regras.n_dantas(_itens_da_planilha(p, False)[1]))}

    t_ncm = tarefas.ativa("ncm", slug)
    ultima_ncm = next((t for t in tarefas.do_pregao(slug) if t.tipo == "ncm"), None)
    st = None if sem_precos else _contexto_st(p)
    aviso_ncm = None
    if st and st["n_identificaveis"] and ultima_ncm is not None and t_ncm is None:
        # Só enquanto ainda há o que re-tentar: sem isso o aviso ficaria velho.
        aviso_ncm = ultima_ncm.extra.get("aviso") or ultima_ncm.erro

    salva = credenciais.carregar_margem_venda()
    return pagina(
        request, "planilha.html", pregao=p, etapa=3,
        sem_precos=sem_precos, tem_selecoes=tem_selecoes, n_escolhidos=len(p.selecoes),
        total=total, com_preco=com_preco, sem_preco=total - com_preco,
        situacao=situacao, pronta=pronta, ultima=ultima,
        historico_falhou=recem is not None,
        sem_identidade=not p.orgao or not p.numero,
        aviso_dantas=None if sem_precos else regras.aviso_desconto_dantas(p.selecoes),
        st=st, tarefa_ncm=t_ncm.retrato() if t_ncm else None, aviso_ncm=aviso_ncm,
        busca_em_curso=tarefas.ativa("busca", slug) is not None,
        margens=regras.opcoes_margem(), margem=regras.margem_efetiva(salva),
        textos=regras)


# --------------------------------------------------------------------------
# Substituição Tributária e NCM
# --------------------------------------------------------------------------

async def marcar_st(request: Request):
    """Escolha Normal × Com Substituição Tributária de um item sem NCM."""
    dados = await corpo_json(request)
    return await run_in_threadpool(_marcar_st, request, dados)


def _marcar_st(request: Request, dados: dict):
    slug = request.path_params["slug"]
    try:
        off = int(dados.get("i"))
    except (TypeError, ValueError):
        return falha("Item inválido.")
    escolha = str(dados.get("st") or "")
    if escolha not in ("st", "normal"):
        return falha("Escolha inválida.")
    with trava(slug):
        p = Pregao.abrir(slug)
        if p is None:
            return falha("Pregão não encontrado.", 404)
        sel = p.selecoes.get(off)
        if sel is None:
            return falha("Este item não tem mais preço escolhido. Atualize a página.")
        if getattr(sel, "ncm", None):
            return falha("Este item já tem NCM — a classificação dele é automática. "
                         "Atualize a página.")
        regras.marcar_st(p.markup_overrides, off, escolha == "st")
        p.salvar()
        cl = regras.classificar(p.selecoes, p.markup_overrides)
    if p.estado.get("pregao_gerado_hash"):
        # Já existe planilha gerada: ela ficou desatualizada (ou voltou a
        # valer) — o topo da tela muda, então recarrega inteira.
        return ok(recarregar=True)
    return ok(html={"#st-resumo": pedaco(request, "planilha_st_resumo.html",
                                         resumo=cl.resumo, st_manual=cl.st_manual)})


def identificar_ncm(slug: str, tarefa: tarefas.Tarefa) -> None:
    """Tarefa `ncm`: consulta o NCM dos produtos ESCOLHIDOS que ainda não têm
    (na página de detalhe do fornecedor) e grava no rascunho. Porta de
    `_identificar_ncm` (app.py L2590-2654); falha passageira e erro geral
    viram aviso em `tarefa.extra["aviso"]` (a tela mostra)."""
    from pregoes import scraper

    p = Pregao.abrir(slug)
    if p is None:
        raise RuntimeError("pregão não encontrado")
    cl = regras.classificar(p.selecoes, p.markup_overrides)
    pedidos = regras.pedidos_ncm(p.selecoes, regras.identificaveis(p.selecoes, cl.sem_ncm))
    if not pedidos:
        return
    tarefa.atualizar(0.0, "Consultando NCM dos produtos escolhidos…")

    def _progresso(feitos, total):
        tarefa.atualizar(feitos / max(total, 1), f"Consultando NCM… {feitos}/{total}")

    try:
        ncms, falhas = scraper.consultar_ncms(pedidos, progresso=_progresso)
    except Exception as exc:    # noqa: BLE001 — vira aviso, como no Streamlit
        _log.exception("falha ao consultar NCM")
        tarefa.atualizar(aviso=erros.mensagem_amigavel(exc, "consultar o NCM"))
        return
    with trava(slug):
        p = Pregao.abrir(slug)
        if p is None:           # descartado enquanto consultava
            return
        achados = regras.aplicar_ncms(p.selecoes, p.markup_overrides, pedidos, ncms, falhas)
        p.salvar()
    if falhas:
        tarefa.atualizar(aviso=regras.aviso_falhas_ncm(len(falhas)))
    _log.info("NCM: pregão %s — %d consultados, %d encontrados, %d falhas passageiras",
              slug, len(pedidos), achados, len(falhas))


def ncm_iniciar(request: Request):
    slug = request.path_params["slug"]
    p = Pregao.abrir(slug)
    if p is None:
        return falha("Pregão não encontrado.", 404)
    cl = regras.classificar(p.selecoes, p.markup_overrides)
    if not regras.pedidos_ncm(p.selecoes, regras.identificaveis(p.selecoes, cl.sem_ncm)):
        return falha("Não há item escolhido com NCM a identificar.")
    t = tarefas.iniciar("ncm", slug, f"Identificando NCM — {p.titulo}",
                        lambda tarefa: identificar_ncm(slug, tarefa))
    return ok(tarefa=t.id, recarregar=True)


# --------------------------------------------------------------------------
# Margem do preço de venda
# --------------------------------------------------------------------------

def _aplicar_margem(escolha) -> bool:
    """Grava a margem como padrão SÓ se difere do padrão efetivo. True se gravou."""
    if not regras.deve_salvar_margem(escolha, credenciais.carregar_margem_venda()):
        return False
    credenciais.salvar_margem_venda(escolha)
    return True


async def margem_salvar(request: Request):
    dados = await corpo_json(request)
    escolha = str(dados.get("margem") or "")
    if escolha not in config.MARGENS_VENDA:
        return falha("Margem inválida.")
    gravou = await run_in_threadpool(_aplicar_margem, escolha)
    if not gravou:
        return ok()
    rotulo = dict(regras.opcoes_margem())[escolha]
    return ok(aviso=f"Margem de {rotulo} salva como padrão.")


# --------------------------------------------------------------------------
# Geração
# --------------------------------------------------------------------------

def _registrar_aprendizado(p: Pregao) -> None:
    """Grava as escolhas do operador (Camada 4). Melhoria progressiva: falha
    aqui só vai para o log (app.py L3376-3425)."""
    try:
        registros = regras.registros_aprendizado(
            p.itens_revisados, p.selecoes, p.resultados_busca)
        if not registros:
            return
        with db.get_conn() as conn:
            for reg in registros:
                db.aprendido_gravar(conn, *reg)
    except Exception:
        _log.exception("falha ao registrar aprendizado de seleções")


def persistir_edital(itens: list, selecoes: dict, arquivo: str, markup,
                     orgao: str, numero: str, pdf_path: str) -> int:
    """Edital + itens + 1 linha do histórico da planilha, na MESMA transação.
    UPSERT por órgão+número normalizados (`lower(trim())`, pega o `id` mais
    alto): re-gerar o mesmo pregão atualiza o registro e troca os itens.
    Porta de `_persistir_edital` (app.py L940-1024). Devolve o id da linha de
    `exports_log` (o Streamlit não precisava dele; aqui é o endereço do
    download)."""
    orgao, numero = (orgao or "").strip(), (numero or "").strip()
    status = regras.status_do_edital(selecoes)
    with db.get_conn() as conn:
        edital_id = None
        if orgao and numero:
            row = conn.execute(
                """
                SELECT id FROM editais
                WHERE lower(trim(orgao)) = lower(trim(?))
                  AND lower(trim(numero_pregao)) = lower(trim(?))
                ORDER BY id DESC LIMIT 1
                """, (orgao, numero)).fetchone()
            if row is not None:
                edital_id = row[0]
        if edital_id is not None:
            conn.execute(
                "UPDATE editais SET status = ?, arquivo_pdf = ?, "
                "atualizado_em = CURRENT_TIMESTAMP WHERE id = ?",
                (status, pdf_path, edital_id))
            conn.execute("DELETE FROM edital_itens WHERE edital_id = ?", (edital_id,))
        else:
            edital_id = conn.execute(
                "INSERT INTO editais (orgao, numero_pregao, arquivo_pdf, status) "
                "VALUES (?, ?, ?, ?)", (orgao, numero, pdf_path, status)).lastrowid
        for idx, item in enumerate(itens):
            sel = selecoes.get(idx)
            conn.execute(
                """
                INSERT INTO edital_itens
                    (edital_id, item_numero, descricao_original, unidade,
                     quantidade, valor_referencia, preco_base, confianca_match, revisado)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (edital_id, item.item_numero, item.descricao, item.unidade,
                 item.quantidade, item.valor_referencia,
                 sel.preco if sel else None, "manual" if sel else None, 1))
        db.export_log_gravar(conn, edital_id, arquivo, len(itens), len(selecoes or {}), markup)
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


async def gerar(request: Request):
    dados = await corpo_json(request)
    return await run_in_threadpool(_gerar, request.path_params["slug"], dados)


def _gerar(slug: str, dados: dict):
    """Porta de `_gerar_excel` (app.py L788-937), na mesma ordem: aprendizado
    (só no botão "com preços", ANTES das validações) → remapeia busca→edital →
    valida → exporta → histórico (falha não bloqueia) → hash → backup."""
    sem_precos = _verdadeiro(dados.get("sem_precos"))
    with trava(slug):
        p = Pregao.abrir(slug)
        if p is None:
            return falha("Pregão não encontrado.", 404)
        p.garantir_itens()
        if not sem_precos:
            _registrar_aprendizado(p)

        itens, selecoes, markups, itens_com_st = _itens_da_planilha(p, sem_precos)
        if not itens:
            return falha(regras.MSG_SEM_ITENS)
        if not p.orgao or not p.numero:
            return falha(regras.MSG_SEM_IDENTIDADE)

        _log.info(
            "GERAR planilha: %d itens | ncm_por_selecao=%s | markups=%s | itens_com_st=%s",
            len(itens), {off: getattr(s, "ncm", None) for off, s in selecoes.items()},
            markups, sorted(itens_com_st) if itens_com_st else itens_com_st)

        # Margem: a que está na tela (e vira o padrão, pela mesma regra do
        # seletor) → padrão salvo → padrão de fábrica.
        escolha = dados.get("margem")
        if escolha in config.MARGENS_VENDA:
            _aplicar_margem(escolha)
            margem = escolha
        else:
            margem = credenciais.carregar_margem_venda() or config.MARGEM_VENDA_PADRAO

        markup = config.MARKUP_DEFAULT
        try:
            destino = exportador.exportar(
                itens, orgao=p.estado.get("orgao"), numero_pregao=p.estado.get("numero"),
                markup=markup, markups=markups, itens_com_st=itens_com_st,
                selecoes=selecoes, margem_venda=margem)
        except Exception as exc:    # noqa: BLE001 — mensagem em português, sem stacktrace
            _log.exception("falha ao gerar planilha")
            return falha(erros.mensagem_amigavel(exc, "gerar a planilha"), 500)

        # Histórico é best-effort: a planilha JÁ está no disco; falha aqui não
        # pode bloquear o download na hora do pregão.
        historico_ok = True
        try:
            persistir_edital(itens, selecoes, destino.name, markup,
                             p.orgao, p.numero, p.estado.get("pdf_path") or "")
        except Exception:
            historico_ok = False
            _log.exception("falha ao salvar o histórico do pregão (planilha OK)")

        # Pregão concluído: a impressão digital do que foi gerado. O rascunho
        # FICA (diferença deliberada do Streamlit) — mexer em qualquer insumo
        # muda o hash e a tela passa a oferecer "Gerar de novo".
        p.estado["pregao_gerado_hash"] = p.hash()
        p.salvar()

    try:
        threading.Thread(target=backup.backup_externo_agora, daemon=True).start()
    except Exception:
        _log.exception("falha ao disparar o backup externo pós-geração")

    # Volta para a tela (endereço limpo), que mostra o bloco "Planilha gerada".
    # Sem registro no histórico não há id para o download: a tela recebe o
    # nome do arquivo e o serve por `baixar_arquivo`.
    consulta = []
    if sem_precos:
        consulta.append("sem_precos=1")
    if not historico_ok:
        consulta.append(f"arquivo={quote(destino.name)}")
    return ok(ir=f"/pregao/{slug}/planilha" + ("?" + "&".join(consulta) if consulta else ""))


def baixar_arquivo(request: Request):
    """Planilha recém-gerada que não entrou no histórico (ver topo do módulo)."""
    p = Pregao.abrir(request.path_params["slug"])
    caminho = _arquivo_do_pregao(p, request.path_params["nome"]) if p else None
    if caminho is None:
        return pagina(request, "erro.html", status=404, titulo="Planilha não encontrada",
                      mensagem="Este arquivo não está mais disponível. Gere a planilha de novo.")
    return FileResponse(caminho, filename=caminho.name, media_type=_MIME_XLSX)


rotas: list = [
    Route("/pregao/{slug}/planilha", tela),
    Route("/pregao/{slug}/planilha/gerar", gerar, methods=["POST"]),
    Route("/pregao/{slug}/planilha/st", marcar_st, methods=["POST"]),
    Route("/pregao/{slug}/planilha/ncm", ncm_iniciar, methods=["POST"]),
    Route("/pregao/{slug}/planilha/margem", margem_salvar, methods=["POST"]),
    Route("/pregao/{slug}/planilha/arquivo/{nome}", baixar_arquivo),
]
