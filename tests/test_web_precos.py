"""Interface web — etapa Preços: busca em lote (tarefa), escolha por item,
buscas manuais, filtros e a paridade das regras com o Streamlit.

Sem rede: o scraper é trocado por dublês (`busca`). O `no_navegador` de
mentira chama a função direto; dois testes usam o de verdade para provar o
caminho real do cancelamento.
"""
from __future__ import annotations

import sys
import threading
import time
import types
import warnings

import pytest

from pregoes import config, credenciais, db, scraper
from pregoes.exportador import SelecaoFornecedor
from pregoes.extrator import ItemExtraido
from pregoes.matcher import ProdutoCatalogo
from pregoes.scraper import ErroBusca, ResultadoBusca, ResultadoLote
from pregoes.web import regras_selecao as regras
from pregoes.web import rotas_precos, servico_busca, tarefas
from pregoes.web.pregao import Pregao


# ============================ dublês e apoio ================================

def _r(desc, marca, preco, cod, forn="almeida", conf="exato", **kw):
    return ResultadoBusca(desc, marca, preco, cod, conf, forn, **kw)


class _Sessao:
    """Sessão logada de mentira (o que `obter_sessao_viva` devolve)."""

    def __init__(self, nome, cookie=None):
        self.nome = nome
        self._http_cookie = cookie
        self._http_only = False


@pytest.fixture
def busca(web, monkeypatch):
    """Almeida e Dantas configuradas + scraper de mentira. `responder(desc,
    fornecedor) -> opções` decide o que cada item acha."""
    credenciais.salvar("almeida", "a@teste", "segredo-a")
    credenciais.salvar("dantas", "d@teste", "segredo-d")
    b = types.SimpleNamespace(
        chamadas=[], sessoes={}, fechadas=[], erros={}, cookie=None, login_falha=set(),
        a_cada_item=None,
        responder=lambda desc, forn: [_r(f"{desc} {forn.upper()}", "MARCA", 10.0,
                                         f"{forn[0].upper()}-{desc[-1]}", forn)])

    def _sessao(nome, aguardando=None):
        if nome in b.login_falha:
            raise RuntimeError(f"falha no login da {nome}")
        return b.sessoes.setdefault(nome, _Sessao(nome, b.cookie))

    def _lote(itens, fornecedores, *, progresso_callback=None, **_kw):
        forn = fornecedores[0]
        b.chamadas.append((forn.nome, [it.descricao for it in itens]))
        resultados = {}
        for pos, it in enumerate(itens):
            resultados[pos] = list(b.responder(it.descricao, forn.nome))
            if b.a_cada_item:
                b.a_cada_item(forn.nome, pos)
            if progresso_callback:
                progresso_callback(pos + 1, len(itens), it.descricao[:60])
        return ResultadoLote(resultados=resultados, erros=list(b.erros.get(forn.nome, [])),
                             cache_hits=1, cache_misses=len(itens))

    monkeypatch.setattr(scraper, "no_navegador",
                        lambda fn, *a, aguardando=None, **kw: fn(*a, **kw))
    monkeypatch.setattr(scraper, "obter_sessao_viva", _sessao)
    monkeypatch.setattr(scraper, "fechar_sessao_viva", b.fechadas.append)
    monkeypatch.setattr(scraper, "buscar_precos_para_itens", _lote)
    b.web = web
    return b


def _esperar(slug, tipo="busca"):
    for _ in range(500):
        if tarefas.ativa(tipo, slug) is None:
            return next(t for t in tarefas.do_pregao(slug) if t.tipo == tipo)
        time.sleep(0.01)
    raise AssertionError("a tarefa não terminou")


def _buscar(web, slug, **dados):
    r = web.post(f"/pregao/{slug}/precos/buscar", json=dados)
    assert r.status_code == 200, r.text
    return _esperar(slug)


def _pregao(web, n=3):
    """Pregão com `n` itens, TODOS marcados para a busca."""
    p = web.pregao(n_itens=n)
    p.definir_itens(p.itens_edital, list(range(n)))
    p.salvar()
    return p


def _com_busca(web, resultados, **estado):
    """Pregão já buscado (grava direto no rascunho, sem tarefa)."""
    p = _pregao(web, len(resultados))
    p.estado["resultados_busca"] = resultados
    regras.selecoes_default(p.itens_revisados, resultados, p.selecoes)
    p.estado.update(estado)
    p.salvar()
    return p


# ============================ guardas e pré-busca ===========================

def test_guardas_da_tela(web):
    assert web.get("/pregao/nao-existe/precos").status_code == 404

    p = web.pregao(n_itens=2)
    p.definir_itens(p.itens_edital, [])
    p.salvar()
    r = web.get(f"/pregao/{p.slug}/precos")
    assert r.status_code == 200
    assert "Nenhum item para buscar. Volte para a revisão." in r.text
    assert "Voltar para revisão" in r.text and "Iniciar busca" not in r.text

    p = _pregao(web)
    r = web.get(f"/pregao/{p.slug}/precos")
    assert "Nenhum fornecedor configurado. Vá em <b>Configurações</b>" in r.text
    assert "Iniciar busca" not in r.text
    r = web.post(f"/pregao/{p.slug}/precos/buscar", json={})
    assert r.status_code == 400 and "Nenhum fornecedor configurado" in r.json()["erro"]
    assert web.post("/pregao/nao-existe/precos/buscar", json={}).status_code == 404


def test_pre_busca_resumo_estimativa_e_condicao_de_pagamento(busca, web):
    credenciais.salvar_preferencia_pagamento("almeida", "7", "PIX", "", "")
    p = _pregao(web, 3)
    t = web.get(f"/pregao/{p.slug}/precos").text
    assert "Escolha a marca e o preço de cada item." in t
    for trecho in ("Itens a buscar", "Almeida + Dantas", "Cache de preço",
                   f"{config.CACHE_PRECO_DIAS} dias", "Iniciar busca", "Voltar para revisão",
                   "Tempo estimado: poucos minutos", "pode fechar esta página",
                   "Almeida: PIX",
                   "Sem condição de pagamento configurada para <b>Dantas</b>"):
        assert trecho in t, trecho
    assert "[dev]" not in t


def test_estimativa_segue_o_limite_dos_sites():
    livres, janela = config.SCRAPER_MAX_CONSULTAS_POR_JANELA, config.SCRAPER_JANELA_SEGUNDOS
    assert (livres, janela) == (80, 600)             # a conta abaixo supõe 80 / 10 min
    # medido em campo: 86 itens nos 2 fornecedores = 32 min (1,6 consulta por item)
    e = regras.estimativa_busca(86, 2)
    assert e["consultas"] == 276 and e["levas"] == 4 and e["segundos"] == 30 * 60
    assert e["texto"] == "cerca de 30 minutos"
    e = regras.estimativa_busca(500, 2)
    assert e["consultas"] == 1600 and e["segundos"] == 19 * janela
    assert e["texto"] == "cerca de 3 horas"
    assert regras.estimativa_busca(20, 2)["texto"] == "poucos minutos"     # cabe na 1ª leva
    assert regras.estimativa_busca(20, 2)["segundos"] == 0
    assert regras.estimativa_busca(60, 1)["texto"] == "cerca de 10 minutos"
    assert [regras.espera_em_minutos(s) for s in (5, 16, 95, 590)] == [1, 1, 2, 10]


