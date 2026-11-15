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


# ---------------------------------------------------------------------------
# Cabeçalho mesclado e linha truncada (edital de Catingueira 25/2026, campo
# 2026-10): 64 de 67 itens saíam sem nº e sem quantidade, e o item 46 sumia.
# ---------------------------------------------------------------------------

# Como o pdfplumber devolve a tabela: 'ITEM' e 'QUANT.' ocupam 2 colunas da
# grade (None = coluna coberta pela mesclagem) e o valor cai ora numa, ora
# noutra.
_CAB_MESCLADO = ['ITEM', None, 'ESPECIFICAÇÃO', 'UNID', 'QUANT.', None,
                 'Preço\nmáximo', 'TOTAL']


def _tabela(pagina, linhas):
    from pregoes.extrator import _TabelaBruta
    return _TabelaBruta(pagina=pagina, indice_pagina=0, linhas=linhas)


def test_cabecalho_mesclado_le_numero_e_quantidade_das_duas_colunas():
    from pregoes.extrator import processar_tabelas_brutas
    pag1 = _tabela(19, [
        _CAB_MESCLADO,
        ['', '1', "ALICATE UNIVERSAL 8'' ( SIMPLES )", 'Unidade', '', '15', 'R$ 43,77', 'R$ 656,55'],
        ['3', None, 'ALAVANCA DE FERRO PEDREIRO 1 X 1,50 METROS', 'Unidade', '6', None, 'R$ 159,99', 'R$ 959,94'],
    ])
    # Continuação na página seguinte: sem cabeçalho, herda mapa E reservas.
    pag2 = _tabela(20, [
        ['', '47', 'PICARETA EM AÇO', 'Unidade', '', '10', 'R$ 102,87', 'R$ 1.028,70', None, None],
    ])
    r = processar_tabelas_brutas([pag1, pag2], arquivo="x.pdf")
    lidos = [(i.item_numero, i.quantidade) for i in r.itens]
    assert lidos == [(1, 15.0), (3, 6.0), (47, 10.0)]


def test_cabecalho_mesclado_nunca_le_a_coluna_vizinha_com_cabecalho_proprio():
    """Sem quantidade em nenhuma das colunas de QUANT., o preço ao lado NÃO
    pode virar quantidade."""
    from pregoes.extrator import _colunas_mescladas, _extrair_item_da_linha
    mapa = {"codigo_orgao": 0, "descricao": 2, "unidade": 3, "quantidade": 4,
            "valor_referencia": 6}
    reservas = _colunas_mescladas([_CAB_MESCLADO], mapa)
    assert reservas == {"codigo_orgao": (1,), "quantidade": (5,)}
    it = _extrair_item_da_linha(
        ['', '9', 'BROXA P/PINTURA 15 CM', 'Unidade', '', '', 'R$ 9,23', ''],
        mapa, 19, reservas)
    assert it.item_numero == 9 and it.quantidade is None


def test_cabecalho_sem_mesclagem_nao_cria_reserva():
    from pregoes.extrator import _colunas_mescladas
    cab = ['ITEM', 'DESCRIÇÃO', 'UNID', 'QUANT.', 'VALOR UNIT.']
    mapa = {"codigo_orgao": 0, "descricao": 1, "unidade": 2, "quantidade": 3,
            "valor_referencia": 4}
    assert _colunas_mescladas([cab], mapa) == {}


def _chars(texto, x0, top, larg=5.0, alt=9.0):
    """Caracteres no formato do pdfplumber (só o que _texto_na_area usa)."""
    out = []
    for k, ch in enumerate(texto):
        cx = x0 + k * larg
        out.append({"text": ch, "x0": cx, "x1": cx + larg, "top": top,
                    "bottom": top + alt, "upright": True, "size": alt,
                    "doctop": top, "fontname": "F", "adv": larg, "matrix": (1, 0, 0, 1, cx, top)})
    return out


def test_linha_truncada_pela_quebra_de_pagina_e_reconstruida():
    """1ª linha da página com a grade incompleta: só as células das 2 primeiras
    colunas existem; o resto é lido na posição das colunas das demais linhas."""
    from pregoes.extrator import _reparar_linhas_truncadas
    cheia = [(45, 88, 49, 99), (49, 88, 73, 99), (73, 88, 325, 99),
             (325, 88, 373, 99), (373, 88, 418, 99)]
    celulas = [
        [(45, 77, 49, 88), (49, 77, 73, 88), None, None, None],   # truncada
        cheia,
    ]
    faixas = [(45, 77, 418, 88), (45, 88, 418, 99)]
    linhas = [['', '46', None, None, None], ['', '47', 'PICARETA', 'Unidade', '10']]
    chars = (_chars("PENEIRA", 80, 78) + _chars("Unidade", 330, 78)
             + _chars("10", 390, 78))
    out = _reparar_linhas_truncadas(linhas, celulas, faixas, chars)
    assert out[0] == ['', '46', 'PENEIRA', 'Unidade', '10']
    assert out[1] == ['', '47', 'PICARETA', 'Unidade', '10']   # intacta


