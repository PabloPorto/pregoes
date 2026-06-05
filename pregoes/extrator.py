"""
Extração de itens de PDFs de edital.

Pipeline em 3 passos:

1. **Coleta bruta** — pdfplumber abre o PDF, varre todas as páginas, junta
   todas as tabelas extraídas com seus metadados (nº de página, índice).

2. **Identificação de candidatos** — para cada tabela, tenta identificar
   cabeçalho considerando 1, 2 ou 3 linhas concatenadas (cabeçalhos de
   edital quase sempre quebram em múltiplas linhas: "Quant. | Eleiçã | o").
   Tabelas sem cabeçalho próprio mas em sequência com uma já válida são
   tratadas como continuação.

3. **Filtragem por score semântico** — cada tabela candidata é pontuada
   por `validador.score_tabela`. Só as tabelas com score alto (≥ 0.5)
   contribuem itens. Dentro das tabelas aceitas, cada item é pontuado
   individualmente:
      - score ≥ 0.7  → confianca='extraido'
      - 0.4 ≤ s < 0.7 → confianca='duvidoso'
      - score < 0.4  → descartado (não entra no resultado)

   O resultado final expõe `confianca_global` = (#extraido) / (#extraido+duvidoso)
   para a UI mostrar o quão limpa foi a extração.

Por que essa arquitetura: heurísticas baseadas só em "achei cabeçalho com
palavras conhecidas" capturam várias tabelas-lixo (formulários de proposta,
listas de contatos, tabelas de rubrica orçamentária) que existem em qualquer
edital real. Score por conteúdo distingue tabela-de-itens de tabela-com-aparência-de-itens.

Quando o extrator não conseguir produzir confiança suficiente, a UI ainda
tem o fallback de mapeamento manual (Fase 1.0.C — tabela `mapeamentos_pdf`).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber

from .validador import ScoreTabela, score_linha, score_tabela


# === Vocabulário de cabeçalhos ===
# Ampliado a partir da inspeção de editais reais (TRE-PE, SEI/HU,
# Hidráulicos, Hash, etc.). Quando aparecer cabeçalho novo, basta
# adicionar aqui — não muda lógica.

PALAVRAS_CHAVE = {
    "descricao": [
        "discriminação", "discriminacao", "descrição", "descricao",
        "especificação", "especificacao", "especificações", "especificacoes",
        "produto", "objeto", "item descrição", "descricao do item",
        "descritivo", "descrição do produto",
    ],
    "quantidade": [
        "quantidade", "quant", "qtd", "qtde", "quant.", "qtd.",
    ],
    "unidade": [
        "unidade", "unid", "und", "u.m.", "um",
        "unidade de fornecimento", "unidade de medida",
    ],
    "valor_referencia": [
        "valor unit", "valor de referência", "valor de referencia",
        "preço unit", "preco unit", "preço estimado", "preco estimado",
        "valor unitário", "valor unitario", "preço de referência",
        "preço unitário máximo", "preco unitario maximo",
        "preço máximo", "preco maximo", "valor máximo", "valor maximo",
        "valor unit.", "preço unit.",
    ],
    "codigo_orgao": [
        # 'item' é frágil (várias tabelas têm); priorizamos 'nº item', 'cod'
        "nº item", "no item", "nº do item", "código", "codigo",
        "cod.", "cod ", "item nº",
    ],
}

# Aliás: 'item' sozinho aparece em muitas tabelas-lixo. Aceitamos só se NÃO
# houver outro candidato a código (lookahead na função de detecção).
PALAVRAS_CHAVE_FRACAS = {
    "codigo_orgao": ["item", "nº", "n°", "n."],
}


@dataclass
class ItemExtraido:
    """Uma linha de produto extraída do edital."""
    item_numero: Optional[int] = None
    codigo_orgao: Optional[str] = None
    descricao: str = ""
    unidade: Optional[str] = None
    quantidade: Optional[float] = None
    valor_referencia: Optional[float] = None
    pagina: Optional[int] = None
    confianca: str = "extraido"   # "extraido" | "duvidoso"


@dataclass
class ResultadoExtracao:
    """Tudo que saiu do PDF mais metadados."""
    arquivo: str
    itens: list[ItemExtraido] = field(default_factory=list)
    tabelas_brutas: list[list[list[str]]] = field(default_factory=list)
    mapa_colunas_detectado: dict[str, int] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)
    confianca_global: float = 1.0      # 0..1 — proporção de itens 'extraido'

    @property
    def total_itens(self) -> int:
        return len(self.itens)

    @property
    def total_duvidosos(self) -> int:
        return sum(1 for i in self.itens if i.confianca == "duvidoso")


# === Funções utilitárias ===

_WHITESPACE_RE = re.compile(r"\s+")


def _normaliza(texto) -> str:
    """Caixa baixa, remove acentos e colapsa espaços múltiplos.

    Colapsar espaços é crítico: tabelas extraídas com modelos visuais
    (Docling) frequentemente devolvem `'Preço  Unitário  Máximo'` com
    espaços duplos ou triplos entre palavras — sem colapsar, o teste
    `'preço unitário máximo' in texto_normalizado` falha.
    """
    if not texto:
        return ""
    s = unicodedata.normalize("NFD", str(texto))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower().strip()
    return _WHITESPACE_RE.sub(" ", s)


_MILHAR_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")


def _para_numero(valor) -> Optional[float]:
    """Converte string brasileira ('1.234,56') para float."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip()
    s = re.sub(r"[R\$\s]", "", s)
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    # Sem vírgula, o ponto é ambíguo: pode ser decimal ('1.5') ou separador
    # de milhar ('1.500' = mil e quinhentos). Editais brasileiros escrevem
    # quantidades grandes com ponto de milhar; tratá-lo como decimal
    # transformava '15.000' em 15.0 (perda silenciosa de 3 ordens). Quando a
    # string casa o padrão de grupos de milhar (1-3 dígitos + blocos de 3),
    # removemos os pontos. '1.5' (1 dígito após ponto) segue como decimal.
    elif _MILHAR_RE.match(s):
        s = s.replace(".", "")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _para_int(valor) -> Optional[int]:
    n = _para_numero(valor)
    return int(n) if n is not None else None


