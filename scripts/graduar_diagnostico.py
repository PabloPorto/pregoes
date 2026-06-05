"""
Gradua um diagnóstico real da busca (xlsx exportado pela UI) com juiz Gemini.

Lente correta para este produto: o operador ESCOLHE entre os candidatos, então
a métrica primária é RECALL — o produto certo está ENTRE os candidatos que a
busca trouxe? — e secundária, se o DEFAULT sugerido (maior relevância) acertou.

Uso: python -m scripts.graduar_diagnostico <caminho.xlsx>
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
from pregoes import credenciais

_PROMPT = (
    "Você é um especialista em compras de material de construção/hidráulico/"
    "elétrico avaliando um sistema de busca de preços para licitações. Para "
    "CADA item abaixo há a descrição do EDITAL, a lista de CANDIDATOS que a "
    "busca trouxe (com marca) e o DEFAULT que ela sugeriu. Avalie com critério "
    "de comprador profissional (medida/especificação têm de bater; marca pode "
    "variar). Responda APENAS um JSON: lista de objetos, um por item, na ordem, "
    "com os campos: {\"item\": int, \"recall\": bool (algum candidato é um "
    "produto comprável correto p/ o edital?), \"default_ok\": bool (o default "
    "sugerido é um dos corretos?), \"nota\": string curta}.\n\nItens:\n"
)


def _ler(caminho):
    wb = openpyxl.load_workbook(caminho, read_only=True)
    res = list(wb["Resumo"].iter_rows(values_only=True))
    det = list(wb["Detalhe"].iter_rows(values_only=True))
    rh, dh = res[0], det[0]
    ri = {h: i for i, h in enumerate(rh)}
    di = {h: i for i, h in enumerate(dh)}
    itens = {}
    for r in res[1:]:
        n = r[ri["Item"]]
        itens[n] = {
            "edital": r[ri["Descrição (PDF)"]],
            "status": r[ri["Status"]],
            "marca_default": r[ri.get("Marca default", -1)] if "Marca default" in ri else None,
            "preco_default": r[ri.get("Preço default", -1)] if "Preço default" in ri else None,
            "candidatos": [],
        }
    for r in det[1:]:
        n = r[di["Item"]]
        if n in itens:
            itens[n]["candidatos"].append({
                "desc": r[di["Descrição encontrada"]],
                "marca": r[di["Marca"]],
                "preco": r[di["Preço"]],
            })
    return itens


def _monta_corpo(itens):
    linhas = []
    for n in sorted(itens):
        it = itens[n]
        cands = it["candidatos"]
        if not cands:
            linhas.append(f'#{n} EDITAL: "{it["edital"]}"\n   CANDIDATOS: (nenhum)\n   DEFAULT: (nenhum)')
            continue
        lc = "; ".join(f'[{c["marca"]}] {c["desc"]}' for c in cands[:8])
        linhas.append(
            f'#{n} EDITAL: "{it["edital"]}"\n   CANDIDATOS: {lc}\n'
            f'   DEFAULT: [{it["marca_default"]}] R$ {it["preco_default"]}')
    return "\n".join(linhas)


def _julga(corpo, api_key):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=120_000))
    resp = client.models.generate_content(model="gemini-2.5-flash-lite", contents=_PROMPT + corpo)
    txt = (resp.text or "").strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1].lstrip("json").strip()
    return json.loads(txt)


def main():
    caminho = sys.argv[1] if len(sys.argv) > 1 else None
    if not caminho:
        print("uso: python -m scripts.graduar_diagnostico <xlsx>"); return
    itens = _ler(caminho)
    api_key = credenciais.carregar_chave_api("gemini")
    if not api_key:
        print("sem chave Gemini"); return

    com_cand = {n: it for n, it in itens.items() if it["candidatos"]}
    sem_cand = {n: it for n, it in itens.items() if not it["candidatos"]}
    print(f"Total: {len(itens)} itens | com candidatos: {len(com_cand)} | "
          f"sem candidatos (vazio): {len(sem_cand)}\n")

    veredictos = _julga(_monta_corpo(itens), api_key)
    vmap = {v["item"]: v for v in veredictos}

    recall_ok = default_ok = avaliados = 0
    print(f"{'#':>2} {'recall':>6} {'default':>7}  edital / nota")
    print("-" * 90)
    for n in sorted(itens):
        v = vmap.get(n)
        it = itens[n]
        if not it["candidatos"]:
            print(f"{n:>2} {'—':>6} {'—':>7}  {it['edital'][:48]}  [VAZIO: busca não trouxe nada]")
            continue
        avaliados += 1
        r = bool(v and v.get("recall"))
        d = bool(v and v.get("default_ok"))
        recall_ok += r
        default_ok += d
        nota = (v or {}).get("nota", "")[:42]
        print(f"{n:>2} {('SIM' if r else 'NÃO'):>6} {('ok' if d else 'x'):>7}  "
              f"{it['edital'][:40]} — {nota}")

    print("-" * 90)
    print(f"RECALL (produto certo entre os candidatos): {recall_ok}/{avaliados} "
          f"= {100*recall_ok//max(avaliados,1)}% dos itens com candidatos")
    print(f"DEFAULT correto (sugestão automática certa): {default_ok}/{avaliados} "
          f"= {100*default_ok//max(avaliados,1)}%")
    print(f"VAZIO (busca não trouxe nada): {len(sem_cand)}/{len(itens)} itens — "
          f"precisa de busca textual/manual")


if __name__ == "__main__":
    main()