# ============================ busca em lote =================================

def test_busca_completa_grava_resultados_e_escolhas_default(busca, web, caplog):
    def responder(desc, forn):
        if desc.endswith("3"):
            return []
        if desc.endswith("2") and forn == "dantas":
            return [_r("PRODUTO PARECIDO", "ZETA", 4.0, "D-2", "dantas", "variacao")]
        return [_r(f"{desc} {forn}", "ACME", 10.0 if forn == "almeida" else 12.0,
                   f"{forn}-1", forn)]
    busca.responder = responder
    p = _pregao(web, 3)
    with caplog.at_level("INFO", logger="pregoes.web"):
        t = _buscar(web, p.slug)
    assert t.estado == "concluida" and t.fracao == 1.0
    assert [c[0] for c in busca.chamadas] == ["almeida", "dantas"]      # sequencial
    assert t.extra["stats"] == [["almeida", 2, 0, 1], ["dantas", 1, 1, 1]]
    assert t.extra["por_fornecedor"]["dantas"] == (3, 3)
    assert t.resultado["texto"] == ("Busca concluída — 3 itens processados "
                                    "(2 do cache, 6 buscas reais).")
    assert "LOTE: 3 itens" in caplog.text

    p = Pregao.abrir(p.slug)
    res = p.resultados_busca
    assert set(res) == {0, 1, 2} and res[2] == []
    assert [o.fornecedor for o in res[0]] == ["almeida", "dantas"]
    # escolha default = melhor opção de cada item COM opções
    assert set(p.selecoes) == {0, 1}
    for i in (0, 1):
        melhor = regras.melhor_opcao(p.itens_revisados[i].descricao, res[i])
        assert p.selecoes[i] == regras.selecao_de(melhor)
    assert p["erros_busca"] == [] and p["avisos_lote_vazio"] == [] and p["avisos_pagamento"] == {}
    assert p.tela_de_trabalho() == "busca_precos"

    html = web.get(f"/pregao/{p.slug}/precos?fim={t.id}").text
    assert "Busca concluída — 3 itens processados" in html
    for trecho in ("Com preço", "Por variação", "Sem resultado", 'id="item-0"', 'id="item-2"',
                   "Buscar preço do 1 item sem resultado",
                   "2 de 3</b> itens com marca/preço selecionados.",
                   "Continuar para a planilha", f"/pregao/{p.slug}/planilha",
                   "A busca teve problemas? Re-buscar ou voltar"):
        assert trecho in html, trecho


def test_busca_paralela_quando_todas_as_sessoes_tem_http(busca, web):
    busca.cookie = "PHPSESSID=x"
    vistos = []
    busca.a_cada_item = lambda nome, pos: vistos.append(
        (nome, busca.sessoes[nome]._http_only, threading.current_thread().name))
    p = _pregao(web, 4)
    t = _buscar(web, p.slug)
    assert t.estado == "concluida"
    assert {v[0] for v in vistos} == {"almeida", "dantas"}
    assert all(v[1] for v in vistos)                      # workers só em modo HTTP
    assert {v[2] for v in vistos} == {"busca-almeida", "busca-dantas"}   # 1 thread cada
    assert not any(s._http_only for s in busca.sessoes.values())         # devolvido
    assert t.extra["por_fornecedor"] == {"almeida": (4, 4), "dantas": (4, 4)}
    assert t.extra["prog_almeida"] == "4 de 4"
    assert t.extra["resumo_dantas"] == "4 encontrados · 0 por variação · 0 sem resultado"
    p = Pregao.abrir(p.slug)
    assert all(len(p.resultados_busca[i]) == 2 for i in range(4))
    assert len(p.selecoes) == 4


def test_falha_de_um_fornecedor_nao_derruba_o_outro(busca, web):
    busca.login_falha = {"dantas"}
    p = _pregao(web, 6)
    t = _buscar(web, p.slug)
    assert t.estado == "concluida"
    assert busca.fechadas == ["dantas"]
    p = Pregao.abrir(p.slug)
    assert all(len(p.resultados_busca[i]) == 1 for i in range(6))
    erro = p["erros_busca"][0]
    assert (erro.indice_item, erro.fornecedor, erro.descricao_resumo) == (
        -1, "dantas", "(login/inicialização)")
    assert "Traceback" not in erro.mensagem
    # zero em TODOS os itens de um lote >= 5 = aviso de site instável
    assert p["avisos_lote_vazio"] == regras.avisos_lote_vazio([("dantas", 0, 0, 6)], 6)
    html = web.get(f"/pregao/{p.slug}/precos").text
    assert "Ver erros técnicos (1)" in html and "Fornecedor inteiro" in html
    assert "nenhum resultado em NENHUM dos 6 itens" in html


def test_avisos_de_recusa_e_de_pagamento_chegam_a_tela(busca, web):
    busca.sessoes["almeida"] = _Sessao("almeida")
    busca.sessoes["almeida"]._pagamento_status = ("nao_pegou", "PIX")
    busca.erros = {"dantas": [ErroBusca(1, "dantas", "PRODUTO DE TESTE 2",
                                        f"{scraper._MENSAGEM_RECUSA} (excesso de consultas)")]}
    p = _pregao(web, 2)
    _buscar(web, p.slug)
    p = Pregao.abrir(p.slug)
    assert "a condição de pagamento NÃO foi aplicada (configurada: PIX)" in \
        p["avisos_pagamento"]["almeida"]
    assert "o site recusou consultas" in p["avisos_lote_vazio"][0]
    html = web.get(f"/pregao/{p.slug}/precos").text
    assert "a condição de pagamento NÃO foi aplicada" in html
    assert "<b>1 itens</b> ficaram sem preço deste fornecedor" in html
    assert "Item <b>2</b> (dantas)" in html                # nº do edital, não a posição


def test_busca_incremental_mantem_escolhas_e_so_preenche_o_que_faltava(busca, web):
    achados = {"PRODUTO DE TESTE 1"}
    busca.responder = lambda desc, forn: (
        [_r(f"{desc} A", "ACME", 10.0, f"{forn}-A", forn),
         _r(f"{desc} B", "BETA", 20.0, f"{forn}-B", forn)] if desc in achados else [])
    p = _pregao(web, 4)
    _buscar(web, p.slug)
    p = Pregao.abrir(p.slug)
    assert set(p.selecoes) == {0}
    # o operador troca a escolha do item 0 e marca o item 3 como "não usar"
    cara = next(o for o in p.resultados_busca[0] if o.marca == "BETA" and o.fornecedor == "dantas")
    assert web.post(f"/pregao/{p.slug}/precos/escolher",
                    json={"i": 0, "k": regras.token_opcao(cara)}).status_code == 200
    assert web.post(f"/pregao/{p.slug}/precos/nao-usar", json={"i": 3, "usar": False}).json()["ok"]
    html = web.get(f"/pregao/{p.slug}/precos").text
    assert "Buscar preços dos 2 itens sem resultado" in html

    busca.chamadas.clear()
    achados.update({"PRODUTO DE TESTE 2", "PRODUTO DE TESTE 4"})
    t = _buscar(web, p.slug, faltantes=True)
    assert t.estado == "concluida"
    # só os itens sem resultado e fora de "não usar" foram ao scraper
    assert busca.chamadas == [("almeida", ["PRODUTO DE TESTE 2", "PRODUTO DE TESTE 3"]),
                              ("dantas", ["PRODUTO DE TESTE 2", "PRODUTO DE TESTE 3"])]
    p = Pregao.abrir(p.slug)
    assert p.selecoes[0] == regras.selecao_de(cara)             # escolha intacta
    assert len(p.resultados_busca[0]) == 4                       # resultados intactos
    assert len(p.resultados_busca[1]) == 4 and 1 in p.selecoes   # achou agora → default
    assert p.resultados_busca[2] == [] and 2 not in p.selecoes
    assert p.resultados_busca[3] == [] and 3 in p.itens_nao_usar and 3 not in p.selecoes


