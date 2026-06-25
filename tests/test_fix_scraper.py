"""
Testes de regressão do fix #6: fechar Chromium quando o login falha no
`__enter__` do scraper (Almeida e Dantas).

Achado: `__enter__` faz start playwright -> launch browser -> new_context/page
-> `_login`. Se `_login` levanta (cookies de sessão ausentes -> RuntimeError;
ou TimeoutError/OSError do Playwright), o `__exit__` NUNCA roda — o `with` só
registra o contexto se `__enter__` RETORNAR. Resultado: Chromium headless +
driver ficam ÓRFÃOS e se acumulam na máquina Windows a cada login falho (caso
comum: senha trocada no site, site fora do ar).

Antes do fix estes testes FALHARIAM: `browser.close()` e `playwright.stop()`
nunca seriam chamados no caminho de exceção. Depois do fix, o except do
`__enter__` chama a mesma limpeza do `__exit__`.

Sem rede, sem navegador real, sem Streamlit — tudo com dublês/monkeypatch
sobre `playwright.sync_api.sync_playwright`.
"""
from __future__ import annotations

import sys
import types

import pytest

from pregoes.scraper import AlmeidaDistribuidor, DantasDistribuidora


# === Dublês mínimos do Playwright ===
#
# Reproduzem só a superfície que `__enter__` usa:
#   sync_playwright().start() -> objeto com .chromium.launch() e .stop()
#   browser.new_context() -> contexto com .new_page() e .cookies()
# Cada dublê registra se foi fechado/parado, para o teste asseverar a limpeza.

class _FakeLocator:
    """Stub encadeável para `page.locator(...).first.fill(...)` /
    `.press(...)` que o `_login` real usa. Não faz nada — só permite o
    `_login` chegar até a checagem de cookies sem rede."""

    @property
    def first(self):
        return self

    def fill(self, *a, **k):
        pass

    def press(self, *a, **k):
        pass


class _FakePage:
    def __init__(self):
        self.goto_chamado = False

    def goto(self, *a, **k):
        self.goto_chamado = True

    def locator(self, *a, **k):
        return _FakeLocator()

    def wait_for_timeout(self, *a, **k):
        pass

    def set_default_timeout(self, *a, **k):
        pass

    def set_default_navigation_timeout(self, *a, **k):
        pass


class _FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def new_page(self):
        return _FakePage()

    def cookies(self):
        return self._cookies


class _FakeBrowser:
    def __init__(self, context):
        self._context = context
        self.fechado = False

    def new_context(self, *a, **k):
        return self._context

    def close(self):
        self.fechado = True


class _FakeChromium:
    def __init__(self, browser):
        self._browser = browser

    def launch(self, *a, **k):
        return self._browser


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)
        self.parado = False

    def start(self):
        return self

    def stop(self):
        self.parado = True


def _instalar_fake_playwright(monkeypatch, fake_pw):
    """Injeta um módulo `playwright.sync_api` falso cujo `sync_playwright()`
    devolve `fake_pw`. `__enter__` faz `from playwright.sync_api import
    sync_playwright` (import tardio), então basta colocar o módulo no
    sys.modules antes da chamada."""
    mod_sync_api = types.ModuleType("playwright.sync_api")
    mod_sync_api.sync_playwright = lambda: fake_pw
    mod_playwright = types.ModuleType("playwright")
    mod_playwright.sync_api = mod_sync_api
    monkeypatch.setitem(sys.modules, "playwright", mod_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", mod_sync_api)


def _montar_fake(cookies):
    """Cria a cadeia de dublês e devolve (fake_pw, browser) já ligados."""
    context = _FakeContext(cookies)
    browser = _FakeBrowser(context)
    fake_pw = _FakePlaywright(browser)
    return fake_pw, browser


# Cookies que disparam RuntimeError no `_login` real (faltam PHPSESSID/token).
_COOKIES_LOGIN_FALHO: list[dict] = []
# Cookies de sessão válida (login OK).
_COOKIES_LOGIN_OK = [
    {"name": "PHPSESSID", "value": "abc"},
    {"name": "token", "value": "xyz"},
]


# === Almeida ===

def test_almeida_login_falho_fecha_browser_e_para_playwright(monkeypatch):
    """RuntimeError de cookies ausentes no `_login` deve fechar o browser e
    parar o Playwright — não deixar Chromium órfão."""
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_FALHO)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    alm = AlmeidaDistribuidor(email="x@y", senha="senha-errada")
    with pytest.raises(RuntimeError):
        alm.__enter__()

    assert browser.fechado is True, "browser.close() deveria ter sido chamado"
    assert fake_pw.parado is True, "playwright.stop() deveria ter sido chamado"


