"""Fase 4 — Substituição Tributária no exportador (modelo do contador, 2026-07).

No modelo novo o PREÇO VENDA (coluna G) é MANUAL — o exportador nunca escreve
`=S*markup`. O efeito tributário do item migrou do markup em G para a FLAG da
coluna AC: o app grava o texto 'SIM' nos itens com ST (por NCM via
`tributacao.eh_st`, ou por decisão explícita do operador em `itens_com_st`) e
DEIXA EM BRANCO os demais. A própria planilha lê essa flag (IF(AC="SIM",...))
para zerar o imposto estadual e ajustar o gross-up de preço.

Estes testes conferem a flag AC por item sem tocar no resto do template. Dados
começam na linha 3 (offset 0 → linha 3)."""
from __future__ import annotations

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from pregoes.exportador import COL_ST, TOKEN_ST, SelecaoFornecedor, exportar
from pregoes.extrator import ItemExtraido


def _itens(n):
    return [ItemExtraido(descricao=f"item {i}", quantidade=1) for i in range(n)]


def test_st_por_ncm_marca_flag_ac_apenas_no_item_st(tmp_path):
    """Item com NCM de ST recebe 'SIM' na coluna AC; item normal fica vazio.

    (Antes o efeito do ST era um markup menor em G — 1,30 vs 1,45. Agora G é
    manual e a condição tributária mora na flag AC.)"""
    itens = _itens(2)
    selecoes = {
        # 39174090 está no Anexo 05 (via posição 3917) → ST → AC 'SIM'
        0: SelecaoFornecedor(preco=10.0, codigo_fornecedor="1", marca="A", ncm="39174090"),
        # 99999999 não está → normal → AC vazio
        1: SelecaoFornecedor(preco=20.0, codigo_fornecedor="2", marca="B", ncm="99999999"),
    }
    destino = tmp_path / "matriz.xlsx"
    exportar(itens, selecoes=selecoes, destino=destino)
    ws = load_workbook(destino).active
    assert ws["AC3"].value == TOKEN_ST     # item ST (offset 0 → linha 3)
    assert ws["AC4"].value is None         # item normal (offset 1 → linha 4)
    # Preço venda é manual em ambos — o exportador nunca escreve G.
    assert ws["G3"].value is None
    assert ws["G4"].value is None
    # Demais fórmulas do template intactas (H referencia G/J).
    assert str(ws["H3"].value or "").startswith("=")


def test_decisao_do_operador_tem_precedencia(tmp_path):
    """A decisão explícita do operador (tela de seleção → itens_com_st) manda.

    Marca ST mesmo quando a opção escolhida não traz NCM que dispararia o ST
    sozinho. (Antes o override do operador era via markup em G; no modelo novo
    o preço G é manual e a condição tributária mora na flag AC.)"""
    itens = _itens(1)
    # Seleção sem NCM → sozinha NÃO viraria ST.
    selecoes = {0: SelecaoFornecedor(preco=10.0, codigo_fornecedor="1", marca="A")}
    destino = tmp_path / "m.xlsx"
    exportar(itens, selecoes=selecoes, itens_com_st={0}, destino=destino)
    ws = load_workbook(destino).active
    assert ws["AC3"].value == TOKEN_ST     # decisão do operador vence o default (não-ST)
    assert ws["G3"].value is None          # preço venda é manual


def test_sem_ncm_e_sem_marcacao_nao_vira_st(tmp_path):
    """Sem NCM de ST e sem marcação explícita → item não é ST (AC vazio).

    O parâmetro markup existe por compatibilidade mas é IGNORADO: não escreve G
    nem afeta a condição tributária."""
    itens = _itens(1)
    selecoes = {0: SelecaoFornecedor(preco=10.0, codigo_fornecedor="1", marca="A")}
    destino = tmp_path / "m2.xlsx"
    exportar(itens, selecoes=selecoes, markup=1.5, destino=destino)
    ws = load_workbook(destino).active
    assert ws["AC3"].value is None         # sem NCM e sem marcação → não é ST
    assert ws["G3"].value is None          # markup global não escreve G (manual)


# === Coluna AC: flag nativa "SUBST. TRIBUTÁRIA" (modelo do contador) ===
# AC (29) é coluna NATIVA do template; cabeçalho em AC2. O app grava 'SIM' nos
# itens com ST (itens_com_st) e deixa os demais em branco.

def test_coluna_st_marca_sim_e_vazio_por_item(tmp_path):
    itens = _itens(3)
    destino = tmp_path / "m.xlsx"
    exportar(itens, itens_com_st={0, 2}, destino=destino)
    ws = load_workbook(destino).active
    ac = get_column_letter(COL_ST)                    # "AC"
    assert "TRIBUT" in str(ws[f"{ac}2"].value or "").upper()  # cabeçalho nativo
    assert ws[f"{ac}3"].value == TOKEN_ST   # offset 0 → ST
    assert ws[f"{ac}4"].value is None       # offset 1 → normal (vazio)
    assert ws[f"{ac}5"].value == TOKEN_ST   # offset 2 → ST


def test_coluna_st_sem_info_fica_tudo_vazio(tmp_path):
    """Sem itens_com_st (ex.: 'Gerar sem buscar') → nenhum item marcado como ST."""
    destino = tmp_path / "m.xlsx"
    exportar(_itens(2), destino=destino)
    ws = load_workbook(destino).active
    ac = get_column_letter(COL_ST)
    assert ws[f"{ac}3"].value is None
    assert ws[f"{ac}4"].value is None


def test_coluna_st_nao_desloca_formulas_do_template(tmp_path):
    """A flag AC não desloca nada: as fórmulas do template seguem idênticas."""
    destino = tmp_path / "m.xlsx"
    exportar(_itens(1), selecoes={0: SelecaoFornecedor(
        preco=10.0, codigo_fornecedor="1", marca="A", ncm="39174090")},
        itens_com_st={0}, destino=destino)
    ws = load_workbook(destino).active
    assert ws["AC3"].value == TOKEN_ST              # flag ST gravada
    assert ws["S3"].value == "=L3+N3+P3+R3"         # custo aquisição intacto
    assert ws["AB3"].value == (                     # última coluna do template intacta
        '=IF($AC3="SIM",($S3/(100%-$V$2-$W$2-$X$2-AB$2)),'
        '($S3/(100%-$V$2-$W$2-$X$2-$Y$2-AB$2)))')
    assert ws["G3"].value is None                   # preço venda é manual


def test_offsets_com_st_reconstroi_decisao():
    from pregoes import tributacao
    markups = {0: 1.30, 1: 1.45, 2: 1.30, 3: 1.45}
    assert tributacao.offsets_com_st(markups, 1.30) == {0, 2}
    assert tributacao.offsets_com_st(None, 1.30) == set()
    assert tributacao.offsets_com_st({}, 1.30) == set()