def test_busca_completa_nao_roda_por_cima_de_outra(busca, web):
    p = _com_busca(web, {0: [_r("X", "M", 1.0, "C")], 1: []})
    r = web.post(f"/pregao/{p.slug}/precos/buscar", json={})
    assert r.json() == {"ok": True, "recarregar": True} and busca.chamadas == []


def test_itens_alterados_durante_a_busca_descartam_o_lote(busca, web):
    p = _pregao(web, 3)

    def mexer(nome, pos):
        if nome == "dantas" and pos == 0:
            outro = Pregao.abrir(p.slug)
            outro.definir_itens(outro.itens_edital, [0, 2])
            outro.salvar()
    busca.a_cada_item = mexer
    t = _buscar(web, p.slug)
    assert t.resultado == {"tipo": "alerta", "texto": servico_busca.MSG_ITENS_MUDARAM}
    assert not Pregao.abrir(p.slug).tem_busca
    assert "A lista de itens deste pregão mudou" in web.get(f"/pregao/{p.slug}/precos").text


# ============================ busca em andamento / cancelar ==================

def test_tela_durante_a_busca_e_bloqueio_das_escolhas(busca, web, monkeypatch):
    solta, entrou = threading.Event(), threading.Event()

    def segurar(nome, pos):
        entrou.set()
        assert solta.wait(5)
    busca.a_cada_item = segurar
    p = _com_busca(web, {0: [_r("X", "M", 1.0, "C")], 1: [], 2: []})
    web.post(f"/pregao/{p.slug}/precos/buscar", json={"faltantes": True})
    try:
        assert entrou.wait(5)
        t = tarefas.ativa("busca", p.slug)
        html = web.get(f"/pregao/{p.slug}/precos").text
        for trecho in (f'data-tarefa="{t.id}"', "Cancelar busca", f"/api/tarefas/{t.id}/cancelar",
                       'data-extra="prog_almeida"', 'data-extra="resumo_dantas"',
                       "A busca roda no servidor", f"/pregao/{p.slug}/precos?fim={t.id}"):
            assert trecho in html, trecho
        assert 'id="item-0"' not in html
        # sem fila de consultas: o aviso de leva existe, escondido
        assert 'class="p-leva oculto" id="p-leva"' in html and 'data-extra="espera_s"' in html
        estado = web.get(f"/api/tarefas/{t.id}").json()
        assert estado["tipo"] == "busca" and "pausa_s" in estado["extra"]
        assert estado["extra"]["espera_s"] == 0
        # lote esperando a janela dos sites: aviso calmo, já com os minutos
        monkeypatch.setattr(scraper, "espera_da_janela", lambda: 290.0)
        html = web.get(f"/pregao/{p.slug}/precos").text
        assert 'class="p-leva" id="p-leva"' in html
        assert "Respeitando o limite de consultas dos fornecedores" in html
        assert 'sai em ~<b id="p-leva-min">5</b> min. Está tudo certo; pode fechar a página.' in html
        assert web.get(f"/api/tarefas/{t.id}").json()["extra"]["espera_s"] == 290
        monkeypatch.setattr(scraper, "espera_da_janela", lambda: 9.0)     # ritmo normal
        assert 'class="p-leva oculto"' in web.get(f"/pregao/{p.slug}/precos").text
        # com a busca rodando, nada de mexer nas escolhas
        for caminho, dados in (("escolher", {"i": 0, "k": "x"}), ("nao-usar", {"i": 0}),
                               ("refazer", {})):
            r = web.post(f"/pregao/{p.slug}/precos/{caminho}", json=dados)
            assert r.status_code == 409 and "busca de preços em andamento" in r.json()["erro"]
    finally:
        solta.set()
    assert _esperar(p.slug).estado == "concluida"


def test_cancelar_busca_completa_nao_toca_no_rascunho(busca, web):
    p = _pregao(web, 5)
    antes = dict(Pregao.abrir(p.slug).estado)

    def cancelar_no_2(nome, pos):
        if pos == 1:
            assert web.post(f"/api/tarefas/{tarefas.ativa('busca', p.slug).id}/cancelar").json()["ok"]
    busca.a_cada_item = cancelar_no_2
    t = _buscar(web, p.slug)
    assert t.estado == "cancelada" and t.resultado is None
    assert busca.chamadas == [("almeida", [f"PRODUTO DE TESTE {i}" for i in range(1, 6)])]
    depois = Pregao.abrir(p.slug)
    assert depois.estado == antes and not depois.tem_busca
    assert busca.fechadas == []                    # cancelar não derruba sessão logada
    html = web.get(f"/pregao/{p.slug}/precos?fim={t.id}").text
    assert "Busca cancelada. Nada foi alterado neste pregão" in html and "Iniciar busca" in html


def test_cancelar_busca_incremental_mantem_o_que_ja_havia(busca, web):
    busca.cookie = "PHPSESSID=x"                   # modo paralelo
    p = _com_busca(web, {0: [_r("X", "M", 1.0, "C")], 1: [], 2: [], 3: []})
    antes = Pregao.abrir(p.slug).estado
    busca.a_cada_item = lambda nome, pos: tarefas.cancelar(tarefas.ativa("busca", p.slug).id)
    t = _buscar(web, p.slug, faltantes=True)
    assert t.estado == "cancelada"
    assert Pregao.abrir(p.slug).estado == antes
    assert busca.fechadas == []
    assert not any(s._http_only for s in busca.sessoes.values())


class _FornHTTP:
    """Fornecedor de mentira com transporte HTTP — liga o pool de itens do
    `buscar_precos_para_itens` DE VERDADE (como em test_paralelo_itens)."""

    def __init__(self, nome, ao_buscar):
        self.nome = nome
        self._http_cookie = "PHPSESSID=x"
        self._http_only = False
        self.buscas = 0
        self._ao_buscar = ao_buscar

    def buscar(self, descricao):
        self.buscas += 1
        self._ao_buscar(self)
        return [_r(f"{descricao} ACHADO", "M", 10.0, f"C-{descricao}", self.nome)]


