"""Suíte do APRENDIZADO (retroalimentação) — a inteligência central do produto.

O cliente investe tempo ensinando o sistema; estes testes provam que esse
investimento é capturado, reusado, corrigível e visível — rumo a ~100% de
assertividade. Cobre 8 frentes (plano de QA 2026-06-23):

1. DB (persistência: guards + UPSERT)
2. Reuso FUZZY (matcher.melhor_aprendido_fuzzy)
3. Orquestrador (aprendizado ANTES do cache + preço AO VIVO)
4. Salvaguarda do código aprendido (relevância >= 0.30)
5. Captura na UI (_registrar_aprendizado — fornecedor correto, resgate manual)
6. Auto-correção (UPSERT ponta a ponta)
7. Pré-seleção / ranking (_chave_default_opcao)
8. Reuso entre editais (sustentabilidade do circuito)

Exercita o ORQUESTRADOR e o DB reais com banco tmp; o único mock é o SITE
(seam _buscar_query). Sem rede, sem Gemini (judge off), determinístico.
"""
from __future__ import annotations

import pytest

from pregoes import config, db
from pregoes.extrator import ItemExtraido
from pregoes.exportador import SelecaoFornecedor
from pregoes.normalizador import normalizar_descricao as _norm
from pregoes.matcher import melhor_aprendido_fuzzy
from pregoes.scraper import (
    AlmeidaDistribuidor, ResultadoBusca, buscar_precos_para_itens,
)
from pregoes.ui.app import (
    _registrar_aprendizado, _chave_default_opcao, _melhor_opcao,
)


# ============================ Harness ====================================

def _card(desc, marca, preco, cod, forn="almeida"):
    return ResultadoBusca(desc, marca, preco, cod, "exato", forn)


class _SiteFake:
    def __init__(self, regras):
        self.regras = regras
        self.queries: list[str] = []

    def __call__(self, page, query, conf):
        self.queries.append(query)
        for pred, cards in self.regras:
            if pred(query):
                return [ResultadoBusca(c.descricao_encontrada, c.marca, c.preco,
                                       c.codigo_fornecedor, conf, "almeida")
                        for c in cards]
        return []


def _fornecedor(monkeypatch, site):
    forn = AlmeidaDistribuidor(email="x", senha="y")
    monkeypatch.setattr(forn, "_buscar_query", site)
    monkeypatch.setattr(forn._rate_limiter, "acquire", lambda: None)
    return forn


@pytest.fixture
def banco(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "pregoes.db")
    db.init_db(tmp_path / "pregoes.db")
    return tmp_path / "pregoes.db"


def _seed_aprendido(dbp, descricao, codigo, *, forn="almeida", marca="X", preco=None):
    with db.conectar(dbp) as conn:
        db.aprendido_gravar(conn, _norm(descricao), forn, codigo, marca=marca, preco=preco)
        conn.commit()


def _item(desc, n=1, qtd=1, valor=None):
    return ItemExtraido(item_numero=n, descricao=desc, unidade="un",
                        quantidade=qtd, valor_referencia=valor)


def _buscar(itens, forn, **kw):
    kw.setdefault("usar_llm_judge", False)
    return buscar_precos_para_itens(itens, [forn], **kw)


def _conta_aprendidos(dbp):
    with db.conectar(dbp) as conn:
        return conn.execute("SELECT COUNT(*) FROM mapeamentos_aprendidos").fetchone()[0]


def _conta_cache(dbp, desc_norm, forn):
    with db.conectar(dbp) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM precos_cache_descricao WHERE descricao_normalizada=? "
            "AND fornecedor=?", (desc_norm, forn)).fetchone()[0]


# =================== 1. DB — persistência (guards + UPSERT) ===============

def test_db_codigo_vazio_nao_grava(banco):
    with db.conectar(banco) as conn:
        db.aprendido_gravar(conn, _norm("ALICATE"), "almeida", "")
        conn.commit()
    assert _conta_aprendidos(banco) == 0
    with db.conectar(banco) as conn:
        assert db.aprendido_buscar(conn, _norm("ALICATE"), "almeida") is None


def test_db_descricao_vazia_nao_grava(banco):
    with db.conectar(banco) as conn:
        db.aprendido_gravar(conn, "", "almeida", "999")
        conn.commit()
    assert _conta_aprendidos(banco) == 0


