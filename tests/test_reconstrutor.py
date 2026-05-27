"""
Testes do reconstrutor de tabelas fragmentadas pelo Docling.

Cobertura:
- Caminho feliz: base + fatias com mesmo nº cols → tudo absorvido
- Fatia com cabeçalho válido distinto → para de absorver (nova seção)
- Fatia com diferença de colunas grande → mantém separada
- Fatia com cabeçalho '0','1','2'... e nº cols diferente → realinha
- Sem tabela base → devolve entrada inalterada
- Linhas vazias após realinhamento → descartadas
"""
from __future__ import annotations

from pregoes.reconstrutor_docling import (
    reconstruir,
    _encontrar_tabela_base,
    _parece_continuacao,
    _perfil_celula,
    _perfis_colunas,
    _alinhar_colunas,
)


# === Cabeçalho realista (TRE-PE Tabela #3) ===
CABECALHO_TRE_PE = [
    "Nº  Item", "Descrição do  Item", "Elemento de  Despesa", "CATMAT",
    "Unidad e", "Quant.  Ordinário", "Quant.  Eleiçã o", "Quant.",
    "Preço  Unitário  Máximo", "Valor  total",
]


def _tabela_base_realista():
    """Tabela base estilo TRE-PE com 10 colunas e 1 item de dados."""
    return [
        CABECALHO_TRE_PE,
        ["1", "Removedor de etiquetas. Especificação: ...",
         "30.22 - Material de Limpeza", "259734 Solvente",
         "Un", "0", "92", "92", "R$ 33,34", "R$ 3.067,28"],
    ]


def _fatia_continuacao_mesmo_n_cols():
    """Fatia com 10 colunas e cabeçalho '0','1','2'... — sem cabeçalho real."""
    return [
        ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
        ["15", "Adaptador de rede USB", "30.30 - material para comunicações",
         "603510 Adaptador", "Unidade", "3", "0", "3", "R$ 43,44", "R$ 130,32"],
        ["20", "Alicate de crimpagem com catraca", "30.42 - Ferramentas",
         "331040 Alicate Para Climpar", "Unidade", "7", "0", "7", "R$ 41,73", "R$ 292,11"],
    ]


# === _encontrar_tabela_base ===

def test_encontra_base_com_cabecalho_valido():
    tabelas = [_tabela_base_realista()]
    assert _encontrar_tabela_base(tabelas) == 0


def test_nao_encontra_base_quando_so_ha_tabela_vazia():
    tabelas = [
        [["0", "1", "2"], ["a", "b", "c"]],  # cabeçalho sequencial — não é base
        [["x", "y"]],  # tabela curta sem cabeçalho real
    ]
    assert _encontrar_tabela_base(tabelas) is None


def test_encontra_base_pulando_tabelas_iniciais():
    tabelas = [
        [["Anexo I"]],  # tabela com 1 célula
        _tabela_base_realista(),
    ]
    assert _encontrar_tabela_base(tabelas) == 1


# === _parece_continuacao ===

