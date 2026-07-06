"""Fase 2 de perf (2026-07): transporte HTTP das consultas de busca.

Os cards da busca são server-rendered — um GET com os cookies da sessão logada
devolve o mesmo conteúdo do navegador (validado em campo: preço HTTP == preço
navegador nos 2 fornecedores). Estes testes travam:
- o parse do HTML cru (atributos data-* + URL do <a> que embrulha o card);
- a guarda anti-deslogado (todos os preços 0 → desliga o HTTP, avisa);
- o fallback para o navegador (rede falhou / sem cookie / flag off);
- o modo http-only (paralelo): erro vira SessaoHTTPExpirou, nunca greenlet;
- o paralelismo por fornecedor no app (fila de progresso + mesclagem).
"""
from __future__ import annotations

import pytest

import pregoes.scraper as scraper
from pregoes import config


def _html_card(codigo, nome, preco, marca, href="https://x/produto-1"):
    return (
        f'<a class="wrap" href="{href}">'
        f'<div class="product-card  "><div class="product-card__info" '
        f'data-id-produto="{codigo}" data-codigo-produto="{codigo}" '
        f'data-nome="{nome}" data-preco="{preco}" data-marca="{marca}" '
        f'data-departamento="X"></div></div></a>'
    )


# --- parse do HTML cru -------------------------------------------------------

def test_cards_do_html_extrai_atributos_e_url():
    html = "<body>" + _html_card("123", "TUBO 50", "8.32", "AMANCO",
                                 "https://site/tubo-50-123") + "</body>"
    cards = scraper._cards_do_html(html, 10)
    assert len(cards) == 1
    c = cards[0]
    assert c["data-codigo-produto"] == "123"
    assert c["data-nome"] == "TUBO 50"
    assert c["data-preco"] == "8.32"
    assert c["data-marca"] == "AMANCO"
    assert c["url"] == "https://site/tubo-50-123"   # p/ "Identificar NCM"


def test_cards_do_html_respeita_max_e_ordem():
    html = "".join(_html_card(str(i), f"P{i}", "1.00", "M") for i in range(5))
    cards = scraper._cards_do_html(html, 3)
    assert [c["data-codigo-produto"] for c in cards] == ["0", "1", "2"]


def test_cards_do_html_sem_cards_devolve_vazio():
    assert scraper._cards_do_html("<body>nenhum resultado</body>", 10) == []


def test_cards_do_html_sem_a_envolvente_url_none():
    html = ('<div class="product-card__info" data-codigo-produto="9" '
            'data-nome="X" data-preco="1.0" data-marca="M"></div>')
    cards = scraper._cards_do_html(html, 10)
    assert cards[0]["url"] is None


# --- _consultar_cards_http: fallback e guarda anti-deslogado ----------------

class _FornHTTP:
    nome = "almeida"

    def __init__(self, cookie="PHPSESSID=a; token=b", http_only=False):
        self._http_cookie = cookie
        self._http_only = http_only


def test_sem_cookie_devolve_none_usa_navegador():
    assert scraper._consultar_cards_http(_FornHTTP(cookie=None), "http://u") is None


def test_flag_desligada_devolve_none(monkeypatch):
    monkeypatch.setattr(config, "SCRAPER_HTTP_QUERIES", False)
    assert scraper._consultar_cards_http(_FornHTTP(), "http://u") is None


def test_rede_falhou_devolve_none(monkeypatch):
    def _boom(url, cookie):
        raise OSError("rede caiu")
    monkeypatch.setattr(scraper, "_http_get", _boom)
    assert scraper._consultar_cards_http(_FornHTTP(), "http://u") is None


def test_html_ok_devolve_cards(monkeypatch):
    monkeypatch.setattr(scraper, "_http_get",
                        lambda u, c: _html_card("7", "LUVA", "5.50", "TIGRE"))
    cards = scraper._consultar_cards_http(_FornHTTP(), "http://u")
    assert cards and cards[0]["data-preco"] == "5.50"


def test_precos_zerados_desliga_http_e_avisa(monkeypatch):
    """Deslogado, o site mostra os cards com data-preco='0.00'. A guarda
    desliga o HTTP da sessão (correção > velocidade) e o navegador assume."""
    monkeypatch.setattr(scraper, "_http_get",
                        lambda u, c: _html_card("7", "LUVA", "0.00", "TIGRE"))
    forn = _FornHTTP()
    assert scraper._consultar_cards_http(forn, "http://u") is None
    assert forn._http_cookie is None, "HTTP deve ser desligado p/ esta sessão"


def test_busca_vazia_legitima_nao_desliga_http(monkeypatch):
    """0 cards ≠ deslogado: busca sem resultado é resposta VÁLIDA (instantânea)."""
    monkeypatch.setattr(scraper, "_http_get", lambda u, c: "<body>nada</body>")
    forn = _FornHTTP()
    assert scraper._consultar_cards_http(forn, "http://u") == []
    assert forn._http_cookie, "cookie deve ser mantido"


