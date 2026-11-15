"""Meu catálogo (`/catalogo`) e Variação de preços (`/precos`).

Meu catálogo reúne o que no Streamlit ficava numa aba de Configurações
(inventário 5.4): as 4 métricas e o importador de planilhas antigas. Novo
aqui: a lista navegável dos produtos (antes não havia como VER o catálogo).

A importação é uma tarefa do servidor (`tarefas`, tipo `importar`): etapa 1
lê as planilhas (`importador.importar`); etapa 2, só com chave da IA,
padroniza os nomes (`alinhador.normalizar_catalogo`). O resumo fica no
resultado da tarefa terminada — recarregar a página não o apaga (no
Streamlit ele sumia no `st.rerun()` do fim).

Variação de preços é o painel previsto no roteiro da Fase 3 (item 8): para
cada produto com 2+ cotações do mesmo fornecedor, o primeiro e o último
preço. Hoje `precos_historico` é alimentada pela importação de planilhas (a
busca nos sites usa outro cache) — a tela diz isso ao operador.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.routing import Route

from pregoes import alinhador, config, credenciais, db, erros, importador
from pregoes.web import regras_historico as regras
from pregoes.web import tarefas
from pregoes.web.nucleo import falha, ok, pagina

_log = logging.getLogger("pregoes.web")

# Recursos de desenvolvimento (inventário 5.10): o operador final não vê.
_DEBUG = bool(os.environ.get("PREGOES_DEBUG"))


# --------------------------------------------------------------------------
# Meu catálogo
# --------------------------------------------------------------------------

def _ultima_terminada(tipo: str) -> Optional[tarefas.Tarefa]:
    return next((t for t in tarefas.do_pregao(None)
                 if t.tipo == tipo and t.estado != "rodando"), None)


def _desfecho(tarefa: Optional[tarefas.Tarefa]) -> Optional[dict]:
    """O que mostrar da última tarefa terminada: o resultado dela, ou a
    mensagem de erro se quebrou no meio."""
    if tarefa is None:
        return None
    if tarefa.estado == "erro" or not isinstance(tarefa.resultado, dict):
        return {"erro": tarefa.erro or "A tarefa não terminou. Tente de novo.",
                "terminou_em": tarefa.fim}
    return {**tarefa.resultado, "terminou_em": tarefa.fim}


def catalogo(request: Request):
    consulta = (request.query_params.get("q") or "").strip()
    with db.get_conn() as conn:
        metricas = regras.metricas_catalogo(conn)
        achados = regras.buscar_produtos(conn, consulta)
        produtos, pagina_atual, paginas = regras.paginar(
            achados, regras.inteiro(request.query_params.get("p")),
            regras.PRODUTOS_POR_PAGINA)
        regras.anexar_ultimas_cotacoes(conn, produtos)
        importacoes = [dict(r) for r in conn.execute(
            "SELECT arquivo, n_produtos, n_precos, status, erro, criado_em "
            "FROM imports_log ORDER BY id DESC LIMIT 10")]
    andamento = tarefas.ativa("importar", None) or tarefas.ativa("padronizar", None)
    tem_ia = bool(credenciais.carregar_chave_api("gemini"))
    return pagina(
        request, "catalogo.html", secao="catalogo",
        metricas=metricas, produtos=produtos, achados=len(achados), consulta=consulta,
        pagina_atual=pagina_atual, paginas=paginas,
        prefixo=regras.prefixo_pagina("/catalogo", q=consulta),
        importacoes=importacoes, tem_ia=tem_ia,
        tarefa=andamento.retrato() if andamento else None,
        resultado=None if andamento else _desfecho(_ultima_terminada("importar")),
        sem_padronizar=(metricas["total"] - metricas["padronizados"]) if _DEBUG else 0)


def _guardar_planilhas(enviadas: list[tuple[str, bytes]]) -> list[Path]:
    """Grava cada planilha em `config.HISTORICO_DIR` com o nome ORIGINAL
    higienizado — determinístico, para reenviar a mesma planilha não duplicar
    os preços (app.py L3812)."""
    pasta = Path(config.HISTORICO_DIR)
    pasta.mkdir(parents=True, exist_ok=True)
    caminhos = []
    for nome, conteudo in enviadas:
        destino = pasta / regras.sanitizar_nome_arquivo(nome)
        destino.write_bytes(conteudo)
        caminhos.append(destino)
    return caminhos


def importar_planilhas(caminhos: list[Path], fornecedor: str, tarefa: tarefas.Tarefa) -> dict:
    """Tarefa `importar`: etapa 1 (planilhas → catálogo) e, com chave da IA,
    etapa 2 (padronização dos nomes). Devolve o resumo que a tela mostra;
    nunca levanta — uma falha vira `{"erro": mensagem}` (app.py L3829-3885)."""
    tarefa.atualizar(0.05, f"Importando {len(caminhos)} planilha(s)…")
    try:
        stats = importador.importar(caminhos, fornecedor=fornecedor)
    except Exception as exc:
        _log.exception("falha ao importar planilhas")
        return {"erro": erros.mensagem_amigavel(exc, "importar as planilhas")}
    # A etapa 2 só roda com chave da IA; o texto de sucesso muda conforme isso
    # (anunciar "Etapa 1 de 2" sem a etapa 2 acontecer confundia o operador).
    vai_padronizar = bool(credenciais.carregar_chave_api("gemini"))
    resultado = {"resumo": stats.resumo(), "avisos": list(stats.avisos),
                 "duas_etapas": vai_padronizar, "falhos": 0, "erro_padronizacao": None}
    _log.info("IMPORTAÇÃO: %s", resultado["resumo"])
    if not vai_padronizar:
        return resultado
    tarefa.atualizar(0.4, "Etapa 2 de 2 — preparando o catálogo para a busca…",
                     etapa1="Etapa 1 de 2 — importação concluída e salva.",
                     resumo=resultado["resumo"])
    try:
        resultado["falhos"] = _padronizar(tarefa, base=0.4).get("falhos") or 0
    except Exception as exc:
        _log.exception("falha ao padronizar catálogo")
        resultado["erro_padronizacao"] = erros.mensagem_amigavel(exc)
    return resultado


def _padronizar(tarefa: tarefas.Tarefa, base: float = 0.0) -> dict:
    def _progresso(feitos: int, total: int) -> None:
        tarefa.atualizar(base + (1 - base) * min(feitos / max(total, 1), 1.0),
                         f"Etapa 2 de 2 — preparando o catálogo… {feitos}/{total}")
    return alinhador.normalizar_catalogo(callback=_progresso)


async def importar(request: Request):
    form = await request.form()
    arquivos = [a for a in form.getlist("planilhas") if getattr(a, "filename", "")]
    fornecedor = form.get("fornecedor") or "almeida"
    if not arquivos:
        return falha("Escolha ao menos uma planilha (.xlsx) para importar.")
    if fornecedor not in regras.FORNECEDORES:
        return falha("Escolha o fornecedor principal das planilhas.")
    fora = [a.filename for a in arquivos if not a.filename.lower().endswith(".xlsx")]
    if fora:
        return falha(f"Só planilhas do Excel (.xlsx) podem ser importadas. Tire da lista: "
                     f"{', '.join(fora)}.")
    if tarefas.ativa("importar", None) or tarefas.ativa("padronizar", None):
        return falha("Já há uma importação em andamento. Espere terminar para enviar "
                     "outras planilhas.")
    enviadas = [(a.filename, await a.read()) for a in arquivos]
    try:
        caminhos = await run_in_threadpool(_guardar_planilhas, enviadas)
    except Exception as exc:
        _log.exception("falha ao salvar planilhas para importação")
        return falha(erros.mensagem_amigavel(exc, "salvar as planilhas para importação"), 500)
    tarefa = tarefas.iniciar("importar", None, "Importando planilhas para o catálogo",
                             lambda t: importar_planilhas(caminhos, fornecedor, t))
    return ok(tarefa=tarefa.id, recarregar=True)


def padronizar(request: Request):
    """[dev] Padroniza sob demanda os nomes que ficaram sem forma padronizada
    (app.py L3769 — só com PREGOES_DEBUG)."""
    if not _DEBUG:
        return falha("Endereço não encontrado.", 404)
    if tarefas.ativa("importar", None):
        return falha("Há uma importação em andamento. Espere terminar.")
    tarefa = tarefas.iniciar("padronizar", None, "Padronizando os nomes do catálogo",
                             lambda t: _padronizar(t))
    return ok(tarefa=tarefa.id, recarregar=True)


# --------------------------------------------------------------------------
# Variação de preços
# --------------------------------------------------------------------------

def variacao(request: Request):
    q = request.query_params
    fornecedor = q.get("fornecedor") if q.get("fornecedor") in regras.FORNECEDORES else ""
    periodo = q.get("periodo") if q.get("periodo") in regras.PERIODOS else "tudo"
    ordem = "queda" if q.get("ordem") == "queda" else "alta"
    consulta = (q.get("q") or "").strip()
    with db.get_conn() as conn:
        tem_historico = conn.execute(
            "SELECT 1 FROM precos_historico LIMIT 1").fetchone() is not None
        todas = regras.variacoes_de_preco(conn, fornecedor, periodo, consulta, ordem)
        visiveis, pagina_atual, paginas = regras.paginar(
            todas, regras.inteiro(q.get("p")), regras.VARIACOES_POR_PAGINA)
        regras.anexar_series(conn, visiveis, periodo)
    filtros = {"fornecedor": fornecedor, "periodo": "" if periodo == "tudo" else periodo,
               "ordem": "" if ordem == "alta" else ordem, "q": consulta}
    return pagina(
        request, "variacao.html", secao="precos",
        variacoes=visiveis, resumo=regras.resumo_variacoes(todas),
        tem_historico=tem_historico, fornecedor=fornecedor, periodo=periodo,
        ordem=ordem, consulta=consulta, pagina_atual=pagina_atual, paginas=paginas,
        prefixo=regras.prefixo_pagina("/precos", **filtros),
        link=lambda **mudancas: regras.endereco("/precos", **{**filtros, **mudancas}),
        filtrando=bool(fornecedor or consulta or periodo != "tudo"))


rotas = [
    Route("/catalogo", catalogo),
    Route("/catalogo/importar", importar, methods=["POST"]),
    Route("/catalogo/padronizar", padronizar, methods=["POST"]),
    Route("/precos", variacao),
]