def test_continuacao_por_sequencia_numerica():
    candidata = [["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]]
    assert _parece_continuacao(candidata, base_n_cols=10) is True


def test_continuacao_por_cabecalho_vazio():
    candidata = [["", "", "", "", "", "", "", "", "", ""],
                 ["1", "x", "y", "", "Un", "", "", "1", "R$ 1,00", "R$ 1,00"]]
    assert _parece_continuacao(candidata, base_n_cols=10) is True


def test_continuacao_por_falta_de_palavras_chave():
    # Primeira linha sem palavras-chave de cabeçalho → continuação
    candidata = [["15", "Adaptador", "Material", "Cabo USB"]]
    assert _parece_continuacao(candidata, base_n_cols=5) is True


def test_nao_eh_continuacao_se_diferenca_cols_grande():
    # Tabela com 3 cols vs base com 10 → muito diferente
    candidata = [["0", "1", "2"], ["a", "b", "c"]]
    assert _parece_continuacao(candidata, base_n_cols=10) is False


# === _perfil_celula ===

def test_perfil_celula_diversos():
    assert _perfil_celula("") == "vazio"
    assert _perfil_celula(None) == "vazio"
    assert _perfil_celula("R$ 33,34") == "monetario"
    assert _perfil_celula("R$ 1.234,56") == "monetario"
    assert _perfil_celula("92") == "num_curto"
    assert _perfil_celula("259734") == "num_longo"
    assert _perfil_celula("Un") == "texto_curto"
    assert _perfil_celula("Removedor de etiquetas. Especificação detalhada do produto.") == "texto_longo"


# === _alinhar_colunas ===

def test_alinha_identidade_quando_nro_cols_igual():
    fatia = _fatia_continuacao_mesmo_n_cols()
    perfis_base = ["num_curto", "texto_longo", "texto_longo", "texto_longo",
                   "texto_curto", "num_curto", "num_curto", "num_curto",
                   "monetario", "monetario"]
    mapa = _alinhar_colunas(fatia, perfis_base, base_n_cols=10)
    assert mapa == {i: i for i in range(10)}


def test_alinha_quando_fatia_perde_uma_coluna():
    # Base 5 cols: [num, texto_longo, num, monetario, monetario]
    # Fatia 4 cols: [num, texto_longo, num, monetario] — perdeu col monetário
    fatia = [
        ["", "", "", ""],
        ["1", "Item desc longa de produto comum", "10", "R$ 1,00"],
    ]
    perfis_base = ["num_curto", "texto_longo", "num_curto", "monetario", "monetario"]
    mapa = _alinhar_colunas(fatia, perfis_base, base_n_cols=5)
    # Deve mapear i=0→j=0, i=1→j=1, i=2→j=2, i=3→j=3 (primeiro monetário)
    assert mapa[0] == 0
    assert mapa[1] == 1


# === reconstruir (caminho ponta-a-ponta) ===

def test_reconstruir_absorve_fatia_mesmo_n_cols():
    base = _tabela_base_realista()
    fatia = _fatia_continuacao_mesmo_n_cols()
    saida = reconstruir([base, fatia])

    # Após absorção, deve sobrar apenas 1 tabela: a base com as linhas da fatia
    assert len(saida) == 1
    # 1 cabeçalho + 1 item original + 2 linhas absorvidas = 4 linhas
    assert len(saida[0]) == 4
    # Última linha deve ser do item #20 ("Alicate de crimpagem...")
    assert "Alicate" in str(saida[0][-1][1])


def test_reconstruir_para_quando_aparece_nova_secao():
    base = _tabela_base_realista()
    fatia = _fatia_continuacao_mesmo_n_cols()
    # Tabela com cabeçalho válido depois (nova seção)
    nova_secao = _tabela_base_realista()
    saida = reconstruir([base, fatia, nova_secao])

    # Base absorve fatia, mas nova_secao fica separada
    assert len(saida) == 2
    assert len(saida[0]) == 4  # base + 2 linhas da fatia
    assert len(saida[1]) == 2  # nova_secao intacta


def test_reconstruir_mantem_tabela_isolada_separada():
    base = _tabela_base_realista()
    # Tabela isolada — pouca diferença de cols mas tem cabeçalho válido?
    # Aqui não tem palavras-chave; é tabela de outra coisa.
    isolada = [["0"], ["x"]]  # 1 coluna — muito diferente da base
    saida = reconstruir([base, isolada])
    assert len(saida) == 2  # ambas permanecem


def test_reconstruir_sem_base_devolve_inalterado():
    tabelas = [
        [["0", "1", "2"], ["a", "b", "c"]],
        [["x", "y", "z"]],
    ]
    saida = reconstruir(tabelas)
    assert saida == tabelas


def test_reconstruir_descarta_linhas_inteiramente_vazias():
    base = _tabela_base_realista()
    fatia_com_vazia = [
        ["", "", "", "", "", "", "", "", "", ""],  # cabeçalho vazio
        ["", "", "", "", "", "", "", "", "", ""],  # linha vazia
        ["15", "Adaptador", "30.30 - material", "603510",
         "Unidade", "3", "0", "3", "R$ 43,44", "R$ 130,32"],
    ]
    saida = reconstruir([base, fatia_com_vazia])
    # 1 cabeçalho + 1 item original + 1 linha real = 3 linhas (vazia descartada)
    assert len(saida[0]) == 3


def test_reconstruir_entrada_vazia():
    assert reconstruir([]) == []


def test_reconstruir_absorve_fatia_que_comeca_com_dado_real():
    """Caso TRE-PE: Docling fragmenta tabela e a fatia começa direto com
    o item (sem cabeçalho fake). Critério D ('perfil casa com a base')
    deve detectar e absorver TODAS as linhas (não pular a primeira)."""
    base = _tabela_base_realista()
    # Fatia que começa com item 15 (já é dado, não cabeçalho)
    fatia_so_dados = [
        ["15", "Adaptador de rede USB Rj45 Garantia mínima de 12 meses",
         "30.30 - material para comunicações", "603510 Adaptador",
         "Unidade", "3", "0", "3", "R$ 43,44", "R$ 130,32"],
        ["20", "Alicate de crimpagem com catraca compatível",
         "30.42 - Ferramentas", "331040 Alicate Para Climpar",
         "Unidade", "7", "0", "7", "R$ 41,73", "R$ 292,11"],
    ]
    saida = reconstruir([base, fatia_so_dados])

    assert len(saida) == 1
    # base: cabeçalho + item 1 = 2. + fatia (2 linhas, ambas reais) = 4
    assert len(saida[0]) == 4
    # Última linha é o item 20
    assert "Alicate" in str(saida[0][-1][1])
