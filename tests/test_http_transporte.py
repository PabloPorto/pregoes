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
    monkeypatch.setattr(scraper, "_http_get", lambda u, c: _PAGINA_SEM_RESULTADO)
    forn = _FornHTTP()
    assert scraper._consultar_cards_http(forn, "http://u") == []
    assert forn._http_cookie, "cookie deve ser mantido"


# --- Limite silencioso: o site devolve a PÁGINA INICIAL no lugar da busca ---
# (campo 2026-11-14: ~100 consultas em ~100 s do mesmo IP → home por ~5 min,
# status 200. O lote gravava "não achou" para dezenas de itens seguidos.)

_PAGINA_SEM_RESULTADO = ('<body><div class="products-view">Não foram encontrados '
                         'produtos com os termos da busca</div></body>')
_PAGINA_INICIAL = '<body class="body-index"><div class="block-slideshow">banners</div></body>'


def test_pagina_inicial_nao_e_busca_vazia():
    assert scraper._pagina_inicial_no_lugar_da_busca(_PAGINA_INICIAL)
    assert not scraper._pagina_inicial_no_lugar_da_busca(_PAGINA_SEM_RESULTADO)
    assert not scraper._pagina_inicial_no_lugar_da_busca(
        "<body>" + _html_card("1", "TUBO", "8.32", "AMANCO", "u") + "</body>")


def test_lote_espera_o_limite_passar_e_repete_a_consulta(monkeypatch):
    """Home 2x e depois a busca de verdade: a consulta devolve os cards — o
    item NÃO sai como 'não achou' nem como erro."""
    monkeypatch.setattr(scraper, "_PAUSA_LIMITE_S", 0.0)
    respostas = [_PAGINA_INICIAL, _PAGINA_INICIAL,
                 "<body>" + _html_card("9", "CADEADO 40MM", "31.90", "PADO", "u") + "</body>"]
    monkeypatch.setattr(scraper, "_http_get", lambda u, c: respostas.pop(0))
    forn = _FornHTTP()
    cards = scraper._consultar_cards_http(forn, "http://u")
    assert [c["data-codigo-produto"] for c in cards] == ["9"]
    assert respostas == [] and forn._http_cookie


def test_limite_que_nao_passa_vira_erro_e_nunca_lista_vazia(monkeypatch):
    monkeypatch.setattr(scraper, "_PAUSA_LIMITE_S", 0.0)
    chamadas = []
    monkeypatch.setattr(scraper, "_http_get",
                        lambda u, c: chamadas.append(u) or _PAGINA_INICIAL)
    with pytest.raises(scraper.SessaoHTTPExpirou) as e:
        scraper._consultar_cards_http(_FornHTTP(), "http://u")
    assert e.value.status == scraper._STATUS_LIMITE
    assert len(chamadas) == scraper._MAX_ESPERAS_LIMITE + 1


def test_busca_manual_nao_espera_o_limite(monkeypatch):
    """`_sem_espera` (busca manual): avisa na hora, sem prender a tela."""
    chamadas = []
    monkeypatch.setattr(scraper, "_http_get",
                        lambda u, c: chamadas.append(u) or _PAGINA_INICIAL)
    forn = _FornHTTP()
    forn._sem_espera = True
    with pytest.raises(scraper.SessaoHTTPExpirou) as e:
        scraper._consultar_cards_http(forn, "http://u")
    assert e.value.status == scraper._STATUS_LIMITE and len(chamadas) == 1
    assert scraper.pausa_por_limite() == 0


def test_teto_de_cards_conta_produtos_e_nao_tags_repetidas():
    """O site repete a tag de cada produto (17x na página logada): com o teto
    em tags, só os ~5 primeiros produtos eram lidos."""
    html = "<body>" + "".join(
        _html_card(str(cod), f"PRODUTO {cod}", "8.32", "M", "u") * 17
        for cod in range(1, 25)) + "</body>"
    cards = scraper._cards_do_html(html, 30)
    assert [c["data-codigo-produto"] for c in cards] == [str(c) for c in range(1, 25)]
    assert len(scraper._cards_do_html(html, 10)) == 10


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