def _concatenar_celula(cel) -> str:
    """Normaliza e junta múltiplas linhas internas de uma célula em
    uma única string para detectar cabeçalhos quebrados em várias linhas."""
    if cel is None:
        return ""
    return str(cel).replace("\n", " ").strip()


def _identifica_cabecalho_em_linha(linha: list) -> dict[str, int]:
    """Detecta mapa {campo: idx_coluna} olhando 1 linha.

    Procura primeiro pelas palavras-chave fortes; só se nenhum campo
    'codigo_orgao' bater, tenta as fracas ('item', 'nº'). Isso evita
    capturar tabelas que têm só 'Item / Descrição / ...' (comuns em
    sumários e formulários de fornecedor) sem o resto do contexto.
    """
    mapa: dict[str, int] = {}
    for idx, cel in enumerate(linha or []):
        norm = _normaliza(_concatenar_celula(cel))
        if not norm:
            continue
        for campo, palavras in PALAVRAS_CHAVE.items():
            if campo in mapa:
                continue
            for pal in palavras:
                if pal in norm:
                    mapa[campo] = idx
                    break

    if "codigo_orgao" not in mapa:
        for idx, cel in enumerate(linha or []):
            norm = _normaliza(_concatenar_celula(cel))
            for pal in PALAVRAS_CHAVE_FRACAS["codigo_orgao"]:
                if norm == pal or norm.startswith(pal + " ") or norm.endswith(" " + pal):
                    mapa["codigo_orgao"] = idx
                    break
            if "codigo_orgao" in mapa:
                break

    return mapa


