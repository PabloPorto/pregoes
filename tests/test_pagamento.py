"""Condição de pagamento (Forma + Prazo) — o preço no site varia conforme a
condição, e o scraper agora a aplica na sessão (feedback do cliente 2026-06-24).

Cobre: armazenamento da preferência (credenciais), as opções bundladas (config) e
a aplicação no scraper (modal → seleciona por value → Confirmar), com degradação
graciosa (sem config / erro → segue no default, sem derrubar o login).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pregoes import config, credenciais
import pregoes.scraper as scraper


# --- Armazenamento da preferência (credenciais.json) ------------------------

@pytest.fixture
def cred_tmp(monkeypatch):
    monkeypatch.setattr(credenciais, "CAMINHO", Path(tempfile.mkdtemp()) / "c.json")
    return credenciais.CAMINHO


def test_preferencia_round_trip(cred_tmp):
    credenciais.salvar_preferencia_pagamento(
        "almeida", "PIX2", "PIX", "32", "30/60/90/120 DIAS")
    pref = credenciais.carregar_preferencia_pagamento("almeida")
    assert pref == {"forma_value": "PIX2", "forma_texto": "PIX",
                    "prazo_value": "32", "prazo_texto": "30/60/90/120 DIAS"}
    # fornecedor não configurado → None
    assert credenciais.carregar_preferencia_pagamento("dantas") is None


def test_preferencia_preserva_credenciais(cred_tmp):
    credenciais.salvar("almeida", "a@x.com", "senha123")
    credenciais.salvar_preferencia_pagamento("almeida", "001", "BB", "1", "A VISTA")
    # a credencial não é destruída ao salvar a preferência
    assert credenciais.carregar("almeida") == {"email": "a@x.com", "senha": "senha123"}
    assert credenciais.carregar_preferencia_pagamento("almeida")["forma_value"] == "001"


def test_preferencia_um_fornecedor_nao_afeta_o_outro(cred_tmp):
    credenciais.salvar_preferencia_pagamento("almeida", "PIX2", "PIX", "32", "X")
    credenciais.salvar_preferencia_pagamento("dantas", "001", "BB", "16", "30/60")
    assert credenciais.carregar_preferencia_pagamento("almeida")["forma_value"] == "PIX2"
    assert credenciais.carregar_preferencia_pagamento("dantas")["forma_value"] == "001"


# --- Opções bundladas (config) ---------------------------------------------

def test_opcoes_pagamento_bundladas():
    ops = config.carregar_opcoes_pagamento()
    assert "almeida" in ops and "dantas" in ops
    # cada fornecedor tem forma e prazo, com value+texto
    for forn in ("almeida", "dantas"):
        assert ops[forn]["forma"] and ops[forn]["prazo"]
        o = ops[forn]["forma"][0]
        assert "value" in o and "texto" in o and o["value"]


# --- Aplicação no scraper (modal → seleciona → Confirmar) -------------------

class _FakeLoc:
    def __init__(self, page, sel):
        self.page = page
        self.sel = sel

    def count(self):
        return 1   # selects/botões "existem e estão visíveis" no fake

    @property
    def first(self):
        return self

    def nth(self, i):
        return self

    def select_option(self, value=None, label=None):
        self.page.selects.append((self.sel, value, label))

    def click(self, **kw):
        self.page.clicks.append(self.sel)

    def get_by_text(self, *a, **k):
        return _FakeLoc(self.page, "by_text")


class _FakePage:
    def __init__(self):
        self.selects: list = []
        self.clicks: list = []
        self.gotos: list = []

    def goto(self, url, **kw):
        self.gotos.append(url)

    def locator(self, sel):
        return _FakeLoc(self, sel)

    def get_by_text(self, *a, **k):
        return _FakeLoc(self, "by_text")

    def get_by_role(self, role, name=None):
        return _FakeLoc(self, f"role:{name}")

    def wait_for_timeout(self, ms):
        pass


def test_aplica_preferencia_seleciona_por_value_e_confirma(monkeypatch):
    monkeypatch.setattr(scraper.credenciais, "carregar_preferencia_pagamento",
                        lambda f: {"forma_value": "PIX2", "forma_texto": "PIX",
                                   "prazo_value": "32", "prazo_texto": "30/60/90/120"})
    page = _FakePage()
    scraper._aplicar_preferencia_pagamento(page, "almeida")
    valores = [v for (_sel, v, _lab) in page.selects]
    assert "PIX2" in valores and "32" in valores, "selecionou Forma e Prazo pelo value"
    assert page.clicks, "clicou em Confirmar"
    assert page.gotos, "navegou para a home do fornecedor"


def test_aplica_preferencia_sem_config_nao_toca_no_site(monkeypatch):
    monkeypatch.setattr(scraper.credenciais, "carregar_preferencia_pagamento",
                        lambda f: None)
    page = _FakePage()
    scraper._aplicar_preferencia_pagamento(page, "almeida")
    # sem condição configurada → segue no default, sem navegar nem selecionar nada
    assert page.gotos == [] and page.selects == [] and page.clicks == []


def test_aplica_preferencia_e_graciosa_em_erro(monkeypatch):
    monkeypatch.setattr(scraper.credenciais, "carregar_preferencia_pagamento",
                        lambda f: {"forma_value": "X", "forma_texto": "X",
                                   "prazo_value": "Y", "prazo_texto": "Y"})

    class _BoomPage:
        def goto(self, *a, **k):
            raise RuntimeError("rede caiu no meio")

    # NÃO pode levantar — derrubaria o login; degrada para o default.
    scraper._aplicar_preferencia_pagamento(_BoomPage(), "almeida")


def test_fornecedor_desconhecido_nao_quebra(monkeypatch):
    monkeypatch.setattr(scraper.credenciais, "carregar_preferencia_pagamento",
                        lambda f: {"forma_value": "X", "forma_texto": "X",
                                   "prazo_value": "Y", "prazo_texto": "Y"})
    page = _FakePage()
    scraper._aplicar_preferencia_pagamento(page, "inexistente")
    assert page.gotos == []   # sem base_url → não faz nada
