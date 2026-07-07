"""
Testes de regressão para dois bugs do extrator (achados em revisão jun/2026):

  #2a — `_para_numero` tratava '.' como decimal mesmo quando o ponto é, na
        verdade, separador de milhar brasileiro. '1.500' virava 1.5,
        '15.000' virava 15.0, '1.000.000' virava None. Quantidades de
        edital ficavam 1000x menores, em silêncio.

  #4  — `_identifica_cabecalho_multilinha` escolhia o n (1..3) que produzia
        o MAIOR mapa. Quando o cabeçalho real era de 1 linha mas a 1ª linha
        de DADOS continha por acaso 'unidade'/'valor unitário'/'código', a
        concatenação n=2 ficava "maior" e ENGOLIA a 1ª linha de dados —
        perda silenciosa do primeiro item.

Sem rede, navegador ou Streamlit: exercitam funções puras do módulo.
"""
from __future__ import annotations

import pytest

from pregoes.extrator import (
    _identifica_cabecalho_multilinha,
    _para_numero,
)


# ---------------------------------------------------------------------------
# #2a — _para_numero: ponto como separador de milhar vs decimal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "entrada, esperado",
    [
        # --- casos que FALHAVAM antes do fix (ponto = milhar) ---
        ("1.500", 1500.0),        # antes: 1.5
        ("15.000", 15000.0),      # antes: 15.0
        ("R$ 2.300", 2300.0),     # antes: 2.3
        ("1.000.000", 1000000.0), # antes: None
        ("2.300", 2300.0),        # antes: 2.3
        # --- casos que DEVEM continuar iguais (regressão) ---
        ("1.5", 1.5),             # 1 dígito após ponto = decimal
        ("2.30", 2.30),           # 2 dígitos após ponto = decimal (não milhar)
        ("1.234,56", 1234.56),    # vírgula presente: ponto é milhar
        ("1234,56", 1234.56),     # só vírgula = decimal
        ("R$ 12,50", 12.50),
        ("42", 42.0),             # inteiro simples
        ("0", 0.0),
    ],
)
def test_para_numero_milhar_vs_decimal(entrada, esperado):
    assert _para_numero(entrada) == pytest.approx(esperado)


def test_para_numero_milhao_nao_vira_none():
    """O caso mais grave: '1.000.000' virava None (item sem quantidade)."""
    assert _para_numero("1.000.000") == pytest.approx(1000000.0)


def test_para_numero_preserva_floats_e_ints_nativos():
    assert _para_numero(1500) == 1500.0
    assert _para_numero(1.5) == 1.5


def test_para_numero_vazio_e_nulo():
    assert _para_numero(None) is None
    assert _para_numero("") is None


# ---------------------------------------------------------------------------
# #4 — cabeçalho multilinha: menor n válido vence, não engole 1ª linha de dados
# ---------------------------------------------------------------------------

def test_cabecalho_de_uma_linha_nao_engole_primeira_linha_de_dados():
    """Cabeçalho real tem 1 linha (Descrição + Quantidade). A 1ª linha de
    DADOS traz a palavra 'unidade' dentro da descrição. Antes do fix, a
    concatenação n=2 ganhava uma coluna 'unidade' e era escolhida, fazendo
    o extrator consumir a 1ª linha de dados como cabeçalho (perdia o item).
    """
    tabela = [
        ["Descrição", "Quantidade"],                       # cabeçalho REAL (n=1)
        ["Tubo PVC unidade vendida avulsa", "1.500"],      # 1º DADO (tem 'unidade')
        ["Conexão de 90 graus", "200"],                    # 2º dado
    ]

    mapa, n = _identifica_cabecalho_multilinha(tabela)

    # menor n válido = 1; consome só a linha de cabeçalho
    assert n == 1, "deveria parar no menor n válido (cabeçalho de 1 linha)"
    assert "descricao" in mapa and "quantidade" in mapa
    # a coluna 'unidade' NÃO deve aparecer só porque a 1ª linha de dados a continha
    assert "unidade" not in mapa


