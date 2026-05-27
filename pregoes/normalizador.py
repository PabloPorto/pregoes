"""
Normalização de descrições para busca em sites de fornecedores.

O scraper de Almeida + Dantas vai usar isto como camada 2 da busca em 4
camadas: descrição exata → descrição limpa → palavras-chave → código.

Princípio: remover só o ruído óbvio que existe nas descrições extraídas
do PDF (acentos, frações unicode, travessões, prefixo numérico do edital,
quebras de linha residuais). NÃO tentamos canonizar unidades nem grafias
diferentes no primeiro lote — isso vai ser calibrado quando o scraper
começar a falhar em casos reais.
"""
from __future__ import annotations

import re
import unicodedata


# Frações unicode comuns em editais (vergalhão ½ X, etc.)
_FRACOES = {
    "½": "1/2",
    "¼": "1/4",
    "¾": "3/4",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅛": "1/8",
}

# Travessões e hífens unicode que aparecem nos PDFs (em "AC–II", "CA–50", etc.)
_TRAVESSAO = re.compile(r"[‐-―−]")

# Prefixo "123) " que vem do edital ("1) ABRAÇADEIRA...")
_PREFIXO_ITEM = re.compile(r"^\s*\d+\)\s*")

# Espaços em branco múltiplos (incluindo \n, \t)
_WHITESPACE = re.compile(r"\s+")

# Vírgula decimal em português entre dígitos: 12,5 → 12.5
# (mas "ca-50, 12,5" → o `, ` separador vira espaço; só dígitos isolados viram .)
_VIRGULA_DECIMAL = re.compile(r"(\d),(\d)")

# Pontuação que não agrega em busca (vírgulas, pontos finais, ponto-vírgula)
# Hífen, barra e parênteses são preservados (fazem parte de códigos: ca-50, c/100).
_PONTUACAO_RUIDO = re.compile(r"[,;:]")

# Stopwords pt-BR genéricas que poluem busca em catálogo de fornecedor
_STOPWORDS = frozenset({
    "de", "da", "do", "das", "dos", "para", "com", "sem", "em",
    "por", "ou", "se", "que",
})


def normalizar_descricao(descricao: str | None) -> str:
    """
    Limpa uma descrição vinda do PDF para usar como query de busca.

    Operações, em ordem:
    1. Remove o prefixo "NN)" que sobrou da extração do edital.
    2. Substitui frações unicode (½ → 1/2).
    3. Substitui travessões unicode (–) por hífen ASCII (-).
    4. Vírgula decimal pt-BR entre dígitos vira ponto (12,5 → 12.5).
    5. Vírgula/dois-pontos/ponto-vírgula soltos viram espaço (são separadores).
    6. Remove acentos.
    7. Reduz para caixa baixa.
    8. Colapsa whitespace múltiplo (inclusive \\n) em um único espaço.

    Mantém hífen, barra e parênteses — fazem parte de códigos válidos
    (`ca-50`, `c/100`, `2.5`).

    >>> normalizar_descricao("1) ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND")
    'abracadeira de nylon 100 x 2.5 pct c/100und'
    >>> normalizar_descricao("14) ACO CA–50, ½, VERGALHAO C/ 12M SUPERFÍCIE LISA")
    'aco ca-50 1/2 vergalhao c/ 12m superficie lisa'
    >>> normalizar_descricao("10) ACO CA-50, 12,5 MM, VERGALHAO")
    'aco ca-50 12.5 mm vergalhao'
    >>> normalizar_descricao("VÁLVULA  DE  RETENÇÃO(SUCÇÃO)\\n32MM")
    'valvula de retencao(succao) 32mm'
    """
    if not descricao:
        return ""

    s = str(descricao)
    s = _PREFIXO_ITEM.sub("", s)
    for frac, ascii_eq in _FRACOES.items():
        s = s.replace(frac, ascii_eq)
    s = _TRAVESSAO.sub("-", s)
    # Decimal pt-BR antes de tratar vírgula como separador, senão "12,5" perde o 5.
    s = _VIRGULA_DECIMAL.sub(r"\1.\2", s)
    s = _PONTUACAO_RUIDO.sub(" ", s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = _WHITESPACE.sub(" ", s).strip()
    return s


def keywords(descricao: str | None) -> list[str]:
    """
    Extrai palavras-chave (tokens) distintivos da descrição normalizada.

    Filtros aplicados:
    - Tokens com <3 chars (ex: "x", "c/")
    - Stopwords pt-BR genéricas ("de", "para", "com", ...)
    - Tokens puramente numéricos (com ou sem decimal): "100", "12.5", "2,5"
    - Unidades sozinhas ("mm", "cm", "kg") — aparecem em quase tudo e
      ranqueiam mal. Mantém quando coladas ao número (ex: "12mm", "30cm").

    >>> keywords("1) ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND")
    ['abracadeira', 'nylon', 'pct', 'c/100und']
    >>> keywords("10) ACO CA-50, 12,5 MM, VERGALHAO C/ 12M SUPERFÍCIE NERVURADA")
    ['aco', 'ca-50', 'vergalhao', 'superficie', 'nervurada']
    """
    normalizada = normalizar_descricao(descricao)
    if not normalizada:
        return []

    UNIDADES_SOZINHAS = {"mm", "cm", "m", "kg", "g", "l", "ml", "und", "pct"}

    out = []
    for t in normalizada.split(" "):
        if len(t) < 3:
            continue
        if t in _STOPWORDS:
            continue
        if t in UNIDADES_SOZINHAS:
            continue
        # Numérico puro (incluindo decimal): '100', '12.5', '2,5', '5/16'
        sem_pontuacao = t.replace(".", "").replace(",", "").replace("/", "")
        if sem_pontuacao.isdigit():
            continue
        out.append(t)
    return out


def relevancia(original: str | None, candidato: str | None) -> float:
    """
    Score 0..1 de quão bem `candidato` casa com `original`.

    Definida como `|kws_orig ∩ kws_candidato| / |kws_orig|` — fração das
    palavras-chave da descrição original que aparecem também no resultado
    do fornecedor.

    Usado pelo scraper para descartar resultados óbvios de lixo (caso
    típico: site faz busca por palavras isoladas e retorna qualquer
    produto que tenha QUALQUER token, mesmo irrelevante).

    >>> relevancia("VERGALHAO ACO CA-50 12,5MM",
    ...            "CORRENTE SOLDADA SAO RAPHAEL 6.5MM SACO C/12,5KG")
    0.0
    >>> relevancia("ABRAÇADEIRA DE NYLON 100 X 2.5",
    ...            "ABRAÇADEIRA DE NYLON FOXLUX BRANCA 2.5X10CM C/100")
    1.0
    """
    if not original or not candidato:
        return 0.0
    kws_orig = set(keywords(original))
    kws_cand = set(keywords(candidato))
    if not kws_orig:
        return 0.0
    return len(kws_orig & kws_cand) / len(kws_orig)