def test_cancelar_esvazia_o_pool_do_scraper_em_vez_de_consultar_o_lote_inteiro(
        web, monkeypatch):
    """Com o scraper REAL: sem a cópia cancelável dos itens, o pool interno
    consultaria os 300 itens antes de devolver o controle."""
    credenciais.salvar("almeida", "a@teste", "s")
    credenciais.salvar("dantas", "d@teste", "s")
    p = _pregao(web, 300)

    def ao_buscar(forn):
        if forn.buscas == 3:
            tarefas.cancelar(tarefas.ativa("busca", p.slug).id)
        time.sleep(0.002)
    sessoes = {n: _FornHTTP(n, ao_buscar) for n in ("almeida", "dantas")}
    monkeypatch.setattr(scraper, "no_navegador",
                        lambda fn, *a, aguardando=None, **kw: fn(*a, **kw))
    monkeypatch.setattr(scraper, "obter_sessao_viva", lambda nome, aguardando=None: sessoes[nome])
    fechadas = []
    monkeypatch.setattr(scraper, "fechar_sessao_viva", fechadas.append)

    t = _buscar(web, p.slug)
    assert t.estado == "cancelada"
    assert sum(s.buscas for s in sessoes.values()) < 60       # e não 600
    assert not any(s._http_only for s in sessoes.values())
    assert fechadas == []
    assert not Pregao.abrir(p.slug).tem_busca


def test_cancelar_pelo_no_navegador_de_verdade_aborta_a_thread_dona(web, monkeypatch):
    """Caminho real: o aviso de progresso levanta na thread da tarefa, o
    `no_navegador` marca a operação como interrompida e ela para no aviso
    seguinte — sem gravar nada."""
    credenciais.salvar("almeida", "a@teste", "s")
    p = _pregao(web, 3)
    abortou = threading.Event()

    def _lote(itens, fornecedores, *, progresso_callback=None, **_kw):
        try:
            for n in range(400):                       # até 4 s; para muito antes
                if n == 2:
                    tarefas.cancelar(tarefas.ativa("busca", p.slug).id)
                progresso_callback(1, len(itens), "item")
                time.sleep(0.01)
        except scraper.OperacaoInterrompida:
            abortou.set()
            raise
        return ResultadoLote(resultados={i: [] for i in range(len(itens))})

    monkeypatch.setattr(scraper, "obter_sessao_viva",
                        lambda nome, aguardando=None: _Sessao(nome))
    monkeypatch.setattr(scraper, "buscar_precos_para_itens", _lote)
    t = _buscar(web, p.slug)
    assert t.estado == "cancelada"
    assert abortou.wait(5)
    assert not Pregao.abrir(p.slug).tem_busca


# ============================ escolhas por item ==============================

def _tres_opcoes():
    return [_r("PRODUTO DE TESTE 1 TIGRE", "TIGRE", 12.0, "A-1", "almeida"),
            _r("PRODUTO DE TESTE 1 AMANCO", "AMANCO", 9.5, "D-7", "dantas", url="https://d/7",
               ncm="39172300"),
            _r("PRODUTO DE TESTE 1 KRONA", "KRONA", 15.0, "A-9", "almeida")]


def test_escolher_opcao_troca_a_selecao_e_devolve_os_pedacos(busca, web):
    p = _com_busca(web, {0: _tres_opcoes(), 1: []})
    amanco = _tres_opcoes()[1]
    r = web.post(f"/pregao/{p.slug}/precos/escolher",
                 json={"i": 0, "k": regras.token_opcao(amanco)})
    corpo = r.json()
    assert corpo["ok"] and set(corpo["html"]) >= {
        "#item-0", "#precos-resumo", "#precos-rodape", "#q-todos", "#q-sem_resultado"}
    cartao = corpo["html"]["#item-0"]
    assert cartao.lstrip().startswith("<article") and 'id="item-0"' in cartao
    assert "R$ 9,50" in cartao and "escolhido" in cartao
    assert 'id="precos-resumo"' in corpo["html"]["#precos-resumo"]
    assert "1 de 2</b>" in corpo["html"]["#precos-rodape"]
    sel = Pregao.abrir(p.slug).selecoes[0]
    assert sel == SelecaoFornecedor(preco=9.5, codigo_fornecedor="D-7", marca="AMANCO",
                                    ncm="39172300", url="https://d/7", fornecedor="dantas")

    r = web.post(f"/pregao/{p.slug}/precos/escolher", json={"i": 0, "k": "nao-existe"})
    assert r.status_code == 400 and "não está mais disponível" in r.json()["erro"]
    r = web.post(f"/pregao/{p.slug}/precos/escolher", json={"i": 99, "k": "x"})
    assert r.status_code == 400 and "não existe mais" in r.json()["erro"]
    assert web.post("/pregao/nao-existe/precos/escolher", json={"i": 0}).status_code == 404


def test_cartao_do_item_mostra_opcoes_pilulas_e_as_alem_do_corte(busca, web):
    opcoes = [_r(f"PRODUTO DE TESTE 1 MODELO {n}", f"MARCA{n}", 10.0 + n, f"A-{n}")
              for n in range(7)]
    opcoes.append(_r("PRODUTO DE TESTE 1 DO CATALOGO", "CATMARCA", 30.0, "A-77",
                     no_catalogo=True))
    opcoes.append(_r("PRODUTO DE TESTE 1 APRENDIDO", "APRMARCA", 40.0, "A-88",
                     veio_por_aprendizado=True))
    longa = "TUBO DE PVC SOLDAVEL " + "COM MUITOS DETALHES TECNICOS " * 6
    p = _com_busca(web, {0: opcoes, 1: []})
    p.itens_revisados[1].descricao = longa
    p.salvar()
    html = web.get(f"/pregao/{p.slug}/precos").text
    cartao = html.split('id="item-0"')[1].split('id="item-1"')[0]
    assert "sua escolha anterior" in cartao and "no seu catálogo" in cartao
    assert "Melhor match" not in cartao            # as 2 primeiras têm pílula mais forte
    assert "Mais barato" in cartao
    assert f"Ver outras {9 - config.MAX_OPCOES_EXIBIDAS} opções" in cartao
    assert cartao.count("data-acao=\"/pregao/%s/precos/escolher\"" % p.slug) == 8   # 9 − a escolhida
    assert "R$ 40,00" in cartao and "cód. A-88" in cartao and "Almeida" in cartao
    assert "4 opções" in cartao
    item1 = html.split('id="item-1"')[1]
    assert "ver descrição completa" in item1 and "Não encontrado" in item1
    assert "Nenhum resultado nos fornecedores." in item1
    assert "Procurar de outro jeito" in item1 and "Não usar este item" in item1
    assert "ver descrição completa" not in cartao   # descrição curta: o título basta


