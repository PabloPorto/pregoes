"""
Alinhamento de nomes de produtos via LLM (Fase B / Q12, 2026-06).

O problema: a descrição do edital e a do catálogo do cliente descrevem o
MESMO produto com vocabulário diferente — "PARAFUSO SXT 516" no catálogo,
"PARAFUSO SEXTAVADO 5/16" no edital; "cano" vs "tubo". O matcher fuzzy
(token_sort_ratio) é cego a isso e casa ~0% dos editais reais (medido).

A solução: reduzir AMBOS os lados a uma FORMA CANÔNICA padronizada
(categoria + dimensões, vocabulário uniforme) usando o Gemini, e então
casar as formas canônicas (que ficam comparáveis).

Princípios econômicos (lição do "judge" que estourou a quota):
- **BATCH**: uma chamada para N descrições, nunca uma por item.
- **CACHE** por hash (TTL infinito — a forma canônica de um nome não muda).
- **DEGRADAÇÃO GRACIOSA**: sem chave/rede/quota, devolve a normalização
  LOCAL (`normalizar_descricao`). O sistema continua com fuzzy + heurística.

Custo previsto: catálogo (~1400 produtos) = ~10 chamadas, uma vez na vida;
por edital = 1-3 chamadas. Free tier do Gemini cobre com folga.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Optional

from . import config, credenciais, db
from .normalizador import normalizar_descricao


MODELO_DEFAULT = "gemini-2.5-flash-lite"

# Descrições por chamada. Conservador para a resposta não truncar — o ganho
# real é não chamar POR ITEM, então 150 já mantém o custo em poucas chamadas.
_LOTE_MAX = 150


PROMPT = """Você padroniza nomes de produtos de construção, hidráulica e elétrica.

Para cada item recebido, devolva uma FORMA CANÔNICA curta e padronizada:
- categoria genérica do produto + dimensões/medidas principais
- tudo em minúsculas, SEM texto contratual (garantia, material, marca,
  fabricante, embalagem, norma, "conforme", etc)
- vocabulário PADRONIZADO: "sextavado" (não "sxt"), "5/16" (não "516"),
  "tubo" para cano/tubo, "soldavel", "rosca", "abracadeira"
- PRESERVE as medidas que identificam o produto: polegadas (5/16),
  milímetros (50mm), nº de tomadas (5 tomadas), volts (12v), watts (30w),
  bitola de vergalhão (ca-50)

Exemplos:
- "PARAFUSO SXT 516 X 4 AÇO ZINCADO" -> "parafuso sextavado 5/16 x 4"
- "Cano PVC sold. 100" -> "tubo pvc soldavel 100mm"
- "ABRAÇADEIRAS DE NYILON 4,8X320" -> "abracadeira nylon 4.8 x 320"

ITENS (JSON):
{itens}