def test_linha_com_celula_mesclada_nao_e_quebrada_em_colunas():
    """Título que ocupa a linha toda (célula única cobrindo as colunas) fica
    como está — não é linha truncada."""
    from pregoes.extrator import _reparar_linhas_truncadas
    cheia = [(45, 88, 73, 99), (73, 88, 325, 99), (325, 88, 418, 99)]
    celulas = [[(45, 77, 418, 88), None, None], cheia]
    faixas = [(45, 77, 418, 88), (45, 88, 418, 99)]
    linhas = [['LOTE 1 - FERRAMENTAS', None, None], ['1', 'PICARETA', '10']]
    chars = _chars("LOTE 1 - FERRAMENTAS", 100, 78)
    out = _reparar_linhas_truncadas(linhas, celulas, faixas, chars)
    assert out[0] == ['LOTE 1 - FERRAMENTAS', None, None]


def test_celula_mesclada_na_vertical_nao_e_picotada():
    """Descrição de várias linhas que desce sobre a linha seguinte (rowspan):
    a 2ª linha vem sem a célula da descrição, mas ela está COBERTA pela de
    cima — ler a faixa da 2ª linha picotava a descrição (regressão vista em
    edital real ao validar o reparo)."""
    from pregoes.extrator import _reparar_linhas_truncadas
    celulas = [
        [(45, 77, 73, 88), (73, 77, 325, 99), (325, 77, 373, 88)],   # descrição alta
        [(45, 88, 73, 99), None, (325, 88, 373, 99)],
        [(45, 99, 73, 110), (73, 99, 325, 110), (325, 99, 373, 110)],
    ]
    faixas = [(45, 77, 373, 99), (45, 88, 373, 99), (45, 99, 373, 110)]
    linhas = [['1', 'DOBRADIÇA DE 04 POLEGADAS (REFORÇADA', '982'],
              ['', None, ''],
              ['2', 'CILINDRO', '10']]
    chars = _chars("PAREDE GROSSA)", 80, 89)
    out = _reparar_linhas_truncadas(linhas, celulas, faixas, chars)
    assert out[1] == ['', None, '']


# ---------------------------------------------------------------------------
# Guarnição de João Pessoa 62/2026 (campo 2026-11): 138 páginas, 0 itens —
# quantidade só na coluna 'Soma', lista do órgão participante sem cabeçalho e
# com outra largura, e 60 páginas finais de estimativas por quartel.
# ---------------------------------------------------------------------------

_CAB_GUARNICAO = ['ITEM', 'DESCRIÇÃO/ESPECIFICAÇÃO', 'UNID', 'CATMAT', 'B Adm Gu\nJP',
                  '15º BI Mtz', 'HGuJP', 'Soma', 'VALOR ESTIMADO', None]


def _linha_guarnicao(n, desc, unid, qtd, preco):
    return [str(n), desc, unid, '243048', '1', '2', '0', str(qtd), preco, 'R$ 1,00']


def test_quantidade_na_coluna_soma_e_preco_em_valor_estimado():
    from pregoes.extrator import processar_tabelas_brutas
    t = _tabela(1, [
        _CAB_GUARNICAO,
        [None] * 8 + ['UNITÁRIO', 'TOTAL ITEM'],
        _linha_guarnicao(1, 'TUBO METALON, FORMATO RETANGULAR', 'PEÇA', 251, 'R$ 81,83'),
        _linha_guarnicao(2, 'CANTONEIRA METAL FERROSO', 'BARRA', 286, 'R$ 67,60'),
        _linha_guarnicao(3, 'ELETRODO SOLDA E-6013', 'KG', 263, 'R$ 25,00'),
    ])
    r = processar_tabelas_brutas([t], arquivo="x.pdf")
    assert [(i.item_numero, i.quantidade, i.valor_referencia) for i in r.itens] == [
        (1, 251.0, 81.83), (2, 286.0, 67.6), (3, 263.0, 25.0)]


def test_soma_nao_vence_coluna_quantidade():
    """'soma' é fraca: com uma coluna 'Quantidade' de verdade, ela manda."""
    from pregoes.extrator import _identifica_cabecalho_em_linha
    mapa = _identifica_cabecalho_em_linha(['Item', 'Descrição', 'Quantidade', 'Soma'])
    assert mapa["quantidade"] == 2