def test_db_buscar_com_fornecedor_vazio_retorna_none(banco):
    _seed_aprendido(banco, "ALICATE DE PRESSAO", "A1")
    with db.conectar(banco) as conn:
        assert db.aprendido_buscar(conn, _norm("ALICATE DE PRESSAO"), "") is None


def test_db_upsert_mantem_uma_linha_e_atualiza(banco):
    _seed_aprendido(banco, "TUBO PVC 75", "OLD", marca="TIGRE", preco=1.0)
    _seed_aprendido(banco, "TUBO PVC 75", "NEW", marca="AMANCO", preco=2.0)
    assert _conta_aprendidos(banco) == 1
    with db.conectar(banco) as conn:
        row = db.aprendido_buscar(conn, _norm("TUBO PVC 75"), "almeida")
    assert row["codigo_fornecedor"] == "NEW" and row["marca"] == "AMANCO"


def test_db_separa_por_fornecedor(banco):
    _seed_aprendido(banco, "TUBO PVC 75", "A1", forn="almeida")
    _seed_aprendido(banco, "TUBO PVC 75", "D1", forn="dantas")
    assert _conta_aprendidos(banco) == 2
    with db.conectar(banco) as conn:
        assert db.aprendido_buscar(conn, _norm("TUBO PVC 75"), "almeida")["codigo_fornecedor"] == "A1"
        assert db.aprendido_buscar(conn, _norm("TUBO PVC 75"), "dantas")["codigo_fornecedor"] == "D1"


def test_db_marca_e_preco_null_aceitos(banco):
    _seed_aprendido(banco, "TUBO PVC 75", "A1", marca=None, preco=None)
    with db.conectar(banco) as conn:
        row = db.aprendido_buscar(conn, _norm("TUBO PVC 75"), "almeida")
    assert row["codigo_fornecedor"] == "A1"
    assert row["marca"] is None and row["preco"] is None


# =================== 2. Reuso FUZZY (extras do plano) =====================

def test_fuzzy_multi_eixo_um_divergente_barra_reuso():
    # volt coincide (220) mas watt diverge (3000≠2000) → não reusa.
    aprendidos = [{
        "fornecedor": "almeida",
        "descricao_normalizada": _norm("TRANSFORMADOR 220V 2000W"),
        "codigo_fornecedor": "T2000", "marca": "X", "preco": None,
    }]
    assert melhor_aprendido_fuzzy("TRANSFORMADOR 220V 3000W", "almeida", aprendidos) is None


# =============== 3. Orquestrador — aprendizado antes do cache =============

def test_orq_preco_vem_ao_vivo_nao_o_salvo(banco, monkeypatch):
    """Preço da escolha aprendida tem que ser o ATUAL do site, NÃO o salvo no
    aprendizado — crítico para a precificação na hora do pregão."""
    _seed_aprendido(banco, "ABRACADEIRA NYLON 100", "555", preco=1.00)
    site = _SiteFake([
        (lambda q: q.strip() == "555",
         [_card("ABRACADEIRA NYLON 100 PRETO FERTAK", "FERTAK", 2.50, "555")]),
        (lambda q: True, []),
    ])
    forn = _fornecedor(monkeypatch, site)
    lote = _buscar([_item("ABRACADEIRA NYLON 100")], forn)
    res = lote.resultados[0]
    assert res and res[0].veio_por_aprendizado is True
    assert res[0].preco == 2.50, "preço AO VIVO do site, não o 1.00 salvo"


def test_orq_reuso_fuzzy_ponta_a_ponta(banco, monkeypatch):
    """Descrição PARECIDA (não exata) aciona o código aprendido via fuzzy —
    o e2e antigo só cobria match EXATO."""
    _seed_aprendido(banco, "VALVULA LAVATORIO REBOUCAS 7/8", "46439")
    site = _SiteFake([
        (lambda q: q.strip() == "46439",
         [_card("VALVULA LAVATORIO REBOUCAS 7/8 CROMADA", "CENSI", 12.0, "46439")]),
        (lambda q: True, []),
    ])
    forn = _fornecedor(monkeypatch, site)
    lote = _buscar([_item("VALVULA P/ LAVATORIO REBOUCAS 7/8")], forn)
    assert lote.codigo_hits >= 1
    assert lote.resultados[0] and lote.resultados[0][0].veio_por_aprendizado is True
    assert "46439" in site.queries


