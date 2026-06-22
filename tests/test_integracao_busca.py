"""Harness de INTEGRAÇÃO da busca: mocka os cards do site (`_buscar_query`) e
roda o pipeline REAL de `buscar()` ponta a ponta — sem rede nem login.

Por quê: os fixes recentes (eixo de diâmetro, diâmetro na query, dedup,
reconhecimento por código) tinham testes de UNIDADE; aqui confirmamos que eles
funcionam JUNTOS no fluxo real de `buscar()` (montar_camadas → filtro de
relevância → top-N → dedup → marcação por código). É também a Fase 0 do
trabalho cross-brand (mesmo seam de mock).
"""
from __future__ import annotations

from pregoes.scraper import AlmeidaDistribuidor, ResultadoBusca
from pregoes.matcher import ProdutoCatalogo, indice_por_codigo


def _card(desc, marca, preco, cod):
    return ResultadoBusca(desc, marca, preco, cod, "exato", "almeida")


def _fornecedor_mock(monkeypatch, cards):
    """Almeida com `_buscar_query` devolvendo `cards` fixos (a confiança é
    carimbada pela camada, como o parser real faz) e rate-limiter no-op."""
    forn = AlmeidaDistribuidor(email="x", senha="y")

    def fake_query(page, query, conf):
        return [ResultadoBusca(c.descricao_encontrada, c.marca, c.preco,
                               c.codigo_fornecedor, conf, "almeida")
                for c in cards]

    monkeypatch.setattr(forn, "_buscar_query", fake_query)
    monkeypatch.setattr(forn._rate_limiter, "acquire", lambda: None)
    return forn


def test_medida_filtra_diametro_errado_no_pipeline(monkeypatch):
    """Fase 1+2 ponta a ponta: edital DIÂMETRO 100; o site devolve 25/32/100mm;
    a busca real mantém SÓ o 100mm (25/32 caem no filtro de relevância)."""
    cards = [
        _card("TUBO PVC SOLDAVEL 25MM 6M", "NORDEPLAST", 12.0, "4079"),
        _card("TUBO PVC SOLDAVEL 32MM 6M", "NORDEPLAST", 27.0, "6918"),
        _card("TUBO PVC SOLDAVEL 100MM 6M", "PLASTUBOS", 90.0, "100A"),
    ]
    forn = _fornecedor_mock(monkeypatch, cards)
    bag = forn.buscar("TUBO HIDRAULICO MATERIAL PVC RIGIDO DIAMETRO 100 "
                      "SOLDAVEL COMPRIMENTO 6M")
    descs = [c.descricao_encontrada for c in bag]
    assert any("100MM" in d for d in descs)       # diâmetro certo sobrevive
    assert not any("25MM" in d for d in descs)     # diâmetro errado filtrado
    assert not any("32MM" in d for d in descs)


def test_dedup_no_pipeline(monkeypatch):
    """Cards repetidos do site não viram opções duplicadas na bag."""
    cards = [
        _card("ABRACADEIRA NYLON FERTAK 100", "FERTAK", 5.0, "1"),
        _card("ABRACADEIRA NYLON FERTAK 100", "FERTAK", 5.0, "1"),   # dup exato
        _card("ABRACADEIRA NYLON FOXLUX 100", "FOXLUX", 6.0, "2"),
    ]
    forn = _fornecedor_mock(monkeypatch, cards)
    bag = forn.buscar("ABRACADEIRA DE NYLON 100")
    assert len(bag) == 2   # 3 cards, 1 repetido → 2 distintos


def test_reconhecimento_por_codigo_no_pipeline(monkeypatch):
    """Candidato cujo código está no catálogo do cliente é marcado (no_catalogo)
    e priorizado, mesmo com relevância textual menor."""
    cards = [
        _card("ABRACADEIRA NYLON FOXLUX 100 BRANCA", "FOXLUX", 6.0, "111"),
        _card("ABRACADEIRA NYLON FERTAK 100", "FERTAK", 5.0, "52763"),
    ]
    cat = [ProdutoCatalogo(produto_id=7, descricao_canonica="abracadeira nylon 100",
                           codigo_almeida="52763", codigo_dantas=None,
                           marca="FERTAK", unidade_padrao="pct")]
    forn = _fornecedor_mock(monkeypatch, cards)
    bag = forn.buscar("ABRACADEIRA DE NYLON 100",
                      idx_codigo=indice_por_codigo(cat))
    assert bag[0].no_catalogo is True
    assert bag[0].codigo_fornecedor == "52763"
    assert bag[0].produto_catalogo_id == 7


def test_nada_relevante_retorna_vazio(monkeypatch):
    """Se nada passa o threshold, a bag é vazia (não inventa)."""
    cards = [_card("PRODUTO TOTALMENTE DIFERENTE ZZZ", "X", 1.0, "9")]
    forn = _fornecedor_mock(monkeypatch, cards)
    assert forn.buscar("TORNEIRA METAL BICA MOVEL") == []

