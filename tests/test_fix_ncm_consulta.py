"""Regressão (auditoria 2026-06): consultar_ncms separa resultado DEFINITIVO
de falha TRANSITÓRIA, para o NCM não cair no mesmo cache-negativo da busca.

Antes, falha de login/navegador virava ``None`` indistinguível de "produto
sem NCM" — o operador não sabia o porquê e o re-clique não re-tentava (o
chamador cacheava o None). Agora a falha vai para ``falhas`` (não cacheável)
e só o que foi visitado entra em ``ncms``.

Hermético: mocka a classe de fornecedor (sem rede/navegador) e as credenciais.
"""
from __future__ import annotations

import pytest

from pregoes import scraper


class _FakePage:
    def __init__(self, txt):
        self._txt = txt

    def goto(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def evaluate(self, *a, **k):
        return self._txt


def _scraper_ok(txt):
    class _S:
        def __init__(self, email, senha):
            pass

        def __enter__(self):
            self._page = _FakePage(txt)
            return self

        def __exit__(self, *a):
            return False
    return _S


class _ScraperLoginFalha:
    def __init__(self, email, senha):
        pass

    def __enter__(self):
        raise RuntimeError("cookies de sessão não estabelecidos")

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _cred_ok(monkeypatch):
    monkeypatch.setattr(scraper.credenciais, "carregar",
                        lambda forn: {"email": "x", "senha": "y"})


@pytest.fixture(autouse=True)
def _forca_caminho_navegador(monkeypatch):
    """Estes testes cobrem o CONTRATO do fallback de NAVEGADOR (definitivo vs
    transitório). Força esse caminho: o HTTP "falha" (cai pro navegador) e o
    cache durável fica fora (sem banco) — hermético. O caminho HTTP/cache tem
    testes próprios abaixo, que re-monkeypatcham por cima."""
    def _boom(*a, **k):
        raise OSError("sem rede no teste")
    monkeypatch.setattr(scraper, "_http_get", _boom)

    def _sem_banco(*a, **k):
        raise RuntimeError("sem banco no teste")
    monkeypatch.setattr(scraper.db, "conectar", _sem_banco)
    scraper._sessoes_vivas.clear()
    yield
    scraper._sessoes_vivas.clear()


def test_sucesso_retorna_ncm_sem_falhas(monkeypatch):
    monkeypatch.setattr(scraper, "AlmeidaDistribuidor",
                        _scraper_ok("Código NCM: 3917.23.00"))
    ncms, falhas = scraper.consultar_ncms(
        [(0, "almeida", "https://almeidadistribuidor.com.br/p/1")])
    assert ncms == {0: "39172300"}
    assert falhas == set()


def test_pagina_sem_ncm_e_definitivo(monkeypatch):
    """Visitou e a página não traz NCM → None DEFINITIVO (cacheável), não falha."""
    monkeypatch.setattr(scraper, "AlmeidaDistribuidor",
                        _scraper_ok("Produto sem essa informação"))
    ncms, falhas = scraper.consultar_ncms(
        [(0, "almeida", "https://almeidadistribuidor.com.br/p/1")])
    assert ncms == {0: None}
    assert falhas == set()


def test_login_falha_vai_para_falhas_nao_para_ncms(monkeypatch):
    """Login/navegador falhou → chave em `falhas` (transitório), fora de `ncms`
    — o chamador não cacheia e permite re-tentar."""
    monkeypatch.setattr(scraper, "AlmeidaDistribuidor", _ScraperLoginFalha)
    ncms, falhas = scraper.consultar_ncms(
        [(0, "almeida", "https://almeidadistribuidor.com.br/p/1"),
         (1, "almeida", "https://almeidadistribuidor.com.br/p/2")])
    assert ncms == {}
    assert falhas == {0, 1}


def test_sem_credencial_e_falha_nao_definitivo(monkeypatch):
    monkeypatch.setattr(scraper.credenciais, "carregar", lambda forn: None)
    monkeypatch.setattr(scraper, "AlmeidaDistribuidor",
                        _scraper_ok("Código NCM: 3917.23.00"))
    ncms, falhas = scraper.consultar_ncms(
        [(0, "almeida", "https://almeidadistribuidor.com.br/p/1")])
    assert ncms == {} and falhas == {0}


def test_progresso_completa_mesmo_com_falha(monkeypatch):
    """A barra chega ao total mesmo quando o fornecedor falha (não trava em X/Y)."""
    monkeypatch.setattr(scraper, "AlmeidaDistribuidor", _ScraperLoginFalha)
    vistos = []
    scraper.consultar_ncms(
        [(0, "almeida", "u1"), (1, "almeida", "u2")],
        progresso=lambda f, t: vistos.append((f, t)))
    assert vistos[-1] == (2, 2)


# === Caminho NOVO (2026-07): HTTP sem login + cache durável por produto =====

def _html_produto(codigo, ncm=None):
    base = f"<html><body>Código: {codigo}<br>Embalagem: LATA"
    if ncm:
        base += f"<br>Código NCM: {ncm}"
    return base + "</body></html>"


def test_extrair_ncm_html_tres_casos():
    assert scraper._extrair_ncm_html(_html_produto("51966", "3209.10.10"),
                                     "51966") == ("ok", "32091010")
    assert scraper._extrair_ncm_html(_html_produto("51966"), "51966") == \
        ("ok", None)                                   # página do produto SEM NCM
    assert scraper._extrair_ncm_html("<html>erro 500</html>", "51966")[0] == \
        "erro"                                         # ambíguo → navegador


_CONECTAR_ORIG = scraper.db.conectar   # capturado ANTES de qualquer patch


def _banco_tmp(monkeypatch, tmp_path):
    """Cache durável REAL num banco temporário. Re-patcha POR CIMA do autouse
    _forca_caminho_navegador usando o conectar ORIGINAL (o autouse já sabotou o
    atributo do módulo — daí capturá-lo no import)."""
    dbp = tmp_path / "t.db"
    monkeypatch.setattr(scraper.db, "conectar",
                        lambda *a, **k: _CONECTAR_ORIG(dbp))
    scraper.db.init_db(dbp)
    return dbp


def test_http_acha_ncm_e_grava_cache_duravel(monkeypatch, tmp_path):
    dbp = _banco_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(scraper, "_http_get",
                        lambda url, cookie: _html_produto("51966", "3209.10.10"))
    ncms, falhas = scraper.consultar_ncms(
        [(0, "almeida", "https://x/p/51966", "51966")])
    assert ncms == {0: "32091010"} and falhas == set()
    from pregoes import db as db_real
    with db_real.conectar(dbp) as c:
        assert db_real.ncm_cache_buscar(c, "almeida", "51966") == (True, "32091010")


def test_cache_duravel_responde_sem_tocar_na_rede(monkeypatch, tmp_path):
    dbp = _banco_tmp(monkeypatch, tmp_path)
    from pregoes import db as db_real
    with db_real.get_conn(dbp) as c:
        db_real.ncm_cache_gravar(c, "almeida", "51966", "32091010")

    def _rede_proibida(*a, **k):
        raise AssertionError("não deveria tocar na rede: cache durável cobre")
    monkeypatch.setattr(scraper, "_http_get", _rede_proibida)
    vistos = []
    ncms, falhas = scraper.consultar_ncms(
        [(0, "almeida", "https://x/p/51966", "51966")],
        progresso=lambda f, t: vistos.append((f, t)))
    assert ncms == {0: "32091010"} and falhas == set()
    assert vistos[-1] == (1, 1)


def test_http_sem_ncm_na_pagina_e_definitivo_e_cacheado(monkeypatch, tmp_path):
    dbp = _banco_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(scraper, "_http_get",
                        lambda url, cookie: _html_produto("777"))
    ncms, _ = scraper.consultar_ncms([(0, "almeida", "https://x/p/777", "777")])
    assert ncms == {0: None}
    from pregoes import db as db_real
    with db_real.conectar(dbp) as c:
        assert db_real.ncm_cache_buscar(c, "almeida", "777") == (True, None)


def test_html_ambiguo_cai_pro_navegador_e_cacheia(monkeypatch, tmp_path):
    dbp = _banco_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(scraper, "_http_get",
                        lambda url, cookie: "<html>pagina estranha</html>")
    monkeypatch.setattr(scraper, "AlmeidaDistribuidor",
                        _scraper_ok("Código NCM: 3917.23.00"))
    ncms, falhas = scraper.consultar_ncms(
        [(0, "almeida", "https://x/p/9185", "9185")])
    assert ncms == {0: "39172300"} and falhas == set()
    from pregoes import db as db_real
    with db_real.conectar(dbp) as c:
        assert db_real.ncm_cache_buscar(c, "almeida", "9185") == (True, "39172300")


def test_http_paralelo_progresso_na_thread_principal(monkeypatch, tmp_path):
    import threading
    _banco_tmp(monkeypatch, tmp_path)
    monkeypatch.setattr(scraper, "_http_get",
                        lambda url, cookie: _html_produto(url.rsplit("/", 1)[-1],
                                                          "3209.10.10"))
    principal = threading.get_ident()
    eventos = []
    pedidos = [(i, "almeida", f"https://x/p/{100+i}", str(100 + i))
               for i in range(6)]
    ncms, falhas = scraper.consultar_ncms(
        pedidos, progresso=lambda f, t: eventos.append((threading.get_ident(), f, t)))
    assert len(ncms) == 6 and falhas == set()
    assert all(t == principal for t, _, _ in eventos), "progresso fora da thread da UI"
    assert [f for _, f, _ in eventos] == list(range(1, 7))
