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
