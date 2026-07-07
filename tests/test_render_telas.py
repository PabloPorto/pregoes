"""Testes de RENDER das telas com o Streamlit AppTest (2026-07).

Por que existem: vários bugs desta fase nasceram na CAMADA DE UI (app.py) —
código que só roda no RENDER/CLIQUE real, não nas funções puras já testadas.
Exemplos que passaram pela suíte antiga e quebraram em campo: 'Identificar NCM'
com ValueError (desempacotava tupla errada), rascunho com DataFrame que sumia,
estado de busca por-item vazando entre pregões. AppTest renderiza a tela de
verdade (sem navegador) e clica botões — pega essa classe inteira.

Isolamento: DB/dados/log/rascunhos apontam para um tmp; sem rede (o scraper é
mockado onde um clique o chamaria). conftest.py já neutraliza o log de produção.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP = str(Path(__file__).resolve().parent.parent / "pregoes" / "ui" / "app.py")


def _limpar_ui_de_sys_modules() -> None:
    """Expulsa os módulos ``pregoes.ui.*`` do cache de import.

    Por quê: outros arquivos de teste (test_fix_app.py, test_historico_exports.py)
    importam ``pregoes.ui.app`` com um Streamlit DUBLÊ em ``sys.modules``. Eles
    restauram o ``streamlit``, mas ``pregoes.ui.style`` fica cacheado amarrado ao
    dublê — seu ``import streamlit as st`` já resolveu para o stub. Quando o
    AppTest re-executa o app.py, o ``from pregoes.ui import style`` reusa esse
    módulo poluído, e ``style.header_pagina`` escreve no dublê (sumindo do
    render) enquanto o ``st.markdown`` direto do app funciona — bug fantasma que
    só aparece quando OUTRO arquivo rodou antes (falha dependente de ordem).
    Expulsar aqui força o AppTest a reimportar ``style`` amarrado ao Streamlit
    real. Idempotente e barato."""
    for nome in [m for m in sys.modules if m.startswith("pregoes.ui")]:
        del sys.modules[nome]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Aponta banco/dados/rascunhos/credenciais para um tmp — o render não toca
    em nada de produção. Devolve o tmp_path para os testes semearem estado."""
    _limpar_ui_de_sys_modules()
    from pregoes import config, credenciais, db, rascunho
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "pregoes.db")
    monkeypatch.setattr(config, "EXPORTS_DIR", data / "exports")
    (data / "exports").mkdir()
    monkeypatch.setattr(credenciais, "CAMINHO", data / "credenciais.json")
    monkeypatch.setattr(rascunho, "DIR", data / "rascunhos")
    monkeypatch.setattr(rascunho, "CAMINHO", data / "rascunho.pkl")
    monkeypatch.setattr(db, "backup", lambda *a, **k: None)   # sem backup diário
    db.init_db(config.DB_PATH)
    credenciais.salvar("almeida", "x@y.com", "senha")   # 1 fornecedor p/ a busca
    return tmp_path


def _seed_busca_precos(at):
    """Semeia um pregão na tela de PREÇOS com busca feita: 3 itens, resultados,
    e uma seleção FETCHÁVEL (tem url, sem NCM) para o botão 'Identificar NCM'."""
    from pregoes.exportador import SelecaoFornecedor
    from pregoes.extrator import ItemExtraido
    from pregoes.scraper import ResultadoBusca
    itens = [ItemExtraido(item_numero=i + 1, descricao=f"CABO FLEXIVEL {i}",
                          unidade="M", quantidade=10.0) for i in range(3)]
    at.session_state["itens_revisados"] = itens
    at.session_state["itens_edital"] = itens
    at.session_state["orgao"] = "Prefeitura X"
    at.session_state["numero"] = "001/2026"
    at.session_state["tela"] = "busca_precos"
    r0 = ResultadoBusca(descricao_encontrada="CABO FLEXIVEL 0", marca="SIL",
                        preco=7.67, codigo_fornecedor="9185", confianca="exato",
                        fornecedor="almeida",
                        url="https://www.almeidadistribuidor.com.br/p/9185")
    at.session_state["resultados_busca"] = {0: [r0], 1: [], 2: []}
    at.session_state["selecoes"] = {0: SelecaoFornecedor(
        preco=7.67, codigo_fornecedor="9185", marca="SIL", ncm=None,
        url="https://www.almeidadistribuidor.com.br/p/9185", fornecedor="almeida")}


def _at(env):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    return at


