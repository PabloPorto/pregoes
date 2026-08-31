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
    # `version` existe porque `__enter__` monta o User-Agent a partir dela
    # (`_user_agent_real`) — sem o atributo, o dublê exercitaria só o fallback.
    version = "131.0.6778.86"

    def __init__(self, context):
        self._context = context
        self.fechado = False

    def new_context(self, *a, **k):
        self.kwargs_context = k   # o teste do User-Agent inspeciona daqui
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


# CONTRATO NOVO (2026-07-06): o driver Playwright é COMPARTILHADO do processo
# (2º sync_playwright().start() na mesma thread quebra — achado de campo, com
# sessões Almeida + Dantas vivas juntas no cache). Fechar uma sessão fecha SÓ o
# navegador dela; o driver FICA para as demais. Por isso `parado is False`.

@pytest.fixture(autouse=True)
def _reseta_driver_compartilhado():
    """O singleton _pw_driver cachearia o fake de um teste para o seguinte."""
    import pregoes.scraper as _s
    _s._pw_driver = None
    yield
    _s._pw_driver = None


@pytest.fixture(autouse=True)
def _pagamento_sem_config(monkeypatch):
    """Estes testes cobrem a LIMPEZA do login (o browser fecha quando o login
    falha), NÃO a condição de pagamento. Sem isto, o `__enter__` REAL chamava
    `_aplicar_preferencia_pagamento`, que — com a página falsa mínima (sem
    `.count()`) — levantava AttributeError irrelevante ao objetivo do teste
    (esse ruído chegou a parecer bug de campo; achado 2026-07-06). Devolvendo
    'sem preferência configurada', o pagamento é pulado."""
    from pregoes import credenciais
    monkeypatch.setattr(credenciais, "carregar_preferencia_pagamento",
                        lambda f: None)


# === Almeida ===

def test_almeida_login_falho_fecha_browser_sem_derrubar_driver(monkeypatch):
    """RuntimeError de cookies ausentes no `_login` deve fechar o browser (sem
    Chromium órfão); o DRIVER compartilhado fica (outra sessão pode usá-lo)."""
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_FALHO)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    alm = AlmeidaDistribuidor(email="x@y", senha="senha-errada")
    with pytest.raises(RuntimeError):
        alm.__enter__()

    assert browser.fechado is True, "browser.close() deveria ter sido chamado"
    assert fake_pw.parado is False, "driver compartilhado NÃO deve parar"


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
    assert fake_pw.parado is False   # driver compartilhado fica


def test_almeida_with_propaga_excecao_e_limpa(monkeypatch):
    """Uso real via `with`: a exceção propaga e a limpeza acontece (mesmo
    sem o `__exit__` rodar, porque `__enter__` não retornou)."""
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_FALHO)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    with pytest.raises(RuntimeError):
        with AlmeidaDistribuidor(email="x@y", senha="z"):
            pytest.fail("não deveria entrar no corpo do with: login falhou")

    assert browser.fechado is True
    assert fake_pw.parado is False   # driver compartilhado fica


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
    assert fake_pw.parado is False   # driver compartilhado fica


# === Dantas ===

def test_dantas_login_falho_fecha_browser_sem_derrubar_driver(monkeypatch):
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_FALHO)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    dan = DantasDistribuidora(email="x@y", senha="senha-errada")
    with pytest.raises(RuntimeError):
        dan.__enter__()

    assert browser.fechado is True, "browser.close() deveria ter sido chamado"
    assert fake_pw.parado is False, "driver compartilhado NÃO deve parar"


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
    assert fake_pw.parado is False   # driver compartilhado fica


def test_dantas_with_propaga_excecao_e_limpa(monkeypatch):
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_FALHO)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    with pytest.raises(RuntimeError):
        with DantasDistribuidora(email="x@y", senha="z"):
            pytest.fail("não deveria entrar no corpo do with: login falhou")

    assert browser.fechado is True
    assert fake_pw.parado is False   # driver compartilhado fica


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
    assert fake_pw.parado is False   # driver compartilhado fica


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


# === _carregar_busca: espera ATIVA pelos cards (otimização de perf 2026-06-25) ===

def test_carregar_busca_usa_domcontentloaded_e_espera_os_cards():
    """Em vez de networkidle + sleep fixo de 1,5s: domcontentloaded + espera o
    seletor dos cards (server-rendered → hit instantâneo)."""
    from pregoes import scraper
    ch = {}

    class _Pg:
        def goto(self, url, **kw):
            ch["goto"] = (url, kw.get("wait_until"))

        def wait_for_selector(self, sel, **kw):
            ch["wait"] = (sel, kw.get("state"), kw.get("timeout"))

    scraper._carregar_busca(_Pg(), "https://x/busca?s=cimento")
    assert ch["goto"][1] == "domcontentloaded"       # NÃO networkidle
    assert ch["wait"][0] == scraper._SEL_CARD         # espera o card
    assert ch["wait"][1] == "attached"


def test_carregar_busca_miss_nao_levanta():
    """MISS (nenhum card): wait_for_selector estoura timeout → _carregar_busca
    engole e segue (o evaluate do chamador devolve [], sem derrubar a busca)."""
    from pregoes import scraper

    class _Pg:
        def goto(self, url, **kw):
            pass

        def wait_for_selector(self, sel, **kw):
            raise TimeoutError("nenhum card no prazo")

    scraper._carregar_busca(_Pg(), "https://x/busca?s=zzz")   # não pode levantar