def test_orq_item_aprendido_nao_grava_cache(banco, monkeypatch):
    _seed_aprendido(banco, "TUBO PVC SOLDAVEL 75MM", "8811")
    site = _SiteFake([
        (lambda q: q.strip() == "8811",
         [_card("TUBO PVC SOLDAVEL 75MM TIGRE", "TIGRE", 90.0, "8811")]),
        (lambda q: True, []),
    ])
    forn = _fornecedor(monkeypatch, site)
    _buscar([_item("TUBO PVC SOLDAVEL 75MM")], forn)
    assert _conta_cache(banco, _norm("TUBO PVC SOLDAVEL 75MM"), "almeida") == 0


# =================== 4. Salvaguarda do código aprendido ==================

def test_salvaguarda_codigo_fora_de_linha_cai_na_textual(banco, monkeypatch):
    """Código aprendido descontinuado: site devolve [] por código → cai na
    textual, sem travar e sem virar 'sua escolha anterior'."""
    _seed_aprendido(banco, "ALICATE DE PRESSAO", "999")
    site = _SiteFake([
        (lambda q: q.strip() == "999", []),     # fora de linha
        (lambda q: True, [_card("ALICATE DE PRESSAO 10POL", "MORCEGO", 35.0, "A1")]),
    ])
    forn = _fornecedor(monkeypatch, site)
    lote = _buscar([_item("ALICATE DE PRESSAO")], forn)
    res = lote.resultados[0]
    assert res and any("ALICATE" in o.descricao_encontrada for o in res)
    assert not any(getattr(o, "veio_por_aprendizado", False) for o in res)


def test_salvaguarda_excecao_no_codigo_registra_erro_e_segue(banco, monkeypatch):
    """buscar_por_codigo lança → ErroBusca registrado, item cai na textual,
    o lote NÃO é derrubado."""
    _seed_aprendido(banco, "ALICATE DE PRESSAO", "999")

    def site(page, query, conf):
        if query.strip() == "999":
            raise RuntimeError("timeout no detalhe")
        return [ResultadoBusca("ALICATE DE PRESSAO 10POL", "MORCEGO", 35.0, "A1", conf, "almeida")]

    forn = AlmeidaDistribuidor(email="x", senha="y")
    monkeypatch.setattr(forn, "_buscar_query", site)
    monkeypatch.setattr(forn._rate_limiter, "acquire", lambda: None)
    lote = _buscar([_item("ALICATE DE PRESSAO")], forn)
    assert lote.resultados[0], "caiu na textual"
    assert any(e.fornecedor == "almeida" for e in lote.erros)


# =================== 5. Captura na UI (_registrar_aprendizado) ============

def test_captura_grava_o_fornecedor_da_selecao(banco):
    """A seleção carrega o fornecedor; grava SÓ ('dantas','D1')."""
    itens = [_item("PARAFUSO SEXTAVADO 5/16")]
    selecoes = {0: SelecaoFornecedor(preco=0.5, codigo_fornecedor="D1",
                                     marca="CISER", fornecedor="dantas")}
    resultados = {0: [_card("PARAFUSO SEXTAVADO 5/16 ZINCADO", "CISER", 0.5, "D1", forn="dantas")]}
    _registrar_aprendizado(itens, selecoes, resultados)
    with db.conectar(banco) as conn:
        assert db.aprendido_buscar(conn, _norm("PARAFUSO SEXTAVADO 5/16"), "dantas")["codigo_fornecedor"] == "D1"
        assert db.aprendido_buscar(conn, _norm("PARAFUSO SEXTAVADO 5/16"), "almeida") is None


def test_captura_resgate_manual_sem_match_ainda_aprende(banco):
    """FIX QA 2026-06: a seleção tem fornecedor próprio, então mesmo SEM casar
    por (preço,código,marca) em resultados, o aprendizado é gravado — antes era
    descartado em silêncio."""
    itens = [_item("REVESTIMENTO TEXTURA")]
    # resultados NÃO contém a opção escolhida (resgate manual de outra via)
    selecoes = {0: SelecaoFornecedor(preco=88.0, codigo_fornecedor="M9",
                                     marca="SUVINIL", fornecedor="almeida")}
    _registrar_aprendizado(itens, selecoes, {0: []})
    with db.conectar(banco) as conn:
        row = db.aprendido_buscar(conn, _norm("REVESTIMENTO TEXTURA"), "almeida")
    assert row is not None and row["codigo_fornecedor"] == "M9"


