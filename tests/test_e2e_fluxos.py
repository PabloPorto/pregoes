"""E2E dos fluxos críticos — como o sistema realmente roda.

Exercita o ORQUESTRADOR real (`buscar_precos_para_itens`) com BANCO real (tmp)
e matcher/tributação/exportador REAIS. O único mock é o SITE do fornecedor, no
seam mais fundo (`_buscar_query`) — assim toda a lógica de relevância,
dimensão, marcação por código, top-N, cache e camadas roda de verdade.

Cobre o que o cliente pediu confiabilidade:
- busca de preço (textual) e CACHE (não re-bate no site)
- busca por ID/código de produto (Camada 0)
- catálogo + reconhecimento "no seu catálogo" por código
- aprendizado (Camada 4 — reuso de escolha anterior)
- filtro de relevância + dimensão (produto errado não passa)
- consulta NCM + markup por ST + coluna na planilha exportada
- fluxo completo: itens → busca → seleção → planilha

Áreas já validadas manualmente em campo (login, extração de PDF, export) têm
só uma validação de fumaça aqui — não o foco.
"""
from __future__ import annotations

import pytest
from openpyxl import load_workbook

from pregoes import config, db, tributacao
from pregoes.extrator import ItemExtraido
from pregoes.exportador import exportar, SelecaoFornecedor
from pregoes.normalizador import normalizar_descricao
from pregoes.scraper import (
    AlmeidaDistribuidor, ResultadoBusca, buscar_precos_para_itens,
)


# ============================ Harness ====================================

def _card(desc, marca, preco, cod):
    """Um card 'cru' (a confiança é carimbada pela camada, como o site real)."""
    return ResultadoBusca(desc, marca, preco, cod, "exato", "almeida")


class _SiteFake:
    """Programa o que o site devolve por QUERY. `buscar()` manda keywords;
    `buscar_por_codigo()` manda o código como query — então dá para simular a
    Camada 0 (código) e a busca textual no mesmo fornecedor. Registra as
    queries para os testes afirmarem se o site foi (ou não) consultado."""

    def __init__(self, regras):
        # regras: lista de (predicado(query)->bool, [cards])
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


def _fornecedor(monkeypatch, site: _SiteFake):
    """AlmeidaDistribuidor REAL (toda a lógica de buscar/por_codigo), só com o
    fetch do site e o rate-limiter neutralizados — sem rede nem navegador."""
    forn = AlmeidaDistribuidor(email="x", senha="y")
    monkeypatch.setattr(forn, "_buscar_query", site)
    monkeypatch.setattr(forn._rate_limiter, "acquire", lambda: None)
    return forn