def test_mesmo_produto_nos_dois_fornecedores_marca_um_so_escolhido(busca, web):
    gemeas = [_r("PRODUTO DE TESTE 1", "TIGRE", 12.0, "100", "almeida"),
              _r("PRODUTO DE TESTE 1", "TIGRE", 12.0, "100", "dantas")]
    p = _com_busca(web, {0: gemeas})
    url = f"/pregao/{p.slug}/precos"
    cartao = web.post(f"{url}/escolher", json={"i": 0, "k": regras.token_opcao(gemeas[1])}
                      ).json()["html"]["#item-0"]
    assert cartao.count('class="candidato escolhido"') == 1
    assert Pregao.abrir(p.slug).selecoes[0].fornecedor == "dantas"
    escolhido = cartao.split('class="candidato escolhido"')[1].split("</div>")[0]
    assert "Dantas" in escolhido and "Almeida" not in escolhido
    # escolha antiga, gravada sem fornecedor: continua aparecendo
    q = Pregao.abrir(p.slug)
    q.selecoes[0] = SelecaoFornecedor(preco=12.0, codigo_fornecedor="100", marca="TIGRE")
    q.salvar()
    assert 'class="candidato escolhido"' in web.get(url).text


def test_nao_usar_voltar_a_usar_e_escolher_desfaz_o_nao_usar(busca, web):
    p = _com_busca(web, {0: _tres_opcoes(), 1: []})
    url = f"/pregao/{p.slug}/precos"
    cartao = web.post(f"{url}/nao-usar", json={"i": 0, "usar": False}).json()["html"]["#item-0"]
    assert "sem preço (você marcou)" in cartao
    assert "Este item ficará <b>sem preço</b> na planilha (em branco)." in cartao
    assert "Voltar a usar este item" in cartao
    q = Pregao.abrir(p.slug)
    assert q.itens_nao_usar == {0} and 0 not in q.selecoes

    # voltar a usar: só sai do conjunto — a escolha NÃO é recriada
    cartao = web.post(f"{url}/nao-usar", json={"i": 0, "usar": True}).json()["html"]["#item-0"]
    assert "Nenhuma opção escolhida — clique" in cartao
    q = Pregao.abrir(p.slug)
    assert q.itens_nao_usar == set() and 0 not in q.selecoes

    # escolher uma opção de um item marcado "não usar" desfaz a marca
    web.post(f"{url}/nao-usar", json={"i": 0, "usar": False})
    web.post(f"{url}/escolher", json={"i": 0, "k": regras.token_opcao(_tres_opcoes()[0])})
    q = Pregao.abrir(p.slug)
    assert q.itens_nao_usar == set() and q.selecoes[0].marca == "TIGRE"


# ============================ buscas manuais =================================

def test_dialogo_procurar_de_outro_jeito(busca, web):
    p = _com_busca(web, {0: _tres_opcoes(), 1: []})
    url = f"/pregao/{p.slug}/precos/item/1"
    t = web.get(f"{url}/procurar").text
    assert t.lstrip().startswith("<div data-largo")
    # uma barra de busca POR FORNECEDOR, na mesma tela (pedido do cliente:
    # não ter de escolher o fornecedor antes de buscar)
    for trecho in ("Procurar de outro jeito", "Nome ou código do produto",
                   'name="fornecedor" value="almeida"', 'name="fornecedor" value="dantas"',
                   'id="procura-estado-almeida"', 'id="procura-resultado-almeida"',
                   'id="procura-estado-dantas"', 'id="procura-resultado-dantas"',
                   "catálogo interno vazio — importe suas planilhas"):
        assert trecho in t, trecho
    assert t.count('data-async') == 2 and 'type="radio"' not in t
    assert t.count("autofocus") == 1
    # só um fornecedor configurado: só a barra dele
    credenciais.remover("dantas")
    t = web.get(f"{url}/procurar").text
    assert 'value="almeida"' in t and 'value="dantas"' not in t
    assert web.get(f"/pregao/{p.slug}/precos/item/9/procurar").status_code == 404


def test_busca_manual_no_site_mensagens_e_usar(busca, web, monkeypatch):
    p = _com_busca(web, {0: _tres_opcoes(), 1: []})
    web.post(f"/pregao/{p.slug}/precos/nao-usar", json={"i": 1, "usar": False})
    url = f"/pregao/{p.slug}/precos/item/1"
    pedidos = []

    def _site(forn, termo, limite=10):
        pedidos.append((forn, termo))
        return [_r("ALICATE DE PRESSAO 10POL", "TRAMONTINA", 45.9, "8811", forn, "variacao",
                   url="https://a/8811"),
                _r("ALICATE UNIVERSAL", "", 30.0, "8812", forn, "variacao")]
    monkeypatch.setattr(scraper, "buscar_no_site", _site)

    r = web.post(f"{url}/site", json={"termo": "  ", "fornecedor": "almeida"}).json()
    assert "Digite um nome ou código para buscar." in r["html"]["#procura-estado-almeida"]
    assert pedidos == []

    r = web.post(f"{url}/site", json={"termo": " alicate ", "fornecedor": "almeida"}).json()
    # a resposta vai para a barra da Almeida — a da Dantas fica como estava
    assert set(r["html"]) == {"#procura-estado-almeida", "#procura-resultado-almeida"}
    lista = r["html"]["#procura-resultado-almeida"]
    assert 'id="procura-resultado-almeida"' in lista
    assert pedidos == [("almeida", "alicate")]
    assert "2 resultados" in lista and "ALICATE DE PRESSAO 10POL" in lista
    assert "TRAMONTINA" in lista and "R$ 45,90" in lista and "Sem marca" in lista
    assert "cód. 8811" in lista and lista.count(">Usar</button>") == 2

    # o pacote que o botão "Usar" manda de volta
    pacote = rotas_precos._embalar(_site("almeida", "x")[0])
    adulterado = {"r": {**pacote["r"], "preco": 1.0}, "a": pacote["a"]}
    r = web.post(f"{url}/site/usar", json=adulterado)
    assert r.status_code == 400 and "Busque de novo" in r.json()["erro"]
    assert Pregao.abrir(p.slug).resultados_busca[1] == []

    corpo = web.post(f"{url}/site/usar", json=pacote).json()
    assert corpo["ok"] and corpo["fechar_modal"] and "TRAMONTINA · R$ 45,90" in corpo["aviso"]
    assert "#item-1" in corpo["html"] and "R$ 45,90" in corpo["html"]["#item-1"]
    q = Pregao.abrir(p.slug)
    assert q.resultados_busca[1] == [_site("almeida", "x")[0]]
    assert q.selecoes[1] == SelecaoFornecedor(preco=45.9, codigo_fornecedor="8811",
                                              marca="TRAMONTINA", url="https://a/8811",
                                              fornecedor="almeida")
    # defeito do Streamlit corrigido: usar uma opção tira o item de "não usar"
    assert 1 not in q.itens_nao_usar

    def _limitado(forn, termo, limite=10):
        raise scraper.SessaoHTTPExpirou("limite", status=scraper._STATUS_LIMITE)
    monkeypatch.setattr(scraper, "buscar_no_site", _limitado)
    r = web.post(f"{url}/site", json={"termo": "x", "fornecedor": "dantas"}).json()
    assert ("O site da Dantas limitou as consultas por alguns minutos (muitas buscas em pouco "
            "tempo). <b>Aguarde uns 5 minutos</b> e busque de novo.") in r["html"]["#procura-estado-dantas"]
    assert ">Usar</button>" not in r["html"]["#procura-resultado-dantas"]

    def _quebrado(forn, termo, limite=10):
        raise TimeoutError("page.goto: Timeout 30000ms exceeded")
    monkeypatch.setattr(scraper, "buscar_no_site", _quebrado)
    estado = web.post(f"{url}/site", json={"termo": "x", "fornecedor": "dantas"}).json()[
        "html"]["#procura-estado-dantas"]
    assert "Não consegui buscar no site agora (" in estado and "Tente de novo em instantes." in estado
    assert "Traceback" not in estado and "page.goto" not in estado

    monkeypatch.setattr(scraper, "buscar_no_site", lambda forn, termo, limite=10: [])
    estado = web.post(f"{url}/site", json={"termo": "x", "fornecedor": "dantas"}).json()[
        "html"]["#procura-estado-dantas"]
    assert "Nada encontrado na Dantas para esse termo." in estado
    assert "pode estar instável neste momento" in estado

    r = web.post(f"{url}/site", json={"termo": "x", "fornecedor": "outro"})
    assert r.status_code == 400


