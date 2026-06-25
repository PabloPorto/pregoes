"""QA ADVERSARIAL — sondas que tentam EXPOR defeitos (não são cobertura).

Uma falha aqui é um CANDIDATO a defeito real (a investigar/classificar no
relatório), não necessariamente um teste a "consertar". Organizado por área de
risco; começa pela #1 (markup + Substituição Tributária — o dinheiro).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from openpyxl import load_workbook

from pregoes.extrator import ItemExtraido
from pregoes.exportador import exportar, SelecaoFornecedor
from pregoes.tributacao import (
    eh_st, markup_para, classificar_markups, offsets_com_st, _conjunto_st,
)


# ============================================================================
# ÁREA #1 — Markup + Substituição Tributária (o dinheiro)
# ============================================================================

# --- eh_st: fronteiras de prefixo (4/6/8 vs lista real 4/5/6/7/8) -----------

def test_eh_st_cobre_posicao_de_5_digitos():
    """REGRESSÃO de D1 (corrigido 2026-06-25): a lista tem posições de 5 dígitos;
    um NCM de 8 cujo prefixo de 5 consta na lista DEVE ser ST."""
    S = _conjunto_st()
    cinco = [n for n in S if len(n) == 5
             and (n + "000") not in S and (n + "000")[:6] not in S and n[:4] not in S]
    assert cinco, "pré-condição: a lista tem posição de 5 dígitos isolada"
    ncm = cinco[0] + "000"
    assert eh_st(ncm) is True, (
        f"NCM {ncm} casa a posição de 5 dígitos '{cinco[0]}' da lista, mas "
        f"eh_st ignora o prefixo de 5 → classificado NORMAL (markup maior).")


def test_eh_st_cobre_posicao_de_7_digitos():
    """REGRESSÃO de D1: idem para posições de 7 dígitos (8409.9 etc.)."""
    S = _conjunto_st()
    sete = [n for n in S if len(n) == 7
            and (n + "0") not in S and (n + "0")[:6] not in S and n[:4] not in S]
    assert sete, "pré-condição: a lista tem posição de 7 dígitos isolada"
    ncm = sete[0] + "0"
    assert eh_st(ncm) is True, (
        f"NCM {ncm} casa a posição de 7 dígitos '{sete[0]}', mas eh_st ignora o "
        f"prefixo de 7 → NORMAL.")


def test_eh_st_casa_8_digitos_exato_e_4_digitos_familia():
    """Controle: o que eh_st DEVERIA acertar (8 exato e 4 família) ele acerta."""
    S = _conjunto_st()
    oito = next(n for n in S if len(n) == 8)
    quatro = next(n for n in S if len(n) == 4)
    assert eh_st(oito) is True
    assert eh_st(quatro + "0000") is True   # 4 dígitos cobre a família


# --- eh_st: null / vazio / malformado (não pode levantar) -------------------

def test_eh_st_none_vazio_curto():
    assert eh_st(None) is False
    assert eh_st("") is False
    assert eh_st("39") is False        # < 4 dígitos


def test_eh_st_malformado_nao_levanta():
    for v in ["abc", "39.17.40.90", "  3917  ", "3917-40", "NCM: 3917", "🙂"]:
        eh_st(v)                        # não pode levantar exceção


# --- markup_para: classificação → multiplicador --------------------------

def test_markup_para_sem_ncm_usa_normal():
    assert markup_para(None, markup_st=1.30, markup_normal=1.45) == 1.45


def test_markup_para_st_usa_menor():
    st_ncm = next(n for n in _conjunto_st() if len(n) == 8)
    assert markup_para(st_ncm, markup_st=1.30, markup_normal=1.45) == 1.30


# --- classificar_markups: precedência override x NCM, vazio, lixo ----------

def test_classificar_vazio_nao_quebra():
    markups, resumo, sem_ncm = classificar_markups({}, 1.30, 1.45)
    assert markups == {} and sem_ncm == [] and resumo["st"] == 0


def test_classificar_override_so_vale_sem_ncm():
    """Item COM NCM de ST: o automático manda; override 'Normal' obsoleto é
    ignorado (fix do bug 2026-06)."""
    st_ncm = next(n for n in _conjunto_st() if len(n) == 8)
    markups, _, _ = classificar_markups(
        {0: st_ncm}, 1.30, 1.45, overrides={0: 1.45})
    assert markups[0] == 1.30, "override não pode vencer a classificação por NCM"


def test_classificar_ncm_invalido_truthy_e_normal_caracterizacao():
    """CARACTERIZAÇÃO (não-defeito alcançável): um NCM truthy-mas-inválido vira
    'normal', NÃO sem_ncm. Inalcançável na prática — selecoes.ncm é sempre 8
    dígitos (ncm_de_card/ncm_de_texto) ou None. Documenta a borda; se um dia o
    NCM puder vir 'sujo', revisitar (deveria cair em sem_ncm)."""
    markups, _resumo, sem_ncm = classificar_markups({0: "abc"}, 1.30, 1.45)
    assert markups[0] == 1.45 and 0 not in sem_ncm


def test_offsets_com_st_degenerado_marca_todos_caracterizacao():
    """CARACTERIZAÇÃO + FLAG (menor): com st==normal, a função infere ST por
    igualdade de markup e marca TODOS como ST — a coluna SUBST. TRIBUTÁRIA passa
    a afirmar 'Sim' até para itens normais. Documentado como 'aceitável' (o
    preço fica certo), mas a coluna fica factualmente errada. Achado menor."""
    assert offsets_com_st({0: 1.45, 1: 1.45}, markup_st=1.45) == {0, 1}


# --- exportador: aplicação por OFFSET (alinhamento / fora do range) --------

def _itens(n):
    return [ItemExtraido(item_numero=i + 1, descricao=f"P{i+1}", unidade="un",
                         quantidade=1, valor_referencia=5.0) for i in range(n)]


def test_exportar_selecao_fora_do_range_e_descartada_caracterizacao(tmp_path):
    """CARACTERIZAÇÃO (não-defeito alcançável): seleção com offset >= len(itens)
    é descartada em silêncio. Desync inalcançável no fluxo — não há caminho que
    mude itens_revisados mantendo selecoes (Continuar-o-atual vai p/ busca se há
    selecoes; Voltar-à-revisão limpa selecoes). Documenta o drop defensivo."""
    sel = {5: SelecaoFornecedor(preco=99.0, codigo_fornecedor="X", marca="M")}
    dest = tmp_path / "m.xlsx"
    exportar(_itens(3), selecoes=sel, destino=dest)
    ws = load_workbook(dest).active
    precos = [ws[f"L{5 + i}"].value for i in range(3)]
    assert 99.0 not in precos    # silenciosamente descartada (não há linha 10)


def test_exportar_markup_e_st_alinham_com_o_item_certo(tmp_path):
    """Controle de alinhamento: com seleções esparsas, markup/ST caem na LINHA
    do item certo (offset == posição na lista completa)."""
    itens = _itens(4)                                  # offsets 0..3
    sel = {2: SelecaoFornecedor(preco=10.0, codigo_fornecedor="A", marca="M")}
    markups = {2: 1.30}
    com_st = {2}
    dest = tmp_path / "m.xlsx"
    exportar(itens, selecoes=sel, markups=markups, itens_com_st=com_st, destino=dest)
    ws = load_workbook(dest).active
    # offset 2 → linha 7 (LINHA_PRIMEIRO_ITEM=5). Só ela tem preço e ST=Sim.
    assert ws["L7"].value == 10.0
    assert ws["AC7"].value == "Sim"
    assert ws["AC5"].value == "Não" and ws["AC6"].value == "Não"


def test_conjunto_st_arquivo_corrompido_degrada_para_vazio(monkeypatch, tmp_path):
    """CARACTERIZAÇÃO: se ncm_st.json sumir/quebrar, _conjunto_st devolve vazio
    → NADA é ST → todos usam markup normal. Degradação segura (não derruba a
    geração) mas SILENCIOSA — nenhum aviso de que a classificação ST sumiu."""
    import pregoes.tributacao as T
    T._conjunto_st.cache_clear()
    monkeypatch.setattr(T, "_ARQ", tmp_path / "nao_existe.json")
    try:
        assert T._conjunto_st() == frozenset()
        assert T.eh_st("76090000") is False   # antes era ST, agora nada é
    finally:
        T._conjunto_st.cache_clear()           # restaura p/ os demais testes


# ============================================================================
# ÁREA #3 — Casamento de produto + retroalimentação (o núcleo sagrado)
# ============================================================================

from pregoes.normalizador import normalizar_descricao as _nz, relevancia as _R
from pregoes.matcher import melhor_aprendido_fuzzy
from pregoes.scraper import _montar_camadas


def _ap(forn, desc, cod):
    return {"fornecedor": forn, "descricao_normalizada": _nz(desc),
            "codigo_fornecedor": cod, "marca": "X", "preco": None}


def test_substituicao_textura_nao_pode_quebrar_ferramentas():
    """REGRESSÃO de D2 (corrigido 2026-06-25): a substituição agora é ESCOPADA às
    frases do revestimento ("revestimento textura"/"textura acrilica") — o
    "textura" solto de FERRAMENTAS (pincel/rolo/lixa) não vira grafiato nem casa
    o revestimento."""
    # 'pincel para textura' NÃO deveria virar grafiato nem casar o revestimento.
    assert "grafiato" not in _nz("PINCEL PARA TEXTURA")
    qs = [q for _c, q in _montar_camadas("PINCEL PARA TEXTURA")]
    assert all("grafiato" not in q for q in qs), \
        "a query mandada ao site não pode trocar 'pincel textura' por 'pincel grafiato'"
    assert _R("PINCEL PARA TEXTURA", "GRAFIATO 25KG") < 0.35, \
        "pincel de textura não pode casar o revestimento grafiato"


def test_revestimento_textura_continua_casando_grafiato():
    """Controle: o caso que a substituição EXISTE para resolver continua certo."""
    assert _R("REVESTIMENTO TEXTURA", "GRAFIATO 25KG") >= 0.35


def test_fuzzy_nao_reusa_codigo_entre_atributos_diferentes():
    """O limiar 92 protege: mesma dimensão, atributo diferente (cor/material/
    acabamento) NÃO reusa o código aprendido do outro."""
    for a, b in [("CABO FLEXIVEL 2.5MM2 PRETO", "CABO FLEXIVEL 2.5MM2 AZUL"),
                 ("ELETRODO 6013 2.5MM", "ELETRODO 6013 2.5MM INOX"),
                 ("PARAFUSO SEXTAVADO 5/16 ZINCADO", "PARAFUSO SEXTAVADO 5/16 GALVANIZADO")]:
        assert melhor_aprendido_fuzzy(b, "almeida", [_ap("almeida", a, "CODIGO_A")]) is None, \
            f"não pode reusar o código de {a!r} para {b!r} (produto diferente)"


def test_normalizacao_virgula_ponto_e_mesma_medida_caracterizacao():
    """CARACTERIZAÇÃO: 'CABO 2,5' e 'CABO 2.5' colidem no mesmo desc_norm — é o
    CORRETO (vírgula pt-BR == ponto), não uma colisão espúria."""
    assert _nz("CABO 2,5") == _nz("CABO 2.5") == "cabo 2.5"


def test_fuzzy_guards_entrada_vazia():
    assert melhor_aprendido_fuzzy("", "almeida", [_ap("almeida", "X", "1")]) is None
    assert melhor_aprendido_fuzzy("QUALQUER", "almeida", []) is None
    assert melhor_aprendido_fuzzy("QUALQUER", "", [_ap("almeida", "X", "1")]) is None


# ============================================================================
# ÁREA — Gemini judge (Camada 3, a inteligência da busca)
# Live (exploração, fora da suíte): o judge pegou o parafuso certo (rejeitou
# prego/torneira), distinguiu E6013 de E7018 (classe AWS) e do 3,25mm, e
# devolveu [] quando nada serve. Aqui travamos o que é DETERMINÍSTICO: o parser
# e a degradação graciosa (sem chamar a API).
# ============================================================================

from pregoes.extrator_llm import julgar_candidatos, _parse_indices_judge
from pregoes.scraper import ResultadoBusca as _RB


def _card(desc):
    return _RB(desc, "M", 1.0, "C", "variacao", "almeida")


def test_judge_parser_descarta_indice_alucinado_e_lixo():
    """Gemini pode alucinar índice fora do range / tipo errado → o parser
    descarta (senão o scraper faria bag[i] fora do range)."""
    assert _parse_indices_judge("[5]", total_candidatos=3) == []
    assert _parse_indices_judge("[0, 2, 99, -1]", total_candidatos=3) == [0, 2]
    assert _parse_indices_judge("[0, 2.5]", total_candidatos=3) == [0]   # float fora


def test_judge_parser_resiliente_a_markdown_e_prosa():
    assert _parse_indices_judge("```json\n[0, 1]\n```", total_candidatos=3) == [0, 1]
    assert _parse_indices_judge("A resposta correta: [0].", total_candidatos=3) == [0]
    assert _parse_indices_judge("", total_candidatos=3) == []
    assert _parse_indices_judge("não há índice", total_candidatos=3) == []
    assert _parse_indices_judge("{}", total_candidatos=3) == []


def test_judge_degrada_gracioso_sem_quebrar():
    # sem candidatos / sem descrição / sem api key → [] (nunca levanta, nunca
    # chama a API). api_key="" evita a chamada real.
    assert julgar_candidatos("PARAFUSO", [], api_key="x") == []
    assert julgar_candidatos("", [_card("X")], api_key="x") == []
    assert julgar_candidatos("PARAFUSO", [_card("X")], api_key="") == []
