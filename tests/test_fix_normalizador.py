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
    _algum_eixo_diverge,
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


# === Diâmetro nominal sem unidade colada (DIÂMETRO 75 / DN 75 / Ø 75) ===
# Feedback do cliente: "TUBO ... DIÂMETRO 75" trazia tubo de 25/32mm porque o
# "75" solto não virava medida. Agora vira eixo mm (como o catálogo guarda 75MM).

def test_diametro_nominal_vira_eixo_mm():
    assert ("mm", "75") in extrair_dimensoes("TUBO PVC DIÂMETRO 75 SOLDÁVEL")
    assert ("mm", "25") in extrair_dimensoes("JOELHO DN 25")
    assert ("mm", "50") in extrair_dimensoes("DN50 ESGOTO")
    assert ("mm", "75") in extrair_dimensoes("DIÂMETRO NOMINAL 75")
    assert ("mm", "60") in extrair_dimensoes("CONEXÃO DIÂMETRO DE 60")   # 'de' opcional
    assert ("mm", "32") in extrair_dimensoes("Ø 32 BRANCO")              # sinal de diâmetro


def test_diametro_nominal_exige_ancora():
    # Número solto SEM âncora não vira diâmetro (não mis-fira).
    assert extrair_dimensoes("PARAFUSO 75 ITEM 30") == set()
    assert ("mm", "75") not in extrair_dimensoes("CÓDIGO 75 BRANCO")


def test_diametro_fracao_cede_a_polegada():
    d = extrair_dimensoes("VÁLVULA DIÂMETRO 7/8")
    assert ("pol", "7/8") in d and ("mm", "7") not in d


def test_diametro_75mm_colado_nao_quebra():
    assert extrair_dimensoes("TUBO DIÂMETRO 75MM") == {("mm", "75")}


def test_cabo_mm2_intacto_com_eixo_diametro():
    # Regressão: o eixo de diâmetro nominal não afeta a seção mm².
    assert extrair_dimensoes("CABO FLEXIVEL 2.5MM2 750V") == {("mm2", "2.5"), ("v", "750")}


def test_diametro_quebra_empate_de_relevancia():
    """Caso REAL do cliente: edital DIÂMETRO 75 (+ COMPRIMENTO 6M). Antes os 3
    diâmetros empatavam; agora 75mm passa e 25/32mm caem abaixo de 0.35. E o
    produto CERTO vendido em barra de tamanho diferente NÃO é punido — porque
    NÃO criamos eixo de metros (decisão da revisão adversarial)."""
    ed = ("TUBO HIDRAULICO MATERIAL PVC RIGIDO DIAMETRO 75 SOLDAVEL "
          "COMPRIMENTO 6M")
    r75 = relevancia(ed, "TUBO PVC SOLDAVEL 75MM 6M")
    r25 = relevancia(ed, "TUBO PVC SOLDAVEL 25MM 6M")
    rbarra = relevancia(ed, "TUBO PVC 75MM BARRA 3M")
    assert r75 >= 0.35                  # diâmetro certo passa
    assert r25 < 0.35                   # diâmetro errado é rejeitado
    assert r75 - r25 >= 0.25            # gap claro (antes era 0 — empate)
    assert rbarra >= 0.35              # barra de 3m (≠ 6M do edital) NÃO punida


# === Fase 2: diâmetro na query (diametro_para_query + _montar_camadas) ===

def test_diametro_para_query():
    from pregoes.normalizador import diametro_para_query
    assert diametro_para_query("TUBO PVC DIAMETRO 75 SOLDAVEL") == "75mm"
    assert diametro_para_query("JOELHO DN 25") == "25mm"
    assert diametro_para_query("CABO FLEXIVEL 2.5MM2") is None   # seção (mm²), não diâmetro
    assert diametro_para_query("VALVULA DIAMETRO 7/8") is None   # fração → polegada
    assert diametro_para_query("PARAFUSO 75") is None            # sem âncora
    assert diametro_para_query("DIAMETRO 5") is None             # bitola <10mm (conservador)


def test_montar_camadas_anexa_diametro_com_fallback():
    """Fase 2: a query com diâmetro vem ANTES da sem (mais precisa primeiro);
    a sem-diâmetro fica como fallback → só adiciona precisão, nunca regride.
    Item SEM diâmetro fica inalterado."""
    from pregoes.scraper import _montar_camadas
    qs = [q for _, q in _montar_camadas(
        "TUBO HIDRAULICO MATERIAL PVC RIGIDO DIAMETRO 75 SOLDAVEL COMPRIMENTO 6M")]
    assert "tubo hidraulico material pvc 75mm" in qs          # 1a: com diâmetro
    assert "tubo hidraulico material pvc" in qs               # 1b: fallback sem diâmetro
    assert qs.index("tubo hidraulico material pvc 75mm") < \
           qs.index("tubo hidraulico material pvc")           # com diâmetro PRIMEIRO
    # Item sem diâmetro: nenhuma camada extra
    qs2 = [q for _, q in _montar_camadas("ABRACADEIRA DE NYLON 100 PCT C/100")]
    assert all("mm" not in q.split() for q in qs2)