def test_almeida_excecao_inesperada_no_login_tambem_limpa(monkeypatch):
    """Falhas não-RuntimeError (TimeoutError/OSError do Playwright) também
    devem limpar. Forçamos via monkeypatch de `_login`."""
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_OK)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    def _login_boom(self, page):
        raise TimeoutError("site fora do ar")

    monkeypatch.setattr(AlmeidaDistribuidor, "_login", _login_boom)

    alm = AlmeidaDistribuidor(email="x@y", senha="z")
    with pytest.raises(TimeoutError):
        alm.__enter__()

    assert browser.fechado is True
    assert fake_pw.parado is True


def test_almeida_with_propaga_excecao_e_limpa(monkeypatch):
    """Uso real via `with`: a exceção propaga e a limpeza acontece (mesmo
    sem o `__exit__` rodar, porque `__enter__` não retornou)."""
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_FALHO)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    with pytest.raises(RuntimeError):
        with AlmeidaDistribuidor(email="x@y", senha="z"):
            pytest.fail("não deveria entrar no corpo do with: login falhou")

    assert browser.fechado is True
    assert fake_pw.parado is True


def test_almeida_login_ok_nao_fecha_no_enter(monkeypatch):
    """Sanidade: login bem-sucedido NÃO fecha o browser no `__enter__` —
    a sessão precisa ficar viva para as buscas."""
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_OK)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    alm = AlmeidaDistribuidor(email="x@y", senha="z")
    retorno = alm.__enter__()

    assert retorno is alm
    assert browser.fechado is False
    assert fake_pw.parado is False

    # `__exit__` (caminho de sucesso) ainda fecha tudo.
    alm.__exit__(None, None, None)
    assert browser.fechado is True
    assert fake_pw.parado is True


# === Dantas ===

def test_dantas_login_falho_fecha_browser_e_para_playwright(monkeypatch):
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_FALHO)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    dan = DantasDistribuidora(email="x@y", senha="senha-errada")
    with pytest.raises(RuntimeError):
        dan.__enter__()

    assert browser.fechado is True, "browser.close() deveria ter sido chamado"
    assert fake_pw.parado is True, "playwright.stop() deveria ter sido chamado"


def test_dantas_excecao_inesperada_no_login_tambem_limpa(monkeypatch):
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_OK)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    def _login_boom(self, page):
        raise OSError("conexão recusada")

    monkeypatch.setattr(DantasDistribuidora, "_login", _login_boom)

    dan = DantasDistribuidora(email="x@y", senha="z")
    with pytest.raises(OSError):
        dan.__enter__()

    assert browser.fechado is True
    assert fake_pw.parado is True


def test_dantas_with_propaga_excecao_e_limpa(monkeypatch):
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_FALHO)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    with pytest.raises(RuntimeError):
        with DantasDistribuidora(email="x@y", senha="z"):
            pytest.fail("não deveria entrar no corpo do with: login falhou")

    assert browser.fechado is True
    assert fake_pw.parado is True


def test_dantas_login_ok_nao_fecha_no_enter(monkeypatch):
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_OK)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    dan = DantasDistribuidora(email="x@y", senha="z")
    retorno = dan.__enter__()

    assert retorno is dan
    assert browser.fechado is False
    assert fake_pw.parado is False

    dan.__exit__(None, None, None)
    assert browser.fechado is True
    assert fake_pw.parado is True


# === Idempotência da limpeza ===

def test_fechar_idempotente_apos_falha(monkeypatch):
    """`_fechar` (via except do `__enter__`) e um `__exit__` posterior não
    devem fechar duas vezes nem explodir — referências são zeradas."""
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_FALHO)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    alm = AlmeidaDistribuidor(email="x@y", senha="z")
    with pytest.raises(RuntimeError):
        alm.__enter__()

    # Conta quantas vezes o browser foi fechado: deve ser exatamente 1.
    browser.fechado = False
    alm.__exit__(None, None, None)  # não deve re-fechar (browser já é None)
    assert browser.fechado is False, "limpeza deveria ser idempotente (browser zerado)"


# === _esperar_sessao: espera ATIVA pelos cookies (fix do login lento 2026-06-25) ===

def test_esperar_sessao_true_quando_cookies_aparecem_tarde():
    """Login lento: cookies só aparecem após algumas checagens → True (não falha
    falso como o sleep fixo de 4s fazia)."""
    from pregoes import scraper

    class _Ctx:
        def __init__(self): self.n = 0
        def cookies(self):
            self.n += 1
            return ([{"name": "PHPSESSID"}, {"name": "token"}]
                    if self.n >= 4 else [])

    class _Pg:
        def wait_for_timeout(self, ms): pass

    assert scraper._esperar_sessao(_Ctx(), _Pg(), timeout_s=15) is True


def test_esperar_sessao_false_se_cookies_nunca_vem():
    """Login genuinamente falho: cookies nunca aparecem → False dentro do prazo."""
    from pregoes import scraper

    class _Ctx:
        def cookies(self): return [{"name": "outro"}]

    class _Pg:
        def wait_for_timeout(self, ms): pass

    assert scraper._esperar_sessao(_Ctx(), _Pg(), timeout_s=1) is False
