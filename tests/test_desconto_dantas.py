"""Desconto comercial da Dantas (2026-06-27): o custo trazido do site da Dantas
entra na coluna L com 17% de desconto; Almeida/catálogo entram cheios. O markup
das fórmulas incide sobre o custo JÁ descontado (margem preservada)."""
from __future__ import annotations

from openpyxl import load_workbook

from pregoes import config
from pregoes.exportador import (SelecaoFornecedor, _preco_com_desconto,
                                eh_dantas, exportar)
from pregoes.extrator import ItemExtraido


def _itens(n):
    return [ItemExtraido(descricao=f"item {i}", quantidade=1) for i in range(n)]


def _L5(tmp_path, sel):
    destino = tmp_path / "m.xlsx"
    exportar(_itens(1), selecoes={0: sel}, destino=destino)
    return load_workbook(destino).active["L5"].value


def test_dantas_aplica_17pct_na_coluna_L(tmp_path):
    sel = SelecaoFornecedor(preco=5.0, codigo_fornecedor="1", marca="A",
                            fornecedor="dantas")
    assert _L5(tmp_path, sel) == 4.15          # 5,00 − 17%


def test_almeida_entra_com_custo_cheio(tmp_path):
    sel = SelecaoFornecedor(preco=5.0, codigo_fornecedor="1", marca="A",
                            fornecedor="almeida")
    assert _L5(tmp_path, sel) == 5.0


def test_selecao_sem_fornecedor_nao_descontada(tmp_path):
    sel = SelecaoFornecedor(preco=5.0, codigo_fornecedor="1", marca="A")
    assert _L5(tmp_path, sel) == 5.0


def test_desconto_compoe_com_markup_st(tmp_path):
    # Item Dantas COM ST: custo descontado em L E markup 1,30 na fórmula G.
    sel = {0: SelecaoFornecedor(preco=10.0, codigo_fornecedor="1", marca="A",
                                ncm="39174090", fornecedor="dantas")}
    destino = tmp_path / "m.xlsx"
    exportar(_itens(1), selecoes=sel, destino=destino)
    ws = load_workbook(destino).active
    assert ws["L5"].value == 8.3               # 10 − 17%
    assert ws["G5"].value == "=S5*1.3"         # markup ST compõe com o desconto


def test_eh_dantas_normaliza_caixa_e_espaco():
    def mk(f):
        return SelecaoFornecedor(preco=1.0, codigo_fornecedor="1", marca="A",
                                 fornecedor=f)
    assert eh_dantas(mk("dantas"))
    assert eh_dantas(mk("  DANTAS  "))
    assert not eh_dantas(mk("almeida"))
    assert not eh_dantas(mk(None))


def test_preco_com_desconto_unit():
    base = dict(codigo_fornecedor="1", marca="A")
    assert _preco_com_desconto(
        SelecaoFornecedor(preco=5.0, fornecedor="dantas", **base)) == 4.15
    # preço 0/ausente passa intacto (célula vazia = sem preço)
    assert _preco_com_desconto(
        SelecaoFornecedor(preco=0.0, fornecedor="dantas", **base)) == 0.0
    # não-dantas passa cheio
    assert _preco_com_desconto(
        SelecaoFornecedor(preco=5.0, fornecedor="almeida", **base)) == 5.0


def test_constante_desconto_existe():
    assert 0 < config.DESCONTO_DANTAS < 1
    assert round(config.DESCONTO_DANTAS, 2) == 0.17