# === Auditoria 2026-06 (achados altos nº 3 e 4): eixos l / kg / m ===========
# O gate de dimensão não cobria litros, quilos e metros — exatamente os eixos
# nº 1 do domínio (caixa d'água 500/1000L, cimento 25/50KG, lona 4/8M). O
# subproduto de medida errada passava com relevância 0.8-0.9 e a Camada 4
# fuzzy propagava o código errado como 'exato'.

def test_eixo_litros_distingue_caixa_dagua():
    a = extrair_dimensoes("CAIXA D'AGUA POLIETILENO 500 LITROS")
    b = extrair_dimensoes("CAIXA D'AGUA POLIETILENO 1000L")
    assert ("l", "500") in a and ("l", "1000") in b
    assert _algum_eixo_diverge(a, b)


def test_eixo_kg_distingue_saco_de_cimento():
    a = extrair_dimensoes("CIMENTO PORTLAND CP II SACO 50KG")
    b = extrair_dimensoes("CIMENTO PORTLAND CP II SACO 25KG")
    assert _algum_eixo_diverge(a, b)


def test_eixo_metros_distingue_lona():
    a = extrair_dimensoes("LONA PLASTICA PRETA 4 METROS")
    b = extrair_dimensoes("LONA PLASTICA PRETA 8M")
    assert _algum_eixo_diverge(a, b)


def test_ml_converte_para_litros_e_casa():
    """'1000ML' e '1L' são o MESMO volume — não podem divergir."""
    a = extrair_dimensoes("ADESIVO PVC FRASCO 1000ML")
    b = extrair_dimensoes("ADESIVO PVC FRASCO 1 LITRO")
    assert a == {("l", "1")} and not _algum_eixo_diverge(a, b)


def test_gramas_converte_para_kg():
    a = extrair_dimensoes("SILICONE ACETICO 280G")
    b = extrair_dimensoes("SILICONE ACETICO 50G")
    assert ("kg", "0.28") in a
    assert _algum_eixo_diverge(a, b)


def test_metro_nao_captura_mm_nem_m2_m3():
    """'75MM' fica no eixo mm; 'M2'/'M3' (área/volume) não viram metro."""
    assert extrair_dimensoes("MANGUEIRA JARDIM 30M") == {("m", "30")}
    assert extrair_dimensoes("PLACA GESSO 30 M2") == set()
    assert extrair_dimensoes("AREIA LAVADA M3") == set()


def test_barra_e_comprimento_ficam_fora_do_eixo_metro():
    """Comprimento de BARRA é unidade de venda de tubo, não identidade do
    produto: 'TUBO 75MM BARRA 3M' não pode ser punido contra edital
    'COMPRIMENTO 6M' (regressão prevista na revisão adversarial do
    cross-brand e consagrada em test_diametro_quebra_empate_de_relevancia)."""
    a = extrair_dimensoes("TUBO PVC RIGIDO DIAMETRO 75 SOLDAVEL COMPRIMENTO 6M")
    b = extrair_dimensoes("TUBO PVC 75MM BARRA 3M")
    assert a == {("mm", "75")} and b == {("mm", "75")}
    assert not _algum_eixo_diverge(a, b)


def test_denominador_de_fracao_nao_vaza_para_eixo():
    """'REGISTRO 1/2 L' — o '2' da fração não pode virar '2 litros'."""
    assert extrair_dimensoes("REGISTRO ESFERA 1/2 L") == {("pol", "1/2")}


# === Verificação adversarial 2026-06 dos eixos novos: valores numéricos =====
# Os céticos demonstraram com linhas REAIS do catálogo que a comparação por
# string quebrava pares legítimos: '1.000 lt' (milhar pt-BR) ≠ '1000 LITROS',
# '2.00m' ≠ '2 METROS', '1.20' ≠ '1.2'. _norm_valor_dim agora normaliza.

def test_milhar_ptbr_no_eixo_litros_nao_diverge():
    a = extrair_dimensoes("CAIXA DAGUA POLIETILENO 1.000 LT")
    b = extrair_dimensoes("CAIXA D'AGUA POLIETILENO 1000 LITROS")
    assert a == b == {("l", "1000")}
    assert not _algum_eixo_diverge(a, b)


def test_zeros_finais_de_decimal_nao_divergem():
    assert extrair_dimensoes("HASTE ATERRAMENTO COBRE 2.00M") == \
        extrair_dimensoes("HASTE DE ATERRAMENTO EM COBRE 2 METROS")
    assert extrair_dimensoes("HASTE DE ATERRAMENTO 1.20 MT") == \
        extrair_dimensoes("HASTE ATERRAMENTO 1,2 MT")


def test_compensado_duas_medidas_decimais_casam():
    a = extrair_dimensoes("COMPENSADO NAVAL PLASTIFICADO 2.20M X 1.10M")
    b = extrair_dimensoes("COMPENSADO NAVAL 2,2M X 1,1M")
    assert not _algum_eixo_diverge(a, b)


def test_largura_l_igual_nao_vira_litros():
    """'L=' é LARGURA no catálogo real ('chapa 26 l=0.70') — não litros."""
    dims = extrair_dimensoes("BOBINA ZINCADA CHAPA 26 L=0.70")
    assert all(eixo != "l" for eixo, _ in dims)


def test_gr_de_graus_nao_vira_kg():
    """'JOELHO 90 GR' — GR é graus no domínio, não gramas."""
    assert extrair_dimensoes("JOELHO PVC SOLDAVEL 90 GR") == set()
