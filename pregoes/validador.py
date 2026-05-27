"""
Validação semântica de candidatos a "tabela de itens de edital".

Por que isto existe: a heurística antiga assumia que "tabela com palavras-chave
no cabeçalho = tabela de itens". Editais reais quebram essa suposição de N
formas: tabelas de endereços, fornecedores, rubricas orçamentárias, contatos,
modelos de proposta em branco. Tudo isso tem "Descrição" no cabeçalho.

A solução é olhar para o CONTEÚDO. Uma tabela de itens real tem padrão
consistente: muitas linhas com descrição plausível, quantidade > 0 e unidade
conhecida. Em vez de procurar pelo cabeçalho, computamos um score semântico
em cada candidato e escolhemos os melhores.

Este módulo não toca em PDF — recebe linhas/itens já parseados e devolve
scores. Isso permite testar em isolamento sem fixtures PDF.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


# Unidades aceitas em editais brasileiros (normalizadas: upper, sem acento,
# sem ponto). Lista propositalmente abrangente — vale a pena aceitar uma
# unidade rara do que rejeitar um item válido.
_UNIDADES_BASE = {
    # contagem
    "UN", "UND", "UNID", "UNIDADE", "UNIDADES",
    "PC", "PCS", "PCA", "PEC", "PECA", "PECAS",
    "PAR", "PARES",
    "DZ", "DUZIA", "DUZIAS",
    "JG", "JOGO", "JOGOS",
    # massa
    "G", "GR", "GRAMA", "GRAMAS",
    "KG", "QUILO", "QUILOS", "QUILOGRAMA",
    "TON", "TONELADA",
    # volume
    "L", "LT", "LITRO", "LITROS",
    "ML", "MILILITRO", "MILILITROS",
    # comprimento e área
    "M", "MT", "METRO", "METROS",
    "MM", "MILIMETRO",
    "CM", "CENTIMETRO",
    "M2", "METROQUADRADO",
    "M3", "METROCUBICO",
    # embalagens
    "CX", "CAIXA", "CAIXAS",
    "PCT", "PACOTE", "PACOTES",
    "SC", "SACO", "SACOS",
    "FR", "FRASCO", "FRASCOS",
    "TB", "TUBO", "TUBOS",
    "RL", "ROLO", "ROLOS",
    "FL", "FOLHA", "FOLHAS",
    "BARRA", "BARRAS", "BR",
    "GL", "GALAO", "GALOES",
    "POTE", "BD", "BALDE",
    "BOBINA", "RESMA",
    # outras
    "DOSE", "AMPOLA", "BISN", "BISNAGA",
}


def _norm_unidade(u: str) -> str:
    """Normaliza unidade para comparação: upper, sem acento, sem ponto/espaço."""
    if not u:
        return ""
    s = unicodedata.normalize("NFD", str(u))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.upper().replace(".", "").replace(" ", "").replace("²", "2").replace("³", "3")
    return s


_UNIDADES_CONHECIDAS = frozenset(_norm_unidade(u) for u in _UNIDADES_BASE)


# Palavras que nunca devem aparecer no início de uma descrição de item.
# Útil para descartar fragmentos visuais ("- continuação", "...", labels).
_PREFIXOS_LIXO = re.compile(
    r"^\s*(grupo|subgrupo|total|subtotal|valor\s+total|valor\s+global|"
    r"item\s+(quant|quantidade|descricao)|natureza|elemento|funcao|"
    r"observa|observações?|nota|notas?|prazo|condicao|condições|"
    r"penalidade|programa|aplicacao|gestor|fiscal|despesa|orcamento)\b",
    re.IGNORECASE,
)


# Texto de placeholder que aparece em modelos de proposta em branco.
_PLACEHOLDER = re.compile(r"^[X\s\.]+$|^[X]{2,}$", re.IGNORECASE)


# Itens de catálogo brasileiro tipicamente começam com SUBSTANTIVO em caixa
# alta ou capitalizado. Útil para distinguir "ABRAÇADEIRA DE NYLON" de
# "Conforme estabelecido no Termo".
_PARECE_NOME_PRODUTO = re.compile(
    r"^\s*[A-ZÁ-Ú][A-ZÁ-Ú0-9\s,\(\)\-/\.]+",
)


@dataclass(frozen=True)
class ScoreLinha:
    """Resultado do scoring de uma linha candidata."""
    score: float        # 0..1
    motivos: tuple[str, ...]   # explica componentes que somaram (debug/UI)


def score_linha(
    descricao: str | None,
    quantidade: float | None,
    unidade: str | None,
    valor_referencia: float | None,
) -> ScoreLinha:
    """
    Estima quão provável é que uma linha extraída seja um item de edital
    real (vs lixo: continuação visual, totais, contatos, modelos em branco).

    Retorna 0..1. Acima de ~0.7 = item plausível; abaixo de ~0.4 = lixo.

    Componentes (somam até 1.0):
    - descrição não-vazia e longa o bastante (>= 12 chars): 0.35
    - descrição parece nome de produto (caixa alta/capitalizado): 0.15
    - quantidade > 0: 0.25
    - unidade reconhecida: 0.15
    - valor de referência > 0: 0.10

    Penalidades (subtraem):
    - descrição é só placeholder (XXXX): -1.0 (zera)
    - descrição começa com palavra-lixo (grupo, total, etc.): -1.0
    """
    motivos: list[str] = []
    score = 0.0

    desc = (descricao or "").strip()
    if not desc:
        return ScoreLinha(0.0, ("desc-vazia",))

    if _PLACEHOLDER.match(desc):
        return ScoreLinha(0.0, ("placeholder",))

    if _PREFIXOS_LIXO.match(desc):
        return ScoreLinha(0.0, (f"prefixo-lixo:{desc[:20]}",))

    if len(desc) >= 12:
        score += 0.35
        motivos.append("desc-longa")
    elif len(desc) >= 5:
        score += 0.15
        motivos.append("desc-curta")

    if _PARECE_NOME_PRODUTO.match(desc):
        score += 0.15
        motivos.append("parece-produto")

    if quantidade is not None and quantidade > 0:
        score += 0.25
        motivos.append("qtd>0")

    if unidade and _norm_unidade(unidade) in _UNIDADES_CONHECIDAS:
        score += 0.15
        motivos.append("unidade-ok")

    if valor_referencia is not None and valor_referencia > 0:
        score += 0.10
        motivos.append("valor-ok")

    return ScoreLinha(min(score, 1.0), tuple(motivos))


@dataclass(frozen=True)
class ScoreTabela:
    """Resultado do scoring de uma tabela candidata inteira."""
    score: float              # 0..1
    n_linhas_validas: int     # linhas com score > 0.7
    n_linhas_total: int
    media_scores: float       # média dos scores >0
    motivos: tuple[str, ...]


def score_tabela(scores_linhas: list[ScoreLinha]) -> ScoreTabela:
    """
    Score de uma tabela inteira a partir dos scores das suas linhas.

    Heurística: tabela é "de itens" se:
    - tem >= 3 linhas válidas (score linha > 0.7)
    - >= 50% das linhas que têm conteúdo são válidas
    - a média dos scores das linhas com conteúdo é > 0.5

    A combinação evita dois extremos: tabela com 1 linha boa cercada de lixo
    (alta média mas pouca densidade) e tabela com 50 linhas tudo medíocre
    (densidade ok mas qualidade baixa).
    """
    n_total = len(scores_linhas)
    if n_total == 0:
        return ScoreTabela(0.0, 0, 0, 0.0, ("vazia",))

    # Ignora linhas com score 0 — provavelmente lixo claro (placeholder,
    # prefixo-lixo, vazia). Contamos só as que têm "alguma coisa".
    com_conteudo = [s for s in scores_linhas if s.score > 0.0]
    if not com_conteudo:
        return ScoreTabela(0.0, 0, n_total, 0.0, ("sem-conteudo",))

    validas = [s for s in com_conteudo if s.score >= 0.7]
    media = sum(s.score for s in com_conteudo) / len(com_conteudo)
    densidade_validas = len(validas) / len(com_conteudo)

    motivos = [
        f"validas={len(validas)}/{len(com_conteudo)}",
        f"media={media:.2f}",
    ]

    # Composição: peso 0.5 na densidade de linhas válidas, peso 0.5 na média
    # geral. Penaliza tabelas com poucas linhas válidas (< 3) com fator.
    if len(validas) < 3:
        fator_volume = len(validas) / 3.0
        motivos.append(f"poucas-validas-fator={fator_volume:.2f}")
    else:
        fator_volume = 1.0

    score_bruto = 0.5 * densidade_validas + 0.5 * media
    score = score_bruto * fator_volume

    return ScoreTabela(
        score=min(score, 1.0),
        n_linhas_validas=len(validas),
        n_linhas_total=n_total,
        media_scores=media,
        motivos=tuple(motivos),
    )