def test_cabecalho_multilinha_legitimo_ainda_consome_duas_linhas():
    """Regressão: quando o cabeçalho REALMENTE quebra em 2 linhas e a 1ª
    linha sozinha NÃO é válida (falta quantidade), o extrator ainda precisa
    crescer n até montar descrição+quantidade."""
    tabela = [
        ["Descrição", "Quant."],   # 'Quant.' isolado não casa? casa -> ver abaixo
        ["do produto", "total"],
        ["Tubo PVC 100mm", "1.500"],
    ]
    # Aqui n=1 já casa (Descrição + Quant.), então deve parar em 1.
    mapa, n = _identifica_cabecalho_multilinha(tabela)
    assert n == 1
    assert "descricao" in mapa and "quantidade" in mapa


def test_cabecalho_quebrado_de_verdade_cresce_n():
    """Cabeçalho onde a coluna de quantidade SÓ aparece ao concatenar 2
    linhas (palavra 'Quant.' está na 2ª linha). n=1 é inválido (sem
    quantidade), n=2 é válido -> deve consumir 2 linhas."""
    tabela = [
        ["Descrição", "Eleiçã"],     # n=1: só descrição -> inválido
        ["do item", "Quant."],       # concatenado vira "Eleiçã Quant." -> casa quantidade
        ["Tubo PVC 100mm", "1.500"],
    ]
    mapa, n = _identifica_cabecalho_multilinha(tabela)
    assert n == 2, "quantidade só surge ao concatenar 2 linhas"
    assert "descricao" in mapa and "quantidade" in mapa


def test_sem_cabecalho_valido_mantem_contrato_vazio():
    """Contrato preservado: nenhuma das linhas iniciais forma um cabeçalho
    válido -> ({}, 0), para o chamador cair no fallback."""
    tabela = [
        ["Nome", "Telefone"],
        ["João", "9999-0000"],
    ]
    mapa, n = _identifica_cabecalho_multilinha(tabela)
    assert mapa == {}
    assert n == 0


def test_tabela_vazia_retorna_contrato_vazio():
    assert _identifica_cabecalho_multilinha([]) == ({}, 0)


# --- Achado alto nº 2 (auditoria 2026-06): célula multi-linha/mesclada ------
# '10\n20' (dois números numa célula, comum quando o pdfplumber não separa
# linhas mescladas) virava 1020.0 — quantidade errada em ordens de grandeza,
# com cara de válida (passa no score). Agora devolve None: a linha perde
# score, vira 'duvidoso' e força revisão humana.

def test_dois_numeros_na_celula_quebra_de_linha_rejeitado():
    assert _para_numero("10\n20") is None


def test_dois_numeros_na_celula_espaco_rejeitado():
    assert _para_numero("10 20") is None


def test_moeda_com_espaco_apos_cifrao_segue_valida():
    assert _para_numero("R$ 1.234,56") == pytest.approx(1234.56)


def test_numero_unico_com_espacos_nas_pontas_segue_valido():
    assert _para_numero("  15  ") == 15.0


def test_para_int_tambem_rejeita_celula_com_dois_numeros():
    from pregoes.extrator import _para_int
    assert _para_int("10\n20") is None


# === Deduplicação de itens repetidos (achado de campo 2026-06-25) ===========

def _it_dedup(num, desc, q, u, v):
    from pregoes.extrator import ItemExtraido
    return ItemExtraido(item_numero=num, descricao=desc, unidade=u,
                        quantidade=q, valor_referencia=v)


def test_dedup_colapsa_copia_mal_parseada_mantendo_a_boa():
    """Termo de referência (bom) + modelo de proposta desalinhado (mesma chave)
    → fica 1 item, com a leitura COMPLETA."""
    from pregoes.extrator import _deduplicar_itens
    bom = _it_dedup(31, "BASCULANTE ANODIZADO", 20, "M2", 280)
    blank = _it_dedup(31, "BASCULANTE ANODIZADO", None, "", 20)  # desalinhado
    out = _deduplicar_itens([bom, blank])
    assert len(out) == 1
    assert (out[0].quantidade, out[0].unidade, out[0].valor_referencia) == (20, "M2", 280)


def test_dedup_nao_funde_itens_distintos_de_mesma_descricao():
    """SKUs diferentes (#150/#151) com descrição idêntica mas CÓDIGO e preço
    diferentes NÃO podem ser fundidos."""
    from pregoes.extrator import _deduplicar_itens
    a = _it_dedup(150, "LACA SELADORA PARA MADEIRA", 50, "UN", 120)
    b = _it_dedup(151, "LACA SELADORA PARA MADEIRA", 50, "UN", 40)
    out = _deduplicar_itens([a, b])
    assert len(out) == 2
    assert {i.valor_referencia for i in out} == {120, 40}