def test_janela_deixa_passar_ate_o_teto_e_depois_dosa():
    """80 em 5 min: as primeiras saem na hora; a seguinte espera a mais antiga
    sair da janela (limite real dos sites, medido em 2026-11-14)."""
    agora = [0.0]
    dormidas = []

    def dormir(seg):
        dormidas.append(seg)
        agora[0] += seg

    j = scraper._JanelaDeConsultas(3, 300, relogio=lambda: agora[0], dormir=dormir)
    for _ in range(3):
        j.acquire()
        agora[0] += 10          # consultas em t=0, 10, 20
    assert dormidas == []
    assert j.espera_restante() == 0
    vistos = []
    j._dormir = lambda seg: (vistos.append(j.espera_restante()), dormir(seg))
    j.acquire()                 # t=30: a de t=0 só sai da janela em t=300
    assert dormidas == [270]
    assert vistos == [270] and j.espera_restante() == 0   # a tela sabe quanto falta
    agora[0] += 400             # janela esvaziou
    j.acquire()
    assert dormidas == [270]


def test_teto_configurado_fica_abaixo_do_limite_medido():
    assert config.SCRAPER_JANELA_SEGUNDOS >= 600
    assert config.SCRAPER_MAX_CONSULTAS_POR_JANELA <= 85


def test_depois_do_bloqueio_so_uma_thread_sonda_por_vez(monkeypatch):
    """8 threads do lote não repetem a consulta juntas a cada sonda: enquanto
    uma sonda, as outras esperam — 1 consulta gasta por rodada, não 8."""
    import threading
    monkeypatch.setattr(scraper, "_PAUSA_LIMITE_S", 0.0)
    lk = threading.Lock()
    estado = {"homes": 6, "simultaneas": 0, "pico_sondando": 0, "chamadas": 0}
    ok = "<body>" + _html_card("9", "CADEADO 40MM", "31.90", "PADO", "u") + "</body>"

    def _get(u, c):
        with lk:
            estado["chamadas"] += 1
            bloqueado = estado["homes"] > 0
            if bloqueado:
                estado["homes"] -= 1
        return _PAGINA_INICIAL if bloqueado else ok

    monkeypatch.setattr(scraper, "_http_get", _get)
    saida = []
    ths = [threading.Thread(
        target=lambda: saida.append(scraper._consultar_cards_http(_FornHTTP(), "http://u")))
        for _ in range(4)]
    [t.start() for t in ths]
    [t.join(timeout=20) for t in ths]
    assert len(saida) == 4 and all(len(cards) == 1 for cards in saida)
    assert estado["chamadas"] == 6 + 4   # nenhuma consulta desperdiçada além das 6 "home"


# ===================== ritmo "rapido": andar no limite (Q20) ================

def _ritmo(monkeypatch, ritmo):
    from pregoes import credenciais
    monkeypatch.setattr(credenciais, "carregar_ritmo_busca", lambda: ritmo)


class _JanelaContada:
    def __init__(self):
        self.pedidos = 0

    def acquire(self):
        self.pedidos += 1

    def espera_restante(self):
        return 0.0


def test_ritmo_rapido_nao_dosa_as_consultas_e_o_seguro_dosa(monkeypatch):
    ok = "<body>" + _html_card("9", "CADEADO 40MM", "31.90", "PADO", "u") + "</body>"
    monkeypatch.setattr(scraper, "_http_get", lambda u, c: ok)
    janela = _JanelaContada()
    monkeypatch.setattr(scraper, "_janela", janela)

    _ritmo(monkeypatch, None)                       # sem escolha: padrão de fábrica
    assert scraper.ritmo_da_busca() == config.SCRAPER_RITMO_PADRAO
    _ritmo(monkeypatch, "seguro")
    scraper._consultar_cards_http(_FornHTTP(), "http://u")
    assert janela.pedidos == 1
    _ritmo(monkeypatch, "rapido")
    assert scraper.ritmo_da_busca() == "rapido"
    for _ in range(3):
        assert scraper._consultar_cards_http(_FornHTTP(), "http://u")
    assert janela.pedidos == 1                      # a janela não foi consultada


def test_ritmo_rapido_bloqueado_pausa_e_repete_sem_perder_o_item(monkeypatch):
    """No limite, o bloqueio é o ciclo normal: a consulta bloqueada espera e
    é REPETIDA — o item nunca sai como 'não achou'."""
    _ritmo(monkeypatch, "rapido")
    respostas = [_PAGINA_INICIAL, _PAGINA_INICIAL,
                 "<body>" + _html_card("9", "CADEADO 40MM", "31.90", "PADO", "u") + "</body>"]
    monkeypatch.setattr(scraper, "_http_get", lambda u, c: respostas.pop(0))
    antes = scraper.bloqueios_por_limite()
    cards = scraper._consultar_cards_http(_FornHTTP(), "http://u")
    assert [c["data-codigo-produto"] for c in cards] == ["9"] and respostas == []
    assert scraper.bloqueios_por_limite() == antes + 1     # UM bloqueio, duas sondas


