"""
Camada de extração via LLM (Gemini 2.0 Flash) — Camada 2 automática
do pipeline quando pdfplumber retorna confiança < 95%.

Por que LLM e não Docling (atual Camada 2 opcional):
- Acurácia em tabelas BR estado-da-arte (~98-99% em testes similares)
- Resolve encoding na origem (lê texto extraído pelo pdfplumber, mas
  o modelo entende contexto — "DOBRADI˙A" vira "DOBRADIÇA")
- Resolve multi-página (recebe a página inteira, entende seções)
- Resolve falsos positivos (modelo distingue tabela de itens de
  tabela de endereços, contatos, totais)
- Tempo: ~30-90s por edital de 39 págs vs 5-10min do Docling
- Free tier do Gemini cobre folgadamente o volume alvo

Trade-off aceito (decisão Q11 / CLAUDE.md flexibilizado em 2026-05-27):
- Requer internet quando acionado (Camada 1 continua offline)
- Dados do edital vão para servidores Google — aceitável porque
  editais são públicos por definição (não há vazamento de privado)

Estratégia de chamada: texto da página, não imagem. Mais barato em
tokens, suficientemente preciso quando pdfplumber consegue extrair o
texto (que é a maioria dos editais). Para PDFs escaneados, usar a
versão com imagem (caminho não implementado ainda — adicionar quando
aparecer caso real).

Falhas: se Gemini retornar JSON inválido, rede falhar, ou rate limit,
a função levanta — quem chama (pipeline) deve cair para o fallback
(Docling/mapeamento manual).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber

from . import credenciais
from .extrator import (
    ItemExtraido,
    ResultadoExtracao,
    _para_int,
    _para_numero,
    _THRESHOLD_LINHA_DUVIDOSA,
    _THRESHOLD_LINHA_EXTRAIDA,
)
from .validador import score_linha


# Modelo padrão: `gemini-2.5-flash-lite` tem 1M de contexto e free tier
# ativo (1000 req/dia, 15 RPM). Modelo equilibrado para nosso caso —
# o gemini-2.0-flash mais simples teve free tier desativado para contas
# novas (descoberto em uso real, 2026-05-27).
MODELO_DEFAULT = "gemini-2.5-flash-lite"

# Limite total de caracteres enviados em uma chamada. 1M tokens ≈ 4M chars
# em pt-BR; deixamos 800k como margem para prompt + output.
# Editais reais raramente passam de 400k chars (extraídos do PDF).
MAX_CHARS_POR_EDITAL = 800_000

# Threshold de confiança da linha pra entrar no resultado final.
# Mesma escala do extrator: >=0.7 extraido, >=0.4 duvidoso.
_THRESHOLD_LINHA = _THRESHOLD_LINHA_DUVIDOSA


PROMPT_TABELA = """Você é um extrator especializado em tabelas de editais de licitação pública brasileiros.

Recebe o TEXTO COMPLETO de um edital, com todas as páginas concatenadas
(marcadores `=== PÁGINA N ===` separam-nas). Sua tarefa: encontrar a
TABELA DE ITENS / PRODUTOS / MATERIAIS do termo de referência e
extrair TODOS os itens dela.

DESCARTE estas tabelas e seções (NÃO são itens):
- Tabelas de contatos, endereços, fiscais, gestores responsáveis
- Formulários em branco, anexo "modelo de proposta" sem dados de produtos
- Tabelas de totais, subtotais, rubricas orçamentárias, fontes de recurso
- Cabeçalhos de seção, rodapés, paginação
- Cláusulas contratuais, condições gerais, instruções

Reconheça a tabela de itens pela presença das colunas típicas:
"ITEM / DESCRIÇÃO / UNIDADE / QUANTIDADE / VALOR DE REFERÊNCIA" ou similar.

Para cada item REAL da tabela de produtos, retorne em JSON:
- "numero": número do item no edital (int ou null se não houver)
- "descricao": descrição completa e limpa (string, mantenha acentos e ç corretos)
- "unidade": unidade de medida brasileira (string, ex: "UN", "KG", "M", "M2", "CX", "PCT")
- "quantidade": quantidade demandada (number)
- "valor_referencia": valor unitário máximo / de referência (number ou null)

Regras IMPORTANTES:
- Retorne SOMENTE um array JSON válido, SEM prosa ou explicação antes/depois.
- Inclua TODOS os itens da tabela de produtos, mesmo que ela se estenda
  por várias páginas. Itens com numeração contínua (1, 2, 3, ...) DEVEM
  todos ser retornados.
