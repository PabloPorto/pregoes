"""Onda 1 (2026-06-23) — construção da QUERY de busca.

Corrige 3 falhas reais de campo, sem IA e sem regressão (mudanças PURAMENTE
aditivas em `_montar_camadas` + poda de marketing + sinônimo de domínio):

- "Alicate profissional" -> a palavra-ruído "profissional" zerava o AND do site.
- "Alicate de pressão"   -> AND de 2 termos restritivo; rede de 1 keyword destrava.
- "Revestimento textura" -> sinônimo (site = "grafiato"); resolvido por substituição.

Garante também NÃO-REGRESSÃO: termos que SÃO categoria ("cabo", "aço", "fita",
"isolante") nunca são podados, e a poda NUNCA esvazia a query.
"""
from __future__ import annotations

from pregoes.scraper import _montar_camadas
from pregoes.normalizador import (
    _casa_como_borda,
    _keyword_discriminadora,
    keywords_distintivas,
    relevancia,
)


def _queries(descricao: str) -> list[str]:
    return [q for _conf, q in _montar_camadas(descricao)]


# --- Caso "profissional": adjetivo-ruído podado da query ---------------------

def test_profissional_query_primaria_vira_so_a_categoria():
    qs = _queries("Alicate profissional")
    # A query PRIMÁRIA (1ª tentada) já é "alicate" — "profissional" foi podado.
    assert qs[0] == "alicate"
    assert "alicate" in qs


def test_premium_tambem_podado():
    assert _queries("Furadeira premium")[0] == "furadeira"


# --- Caso "pressão": rede final de UMA keyword de categoria ------------------

def test_pressao_ganha_rede_de_uma_keyword():
    qs = _queries("Alicate DE pressão")
    # As queries específicas continuam (não regride) e a rede "alicate" entra
    # como ÚLTIMO recurso para a categoria sempre devolver candidatos.
    assert "alicate pressao" in qs
    assert "alicate" in qs
    assert qs[-1] == "alicate"   # rede de categoria por último


def test_rede_de_categoria_exige_4_chars():
    # Categoria curta (<4) não vira rede de 1 keyword (evita sigla genérica
    # trazendo o catálogo inteiro). 'cap' (3) não deve aparecer isolado.
    qs = _queries("CAP PVC 50MM")
    assert "cap" not in qs


# --- Caso "textura": sinônimo por substituição ------------------------------

def test_textura_substituida_por_grafiato_na_query():
    qs = _queries("Revestimento textura")
    assert any("grafiato" in q for q in qs)
    assert all("textura" not in q for q in qs)


# --- NÃO-REGRESSÃO: categoria nunca é podada --------------------------------

def test_nao_regride_cabo_de_aco():
    qs = _queries("Cabo de aço 6mm")
    # 'cabo' e 'aco' são CATEGORIA, não marketing — têm que sobreviver na query.
    assert any("cabo" in q and "aco" in q for q in qs)


def test_nao_regride_fita_isolante():
    qs = _queries("Fita isolante 19mm")
    assert any("fita" in q and "isolante" in q for q in qs)


# --- Guarda dura: poda NUNCA esvazia a query --------------------------------

def test_poda_nunca_esvazia_a_query():
    # Entrada degenerada só com marketing: a guarda mantém o original.
    qs = _queries("Profissional Premium")
    assert qs, "nunca pode resultar em lista de queries vazia"
    assert all(q.strip() for q in qs)


# --- Poda de marketing: vizinhos do caso real (rede de 1 keyword salva) ------

def test_poda_cobre_so_profissional_premium_rede_salva_o_resto():
    # 'top'/'comum'/'reforçado'/'industrial' NÃO estão em _ADJETIVOS_MARKETING:
    # a query primária os mantém, mas a rede de 1 keyword ('alicate') garante
    # que a categoria volte se o site zerar com o ruído.
    for s, primaria in [("Alicate top", "alicate top"),
                        ("Alicate comum", "alicate comum"),
                        ("Alicate reforçado", "alicate reforcado"),
                        ("Alicate industrial", "alicate industrial")]:
        qs = _queries(s)
        assert qs[0] == primaria
        assert qs[-1] == "alicate"


def test_rede_de_categoria_guard_4_chars_inclusivo():
    # Categorias com EXATAMENTE 4 chars viram rede (limite é inclusivo).
    for s, cat in [("CANO PVC 50MM", "cano"), ("LONA PRETA 8M", "lona"),
                   ("FITA ISOLANTE 19MM", "fita"), ("SACO LIXO 100L", "saco")]:
        assert _queries(s)[-1] == cat


# --- Discriminador (marca/tipo) ---------------------------------------------

