"""Fase 4 — testes do módulo de Substituição Tributária (markup por NCM).

Valida o lookup hierárquico (prefixo 4/6/8) contra a lista real extraída do
Anexo 05 do RICMS/PB, incluindo os dois produtos reais dos fornecedores."""
from __future__ import annotations

from pregoes import config, tributacao


def test_normalizar_ncm():
    assert tributacao.normalizar_ncm("3917.40.90") == "39174090"
    assert tributacao.normalizar_ncm("8544.42.00") == "85444200"
    assert tributacao.normalizar_ncm(None) == ""
    assert tributacao.normalizar_ncm("  3917 . 40 ") == "391740"


def test_lista_st_carregou():
    # A lista real do Anexo 05/PB tem ~389 entradas.
    assert len(tributacao._conjunto_st()) >= 300


def test_produto_real_almeida_eh_st_por_posicao():
    # Distribuidor Forceline (Almeida), NCM 3917.40.90 → ST via a POSIÇÃO 3917.
    assert tributacao.eh_st("39174090") is True
    assert tributacao.eh_st("3917.40.90") is True


def test_produto_real_dantas_eh_st_por_posicao():
    # Extensão Forceline (Dantas), NCM 8544.42.00 → ST via a POSIÇÃO 8544.
    assert tributacao.eh_st("85444200") is True


def test_nao_st_e_entradas_invalidas():
    assert tributacao.eh_st("99999999") is False   # inexistente
    assert tributacao.eh_st(None) is False
    assert tributacao.eh_st("") is False
    assert tributacao.eh_st("12") is False         # curto demais p/ classificar


def test_markup_por_classificacao():
    assert tributacao.markup_para("39174090") == config.MARKUP_ST      # 1.30
    assert tributacao.markup_para("99999999") == config.MARKUP_NORMAL  # 1.45
    assert tributacao.markup_para(None) == config.MARKUP_NORMAL
    # defaults editáveis por pregão
    assert tributacao.markup_para("39174090", markup_st=1.25) == 1.25
    assert tributacao.markup_para("99999999", markup_normal=1.5) == 1.5
