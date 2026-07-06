"""Regressão do parse contra HTML REAL dos sites (fixtures congeladas).

As fixtures em tests/fixtures/*.html são fatias ANONIMIZADAS de páginas de
busca LOGADAS, capturadas por scripts/capturar_fixtures_html.py. Elas congelam
o contrato atual (atributos data-* dos cards + <a> do produto): se o parser
regredir, estes testes quebram SEM precisar de rede/login no pytest.

Quando o SITE mudar de layout (parse quebrando em produção), recapture com o
script e ajuste o parser até estes testes voltarem a passar.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import pregoes.scraper as scraper

FIXTURES = Path(__file__).parent / "fixtures"


def _html(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8")


@pytest.mark.parametrize("forn", ["almeida", "dantas"])
def test_fragmento_logado_extrai_cards_completos(forn):
    brutos = scraper._cards_do_html(_html(f"{forn}_busca_logada.html"), 30)
    # Os <a> do produto TAMBÉM carregam data-codigo-produto (achado da fixture):
    # entram crus aqui e são descartados depois por não terem nome/preço. O
    # contrato dos CARDS de verdade é ter data-nome.
    cards = [c for c in brutos if (c.get("data-nome") or "").strip()]
    assert len(cards) >= 3, "o fragmento congelado tem >= 3 cards de produto"
    for c in cards:
        assert c["data-codigo-produto"].strip().isdigit()
    # Preço LOGADO presente e > 0 (é o que diferencia sessão logada).
    com_preco = [c for c in cards
                 if float((c.get("data-preco") or "0").replace(",", ".")) > 0]
    assert com_preco, "nenhum card com preço logado — parse do preço regrediu?"
    # URL do produto recuperada via <a data-id-produto> (layout logado).
    com_url = [c for c in brutos if c.get("url")]
    assert com_url, "nenhuma URL recuperada — o mapa por data-id-produto regrediu?"
    assert all(u["url"].startswith("https://") for u in com_url)


@pytest.mark.parametrize("forn", ["almeida", "dantas"])
def test_fragmento_logado_vira_resultado_busca(forn):
    """Fim a fim no parser da classe: HTML real → ResultadoBusca com código,
    preço logado e fornecedor certos (o que o motor consome de verdade)."""
    cls = {"almeida": scraper.AlmeidaDistribuidor,
           "dantas": scraper.DantasDistribuidora}[forn]
    inst = cls.__new__(cls)
    inst._http_cookie = "PHPSESSID=x; token=y"
    inst._http_only = False
    html = _html(f"{forn}_busca_logada.html")
    import pregoes.scraper as s
    orig = s._http_get
    s._http_get = lambda u, c: html
    try:
        out = inst._buscar_query(None, "tubo", "variacao")
    finally:
        s._http_get = orig
    assert out, "nenhum ResultadoBusca do HTML real"
    assert all(r.preco > 0 for r in out), "preço deslogado/zero passou pelo parser"
    assert all(r.fornecedor == forn for r in out)
    assert any(r.url for r in out), "URL (p/ Identificar NCM) se perdeu"


@pytest.mark.parametrize("forn", ["almeida", "dantas"])
def test_pagina_vazia_nao_tem_cards(forn):
    """Busca sem resultado: página real NÃO contém cards — vazio é resposta
    válida e instantânea (não dispara a guarda de deslogado)."""
    cards = scraper._cards_do_html(_html(f"{forn}_busca_vazia.html"), 30)
    assert cards == []


@pytest.mark.parametrize("forn", ["almeida", "dantas"])
def test_fixtures_sem_dados_pessoais(forn):
    """As fixtures vão para o git — nunca podem carregar dado do cliente.
    A regra de '@' é por E-MAIL real ('@type'/'@context' do JSON-LD das
    páginas de detalhe são marcadores schema.org, benignos)."""
    import re
    for sufixo in ("busca_logada", "busca_vazia", "detalhe"):
        html = _html(f"{forn}_{sufixo}.html").lower()
        emails = [m for m in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", html)
                  if "example.com" not in m]
        assert not emails, f"e-mail real vazou: {emails[:2]}"
        assert "anselmo" not in html and "centraldaconstrucao" not in html


@pytest.mark.parametrize("forn,ncm_esperado", [("almeida", "32091010"),
                                               ("dantas", "39173900")])
def test_detalhe_extrai_ncm(forn, ncm_esperado):
    """Contrato do consultar_ncms por HTTP: o 'Código NCM' da página de
    DETALHE real (fixture congelada) é extraído certo, sem rede."""
    html = _html(f"{forn}_detalhe.html")
    status, ncm = scraper._extrair_ncm_html(html, None)
    assert (status, ncm) == ("ok", ncm_esperado)
