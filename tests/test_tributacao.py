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


def test_ncm_de_card():
    assert tributacao.ncm_de_card("39174090") == "39174090"
    assert tributacao.ncm_de_card("3917.40.90") == "39174090"
    assert tributacao.ncm_de_card(None) is None
    assert tributacao.ncm_de_card("12345") is None     # não tem 8 dígitos


def test_ncm_de_texto_pagina_detalhe():
    # Como aparece na página do fornecedor (imagens Almeida/Dantas).
    assert tributacao.ncm_de_texto("Código NCM: 39174090") == "39174090"
    assert tributacao.ncm_de_texto("... Código NCM: 8544.42.00 ...") == "85444200"
    assert tributacao.ncm_de_texto("sem ncm aqui") is None
    assert tributacao.ncm_de_texto(None) is None


def test_classificar_markups():
    # 0=ST (3917), 1=normal, 2=sem NCM
    ncms = {0: "39174090", 1: "99999999", 2: None}
    markups, resumo, sem = tributacao.classificar_markups(ncms, 1.30, 1.45)
    assert markups == {0: 1.30, 1: 1.45, 2: 1.45}
    assert resumo == {"st": 1, "normal": 1, "sem_ncm": 1}
    assert sem == [2]


def test_classificar_markups_override_e_knobs():
    ncms = {0: "39174090", 1: None}
    # operador troca os knobs do pregão e força o item 1 (sem NCM) como ST
    markups, _, _ = tributacao.classificar_markups(
        ncms, markup_st=1.25, markup_normal=1.5, overrides={1: 1.25})
    assert markups == {0: 1.25, 1: 1.25}


def test_markup_por_classificacao():
    assert tributacao.markup_para("39174090") == config.MARKUP_ST      # 1.30
    assert tributacao.markup_para("99999999") == config.MARKUP_NORMAL  # 1.45
    assert tributacao.markup_para(None) == config.MARKUP_NORMAL
    # defaults editáveis por pregão
    assert tributacao.markup_para("39174090", markup_st=1.25) == 1.25
    assert tributacao.markup_para("99999999", markup_normal=1.5) == 1.5


def test_override_obsoleto_nao_vence_ncm_de_st():
    """Bug 2026-06 (reproduz o log do cliente): itens começam SEM NCM → o
    painel grava override 'Normal' (1.45) para todos. Depois o NCM é
    identificado (39172300/39174090 = ST). O override obsoleto NÃO pode mais
    vencer — com NCM, a classificação automática manda."""
    ncms = {0: "39172300", 1: "39174090", 2: "84818011"}
    overrides_obsoletos = {0: 1.45, 1: 1.45, 2: 1.45}   # do tempo em que eram sem-NCM
    markups, resumo, _ = tributacao.classificar_markups(
        ncms, 1.30, 1.45, overrides=overrides_obsoletos)
    assert markups == {0: 1.30, 1: 1.30, 2: 1.30}       # todos ST, não 1.45
    assert resumo["st"] == 3
    assert tributacao.offsets_com_st(markups, 1.30) == {0, 1, 2}


def test_override_ainda_vale_para_item_sem_ncm():
    """O override continua sendo o fallback de itens SEM NCM (não regrediu)."""
    ncms = {0: None, 1: None}
    markups, _, _ = tributacao.classificar_markups(
        ncms, 1.30, 1.45, overrides={0: 1.30})   # operador marcou o 0 como ST
    assert markups == {0: 1.30, 1: 1.45}         # 0=override ST, 1=default normal


# === Lacunas do Anexo V achadas EM CAMPO (2026-07-06) ========================
# Cliente: tinta acrílica Suvinil (NCM 3209.10.10, no Anexo V linha 24.001.00
# "Tintas, vernizes — NCM 3208, 3209, 3210.00") saiu SEM ST na planilha; o
# esmalte (3208.10.10) da MESMA linha saiu com ST. A extração original do PDF
# emitiu só parte dos códigos de algumas linhas. Correção: +9 NCMs (7 por
# auditoria interna + 2 por conferência visual do anexo).

def test_tintas_vernizes_linha_completa_do_anexo():
    from pregoes.tributacao import eh_st
    assert eh_st("32081010"), "esmalte (3208) — já funcionava"
    assert eh_st("32091010"), "tinta acrílica (3209) — o caso do cliente"
    assert eh_st("32100000"), "3210.00 — já funcionava"


def test_corantes_e_vedacao_recuperados():
    from pregoes.tributacao import eh_st
    assert eh_st("32069999"), "posição 3206 inteira (corantes)"
    assert eh_st("32120010"), "3212 (corantes — print do anexo)"
    assert eh_st("40169300"), "juntas/gaxetas de vedação — domínio do cliente"


def test_nenhum_ncm_citado_na_descricao_fica_sem_cobertura():
    """INVARIANTE (a auditoria de 2026-07-06 virou guarda permanente): todo
    NCM citado no texto '(NCM ...)' de uma linha do arquivo precisa estar
    coberto por alguma entrada emitida (igual ou prefixo). Foi exatamente
    essa classe de furo que deixou a tinta 3209 sem ST em produção.

    Descrições TRUNCADAS (cortadas no meio da lista) têm o último token
    ignorado — esses casos são o risco residual registrado na Q14."""
    import json
    import re
    from pathlib import Path
    doc = json.loads((Path("pregoes/dados/ncm_st.json")).read_text("utf-8"))
    emitidos = {e["ncm"] for e in doc["ncms"]}
    por_cest: dict = {}
    for e in doc["ncms"]:
        por_cest.setdefault(e["cest"], e["descricao"])
    faltando = []
    for cest, desc in por_cest.items():
        m = re.search(r"NCM[:\s]*([\d.,\s e/]+)", desc)
        if not m:
            continue
        tokens = re.findall(r"[\d.]+", m.group(1))
        truncada = not desc.rstrip().endswith(")")
        if truncada and tokens:
            tokens = tokens[:-1]   # último código pode estar cortado
        for t in tokens:
            c = t.replace(".", "")
            if len(c) < 4:
                continue
            if c not in emitidos and not any(c.startswith(e) for e in emitidos):
                faltando.append((cest, c))
    assert not faltando, f"NCMs citados sem cobertura: {faltando}"
