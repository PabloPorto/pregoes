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


def normalizar_descricao(descricao: str | None) -> str:
    """
    Limpa uma descrição vinda do PDF para usar como query de busca.

    Operações, em ordem:
    1. Remove o prefixo "NN)" que sobrou da extração do edital.
    2. Substitui frações unicode (½ → 1/2).
    3. Substitui travessões unicode (–) por hífen ASCII (-).
    4. Remove acentos.
    5. Reduz para caixa baixa.
    6. Colapsa whitespace múltiplo (inclusive \\n) em um único espaço.

    Não toca em pontuação interna, não tenta padronizar unidades, não
    descarta palavras curtas. Tudo isso é trabalho de calibração quando
    o scraper estiver buscando de fato.

    >>> normalizar_descricao("1) ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND")
    'abracadeira de nylon 100 x 2.5 pct c/100und'
    >>> normalizar_descricao("14) ACO CA–50, ½, VERGALHAO C/ 12M SUPERFÍCIE LISA")
    'aco ca-50, 1/2, vergalhao c/ 12m superficie lisa'
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
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = _WHITESPACE.sub(" ", s).strip()
    return s


def keywords(descricao: str | None) -> list[str]:
    """
    Extrai palavras-chave (tokens) da descrição normalizada.

    Útil para a camada 3 da busca: quando descrição limpa não acha, o
    scraper tenta com as 2-3 keywords mais distintivas.

    Heurística simples no primeiro lote: split por espaço, descarta
    palavras com menos de 3 caracteres e palavras puramente numéricas
    (são unidades como '40', '100' que aparecem em quase tudo e ranqueiam
    mal).

    >>> keywords("1) ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND")
    ['abracadeira', 'nylon', 'pct', 'c/100und']
    """
    normalizada = normalizar_descricao(descricao)
    if not normalizada:
        return []
    tokens = normalizada.split(" ")
    return [t for t in tokens if len(t) >= 3 and not t.replace(".", "").isdigit()]