def _identifica_cabecalho_multilinha(tabela: list[list], max_linhas: int = 3) -> tuple[dict[str, int], int]:
    """
    Tenta identificar cabeçalho considerando até `max_linhas` iniciais
    concatenadas. Algumas tabelas de edital têm cabeçalho quebrado em
    2-3 linhas (Quant. / Eleiçã / o) — concatenar resolve.

    Retorna (mapa, n_linhas_consumidas). Se nenhum cabeçalho válido for
    encontrado, retorna ({}, 0).
    """
    if not tabela:
        return {}, 0

    # Estratégia: o MENOR n que já produz cabeçalho válido vence. Aumentar n
    # concatena a 1ª linha de DADOS ao cabeçalho — se essa linha contiver por
    # acaso 'unidade'/'valor unitário'/'item'/'código', o n maior produz um
    # mapa "mais rico" e ENGOLE o primeiro item (perda silenciosa). Por isso
    # paramos no primeiro n cujo cabeçalho é válido (descrição E quantidade);
    # só crescemos o cabeçalho enquanto esses campos centrais ainda faltam.
    for n in range(1, min(max_linhas, len(tabela)) + 1):
        # Concatena as primeiras n linhas célula a célula
        n_cols = max(len(l) for l in tabela[:n] if l)
        linha_concat = []
        for c in range(n_cols):
            partes = []
            for linha in tabela[:n]:
                if c < len(linha or []) and linha[c]:
                    partes.append(str(linha[c]).replace("\n", " ").strip())
            linha_concat.append(" ".join(partes))

        mapa = _identifica_cabecalho_em_linha(linha_concat)
        if _eh_cabecalho_valido(mapa):
            return mapa, n  # menor n válido vence — não engole a 1ª linha de dados

    # Nenhum n produziu cabeçalho válido: contrato preservado ({}, 0), o
    # chamador cai para o mapa herdado / fallback de mapeamento manual.
    return {}, 0


def _eh_cabecalho_valido(mapa: dict[str, int]) -> bool:
    """Cabeçalho útil precisa ter descrição + quantidade. Unidade ajuda mas
    não é obrigatória (algumas tabelas-itens não têm coluna unidade isolada
    — incorporam dentro da descrição)."""
    return "descricao" in mapa and "quantidade" in mapa


def _eh_modelo_de_proposta(linha: list) -> bool:
    """Detecta linha-cabeçalho de modelo de proposta em branco. Casa em
    'MARCA/MODELO' que aparece em anexos do edital de Araruna 06/2026."""
    for cel in linha or []:
        norm = _normaliza(_concatenar_celula(cel))
        if "marca/modelo" in norm or norm in ("marca", "modelo"):
            return True
    return False


# === Pipeline principal ===

@dataclass
class _TabelaBruta:
    pagina: int
    indice_pagina: int
    linhas: list[list]


@dataclass
class _TabelaCandidata:
    bruta: _TabelaBruta
    mapa: dict[str, int]
    linha_inicial_dados: int
    itens: list[ItemExtraido]
    score: ScoreTabela


def _coletar_tabelas(caminho_pdf: Path) -> list[_TabelaBruta]:
    """Abre o PDF e devolve todas as tabelas em ordem de página/posição."""
    out: list[_TabelaBruta] = []
    with pdfplumber.open(caminho_pdf) as pdf:
        for n_pag, page in enumerate(pdf.pages, start=1):
            try:
                tabs = page.extract_tables() or []
            except Exception:
                continue
            for idx, tab in enumerate(tabs):
                if tab and len(tab) >= 1:
                    out.append(_TabelaBruta(pagina=n_pag, indice_pagina=idx, linhas=tab))
    return out


def _extrair_item_da_linha(linha: list, mapa: dict[str, int], pagina: int) -> Optional[ItemExtraido]:
    """Extrai uma linha de tabela em ItemExtraido seguindo o mapa de colunas.

    Retorna None apenas quando a linha está completamente vazia. Filtragem
    de lixo (totais, fragmentos) acontece DEPOIS via score, não aqui — esta
    função é apenas parser estrutural.
    """
    if not linha or all(_normaliza(c) == "" for c in linha):
        return None

    def _get(campo: str) -> Optional[str]:
        idx = mapa.get(campo)
        if idx is None or idx >= len(linha):
            return None
        v = linha[idx]
        if v is None:
            return None
        return str(v).strip().replace("\n", " ")

    descricao = _get("descricao") or ""
    if not descricao or len(descricao) < 3:
        return None

    quantidade = _para_numero(_get("quantidade"))
    valor_ref = _para_numero(_get("valor_referencia"))
    codigo = _get("codigo_orgao")

    return ItemExtraido(
        item_numero=_para_int(codigo),
        codigo_orgao=codigo,
        descricao=descricao,
        unidade=_get("unidade"),
        quantidade=quantidade,
        valor_referencia=valor_ref,
        pagina=pagina,
    )


