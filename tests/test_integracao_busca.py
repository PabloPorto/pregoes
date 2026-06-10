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


def test_aprendizado_fuzzy_orquestrador_reusa_codigo(tmp_path, monkeypatch):
    """Camada 4 plena ponta a ponta: um mapeamento aprendido de descrição
    PARECIDA faz o orquestrador ir DIRETO no código (buscar_por_codigo),
    pulando a busca textual — o sistema se retroalimenta."""
    from pregoes import db, config
    from pregoes.scraper import buscar_precos_para_itens
    from pregoes.extrator import ItemExtraido
    from pregoes.normalizador import normalizar_descricao
    dbp = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)
    db.init_db(dbp)
    with db.conectar(dbp) as conn:
        db.aprendido_gravar(conn, normalizar_descricao("VALVULA LAVATORIO REBOUCAS 7/8"),
                            "almeida", "46439", "REBOUCAS", 1.5)
        conn.commit()

    chamou = {"codigo": [], "texto": []}

    class FornFake:
        nome = "almeida"
        def buscar(self, descricao, _log=None, idx_codigo=None):
            chamou["texto"].append(descricao)
            return []
        def buscar_por_codigo(self, codigo):
            chamou["codigo"].append(codigo)
            return [ResultadoBusca("VALVULA LAVATORIO REBOUCAS 7/8", "REBOUCAS",
                                   1.5, codigo, "exato", "almeida")]

    itens = [ItemExtraido(item_numero=1,
                          descricao="VALVULA P/ LAVATORIO REBOUCAS 7/8")]
    lote = buscar_precos_para_itens(
        itens, [FornFake()], usar_cache=False, usar_catalogo=True,
        usar_canonico=False, usar_llm_judge=False)
    # Reusou o código aprendido via fuzzy (não caiu na busca textual)
    assert "46439" in chamou["codigo"]
    assert lote.resultados[0] and lote.resultados[0][0].codigo_fornecedor == "46439"