def _semear_catalogo():
    with db.get_conn() as conn:
        conn.executemany(
            "INSERT INTO produtos (id, descricao_canonica, marca, codigo_almeida, codigo_dantas) "
            "VALUES (?, ?, ?, ?, ?)",
            [(1, "tubo pvc soldavel 75mm", "TIGRE", "8811", "D500"),
             (2, "tubo pvc esgoto 100mm", "AMANCO", "8820", None),
             (3, "valvula lavatorio cromada", "DECA", None, "7001"),
             (4, "registro esfera 25mm", None, "9100", "")])
        conn.execute("INSERT INTO precos_historico (produto_id, fornecedor, preco, consultado_em) "
                     "VALUES (1, 'almeida', 31.5, '2026-05-02 10:00:00')")


def test_busca_no_catalogo_modos_e_referencia(busca, web):
    _semear_catalogo()
    credenciais.remover("dantas")
    p = _com_busca(web, {0: [], 1: []})
    url = f"/pregao/{p.slug}/precos/item/0"
    dialogo = web.get(f"{url}/procurar").text
    assert "Meus produtos" in dialogo and "4 que você já comprou" in dialogo
    assert 'id="procura-estado-catalogo"' in dialogo and "catálogo interno vazio" not in dialogo

    def procurar(q):
        return web.post(f"{url}/catalogo", json={"q": q}).json()["html"]

    vazio = procurar("")
    assert "ou um código para procurar nos seus 4 produtos." in vazio["#procura-estado-catalogo"]

    # 1) código (igual ou prefixo), uma linha por fornecedor com código
    lista = procurar("88")["#procura-resultado-catalogo"]
    assert "TIGRE" in lista and "AMANCO" in lista and "DECA" not in lista
    assert "referência da última compra" in lista and "preço atual no site" in lista
    assert "referência: R$ 31,50 (últ. compra 2026-05-02)" in lista
    assert "sem referência de preço" in lista
    assert "cód. 8811" in lista and "cód. D500" in lista
    assert lista.count("Usar (preço atual)</button>") == 2       # as linhas da Almeida
    assert "Dantas não configurada" in lista                     # sem acesso: sem botão
    # 2) palavras: contém TODAS
    lista = procurar("tubo 75")["#procura-resultado-catalogo"]
    assert "TIGRE" in lista and "AMANCO" not in lista
    # 3) nenhuma contém todas → aproximado
    assert "DECA" in procurar("valvula lavatorio dourada")["#procura-resultado-catalogo"]
    assert "Sem marca" in procurar("registro esfera")["#procura-resultado-catalogo"]
    nada = procurar("zzzz qqqq")
    assert "Nenhum produto encontrado no seu catálogo." in nada["#procura-estado-catalogo"]
    assert "Usar" not in nada["#procura-resultado-catalogo"]


def test_usar_do_catalogo_cota_ao_vivo_e_nunca_usa_preco_antigo(busca, web, monkeypatch):
    _semear_catalogo()
    p = _com_busca(web, {0: [], 1: []})
    web.post(f"/pregao/{p.slug}/precos/nao-usar", json={"i": 0, "usar": False})
    url = f"/pregao/{p.slug}/precos/item/0/catalogo/usar"
    pedido = {"produto_id": 1, "fornecedor": "almeida"}

    def _fora_do_ar(forn, codigo):
        raise RuntimeError("net::ERR_INTERNET_DISCONNECTED")
    monkeypatch.setattr(scraper, "cotar_por_codigo", _fora_do_ar)
    estado = web.post(url, json=pedido).json()["html"]["#procura-estado-catalogo"]
    assert "Não consegui consultar o preço atual agora (" in estado
    assert "O preço do catálogo <b>não</b> é usado. Tente de novo em instantes." in estado

    monkeypatch.setattr(scraper, "cotar_por_codigo", lambda forn, codigo: None)
    estado = web.post(url, json=pedido).json()["html"]["#procura-estado-catalogo"]
    assert "O código <code>8811</code> não foi encontrado agora no site da Almeida" in estado
    q = Pregao.abrir(p.slug)
    assert q.resultados_busca[0] == [] and 0 not in q.selecoes       # nada gravado

    cotados = []

    def _cotar(forn, codigo):
        cotados.append((forn, codigo))
        return _r("TUBO PVC SOLD 75MM 6M", "TIGRE", 38.9, "8811", forn, url="https://a/8811")
    monkeypatch.setattr(scraper, "cotar_por_codigo", _cotar)
    corpo = web.post(url, json=pedido).json()
    assert corpo["ok"] and corpo["fechar_modal"] and cotados == [("almeida", "8811")]
    cartao = corpo["html"]["#item-0"]
    assert "no seu catálogo" in cartao and "R$ 38,90" in cartao and "R$ 31,50" not in cartao
    q = Pregao.abrir(p.slug)
    opcao = q.resultados_busca[0][0]
    assert (opcao.preco, opcao.no_catalogo, opcao.produto_catalogo_id, opcao.confianca,
            opcao.descricao_catalogo) == (38.9, True, 1, "exato", "tubo pvc soldavel 75mm")
    assert q.selecoes[0].preco == 38.9 and q.selecoes[0].fornecedor == "almeida"
    assert 0 not in q.itens_nao_usar                 # o mesmo defeito, corrigido aqui

    r = web.post(url, json={"produto_id": 2, "fornecedor": "dantas"})   # sem código Dantas
    assert r.status_code == 400


# ============================ filtros e paginação ============================

