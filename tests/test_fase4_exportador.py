"""Fase 4 — markup POR ITEM no exportador.

Item com ST (NCM no Anexo 05) sai com markup 1,30; item normal com 1,45; e o
override do operador (UI) tem precedência. Confere a fórmula da coluna G por
linha, sem tocar no resto do template."""
from __future__ import annotations

from openpyxl import load_workbook

from pregoes.exportador import SelecaoFornecedor, exportar
from pregoes.extrator import ItemExtraido


def _itens(n):
    return [ItemExtraido(descricao=f"item {i}", quantidade=1) for i in range(n)]


def test_markup_por_item_st_vs_normal(tmp_path):
    itens = _itens(2)
    selecoes = {
        # 3917.40.90 está no Anexo 05 (via posição 3917) → ST → 1,30
        0: SelecaoFornecedor(preco=10.0, codigo_fornecedor="1", marca="A", ncm="39174090"),
        # 99999999 não está → normal → 1,45
        1: SelecaoFornecedor(preco=20.0, codigo_fornecedor="2", marca="B", ncm="99999999"),
    }
    destino = tmp_path / "matriz.xlsx"
    exportar(itens, selecoes=selecoes, destino=destino)
    ws = load_workbook(destino).active
    assert ws["G5"].value == "=S5*1.3"     # item ST
    assert ws["G6"].value == "=S6*1.45"    # item normal
    # Demais fórmulas intactas (H referencia G)
    assert str(ws["H5"].value or "").startswith("=")


def test_override_da_ui_tem_precedencia(tmp_path):
    itens = _itens(1)
    selecoes = {0: SelecaoFornecedor(preco=10.0, codigo_fornecedor="1",
                                     marca="A", ncm="39174090")}  # seria 1,30
    destino = tmp_path / "m.xlsx"
    exportar(itens, selecoes=selecoes, markups={0: 1.6}, destino=destino)
    ws = load_workbook(destino).active
    assert ws["G5"].value == "=S5*1.6"     # override vence o default de ST


def test_sem_ncm_usa_markup_global(tmp_path):
    itens = _itens(1)
    selecoes = {0: SelecaoFornecedor(preco=10.0, codigo_fornecedor="1", marca="A")}
    destino = tmp_path / "m2.xlsx"
    exportar(itens, selecoes=selecoes, markup=1.5, destino=destino)
    ws = load_workbook(destino).active
    assert ws["G5"].value == "=S5*1.5"