@pytest.fixture
def banco(tmp_path, monkeypatch):
    """Aponta o config para um banco tmp isolado (o orquestrador usa
    config.DB_PATH internamente) e devolve o caminho."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "pregoes.db")
    db.init_db(tmp_path / "pregoes.db")
    return tmp_path / "pregoes.db"


def _seed_produto(dbp, descricao, *, codigo_almeida=None, codigo_dantas=None,
                  marca=None, ncm=None):
    with db.conectar(dbp) as conn:
        conn.execute(
            "INSERT INTO produtos (descricao_canonica, unidade_padrao, marca, "
            "codigo_almeida, codigo_dantas, ncm) VALUES (?,?,?,?,?,?)",
            (descricao, "un", marca, codigo_almeida, codigo_dantas, ncm))
        conn.commit()


def _seed_aprendido(dbp, descricao, codigo, marca="X"):
    with db.conectar(dbp) as conn:
        db.aprendido_gravar(conn, normalizar_descricao(descricao), "almeida",
                            codigo, marca=marca)
        conn.commit()


def _item(desc, n=1, qtd=1, valor=None):
    return ItemExtraido(item_numero=n, descricao=desc, unidade="un",
                        quantidade=qtd, valor_referencia=valor)


def _buscar(itens, forn, **kw):
    kw.setdefault("usar_llm_judge", False)   # judge é externo (Gemini) — fora do escopo
    return buscar_precos_para_itens(itens, [forn], **kw)


# ===================== Fluxo: busca de preço (textual) ===================

def test_busca_textual_traz_opcoes_relevantes(banco, monkeypatch):
    site = _SiteFake([(lambda q: True, [
        _card("TORNEIRA METAL CROMADA BICA MOVEL 1/2", "DECA", 89.0, "501"),
        _card("TORNEIRA METAL CROMADA BICA MOVEL 1/2", "DOCOL", 95.0, "502"),
    ])])
    forn = _fornecedor(monkeypatch, site)
    lote = _buscar([_item("TORNEIRA METAL CROMADA BICA MOVEL 1/2")], forn)
    marcas = {o.marca for o in lote.resultados[0]}
    assert marcas == {"DECA", "DOCOL"}
    assert lote.cache_misses >= 1


def test_busca_nada_relevante_retorna_vazio_sem_inventar(banco, monkeypatch):
    site = _SiteFake([(lambda q: True, [
        _card("PARAFUSO SEXTAVADO 5/16", "CISER", 0.5, "9"),
    ])])
    forn = _fornecedor(monkeypatch, site)
    lote = _buscar([_item("TORNEIRA METAL BICA MOVEL")], forn)
    assert lote.resultados[0] == []


# =========================== Fluxo: CACHE ================================

def test_cache_segunda_busca_nao_rebate_no_site(banco, monkeypatch):
    site = _SiteFake([(lambda q: True, [
        _card("CIMENTO CP II 50KG POTY", "POTY", 38.5, "777"),
    ])])
    forn = _fornecedor(monkeypatch, site)
    item = [_item("CIMENTO PORTLAND CP II 50KG")]

    lote1 = _buscar(item, forn)
    assert lote1.resultados[0] and lote1.cache_misses >= 1
    n_apos_1 = len(site.queries)

    lote2 = _buscar(item, forn)
    assert lote2.cache_hits >= 1, "2ª busca deve vir do cache"
    assert len(site.queries) == n_apos_1, "site NÃO pode ser consultado de novo"
    assert lote2.resultados[0], "cache devolve o mesmo resultado"


def test_cache_resultado_vazio_nao_e_cacheado(banco, monkeypatch):
    """Soluço do site (nada encontrado) não vira 'Não encontrado' por 2 dias —
    a re-busca tenta de novo (fix do cache negativo). O estado alterna ENTRE as
    duas buscas (não por query — cada busca faz várias queries internas)."""
    estado = {"acha": False}

    def site(page, query, conf):
        if not estado["acha"]:
            return []
        return [ResultadoBusca("VALVULA DESCARGA 1.1/2 CROMADA", "CENSI",
                               42.0, "313", conf, "almeida")]

    forn = AlmeidaDistribuidor(email="x", senha="y")
    monkeypatch.setattr(forn, "_buscar_query", site)
    monkeypatch.setattr(forn._rate_limiter, "acquire", lambda: None)
    item = [_item("VALVULA DE DESCARGA 1.1/2 CROMADA")]

    assert _buscar(item, forn).resultados[0] == []      # 1ª rodada: site instável
    estado["acha"] = True
    assert _buscar(item, forn).resultados[0], "vazio não cacheado → re-busca acha"


# ================= Fluxo: busca por ID/código (Camada 0) =================

def test_busca_por_codigo_do_catalogo(banco, monkeypatch):
    """Item casa com produto do catálogo → busca pelo CÓDIGO (determinística),
    não por texto. codigo_hits registra a Camada 0."""
    _seed_produto(banco, "tubo pvc soldavel 75mm", codigo_almeida="8811",
                  marca="TIGRE")
    # O site devolve o produto exato SÓ quando consultado pelo código 8811.
    site = _SiteFake([
        (lambda q: q.strip() == "8811",
         [_card("TUBO PVC SOLDAVEL 75MM BARRA 6M", "TIGRE", 92.0, "8811")]),
        (lambda q: True, []),    # busca textual não acharia nada
    ])
    forn = _fornecedor(monkeypatch, site)
    lote = _buscar([_item("TUBO PVC SOLDAVEL 75MM")], forn)
    assert lote.codigo_hits >= 1, "deve resolver pela Camada 0 (código)"
    assert lote.resultados[0][0].codigo_fornecedor == "8811"
    assert lote.resultados[0][0].confianca == "exato"   # determinística
    assert "8811" in site.queries     # consultou pelo código
    # NOTA (achado): o resultado por código NÃO recebe o selo no_catalogo
    # (no_catalogo=False), embora seja o sinal de catálogo mais forte. Só o
    # match textual com código no catálogo recebe o selo. Ver relatório.


def test_busca_por_codigo_salvaguarda_descarta_produto_sem_relacao(banco, monkeypatch):
    """Se o código histórico resolver para um produto SEM relação (site mudou),
    a salvaguarda descarta e cai na textual — não entrega o errado como 'exato'."""
    _seed_produto(banco, "tubo pvc soldavel 75mm", codigo_almeida="8811",
                  marca="TIGRE")
    site = _SiteFake([
        # código 8811 agora devolve um produto totalmente diferente
        (lambda q: q.strip() == "8811",
         [_card("PARAFUSO SEXTAVADO 5/16 ZINCADO", "CISER", 0.4, "8811")]),
        # textual acha o tubo certo
        (lambda q: True,
         [_card("TUBO PVC SOLDAVEL 75MM 6M", "AMANCO", 90.0, "1530")]),
    ])
    forn = _fornecedor(monkeypatch, site)
    lote = _buscar([_item("TUBO PVC SOLDAVEL 75MM")], forn)
    descs = [o.descricao_encontrada for o in lote.resultados[0]]
    assert any("TUBO" in d for d in descs)
    assert not any("PARAFUSO" in d for d in descs), "produto sem relação descartado"


# ===================== Fluxo: aprendizado (Camada 4) =====================

def test_aprendizado_reusa_escolha_anterior_por_codigo(banco, monkeypatch):
    """Descrição já 'ensinada' → vai direto no código aprendido (sem texto)."""
    item_desc = "ABRACADEIRA DE NYLON 100 X 2,5MM PCT C/100"
    _seed_aprendido(banco, item_desc, "52763", marca="FERTAK")
    site = _SiteFake([
        (lambda q: q.strip() == "52763",
         [_card("ABRACADEIRA NYLON FERTAK 100 PRETO", "FERTAK", 5.2, "52763")]),
        (lambda q: True, []),
    ])
    forn = _fornecedor(monkeypatch, site)
    lote = _buscar([_item(item_desc)], forn)
    assert lote.resultados[0], "deve achar pelo aprendizado"
    assert lote.resultados[0][0].codigo_fornecedor == "52763"
    assert lote.resultados[0][0].veio_por_aprendizado is True
    assert "52763" in site.queries


def test_aprendizado_vence_o_cache_na_segunda_busca(banco, monkeypatch):
    """Bloco A (2026-06): item repetido DENTRO da janela de cache (2 dias) deve
    devolver a ESCOLHA APRENDIDA do operador — não o resultado cru do cache.

    Regressão da causa-raiz: o cache era consultado ANTES do aprendizado e dava
    `continue`, atropelando a retroalimentação. Agora o aprendizado vem primeiro
    e PULA o cache."""
    item_desc = "PARAFUSO SEXTAVADO 5/16 X 2 ZINCADO"
    site = _SiteFake([
        # busca por código 555 → o produto que o operador vai ENSINAR ser o certo
        (lambda q: q.strip() == "555",
         [_card("PARAFUSO SEXTAVADO 5/16X2 GALVANIZADO CISER", "CISER", 0.9, "555")]),
        # busca textual → um card 'cru' diferente (vai pro cache na 1ª rodada)
        (lambda q: True,
         [_card("PARAFUSO SEXTAVADO 5/16X2 COMUM", "GENERICA", 0.5, "111")]),
    ])
    forn = _fornecedor(monkeypatch, site)

    # 1ª busca, SEM aprendizado: textual acha o card cru "111" e CACHEIA.
    lote1 = _buscar([_item(item_desc)], forn)
    assert lote1.resultados[0][0].codigo_fornecedor == "111"
    assert lote1.cache_misses >= 1

    # Operador ENSINA: para este item, a escolha certa é o código 555 (CISER).
    _seed_aprendido(banco, item_desc, "555", marca="CISER")

    # 2ª busca, mesmo item, dentro da janela de cache → o APRENDIZADO vence.
    n_antes = len(site.queries)
    lote2 = _buscar([_item(item_desc)], forn)
    res = lote2.resultados[0]
    assert res, "2ª busca deve retornar a escolha aprendida"
    assert res[0].codigo_fornecedor == "555", "veio a escolha aprendida, não o cache"
    assert res[0].veio_por_aprendizado is True
    assert lote2.codigo_hits >= 1
    assert lote2.cache_hits == 0, "aprendizado pula o cache de propósito"
    assert "555" in site.queries[n_antes:], "consultou o site pelo código aprendido"


# ============ Fluxo: filtro de relevância + dimensão (precisão) ==========

def test_dimensao_errada_e_filtrada_no_pipeline(banco, monkeypatch):
    """Edital DIÂMETRO 100: o site devolve 25/100mm; só o 100mm sobrevive."""
    site = _SiteFake([(lambda q: True, [
        _card("TUBO PVC SOLDAVEL 25MM 6M", "NORDEPLAST", 12.0, "4079"),
        _card("TUBO PVC SOLDAVEL 100MM 6M", "PLASTUBOS", 90.0, "100A"),
    ])])
    forn = _fornecedor(monkeypatch, site)
    lote = _buscar([_item("TUBO HIDRAULICO MATERIAL PVC RIGIDO DIAMETRO 100 "
                          "SOLDAVEL COMPRIMENTO 6M")], forn)
    descs = [o.descricao_encontrada for o in lote.resultados[0]]
    assert any("100MM" in d for d in descs)
    assert not any("25MM" in d for d in descs)


def test_conhecido_de_categoria_errada_nao_aparece(banco, monkeypatch):
    """Fix 2026-06: produto cujo CÓDIGO está no catálogo mas é de categoria
    errada NÃO entra só por ser 'conhecido' — a relevância vale para todos."""
    # Catálogo tem mangueira de gás e o adesivo certo (ambos já comprados).
    _seed_produto(banco, "mangueira gas glp", codigo_almeida="10175")
    _seed_produto(banco, "adesivo cola pvc 75g", codigo_almeida="26372")
    site = _SiteFake([(lambda q: True, [
        _card("MANGUEIRA GAS GLP PLASBOHN 4.2 X 100CM", "PLASBOHN", 6.11, "10175"),
        _card("ADESIVO POLYTUBES COLA PVC 75GR", "POLYTUBES", 4.36, "26372"),
    ])])
    forn = _fornecedor(monkeypatch, site)
    lote = _buscar([_item("ADESIVO PLASTICO PARA TUBOS E CONEXOES DE PVC "
                          "RIGIDO BISNAGA 75G COMPOSICAO RESINA DE PVC")], forn)
    descs = [o.descricao_encontrada for o in lote.resultados[0]]
    assert any("POLYTUBES" in d for d in descs), "adesivo certo sobrevive"
    assert not any("MANGUEIRA" in d for d in descs), "gás (irrelevante) some"


# ============== Fluxo: NCM → markup por ST → coluna na planilha ==========

def test_ncm_tabela_classifica_st_hierarquico():
    """Consulta na tabela NCM (Anexo 05) por 8/6/4 dígitos."""
    assert tributacao.eh_st("39174090") is True      # 3917 (conexões PVC)
    assert tributacao.eh_st("3917.40.90") is True     # com pontos
    assert tributacao.eh_st("99999999") is False
    assert tributacao.eh_st(None) is False


def test_markup_e_coluna_st_na_planilha_exportada(banco, tmp_path):
    """E2E até o .xlsx: item com NCM de ST sai markup 1,30 e coluna 'Sim';
    normal sai 1,45 e 'Não'."""
    itens = [_item("CONEXAO PVC", n=1), _item("CABO DE ACO", n=2)]
    selecoes = {
        0: SelecaoFornecedor(preco=10.0, codigo_fornecedor="A1", marca="TIGRE",
                             ncm="39174090"),                  # ST
        1: SelecaoFornecedor(preco=20.0, codigo_fornecedor="B2", marca="X",
                             ncm="73121090"),                  # normal
    }
    ncms = {off: s.ncm for off, s in selecoes.items()}
    markups, resumo, _ = tributacao.classificar_markups(ncms, 1.30, 1.45, {})
    com_st = tributacao.offsets_com_st(markups, 1.30)
    destino = tmp_path / "matriz.xlsx"
    exportar(itens, selecoes=selecoes, markups=markups, itens_com_st=com_st,
             destino=destino)

    ws = load_workbook(destino).active
    assert ws["G5"].value == "=S5*1.3"      # ST
    assert ws["G6"].value == "=S6*1.45"     # normal
    assert ws["AC5"].value == "Sim"
    assert ws["AC6"].value == "Não"


def test_override_obsoleto_nao_vence_ncm_no_pipeline():
    """Regressão 2026-06: override 'Normal' criado quando o item era sem-NCM
    não pode vencer a classificação depois que o NCM (de ST) é identificado."""
    ncms = {0: "39172300", 1: "39174090"}
    markups, resumo, _ = tributacao.classificar_markups(
        ncms, 1.30, 1.45, overrides={0: 1.45, 1: 1.45})   # overrides obsoletos
    assert markups == {0: 1.30, 1: 1.30}
    assert tributacao.offsets_com_st(markups, 1.30) == {0, 1}


# ===================== Fluxo COMPLETO (como o usuário) ===================

def test_jornada_completa_busca_selecao_planilha(banco, monkeypatch, tmp_path):
    """Itens → busca (Camada 0 por código + textual) → seleção do melhor →
    planilha MATRIZ com preço, código, marca e markup por ST."""
    _seed_produto(banco, "tubo pvc soldavel 75mm", codigo_almeida="8811",
                  marca="TIGRE", ncm="39172100")
    site = _SiteFake([
        (lambda q: q.strip() == "8811",
         [_card("TUBO PVC SOLDAVEL 75MM 6M", "TIGRE", 92.0, "8811")]),
        (lambda q: True, [
            _card("TORNEIRA JARDIM PLASTICA 1/2", "FORTLEV", 7.9, "120"),
        ]),
    ])
    forn = _fornecedor(monkeypatch, site)
    itens = [_item("TUBO PVC SOLDAVEL 75MM", n=1, qtd=30),
             _item("TORNEIRA JARDIM PLASTICA 1/2", n=2, qtd=10)]
    lote = _buscar(itens, forn)

    # Seleciona o melhor de cada (o que o operador faria com o default).
    selecoes = {}
    for idx, ops in lote.resultados.items():
        if ops:
            o = ops[0]
            selecoes[idx] = SelecaoFornecedor(preco=o.preco,
                                              codigo_fornecedor=o.codigo_fornecedor,
                                              marca=o.marca,
                                              ncm="39172100" if idx == 0 else None)
    assert 0 in selecoes and selecoes[0].codigo_fornecedor == "8811"

    ncms = {off: s.ncm for off, s in selecoes.items()}
    markups, _, _ = tributacao.classificar_markups(ncms, 1.30, 1.45, {})
    com_st = tributacao.offsets_com_st(markups, 1.30)
    destino = tmp_path / "pregao.xlsx"
    exportar(itens, selecoes=selecoes, markups=markups, itens_com_st=com_st,
             destino=destino)

    ws = load_workbook(destino).active
    assert ws["L5"].value == 92.0           # preço do tubo
    assert ws["T5"].value == "8811"         # código fornecedor
    assert ws["G5"].value == "=S5*1.3"      # tubo PVC = ST
    assert ws["AC5"].value == "Sim"


# ===================== Validação de fumaça (áreas confiáveis) ============

def test_smoke_extracao_pdf_sintetico():
    """Validação leve (extração já validada em campo): o PDF sintético extrai
    itens > 0 e com descrição."""
    from pathlib import Path
    from pregoes.extrator import extrair_pdf
    pdf = Path("data/editais/araruna_teste_sintetico.pdf")
    if not pdf.exists():
        pytest.skip("PDF sintético ausente")
    res = extrair_pdf(pdf)
    assert res.total_itens > 0
    assert all(it.descricao for it in res.itens)


# ============ Correções da passada adversarial (2026-06) =================

def test_adv_gramas_nao_viram_quilos_1000x():
    """#1 CRÍTICO: 45g é 0.045kg, não 45kg — milhar pt-BR não pode disparar
    sobre o resultado já dividido. 45g e 45kg DEVEM divergir."""
    from pregoes.normalizador import extrair_dimensoes, _algum_eixo_diverge
    assert extrair_dimensoes("CURVA 45G") == {("kg", "0.045")}
    assert extrair_dimensoes("FRASCO 45ML") == {("l", "0.045")}
    assert extrair_dimensoes("ADESIVO 1000ML") == {("l", "1")}      # conversão certa
    assert extrair_dimensoes("CAIXA 1.000 LT") == {("l", "1000")}   # milhar real intacto
    assert _algum_eixo_diverge(extrair_dimensoes("ELETRODO 45G"),
                               extrair_dimensoes("ELETRODO 45KG"))


def test_adv_mm2_nao_captura_denominador_de_fracao():
    """#6: 'CABO 1/2 MM2' não cria eixo mm2 fantasma."""
    from pregoes.normalizador import extrair_dimensoes
    assert extrair_dimensoes("CABO 1/2 MM2") == {("pol", "1/2")}
    assert extrair_dimensoes("CABO 2.5MM2 750V") == {("mm2", "2.5"), ("v", "750")}


