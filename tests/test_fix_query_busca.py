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