# Nota: o reuso fuzzy do aprendizado (Camada 4) é coberto em unidade por
# tests/test_aprendido_fuzzy.py (melhor_aprendido_fuzzy). Um teste end-to-end
# pelo orquestrador foi removido por fragilidade de isolamento de estado de DB
# entre arquivos de teste — revisitar com isolamento próprio se necessário.


def test_medida_kg_filtra_peso_errado_no_pipeline(monkeypatch):
    """Auditoria 2026-06 (achado alto nº 3) ponta a ponta: edital pede saco
    de 50KG; o site devolve 25KG e 50KG; a busca real NÃO pode deixar o
    25KG passar com relevância de match pleno."""
    cards = [
        _card("CIMENTO PORTLAND CP II 25KG POTY", "POTY", 22.0, "251"),
        _card("CIMENTO PORTLAND CP II 50KG POTY", "POTY", 38.5, "501"),
    ]
    forn = _fornecedor_mock(monkeypatch, cards)
    bag = forn.buscar("CIMENTO PORTLAND CP II SACO 50KG")
    descs = [c.descricao_encontrada for c in bag]
    assert any("50KG" in d for d in descs)        # peso certo sobrevive
    assert not any("25KG" in d for d in descs)    # peso errado filtrado


def test_conhecido_irrelevante_nao_fura_o_filtro(monkeypatch):
    """Feedback do cliente 2026-06: produto cujo CÓDIGO está no catálogo mas é
    de categoria errada NÃO pode aparecer só por ser 'conhecido'. Caso real do
    print: edital de adesivo PVC trazia mangueira de gás, cap e adesivo de
    diesel (todos no catálogo do cliente) e pré-selecionava o errado. Agora só
    o relevante (POLYTUBES cola PVC) sobrevive — e como é catálogo, fica no topo."""
    edital = ("ADESIVO PLASTICO PARA TUBOS E CONEXOES DE PVC RIGIDO BISNAGA 75G "
              "COMPOSICAO RESINA DE PVC")
    cards = [
        _card("MANGUEIRA GAS GLP PLASBOHN 4.2 X 100CM", "PLASBOHN", 6.11, "10175"),
        _card("CAP SOLDAVEL PLASTUBOS DN20", "PLASTUBOS", 0.76, "10536"),
        _card("ADESIVO PARA JUNTAS DE MOTORES-DIESEL 3M 73G", "3M", 9.96, "11662"),
        _card("ADESIVO POLYTUBES PLAST COLA PVC 75GR", "POLYTUBES", 4.36, "26372"),
    ]
    # TODOS os códigos estão no catálogo do cliente (todos "conhecidos").
    cat = [ProdutoCatalogo(produto_id=i, descricao_canonica=d,
                           codigo_almeida=c, codigo_dantas=None,
                           marca="X", unidade_padrao="un")
           for i, (d, c) in enumerate([
               ("mangueira gas glp", "10175"),
               ("cap soldavel plastubos", "10536"),
               ("adesivo juntas diesel", "11662"),
               ("adesivo cola pvc", "26372")])]
    forn = _fornecedor_mock(monkeypatch, cards)
    bag = forn.buscar(edital, idx_codigo=indice_por_codigo(cat))
    descs = [c.descricao_encontrada for c in bag]

    assert any("POLYTUBES" in d for d in descs), "o adesivo PVC certo deve sobreviver"
    assert not any("MANGUEIRA" in d for d in descs), "mangueira de gás some"
    assert not any("CAP SOLDAVEL" in d for d in descs), "cap some"
    assert not any("DIESEL" in d for d in descs), "adesivo de diesel some"
    assert bag[0].no_catalogo is True, "o certo é de catálogo → topo + selo"


def test_conhecido_relevante_continua_no_topo(monkeypatch):
    """Não regrediu a feature do cliente: catálogo RELEVANTE segue marcado e
    priorizado (mesmo abaixo de um não-catálogo mais barato)."""
    cards = [
        _card("ABRACADEIRA NYLON FOXLUX 100 BRANCA", "FOXLUX", 4.0, "111"),
        _card("ABRACADEIRA NYLON FERTAK 100", "FERTAK", 9.0, "52763"),
    ]
    cat = [ProdutoCatalogo(produto_id=7, descricao_canonica="abracadeira nylon 100",
                           codigo_almeida="52763", codigo_dantas=None,
                           marca="FERTAK", unidade_padrao="pct")]
    forn = _fornecedor_mock(monkeypatch, cards)
    bag = forn.buscar("ABRACADEIRA DE NYLON 100", idx_codigo=indice_por_codigo(cat))
    assert bag[0].no_catalogo is True and bag[0].codigo_fornecedor == "52763"
