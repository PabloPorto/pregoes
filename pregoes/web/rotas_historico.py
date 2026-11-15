"""Planilhas geradas — a lista dos pregões já processados (`/historico`).

Tela de LEITURA: consulta o banco e serve os `.xlsx` guardados em
`config.EXPORTS_DIR`. Baixar de novo relê o arquivo do disco — NUNCA
re-exporta (a planilha é o que o operador conferiu e entregou).

O que mudou em relação ao Streamlit (inventário 2.7 e 9.13):
- todas as planilhas de cada pregão ficam à mão (antes, só a última);
- a lista é paginada e o filtro mora na URL (`?q=…&p=…`);
- quando o pregão ainda está guardado em "Pregões em andamento", a linha
  ganha o atalho "Reabrir".

`GET /historico/planilha/<id>` é o endereço de download usado também pela
tela de geração da planilha (contrato em docs/ARQUITETURA_WEB.md).
"""
from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse
from starlette.routing import Route

from pregoes import db, rascunho
from pregoes.web import regras_historico as regras
from pregoes.web.nucleo import corpo_json, falha, ok, pagina, pedaco

_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _com_planilhas(conn, editais: list[dict]) -> list[dict]:
    """Completa cada pregão com as planilhas e, se ainda há rascunho dele, o
    slug para reabrir."""
    planilhas = regras.planilhas_dos_editais(conn, [e["id"] for e in editais])
    try:
        guardados = regras.rascunhos_por_identidade(rascunho.listar_pregoes())
    except Exception:       # rascunho ilegível nunca derruba o histórico
        guardados = {}
    for e in editais:
        e["planilhas"] = planilhas.get(e["id"], [])
        e["slug_rascunho"] = guardados.get(
            regras.chave_identidade(e["orgao"], e["numero_pregao"]))
    return editais


def _edital(conn, edital_id: int) -> dict | None:
    return next((e for e in regras.listar_editais(conn) if e["id"] == edital_id), None)


def historico(request: Request):
    filtro = (request.query_params.get("q") or "").strip()
    with db.get_conn() as conn:
        todos = regras.listar_editais(conn)
        achados = regras.filtrar_editais(todos, filtro)
        visiveis, pagina_atual, paginas = regras.paginar(
            achados, regras.inteiro(request.query_params.get("p")),
            regras.PREGOES_POR_PAGINA)
        _com_planilhas(conn, visiveis)
    return pagina(request, "historico.html", secao="historico",
                  editais=visiveis, total=len(todos), achados=len(achados),
                  filtro=filtro, pagina_atual=pagina_atual, paginas=paginas,
                  prefixo=regras.prefixo_pagina("/historico", q=filtro))


def baixar_planilha(request: Request):
    with db.get_conn() as conn:
        linha = conn.execute("SELECT arquivo FROM exports_log WHERE id = ?",
                             (request.path_params["export_id"],)).fetchone()
    caminho = regras.caminho_da_planilha(linha["arquivo"]) if linha else None
    if caminho is None:
        return pagina(request, "erro.html", secao="historico", status=404,
                      titulo="Planilha não encontrada",
                      mensagem="Arquivo não está na pasta — gere de novo a partir do pregão.")
    return FileResponse(caminho, filename=caminho.name, media_type=_MIME_XLSX)


def editar_form(request: Request):
    with db.get_conn() as conn:
        edital = _edital(conn, request.path_params["edital_id"])
    if edital is None:
        return falha("Este pregão não está mais no histórico.", 404)
    return pagina(request, "historico_editar.html", e=edital)


async def editar_salvar(request: Request):
    edital_id = request.path_params["edital_id"]
    dados = await corpo_json(request)

    def _gravar() -> dict | None:
        if not regras.atualizar_pregao(edital_id, str(dados.get("orgao") or ""),
                                       str(dados.get("numero") or "")):
            return None
        with db.get_conn() as conn:
            return _com_planilhas(conn, [_edital(conn, edital_id)])[0]

    edital = await run_in_threadpool(_gravar)
    if edital is None:
        return falha("Este pregão não está mais no histórico.", 404)
    return ok(aviso="Identificação do pregão atualizada.", fechar_modal=True,
              html={f"#edital-{edital_id}": pedaco(request, "historico_linha.html",
                                                    e=edital).strip()})


def excluir(request: Request):
    if not regras.excluir_pregao(request.path_params["edital_id"]):
        return falha("Este pregão já tinha sido excluído.", 404)
    return ok(recarregar=True)


rotas = [
    Route("/historico", historico),
    Route("/historico/planilha/{export_id:int}", baixar_planilha),
    Route("/historico/{edital_id:int}/editar", editar_form),
    Route("/historico/{edital_id:int}/editar", editar_salvar, methods=["POST"]),
    Route("/historico/{edital_id:int}/excluir", excluir, methods=["POST"]),
]
