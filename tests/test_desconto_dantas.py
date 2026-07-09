"""Desconto comercial da Dantas (2026-06-27): o custo trazido do site da Dantas
entra na coluna L com 17% de desconto; Almeida/catálogo entram cheios.

Modelo do contador (2026-07): o item offset 0 cai na 1ª linha de dados
(config.LINHA_DADOS_INICIO = 3). O PREÇO VENDA (G) é MANUAL — não há mais
fórmula '=S*markup'; o efeito da Substituição Tributária migrou para a flag
nativa 'SIM' na coluna AC, que a própria planilha lê para ajustar o preço."""
from __future__ import annotations

from openpyxl import load_workbook

from pregoes import config
from pregoes.exportador import (COL_ST, TOKEN_ST, SelecaoFornecedor,
                                _preco_com_desconto, eh_dantas, exportar)
from pregoes.extrator import ItemExtraido

# 1ª linha de dados no modelo do contador (offset 0 → linha 3).
_L1 = config.LINHA_DADOS_INICIO


def _itens(n):
    return [ItemExtraido(descricao=f"item {i}", quantidade=1) for i in range(n)]


def _celula(tmp_path, sel, coluna):
    destino = tmp_path / "m.xlsx"
    exportar(_itens(1), selecoes={0: sel}, destino=destino)
    return load_workbook(destino).active[f"{coluna}{_L1}"].value


def _L(tmp_path, sel):
    return _celula(tmp_path, sel, "L")


def test_dantas_aplica_17pct_na_coluna_L(tmp_path):
    sel = SelecaoFornecedor(preco=5.0, codigo_fornecedor="1", marca="A",
                            fornecedor="dantas")
    assert _L(tmp_path, sel) == 4.15          # 5,00 − 17%


def test_almeida_entra_com_custo_cheio(tmp_path):
    sel = SelecaoFornecedor(preco=5.0, codigo_fornecedor="1", marca="A",
                            fornecedor="almeida")
    assert _L(tmp_path, sel) == 5.0


def test_selecao_sem_fornecedor_nao_descontada(tmp_path):
    sel = SelecaoFornecedor(preco=5.0, codigo_fornecedor="1", marca="A")
    assert _L(tmp_path, sel) == 5.0


# === Sufixo "D" no código dos itens da Dantas (coluna T) — pedido do cliente
# 2026-06-27: identificar a origem direto na planilha. Mesma convenção do
# importador (sufixo D = Dantas), então a retroalimentação atribui a origem certa.

def _T(tmp_path, sel):
    return _celula(tmp_path, sel, "T")


def test_codigo_dantas_recebe_sufixo_D(tmp_path):
    sel = SelecaoFornecedor(preco=5.0, codigo_fornecedor="9185", marca="A",
                            fornecedor="dantas")
    assert _T(tmp_path, sel) == "9185D"


def test_codigo_almeida_fica_sem_sufixo(tmp_path):
    sel = SelecaoFornecedor(preco=5.0, codigo_fornecedor="37215", marca="A",
                            fornecedor="almeida")
    assert _T(tmp_path, sel) == "37215"


def test_codigo_dantas_vazio_nao_vira_so_D(tmp_path):
    sel = SelecaoFornecedor(preco=5.0, codigo_fornecedor="", marca="A",
                            fornecedor="dantas")
    assert not _T(tmp_path, sel)   # sem código → não inventa um "D" solto


def test_desconto_compoe_com_flag_st(tmp_path):
    # Item Dantas COM ST: custo descontado em L E flag 'SIM' na coluna AC.
    # (No modelo antigo o ST vinha como markup 1,30 em G; agora G é manual e o
    # efeito do ST migrou para a flag AC, que a planilha lê no cálculo.)
    sel = {0: SelecaoFornecedor(preco=10.0, codigo_fornecedor="1", marca="A",
                                ncm="39174090", fornecedor="dantas")}
    destino = tmp_path / "m.xlsx"
    exportar(_itens(1), selecoes=sel, destino=destino)
    ws = load_workbook(destino).active
    assert ws[f"L{_L1}"].value == 8.3               # 10 − 17% (desconto Dantas)
    # ST via NCM → 'SIM' na coluna AC; compõe com o desconto já aplicado em L.
    assert ws.cell(row=_L1, column=COL_ST).value == TOKEN_ST
    # PREÇO VENDA é manual no modelo novo: G fica em branco.
    assert ws[f"G{_L1}"].value is None


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
