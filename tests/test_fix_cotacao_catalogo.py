"""Requisito do cliente (2026-06): a "busca no meu catálogo" NÃO pode usar o
preço guardado — preço muda todo dia. O catálogo dá o CÓDIGO; o preço tem que
vir AO VIVO do site (`cotar_por_codigo`), e uma falha de cotação não pode cair
no preço histórico.

Hermético: mocka a classe de fornecedor (sem rede/navegador) e credenciais.
"""
from __future__ import annotations

import pytest

from pregoes import scraper
from pregoes.scraper import ResultadoBusca


def _scraper_com(resultados):
    class _S:
        def __init__(self, email, senha):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def buscar_por_codigo(self, codigo):
            return list(resultados)
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


def _card(preco):
    return ResultadoBusca("TUBO PVC 75MM", "TIGRE", preco, "8811",
                          "exato", "almeida", url="http://x/p/8811")


def test_retorna_preco_ao_vivo_do_site(monkeypatch):
    monkeypatch.setattr(scraper, "AlmeidaDistribuidor", _scraper_com([_card(92.50)]))
    r = scraper.cotar_por_codigo("almeida", "8811")
    assert r is not None and r.preco == 92.50 and r.codigo_fornecedor == "8811"


def test_codigo_fora_de_linha_retorna_none(monkeypatch):
    """Site não devolve nada para o código → None (definitivo), nunca preço velho."""
    monkeypatch.setattr(scraper, "AlmeidaDistribuidor", _scraper_com([]))
    assert scraper.cotar_por_codigo("almeida", "0000") is None


def test_login_falha_levanta_em_vez_de_cair_em_preco_velho(monkeypatch):
    """Falha transitória PROPAGA — o chamador avisa e não usa o catálogo."""
    monkeypatch.setattr(scraper, "AlmeidaDistribuidor", _ScraperLoginFalha)
    with pytest.raises(Exception):
        scraper.cotar_por_codigo("almeida", "8811")


def test_sem_credencial_levanta(monkeypatch):
    monkeypatch.setattr(scraper.credenciais, "carregar", lambda forn: None)
    with pytest.raises(RuntimeError):
        scraper.cotar_por_codigo("almeida", "8811")


def test_fornecedor_desconhecido_levanta():
    with pytest.raises(ValueError):
        scraper.cotar_por_codigo("fornecedor_x", "8811")