def test_http_only_rede_falha_levanta_sessao_expirou(monkeypatch):
    def _boom(url, cookie):
        raise OSError("rede caiu")
    monkeypatch.setattr(scraper, "_http_get", _boom)
    with pytest.raises(scraper.SessaoHTTPExpirou):
        scraper._consultar_cards_http(_FornHTTP(http_only=True), "http://u")


def test_http_only_deslogado_levanta_sessao_expirou(monkeypatch):
    monkeypatch.setattr(scraper, "_http_get",
                        lambda u, c: _html_card("7", "L", "0.00", "M"))
    with pytest.raises(scraper.SessaoHTTPExpirou):
        scraper._consultar_cards_http(_FornHTTP(http_only=True), "http://u")


# --- _buscar_query pelo transporte HTTP (fim a fim, sem navegador) ----------

def _forn_real_sem_navegador(cls):
    """Instância real SEM __enter__ (sem Playwright): só o caminho HTTP."""
    forn = cls.__new__(cls)
    forn._http_cookie = "PHPSESSID=a; token=b"
    forn._http_only = False
    return forn


def test_buscar_query_via_http_produz_resultado_completo(monkeypatch):
    monkeypatch.setattr(
        scraper, "_http_get",
        lambda u, c: _html_card("9185", "LUVA SOLDÁVEL DN50", "8.32", "CORR",
                                "https://site/luva-9185"))
    forn = _forn_real_sem_navegador(scraper.DantasDistribuidora)
    out = forn._buscar_query(None, "luva", "variacao")   # page=None: HTTP puro
    assert len(out) == 1
    r = out[0]
    assert (r.codigo_fornecedor, r.preco, r.marca) == ("9185", 8.32, "CORR")
    assert r.url == "https://site/luva-9185"
    assert r.fornecedor == "dantas"


def test_buscar_query_almeida_via_http(monkeypatch):
    monkeypatch.setattr(
        scraper, "_http_get",
        lambda u, c: _html_card("1271", "TUBO CL15", "66.04", "AMANCO"))
    forn = _forn_real_sem_navegador(scraper.AlmeidaDistribuidor)
    out = forn._buscar_query(None, "tubo", "exato")
    assert out and out[0].codigo_fornecedor == "1271"
    assert out[0].fornecedor == "almeida"


# --- paralelismo por fornecedor (helper do app) ------------------------------

def test_buscar_fornecedores_paralelo_mescla_e_progride(monkeypatch):
    from tests.test_fix_app import app  # app com streamlit dublê já importado

    class _Forn:
        def __init__(self):
            self._http_only = False

    class _Lote:
        def __init__(self, marca):
            self.resultados = {0: [marca]}
            self.erros = []
            self.cache_hits = 1
            self.cache_misses = 0

    def _fake_buscar(itens, forns, progresso_callback=None, **k):
        nome = forns[0]._nome
        if progresso_callback:
            progresso_callback(1, 1, f"item de {nome}")
        if nome == "dantas":
            raise RuntimeError("dantas caiu")
        return _Lote(nome)

    monkeypatch.setattr("pregoes.scraper.buscar_precos_para_itens", _fake_buscar)
    fa, fd = _Forn(), _Forn()
    fa._nome, fd._nome = "almeida", "dantas"
    eventos = []
    saida = app._buscar_fornecedores_paralelo(
        [object()], {"almeida": fa, "dantas": fd},
        lambda nome, a, t, m: eventos.append((nome, a, t)))
    assert not isinstance(saida["almeida"], Exception)
    assert saida["almeida"].resultados[0] == ["almeida"]
    assert isinstance(saida["dantas"], Exception), "falha isolada não derruba o outro"
    assert ("almeida", 1, 1) in eventos, "progresso chegou pela fila"
    assert fa._http_only is False and fd._http_only is False, "flag restaurada"


def test_cards_do_html_layout_logado_anchor_depois_com_id():
    """Na página LOGADA o <a> do produto vem DEPOIS do card, com
    data-id-produto — o mapa por id casa card→href (campo 2026-07-06)."""
    html = (
        '<div class="product-card__info" data-id-produto="1349" '
        'data-codigo-produto="1349" data-nome="TUBO ESGOTO DN50" '
        'data-preco="43.28" data-marca="AMANCO"></div>'
        '<p>bloco no meio</p>'
        '<a href="https://site/tubo-esgoto-1349" data-click="produto" '
        'data-page="1" data-id-produto="1349">ver</a>'
    )
    cards = scraper._cards_do_html(html, 10)
    assert cards[0]["url"] == "https://site/tubo-esgoto-1349"