# === _sem_duplicatas: site às vezes devolve o mesmo card 2x (campo 2026-06-27) ===

def test_sem_duplicatas_remove_card_repetido_por_codigo():
    from pregoes.scraper import _sem_duplicatas, ResultadoBusca
    def r(cod, p):
        return ResultadoBusca("PLUZIE INTERRUPTOR 10A", "PLUZIE", p, cod,
                              "variacao", "almeida")
    out = _sem_duplicatas([r("38808", 3.40), r("38808", 3.40), r("99999", 5.0)])
    assert [x.codigo_fornecedor for x in out] == ["38808", "99999"]  # dedup + ordem


def test_sem_duplicatas_sem_codigo_cai_para_desc_marca_preco():
    from pregoes.scraper import _sem_duplicatas, ResultadoBusca
    def r(d, p):
        return ResultadoBusca(d, "X", p, "", "variacao", "almeida")
    out = _sem_duplicatas([r("CANO PVC", 2.0), r("CANO PVC", 2.0), r("CANO PVC", 9.0)])
    assert len(out) == 2   # os dois idênticos colapsam; o de preço diferente fica


# === User-Agent: 403 dos fornecedores contra `HeadlessChrome` (campo 2026-08-31) ===
#
# Os dois sites passaram a devolver 403 (117 bytes, sem formulário) para
# requisição cujo User-Agent contém `HeadlessChrome` — assinatura padrão do
# Chromium headless. Como `page.goto()` NÃO levanta em 403, a falha só aparecia
# 30s depois como timeout do `input[name='email']`, culpando o seletor errado.
# Estes testes falhariam antes do fix.

def test_user_agent_nao_anuncia_headless():
    """A regressão que quebrou o cliente: UA com `HeadlessChrome` → 403."""
    from pregoes.scraper import _user_agent_real

    class _B:
        version = "147.0.7727.15"

    ua = _user_agent_real(_B())
    assert "HeadlessChrome" not in ua, "UA headless é bloqueado com 403 pelos sites"
    assert "Chrome/147.0.7727.15" in ua, "deve derivar da versão real do Chromium"


def test_user_agent_cai_no_fallback_sem_versao():
    """Sem `version` acessível, um UA levemente velho ainda passa — estourar
    aqui derrubaria o login inteiro por causa de uma string."""
    from pregoes.scraper import _user_agent_real, _CHROME_VERSAO_FALLBACK

    class _BSemVersao:
        @property
        def version(self):
            raise AttributeError("API mudou")

    ua = _user_agent_real(_BSemVersao())
    assert _CHROME_VERSAO_FALLBACK in ua
    assert "HeadlessChrome" not in ua


@pytest.mark.parametrize("classe", [AlmeidaDistribuidor, DantasDistribuidora])
def test_enter_passa_user_agent_ao_criar_contexto(monkeypatch, classe):
    """Não basta a função existir: o `__enter__` precisa de fato entregar o
    `user_agent` ao `new_context` — nos DOIS fornecedores."""
    fake_pw, browser = _montar_fake(_COOKIES_LOGIN_OK)
    _instalar_fake_playwright(monkeypatch, fake_pw)

    forn = classe(email="x@y", senha="z")
    forn.__enter__()

    ua = browser.kwargs_context.get("user_agent")
    assert ua, "sem user_agent explícito o Chromium se anuncia como HeadlessChrome"
    assert "HeadlessChrome" not in ua
    assert browser.kwargs_context.get("viewport") == {"width": 1280, "height": 900}


# === Falha rápida quando o site RECUSA o acesso ===

def test_abrir_pagina_login_levanta_em_403():
    """403 tem que virar erro imediato e nomeado, não timeout de 30s no
    seletor seguinte."""
    from pregoes.scraper import _abrir_pagina_login

    class _Resp:
        status = 403

    class _Pg:
        def goto(self, url, **kw):
            return _Resp()

    with pytest.raises(RuntimeError) as e:
        _abrir_pagina_login(_Pg(), "https://x/entrar", "almeida")

    msg = str(e.value)
    assert "403" in msg, "o status precisa aparecer para o suporte diagnosticar"
    assert "almeida" in msg.lower()


def test_abrir_pagina_login_segue_em_200_e_sem_resposta():
    """200 passa direto; `None` (dublê/redirect sem resposta) não pode
    virar falso positivo de bloqueio."""
    from pregoes.scraper import _abrir_pagina_login

    class _Resp:
        status = 200

    class _PgOk:
        def goto(self, url, **kw):
            return _Resp()

    class _PgNone:
        def goto(self, url, **kw):
            return None

    _abrir_pagina_login(_PgOk(), "https://x/entrar", "almeida")    # não levanta
    _abrir_pagina_login(_PgNone(), "https://x/entrar", "dantas")   # não levanta


def test_mensagem_amigavel_do_403_nao_culpa_a_senha():
    """O operador não pode ser mandado conferir e-mail/senha quando quem
    barrou foi o site — nem cair no fallback genérico 'algo deu errado'."""
    from pregoes.erros import mensagem_amigavel

    msg = mensagem_amigavel(
        RuntimeError("O site da almeida recusou o acesso (HTTP 403)."))

    assert "Almeida" in msg
    assert "não é problema de e-mail ou senha" in msg
    assert "Algo deu errado" not in msg
