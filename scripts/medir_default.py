"""
Mede o ranking do DEFAULT antes/depois do bônus de código, em diagnósticos
reais — safeguard anti-regressão. Recalcula o default com a chave ANTIGA
(relevância, preço) e a NOVA (código forte, relevância, preço), e usa o juiz
Gemini SÓ nos itens onde o default MUDOU para classificar:
  improvado = antigo errado -> novo certo
  REGREDIDO = antigo certo  -> novo errado   (precisa ser ZERO)
  neutro    = mesma corretude

Uso: python -m scripts.medir_default <diag.xlsx> [diag2.xlsx ...]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
from pregoes import credenciais
from pregoes.normalizador import relevancia

_RE_NUM = re.compile(r"\d+")


def _codigos_fortes(t):
    return {n for n in _RE_NUM.findall(t or "") if len(n) >= 3}


def _bonus(ed, cand):
    return len(_codigos_fortes(ed) & _codigos_fortes(cand))


def _ler(caminho):
    wb = openpyxl.load_workbook(caminho, read_only=True)
    res = list(wb["Resumo"].iter_rows(values_only=True))
    det = list(wb["Detalhe"].iter_rows(values_only=True))
    ri = {h: i for i, h in enumerate(res[0])}
    di = {h: i for i, h in enumerate(det[0])}
    itens = {}
    for r in res[1:]:
        n = r[ri["Item"]]
        itens[n] = {"edital": r[ri["Descrição (PDF)"]],
                    "marca_def": r[ri.get("Marca default", -1)],
                    "cand": []}
    for r in det[1:]:
        n = r[di["Item"]]
        if n in itens:
            itens[n]["cand"].append({"desc": r[di["Descrição encontrada"]],
                                     "marca": r[di["Marca"]],
                                     "preco": r[di["Preço"]] or 0.0})
    return {n: it for n, it in itens.items() if it["cand"]}


def _default(ed, cands, *, novo):
    def chave(c):
        rel = relevancia(ed, c["desc"])
        if novo:
            return (-_bonus(ed, c["desc"]), -rel, c["preco"])
        return (-rel, c["preco"])
    return min(cands, key=chave)


def _julga(mudados, api_key):
    """Para cada item mudado, julga old e new. Retorna {item: {old_ok,new_ok}}."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=120_000))
    prompt = (
        "Para cada item, EDITAL é o pedido e há dois candidatos retornados por "
        "um sistema (ANTIGO e NOVO). Diga, para CADA candidato, se é um produto "
        "comprável correto para o edital (medida/especificação compatível; marca "
        "pode variar). Responda APENAS JSON: lista de "
        '{"item":int,"old_ok":bool,"new_ok":bool}.\n\n')
    corpo = "\n".join(
        f'#{n} EDITAL: "{m["edital"]}"\n   ANTIGO: {m["old"]}\n   NOVO: {m["new"]}'
        for n, m in mudados.items())
    resp = client.models.generate_content(
        model="gemini-2.5-flash-lite", contents=prompt + corpo)
    txt = (resp.text or "").strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1].lstrip("json").strip()
    return {v["item"]: v for v in json.loads(txt)}


def medir(caminho, api_key):
    itens = _ler(caminho)
    sane = 0  # quantos OLD recalculados batem o default gravado
    mudados = {}
    for n, it in itens.items():
        old = _default(it["edital"], it["cand"], novo=False)
        new = _default(it["edital"], it["cand"], novo=True)
        if it["marca_def"] and old["marca"] == it["marca_def"]:
            sane += 1
        if (old["desc"], old["preco"]) != (new["desc"], new["preco"]):
            mudados[n] = {"edital": it["edital"],
                          "old": f'[{old["marca"]}] {old["desc"]}',
                          "new": f'[{new["marca"]}] {new["desc"]}'}
    print(f"\n### {Path(caminho).name}")
    print(f"  itens com candidatos: {len(itens)} | sanity OLD==default gravado: "
          f"{sane}/{len(itens)} | default MUDOU em: {len(mudados)}")
    if not mudados:
        print("  → nenhum default mudou: regressão IMPOSSÍVEL neste edital.")
        return 0, 0
    ver = _julga(mudados, api_key)
    improv = regrediu = neutro = 0
    for n, m in mudados.items():
        v = ver.get(n, {})
        o, nw = bool(v.get("old_ok")), bool(v.get("new_ok"))
        tag = ("IMPROVOU" if (not o and nw) else
               "REGREDIU" if (o and not nw) else "neutro")
        improv += (not o and nw); regrediu += (o and not nw); neutro += (o == nw)
        print(f"  #{n} [{tag}] old_ok={o} new_ok={nw}")
        print(f"      edital: {m['edital'][:60]}")
        print(f"      antigo: {m['old'][:60]}")
        print(f"      novo:   {m['new'][:60]}")
    print(f"  RESULTADO: improvou={improv} | REGREDIU={regrediu} | neutro={neutro}")
    return improv, regrediu


def main():
    api_key = credenciais.carregar_chave_api("gemini")
    tot_i = tot_r = 0
    for c in sys.argv[1:]:
        i, r = medir(c, api_key)
        tot_i += i; tot_r += r
    print("\n" + "=" * 60)
    print(f"TOTAL: improvou={tot_i} | REGREDIU={tot_r}")
    print("VEREDITO:", "SEM REGRESSÃO ✓" if tot_r == 0 else "TEM REGRESSÃO ✗ — NÃO ENTREGAR")


if __name__ == "__main__":
    main()