def test_render_home_sem_crash(env):
    at = _at(env)
    assert not at.exception, f"Home quebrou no render: {at.exception}"
    textos = " ".join(m.value for m in at.markdown) + " ".join(
        getattr(h, "value", "") for h in at.title)
    assert "Início" in textos or "Visão geral" in " ".join(
        c.value for c in at.caption)


def test_render_busca_precos_sem_crash(env):
    """A tela de PREÇOS (item cards + painel de markup + escolha visível +
    chips de pagamento) renderiza com um pregão real, sem exceção. Pega crashes
    de render que só apareciam no clique/uso real."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()                       # boot na home
    _seed_busca_precos(at)
    at.run()                       # renderiza busca_precos
    assert not at.exception, f"busca_precos quebrou no render: {at.exception}"
    # o botão de Identificar NCM aparece (há seleção fetchável sem NCM)
    assert any(b.key == "btn_ncm" for b in at.button), "botão Identificar NCM sumiu"


def test_clicar_identificar_ncm_nao_quebra(env, monkeypatch):
    """CLICAR 'Identificar NCM' não pode quebrar (regressão do ValueError
    'too many values to unpack' de 2026-07 — o wrapper _identificar_ncm que só
    roda no clique). consultar_ncms é mockado (sem rede)."""
    monkeypatch.setattr(
        "pregoes.scraper.consultar_ncms",
        lambda pedidos, progresso=None: ({p[0]: "39172300" for p in pedidos}, set()))
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    _seed_busca_precos(at)
    at.run()
    at.button(key="btn_ncm").click().run()
    assert not at.exception, f"clique em Identificar NCM quebrou: {at.exception}"
    # o NCM foi aplicado na seleção (39172300 não é ST → sem crash de classificação)
    assert at.session_state["selecoes"][0].ncm == "39172300"


def test_home_esconde_pregao_finalizado_e_mostra_aberto(env):
    """Início não LISTA um pregão que JÁ está no histórico (planilha gerada); um
    pregão em aberto continua aparecendo. E o rascunho do finalizado NÃO é apagado
    (o Início pode ocultar, jamais destruir trabalho — regressão do 'reaparece no
    Início após gerar', repetidamente relatada, + achado 2026-07 de que o filtro
    deletava). Fim a fim, render + db."""
    from pregoes import config, db, rascunho
    # pregão FINALIZADO no histórico (edital + planilha gerada)
    with db.get_conn(config.DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO editais (orgao, numero_pregao, arquivo_pdf, status) "
            "VALUES ('Prefeitura X', '001/2026', 'x.pdf', 'precificado')")
        db.export_log_gravar(conn, cur.lastrowid, "p.xlsx", 3, 1, 1.45)
    # rascunhos: um do finalizado + um em aberto
    rascunho.salvar_pregao({"orgao": "Prefeitura X", "numero": "001/2026",
                            "itens_edital": [1, 2, 3]})
    rascunho.salvar_pregao({"orgao": "Prefeitura Y", "numero": "002/2026",
                            "itens_edital": [1]})
    at = _at(env)
    assert not at.exception
    txt = " ".join(m.value for m in at.markdown)
    assert "002/2026" in txt, "pregão em aberto deveria aparecer no Início"
    assert "001/2026" not in txt, "pregão finalizado NÃO pode aparecer no Início"
    # ambos os arquivos CONTINUAM no disco — o Início oculta, não deleta.
    slugs = {p.stem for p in rascunho.DIR.glob("*.pkl")}
    assert "002-2026-prefeitura-y" in slugs
    assert "001-2026-prefeitura-x" in slugs, "o rascunho finalizado NÃO pode ser apagado"


def test_render_revisao_sem_crash(env):
    """A tela de REVISÃO (data_editor + métricas + stepper) renderiza com uma
    extração real, sem exceção."""
    from streamlit.testing.v1 import AppTest
    from pregoes.extrator import ItemExtraido, ResultadoExtracao
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    itens = [ItemExtraido(item_numero=i + 1, descricao=f"ITEM {i}",
                          unidade="UN", quantidade=1.0) for i in range(3)]
    at.session_state["extracao"] = ResultadoExtracao(
        arquivo="x.pdf", itens=itens, tabelas_brutas=[],
        mapa_colunas_detectado={}, avisos=[], confianca_global=0.99)
    at.session_state["itens_revisados"] = itens
    at.session_state["orgao"] = "P"
    at.session_state["numero"] = "1"
    at.session_state["tela"] = "revisao"
    at.run()
    assert not at.exception, f"revisão quebrou no render: {at.exception}"
