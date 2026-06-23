"""Camada 4 plena: reuso FUZZY do aprendizado (descrição nova ≈ já ensinada).

Pedido do cliente: o sistema deve se retroalimentar — quando uma descrição
PARECIDA com uma já escolhida aparece, reusar o produto direto (pelo código).
Antes só reusava em match EXATO. O gate de dimensão impede reuso entre medidas
diferentes (não reusar 'válvula 7/8' para 'válvula 1.1/4').
"""
from __future__ import annotations

from pregoes.normalizador import normalizar_descricao
from pregoes.matcher import melhor_aprendido_fuzzy


def _ap(forn, desc, cod):
    """Simula uma linha de db.aprendidos_carregar (acesso por chave)."""
    return {"fornecedor": forn,
            "descricao_normalizada": normalizar_descricao(desc),
            "codigo_fornecedor": cod, "marca": None, "preco": None}


APRENDIDOS = [
    _ap("almeida", "VALVULA LAVATORIO REBOUCAS 7/8", "46439"),
    _ap("dantas", "JOELHO PVC SOLDAVEL 25MM", "5004"),
]


def test_reusa_descricao_parecida():
    m = melhor_aprendido_fuzzy("VALVULA P/ LAVATORIO REBOUCAS 7/8",
                               "almeida", APRENDIDOS)
    assert m is not None and m["codigo_fornecedor"] == "46439"


def test_nao_reusa_se_dimensao_diverge():
    # Nome muito parecido, mas 1.1/4 != 7/8 → o gate de dimensão barra o reuso.
    assert melhor_aprendido_fuzzy("VALVULA LAVATORIO 1.1/4",
                                  "almeida", APRENDIDOS) is None


def test_so_mesmo_fornecedor():
    # O aprendido do joelho é do Dantas; buscar no Almeida não reusa.
    assert melhor_aprendido_fuzzy("JOELHO PVC SOLDAVEL 25MM",
                                  "almeida", APRENDIDOS) is None
    # Mesmo fornecedor + descrição equivalente ("25 MM" ≡ "25mm") → reusa.
    assert melhor_aprendido_fuzzy("JOELHO PVC SOLDAVEL 25 MM",
                                  "dantas", APRENDIDOS) is not None


def test_nao_reusa_descricao_diferente():
    assert melhor_aprendido_fuzzy("PARAFUSO SEXTAVADO ACO INOX",
                                  "dantas", APRENDIDOS) is None


def test_entradas_vazias():
    assert melhor_aprendido_fuzzy("QUALQUER", "almeida", []) is None
    assert melhor_aprendido_fuzzy("", "almeida", APRENDIDOS) is None


# === Auditoria 2026-06 (achado alto nº 4): gate de medida na Camada 4 =======

def test_fuzzy_nao_reusa_codigo_de_peso_errado():
    """Cimento 50KG aprendido NÃO pode ser reusado para edital de 25KG —
    mesmo com descrição quase idêntica (fuzzy altíssimo), o gate de
    dimensão (eixo kg) barra o reuso."""
    aprendidos = [{
        "descricao_normalizada": "cimento portland cp ii saco 50kg",
        "fornecedor": "almeida", "codigo_fornecedor": "111",
        "marca": "POTY",
    }]
    assert melhor_aprendido_fuzzy(
        "CIMENTO PORTLAND CP II SACO 25KG", "almeida", aprendidos) is None


def test_fuzzy_continua_reusando_quando_medida_bate():
    aprendidos = [{
        "descricao_normalizada": "cimento portland cp ii saco 50kg",
        "fornecedor": "almeida", "codigo_fornecedor": "111",
        "marca": "POTY",
    }]
    achado = melhor_aprendido_fuzzy(
        "CIMENTO PORTLAND CP II SC 50KG", "almeida", aprendidos)
    assert achado is not None and achado["codigo_fornecedor"] == "111"


def test_fuzzy_reusa_com_milhar_ptbr_no_catalogo():
    """Verificação adversarial 2026-06: aprendizado salvo como '1.000 lt'
    (milhar pt-BR, linha real) TEM que ser reusado para edital '1000 LITROS'
    — o gate de dimensão compara valor numérico, não string."""
    aprendidos = [{
        "descricao_normalizada": "caixa dagua polietileno 1.000 lt",
        "fornecedor": "almeida", "codigo_fornecedor": "777",
        "marca": "FORTLEV",
    }]
    achado = melhor_aprendido_fuzzy(
        "CAIXA DAGUA POLIETILENO 1000 LITROS", "almeida", aprendidos)
    assert achado is not None and achado["codigo_fornecedor"] == "777"


# === Regressão do fix de eixos elétricos (2026-06-23) =======================
# O gate de dimensão do reuso fuzzy depende de extrair_dimensoes ler o valor
# CERTO. Antes, "1.500 W" virava ('w','500') e o fuzzy propagava o código de
# um 500W para um edital de 1500W como se fosse a "escolha anterior" — pior
# tipo de erro na retroalimentação (auto-seleciona produto errado).

def test_fuzzy_nao_propaga_codigo_de_watt_errado():
    aprendidos = [{
        "fornecedor": "almeida",
        "descricao_normalizada": normalizar_descricao("SECADOR PROFISSIONAL 500 W"),
        "codigo_fornecedor": "C500", "marca": "X", "preco": None,
    }]
    # 1.500W (1500) != 500W → o gate de dimensão barra o reuso.
    assert melhor_aprendido_fuzzy(
        "SECADOR PROFISSIONAL 1.500 W", "almeida", aprendidos) is None
    # Mesma potência → reusa (não-regressão).
    assert melhor_aprendido_fuzzy(
        "SECADOR PROFISSIONAL 500 W", "almeida", aprendidos) is not None


def test_fuzzy_reusa_quando_so_muda_zero_decimal():
    # '5MM' (edital) ≡ '5.0MM' (catálogo) — normalização une, gate não diverge.
    aprendidos = [{
        "fornecedor": "almeida",
        "descricao_normalizada": normalizar_descricao("PARAFUSO SEXTAVADO 5.0MM"),
        "codigo_fornecedor": "B", "marca": "X", "preco": None,
    }]
    m = melhor_aprendido_fuzzy("PARAFUSO SEXTAVADO 5MM", "almeida", aprendidos)
    assert m is not None and m["codigo_fornecedor"] == "B"


def test_fuzzy_isola_por_fornecedor_explicito():
    aprendidos = [{
        "fornecedor": "almeida",
        "descricao_normalizada": normalizar_descricao("PARAFUSO 5MM"),
        "codigo_fornecedor": "AAA", "marca": "X", "preco": None,
    }]
    assert melhor_aprendido_fuzzy("PARAFUSO 5MM", "dantas", aprendidos) is None
    assert melhor_aprendido_fuzzy("PARAFUSO 5MM", "almeida", aprendidos) is not None


def test_fuzzy_rejeita_produto_diferente_mesma_palavra():
    # 'válvula' compartilhada, mas produto totalmente diferente → abaixo de 92.
    aprendidos = [{
        "fornecedor": "almeida",
        "descricao_normalizada": normalizar_descricao("VALVULA LAVATORIO REBOUCAS 7/8"),
        "codigo_fornecedor": "C5", "marca": "X", "preco": None,
    }]
    assert melhor_aprendido_fuzzy("VALVULA P/ COZINHA INOX", "almeida", aprendidos) is None