def test_freio_pausa_longa_no_bloqueio_novo_e_sonda_curta_se_ele_continua(monkeypatch):
    relogio = [1000.0]
    monkeypatch.setattr(scraper.time, "monotonic", lambda: relogio[0])
    f = scraper._FreioDeConsultas()

    assert f.bloqueou(310, 30) == (True, 0.0)              # bloqueio novo: 5 min
    assert f.segundos_restantes() == 310 and f.bloqueios == 1
    relogio[0] += 1                                        # outra thread viu o mesmo bloqueio
    assert f.bloqueou(310, 30) == (False, 1.0)
    assert f.segundos_restantes() == 309                   # não estica a pausa
    relogio[0] += 309                                      # a pausa acabou…
    assert f.segundos_restantes() == 0
    relogio[0] += 2                                        # …e a sonda ainda veio bloqueada
    novo, dura = f.bloqueou(310, 30)
    assert (novo, dura) == (False, 312.0)
    assert f.segundos_restantes() == 30 and f.bloqueios == 1    # só uma sonda, não 5 min
    relogio[0] += 30 + 100                                 # liberou; 100 s consultando
    assert f.bloqueou(310, 30) == (True, 0.0)              # o bloqueio seguinte é NOVO
    assert f.segundos_restantes() == 310 and f.bloqueios == 2


def test_bloqueio_que_dura_demais_desiste_pelo_relogio(monkeypatch):
    """Várias threads se revezam na sonda: quem decide a desistência é a
    duração do bloqueio, não a contagem de cada thread."""
    relogio = [1000.0]
    monkeypatch.setattr(scraper.time, "monotonic", lambda: relogio[0])
    monkeypatch.setattr(scraper, "_freio", scraper._FreioDeConsultas())
    monkeypatch.setattr(scraper.time, "sleep", lambda s: relogio.__setitem__(0, relogio[0] + s))
    monkeypatch.setattr(scraper, "_PAUSA_LIMITE_S", 310.0)
    monkeypatch.setattr(scraper, "_SONDA_LIMITE_S", 30.0)
    chamadas = []
    monkeypatch.setattr(scraper, "_http_get",
                        lambda u, c: chamadas.append(relogio[0]) or _PAGINA_INICIAL)
    with pytest.raises(scraper.SessaoHTTPExpirou) as e:
        scraper._consultar_cards_http(_FornHTTP(), "http://u")
    assert e.value.status == scraper._STATUS_LIMITE
    # 1ª consulta, uma sonda depois dos 5 min e depois de 30 em 30 s até ~15 min
    assert chamadas[1] - chamadas[0] == 310
    assert all(b - a == 30 for a, b in zip(chamadas[1:], chamadas[2:]))
    assert scraper._DESISTE_LIMITE_S < chamadas[-1] - chamadas[0] <= scraper._DESISTE_LIMITE_S + 30
    assert len(chamadas) < scraper._MAX_ESPERAS_LIMITE + 1


def test_busca_manual_com_o_lote_em_pausa_avisa_sem_consultar(monkeypatch):
    chamadas = []
    monkeypatch.setattr(scraper, "_http_get", lambda u, c: chamadas.append(u) or "x")
    scraper._freio.fechar(200)
    forn = _FornHTTP()
    forn._sem_espera = True
    with pytest.raises(scraper.SessaoHTTPExpirou) as e:
        scraper._consultar_cards_http(forn, "http://u")
    assert e.value.status == scraper._STATUS_LIMITE and chamadas == []
    assert 190 < scraper.pausa_por_limite() <= 200


def test_ritmo_da_busca_fica_salvo_nas_preferencias(tmp_path, monkeypatch):
    from pregoes import credenciais
    monkeypatch.setattr(credenciais, "CAMINHO", tmp_path / "credenciais.json")
    assert credenciais.carregar_ritmo_busca() is None
    credenciais.salvar("almeida", "a@x", "s")
    credenciais.salvar_ritmo_busca("rapido")
    assert credenciais.carregar_ritmo_busca() == "rapido"
    assert credenciais.carregar("almeida")["email"] == "a@x"      # preserva o resto
    assert scraper.ritmo_da_busca() == "rapido"
    credenciais.salvar_ritmo_busca("seguro")
    assert scraper.ritmo_da_busca() == "seguro"
    with pytest.raises(ValueError):
        credenciais.salvar_ritmo_busca("turbo")