def test_captura_colisao_grava_o_fornecedor_escolhido(banco):
    """Dois fornecedores com (preço,código,marca) idênticos: grava o que a
    SELEÇÃO indica (dantas), não o 1º da lista (almeida) — resolve a
    ambiguidade que o re-match tinha."""
    itens = [_item("FITA ISOLANTE 19MM")]
    resultados = {0: [
        _card("FITA ISOLANTE 19MM", "3M", 3.0, "Z", forn="almeida"),
        _card("FITA ISOLANTE 19MM", "3M", 3.0, "Z", forn="dantas"),
    ]}
    selecoes = {0: SelecaoFornecedor(preco=3.0, codigo_fornecedor="Z",
                                     marca="3M", fornecedor="dantas")}
    _registrar_aprendizado(itens, selecoes, resultados)
    assert _conta_aprendidos(banco) == 1
    with db.conectar(banco) as conn:
        assert db.aprendido_buscar(conn, _norm("FITA ISOLANTE 19MM"), "dantas") is not None
        assert db.aprendido_buscar(conn, _norm("FITA ISOLANTE 19MM"), "almeida") is None


def test_captura_item_nao_usar_nao_e_aprendido(banco):
    """Item fora de `selecoes` ('Não usar') não vira conhecimento espúrio."""
    itens = [_item("ITEM A", n=1), _item("ITEM B", n=2)]
    selecoes = {0: SelecaoFornecedor(preco=1.0, codigo_fornecedor="A1",
                                     marca="X", fornecedor="almeida")}  # item 1 (B) ausente
    resultados = {0: [_card("ITEM A", "X", 1.0, "A1")]}
    _registrar_aprendizado(itens, selecoes, resultados)
    assert _conta_aprendidos(banco) == 1
    with db.conectar(banco) as conn:
        assert db.aprendido_buscar(conn, _norm("ITEM B"), "almeida") is None


def test_captura_descricao_so_espacos_nao_grava(banco):
    itens = [_item("   ")]
    selecoes = {0: SelecaoFornecedor(preco=1.0, codigo_fornecedor="A1",
                                     marca="X", fornecedor="almeida")}
    _registrar_aprendizado(itens, selecoes, {0: []})
    assert _conta_aprendidos(banco) == 0


def test_captura_e_nao_bloqueante_em_erro(banco, monkeypatch):
    """Falha ao gravar não derruba a geração da planilha (try/except)."""
    import pregoes.ui.app as app
    monkeypatch.setattr(app, "get_conn",
                        lambda: (_ for _ in ()).throw(RuntimeError("db travado")))
    # não deve levantar
    _registrar_aprendizado([_item("X")],
                           {0: SelecaoFornecedor(preco=1.0, codigo_fornecedor="A1",
                                                 marca="X", fornecedor="almeida")},
                           {0: []})


# =================== 6. Auto-correção (UPSERT ponta a ponta) ==============

def test_correcao_regerar_sobrepoe_a_escolha(banco):
    itens = [_item("PARAFUSO 5/16")]
    res = {0: [_card("PARAFUSO 5/16 A", "A", 0.5, "OLD"),
               _card("PARAFUSO 5/16 B", "CISER", 0.8, "NEW")]}
    # 1ª geração: operador escolhe OLD
    _registrar_aprendizado(itens, {0: SelecaoFornecedor(
        preco=0.5, codigo_fornecedor="OLD", marca="A", fornecedor="almeida")}, res)
    # 2ª geração (correção): escolhe NEW
    _registrar_aprendizado(itens, {0: SelecaoFornecedor(
        preco=0.8, codigo_fornecedor="NEW", marca="CISER", fornecedor="almeida")}, res)
    assert _conta_aprendidos(banco) == 1
    with db.conectar(banco) as conn:
        row = db.aprendido_buscar(conn, _norm("PARAFUSO 5/16"), "almeida")
    assert row["codigo_fornecedor"] == "NEW" and row["marca"] == "CISER"