def test_descricao_que_quebra_a_pagina_e_emendada_no_item():
    from pregoes.extrator import processar_tabelas_brutas
    pag1 = _tabela(1, [
        _CAB_GUARNICAO,
        _linha_guarnicao(1, 'TUBO METALON, FORMATO RETANGULAR', 'PEÇA', 251, 'R$ 81,83'),
        _linha_guarnicao(2, 'CANTONEIRA METAL FERROSO', 'BARRA', 286, 'R$ 67,60'),
        _linha_guarnicao(5, 'MASSA PLÁSTICA, COMPOSIÇÃO BÁSICA', 'PACOTE 100', 226, 'R$ 12,85'),
    ])
    pag2 = _tabela(2, [
        ['', 'COM CATALISADOR, APLICAÇÃO CERÂMICAS.', 'UND', '', '', '', '', '', '', ''],
        _linha_guarnicao(6, 'ELETRODO SOLDA E-6013', 'KG', 263, 'R$ 25,00'),
    ])
    r = processar_tabelas_brutas([pag1, pag2], arquivo="x.pdf")
    assert [i.item_numero for i in r.itens] == [1, 2, 5, 6]
    assert r.itens[2].descricao == 'MASSA PLÁSTICA, COMPOSIÇÃO BÁSICA COM CATALISADOR, APLICAÇÃO CERÂMICAS.'
    assert r.itens[2].unidade == 'PACOTE 100 UND'


def test_linha_sem_numero_nao_e_emendada_quando_tabela_nao_tem_coluna_de_numero():
    """Sem coluna de nº, uma linha sem quantidade pode ser item real com dado
    faltando — não some dentro do item anterior."""
    from pregoes.extrator import processar_tabelas_brutas
    cab = ['DESCRIÇÃO', 'UNID', 'QUANTIDADE']
    pag1 = _tabela(1, [cab, ['ALICATE UNIVERSAL 8 POL', 'UND', '10'],
                       ['MARTELO UNHA 27MM', 'UND', '5'], ['TRENA 5 METROS', 'UND', '8']])
    pag2 = _tabela(2, [['SERROTE 22 POLEGADAS', '', ''], ['NIVEL DE BOLHA 12', 'UND', '3']])
    r = processar_tabelas_brutas([pag1, pag2], arquivo="x.pdf")
    assert 'SERROTE 22 POLEGADAS' not in r.itens[2].descricao


def test_continuacao_com_menos_colunas_realinha_pela_direita():
    """Lista do órgão participante: sem cabeçalho e sem as colunas por quartel —
    QTD|UNITÁRIO|TOTAL ficam coladas à direita."""
    from pregoes.extrator import processar_tabelas_brutas
    pag1 = _tabela(1, [
        _CAB_GUARNICAO,
        _linha_guarnicao(1, 'TUBO METALON, FORMATO RETANGULAR', 'PEÇA', 251, 'R$ 81,83'),
        _linha_guarnicao(2, 'CANTONEIRA METAL FERROSO', 'BARRA', 286, 'R$ 67,60'),
        _linha_guarnicao(3, 'ELETRODO SOLDA E-6013', 'KG', 263, 'R$ 25,00'),
    ])
    pag2 = _tabela(2, [
        ['261', 'MASSA PLÁSTICA, COMPOSIÇÃO BÁSICA', 'LATA', '238514', '10', 'R$ 12,85', 'R$ 128,50'],
        ['262', 'ELETRODO SOLDA E-6013', 'KG', '431110', '10', 'R$ 25,00', 'R$ 250,00'],
        ['263', 'WASH PRIMER ANTICORROSIVO', 'UND', '454883', '5', 'R$ 185,00', 'R$ 925,00'],
    ])
    r = processar_tabelas_brutas([pag1, pag2], arquivo="x.pdf")
    assert [(i.item_numero, i.unidade, i.quantidade, i.valor_referencia) for i in r.itens[3:]] == [
        (261, 'LATA', 10.0, 12.85), (262, 'KG', 10.0, 25.0), (263, 'UND', 5.0, 185.0)]