def test_adv_preco_nan_inf_rejeitado_no_parser():
    """#4: data-preco NaN/Infinity furava o guard '<= 0' e contaminava a matriz."""
    from pregoes.scraper import _parse_card_attributes
    base = {"data-codigo-produto": "1", "data-nome": "X", "data-marca": "M",
            "url": "u"}
    assert _parse_card_attributes({**base, "data-preco": "NaN"}, "exato") is None
    assert _parse_card_attributes({**base, "data-preco": "Infinity"}, "exato") is None
    ok = _parse_card_attributes({**base, "data-preco": "9,90"}, "exato")
    assert ok is not None and ok.preco == 9.90


def test_adv_caractere_de_controle_nao_derruba_export(tmp_path):
    """#3: form-feed do pdfplumber numa descrição não pode derrubar a planilha."""
    itens = [_item("CANO PVC\x0c50MM", n=1), _item("TORNEIRA 1/2", n=2)]
    destino = tmp_path / "m.xlsx"
    exportar(itens, destino=destino)            # não pode levantar
    ws = load_workbook(destino).active
    assert "CANO PVC" in (ws["C5"].value or "")  # gravou, sem o char ilegal
    assert ws["C6"].value                        # o 2º item não se perdeu


def test_adv_quantidade_nan_inf_nao_some_em_silencio(tmp_path):
    """#5: nan/inf em quantidade/valor viram ausente (0/vazio), não corrompem."""
    itens = [_item("PRODUTO X", n=1, qtd=float("nan"), valor=float("inf"))]
    destino = tmp_path / "m.xlsx"
    exportar(itens, destino=destino)
    ws = load_workbook(destino).active
    assert ws["E5"].value == 0          # nan → 0 (não célula vazia silenciosa)
    assert ws["F5"].value is None       # inf → ausente