def test_correcao_proxima_busca_reflete(banco, monkeypatch):
    """Após corrigir o aprendizado, a busca seguinte consulta o código NOVO."""
    _seed_aprendido(banco, "PARAFUSO 5/16", "OLD")
    _seed_aprendido(banco, "PARAFUSO 5/16", "NEW")   # correção (UPSERT)
    site = _SiteFake([
        (lambda q: q.strip() == "NEW",
         [_card("PARAFUSO SEXTAVADO 5/16 CISER", "CISER", 0.8, "NEW")]),
        (lambda q: True, []),
    ])
    forn = _fornecedor(monkeypatch, site)
    lote = _buscar([_item("PARAFUSO 5/16")], forn)
    assert lote.resultados[0] and lote.resultados[0][0].codigo_fornecedor == "NEW"
    assert "NEW" in site.queries and "OLD" not in site.queries


# =================== 7. Pré-seleção / ranking ============================

def test_ranking_aprendido_vence_relevancia_maior():
    desc = "ALICATE DE PRESSAO 10"
    aprendido = _card("ALICATE PRESSAO MORCEGO", "MORCEGO", 30.0, "A1")
    aprendido = ResultadoBusca(aprendido.descricao_encontrada, aprendido.marca,
                               aprendido.preco, aprendido.codigo_fornecedor,
                               "exato", "almeida", veio_por_aprendizado=True)
    melhor_texto = _card("ALICATE DE PRESSAO 10 POLEGADAS PRO", "GEDORE", 28.0, "B2")
    escolhido = _melhor_opcao(desc, [melhor_texto, aprendido])
    assert escolhido is aprendido, "a escolha anterior vence o melhor match textual"


def test_ranking_aprendido_acima_de_catalogo():
    desc = "TUBO PVC 75"
    aprendido = ResultadoBusca("TUBO PVC 75 AMANCO", "AMANCO", 90.0, "A1",
                               "exato", "almeida", veio_por_aprendizado=True)
    catalogo = ResultadoBusca("TUBO PVC 75 TIGRE", "TIGRE", 85.0, "C1",
                              "exato", "almeida", no_catalogo=True)
    assert _melhor_opcao(desc, [catalogo, aprendido]) is aprendido


def test_ranking_default_reflete_a_pre_selecao():
    """O 1º da lista ordenada por _chave_default_opcao é o melhor — garante que
    o badge e o checkbox inicial concordam (o que a UI mostra = o que exporta)."""
    desc = "TUBO PVC 75"
    a = ResultadoBusca("TUBO PVC 75 X", "X", 90.0, "A1", "exato", "almeida",
                       veio_por_aprendizado=True)
    b = _card("TUBO PVC 75 Y", "Y", 80.0, "B1")
    ordenada = sorted([b, a], key=lambda o: _chave_default_opcao(desc, o))
    assert ordenada[0] is a is _melhor_opcao(desc, [a, b])


# =================== 8. Reuso entre editais (sustentabilidade) ============

def test_reuso_entre_dois_editais_separados(banco, monkeypatch):
    """1º edital ensina; 2º edital (lote separado) reusa por código sem textual."""
    site = _SiteFake([
        (lambda q: q.strip() == "8811",
         [_card("TUBO PVC SOLDAVEL 75MM TIGRE", "TIGRE", 90.0, "8811")]),
        (lambda q: True, []),
    ])
    forn = _fornecedor(monkeypatch, site)
    _seed_aprendido(banco, "TUBO PVC SOLDAVEL 75MM", "8811")
    lote2 = _buscar([_item("TUBO PVC SOLDAVEL 75MM")], forn)
    assert lote2.codigo_hits >= 1
    assert lote2.resultados[0][0].veio_por_aprendizado is True
    assert "8811" in site.queries


def test_reuso_entre_editais_nao_dispara_se_dimensao_diverge(banco, monkeypatch):
    """Cimento 50KG aprendido NÃO reusa para edital de 25KG (gate de dimensão)."""
    _seed_aprendido(banco, "CIMENTO PORTLAND CP II SACO 50KG", "111")
    site = _SiteFake([
        (lambda q: q.strip() == "111", [_card("CIMENTO CP II 50KG POTY", "POTY", 38.0, "111")]),
        (lambda q: True, []),    # textual não acha o de 25KG
    ])
    forn = _fornecedor(monkeypatch, site)
    lote = _buscar([_item("CIMENTO PORTLAND CP II SACO 25KG")], forn)
    # não reusou o 111 (50kg) — não é 'sua escolha anterior'
    assert not any(getattr(o, "veio_por_aprendizado", False)
                   for o in lote.resultados[0])
    assert "111" not in site.queries
