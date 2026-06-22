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


# === Coluna AC: indicador "SUBST. TRIBUTÁRIA" (pedido do cliente, 2026-06) ===
# Coluna AC (29), após a última do template (AB) — não desloca fórmula nenhuma.
# "Sim"/"Não" por item, baseado em itens_com_st (decisão da tela de seleção).

def test_coluna_st_marca_sim_e_nao_por_item(tmp_path):
    from openpyxl.utils import get_column_letter
    from pregoes.exportador import COL_ST_INDICADOR, CABECALHO_ST
    itens = _itens(3)
    destino = tmp_path / "m.xlsx"
    exportar(itens, itens_com_st={0, 2}, destino=destino)
    ws = load_workbook(destino).active
    ac = get_column_letter(COL_ST_INDICADOR)          # "AC"
    assert ws[f"{ac}4"].value == CABECALHO_ST
    assert ws[f"{ac}5"].value == "Sim"   # offset 0 → ST
    assert ws[f"{ac}6"].value == "Não"   # offset 1 → normal
    assert ws[f"{ac}7"].value == "Sim"   # offset 2 → ST


def test_coluna_st_sem_info_fica_tudo_nao(tmp_path):
    """Sem itens_com_st (ex.: 'Gerar sem buscar') → coluna presente, tudo Não."""
    from openpyxl.utils import get_column_letter
    from pregoes.exportador import COL_ST_INDICADOR
    destino = tmp_path / "m.xlsx"
    exportar(_itens(2), destino=destino)
    ws = load_workbook(destino).active
    ac = get_column_letter(COL_ST_INDICADOR)
    assert ws[f"{ac}5"].value == "Não"
    assert ws[f"{ac}6"].value == "Não"


def test_coluna_st_nao_desloca_formulas_do_template(tmp_path):
    """A coluna AC é APÊNDICE: as fórmulas G..AB seguem idênticas."""
    destino = tmp_path / "m.xlsx"
    exportar(_itens(1), selecoes={0: SelecaoFornecedor(
        preco=10.0, codigo_fornecedor="1", marca="A", ncm="39174090")},
        itens_com_st={0}, destino=destino)
    ws = load_workbook(destino).active
    assert ws["G5"].value == "=S5*1.3"          # markup ST intacto
    assert ws["S5"].value == "=L5+N5+P5+R5"     # custo aquisição intacto
    assert ws["AB5"].value == "=G5-AA5"         # última coluna do template intacta


def test_offsets_com_st_reconstroi_decisao():
    from pregoes import tributacao
    markups = {0: 1.30, 1: 1.45, 2: 1.30, 3: 1.45}
    assert tributacao.offsets_com_st(markups, 1.30) == {0, 2}
    assert tributacao.offsets_com_st(None, 1.30) == set()
    assert tributacao.offsets_com_st({}, 1.30) == set()