def test_adv_item_descricao_none_nao_derruba_lote(banco, monkeypatch):
    """#7: item com descricao=None + callback de progresso não pode derrubar
    o lote inteiro."""
    site = _SiteFake([(lambda q: True, [])])
    forn = _fornecedor(monkeypatch, site)
    itens = [ItemExtraido(item_numero=1, descricao=None, unidade="un", quantidade=1),
             _item("TORNEIRA 1/2", n=2)]
    lote = _buscar(itens, forn, progresso_callback=lambda f, t, d: None)
    assert 1 in lote.resultados        # o lote não quebrou; o 2º item foi processado


# ============ Fluxo: busca manual AO VIVO no site (Bloco C) ==============

def _fake_forn_site(cards, espera_query=None):
    """Fábrica de scraper falso para `buscar_no_site`: context manager com
    `_page`, `_rate_limiter.acquire` e `_buscar_query` — sem rede/navegador."""
    class _RL:
        def acquire(self):
            return None

    class _FakeForn:
        def __init__(self, email, senha):
            self._page = object()
            self._rate_limiter = _RL()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def _buscar_query(self, page, query, conf):
            if espera_query is not None:
                assert query == espera_query, query
            return [ResultadoBusca(d, m, p, c, conf, "almeida")
                    for (d, m, p, c) in cards]
    return _FakeForn