def test_dedup_mesmo_codigo_quantidades_distintas_mantem_separado():
    """Mesmo item em LOTES diferentes (mesmo código/descrição, quantidades
    distintas) são linhas distintas — não funde."""
    from pregoes.extrator import _deduplicar_itens
    a = _it_dedup(5, "CIMENTO CP-II 50KG", 500, "SC", 30)
    b = _it_dedup(5, "CIMENTO CP-II 50KG", 300, "SC", 30)
    out = _deduplicar_itens([a, b])
    assert len(out) == 2
    assert {i.quantidade for i in out} == {500, 300}


def test_dedup_preserva_ordem_e_pega_a_melhor_copia_mesmo_vindo_depois():
    """A melhor cópia mantém a POSIÇÃO da 1ª ocorrência (ordem do edital), mesmo
    que a boa apareça depois da ruim."""
    from pregoes.extrator import _deduplicar_itens
    blank = _it_dedup(1, "ITEM A", None, "", 10)    # ruim, vem 1º
    outro = _it_dedup(2, "ITEM B", 3, "UN", 7)
    bom = _it_dedup(1, "ITEM A", 10, "UN", 5)       # boa, vem depois
    out = _deduplicar_itens([blank, outro, bom])
    assert [i.item_numero for i in out] == [1, 2]    # ordem preservada
    assert out[0].quantidade == 10                   # manteve a cópia boa


# === Dedup: cópia do Modelo de Proposta SEM número (achado edital PE 2026-07) ==

def test_dedup_colapsa_copia_sem_numero_herdando_o_numero_real():
    """Modelo de Proposta repete os itens SEM a coluna de número (item_numero
    None). O antigo (chave num+desc) não colapsava — (1,'x') != (None,'x') — e
    o edital saía DOBRADO. Agora a cópia sem número herda o número real e funde."""
    from pregoes.extrator import _deduplicar_itens
    termo = _it_dedup(1, "ABRACADEIRA NYLON 140X3,6MM", 10000, "UNIDADE", None)
    modelo = _it_dedup(None, "ABRACADEIRA NYLON 140X3,6MM", 10000, "UNIDADE", None)
    out = _deduplicar_itens([termo, modelo])
    assert len(out) == 1
    assert out[0].item_numero == 1           # herdou o número real
    assert out[0].quantidade == 10000


def test_dedup_copia_sem_numero_ambigua_e_descartada():
    """Se a descrição tem VÁRIOS números (SKUs homônimos), a cópia sem número é
    ambígua → descartada; os SKUs numerados permanecem separados."""
    from pregoes.extrator import _deduplicar_itens
    a = _it_dedup(150, "LACA SELADORA", 50, "UN", 120)
    b = _it_dedup(151, "LACA SELADORA", 50, "UN", 40)
    sem_num = _it_dedup(None, "LACA SELADORA", 50, "UN", None)
    out = _deduplicar_itens([a, b, sem_num])
    assert {i.item_numero for i in out} == {150, 151}   # 2 itens; a sem número saiu


def test_dedup_mesmo_numero_variante_em_branco_descarta_a_em_branco():
    """Mesmo número, descrição levemente diferente (espaço no hífen / truncada)
    e uma cópia EM BRANCO (sem qtd e sem unidade = Modelo) → fica só a completa
    (itens 45/46/112 do edital PE)."""
    from pregoes.extrator import _deduplicar_itens
    completa = _it_dedup(45, "CONECTOR CDP 10-70", 700, "UNIDADE", None)
    variante_branca = _it_dedup(45, "CONECTOR CDP 10- 70", None, "", None)
    out = _deduplicar_itens([completa, variante_branca])
    assert len(out) == 1
    assert out[0].quantidade == 700 and out[0].unidade == "UNIDADE"


def test_dedup_mesmo_numero_duas_completas_distintas_nao_funde():
    """Salvaguarda: mesmo número com DUAS cópias que têm dados (não em branco)
    NÃO é fundido às cegas (poderia ser engano de numeração)."""
    from pregoes.extrator import _deduplicar_itens
    a = _it_dedup(7, "CABO A", 10, "M", None)
    b = _it_dedup(7, "CABO B DIFERENTE", 20, "M", None)
    out = _deduplicar_itens([a, b])
    assert len(out) == 2   # ambas com dados → preserva