def test_filtros_contagens_e_paginacao(busca, web):
    resultados = {}
    for i in range(60):
        if i % 10 == 0:
            resultados[i] = []
        elif i % 10 == 1:
            resultados[i] = [_r("PARECIDO", "VAR", 5.0, f"V{i}", conf="variacao")]
        else:
            resultados[i] = [_r(f"PRODUTO DE TESTE {i + 1}", "ACME", 7.0, f"A{i}")]
    p = _com_busca(web, resultados)
    web.post(f"/pregao/{p.slug}/precos/nao-usar", json={"i": 5, "usar": False})
    url = f"/pregao/{p.slug}/precos"

    html = web.get(url).text
    assert html.count('<article class="item-cartao') == rotas_precos.POR_PAGINA == 25
    for chave, n in (("todos", 60), ("com_preco", 47), ("conferir", 6), ("sem_resultado", 6),
                     ("sem_preco", 1)):
        assert f'id="q-{chave}">{n}</span>' in html, chave
    for rotulo in ("Todos", "Com preço", "A conferir", "Sem resultado", "Sem preço (você marcou)"):
        assert rotulo in html
    assert "Página 1 de 3 — itens 1–25 de 60" in html and "pagina=2" in html
    assert 'id="item-0"' in html and 'id="item-24"' in html and 'id="item-25"' not in html

    html = web.get(f"{url}?pagina=3").text
    assert html.count('<article class="item-cartao') == 10 and 'id="item-59"' in html
    assert "Página 3 de 3 — itens 51–60 de 60" in html
    assert "Página 3 de 3" in web.get(f"{url}?pagina=99").text      # limitado ao intervalo

    html = web.get(f"{url}?status=sem_resultado").text
    assert html.count('<article class="item-cartao') == 6
    assert 'id="item-0"' in html and 'id="item-50"' in html and 'id="item-1"' not in html
    assert "Limpar filtros" in html and "Página" not in html
    assert web.get(f"{url}?status=conferir").text.count('<article class="item-cartao') == 6
    assert 'id="item-5"' in web.get(f"{url}?status=sem_preco").text

    html = web.get(f"{url}?q=Teste+60").text                      # item 60 (posição 59)
    assert html.count('<article class="item-cartao') == 1 and 'id="item-59"' in html
    assert 'value="Teste 60"' in html
    # a marca e o código ESCOLHIDOS também contam: "a37" é o código do item 38
    assert 'id="item-37"' in web.get(f"{url}?q=a37").text
    html = web.get(f"{url}?q=acme&status=com_preco&pagina=2").text   # filtro + página na URL
    assert "Página 2 de 2" in html and "status=com_preco" in html and "q=acme" in html
    assert "Nenhum item neste filtro" in web.get(f"{url}?q=inexistente").text
    assert web.get(f"{url}?status=bobagem").text.count('<article class="item-cartao') == 25


def test_situacao_rapida_e_igual_a_do_cartao():
    """`situacao_de` (contagem/filtro) nunca diverge de `visao_item` (cartão)."""
    item = ItemExtraido(item_numero=1, descricao="TUBO PVC SOLDAVEL 25MM")
    exatas = [_r(f"TUBO PVC SOLDAVEL 25MM {n}", f"M{n}", 10.0 + n, f"E{n}") for n in range(5)]
    variacoes = [_r(f"CANO {n}", f"V{n}", 1.0 + n, f"V{n}", conf="variacao") for n in range(3)]
    casos = [[], exatas[:2], variacoes[:2], exatas + variacoes, exatas[:1] + variacoes,
             variacoes + exatas[:1], exatas[:3] + variacoes[:1], exatas + exatas]
    for opcoes in casos:
        for sel in (None, *(regras.selecao_de(o) for o in opcoes)):
            for nao_usar in (False, True):
                assert regras.situacao_de(item, opcoes, sel, nao_usar) == \
                    regras.visao_item(item, 0, opcoes, sel, nao_usar)["status"]


# ============================ re-busca destrutiva ============================

def test_refazer_descarta_resultados_e_escolhas(busca, web):
    for destino in ("precos", "revisao"):
        p = _com_busca(web, {0: _tres_opcoes(), 1: []}, erros_busca=[],
                       avisos_lote_vazio=["x"], avisos_pagamento={"almeida": "y"})
        web.post(f"/pregao/{p.slug}/precos/nao-usar", json={"i": 1, "usar": False})
        r = web.post(f"/pregao/{p.slug}/precos/refazer", json={"destino": destino})
        assert r.json() == {"ok": True, "ir": f"/pregao/{p.slug}/{destino}"}
        q = Pregao.abrir(p.slug)
        assert not q.tem_busca
        for chave in ("resultados_busca", "selecoes", "erros_busca", "avisos_lote_vazio",
                      "avisos_pagamento", "itens_nao_usar"):
            assert chave not in q.estado, chave
        assert len(q.itens_revisados) == 2            # os itens ficam
        assert "Iniciar busca" in web.get(f"/pregao/{p.slug}/precos").text


# ============================ paridade com o Streamlit =======================

@pytest.fixture(scope="module")
def app_antigo(tmp_path_factory):
    """`pregoes.ui.app` (o Streamlit) para conferir as portas. Importar o
    módulo executa o corpo dele (abre o banco, backup diário): quando ainda
    não foi importado por outro teste, isso é feito com os dados apontados
    para uma pasta temporária — nunca para `data/`."""
    if "pregoes.ui.app" in sys.modules:
        return sys.modules["pregoes.ui.app"]
    import pregoes
    from pregoes import backup
    tmp = tmp_path_factory.mktemp("app_antigo")
    mp = pytest.MonkeyPatch()
    try:
        for cfg in {id(c): c for c in (config, db.config, credenciais.config,
                                       sys.modules.get("pregoes.config"),
                                       getattr(pregoes, "config", None)) if c}.values():
            mp.setattr(cfg, "DATA_DIR", tmp)
            mp.setattr(cfg, "DB_PATH", tmp / "pregoes.db")
        mp.setattr(credenciais, "CAMINHO", tmp / "credenciais.json")
        mp.setattr(backup, "fazer_backup_externo", lambda *a, **k: None)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import pregoes.ui.app as app
        return app
    finally:
        mp.undo()


def _amostras_de_opcoes():
    return [
        _r("TUBO PVC SOLDAVEL 25MM 6M TIGRE", "TIGRE", 22.0, "A1"),
        _r("TUBO PVC SOLDAVEL 25MM 6M TIGRE", "TIGRE", 22.0, "A1"),            # duplicata
        _r("TUBO SOLDAVEL 25MM", "AMANCO", 19.0, "D4", "dantas"),
        _r("CILINDRO STAM 803/1401", "STAM", 55.0, "A9"),
        _r("LUVA PVC 25MM", "KRONA", 2.5, "A3", conf="variacao"),
        _r("TUBO PVC 25", "FORTLEV", 25.0, "D8", "dantas", no_catalogo=True,
           produto_catalogo_id=7),
        _r("TUBO ESGOTO 100MM", "TIGRE", 60.0, "A7", veio_por_aprendizado=True),
        _r("JOELHO 90 25MM", "", 1.2, "A5", conf="variacao"),
    ]


_DESCRICOES = ["TUBO PVC SOLDAVEL 25MM BARRA 6M", "CILINDRO PARA FECHADURA 803/1401",
               "joelho 90 graus 25mm", "", "ABRAÇADEIRA NYLON 2,5X100MM C/100 PCT. ;"]


