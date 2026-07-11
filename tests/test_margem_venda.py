"""
Pré-preenchimento do PREÇO VENDA (G) pela margem escolhida (2026-07-11).

Decisão do cliente: em vez de deixar G 100% manual, o app pré-preenche G com um
dos 3 cenários que a planilha já calcula (15/12/7% → colunas Z/AA/AB), via
FÓRMULA (`=Z{linha}` etc.). Assim a planilha sai pronta, já ajustada para ST, e
continua editável. Padrão de fábrica: 7% (coluna AB).

Cobre: o exportador grava a fórmula certa só nos itens COM preço; sem margem
(ou margem inválida) mantém G em branco (contrato antigo); e a persistência da
escolha em credenciais.json.
"""
import json

import pytest
from openpyxl import load_workbook

from pregoes import config, credenciais
from pregoes.exportador import exportar, SelecaoFornecedor
from pregoes.extrator import ItemExtraido

_L1 = config.LINHA_DADOS_INICIO        # linha 3 = 1º item


def _itens():
    return [
        ItemExtraido(item_numero=1, descricao="CONEXAO PVC 50MM", unidade="UN",
                     quantidade=10, valor_referencia=8.0),
        ItemExtraido(item_numero=2, descricao="TUBO PVC 100MM", unidade="BR",
                     quantidade=5, valor_referencia=60.0),
    ]


def _selecoes_um_item():
    # Só o 1º item tem preço; o 2º fica sem seleção (G deve ficar em branco nele).
    return {0: SelecaoFornecedor(preco=5.22, codigo_fornecedor="39010",
                                 marca="SOCELPLAST", fornecedor="almeida")}


# === Exportador: G vira fórmula do cenário escolhido ===

@pytest.mark.parametrize("coluna", ["Z", "AA", "AB"])
def test_margem_prefill_grava_formula_no_item_com_preco(tmp_path, coluna):
    destino = tmp_path / "m.xlsx"
    exportar(_itens(), selecoes=_selecoes_um_item(),
             margem_venda=coluna, destino=destino)
    ws = load_workbook(destino).active
    assert ws[f"G{_L1}"].value == f"={coluna}{_L1}"      # item COM preço → fórmula
    assert ws[f"G{_L1 + 1}"].value is None               # item SEM preço → em branco


def test_padrao_de_fabrica_e_7_por_cento_coluna_ab():
    """A decisão do cliente (2026-07-11): padrão de fábrica = 7% = coluna AB."""
    assert config.MARGEM_VENDA_PADRAO == "AB"
    assert config.MARGENS_VENDA["AB"] == 0.07


def test_sem_margem_mantem_g_manual(tmp_path):
    """Contrato antigo preservado: sem margem escolhida, G fica em branco."""
    destino = tmp_path / "m.xlsx"
    exportar(_itens(), selecoes=_selecoes_um_item(), destino=destino)   # sem margem_venda
    ws = load_workbook(destino).active
    assert ws[f"G{_L1}"].value is None


def test_margem_invalida_e_ignorada(tmp_path):
    """Coluna desconhecida nunca vira fórmula (defensivo) — G fica em branco."""
    destino = tmp_path / "m.xlsx"
    exportar(_itens(), selecoes=_selecoes_um_item(),
             margem_venda="ZZ", destino=destino)
    ws = load_workbook(destino).active
    assert ws[f"G{_L1}"].value is None


def test_formula_g_aponta_para_a_mesma_linha_de_cada_item(tmp_path):
    """Cada G referencia o cenário DA SUA linha (não uma linha fixa)."""
    itens = _itens()
    selecoes = {
        0: SelecaoFornecedor(preco=5.0, codigo_fornecedor="1", marca="A", fornecedor="almeida"),
        1: SelecaoFornecedor(preco=9.0, codigo_fornecedor="2", marca="B", fornecedor="almeida"),
    }
    destino = tmp_path / "m.xlsx"
    exportar(itens, selecoes=selecoes, margem_venda="AB", destino=destino)
    ws = load_workbook(destino).active
    assert ws[f"G{_L1}"].value == f"=AB{_L1}"
    assert ws[f"G{_L1 + 1}"].value == f"=AB{_L1 + 1}"


# === Persistência da escolha em credenciais.json ===

@pytest.fixture
def cred_isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(credenciais, "CAMINHO", tmp_path / "credenciais.json")
    return tmp_path


def test_salvar_e_carregar_margem(cred_isolado):
    assert credenciais.carregar_margem_venda() is None        # nada salvo ainda
    credenciais.salvar_margem_venda("Z")
    assert credenciais.carregar_margem_venda() == "Z"


def test_salvar_margem_preserva_outras_entradas(cred_isolado):
    credenciais.salvar("almeida", "a@b.c", "senha")
    credenciais.salvar_margem_venda("AA")
    assert credenciais.carregar_margem_venda() == "AA"
    assert credenciais.carregar("almeida") == {"email": "a@b.c", "senha": "senha"}
    # E o arquivo continua um JSON válido com as duas chaves.
    dados = json.loads(credenciais.CAMINHO.read_text(encoding="utf-8"))
    assert dados["margem_venda"] == "AA" and "almeida" in dados