def test_tabela_com_cabecalho_proprio_sem_quantidade_quebra_a_heranca():
    """Estimativas por quartel (mínimo/máximo, sem preço): cabeçalho próprio
    que não é de itens — ela e as páginas seguintes não repetem os itens."""
    from pregoes.extrator import processar_tabelas_brutas
    pag1 = _tabela(1, [
        _CAB_GUARNICAO,
        _linha_guarnicao(1, 'TUBO METALON, FORMATO RETANGULAR', 'PEÇA', 251, 'R$ 81,83'),
        _linha_guarnicao(2, 'CANTONEIRA METAL FERROSO', 'BARRA', 286, 'R$ 67,60'),
        _linha_guarnicao(3, 'ELETRODO SOLDA E-6013', 'KG', 263, 'R$ 25,00'),
    ])
    estimativas = ['ITEM', 'DESCRIÇÃO/ ESPECIFICAÇÃO', 'UNID', 'B Adm Gu JP', None,
                   '15º BIMTZ', None, 'HGU', None, 'MARINHA']
    pag2 = _tabela(2, [estimativas,
                       [None, None, None] + ['Req.\nMínima', 'Req.\nMáxima'] * 3 + ['Req.\nMínima'],
                       ['1', 'TUBO METALON, FORMATO RETANGULAR', 'PEÇA', '10', '20', '20', '40', '0', '0', '3']])
    pag3 = _tabela(3, [['2', 'CANTONEIRA METAL FERROSO', 'BARRA', '13', '25', '40', '80', '0', '0', '3']])
    r = processar_tabelas_brutas([pag1, pag2, pag3], arquivo="x.pdf")
    assert [(i.item_numero, i.quantidade) for i in r.itens] == [(1, 251.0), (2, 286.0), (3, 263.0)]


def test_linha_com_grade_propria_e_relida_pelas_celulas_reais():
    """Emenda entre a lista geral e a do participante: a linha do item 261
    vem na grade da tabela anterior, com None onde estão quantidade e preço."""
    from pregoes.extrator import processar_tabelas_brutas
    pag1 = _tabela(1, [
        _CAB_GUARNICAO,
        _linha_guarnicao(1, 'TUBO METALON, FORMATO RETANGULAR', 'PEÇA', 251, 'R$ 81,83'),
        _linha_guarnicao(2, 'CANTONEIRA METAL FERROSO', 'BARRA', 286, 'R$ 67,60'),
        _linha_guarnicao(3, 'ELETRODO SOLDA E-6013', 'KG', 263, 'R$ 25,00'),
        ['261', 'MASSA PLÁSTICA, COMPOSIÇÃO BÁSICA', 'LATA', '238514', None, '10', None,
         None, 'R$ 12,85', 'R$ 128,50'],
    ])
    r = processar_tabelas_brutas([pag1], arquivo="x.pdf")
    it = r.itens[-1]
    assert (it.item_numero, it.unidade, it.quantidade, it.valor_referencia) == (261, 'LATA', 10.0, 12.85)


# ---------------------------------------------------------------------------
# Edital PE SRP 020/2026 (campo 2026-11): coluna única 'Item - Código -
# Descrição' — 717 itens sem número e com os códigos na frente do nome.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texto,esperado", [
    ("1 - 0056802 - 1 - 0045799 - ABRAÇADEIRA COLAR 50 X 1/2.",
     (1, "1 - 0056802 - 1 - 0045799", "ABRAÇADEIRA COLAR 50 X 1/2.")),
    ("12 - 0056813 - ADITIVO 18 L", (12, "12 - 0056813", "ADITIVO 18 L")),
    ("requer mistura, com dispositivo anti- entupimento", (None, None,
     "requer mistura, com dispositivo anti- entupimento")),
    ("1/2 POL ADAPTADOR", (None, None, "1/2 POL ADAPTADOR")),
])
def test_separar_prefixo_numerico(texto, esperado):
    from pregoes.extrator import _separar_prefixo_numerico
    assert _separar_prefixo_numerico(texto) == esperado


def test_coluna_item_codigo_descricao_separa_numero_e_emenda_continuacao():
    from pregoes.extrator import processar_tabelas_brutas
    cab = ['Item - Código - Descrição', 'Unidade', 'Quantidade', '', 'Vlr. Unit.', '']
    pag1 = _tabela(17, [
        cab, [None, None, None, None, 'Máximo', None],
        ['1 - 0056802 - 1 - 0045799 - ABRAÇADEIRA COLAR 50 X 1/2.', 'UND', '100', '', None, None],
        ['2 - 0056803 - 2 - 0045800 - ABRAÇADEIRA COLAR 50 X 3/4.', 'UND', '100', '', None, None],
        ['3 - 0056804 - 3 - 0045801 - COLA INSTANTANEA 100G, adesivo', 'UND', '150', '', None, None],
    ])
    pag2 = _tabela(18, [
        ['requer mistura, ótima qualidade.', '', '', ''],
        ['4 - 0056805 - 4 - 0045802 - ABRAÇADEIRA COLAR 60 X 3/4.', 'UND', '50', ''],
    ])
    r = processar_tabelas_brutas([pag1, pag2], arquivo="x.pdf")
    assert [(i.item_numero, i.quantidade) for i in r.itens] == [
        (1, 100.0), (2, 100.0), (3, 150.0), (4, 50.0)]
    assert r.itens[0].descricao == 'ABRAÇADEIRA COLAR 50 X 1/2.'
    assert r.itens[2].descricao == 'COLA INSTANTANEA 100G, adesivo requer mistura, ótima qualidade.'
    assert r.confianca_global == 1.0
