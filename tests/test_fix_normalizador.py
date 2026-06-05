"""
Testes de regressão — seção de cabo elétrico em mm² / mm2.

Achado (2026-06): `extrair_dimensoes` perdia a SEÇÃO de cabo elétrico.
- '2.5MM²' (com superscript U+00B2) e '2.5MM2' (ASCII) NÃO geravam dimensão
  alguma porque o '2'/'²' depois de 'mm' quebrava o `\\b` do regex `_RE_MM`.
- Consequência: `extrair_dimensoes('CABO 2.5MM2 750V')` só via a tensão;
  `matcher._dimensao_diverge('CABO 2.5MM2', 'CABO 4MM2')` == False; e
  `relevancia` dava 1.0 — cabos de seções diferentes casavam (preço errado
  silencioso, já que 2.5mm² e 4mm² são subprodutos distintos).

Correção:
- Normalização rebaixa o expoente de área ('²' → '2', '³' → '3').
- A seção 'mm²/mm2' virou um EIXO PRÓPRIO ('mm2'), distinto do diâmetro
  nominal 'mm' (joelho 20mm). '2.5mm2' NÃO é lido como '2.5mm'.
- A penalidade de dimensão divergente em `relevancia` passou a ter
  PRECEDÊNCIA sobre o boost — um eixo coincidente (mesma tensão) não
  cancela a divergência de outro eixo (seção do cabo).

Todos os testes abaixo FALHAM no código pré-fix e passam depois. Pytest
puro, sem rede/navegador/Streamlit.
"""
from __future__ import annotations

from pregoes.normalizador import (
    extrair_dimensoes,
    normalizar_descricao,
    relevancia,
)
from pregoes.matcher import _dimensao_diverge


# --- Normalização do expoente de área ---------------------------------------

def test_normalizacao_rebaixa_superscript_area():
    # '²' (U+00B2) sobrevive ao stripping de acento; precisa virar '2' ASCII.
    assert normalizar_descricao("CABO 2.5MM² 750V") == "cabo 2.5mm2 750v"


def test_normalizacao_unifica_unicode_e_ascii():
    # A grafia com superscript e a grafia ASCII colapsam na mesma string.
    assert (normalizar_descricao("CABO 2.5MM² 750V")
            == normalizar_descricao("CABO 2.5MM2 750V"))


# --- extrair_dimensoes: seção como eixo próprio 'mm2' ------------------------

def test_extrai_secao_mm2_ascii():
    dims = extrair_dimensoes("CABO FLEXIVEL 2.5MM2 750V")
    assert ("mm2", "2.5") in dims


def test_extrai_secao_mm2_superscript():
    dims = extrair_dimensoes("CABO FLEXIVEL 2.5MM² 750V")
    assert ("mm2", "2.5") in dims


def test_secao_nao_eh_lida_como_diametro():
    # '2.5mm2' é seção (mm2), nunca diâmetro (mm). Sem isso, cabo 2.5mm²
    # casaria joelho 2.5mm.
    dims = extrair_dimensoes("CABO 2.5MM2")
    assert ("mm2", "2.5") in dims
    assert ("mm", "2.5") not in dims
    # Não há nenhum par no eixo 'mm' para esta descrição (só seção).
    assert not any(eixo == "mm" for eixo, _ in dims)


def test_secao_diferente_valor_diferente():
    assert extrair_dimensoes("CABO 4MM2 750V") >= {("mm2", "4")}
    assert ("mm2", "2.5") not in extrair_dimensoes("CABO 4MM2 750V")


# --- Diâmetro 'mm' (joelho/conexão) continua funcionando --------------------

def test_diametro_mm_de_conexao_preservado():
    assert extrair_dimensoes("JOELHO 20MM") == {("mm", "20")}
    assert extrair_dimensoes("JOELHO 25MM") == {("mm", "25")}


def test_metros_nao_viram_milimetro():
    # '6M' (metros) nunca pode cair no eixo 'mm'.
    assert not any(eixo in ("mm", "mm2") for eixo, _ in
                   extrair_dimensoes("VERGALHAO ACO C/ 6M"))


# --- _dimensao_diverge (contrato do matcher) --------------------------------

def test_dimensao_diverge_cabo_secao_diferente_ascii():
    assert _dimensao_diverge("CABO 2.5MM2 750V", "CABO 4MM2 750V") is True


def test_dimensao_diverge_cabo_secao_diferente_superscript():
    assert _dimensao_diverge("CABO 2.5MM² 750V", "CABO 4MM² 750V") is True


def test_dimensao_nao_diverge_mesma_secao():
    assert _dimensao_diverge("CABO 2.5MM2 750V", "CABO 2.5MM² 750V") is False


def test_dimensao_diverge_diametro_conexao_continua():
    # Regressão do fix anterior (mm de conexão): 20mm vs 25mm diverge.
    assert _dimensao_diverge("JOELHO 20MM", "JOELHO 25MM") is True
    assert _dimensao_diverge("JOELHO 20MM", "JOELHO 20MM") is False


# --- relevancia: seções diferentes não podem casar com 1.0 ------------------

def test_relevancia_cabos_secao_diferente_abaixo_de_um():
    # Mesma tensão (750V) NÃO pode mascarar a divergência de seção.
    r = relevancia("CABO FLEXIVEL 2.5MM2 750V", "CABO FLEXIVEL 4MM2 750V")
    assert r < 1.0


def test_relevancia_cabos_secao_diferente_superscript_abaixo_de_um():
    r = relevancia("CABO FLEXIVEL 2.5MM² 750V", "CABO FLEXIVEL 4MM² 750V")
    assert r < 1.0


def test_relevancia_mesma_secao_casa_alto():
    # Mesma seção (variando só grafia/ruído) continua casando forte.
    r = relevancia("CABO FLEXIVEL 2.5MM² 750V",
                   "CABO FLEXIVEL 2.5MM2 750V PRETO")
    assert r >= 0.7


def test_relevancia_conexao_diametro_diferente_abaixo_de_um():
    # Regressão: 20mm vs 25mm de conexão segue divergindo (não casa 1.0).
    r = relevancia("JOELHO 20MM PVC SOLDAVEL", "JOELHO 25MM PVC SOLDAVEL")
    assert r < 1.0