Responda SOMENTE um array JSON, um objeto por item, na ordem recebida:
[{{"id": 0, "canonico": "..."}}, ...]"""


def _hash(desc_norm: str) -> str:
    return hashlib.sha256(desc_norm.encode("utf-8")).hexdigest()


def normalizar_canonico(
    descricoes: list[str],
    *,
    conn=None,
    api_key: Optional[str] = None,
    modelo: str = MODELO_DEFAULT,
) -> dict[str, str]:
    """Mapeia cada descrição para sua forma canônica padronizada.

    Retorna `{descricao_original: forma_canonica}`. Descrições que o LLM não
    cobrir (sem chave, sem rede, erro, item ausente na resposta) recebem a
    normalização LOCAL como fallback — nunca levanta exceção.

    Cache: formas vindas do LLM são gravadas em `cache_canonico` (por hash da
    descrição normalizada). O fallback local NÃO é cacheado (para que uma
    chamada futura com quota ainda tente o LLM).
    """
    if not descricoes:
        return {}

    deve_fechar = False
    if conn is None:
        db.init_db()
        conn = db.conectar()
        deve_fechar = True

    try:
        # 1. Normaliza local + indexa por hash (dedup natural).
        norm_por_desc = {d: normalizar_descricao(d) for d in set(descricoes)}
        hash_por_norm = {n: _hash(n) for n in set(norm_por_desc.values()) if n}

        # 2. Consulta cache.
        cache = db.canonico_buscar_muitos(conn, list(hash_por_norm.values()))

        # 3. Misses → chama o LLM em lotes.
        normas_miss = [n for n, h in hash_por_norm.items() if h not in cache]
        if normas_miss:
            chave = api_key or credenciais.carregar_chave_api("gemini")
            if chave:
                novos = _chamar_llm_em_lotes(normas_miss, chave, modelo)
                if novos:
                    db.canonico_gravar_muitos(
                        conn,
                        [(hash_por_norm[n], n, c) for n, c in novos.items()],
                    )
                    conn.commit()
                    for n, c in novos.items():
                        cache[hash_por_norm[n]] = c

        # 4. Monta o retorno: cache (LLM) ou fallback local.
        out: dict[str, str] = {}
        for desc in descricoes:
            n = norm_por_desc[desc]
            if not n:
                out[desc] = ""
                continue
            out[desc] = cache.get(hash_por_norm.get(n, ""), n)  # n = fallback local
        return out
    finally:
        if deve_fechar:
            conn.close()


def normalizar_catalogo(
    conn=None,
    *,
    apenas_faltantes: bool = True,
    callback=None,
) -> dict:
    """Normaliza os produtos do catálogo (one-shot) e grava em
    `produtos_canonico`. Retorna stats `{total, normalizados, falhos, ja_tinham}`.

    Idempotente: por padrão pula produtos que já têm forma canônica
    (`apenas_faltantes=True`). O cache por descrição (`cache_canonico`)
    também evita re-chamar o LLM para descrições repetidas entre planilhas.

    **Só grava o que o LLM REALMENTE alinhou** (presente em `cache_canonico`).
    Se a quota/rede falhar, a descrição cai no fallback local — esse NÃO é
    gravado como canônico, para não "prender" um vocabulário desalinhado.
    Os que falharam contam em `falhos` e podem ser re-tentados depois.

    `callback(feitos, total)` é opcional — para a UI mostrar progresso.
    """
    deve_fechar = False
    if conn is None:
        db.init_db()
        conn = db.conectar()
        deve_fechar = True

    try:
        if apenas_faltantes:
            rows = conn.execute(
                """
                SELECT p.id, p.descricao_canonica
                FROM produtos p
                LEFT JOIN produtos_canonico pc ON pc.produto_id = p.id
                WHERE pc.produto_id IS NULL
                  AND p.descricao_canonica IS NOT NULL
                  AND TRIM(p.descricao_canonica) != ''
                """
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, descricao_canonica FROM produtos "
                "WHERE descricao_canonica IS NOT NULL AND TRIM(descricao_canonica) != ''"
            ).fetchall()

        total = conn.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
        if not rows:
            return {"total": total, "normalizados": 0, "falhos": 0, "ja_tinham": total}

        descricoes = [r["descricao_canonica"] for r in rows]
        normalizar_canonico(descricoes, conn=conn)   # popula cache_canonico

        # Só grava produtos cujo canônico VEIO DO LLM (está no cache). O
        # fallback local não é cacheado, então não vira canônico preso.
        hash_por_id = {
            r["id"]: _hash(normalizar_descricao(r["descricao_canonica"]))
            for r in rows
        }
        cache = db.canonico_buscar_muitos(conn, list(hash_por_id.values()))

        feitos = falhos = 0
        for r in rows:
            canon = cache.get(hash_por_id[r["id"]])
            if canon:
                db.produto_canonico_gravar(conn, r["id"], canon)
                feitos += 1
                if callback and feitos % 50 == 0:
                    callback(feitos, len(rows))
            else:
                falhos += 1
        conn.commit()
        if falhos:
            from .logconf import get_logger
            get_logger("alinhador").warning(
                "Padronização parcial: %d ok, %d não alinhados (quota/rede?).",
                feitos, falhos)
        return {"total": total, "normalizados": feitos, "falhos": falhos,
                "ja_tinham": total - len(rows)}
    finally:
        if deve_fechar:
            conn.close()


def _chamar_llm_em_lotes(
    normas: list[str], api_key: str, modelo: str
) -> dict[str, str]:
    """Chama o Gemini em lotes de `_LOTE_MAX`. Retorna {norma: canonico}.
    Falha de um lote não derruba os outros (cada lote é independente)."""
    try:
        from google import genai
    except ImportError:
        return {}
    client = genai.Client(api_key=api_key)
    resultado: dict[str, str] = {}
    for inicio in range(0, len(normas), _LOTE_MAX):
        lote = normas[inicio:inicio + _LOTE_MAX]
        itens = [{"id": i, "texto": n} for i, n in enumerate(lote)]
        prompt = PROMPT.format(itens=json.dumps(itens, ensure_ascii=False))
        try:
            resp = client.models.generate_content(model=modelo, contents=prompt)
            arr = _parse_array(resp.text or "")
        except Exception:
            # Lote falhou (quota/rede) — os outros seguem; estes viram
            # fallback local. Loga para diagnóstico (não silencioso).
            from .logconf import get_logger
            get_logger("alinhador").warning(
                "Lote de canonização falhou (%d itens) — virou fallback local.",
                len(lote))
            continue
        for obj in arr:
            try:
                i = int(obj["id"])
                canon = str(obj["canonico"]).strip().lower()
                if 0 <= i < len(lote) and canon:
                    resultado[lote[i]] = canon
            except (KeyError, TypeError, ValueError):
                continue
    return resultado


def _parse_array(texto: str) -> list[dict]:
    """Extrai o array JSON da resposta, tolerando cercas markdown."""
    s = texto.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s)
    ini, fim = s.find("["), s.rfind("]")
    if ini < 0 or fim <= ini:
        return []
    try:
        dados = json.loads(s[ini:fim + 1])
        return dados if isinstance(dados, list) else []
    except (ValueError, TypeError):
        return []
