"""
Avaliação reproduzível da busca por código (Camada 0: edital -> catálogo).

Ideia (ground-truth automático): pega produtos REAIS do catálogo, pede ao
Gemini para reescrever cada um "como um edital de licitação descreveria"
(vocabulário/ordem/abreviações diferentes, MESMO produto) e mede se o
matcher reencontra o produto original entre os 1408. O gerador (Gemini) é
independente da lógica do matcher, então não é circular; as queries geradas
são gravadas para o humano julgar se são realistas.

Mede em DOIS modos para isolar o valor de cada camada:
  - cru:      fuzzy local (rapidfuzz token_set) sem canonização
  - canônico: com canonização via Gemini (Fase B) dos DOIS lados

Uso:
    python -m scripts.avaliar_busca --n 60 [--seed 42]

NÃO usa rede de fornecedor (offline). Usa o Gemini (chave em credenciais)
para gerar as queries e canonizar. Sem chave/quota, cai para perturbação
programática transparente (degradação explícita, marcada no relatório).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pregoes import credenciais, db
from pregoes.matcher import carregar_catalogo, melhor_match
from pregoes.normalizador import normalizar_descricao


def _amostra_estratificada(catalogo, n, seed):
    """Diversifica por 1ª palavra (categoria) para não viciar numa só."""
    rnd = random.Random(seed)
    por_cat = defaultdict(list)
    for p in catalogo:
        if not p.descricao_canonica:
            continue
        cat = p.descricao_canonica.split()[0] if p.descricao_canonica.split() else "?"
        por_cat[cat].append(p)
    cats = list(por_cat)
    rnd.shuffle(cats)
    escolhidos, i = [], 0
    # round-robin entre categorias até atingir n
    while len(escolhidos) < n and any(por_cat.values()):
        cat = cats[i % len(cats)]
        if por_cat[cat]:
            escolhidos.append(por_cat[cat].pop(rnd.randrange(len(por_cat[cat]))))
        i += 1
        if i > n * 50:
            break
    return escolhidos[:n]


_PROMPT_GERA = (
    "Reescreva cada item como uma LINHA DE EDITAL de licitação pública "
    "brasileira (material de construção/hidráulico/elétrico). REGRAS: "
    "MANTENHA o nome do produto e as medidas/especificações principais "
    "(um pregoeiro precisa reconhecer o item); pode variar a ORDEM das "
    "palavras, expandir siglas, usar sinônimo do ramo ou acrescentar 1 "
    "detalhe técnico típico. NÃO troque o produto por um conceito abstrato "
    "nem copie a descrição literal. Uma frase curta por item. Responda "
    "APENAS um JSON: lista de strings, na mesma ordem.\n\nItens:\n"
)

_PROMPT_JULGA = (
    "Para cada par [A, B] abaixo, A é a descrição de um produto e B é o "
    "produto que um sistema de busca retornou. Responda se são O MESMO "
    "produto comprável (mesma categoria, medida e especificação compatíveis "
    "— variação de marca/grafia conta como MESMO). Responda APENAS um JSON: "
    "lista de true/false, na mesma ordem.\n\nPares:\n"
)


def _julga_equivalencia(pares, api_key):
    """Gemini julga se cada (esperado, obtido) é o mesmo produto. Lista bool."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key,
                          http_options=types.HttpOptions(timeout=60_000))
    corpo = "\n".join(f'{i+1}. ["{a}", "{b}"]' for i, (a, b) in enumerate(pares))
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite", contents=_PROMPT_JULGA + corpo)
        txt = (resp.text or "").strip()
        if txt.startswith("```"):
            txt = txt.split("```")[1].lstrip("json").strip()
        arr = [bool(x) for x in json.loads(txt)][:len(pares)]
        arr += [False] * (len(pares) - len(arr))
        return arr
    except Exception as exc:
        print(f"  [julga] falhou ({type(exc).__name__})", file=sys.stderr)
        return [False] * len(pares)


