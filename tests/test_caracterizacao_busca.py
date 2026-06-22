"""Rede de CARACTERIZAÇÃO ("golden") do motor de busca.

Trava o comportamento que JÁ funciona, para que qualquer mudança futura
(regex de dimensão, threshold, markup) que o altere FALHE na hora. É a
salvaguarda que de-risca evoluções como o eixo de bitola (nº) do #2.

Três camadas:
1. SNAPSHOT de extrair_dimensoes sobre um corpus REAL curado do catálogo do
   cliente — muda só de propósito (e o diff fica explícito no teste).
2. Pares que DEVEM casar (não podem divergir): abreviação, valor que cobre
   faixa, unidade de venda (barra) — o que o operador espera achar.
3. Pares que NÃO PODEM casar (devem divergir): medida/bitola/classe errada —
   o que protege contra preço errado.

Corpus extraído de data/pregoes.db (3245 produtos) em 2026-06; descrições são
genéricas de catálogo (sem preço/código), curadas para cobrir cada eixo.
"""
from __future__ import annotations

import pytest

from pregoes.normalizador import extrair_dimensoes, _algum_eixo_diverge
from pregoes.matcher import _dimensao_diverge


# ============ Camada 1: SNAPSHOT (golden) das dimensões ==================
# Real do catálogo. Se um valor mudar, foi uma mudança DELIBERATA no extrator
# — atualize aqui CONSCIENTEMENTE (o diff mostra exatamente o que mexeu).
CORPUS_DIMS = {
    # bitola por nº (#2): valor único vira eixo; lista vira vários; faixa fica fora
    "arame galvanizado nº 12": [("n", "12")],
    "arame galvanizado nº 14": [("n", "14")],
    "arame galvanizado nº 16": [("n", "16")],
    "arame galvanizado kg nº18 nº16 nº14": [("n", "14"), ("n", "16"), ("n", "18")],
    "bucha nylon nº 10": [("n", "10")],
    "brita n°19": [("n", "19")],
    "bota eletricista n° 37 a 44": [],                    # FAIXA → sem eixo n
    # classe AWS de eletrodo (#2)
    "eletrodo e6013 2.5mm": [("eletrodo", "6013"), ("mm", "2.5")],
    "eletrodo 2.5mm": [("mm", "2.5")],
    # eixos que JÁ funcionavam — TÊM que permanecer idênticos
    "abracadeira aco para tubo 25mm": [("mm", "25")],
    "abracadeira aco para tubo 40mm": [("mm", "40")],
    "registro de esfera 3/4": [("pol", "3/4")],
    "registro de gaveta 1/2": [("pol", "1/2")],
    "cabo cobre nu 16mm2 para aterramento": [("mm2", "16")],
    "cabo flexivel 4mm2 750v": [("mm2", "4"), ("v", "750")],
    "caixa d'agua 1000 l plastica": [("l", "1000")],
    "caixa d'agua 5000l de polietileno": [("l", "5000")],
    "adesivo pva cola branca 1kg": [("kg", "1")],
    "tubo pvc soldavel 75mm barra 6m": [("mm", "75")],   # barra fora do eixo m
    "adesivo cola pvc 75g": [("kg", "0.075")],           # 75g = 0.075kg (não 75)
    "joelho pvc soldavel 90 graus 40mm": [("mm", "40")],
    "valvula de escoamento 7/8": [("pol", "7/8")],
}


@pytest.mark.parametrize("descricao,esperado", list(CORPUS_DIMS.items()))
def test_snapshot_dimensoes_do_catalogo(descricao, esperado):
    assert sorted(extrair_dimensoes(descricao)) == sorted(esperado)


# ============ Camada 2: pares que DEVEM casar (não divergem) =============
DEVE_CASAR = [
    # mesmo produto, grafia/abreviação diferente
    ("CAIXA D'AGUA 1000 LITROS", "caixa d'agua 1000 l plastica"),
    ("ADESIVO COLA PVC 75G", "adesivo polytubes cola pvc 75gr"),
    ("REGISTRO ESFERA 3/4", "registro de esfera 3/4"),
    ("CABO FLEXIVEL 4MM2 750V", "cabo flexivel 4mm2 cobre 750v"),
    # barra (unidade de venda) não pode punir o tubo certo
    ("TUBO PVC 75MM COMPRIMENTO 6M", "tubo pvc soldavel 75mm barra 3m"),
    # valor que COBRE: produto multi-bitola casa um edital de bitola específica
    ("ARAME GALVANIZADO Nº 14", "arame galvanizado kg nº18 nº16 nº14"),
    # faixa de tamanho NÃO pode divergir de um tamanho dentro dela
    ("BOTA ELETRICISTA Nº 42", "bota eletricista n° 37 a 44"),
    # ml/g convertidos: 1000ml == 1l, 75g == 75g
    ("ADESIVO 1000ML", "adesivo pvc 1 litro"),
]


@pytest.mark.parametrize("edital,produto", DEVE_CASAR)
def test_pares_devem_casar(edital, produto):
    da, dp = extrair_dimensoes(edital), extrair_dimensoes(produto)
    assert not _algum_eixo_diverge(da, dp), f"{edital!r} x {produto!r} não deviam divergir"
    assert _dimensao_diverge(edital, produto) is False


# ============ Camada 3: pares que NÃO PODEM casar (divergem) =============
NAO_PODE_CASAR = [
    # bitola/numeração errada (#2)
    ("ARAME GALVANIZADO Nº 14", "arame galvanizado nº 16"),
    ("BUCHA NYLON Nº 10", "bucha nylon nº 8"),
    ("BRITA Nº 1", "brita n°19"),
    # classe de eletrodo errada (#2)
    ("ELETRODO E6013 2.5MM", "eletrodo e7018 2.5mm"),
    # medidas que já barravam — continuam barrando
    ("TUBO PVC DIAMETRO 100", "tubo pvc soldavel 25mm 6m"),
    ("REGISTRO 1/2", "registro de gaveta 3/4"),
    ("CABO FLEXIVEL 4MM2", "cabo cobre nu 16mm2 para aterramento"),
    ("CAIXA D'AGUA 1000 LITROS", "caixa d'agua 5000l de polietileno"),
    ("CIMENTO 25KG", "cimento portland cp ii 50kg"),
    ("ADESIVO 75G", "adesivo 250g"),
]


@pytest.mark.parametrize("edital,produto", NAO_PODE_CASAR)
def test_pares_nao_podem_casar(edital, produto):
    da, dp = extrair_dimensoes(edital), extrair_dimensoes(produto)
    assert _algum_eixo_diverge(da, dp), f"{edital!r} x {produto!r} DEVIAM divergir"
    assert _dimensao_diverge(edital, produto) is True
