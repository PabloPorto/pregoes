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
    def __init__(self, page, sel, count=1):
        self.page = page
        self.sel = sel
        self._count = count

    def count(self):
        return self._count   # selects/botões "existem e estão visíveis" no fake

    @property
    def first(self):
        return self

    def nth(self, i):
        return self

    def select_option(self, value=None, label=None, **kw):
        self.page.selects.append((self.sel, value, label))

    def click(self, **kw):
        self.page.clicks.append(self.sel)

    def get_by_text(self, *a, **k):
        return _FakeLoc(self.page, "by_text")


class _FakePage:
    def __init__(self, prazo_visivel=True):
        self.selects: list = []
        self.clicks: list = []
        self.gotos: list = []
        self.prazo_visivel = prazo_visivel

    def goto(self, url, **kw):
        self.gotos.append(url)

    def locator(self, sel):
        # O <select> de Condição/Prazo fica OCULTO para formas sem prazo (PIX,
        # antecipado). O teste simula isso com prazo_visivel=False → count 0.
        if "id_condicao_pagamento" in sel and ":visible" in sel and not self.prazo_visivel:
            return _FakeLoc(self, sel, count=0)
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


def test_aplica_preferencia_forma_sem_prazo_pula_prazo_e_confirma(monkeypatch):
    """Regressão de campo (2026-06): a forma PIX NÃO tem <select> de Condição/
    Prazo (fica oculto após escolher PIX). O código deve PULAR o prazo — não
    travar tentando aplicá-lo num select oculto — e ainda assim aplicar a forma
    e clicar em Confirmar. Antes, travava ~60s por value+texto e caía no default
    do site (voltando preço na condição errada)."""
    monkeypatch.setattr(scraper.credenciais, "carregar_preferencia_pagamento",
                        lambda f: {"forma_value": "PIX2", "forma_texto": "PIX",
                                   "prazo_value": "1", "prazo_texto": "A VISTA"})
    page = _FakePage(prazo_visivel=False)        # PIX → prazo oculto
    scraper._aplicar_preferencia_pagamento(page, "almeida")
    valores = [v for (_sel, v, _lab) in page.selects]
    assert "PIX2" in valores, "aplicou a forma (PIX)"
    assert "1" not in valores, "NÃO tentou aplicar o prazo oculto"
    assert page.clicks, "clicou em Confirmar mesmo sem prazo"


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


# --- VERIFICAÇÃO pós-aplicação (campo 2026-06-27): o código antigo logava
# 'aplicada' SEM conferir, e o preço saía na condição errada em silêncio (ex.:
# Dantas configurada em banco, preço vinha PIX). Agora relê a forma salva e é
# HONESTO: confirma / avisa que não pegou / avisa que não deu pra conferir.

_PREF = {"forma_value": "001", "forma_texto": "BANCO DO BRASIL",
         "prazo_value": "1", "prazo_texto": "A VISTA"}


def _capturar_logs(monkeypatch):
    msgs: list = []

    def _wrap(orig):
        def w(m, *a, **k):
            msgs.append((m % a) if a else m)
            return orig(m, *a, **k)
        return w

    monkeypatch.setattr(scraper._logger, "info", _wrap(scraper._logger.info))
    monkeypatch.setattr(scraper._logger, "warning", _wrap(scraper._logger.warning))
    return msgs


def test_verifica_confirma_quando_forma_salva_bate(monkeypatch):
    monkeypatch.setattr(scraper.credenciais, "carregar_preferencia_pagamento",
                        lambda f: _PREF)
    monkeypatch.setattr(scraper, "_forma_salva", lambda page, base, **k: "001")  # pegou
    msgs = _capturar_logs(monkeypatch)
    scraper._aplicar_preferencia_pagamento(_FakePage(), "dantas")
    assert any("CONFIRMADA" in m for m in msgs)


def test_verifica_avisa_quando_forma_salva_diverge(monkeypatch):
    monkeypatch.setattr(scraper.credenciais, "carregar_preferencia_pagamento",
                        lambda f: _PREF)
    # a conta ficou em PIX2 (não no banco configurado) → preço sairia errado
    monkeypatch.setattr(scraper, "_forma_salva", lambda page, base, **k: "PIX2")
    msgs = _capturar_logs(monkeypatch)
    scraper._aplicar_preferencia_pagamento(_FakePage(), "dantas")
    assert any("NÃO pegou" in m for m in msgs)
    assert not any("CONFIRMADA" in m for m in msgs)


def test_verifica_avisa_quando_nao_consegue_ler(monkeypatch):
    monkeypatch.setattr(scraper.credenciais, "carregar_preferencia_pagamento",
                        lambda f: _PREF)
    monkeypatch.setattr(scraper, "_forma_salva", lambda page, base, **k: None)  # leitura falhou
    msgs = _capturar_logs(monkeypatch)
    scraper._aplicar_preferencia_pagamento(_FakePage(), "dantas")
    assert any("CONFERIR" in m for m in msgs)
    assert not any("CONFIRMADA" in m for m in msgs)


# --- VERIFY-FIRST (perf, campo 2026-07): a condição é persistente na conta —
# se a forma salva JÁ é a configurada, pula a aplicação inteira (modal + esperas
# fixas) e loga CONFIRMADA direto. Pré-check falhando não impede a aplicação.

def test_verify_first_pula_aplicacao_quando_ja_configurada(monkeypatch):
    monkeypatch.setattr(scraper.credenciais, "carregar_preferencia_pagamento",
                        lambda f: _PREF)
    monkeypatch.setattr(scraper, "_forma_salva", lambda page, base, **k: "001")
    msgs = _capturar_logs(monkeypatch)
    page = _FakePage()
    scraper._aplicar_preferencia_pagamento(page, "dantas")
    assert page.selects == [] and page.clicks == [], "NÃO deve reaplicar"
    assert any("já estava configurada" in m for m in msgs)
    assert any("CONFIRMADA" in m for m in msgs)   # contrato de confirmação


def test_verify_first_precheck_falho_nao_impede_aplicacao(monkeypatch):
    monkeypatch.setattr(scraper.credenciais, "carregar_preferencia_pagamento",
                        lambda f: _PREF)
    chamadas = {"n": 0}

    def _fs(page, base, **k):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise RuntimeError("pré-check caiu")   # 1ª leitura (verify-first)
        return "001"                                # verificação pós-aplicação

    monkeypatch.setattr(scraper, "_forma_salva", _fs)
    msgs = _capturar_logs(monkeypatch)
    page = _FakePage()
    scraper._aplicar_preferencia_pagamento(page, "dantas")   # não levanta
    assert page.selects and page.clicks, "aplicou normalmente"
    assert any("CONFIRMADA" in m for m in msgs)