def _gera_queries_gemini(descricoes, api_key):
    """Gera reescritas edital-style via Gemini, em lotes. Retorna lista
    paralela a `descricoes` (ou None no índice que falhar)."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key,
                          http_options=types.HttpOptions(timeout=60_000))
    out: list[str | None] = []
    LOTE = 20
    for ini in range(0, len(descricoes), LOTE):
        bloco = descricoes[ini:ini + LOTE]
        corpo = "\n".join(f"{i+1}. {d}" for i, d in enumerate(bloco))
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=_PROMPT_GERA + corpo,
            )
            txt = (resp.text or "").strip()
            if txt.startswith("```"):
                txt = txt.split("```")[1].lstrip("json").strip()
            arr = json.loads(txt)
            arr = [str(x) for x in arr][:len(bloco)]
            arr += [None] * (len(bloco) - len(arr))
            out.extend(arr)
        except Exception as exc:  # degrada para None neste lote
            print(f"  [gera] lote {ini}: falhou ({type(exc).__name__})", file=sys.stderr)
            out.extend([None] * len(bloco))
    return out


def _perturba_programatica(desc, rnd):
    """Fallback sem Gemini: reordena tokens e tira 1 descritor (transparente)."""
    toks = desc.split()
    if len(toks) > 3:
        toks = toks[:-1]  # tira último descritor
    rnd.shuffle(toks)
    return " ".join(toks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    conn = db.conectar()
    catalogo = carregar_catalogo(conn)
    print(f"Catálogo: {len(catalogo)} produtos")
    com_codigo = sum(1 for p in catalogo if (p.codigo_almeida or p.codigo_dantas))
    com_canon = sum(1 for p in catalogo if p.canonico)
    print(f"  com código (almeida/dantas): {com_codigo} "
          f"({100*com_codigo//max(len(catalogo),1)}%)")
    print(f"  com forma canônica (Gemini): {com_canon} "
          f"({100*com_canon//max(len(catalogo),1)}%)")

    alvos = _amostra_estratificada(catalogo, args.n, args.seed)
    print(f"\nAmostra estratificada: {len(alvos)} produtos\n")

    api_key = credenciais.carregar_chave_api("gemini")
    descricoes = [p.descricao_canonica for p in alvos]

    if api_key:
        print("Gerando queries edital-style via Gemini...")
        queries = _gera_queries_gemini(descricoes, api_key)
    else:
        print("SEM chave Gemini — usando perturbação programática (degradado).")
        queries = [None] * len(alvos)

    rnd = random.Random(args.seed)
    queries = [q if q else _perturba_programatica(d, rnd)
               for q, d in zip(queries, descricoes)]

    # Canonização das queries (Fase B) — mesma do pipeline real.
    canon_map = {}
    if api_key:
        try:
            from pregoes.alinhador import normalizar_canonico
            print("Canonizando queries via Gemini (Fase B)...")
            canon_map = normalizar_canonico(queries, conn=conn, api_key=api_key)
        except Exception as exc:
            print(f"  canonização falhou ({type(exc).__name__}); modo canônico = cru",
                  file=sys.stderr)

    # Avalia nos dois modos.
    res = {"cru": {"acertos": 0, "errados": 0, "sem_match": 0, "falhas": []},
           "canonico": {"acertos": 0, "errados": 0, "sem_match": 0, "falhas": []}}
    exemplos_query = []

    canon_errados = []  # (esperado, match_obtido, query) p/ juiz de equivalência
    for p, q in zip(alvos, queries):
        q_norm = normalizar_descricao(q)
        if len(exemplos_query) < 8:
            exemplos_query.append((p.descricao_canonica, q))
        # modo cru
        m = melhor_match(q_norm, catalogo, canonico_edital=None)
        _conta(res["cru"], p, m, q)
        # modo canônico
        qc = canon_map.get(q)
        m2 = melhor_match(q_norm, catalogo, canonico_edital=qc)
        _conta(res["canonico"], p, m2, q)
        if m2 is not None and m2.produto.produto_id != p.produto_id:
            canon_errados.append((p, m2, q))

    # Juiz de equivalência (Gemini): trata duplicatas do catálogo e produtos
    # equivalentes ("assento sanitario" vs "assento p/ vaso") como acerto
    # PRÁTICO — o id exato é pessimista quando o catálogo tem quase-duplicatas.
    equivalentes, equiv_exemplos = 0, []
    if api_key and canon_errados:
        print(f"\nJulgando equivalência de {len(canon_errados)} 'errados' (Gemini)...")
        pares = [(p.descricao_canonica, m2.produto.descricao_canonica)
                 for p, m2, _ in canon_errados]
        veredito = _julga_equivalencia(pares, api_key)
        for (p, m2, _), v in zip(canon_errados, veredito):
            if v:
                equivalentes += 1
                if len(equiv_exemplos) < 6:
                    equiv_exemplos.append((p.descricao_canonica,
                                           m2.produto.descricao_canonica))

    _relatorio(len(alvos), res, exemplos_query, equivalentes, equiv_exemplos)
    conn.close()


def _conta(acc, esperado, match, query):
    if match is None:
        acc["sem_match"] += 1
        if len(acc["falhas"]) < 12:
            acc["falhas"].append({"tipo": "sem_match", "query": query,
                                  "esperado": esperado.descricao_canonica, "obtido": None})
    elif match.produto.produto_id == esperado.produto_id:
        acc["acertos"] += 1
    else:
        acc["errados"] += 1
        if len(acc["falhas"]) < 12:
            acc["falhas"].append({"tipo": "errado", "query": query,
                                  "esperado": esperado.descricao_canonica,
                                  "obtido": match.produto.descricao_canonica,
                                  "score": round(match.score, 1)})


def _relatorio(n, res, exemplos, equivalentes=0, equiv_exemplos=()):
    print("\n" + "=" * 78)
    print("EXEMPLOS de query gerada (original  ->  como o 'edital' descreveu):")
    for orig, q in exemplos:
        print(f"  • {orig[:46]:46}  ->  {q}")
    for modo in ("cru", "canonico"):
        a = res[modo]
        p1 = 100 * a["acertos"] / max(n, 1)
        print("\n" + "-" * 78)
        print(f"MODO {modo.upper()}: precisão@1 ESTRITA (id exato) = {p1:.0f}%  "
              f"({a['acertos']} certos / {a['errados']} 'errado' / "
              f"{a['sem_match']} não achou, de {n})")
        for f in a["falhas"][:6]:
            if f["tipo"] == "errado":
                print(f"  ✗ '{f['query'][:42]}'\n      esperava: {f['esperado'][:50]}"
                      f"\n      obteve:   {f['obtido'][:50]} (score {f['score']})")
            else:
                print(f"  ∅ '{f['query'][:42]}'  (não achou; esperava {f['esperado'][:40]})")
    # Número PRÁTICO do modo canônico: acerto exato + equivalente (duplicata).
    a = res["canonico"]
    pratico = 100 * (a["acertos"] + equivalentes) / max(n, 1)
    print("\n" + "-" * 78)
    print(f"MODO CANÔNICO — precisão@1 PRÁTICA (id exato + equivalente por juiz) "
          f"= {pratico:.0f}%")
    print(f"  = {a['acertos']} exatos + {equivalentes} equivalentes "
          f"(duplicata/sinônimo) / {n}; restam {a['errados']-equivalentes} "
          f"erros reais e {a['sem_match']} não-achou.")
    if equiv_exemplos:
        print("  exemplos contados como equivalentes:")
        for esp, obt in equiv_exemplos:
            print(f"    ≈ {esp[:40]}  ==  {obt[:40]}")
    print("=" * 78)


if __name__ == "__main__":
    main()