- Mantenha acentos e ç corretos — corrija defeitos de extração se reconhecer
  (ex: "DOBRADI˙A" → "DOBRADIÇA", "MANUTEN`AO" → "MANUTENÇÃO").
- NÃO invente itens. Se em dúvida sobre se uma linha é item ou ruído, descarte.
- NÃO inclua linhas de "TOTAL", "SUBTOTAL", "VALOR GLOBAL" como itens.
- Se a quantidade aparecer em mais de uma coluna (ex: "Quant. Ordinário"
  e "Quant. Eleição" e "Quant. Total"), use a QUANTIDADE TOTAL.

TEXTO DO EDITAL:
\"\"\"
{texto}
\"\"\"

JSON (array de objetos, ou [] se não houver tabela de itens):"""


@dataclass
class _Stats:
    paginas_processadas: int = 0
    paginas_com_itens: int = 0
    chamadas_api: int = 0
    erros_api: int = 0
    erros_json: int = 0


def disponivel() -> bool:
    """True se o SDK do Google Gen AI está instalado E há API key configurada."""
    try:
        import importlib.util
        if importlib.util.find_spec("google.genai") is None:
            return False
    except Exception:
        return False
    return credenciais.carregar_chave_api("gemini") is not None


def extrair_via_gemini(
    caminho_pdf: str | Path,
    *,
    api_key: Optional[str] = None,
    modelo: str = MODELO_DEFAULT,
    callback_progresso=None,
) -> ResultadoExtracao:
    """Extrai itens de um PDF inteiro via Gemini em **1 única chamada**.

    Estratégia (refatorada em 2026-05-27 após bug em produção):
    - Concatena texto de TODAS as páginas com marcadores `=== PÁGINA N ===`
    - Envia tudo em UMA chamada (modelo aceita 1M de tokens de contexto)
    - Custo: ~$0.01-0.02 por edital típico no `gemini-2.5-flash-lite`
    - 1 req conta como 1/1000 do free tier diário — muito folgado

    Por que 1 chamada e não N (uma por página):
    - Free tier do `gemini-2.0-flash` foi removido para contas novas; usar
      `gemini-2.5-flash-lite` que tem free tier ativo.
    - 93 chamadas por edital exauriam o RPM rapidamente E desperdiçavam
      ~80% delas em páginas sem tabela de itens (capa, cláusulas, anexos).
    - 1 chamada com tudo: modelo entende contexto, junta multi-página
      naturalmente, ignora seções não-relevantes pelo prompt.

    Args:
        caminho_pdf: PDF a processar.
        api_key: se None, carrega de `credenciais.carregar_chave_api("gemini")`.
        modelo: nome do modelo (default `gemini-2.5-flash-lite`).
        callback_progresso: opcional, `cb(etapa, total, msg)` chamado em
            cada fase (leitura PDF, chamada API, parse).

    Levanta:
        - `RuntimeError` se API key não configurada ou texto vazio.
        - `ImportError` se `google-genai` não estiver instalado.

    Retorna `ResultadoExtracao` com `confianca_global` calculado pelo
    validador semântico (uniforme com outras camadas — não confiamos
    cegamente, validamos a saída do LLM).
    """
    caminho_pdf = Path(caminho_pdf)

    if api_key is None:
        api_key = credenciais.carregar_chave_api("gemini")
    if not api_key:
        raise RuntimeError(
            "Chave de API do Gemini não configurada. "
            "Acesse Configurações → IA (Gemini) para colar a chave."
        )

    from google import genai
    client = genai.Client(api_key=api_key)

    resultado = ResultadoExtracao(arquivo=str(caminho_pdf))

    # Etapa 1: extrai texto de todas as páginas + concatena
    if callback_progresso:
        callback_progresso(0, 3, "Lendo PDF…")

    blocos: list[str] = []
    n_paginas = 0
    n_paginas_com_texto = 0
    with pdfplumber.open(caminho_pdf) as pdf:
        n_paginas = len(pdf.pages)
        for n_pag, page in enumerate(pdf.pages, start=1):
            texto = (page.extract_text() or "").strip()
            if len(texto) < 50:
                continue
            n_paginas_com_texto += 1
            blocos.append(f"=== PÁGINA {n_pag} ===\n{texto}")

    if not blocos:
        resultado.avisos.append(
            "Gemini: PDF sem texto extraível (possivelmente escaneado). "
            "OCR ainda não suportado."
        )
        return resultado

    texto_completo = "\n\n".join(blocos)

    # Trunca se exceder o limite (raríssimo — 800k chars cabem ~93 págs de A4)
    if len(texto_completo) > MAX_CHARS_POR_EDITAL:
        resultado.avisos.append(
            f"Gemini: PDF muito grande ({len(texto_completo):,} chars), "
            f"truncado em {MAX_CHARS_POR_EDITAL:,}. Itens das últimas "
            f"páginas podem ser perdidos."
        )
        texto_completo = texto_completo[:MAX_CHARS_POR_EDITAL]

    # Etapa 2: chamada única ao Gemini
    if callback_progresso:
        callback_progresso(
            1, 3,
            f"Enviando {len(texto_completo):,} chars ({n_paginas_com_texto} págs) à IA…"
        )

    try:
        resposta = client.models.generate_content(
            model=modelo,
            contents=PROMPT_TABELA.format(texto=texto_completo),
        )
        texto_resp = resposta.text or ""
    except Exception as exc:
        # Inclui mensagem completa para debug — operador precisa ver
        # erros de quota, modelo inválido, etc.
        resultado.avisos.append(
            f"Gemini falhou na chamada: {type(exc).__name__}: {exc}"
        )
        return resultado

    if not texto_resp.strip():
        resultado.avisos.append(
            "Gemini retornou resposta vazia. Pode ser safety filter ou "
            "modelo não conseguiu identificar tabela no PDF."
        )
        return resultado

    # Etapa 3: parse JSON + validação
    if callback_progresso:
        callback_progresso(2, 3, "Processando resposta da IA…")

    try:
        lista = _parse_json_lista(texto_resp)
    except (json.JSONDecodeError, ValueError) as exc:
        resultado.avisos.append(
            f"Gemini retornou JSON inválido: {exc}. "
            f"Início da resposta: {texto_resp[:200]!r}"
        )
        return resultado

    for d in lista:
        item = _dict_para_item(d, pagina=0)  # multi-página: pagina=0
        if item is None:
            continue
        sc = score_linha(
            item.descricao, item.quantidade,
            item.unidade, item.valor_referencia,
        ).score
        if sc >= _THRESHOLD_LINHA_EXTRAIDA:
            item.confianca = "extraido"
        elif sc >= _THRESHOLD_LINHA_DUVIDOSA:
            item.confianca = "duvidoso"
        else:
            continue
        resultado.itens.append(item)

    n_extraidos = sum(1 for i in resultado.itens if i.confianca == "extraido")
    total = len(resultado.itens)
    resultado.confianca_global = (n_extraidos / total) if total else 0.0

    resumo = (
        f"Gemini ({modelo}): {total} itens extraídos de "
        f"{n_paginas_com_texto}/{n_paginas} págs com texto, "
        f"{len(lista)} candidatos brutos, {len(texto_completo):,} chars enviados"
    )
    resultado.avisos.insert(0, resumo)

    if callback_progresso:
        callback_progresso(3, 3, f"{total} itens extraídos.")

    return resultado


def _parse_json_lista(texto: str) -> list[dict]:
    """Extrai array JSON da resposta do modelo. Tolera markdown wrappers
    como ```json ... ``` que o Gemini às vezes coloca apesar do prompt."""
    s = texto.strip()

    # Remove cercas markdown se houver
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s)

    # Tenta achar o primeiro array
    inicio = s.find("[")
    fim = s.rfind("]")
    if inicio < 0 or fim < 0 or fim <= inicio:
        if s.strip() == "[]" or not s.strip():
            return []
        raise ValueError(f"sem array JSON na resposta: {s[:120]!r}")

    fatia = s[inicio:fim + 1]
    dados = json.loads(fatia)
    if not isinstance(dados, list):
        raise ValueError(f"esperava array, recebeu {type(dados).__name__}")
    return dados


def _dict_para_item(d: dict, *, pagina: int) -> Optional[ItemExtraido]:
    """Converte um dict do JSON do Gemini em ItemExtraido validado."""
    if not isinstance(d, dict):
        return None

    descricao = (d.get("descricao") or "").strip() if isinstance(d.get("descricao"), str) else ""
    if not descricao or len(descricao) < 3:
        return None

    return ItemExtraido(
        item_numero=_para_int(d.get("numero")),
        codigo_orgao=None,
        descricao=descricao,
        unidade=(d.get("unidade") or "").strip() if d.get("unidade") else None,
        quantidade=_para_numero(d.get("quantidade")),
        valor_referencia=_para_numero(d.get("valor_referencia")),
        pagina=pagina,
        # confianca será setada pelo caller após score_linha
    )