def test_discriminador_pega_marca_descarta_generico_e_numerado():
    assert _keyword_discriminadora("CHAVE FENDA COM PONTA STANLEY 3/16") == "stanley"
    assert _keyword_discriminadora("CABO DE ACO GALVANIZADO BELGO 6MM") == "belgo"
    # Marca com dígito (3M, E6013) não é discriminador (cai no Tier 2 de dimensão).
    assert _keyword_discriminadora("FITA 3M ISOLANTE 19MM") is None
    assert _keyword_discriminadora("ELETRODO E6013 2.5MM") is None


def test_tipo_e_stopword_comum_nao_e():
    # Assimetria documentada: 'tipo' é stopword de domínio (podada), 'comum' não.
    assert keywords_distintivas("Alicate tipo") == ["alicate"]
    assert keywords_distintivas("Alicate comum") == ["alicate", "comum"]


# --- Anti-falso-positivo de substring (borda) -------------------------------

def test_pilha_nao_casa_empilhadeira():
    # Substring no MEIO não casa (senão 'pilha' ⊂ 'empilhadeira' alucinaria).
    assert relevancia("PILHA AAA ALCALINA", "EMPILHADEIRA MANUAL 2T") < 0.35


def test_casa_como_borda_prefixo_sim_meio_nao():
    assert _casa_como_borda("cap", "cape")
    assert _casa_como_borda("sold", "soldavel")
    assert not _casa_como_borda("rato", "borracha")


# ---------------------------------------------------------------------------
# Texto contratual do edital fora da consulta (campo 2026-11-14): em 836 de
# 1.362 itens reais havia "produto de alta qualidade…", "com selo do INMETRO",
# "de acordo com as normas da ABNT", e palavras dessas frases iam para o site.
# ---------------------------------------------------------------------------

import pytest as _pytest

from pregoes.normalizador import relevancia as _relevancia
from pregoes.scraper import _montar_camadas as _camadas


def _queries(desc):
    return [q for _, q in _camadas(desc)]


@_pytest.mark.parametrize("desc, primeira", [
    ("CADEADO LATAO 40 MM, produto de alta qualidade, corpo em latao macico e "
     "chave em latao.", "cadeado latao 40mm"),                 # era '... 40mm alta'
    ("Ponteiro 12\", com selo do INMETRO", "ponteiro"),           # era 'ponteiro selo'
    ("REGISTRO PRESSÃO 1416 1/2 METAL DE ACORDO COM AS NORMAS TÉCNICAS DA ABNT.",
     "registro pressao metal"),                                  # era '... metal tecnicas'
    ("PÁ QUADRADA COM NORMA ABNT", "pa quadrada"),               # era 'quadrada'
    ("TE SOLDAVEL 25MM, de acordo com as normas da ABNT", "te soldavel 25mm"),
    ("ACABAMENTO C/CAN C40 CR COM NORMA ABNT", "acabamento can c40"),   # era 'c/can c40'
    ("BROCA EM ESPIRAL P/MADEIRA 5MM B COM SELO DO INMETRO",
     "broca espiral madeira 5mm"),                               # era '... p/madeira 5mm'
    ("FURADEIRA DE IMPACTO GSB 550W 220V, produto de altaqualidade, reversao",
     "furadeira impacto gsb 550w"),
])
def test_primeira_consulta_so_tem_palavra_de_produto(desc, primeira):
    assert _queries(desc)[0] == primeira


def test_nenhuma_consulta_carrega_o_texto_contratual():
    desc = ("CAIXA DAGUA POLIETILENO 1000L, produto de alta qualidade, com a "
            "presenca do UV na materia prima garante durabilidade.")
    for q in _queries(desc):
        assert "qualidade" not in q and "alta" not in q.split(), q


def test_descricao_sem_frase_contratual_fica_como_estava():
    assert _queries("LAMPADA VAPOR DE SODIO 400W OVOIDE LEITOSA E40")[:2] == [
        "lampada vapor sodio 400w", "lampada vapor"]


def test_texto_contratual_nao_pesa_na_relevancia():
    """'PÁ QUADRADA COM NORMA ABNT' × o produto certo dava 0,33 (abaixo do
    mínimo): 'norma' e 'abnt' contavam como palavras que o produto não tem."""
    assert _relevancia("PÁ QUADRADA COM NORMA ABNT",
                       "PÁ QUADRADA THOR Nº3 COM CABO PLÁSTICO Y") >= 0.35
    # ...sem afrouxar para produto de outra categoria
    assert _relevancia("PÁ QUADRADA COM NORMA ABNT",
                       "MESA QUADRADA TRAMONTINA TAMBAU") < 0.35
    assert _relevancia("TE SOLDAVEL 25MM", "TE SOLDAVEL PLASTUBOS 25MM") >= 0.7
    assert _relevancia("TE SOLDAVEL 25MM", "JOELHO SOLDAVEL PLASTUBOS 25MM") < 0.35
