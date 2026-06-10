"""
Normalização de descrições para busca em sites de fornecedores.

O scraper de Almeida + Dantas vai usar isto como camada 2 da busca em 4
camadas: descrição exata → descrição limpa → palavras-chave → código.

Princípio: remover só o ruído óbvio que existe nas descrições extraídas
do PDF (acentos, frações unicode, travessões, prefixo numérico do edital,
quebras de linha residuais). NÃO tentamos canonizar unidades nem grafias
diferentes no primeiro lote — isso vai ser calibrado quando o scraper
começar a falhar em casos reais.

A `relevancia` é HÍBRIDA (Q12, Camada 2): combina a lógica de keywords
com `rapidfuzz.token_sort_ratio` para tolerar erros de digitação e
ordem trocada de palavras.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from rapidfuzz import fuzz


# Frações unicode comuns em editais (vergalhão ½ X, etc.)
_FRACOES = {
    "½": "1/2",
    "¼": "1/4",
    "¾": "3/4",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅛": "1/8",
}

# Expoentes unicode de unidade de ÁREA/volume — "2.5MM²" (seção de cabo),
# "8M²", "10CM²". O caractere ² (U+00B2) NÃO é um acento combinante (Mn),
# então sobrevive ao stripping de acentos e quebraria o \b depois de "mm"
# se não fosse rebaixado para o dígito ASCII. Mapear para o dígito também
# unifica a grafia com a variante ASCII ("MM2") que o operador digita.
_EXPOENTES = {
    "²": "2",
    "³": "3",
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

# Cola número + unidade de medida separados por espaço: "500 L" → "500l",
# "12,5 MM" → "12.5mm", "750 V" → "750v".
# PORQUÊ: a unidade sozinha é descartada como keyword (aparece em tudo) e leva
# o número junto na poda — "caixa d agua 500 L" vira só "caixa agua" (genérico
# demais → casa errado/alucina). Colada, "500l" sobrevive como token distintivo
# e a busca acerta. Roda DEPOIS do lower e da vírgula-decimal (o número já é
# "500"/"12.5"). Só unidades de MEDIDA — unidades de embalagem (pct/und) ficam
# de fora porque o número antes costuma ser a dimensão, não a contagem do pacote
# (ex.: "100 X 2.5 PCT" → "2.5" é a bitola, não "2.5 pacotes").
_UNIDADES_MEDIDA = r"mm2|mm|cm|km|ml|kg|mg|kw|kva|kv|mah|ah|va|hp|cv|l|m|g|v|w"
_NUM_UNIDADE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s+(" + _UNIDADES_MEDIDA + r")\b")

# Grafias divergentes de alta frequência no domínio: a mesma característica
# escrita de formas diferentes no edital vs no catálogo do fornecedor. Mapeia
# para uma forma canônica (Tier 2.5 / 2026-06). Conservador — só pares reais
# observados, com word boundary para não substituir dentro de outra palavra.
_GRAFIAS_EQUIVALENTES = [
    (re.compile(r"\bphillips\b"), "philips"),
    (re.compile(r"\bphilipis\b"), "philips"),
    (re.compile(r"\bfillips\b"), "philips"),
]

# Stopwords pt-BR genéricas que poluem busca em catálogo de fornecedor
_STOPWORDS = frozenset({
    "de", "da", "do", "das", "dos", "para", "com", "sem", "em",
    "por", "ou", "se", "que", "nao", "sao", "seu", "sua",
})

# Termos CONTRATUAIS de edital — aparecem na descrição do item mas NUNCA no
# nome do produto no catálogo do fornecedor. Usados por `keywords_distintivas`
# para montar uma QUERY curta e distintiva (não para o cálculo de relevância,
# que continua vendo todas as keywords). Calibrado a partir dos editais reais
# (TRE-PE etc). Conservador de propósito: termos ambíguos que PODEM ser
# categoria de produto (ex: "material", "cor") ficam de fora para não perder
# sinal.
_STOPWORDS_DOMINIO = frozenset({
    "garantia", "garantias", "minima", "minimo", "maxima", "maximo",
    "conforme", "acordo", "norma", "normas", "abnt", "nbr", "inmetro",
    "similar", "similares", "embalagem", "embalagens", "original",
    "fabricante", "fabricacao", "modelo", "referencia", "marca",
    "qualidade", "qualidades", "especificacao", "especificacoes",
    "especificado", "produto", "produtos", "caracteristicas",
    "caracteristica", "aplicacao", "medida", "medidas", "tipo", "tipos",
    "utilizacao", "acabamento", "composicao", "contendo", "contem",
    "aproximada", "aproximado", "aproximadamente", "preferencia",
    "preferencialmente", "devera", "deverao", "devem", "deve", "possa",
    "possam", "possamos", "forma", "meses", "anos", "validade",
    "procedencia", "nacional", "importado", "primeira", "conter",
    "impresso", "impressa", "corpo", "nome",
    # Termos de MEDIDA — descrevem a peça mas não distinguem subproduto;
    # nunca devem virar "discriminador". (2026-06)
    "espessura", "espessuras", "largura", "larguras", "comprimento",
    "comprimentos", "altura", "alturas", "diametro", "diametros",
    "micra", "micras", "gramatura", "capacidade", "profundidade",
    "peso", "polegada", "polegadas", "metro", "metros", "centimetro",
    "centimetros", "milimetro", "milimetros", "litro", "litros",
    "espessura", "dimensao", "dimensoes",
})

# Numerais por extenso — descartados na QUERY (não ajudam a busca textual no
# site). O número em si vira "dimensão" via `extrair_dimensoes` para o filtro
# de relevância (ex: "5 tomadas").
_NUMERAIS_EXTENSO = frozenset({
    "um", "uma", "dois", "duas", "tres", "quatro", "cinco", "seis",
    "sete", "oito", "nove", "dez", "onze", "doze", "treze", "quatorze",
    "catorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove",
    "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "cem",
    "cento", "mil",
})


def normalizar_descricao(descricao: str | None) -> str:
    """
    Limpa uma descrição vinda do PDF para usar como query de busca.

    Operações, em ordem:
    1. Remove o prefixo "NN)" que sobrou da extração do edital.
    2. Substitui frações unicode (½ → 1/2).
    2b. Substitui expoentes de área (² → 2, ³ → 3) — "2.5MM²" → "2.5mm2".
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
    'aco ca-50 12.5mm vergalhao'
    >>> normalizar_descricao("VÁLVULA  DE  RETENÇÃO(SUCÇÃO)\\n32MM")
    'valvula de retencao(succao) 32mm'
    >>> normalizar_descricao("CAIXA D AGUA 500 L")  # unidade colada ao número
    'caixa d agua 500l'
    """
    if not descricao:
        return ""

    s = str(descricao)
    s = _PREFIXO_ITEM.sub("", s)
    for frac, ascii_eq in _FRACOES.items():
        s = s.replace(frac, ascii_eq)
    # Expoente de área (²/³) → dígito ASCII, senão "mm²" quebra o \b da
    # extração de dimensão e a seção do cabo é perdida silenciosamente.
    for expo, ascii_eq in _EXPOENTES.items():
        s = s.replace(expo, ascii_eq)
    s = _TRAVESSAO.sub("-", s)
    # Decimal pt-BR antes de tratar vírgula como separador, senão "12,5" perde o 5.
    s = _VIRGULA_DECIMAL.sub(r"\1.\2", s)
    s = _PONTUACAO_RUIDO.sub(" ", s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    # Grafias equivalentes (philips/phillips) — depois do lower, antes do
    # colapso de espaços. Barato e estável.
    for padrao, canonico in _GRAFIAS_EQUIVALENTES:
        s = padrao.sub(canonico, s)
    # Cola "número unidade" (ver _NUM_UNIDADE): "500 l" → "500l". Preventivo
    # contra o espaço solto que perde a medida na busca.
    s = _NUM_UNIDADE.sub(r"\1\2", s)
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
    ['abracadeira', 'nylon', 'c/100und']
    >>> keywords("10) ACO CA-50, 12,5 MM, VERGALHAO C/ 12M SUPERFÍCIE NERVURADA")
    ['aco', 'ca-50', '12.5mm', 'vergalhao', '12m', 'superficie', 'nervurada']
    >>> keywords("CAIXA D AGUA 500 L")  # '500l' sobrevive; sem o fix viria só caixa/agua
    ['caixa', 'agua', '500l']
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


def keywords_distintivas(descricao: str | None, n: int = 4) -> list[str]:
    """As `n` keywords mais distintivas para montar uma QUERY de busca curta.

    Diferença para `keywords()`: além dos filtros daquela, remove termos
    contratuais de edital (`_STOPWORDS_DOMINIO`) e numerais por extenso, e
    limpa pontuação de borda dos tokens. Preserva a ORDEM do texto — a
    categoria do produto aparece nas primeiras palavras do edital, então os
    primeiros tokens distintivos são justamente o "núcleo" do produto.

    Motivação (Q12 / diagnóstico real 2026-06): enviar a descrição COMPLETA
    do edital como query (~30 tokens contratuais) faz os sites devolverem
    produtos sem relação. Uma query curta e distintiva ("filtro de linha
    tomadas", "chave fenda ponta philips") devolve resultados muito melhores.

    >>> keywords_distintivas(
    ...     "Filtro de linha com 5 (CINCO) tomadas elétricas de 10 Amperes, "
    ...     "espaçadas... conforme ABNT NBR 14136, garantia mínima 03 meses")
    ['filtro', 'linha', 'tomadas', 'eletricas']
    >>> keywords_distintivas('Chave de fenda com ponta Philips (3/16" x 3")')
    ['chave', 'fenda', 'ponta', 'philips']
    >>> # Editais repetem termos — deduplica preservando ordem
    >>> keywords_distintivas("Removedor de etiquetas e colas; removedor base solvente")
    ['removedor', 'etiquetas', 'colas', 'base']
    """
    out: list[str] = []
    vistos: set[str] = set()
    for k in keywords(descricao):
        k_limpo = k.strip('()[]{}.,"\'')
        if len(k_limpo) < 3:
            continue
        if k_limpo in _STOPWORDS_DOMINIO:
            continue
        if k_limpo in _NUMERAIS_EXTENSO:
            continue
        if k_limpo in vistos:   # dedup — editais repetem a categoria
            continue
        vistos.add(k_limpo)
        out.append(k_limpo)
        if len(out) >= n:
            break
    return out


def _primeiras_kw_categoria(desc: str, n: int = 2) -> list[str]:
    """Primeiras N keywords-categoria da descrição.

    Heurística: a "categoria" do produto fica nas 2 primeiras palavras
    distintivas do edital — REMOVEDOR de etiquetas, FILTRO de linha,
    LONA plástica, BATERIA automotiva, CHAVE com PONTA philips.

    Filtros aplicados (além dos do `keywords()`):
    - tokens com <4 chars (descarta "pvc", "mm", siglas curtas)
    - tokens puramente numéricos (mesmo escritos com `/` ou `.`)
    """
    out: list[str] = []
    for k in keywords(desc):
        k_limpo = k.rstrip('".,').lstrip('(').rstrip(')')
        if len(k_limpo) < 4:
            continue
        sem_pont = k_limpo.replace(".", "").replace(",", "").replace("/", "")
        if sem_pont.isdigit():
            continue
        out.append(k_limpo)
        if len(out) >= n:
            break
    return out


def _casa_como_borda(curta: str, longa: str) -> bool:
    """True se `curta` é prefixo OU sufixo de `longa` (não conta meio).

    Evita falsos positivos do tipo 'PILHA' casando em 'EMPILHADEIRA'
    (bug visto em produção 2026-05-27 com bateria automotiva).
    Mantém casos legítimos:
    - prefixo: 'sold' em 'soldavel' (abreviação edital)
    - sufixo: '1.1/2' em '50x1.1/2' (dimensão colada)
    - sufixo: 'c/100' em 'c/100und'
    """
    if not curta or len(curta) < 3:
        return False
    return longa.startswith(curta) or longa.endswith(curta)


def _contar_keywords_casadas(kws_orig: list[str], kws_cand: list[str]) -> int:
    """Conta keywords do original que têm correspondência (exata, prefixo
    ou sufixo) entre as keywords do candidato.

    Substring "no meio" da palavra **não conta** (vide `_casa_como_borda`).
    Histórico: a lógica antiga deixava 'pilha' bater em 'empilhadeira',
    'rato' bater em 'borracha', etc — falsos positivos em editais com
    descrições longas.
    """
    n_match = 0
    for ko in kws_orig:
        ko_limpo = ko.rstrip('".,')
        if not ko_limpo:
            continue
        for kc in kws_cand:
            kc_limpo = kc.rstrip('".,')
            if not kc_limpo:
                continue
            if ko_limpo == kc_limpo:
                n_match += 1
                break
            # Substring só conta se aparece como prefixo ou sufixo do
            # outro token — evita "PILHA" casando em "EMPILHADEIRA".
            if _casa_como_borda(ko_limpo, kc_limpo):
                n_match += 1
                break
            if _casa_como_borda(kc_limpo, ko_limpo):
                n_match += 1
                break
    return n_match


# Threshold do `token_sort_ratio` (0..100) para considerar "typo óbvio
# ou ordem trocada" entre as descrições completas. Calibrado em
# 2026-05-27: 'PARAFUSO 10MM' vs 'PARAFUZO 10MM' = 92, 'PVC ADAPTADOR'
# vs 'ADAPTADOR PVC' = 100. Casos legítimos com ordem trocada e typos
# leves entram nesse intervalo. Editais detalhados vs candidatos curtos
# raramente passam de 70 — não dispara em falsos positivos.
_TYPO_TOKEN_SORT_MIN = 90
_TYPO_SCORE_PISO = 0.85


# === Tier 2: match de dimensão (Q12 / 2026-06) ===
# Eixos com semântica confiável no domínio. `mm` foi ADICIONADO em 2026-06
# após um edital hidráulico real expor que joelho 60mm casava joelho 20mm
# (mm é o diâmetro nominal — a dimensão #1 em conexões PVC). Mantém-se de
# fora apenas o "NxM" solto (8x6), que é genuinamente ambíguo.
_NUMERAL_MAP = {
    "um": "1", "uma": "1", "dois": "2", "duas": "2", "tres": "3",
    "quatro": "4", "cinco": "5", "seis": "6", "sete": "7", "oito": "8",
    "nove": "9", "dez": "10", "onze": "11", "doze": "12",
}
_RE_POLEGADA = re.compile(r"(\d+(?:\.\d+)?/\d+)")          # 3/16, 1.1/2, 5/16
_RE_TOMADAS = re.compile(r"\b(\d+)\s*tomadas?\b")
_RE_AMPERE = re.compile(r"\b(\d+)(?:a\b|\s*amperes?\b)")
_RE_VOLT = re.compile(r"\b(\d+)\s*v(?:olts?)?\b")
_RE_WATT = re.compile(r"\b(\d+)\s*w(?:atts?)?\b")
_RE_CA = re.compile(r"\bca[\s-]?(\d+)\b")
# Seção de cabo elétrico em mm² (área): "2.5MM²" / "2.5MM2" / "4 mm2".
# É um EIXO PRÓPRIO ("mm2"), distinto do diâmetro nominal "mm" — cabo
# 2.5mm² e cabo 4mm² são subprodutos diferentes (preços diferentes), e
# misturá-los no eixo "mm" faria 2.5mm² casar 2.5mm (joelho), o que é
# absurdo. Deve ser testada ANTES de _RE_MM (vide ordem em
# `extrair_dimensoes`); o lookahead negativo em _RE_MM garante a separação
# de qualquer forma.
_RE_MM2 = re.compile(r"\b(\d+(?:\.\d+)?)\s*mm2\b")
# Milímetro/centímetro: diâmetro nominal de conexões, bitola de cabo, etc.
# Exige a unidade colada/adjacente — "6m" (metros) NÃO casa "mm". O
# lookahead (?![0-9]) impede que "2.5mm2" (seção) seja lido como "2.5mm"
# (diâmetro): seção é tratada por _RE_MM2.
_RE_MM = re.compile(r"\b(\d+(?:\.\d+)?)\s*mm(?![0-9])\b")
_RE_CM = re.compile(r"\b(\d+(?:\.\d+)?)\s*cm\b")
# Diâmetro nominal escrito SEM unidade colada: "DIÂMETRO 75", "DIÂMETRO DE 75",
# "DIÂMETRO NOMINAL 75", "DN 75", "DN75", "Ø 75", "⌀ 75". Mapeado para o eixo
# "mm" (em tubos/conexões PVC, mm É o diâmetro nominal — o catálogo guarda
# "75MM"), para casar "DIÂMETRO 75" do edital com "TUBO 75MM" do fornecedor.
# ÂNCORA OBRIGATÓRIA (a palavra/sinal antes do número) — um "75" solto NÃO vira
# diâmetro. O lookahead (?![\\d./]) cede a fração ao eixo "pol" ("DIÂMETRO 7/8"
# → pol) e não conflita com "DIÂMETRO 75MM" (o _RE_MM pega; duplo é inócuo no set).
_RE_DIAM_NOMINAL = re.compile(
    r"(?:\bdiametros?(?:\s+nominal)?|\bdn|ø|⌀)\s*(?:de\s+)?(\d+(?:\.\d+)?)(?![\d./])"
)


def _converter_numerais_extenso(texto: str) -> str:
    """'cinco tomadas' → '5 tomadas'. Só para extração de dimensão."""
    return " ".join(_NUMERAL_MAP.get(p, p) for p in texto.split())


def extrair_dimensoes(texto: str | None) -> set[tuple[str, str]]:
    """Extrai dimensões/medidas-chave como pares `(eixo, valor)`.

    Cada eixo tem semântica única no domínio, então comparar permite
    distinguir subprodutos ("chave 3/16" vs "chave 1/8", "filtro 5 tomadas"
    vs "filtro 3 tomadas") sem ambiguidade.

    >>> sorted(extrair_dimensoes('Chave de fenda Philips 3/16" x 3"'))
    [('pol', '3/16')]
    >>> sorted(extrair_dimensoes("Filtro de linha 5 (CINCO) tomadas 10 Amperes"))
    [('amp', '10'), ('tom', '5')]
    >>> sorted(extrair_dimensoes("Bateria 12V"))
    [('v', '12')]
    >>> # Seção de cabo: eixo próprio "mm2" (não confunde com diâmetro "mm")
    >>> sorted(extrair_dimensoes("CABO FLEXIVEL 2.5MM² 750V"))
    [('mm2', '2.5'), ('v', '750')]
    >>> sorted(extrair_dimensoes("CABO 4MM2 750V"))
    [('mm2', '4'), ('v', '750')]
    >>> # Diâmetro nominal sem "mm" colado → eixo mm (casa "75MM" do catálogo)
    >>> sorted(extrair_dimensoes("TUBO PVC RÍGIDO DIÂMETRO 75 SOLDÁVEL"))
    [('mm', '75')]
    >>> sorted(extrair_dimensoes("Joelho DN 25"))
    [('mm', '25')]
    >>> sorted(extrair_dimensoes("Conexão DIÂMETRO DE 50"))
    [('mm', '50')]
    >>> # Âncora obrigatória: número solto NÃO vira diâmetro
    >>> sorted(extrair_dimensoes("PARAFUSO 75 ITEM 30"))
    []
    >>> # Fração cede ao eixo polegada (não vira mm)
    >>> sorted(extrair_dimensoes("VÁLVULA DIÂMETRO 7/8"))
    [('pol', '7/8')]
    """
    if not texto:
        return set()
    # Remove parênteses (separam o número do substantivo: "5 (cinco) tomadas")
    t = normalizar_descricao(texto).replace("(", " ").replace(")", " ")
    t = _converter_numerais_extenso(t)
    dims: set[tuple[str, str]] = set()
    for v in _RE_POLEGADA.findall(t):
        dims.add(("pol", v))
    for v in _RE_TOMADAS.findall(t):
        dims.add(("tom", v))
    for v in _RE_AMPERE.findall(t):
        dims.add(("amp", v))
    for v in _RE_VOLT.findall(t):
        dims.add(("v", v))
    for v in _RE_WATT.findall(t):
        dims.add(("w", v))
    for v in _RE_CA.findall(t):
        dims.add(("ca", v))
    # Seção de cabo (mm²) ANTES do diâmetro (mm): "2.5mm2" é seção, não
    # diâmetro. O lookahead de _RE_MM já evita o duplo-match, mas a ordem
    # deixa a intenção explícita.
    for v in _RE_MM2.findall(t):
        dims.add(("mm2", v))
    for v in _RE_MM.findall(t):
        dims.add(("mm", v))
    # Diâmetro nominal sem "mm" colado ("DIÂMETRO 75", "DN 25") → eixo mm.
    for v in _RE_DIAM_NOMINAL.findall(t):
        dims.add(("mm", v))
    for v in _RE_CM.findall(t):
        dims.add(("cm", v))
    # Normaliza o valor de cada eixo para comparação: zeros à esquerda de
    # inteiros somem ('05'->'5'), senão "5 tomadas" e "05 TOMADAS" pareciam
    # eixos divergentes e rebaixavam um match verdadeiro (filtro de linha).
    # Decimais ('2.5') e frações ('3/16') ficam intactos.
    return {(eixo, _norm_valor_dim(v)) for eixo, v in dims}


def _norm_valor_dim(v: str) -> str:
    """Tira zeros à esquerda de valores inteiros de dimensão ('05'->'5').

    Mantém decimais e frações como estão — só inteiros puros são normalizados,
    para que '5' e '05' (mesma quantidade) não contem como eixo divergente.
    """
    return str(int(v)) if v.isdigit() else v


def _algum_eixo_diverge(
    dims_a: set[tuple[str, str]], dims_b: set[tuple[str, str]]
) -> bool:
    """True se A e B têm ALGUM eixo em comum sem nenhum valor compartilhado.

    Espelha `matcher._dimensao_diverge`, mas opera sobre os pares já
    extraídos. Avaliado por EIXO: um eixo que coincide (mesma tensão) NÃO
    cancela a divergência de outro eixo (seção do cabo). É essa precedência
    que impede cabo 2.5mm²/750V de casar 4mm²/750V com relevância plena.
    """
    eixos_comuns = {e for e, _ in dims_a} & {e for e, _ in dims_b}
    for eixo in eixos_comuns:
        va = {v for e, v in dims_a if e == eixo}
        vb = {v for e, v in dims_b if e == eixo}
        if not (va & vb):
            return True
    return False


# === Tier 3: keyword discriminadora (Q12 / 2026-06) ===
# Atributos genéricos que aparecem em muitos produtos da MESMA categoria —
# não distinguem subproduto. A "discriminadora" é a keyword que sobra depois
# de remover categoria + atributos genéricos + dimensões: tipicamente uma
# marca/tipo específico ("philips", "multicraft", "lonax", "neon").
_ATRIBUTOS_GENERICOS = frozenset({
    "ponta", "cabo", "isolante", "base", "tipo", "tipos", "cor", "cores",
    "preto", "preta", "branco", "branca", "azul", "verde", "vermelho",
    "amarelo", "cinza", "reforcado", "reforcada", "flexivel", "rigido",
    "rigida", "macho", "femea", "simples", "dupla", "duplo", "redondo",
    "redonda", "quadrado", "quadrada", "chato", "chata", "liso", "lisa",
    "comum", "profissional", "manual", "automatico", "automatica",
    "eletrico", "eletrica", "eletricas", "metalico", "metalica", "metalicos",
    "metalicas", "plastico", "plastica", "aco", "carbono", "temperado",
    "cromado", "galvanizado", "inox", "longo", "longa", "curto", "curta",
    "grande", "pequeno", "medio", "media", "novo", "nova",
    # Características construtivas que aparecem na descrição do edital mas
    # raramente no NOME do produto no catálogo — penalizar por elas geraria
    # falso negativo (o produto pode ter a característica sem citá-la).
    "cantos", "ilhoses", "ilhos", "reforcado", "reforcada", "reforcados",
    "reforcadas", "reforco", "borda", "bordas", "furos", "furado",
    "furada", "soldado", "soldada", "rosca", "rosqueado",
})


def _keyword_discriminadora(desc: str | None) -> Optional[str]:
    """A keyword mais específica do edital, fora da categoria e dos atributos
    genéricos. Tipicamente marca/tipo ("philips") que define o subproduto.

    Retorna None quando não há candidato claro (degrada gracioso — sem
    penalidade no `relevancia`).

    >>> _keyword_discriminadora('Chave de fenda com ponta Philips 3/16')
    'philips'
    >>> # Sem marca/tipo — só medidas e características → None (sem penalidade)
    >>> _keyword_discriminadora(
    ...     'LONA PLÁSTICA ESPESSURA 200 MICRA LARGURA 8M COR PRETA '
    ...     'COM CANTOS REFORÇADOS E ILHOSES') is None
    True
    """
    if not desc:
        return None
    cats = set(_primeiras_kw_categoria(desc, 2))
    # Fonte: keywords DISTINTIVAS (top do texto, já sem ruído de domínio e
    # numerais). Isso garante que o discriminador é um termo PRINCIPAL —
    # marca/tipo no começo da descrição — e não uma característica
    # construtiva de cauda ("ilhoses", "cantos") que o produto pode ter
    # sem citar no nome.
    for kl in keywords_distintivas(desc, 6):
        if kl in cats or kl in _ATRIBUTOS_GENERICOS:
            continue
        if any(ch.isdigit() for ch in kl):   # dimensão/spec → Tier 2 cuida
            continue
        return kl
    return None


def relevancia(original: str | None, candidato: str | None) -> float:
    """
    Score 0..1 de quão bem `candidato` casa com `original`.

    Algoritmo (calibrado em 2026-05-27 com diagnóstico real do TRE-PE):

    1. **Base** = `keywords_casadas / min(len(orig), len(cand))`.
       O `min` resolve dois problemas simétricos:
       - Editais com descrição longa (50+ tokens) viviam com score baixo
         se dividíamos pelo `len(orig)` — diluição.
       - Candidatos detalhados ("abraçadeira foxlux 2.5x10cm c/100") viviam
         com score baixo se dividíamos pelo `len(cand)` — mesma diluição.
       Pegar o `min` casa o cálculo pela parte mais sucinta da comparação.

    2. **Veto/boost de categoria**: pega as 2 primeiras keywords ≥4 chars
       não-numéricas do edital (a "categoria" do produto: "filtro+linha",
       "lona+plastica", "bateria+automotiva"). Conta quantas dessas
       categorias batem nas keywords do candidato:
       - 0 de N → score × 0.3 (veto pesado — produto de categoria errada)
       - N de N → score × 1.2 (boost — produto certo na categoria certa)
       - Parcial → neutro

    3. **Boost de typo/ordem**: se `token_sort_ratio` entre as descrições
       inteiras é ≥ 90 (typo óbvio ou ordem trocada) E pelo menos uma
       keyword casa, devolve no mínimo `0.85`. Resolve `PARAFUZO`/`PARAFUSO`
       e `PVC ADAPTADOR`/`ADAPTADOR PVC` sem afrouxar o filtro para o resto.

    Threshold recomendado no scraper: **0.35** (calibrado em 30 candidatos
    reais — 8/9 verdadeiros passam, 0/21 falsos passam).

    >>> # Vergalhão vs Corrente — categorias diferentes
    >>> relevancia("VERGALHAO ACO CA-50 12,5MM",
    ...            "CORRENTE SOLDADA SAO RAPHAEL 6.5MM SACO C/12,5KG") < 0.35
    True
    >>> # Abraçadeira — match limpo
    >>> relevancia("ABRAÇADEIRA DE NYLON 100 X 2.5",
    ...            "ABRAÇADEIRA DE NYLON FOXLUX BRANCA 2.5X10CM C/100") >= 0.7
    True
    >>> # Abreviação 'sold.' encontra 'soldavel'
    >>> relevancia('ADAPTADOR PVC SOLD. CURTO DE 50 X 1.1/2"',
    ...            'ADAPTADOR SOLDÁVEL CURTO AMANCO 50X1.1/2') >= 0.7
    True
    >>> # Typo no edital — boost via token_sort_ratio
    >>> relevancia('PARAFUSO 10MM', 'PARAFUZO 10MM') >= 0.7
    True
    >>> # Ordem trocada
    >>> relevancia('PVC ADAPTADOR', 'ADAPTADOR PVC') >= 0.7
    True
    >>> # Filtro de linha do TRE-PE — categoria certa, edital longo
    >>> # vs candidato curto. Diagnóstico mostrou 0.40 na variante F.
    >>> relevancia(
    ...     "Filtro de linha com 5 (CINCO) tomadas elétricas de 10 Amperes",
    ...     "FILTRO DE LINHA MULTICRAFT 2P+T PRETO 10A 05 TOMADAS",
    ... ) >= 0.35
    True
    >>> # Filtro de linha vs Luminária LED — categoria errada (veto)
    >>> relevancia(
    ...     "Filtro de linha com 5 (CINCO) tomadas elétricas de 10 Amperes",
    ...     "LUMINARIA AVANT LED LINHA POP PLASTICO ABS 30W",
    ... ) < 0.35
    True
    """
    if not original or not candidato:
        return 0.0

    ko = keywords(original)
    kc = keywords(candidato)
    if not ko or not kc:
        return 0.0

    casadas = _contar_keywords_casadas(ko, kc)
    base = casadas / min(len(ko), len(kc))

    # Veto/boost de categoria
    cats = _primeiras_kw_categoria(original, n=2)
    if cats:
        kc_limpos = [k.rstrip('".,') for k in kc]
        cat_casadas = 0
        for cat in cats:
            for k in kc_limpos:
                if (cat == k
                        or (len(cat) >= 4 and cat in k)
                        or (len(k) >= 4 and k in cat)):
                    cat_casadas += 1
                    break
        if cat_casadas == 0:
            base *= 0.3
        elif cat_casadas == len(cats):
            base = min(1.0, base * 1.2)

    # Boost de typo / ordem trocada — só dispara se já há alguma
    # afinidade textual (`casadas > 0`) pra evitar que descrições
    # detalhadas coincidentemente próximas em distância de edição
    # passem indevidamente. Aplicado como PISO antes dos ajustes de
    # dimensão/discriminador (que ainda podem rebaixar).
    if casadas > 0:
        norm_o = normalizar_descricao(original)
        norm_c = normalizar_descricao(candidato)
        if (norm_o and norm_c and
                fuzz.token_sort_ratio(norm_o, norm_c) >= _TYPO_TOKEN_SORT_MIN):
            base = max(base, _TYPO_SCORE_PISO)

    # Tier 2 — match de dimensão. Eixos confiáveis (polegada, tomadas,
    # ampere, volt, watt, ca, mm, mm², cm): mesmo eixo+valor → boost;
    # mesmo eixo com valor divergente (3/16 vs 1/8, cabo 2.5mm² vs 4mm²)
    # → penalidade. Distingue subprodutos.
    #
    # A PENALIDADE TEM PRECEDÊNCIA sobre o boost: se ALGUM eixo diverge
    # (ex.: seção do cabo), rebaixamos mesmo que OUTRO eixo coincida (ex.:
    # mesma tensão 750V). Sem isso, dois cabos de seções diferentes mas
    # mesma tensão batiam relevancia=1.0 (preço errado silencioso).
    dims_o = extrair_dimensoes(original)
    dims_c = extrair_dimensoes(candidato)
    if dims_o and dims_c:
        if _algum_eixo_diverge(dims_o, dims_c):
            base *= 0.6                          # mesmo eixo, valor diferente
        elif dims_o & dims_c:                    # alguma dimensão idêntica
            base *= 1.15

    # Tier 3 — keyword discriminadora. Se o edital tem um termo específico
    # (marca/tipo: "philips") e o candidato NÃO o contém, rebaixa (mostra
    # rebaixado, não esconde — decisão do operador 2026-06).
    disc = _keyword_discriminadora(original)
    if disc:
        kc_limpos = [k.rstrip('".,') for k in kc]
        achou = any(
            disc == k or _casa_como_borda(disc, k) or _casa_como_borda(k, disc)
            for k in kc_limpos
        )
        if not achou:
            base *= 0.7

    return min(1.0, base)
