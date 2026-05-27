"""
Testes do primeiro lote da Fase 2 (normalizador, cache de preços, contrato
do scraper). Os scrapers reais ainda não existem — os testes aqui cobrem
só o que já roda sem rede.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from pregoes.db import init_db, get_conn, preco_em_cache, gravar_preco
from pregoes.normalizador import normalizar_descricao, keywords, relevancia
from pregoes.scraper import (
    AlmeidaDistribuidor,
    DantasDistribuidora,
    Fornecedor,
    ResultadoBusca,
    _parse_card_attributes,
    buscar_precos_para_itens,
)
from pregoes import credenciais
from pregoes.extrator import ItemExtraido


# === Normalizador ===

@pytest.mark.parametrize("entrada,esperado", [
    ("1) ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND",
     "abracadeira de nylon 100 x 2.5 pct c/100und"),
    # Vírgula decimal entre dígitos vira ponto; vírgulas separadoras viram espaço
    ("14) ACO CA–50, ½, VERGALHAO C/ 12M SUPERFÍCIE LISA",
     "aco ca-50 1/2 vergalhao c/ 12m superficie lisa"),
    ("10) ACO CA-50, 12,5 MM, VERGALHAO",
     "aco ca-50 12.5 mm vergalhao"),
    ("VÁLVULA  DE  RETENÇÃO(SUCÇÃO)\n32MM",
     "valvula de retencao(succao) 32mm"),
    ("  ÁCIDO   MURIÁTICO  ", "acido muriatico"),
    ("", ""),
    (None, ""),
])
def test_normalizar_descricao(entrada, esperado):
    assert normalizar_descricao(entrada) == esperado


def test_keywords_descarta_ruido():
    """Stopwords pt-BR ('de', 'para'), unidades sozinhas ('mm', 'cm'),
    números puros ('100', '12.5', '2,5') e tokens curtos saem.
    Sobram os tokens distintivos de busca."""
    kws = keywords("1) ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND")
    assert "abracadeira" in kws
    assert "nylon" in kws
    assert "de" not in kws
    assert "100" not in kws
    assert "2.5" not in kws

    # Caso vergalhão: stopwords + unidades soltas + decimais saem
    kws2 = keywords("10) ACO CA-50, 12,5 MM, VERGALHAO C/ 12M SUPERFÍCIE NERVURADA")
    assert "aco" in kws2
    assert "ca-50" in kws2
    assert "vergalhao" in kws2
    assert "nervurada" in kws2
    assert "mm" not in kws2  # unidade solta
    assert "12.5" not in kws2  # decimal puro


def test_keywords_vazio():
    assert keywords("") == []
    assert keywords(None) == []


# === Relevância ===

def test_relevancia_match_alto_quando_palavras_chave_coincidem():
    """Caso real: ABRAÇADEIRA NYLON do edital casa com card da Almeida."""
    score = relevancia(
        "1) ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND",
        "ABRAÇADEIRA DE NYLON FOXLUX BRANCA 2.5X10CM C/100",
    )
    assert score >= 0.5  # 'abracadeira', 'nylon' coincidem


def test_relevancia_zero_quando_categorias_diferentes():
    """Caso visto na UI: vergalhão do edital vs corrente soldada do site.
    Nada em comum nas keywords — score baixo, deve ser descartado."""
    score = relevancia(
        "10) ACO CA-50, 12,5 MM, VERGALHAO C/ 12M SUPERFÍCIE NERVURADA",
        "CORRENTE SOLDADA SAO RAPHAEL 6.5MM-1/4 SACO C/12,5KG",
    )
    assert score < 0.5  # nenhuma das kws distintivas (aco, ca-50, vergalhao, nervurada) bate


def test_relevancia_lida_com_vazio():
    assert relevancia("", "qualquer coisa") == 0.0
    assert relevancia("qualquer coisa", "") == 0.0
    assert relevancia(None, None) == 0.0


# === Cache de preços ===

def _setup_produto(conn) -> int:
    """Cria um produto fake e retorna o id."""
    cursor = conn.execute(
        "INSERT INTO produtos (descricao_canonica, marca) VALUES (?, ?)",
        ("ABRACADEIRA NYLON 100", "Tramontina"),
    )
    return cursor.lastrowid


def test_cache_miss_quando_vazio(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    with get_conn(db) as conn:
        produto_id = _setup_produto(conn)
        assert preco_em_cache(conn, produto_id, "almeida") is None


def test_cache_hit_dentro_do_ttl(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    with get_conn(db) as conn:
        produto_id = _setup_produto(conn)
        gravar_preco(conn, produto_id, "almeida", preco=12.34, unidade="un")
        row = preco_em_cache(conn, produto_id, "almeida")
        assert row is not None
        assert row["preco"] == 12.34
        assert row["fornecedor"] == "almeida"


def test_cache_miss_apos_ttl_expirar(tmp_path):
    """Insere um registro com consultado_em forçado para 3 dias atrás e
    confirma que preco_em_cache não o retorna (TTL padrão = 2 dias)."""
    db = tmp_path / "t.db"
    init_db(db)
    tres_dias_atras = (datetime.utcnow() - timedelta(days=3)).isoformat(sep=" ")
    with get_conn(db) as conn:
        produto_id = _setup_produto(conn)
        conn.execute(
            """INSERT INTO precos_historico
               (produto_id, fornecedor, preco, consultado_em)
               VALUES (?, ?, ?, ?)""",
            (produto_id, "almeida", 99.99, tres_dias_atras),
        )
        assert preco_em_cache(conn, produto_id, "almeida") is None


def test_cache_separado_por_fornecedor(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    with get_conn(db) as conn:
        produto_id = _setup_produto(conn)
        gravar_preco(conn, produto_id, "almeida", preco=10.0)
        # Mesmo produto, fornecedor diferente: deve ser cache miss
        assert preco_em_cache(conn, produto_id, "dantas") is None
        assert preco_em_cache(conn, produto_id, "almeida") is not None


# === Contrato do scraper ===

def test_classes_concretas_cumprem_protocolo():
    """Garantir que Almeida e Dantas têm `nome` e `buscar` — se isso quebrar
    o resto do pipeline já quebra junto."""
    almeida = AlmeidaDistribuidor(email="x@y", senha="z")
    dantas = DantasDistribuidora()
    assert almeida.nome == "almeida"
    assert dantas.nome == "dantas"
    # Tipo estrutural: ambas devem satisfazer o Protocol em runtime,
    # via duck typing (Python não checa Protocol em runtime sem @runtime_checkable).
    assert hasattr(almeida, "buscar")
    assert hasattr(dantas, "buscar")


def test_dantas_esqueleto_levanta_not_implemented():
    """Dantas ainda é esqueleto e deve falhar de forma clara."""
    with pytest.raises(NotImplementedError, match="implementado"):
        DantasDistribuidora().buscar("ABRAÇADEIRA")


def test_fornecedor_mockado_funciona_via_protocol():
    """Um fornecedor falso que cumpre o Protocol deve passar pelo type system
    estrutural — é como vamos mockar nos testes da implementação real."""

    class FornecedorFake:
        nome = "fake"

        def buscar(self, descricao: str) -> list[ResultadoBusca]:
            return [
                ResultadoBusca(
                    descricao_encontrada=descricao,
                    marca="MARCA X",
                    preco=42.0,
                    codigo_fornecedor="ABC123",
                    confianca="exato",
                    fornecedor="fake",
                )
            ]

    fake: Fornecedor = FornecedorFake()  # checagem estática vale para mypy
    resultados = fake.buscar("ABRAÇADEIRA")
    assert len(resultados) == 1
    assert resultados[0].marca == "MARCA X"
    assert resultados[0].confianca == "exato"


# === Parse de card data-* (Almeida) — função pura ===

def test_parse_card_attributes_caso_real():
    """Atributos vistos na inspeção 2026-05-15 (.product-card__info)."""
    attrs = {
        "data-codigo-produto": "11656",
        "data-nome": "ABRAÇADEIRA DE NYLON FOXLUX BRANCA 2.5X10CM C/100 ",
        "data-preco": "3.18",
        "data-marca": "FOXLUX",
    }
    r = _parse_card_attributes(attrs, confianca="exato")
    assert r is not None
    assert r.codigo_fornecedor == "11656"
    assert r.descricao_encontrada == "ABRAÇADEIRA DE NYLON FOXLUX BRANCA 2.5X10CM C/100"
    assert r.marca == "FOXLUX"
    assert r.preco == 3.18
    assert r.confianca == "exato"
    assert r.fornecedor == "almeida"


def test_parse_card_attributes_preco_com_virgula():
    """Algum card pode vir com vírgula brasileira em vez de ponto — o parser aceita."""
    r = _parse_card_attributes(
        {"data-codigo-produto": "1", "data-nome": "X",
         "data-preco": "12,34", "data-marca": "Y"},
        confianca="variacao",
    )
    assert r is not None and r.preco == 12.34


@pytest.mark.parametrize("attrs_faltando", [
    {"data-nome": "X", "data-preco": "1.0", "data-marca": "Y"},               # sem código
    {"data-codigo-produto": "1", "data-preco": "1.0", "data-marca": "Y"},     # sem nome
    {"data-codigo-produto": "1", "data-nome": "X", "data-marca": "Y"},        # sem preço
    {"data-codigo-produto": "1", "data-nome": "X", "data-preco": "abc"},      # preço inválido
    {"data-codigo-produto": "1", "data-nome": "X", "data-preco": "0"},        # preço zero
    {"data-codigo-produto": "1", "data-nome": "X", "data-preco": "-5"},       # preço negativo
    {},                                                                       # tudo vazio
])
def test_parse_card_attributes_descarta_invalidos(attrs_faltando):
    assert _parse_card_attributes(attrs_faltando, "exato") is None


def test_parse_card_attributes_marca_vazia_ok():
    """Marca em branco não é motivo para descartar — o operador escolhe na UI."""
    r = _parse_card_attributes(
        {"data-codigo-produto": "9", "data-nome": "X",
         "data-preco": "5.0", "data-marca": ""},
        confianca="exato",
    )
    assert r is not None
    assert r.marca == ""


# === Módulo credenciais ===

def test_credenciais_round_trip(tmp_path, monkeypatch):
    """Salvar + carregar volta o que foi salvo."""
    fake_path = tmp_path / "credenciais.json"
    monkeypatch.setattr(credenciais, "CAMINHO", fake_path)

    assert credenciais.carregar("almeida") is None  # vazio antes
    credenciais.salvar("almeida", "user@x.com", "senha123")
    cred = credenciais.carregar("almeida")
    assert cred == {"email": "user@x.com", "senha": "senha123"}


def test_credenciais_preserva_outros_fornecedores(tmp_path, monkeypatch):
    """Salvar Dantas não pode apagar Almeida (e vice-versa)."""
    fake_path = tmp_path / "credenciais.json"
    monkeypatch.setattr(credenciais, "CAMINHO", fake_path)

    credenciais.salvar("almeida", "a@x.com", "s1")
    credenciais.salvar("dantas", "d@y.com", "s2")
    assert credenciais.carregar("almeida")["email"] == "a@x.com"
    assert credenciais.carregar("dantas")["email"] == "d@y.com"


def test_credenciais_remover(tmp_path, monkeypatch):
    fake_path = tmp_path / "credenciais.json"
    monkeypatch.setattr(credenciais, "CAMINHO", fake_path)
    credenciais.salvar("almeida", "a@x.com", "s1")
    credenciais.remover("almeida")
    assert credenciais.carregar("almeida") is None


def test_credenciais_arquivo_corrompido_retorna_none(tmp_path, monkeypatch):
    """Se o JSON for inválido (cliente editou na mão e estragou), o sistema
    deve tratar como 'sem credencial' em vez de explodir."""
    fake_path = tmp_path / "credenciais.json"
    fake_path.write_text("isso não é json {{{")
    monkeypatch.setattr(credenciais, "CAMINHO", fake_path)
    assert credenciais.carregar("almeida") is None


# === Orquestrador buscar_precos_para_itens ===

class _FornecedorFake:
    """Fornecedor mockado: retorna resultados conforme dict pré-configurado."""
    def __init__(self, nome: str, mapa: dict[str, list[ResultadoBusca]],
                 raise_para: set[str] | None = None):
        self.nome = nome
        self._mapa = mapa
        self._raise_para = raise_para or set()
        self.chamadas = []

    def buscar(self, descricao: str) -> list[ResultadoBusca]:
        self.chamadas.append(descricao)
        if descricao in self._raise_para:
            raise RuntimeError(f"falha simulada em {descricao!r}")
        return self._mapa.get(descricao, [])


def _r(descricao, marca, preco, codigo="X", confianca="exato", fornecedor="fake"):
    return ResultadoBusca(descricao, marca, preco, codigo, confianca, fornecedor)


def test_buscar_precos_chama_callback_em_cada_item():
    itens = [ItemExtraido(descricao=d) for d in ("AAA", "BBB", "CCC")]
    forn = _FornecedorFake("fake", mapa={"AAA": [_r("AAA", "MX", 1.0)]})
    chamadas = []

    def cb(atual, total, msg):
        chamadas.append((atual, total, msg))

    res = buscar_precos_para_itens(itens, [forn], progresso_callback=cb)
    assert chamadas == [(1, 3, "AAA"), (2, 3, "BBB"), (3, 3, "CCC")]
    assert len(res) == 3
    assert len(res[0]) == 1 and res[0][0].marca == "MX"
    assert res[1] == [] and res[2] == []


def test_buscar_precos_concatena_de_multiplos_fornecedores():
    itens = [ItemExtraido(descricao="ABC")]
    f1 = _FornecedorFake("almeida", {"ABC": [_r("ABC", "M1", 10.0)]})
    f2 = _FornecedorFake("dantas", {"ABC": [_r("ABC", "M2", 11.0)]})
    res = buscar_precos_para_itens(itens, [f1, f2])
    assert len(res[0]) == 2
    marcas = {r.marca for r in res[0]}
    assert marcas == {"M1", "M2"}


def test_buscar_precos_segue_apos_falha_isolada():
    """Se um fornecedor falha em UM item, o batch continua e o item fica vazio."""
    itens = [ItemExtraido(descricao=d) for d in ("OK1", "FAIL", "OK2")]
    forn = _FornecedorFake("fake",
                           mapa={"OK1": [_r("OK1", "M", 1.0)],
                                 "OK2": [_r("OK2", "M", 2.0)]},
                           raise_para={"FAIL"})
    res = buscar_precos_para_itens(itens, [forn])
    assert len(res[0]) == 1
    assert res[1] == []   # falha não derrubou o batch
    assert len(res[2]) == 1
    assert forn.chamadas == ["OK1", "FAIL", "OK2"]


def test_buscar_precos_lista_vazia_de_itens():
    forn = _FornecedorFake("fake", {})
    res = buscar_precos_para_itens([], [forn])
    assert res == {}
    assert forn.chamadas == []