def _processar_tabela(bruta: _TabelaBruta, mapa_herdado: dict[str, int]) -> Optional[_TabelaCandidata]:
    """
    Tenta tornar uma tabela bruta em candidata. Devolve:
    - candidata com cabeçalho próprio (se conseguiu detectar)
    - candidata usando mapa_herdado (se a tabela parece continuação)
    - None se nem isso funciona

    Ignora tabelas que parecem 'modelo de proposta'.
    """
    for i in range(min(3, len(bruta.linhas))):
        if _eh_modelo_de_proposta(bruta.linhas[i]):
            return None

    mapa, n_consumidas = _identifica_cabecalho_multilinha(bruta.linhas)
    if mapa:
        itens = _extrair_itens(bruta, mapa, n_consumidas)
        sc = score_tabela([
            score_linha(i.descricao, i.quantidade, i.unidade, i.valor_referencia)
            for i in itens
        ])
        return _TabelaCandidata(bruta=bruta, mapa=mapa, linha_inicial_dados=n_consumidas, itens=itens, score=sc)

    if mapa_herdado:
        itens = _extrair_itens(bruta, mapa_herdado, 0)
        sc = score_tabela([
            score_linha(i.descricao, i.quantidade, i.unidade, i.valor_referencia)
            for i in itens
        ])
        return _TabelaCandidata(bruta=bruta, mapa=mapa_herdado, linha_inicial_dados=0, itens=itens, score=sc)

    return None


def _extrair_itens(bruta: _TabelaBruta, mapa: dict[str, int], linha_inicial: int) -> list[ItemExtraido]:
    out: list[ItemExtraido] = []
    for linha in bruta.linhas[linha_inicial:]:
        it = _extrair_item_da_linha(linha, mapa, bruta.pagina)
        if it is not None:
            out.append(it)
    return out


def _agrupar_em_blocos(candidatas: list[_TabelaCandidata]) -> list[list[_TabelaCandidata]]:
    """
    Agrupa candidatas adjacentes em sequência que compartilham o mesmo mapa
    de colunas (mesma "tabela de itens" quebrada em várias páginas).

    Tabelas com mapa diferente quebram o bloco. Resultado: lista de blocos,
    onde cada bloco é uma sequência de candidatas que representam a MESMA
    tabela lógica.
    """
    blocos: list[list[_TabelaCandidata]] = []
    atual: list[_TabelaCandidata] = []
    mapa_atual: dict[str, int] = {}
    for c in candidatas:
        if not atual or c.mapa == mapa_atual:
            atual.append(c)
            mapa_atual = c.mapa
        else:
            blocos.append(atual)
            atual = [c]
            mapa_atual = c.mapa
    if atual:
        blocos.append(atual)
    return blocos


def _score_bloco(bloco: list[_TabelaCandidata]) -> float:
    """Score agregado de um bloco: media ponderada pelo nº de linhas válidas."""
    total_validas = sum(c.score.n_linhas_validas for c in bloco)
    if total_validas == 0:
        return 0.0
    media_ponderada = sum(c.score.score * c.score.n_linhas_validas for c in bloco) / total_validas
    return media_ponderada


# Thresholds calibrados na inspeção dos 5 PDFs reais. Revisitar quando
# aparecerem novos formatos que produzam falsos negativos.
_THRESHOLD_TABELA_ACEITA = 0.5
_THRESHOLD_LINHA_EXTRAIDA = 0.7
_THRESHOLD_LINHA_DUVIDOSA = 0.4


