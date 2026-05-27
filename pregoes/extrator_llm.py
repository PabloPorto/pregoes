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


# Modelo padrão. Free tier cobre folgadamente o volume alvo (1500 req/dia).
# Mudou-se de gemini-2.0-flash-exp para flash (GA) em fev/2025.
MODELO_DEFAULT = "gemini-2.0-flash"

# Limite de caracteres por página enviada ao modelo. PDFs grandes têm
# páginas com 4-8k chars; 12k cobre 99% dos casos sem estourar contexto
# nem dar prompts longos demais.
MAX_CHARS_POR_PAGINA = 12_000

# Threshold de confiança da linha pra entrar no resultado final.
# Mesma escala do extrator: >=0.7 extraido, >=0.4 duvidoso.
_THRESHOLD_LINHA = _THRESHOLD_LINHA_DUVIDOSA


PROMPT_TABELA = """Você é um extrator de tabelas de editais de licitação pública brasileiros.

Receba o TEXTO de uma página do edital. Sua tarefa: extrair os ITENS da
tabela de produtos/materiais. IGNORE:
- Tabelas de contatos, endereços, fiscais, gestores
- Formulários em branco, anexo de modelo de proposta sem dados
- Tabelas de totais, subtotais, rubricas orçamentárias
- Cabeçalhos, rodapés, paginação

Para cada item REAL da tabela de produtos, retorne em JSON:
- "numero": número do item no edital (int ou null se não houver)
- "descricao": descrição completa e limpa (string, mantenha acentos e ç corretos)
- "unidade": unidade de medida brasileira (string, ex: "UN", "KG", "M", "M2", "CX", "PCT")
- "quantidade": quantidade demandada (number)
- "valor_referencia": valor unitário máximo / de referência (number ou null)

Regras importantes:
- Retorne SOMENTE um array JSON válido, SEM prosa ou explicação antes/depois.
- Se a página não tiver tabela de itens, retorne array vazio: []
- Mantenha acentos e ç corretos — corrija defeitos de extração se reconhecer
  (ex: "DOBRADI˙A" → "DOBRADIÇA", "MANUTEN`AO" → "MANUTENÇÃO").
- NÃO invente itens. Se em dúvida sobre se uma linha é item ou ruído, descarte.
- NÃO inclua linhas de "TOTAL", "SUBTOTAL", "VALOR GLOBAL" como itens.

TEXTO DA PÁGINA:
\"\"\"
{texto}
\"\"\"

JSON (array de objetos, ou [] se nenhum item):"""


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
    """Extrai itens de um PDF via Gemini, página por página.

    Args:
        caminho_pdf: PDF a processar.
        api_key: se None, carrega de `credenciais.carregar_chave_api("gemini")`.
        modelo: nome do modelo (default `gemini-2.0-flash`).
        callback_progresso: opcional, `cb(pag_atual, total_pag, msg)` chamado
            após cada página.

    Levanta:
        - `RuntimeError` se API key não configurada.
        - `ImportError` se `google-genai` não estiver instalado.

    Retorna `ResultadoExtracao` no mesmo formato das outras camadas, com
    `confianca_global` calculado pelo validador semântico (igual nos outros
    caminhos — não confiamos cegamente no Gemini, validamos a saída).
    """
    caminho_pdf = Path(caminho_pdf)

    if api_key is None:
        api_key = credenciais.carregar_chave_api("gemini")
    if not api_key:
        raise RuntimeError(
            "Chave de API do Gemini não configurada. "
            "Acesse Configurações → IA (Gemini) para colar a chave."
        )

    # Import local — paga o custo de importar `google.genai` só quando
    # alguém realmente vai usar Gemini.
    from google import genai
    client = genai.Client(api_key=api_key)

    stats = _Stats()
    resultado = ResultadoExtracao(arquivo=str(caminho_pdf))

    with pdfplumber.open(caminho_pdf) as pdf:
        total_pag = len(pdf.pages)
        for n_pag, page in enumerate(pdf.pages, start=1):
            stats.paginas_processadas += 1
            texto = page.extract_text() or ""
            # Páginas sem texto suficiente (escaneadas? em branco?) não
            # geram chamada — economiza tokens e tempo.
            if len(texto.strip()) < 80:
                if callback_progresso:
                    callback_progresso(n_pag, total_pag, "(página sem texto)")
                continue

            # Trunca para não estourar contexto
            texto_chunk = texto[:MAX_CHARS_POR_PAGINA]

            try:
                stats.chamadas_api += 1
                resposta = client.models.generate_content(
                    model=modelo,
                    contents=PROMPT_TABELA.format(texto=texto_chunk),
                )
                texto_resp = resposta.text or ""
            except Exception as exc:
                stats.erros_api += 1
                resultado.avisos.append(
                    f"Gemini falhou na página {n_pag}: "
                    f"{type(exc).__name__}: {exc}"
                )
                if callback_progresso:
                    callback_progresso(n_pag, total_pag, "(erro Gemini)")
                continue

            try:
                lista = _parse_json_lista(texto_resp)
            except (json.JSONDecodeError, ValueError) as exc:
                stats.erros_json += 1
                resultado.avisos.append(
                    f"Gemini retornou JSON inválido na página {n_pag}: {exc}"
                )
                if callback_progresso:
                    callback_progresso(n_pag, total_pag, "(JSON inválido)")
                continue

            n_itens_pag = 0
            for d in lista:
                item = _dict_para_item(d, pagina=n_pag)
                if item is None:
                    continue
                # Aplica score do validador para classificar 'extraido'
                # vs 'duvidoso' — uniforme com outras camadas.
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
                n_itens_pag += 1

            if n_itens_pag > 0:
                stats.paginas_com_itens += 1

            if callback_progresso:
                callback_progresso(n_pag, total_pag, f"{n_itens_pag} item(ns)")

    # Confiança global = fração de itens com score 'extraido'
    n_extraidos = sum(1 for i in resultado.itens if i.confianca == "extraido")
    total = len(resultado.itens)
    resultado.confianca_global = (n_extraidos / total) if total else 0.0

    # Aviso no topo com estatísticas — útil para debug e transparência
    resumo = (
        f"Gemini: {stats.paginas_com_itens}/{stats.paginas_processadas} págs "
        f"com itens, {stats.chamadas_api} chamadas API"
    )
    if stats.erros_api or stats.erros_json:
        resumo += f", {stats.erros_api} erro(s) API, {stats.erros_json} JSON inválido"
    resultado.avisos.insert(0, resumo)

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
