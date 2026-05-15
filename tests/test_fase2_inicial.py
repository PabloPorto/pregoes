"""
Testes do primeiro lote da Fase 2 (normalizador, cache de preços, contrato
do scraper). Os scrapers reais ainda não existem — os testes aqui cobrem
só o que já roda sem rede.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from pregoes.db import init_db, get_conn, preco_em_cache, gravar_preco
from pregoes.normalizador import normalizar_descricao, keywords
from pregoes.scraper import (
    AlmeidaDistribuidor,
    DantasDistribuidora,
    Fornecedor,
    ResultadoBusca,
)


# === Normalizador ===

@pytest.mark.parametrize("entrada,esperado", [
    ("1) ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND",
     "abracadeira de nylon 100 x 2.5 pct c/100und"),
    ("14) ACO CA–50, ½, VERGALHAO C/ 12M SUPERFÍCIE LISA",
     "aco ca-50, 1/2, vergalhao c/ 12m superficie lisa"),
    ("VÁLVULA  DE  RETENÇÃO(SUCÇÃO)\n32MM",
     "valvula de retencao(succao) 32mm"),
    ("  ÁCIDO   MURIÁTICO  ", "acido muriatico"),
    ("", ""),
    (None, ""),
])
def test_normalizar_descricao(entrada, esperado):
    assert normalizar_descricao(entrada) == esperado


def test_keywords_descarta_palavras_curtas_e_numeros():
    """Tokens com <3 chars ('de', 'x') e numéricos puros ('100', '2.5') saem
    porque ranqueiam mal como query — sobram os tokens distintivos."""
    kws = keywords("1) ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND")
    assert "abracadeira" in kws
    assert "nylon" in kws
    assert "de" not in kws
    assert "100" not in kws
    assert "2.5" not in kws


def test_keywords_vazio():
    assert keywords("") == []
    assert keywords(None) == []


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
    almeida = AlmeidaDistribuidor()
    dantas = DantasDistribuidora()
    assert almeida.nome == "almeida"
    assert dantas.nome == "dantas"
    # Tipo estrutural: ambas devem satisfazer o Protocol em runtime,
    # via duck typing (Python não checa Protocol em runtime sem @runtime_checkable).
    assert hasattr(almeida, "buscar")
    assert hasattr(dantas, "buscar")


def test_esqueleto_levanta_not_implemented():
    """Esqueleto deve falhar de forma clara, não silenciosa."""
    with pytest.raises(NotImplementedError, match="implementado"):
        AlmeidaDistribuidor().buscar("ABRAÇADEIRA")
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