def processar_tabelas_brutas(
    tabelas_brutas: list[_TabelaBruta],
    arquivo: str,
) -> ResultadoExtracao:
    """Aplica o pipeline de score em tabelas já coletadas.

    Existe separadamente de `extrair_pdf` para permitir que outras camadas
    (Docling visual, mapeamento manual) reusem a mesma lógica de scoring
    e seleção. Cada camada produz `_TabelaBruta`s à sua maneira e chama
    esta função para o restante.
    """
    resultado = ResultadoExtracao(arquivo=arquivo)
    resultado.tabelas_brutas = [t.linhas for t in tabelas_brutas]

    if not tabelas_brutas:
        resultado.avisos.append(
            "Nenhuma tabela detectada no PDF. Pode ser PDF escaneado "
            "(precisa OCR) ou layout incomum."
        )
        resultado.confianca_global = 0.0
        return resultado

    candidatas: list[_TabelaCandidata] = []
    mapa_herdado: dict[str, int] = {}
    for bruta in tabelas_brutas:
        cand = _processar_tabela(bruta, mapa_herdado)
        if cand is not None:
            candidatas.append(cand)
            mapa_herdado = cand.mapa

    if not candidatas:
        resultado.avisos.append(
            "Tabelas encontradas, mas nenhum cabeçalho de itens identificado. "
            "Mapeamento manual necessário."
        )
        resultado.confianca_global = 0.0
        return resultado

    # Agrupa em blocos e escolhe os blocos com score alto. Aceitar múltiplos
    # blocos cobre o caso "edital com vários grupos" (TRE-PE: grupo 01, 02, ...).
    blocos = _agrupar_em_blocos(candidatas)
    blocos_aceitos = [b for b in blocos if _score_bloco(b) >= _THRESHOLD_TABELA_ACEITA]

    if not blocos_aceitos:
        # Não bateu threshold em nenhum bloco. Pega o melhor como melhor
        # esforço — a UI vai mostrar confiança baixa e o operador pode
        # optar pelo mapeamento manual.
        melhor = max(blocos, key=_score_bloco, default=None)
        if melhor is not None:
            blocos_aceitos = [melhor]
            resultado.avisos.append(
                "Confiança baixa nas tabelas detectadas — considere o "
                "mapeamento manual ou revise os itens cuidadosamente."
            )

    if not blocos_aceitos:
        resultado.confianca_global = 0.0
        return resultado

    resultado.mapa_colunas_detectado = blocos_aceitos[0][0].mapa

    n_extraidos = 0
    n_duvidosos = 0
    for bloco in blocos_aceitos:
        for cand in bloco:
            for item in cand.itens:
                sc = score_linha(item.descricao, item.quantidade, item.unidade,
                                  item.valor_referencia).score
                if sc >= _THRESHOLD_LINHA_EXTRAIDA:
                    item.confianca = "extraido"
                    n_extraidos += 1
                elif sc >= _THRESHOLD_LINHA_DUVIDOSA:
                    item.confianca = "duvidoso"
                    n_duvidosos += 1
                else:
                    # Descartado: lixo claro (placeholder, prefixo, etc.)
                    continue
                resultado.itens.append(item)

    total_aceitos = n_extraidos + n_duvidosos
    resultado.confianca_global = (n_extraidos / total_aceitos) if total_aceitos else 0.0
    return resultado


def tem_texto_extraivel(caminho_pdf: str | Path, min_chars: int = 80) -> bool:
    """True se o PDF tem texto suficiente para ler (não é escaneado/imagem).

    Checa as primeiras páginas e para assim que acumula `min_chars`. Na dúvida
    (erro ao abrir), retorna True — deixa o pipeline tentar e falhar com aviso
    próprio. Usado pela UI para distinguir "PDF escaneado" de "não achei a
    tabela", dando ao operador uma mensagem clara (P0.6).
    """
    try:
        import pdfplumber
        total = 0
        with pdfplumber.open(caminho_pdf) as pdf:
            for page in pdf.pages[:5]:
                total += len((page.extract_text() or "").strip())
                if total >= min_chars:
                    return True
        return total >= min_chars
    except Exception:
        return True


