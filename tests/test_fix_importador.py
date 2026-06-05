"""
Regressão do fix #2b — _cell_num e preço pt-BR no importador.

Bug original: `_cell_num` fazia `str(valor).replace(",", ".")` + `float()`.
Para o formato monetário pt-BR com milhar ('1.234,56') isso virava
'1.234.56' e o `float()` falhava → preço descartado em SILÊNCIO (produto
entrava no catálogo sem histórico de preço, sem qualquer aviso ao operador).

Estes testes falhariam antes do fix:
- '1.234,56' → 1234.56 (antes: None)
- 'R$ 5,00'  → 5.0     (antes: None)
- e: célula de preço inconversível agora gera aviso em stats.avisos
  (antes: perdido em silêncio).

Sem rede, sem navegador, sem Streamlit. Exercita as funções puras e o
laço de importação com uma planilha .xlsx temporária gerada em memória.
"""
from __future__ import annotations

import pytest
from openpyxl import Workbook

from pregoes.importador import (
    _cell_num,
    _ler_linha_row,
    _texto_para_float_br,
)


# === _cell_num: formato pt-BR e contrato (valor, erro) ===

@pytest.mark.parametrize("entrada,esperado", [
    ("1.234,56", 1234.56),   # milhar BR — o caso que quebrava antes
    ("R$ 5,00", 5.0),        # prefixo monetário + decimal vírgula
    ("10", 10.0),            # inteiro como texto
    ("1234,56", 1234.56),    # sem milhar
    ("12.34", 12.34),        # ponto decimal (sem vírgula) — mantém compat
    ("1.234.567,89", 1234567.89),  # dois pontos de milhar
    ("R$ 1.000,00", 1000.0),
    ("  R$  42,00 ", 42.0),  # espaços ao redor
])
def test_cell_num_converte_formatos_br(entrada, esperado):
    valor, erro = _cell_num(entrada)
    assert valor == pytest.approx(esperado)
    assert erro is None


@pytest.mark.parametrize("entrada", [None, "", "   "])
def test_cell_num_vazio_retorna_none_sem_erro(entrada):
    """Célula legitimamente vazia: None e SEM aviso (não é erro)."""
    valor, erro = _cell_num(entrada)
    assert valor is None
    assert erro is None


def test_cell_num_celula_numerica_data_only():
    """O caminho de célula numérica (openpyxl data_only=True) é preservado."""
    assert _cell_num(10) == (10.0, None)
    assert _cell_num(3.14) == (3.14, None)


def test_cell_num_zero_ou_negativo_vira_none():
    """Preço <= 0 não é preço válido — None, mas sem aviso (não é texto inválido)."""
    assert _cell_num(0) == (None, None)
    assert _cell_num("0,00") == (None, None)
    assert _cell_num(-5) == (None, None)


def test_cell_num_booleano_nao_e_numero():
    """bool é subclasse de int em Python — não pode ser tratado como preço."""
    valor, erro = _cell_num(True)
    assert valor is None


def test_cell_num_texto_inconversivel_gera_erro():
    """Conteúdo presente mas não numérico: None + mensagem de erro (não silêncio)."""
    valor, erro = _cell_num("consultar")
    assert valor is None
    assert erro is not None
    assert "consultar" in erro


def test_cell_num_milhar_br_antes_falhava():
    """Guarda explícita do bug: '1.234,56' jamais pode voltar a virar None."""
    valor, erro = _cell_num("1.234,56")
    assert valor == pytest.approx(1234.56)
    assert erro is None


# === _texto_para_float_br: helper de baixo nível ===

@pytest.mark.parametrize("entrada,esperado", [
    ("1.234,56", 1234.56),  # com vírgula: ponto é milhar
    ("R$ 5,00", 5.0),
    ("12.34", 12.34),       # sem vírgula: ponto é decimal (compat com extrator)
    ("1.000", 1.0),         # sem vírgula: ponto tratado como decimal, por convenção
])
def test_texto_para_float_br_basico(entrada, esperado):
    assert _texto_para_float_br(entrada) == pytest.approx(esperado)


def test_texto_para_float_br_inconversivel():
    assert _texto_para_float_br("abc") is None
    assert _texto_para_float_br("") is None


# === _ler_linha_row: propaga aviso quando o preço se perde ===

def _mapa_simples() -> dict[str, int]:
    # descricao=0, preco=1 (layout mínimo, sem item/quantidade para não filtrar)
    return {"descricao": 0, "preco": 1}


def test_ler_linha_preco_br_ok_sem_aviso():
    row = ("PARAFUSO SEXTAVADO 1/2", "1.234,56")
    linha, aviso = _ler_linha_row(row, _mapa_simples())
    assert linha is not None
    assert linha.preco == pytest.approx(1234.56)
    assert aviso is None


def test_ler_linha_preco_inconversivel_gera_aviso():
    """O cerne do fix: preço com lixo não some calado — vira aviso ao operador."""
    row = ("CABO FLEXIVEL 2,5MM", "sob consulta")
    linha, aviso = _ler_linha_row(row, _mapa_simples())
    assert linha is not None
    assert linha.preco is None
    assert aviso is not None
    assert "CABO FLEXIVEL 2,5MM" in aviso
    assert "sob consulta" in aviso


def test_ler_linha_preco_vazio_sem_aviso():
    row = ("TUBO PVC 100MM", None)
    linha, aviso = _ler_linha_row(row, _mapa_simples())
    assert linha is not None
    assert linha.preco is None
    assert aviso is None


def test_ler_linha_aviso_encurta_descricao_longa():
    desc_longa = "ELETRODUTO " + "X" * 100
    row = (desc_longa, "R$ inválido")
    linha, aviso = _ler_linha_row(row, _mapa_simples())
    assert aviso is not None
    assert "..." in aviso  # truncada para não poluir o resumo


# === Integração leve: laço de importação acumula aviso em stats ===

def test_importar_planilha_acumula_aviso_de_preco(tmp_path):
    """End-to-end do fix: uma planilha com preço lixo gera aviso em stats,
    o produto entra (catálogo) mas o preço não vira histórico em silêncio."""
    from pregoes import db, importador

    wb = Workbook()
    ws = wb.active
    # Cabeçalho reconhecível: precisa conter 'DESCRIÇÃO' e idealmente CÓDIGO.
    ws.append(["ITEM", "DESCRIÇÃO", "UNID", "CÓDIGO", "PREÇO"])
    ws.append([1, "PARAFUSO SEXTAVADO 1/2", "UN", "A123", "1.234,56"])  # BR ok
    ws.append([2, "CABO FLEXIVEL 2,5MM", "M", "A456", "sob consulta"])  # lixo
    caminho = tmp_path / "historico_teste.xlsx"
    wb.save(caminho)

    # Banco isolado em arquivo temporário (init_db e conectar compartilham o
    # path; ':memory:' não é compartilhado entre conexões distintas).
    db_path = tmp_path / "pregoes_teste.db"
    db.init_db(db_path)
    conn = db.conectar(db_path)
    try:
        stats = importador.importar([caminho], fornecedor="almeida", conn=conn)
    finally:
        conn.close()

    # O preço BR válido virou histórico; o lixo gerou aviso, não sumiu calado.
    assert stats.precos_gravados >= 1
    assert any("sob consulta" in a for a in stats.avisos), stats.avisos
    assert any("CABO FLEXIVEL" in a for a in stats.avisos), stats.avisos
