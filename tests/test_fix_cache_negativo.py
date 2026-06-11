"""Regressão do achado alto nº 5 (auditoria 2026-06) — cache negativo.

Um logout/instabilidade no meio do lote fazia a busca devolver [] e o
orquestrador gravava "[]" em precos_cache_descricao por 2 dias: re-buscar
devolvia os mesmos vazios pelo cache, exatamente na hora do pregão.

Fix dos dois lados em buscar_precos_para_itens:
- escrita: resultado vazio NUNCA entra no cache;
- leitura: payload vazio já gravado (legado) é tratado como MISS.

Hermético: config monkeypatchado para tmp_path (banco isolado), fornecedor
fake em memória, camadas catálogo/canônico/judge desligadas. Sem rede.
"""
from __future__ import annotations

import pytest

from pregoes import config, db
from pregoes.extrator import ItemExtraido
from pregoes.normalizador import normalizar_descricao
from pregoes.scraper import ResultadoBusca, buscar_precos_para_itens


class FornecedorSequencia:
    """Fornecedor fake: devolve as respostas pré-programadas em ordem e
    conta as chamadas — para afirmar se a 2ª rodada veio do cache ou não."""

    nome = "almeida"

    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

    def buscar(self, descricao):
        self.chamadas += 1
        return list(self.respostas.pop(0)) if self.respostas else []


def _item(desc="TORNEIRA METAL CROMADA BICA MOVEL"):
    return ItemExtraido(item_numero=1, descricao=desc, unidade="UN",
                        quantidade=2, valor_referencia=10.0)


def _resultado(desc="TORNEIRA METAL CROMADA BICA MOVEL 1/2"):
    return ResultadoBusca(desc, "MARCA", 9.9, "123", "exato", "almeida")


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init_db(tmp_path / "t.db")
    return tmp_path


def _rodar(forn):
    return buscar_precos_para_itens(
        [_item()], [forn],
        usar_catalogo=False, usar_canonico=False, usar_llm_judge=False,
    )


def test_resultado_vazio_nao_entra_no_cache_e_rebusca_tenta_de_novo(ambiente):
    """1ª rodada falha ([]): nada vai pro cache. 2ª rodada consulta o
    fornecedor DE NOVO e encontra — o soluço do site não persiste."""
    forn = FornecedorSequencia([[], [_resultado()]])

    lote1 = _rodar(forn)
    assert lote1.resultados[0] == []
    assert forn.chamadas == 1

    lote2 = _rodar(forn)
    assert forn.chamadas == 2, "vazio não pode ter virado hit de cache"
    assert lote2.resultados[0], "2ª rodada deve trazer o resultado real"


def test_resultado_cheio_continua_cacheado(ambiente):
    """Comportamento preservado: quem ACHOU preço responde do cache na
    2ª rodada, sem nova consulta ao fornecedor."""
    forn = FornecedorSequencia([[_resultado()]])

    lote1 = _rodar(forn)
    assert lote1.resultados[0]
    assert forn.chamadas == 1

    lote2 = _rodar(forn)
    assert forn.chamadas == 1, "2ª rodada devia vir do cache"
    assert lote2.resultados[0]
    assert lote2.cache_hits >= 1


def test_payload_vazio_legado_no_cache_e_tratado_como_miss(ambiente, tmp_path):
    """Bancos que JÁ têm '[]' gravado (antes do fix): a leitura trata como
    miss e re-busca — o lixo legado não precisa de migração."""
    desc_norm = normalizar_descricao(_item().descricao)
    with db.conectar(tmp_path / "t.db") as conn:
        db.cache_gravar(conn, desc_norm, "almeida", "[]")
        conn.commit()

    forn = FornecedorSequencia([[_resultado()]])
    lote = _rodar(forn)
    assert forn.chamadas == 1, "payload vazio legado devia ser miss"
    assert lote.resultados[0]