def test_paridade_ranking_default_dedup_e_visiveis(app_antigo):
    opcoes = _amostras_de_opcoes()
    for desc in _DESCRICOES:
        for o in opcoes:
            assert regras.chave_default_opcao(desc, o) == app_antigo._chave_default_opcao(desc, o)
        assert regras.melhor_opcao(desc, opcoes) is app_antigo._melhor_opcao(desc, opcoes)
        ordenadas = sorted(opcoes, key=lambda o: regras.chave_default_opcao(desc, o))
        for sel in (None, *(regras.selecao_de(o) for o in opcoes)):
            for corte in (1, 2, config.MAX_OPCOES_EXIBIDAS, 20):
                assert regras.opcoes_visiveis(ordenadas, corte, sel) == \
                    app_antigo._opcoes_visiveis(ordenadas, corte, sel)
            for o in opcoes:
                assert regras.mesma_selecao(o, sel) == app_antigo._mesma_selecao(o, sel)
    assert regras.dedup_opcoes(opcoes) == app_antigo._dedup_opcoes(opcoes)
    assert len(regras.dedup_opcoes(opcoes)) == len(opcoes) - 1


def test_paridade_titulo_e_numero_do_item(app_antigo):
    textos = [*_DESCRICOES, None, "CILINDRO PARA FECHADURA 803/1401 CR PVC 53MM.",
              "x" * 200, "  registro de gaveta 3/4 -", "TINTA ACRÍLICA FOSCA BRANCO NEVE " * 5]
    for texto in textos:
        assert regras.titulo_item(texto) == app_antigo._titulo_item(texto)
    for numero in (None, 0, 1, 37):
        item = ItemExtraido(item_numero=numero, descricao="X")
        assert regras.numero_item_edital(item, 4) == app_antigo._numero_item_edital(item, 4)
    assert regras.numero_item_edital(ItemExtraido(item_numero=0), 4) == 0


def test_paridade_escolha_default_e_mesclagem(app_antigo, monkeypatch):
    class _Sessao(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__

    itens = [ItemExtraido(item_numero=i + 1, descricao=d) for i, d in enumerate(_DESCRICOES)]
    opcoes = _amostras_de_opcoes()
    resultados = {0: opcoes[:4], 1: opcoes[2:5], 2: [], 3: opcoes[5:], 4: []}

    # escolha default: o que o Streamlit montava na 1ª exibição da lista
    esperado = {}
    for idx, bag in resultados.items():
        if bag:
            m = app_antigo._melhor_opcao(itens[idx].descricao, bag)
            esperado[idx] = SelecaoFornecedor(preco=m.preco, codigo_fornecedor=m.codigo_fornecedor,
                                              marca=m.marca, ncm=m.ncm, url=m.url,
                                              fornecedor=m.fornecedor)
    assert regras.selecoes_default(itens, resultados) == esperado

    # mesclagem da re-busca parcial
    def estado():
        return {"resultados_busca": {k: list(v) for k, v in resultados.items()},
                "selecoes": {0: regras.selecao_de(opcoes[3])},        # escolha do operador
                "itens_nao_usar": {4}}
    novos = {2: opcoes[:2], 4: opcoes[4:6], 1: []}
    indices = [1, 2, 4]
    ss = _Sessao(estado())
    monkeypatch.setattr(app_antigo, "st", types.SimpleNamespace(session_state=ss))
    app_antigo._mesclar_busca_incremental(itens, indices, novos)

    meu = estado()
    regras.mesclar(itens, indices, novos, meu["resultados_busca"], meu["selecoes"],
                   meu["itens_nao_usar"])
    assert meu["resultados_busca"] == ss["resultados_busca"]
    assert meu["selecoes"] == ss["selecoes"]
    assert set(meu["selecoes"]) == {0, 2} and meu["selecoes"][0] == regras.selecao_de(opcoes[3])
    assert regras.faltantes(5, meu["resultados_busca"], {4}) == [1]


def test_paridade_avisos_da_busca(app_antigo):
    for total in (3, 5, 40):
        stats = [("almeida", 0, 0, total), ("dantas", 2, 1, total - 3)]
        assert regras.avisos_lote_vazio(stats, total) == app_antigo._avisos_lote_vazio(stats, total)
    recusa = f"{scraper._MENSAGEM_RECUSA} (excesso de consultas ou sessão expirada)"
    erros = [ErroBusca(0, "almeida,dantas", "A", recusa), ErroBusca(3, "dantas", "B", recusa),
             ErroBusca(3, "dantas", "B", recusa), ErroBusca(5, "almeida", "C", "Timeout"),
             ErroBusca(-1, "dantas", "(login/inicialização)", "Não consegui entrar")]
    assert regras.avisos_recusa(erros) == app_antigo._avisos_recusa(erros)
    assert len(regras.avisos_recusa(erros)) == 2 and regras.avisos_recusa([]) == []

    sessoes = {}
    for nome, status in (("almeida", ("nao_pegou", "PIX")), ("dantas", ("falhou", None)),
                         ("outro", ("ok", "BOLETO 30 DIAS")), ("mais", ("nao_conferida", "A VISTA"))):
        sessoes[nome] = types.SimpleNamespace(_pagamento_status=status)
    sessoes["sem"] = types.SimpleNamespace()
    assert regras.avisos_pagamento_de(sessoes) == app_antigo._avisos_pagamento_de(sessoes)
    assert set(regras.avisos_pagamento_de(sessoes)) == {"almeida", "dantas", "mais"}


def test_paridade_busca_no_catalogo(app_antigo):
    catalogo = [
        ProdutoCatalogo(1, "tubo pvc soldavel 75mm", "8811", "D500", "TIGRE", "UN"),
        ProdutoCatalogo(2, "tubo pvc esgoto 100mm barra 6m", "8820", None, "AMANCO", "UN"),
        ProdutoCatalogo(3, "valvula lavatorio cromada", None, "7001", "DECA", "UN"),
        ProdutoCatalogo(4, "tubo pvc 75mm", " 881 ", "", None, "UN"),
        ProdutoCatalogo(5, "registro esfera 25mm", "9100", "8899", "DOCOL", "UN"),
        ProdutoCatalogo(6, "torneira jardim plastica", "1", "2", "HERC", "UN"),
        ProdutoCatalogo(7, "tubo pvc soldavel 50mm", "8830", "D501", "TIGRE", "UN"),
        ProdutoCatalogo(8, "tubo pvc soldavel 32mm", "8831", "D502", "TIGRE", "UN"),
        ProdutoCatalogo(9, "tubo pvc soldavel 20mm", "8832", "D503", "TIGRE", "UN"),
        ProdutoCatalogo(10, "tubo pvc soldavel 110mm", "8833", "D504", "TIGRE", "UN"),
    ]
    consultas = ["8811", "88", "8 8", "7", "D500", "tubo", "tubo pvc 75", "TUBO PVC",
                 "valvula lavatório", "valvula lavatorio dourada", "torneyra jardin", "zzzz", "",
                 "   ", "a", "registro 25"]
    for consulta in consultas:
        for limite in (6, 2):
            assert regras.buscar_no_catalogo(catalogo, consulta, limite) == \
                app_antigo._buscar_no_catalogo(catalogo, consulta, limite), consulta
    assert regras.buscar_no_catalogo([], "tubo") == []
    assert [p.produto_id for p in regras.buscar_no_catalogo(catalogo, "88")][:3] == [1, 2, 4]
    assert len(regras.buscar_no_catalogo(catalogo, "tubo")) == 6          # limite
    assert [p.produto_id for p in regras.buscar_no_catalogo(catalogo, "tubo pvc 75")] == [4, 1]