def test_busca_no_site_manual_traz_cards_crus_sem_filtro(monkeypatch):
    """A busca manual no site devolve os cards CRUS (sem filtro de relevância)
    para o operador julgar — é a mitigação para casos onde o filtro automático
    erra (ex.: alicate)."""
    import pregoes.scraper as scraper
    cards = [("ALICATE PRESSAO 10POL MORCEGO", "MORCEGO", 35.0, "A1"),
             ("ALICATE PRESSAO 8POL CR-V", "CR-V", 28.0, "A2")]
    monkeypatch.setattr(scraper, "AlmeidaDistribuidor",
                        _fake_forn_site(cards, espera_query="alicate de pressao"))
    monkeypatch.setattr(scraper.credenciais, "carregar",
                        lambda f: {"email": "x", "senha": "y"})

    achados = scraper.buscar_no_site("almeida", "  alicate de pressao  ")
    assert [a.codigo_fornecedor for a in achados] == ["A1", "A2"]
    assert achados[0].preco == 35.0


def test_busca_no_site_sem_credencial_levanta(monkeypatch):
    """Sem credencial configurada → erro claro (config/transitório), não silêncio."""
    import pregoes.scraper as scraper
    monkeypatch.setattr(scraper.credenciais, "carregar", lambda f: None)
    with pytest.raises(RuntimeError):
        scraper.buscar_no_site("almeida", "alicate")


def test_busca_no_site_termo_vazio_nao_toca_no_site(monkeypatch):
    """Termo vazio → retorna [] sem instanciar o scraper (sem rede)."""
    import pregoes.scraper as scraper
    chamou = {"n": 0}

    class _Boom:
        def __init__(self, *a):
            chamou["n"] += 1

    monkeypatch.setattr(scraper, "AlmeidaDistribuidor", _Boom)
    monkeypatch.setattr(scraper.credenciais, "carregar",
                        lambda f: {"email": "x", "senha": "y"})
    assert scraper.buscar_no_site("almeida", "   ") == []
    assert chamou["n"] == 0