def extrair_pdf(caminho_pdf: str | Path) -> ResultadoExtracao:
    """Extrai itens via pdfplumber + score semântico.

    Camada 1 do pipeline. Rápida (~1-3s/edital), sem dependências pesadas.
    Resolve PDFs com layout tabular limpo. Para layouts complexos, usar
    `extrair_com_pipeline` que automaticamente tenta camadas adicionais.
    """
    caminho_pdf = Path(caminho_pdf)
    tabelas = _coletar_tabelas(caminho_pdf)
    return processar_tabelas_brutas(tabelas, arquivo=str(caminho_pdf))


# === Pipeline multi-camada ===

# Confiança mínima para aceitar resultado da camada e não tentar a seguinte.
# Abaixo disso, escalonamos automaticamente para a camada mais robusta.
_CONFIANCA_SUFICIENTE = 0.95


def extrair_com_pipeline(
    caminho_pdf: str | Path,
    *,
    callback_progresso_llm=None,
) -> ResultadoExtracao:
    """Pipeline em camadas com fallback automático.

    Ordem:
    1. **pdfplumber** + score semântico (rápido, offline, sem custo).
       Se conf ≥ 95% → entrega; senão segue.
    2. **Gemini 2.0 Flash** (LLM) — automático quando Camada 1 falha
       E o operador configurou API key. Resolve encoding, multi-página
       e falsos positivos. Free tier do Google cobre o volume alvo.
       Se conf ≥ 95% → entrega; senão devolve o melhor dos dois.
    3. **Mapeamento manual** (UI) — quando 1 e 2 falham, operador
       escolhe tabela e mapeia colunas. Sistema aprende para próximos
       editais.

    Args:
        caminho_pdf: PDF a processar.
        callback_progresso_llm: opcional, callback de progresso da Camada 2
            (Gemini). Útil para UI mostrar barra de progresso já que LLM
            demora 30-90s. Assinatura: `cb(pag_atual, total_pag, msg)`.

    Por que Docling não é mais Camada 2 automática:
    - Era lento (5-10min/edital, vs 30-90s do Gemini)
    - Fragmentava tabelas multi-página
    - Tinha problemas de encoding
    - Continua disponível SOB DEMANDA na tela de mapeamento manual.
    """
    res = extrair_pdf(caminho_pdf)
    if res.confianca_global >= _CONFIANCA_SUFICIENTE:
        # Caminho feliz: banner verde de "alta confiança" da revisão já
        # comunica o sucesso. Não precisa de aviso técnico extra.
        return res

    # Camada 1 < 95%. Tenta Gemini se disponível.
    try:
        from . import extrator_llm
        if not extrator_llm.disponivel():
            res.avisos.insert(
                0,
                "Confiança abaixo de 95%. Configure a IA em "
                "Configurações para extração automática mais precisa, "
                "ou use o Mapeamento Manual."
            )
            return res
    except ImportError:
        res.avisos.insert(
            0, "Confiança abaixo de 95% — use Mapeamento Manual."
        )
        return res

    # Roda Gemini
    try:
        res_llm = extrator_llm.extrair_via_gemini(
            caminho_pdf,
            callback_progresso=callback_progresso_llm,
        )
    except Exception:
        # Operador não precisa saber do stack trace; o aviso vermelho
        # da Camada 1 já indica que vai pra mapeamento manual.
        return res

    # Escolhe o melhor resultado entre Camada 1 e Camada 2.
    # Princípio: confiança vale mais que quantidade. pdfplumber tende a
    # inflar a contagem capturando linhas-fragmento como itens duvidosos;
    # a IA distingue itens reais e devolve menos itens mas todos limpos.
    if res_llm.total_itens > 0 and res_llm.confianca_global >= res.confianca_global:
        # Preserva tabelas brutas do pdfplumber para a UI mostrar no
        # mapeamento manual caso operador queira ajustar
        res_llm.tabelas_brutas = res.tabelas_brutas
        # Avisos técnicos do extrator_llm são removidos — UI já mostra
        # o banner de confiança alta no topo, é suficiente.
        res_llm.avisos = []
        return res_llm

    # Mantém pdfplumber sem aviso adicional — o aviso vermelho de baixa
    # confiança da Camada 1 (que já está no resultado) é o sinal certo
    # para o operador entrar no mapeamento manual.
    return res
