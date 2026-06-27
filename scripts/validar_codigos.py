"""Valida os códigos das planilhas de Formação de Preços contra o site dos
fornecedores e gera (1) um RELATÓRIO de status por código e (2) uma PLANILHA
UNIFICADA LIMPA só com os códigos corretos — para realimentar o catálogo do
sistema com dados concretos e coerentes.

Para cada item (código, descrição) das planilhas:
  - busca o código no site (fornecedor do sufixo A/D; sem sufixo → Almeida, com
    fallback para Dantas);
  - decide o status:
      OK         — o código EXISTE e a descrição do site BATE com a da planilha.
      DIFERENTE  — o código existe, mas é OUTRO produto (descrição não bate).
      NAO_EXISTE — o código não foi encontrado em nenhum fornecedor.

SEM LOGIN (achado de campo 2026-06-27): a busca `/busca?s={codigo}` mostra
código, NOME e MARCA do produto SEM autenticar — só o PREÇO exige login. Como a
validação só precisa de EXISTÊNCIA + DESCRIÇÃO, não logamos. Isso elimina o
throttle de login, o re-login periódico e a degradação de sessão de auth que
tornavam a abordagem anterior frágil (e davam "não existe" FALSO). O preço fica
de fora (o sistema o busca ao vivo por código quando precisa).

"Mesmo produto?" é decidido pelo Gemini (Camada 3), com fallback fuzzy.

Uso:
    python scripts/validar_codigos.py "<dir>" [--limite N] [--saida DIR]
    python scripts/validar_codigos.py "<dir>" --recheck <relatorio.xlsx>
        (re-valida SÓ os NAO_EXISTE de um relatório e mescla com os já achados)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import Workbook, load_workbook
from rapidfuzz import fuzz

from pregoes import credenciais, scraper
from pregoes.importador import detectar_colunas, _parse_codigo
from pregoes.normalizador import normalizar_descricao

THRESHOLD_MATCH = 60          # fuzzy mínimo p/ "bate" (fallback do Gemini)
# Pausa EXTRA entre buscas (além do rate-limiter) — gentil com o site (~1 req/s).
# Sem login não há throttle de conta, mas mantemos o ritmo educado por IP.
PAUSA_ENTRE_BUSCAS = 0.4


# ----------------------------------------------------------------- Gemini ---
_GEMINI = {"client": None, "tentou": False}


def _gemini_client():
    if _GEMINI["tentou"]:
        return _GEMINI["client"]
    _GEMINI["tentou"] = True
    try:
        api_key = credenciais.carregar_chave_api("gemini")
        if not api_key:
            return None
        from google import genai
        from google.genai import types
        _GEMINI["client"] = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=60_000))
    except Exception:
        _GEMINI["client"] = None
    return _GEMINI["client"]


def _mesmo_produto(desc_plan, desc_site, marca_site):
    client = _gemini_client()
    if client is None:
        return None
    marca = f" (marca {marca_site})" if marca_site else ""
    prompt = (
        "Você compara itens de licitação de material de construção, elétrico e "
        "hidráulico. A 'descrição do edital' e o 'produto do site' são o MESMO "
        "produto — mesma categoria, função e DIMENSÃO/medida essencial? Diferença "
        "de redação, marca ou ordem das palavras não importa; o que importa é ser "
        "o mesmo item. Se a medida/bitola do edital claramente não bate, responda "
        "NAO. Responda SÓ uma palavra: SIM ou NAO.\n\n"
        f"EDITAL: {desc_plan}\nSITE: {desc_site}{marca}"
    )
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite", contents=prompt)
        return (resp.text or "").strip().upper().startswith("S")
    except Exception:
        return None


# ------------------------------------------------- busca por código via HTTP ---
# Os cards da busca `/busca?s={codigo}` são SERVER-RENDERED no HTML (sem XHR),
# e aparecem SEM login (só o data-preco vem 0.00 deslogado). Então um GET cru +
# parse do HTML resolve EXISTÊNCIA + NOME + MARCA: sem navegador, sem login, sem
# throttle, e o MISS é instantâneo (0 cards) — nada de teto de espera por card.
_BASE_URL = {"almeida": scraper._BASE_URL_ALMEIDA,
             "dantas": scraper._BASE_URL_DANTAS}
_RE_TAG_CARD = re.compile(r'<[^>]*\bdata-codigo-produto="[^"]*"[^>]*>')
_RE_DATA_ATTR = re.compile(r'(data-[\w-]+)="([^"]*)"')


def _buscar_codigo_http(codigo: str, fornecedor: str) -> list[dict]:
    """GET na busca e extrai os cards (codigo/nome/marca) do HTML. 1 retry p/
    descartar glitch de rede (vazio real volta vazio nos dois)."""
    url = f"{_BASE_URL[fornecedor]}/busca?s={urllib.parse.quote_plus(str(codigo))}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = ""
    for tentativa in (1, 2):
        try:
            time.sleep(PAUSA_ENTRE_BUSCAS)   # educado com o site
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", "ignore")
            break
        except Exception:
            if tentativa == 2:
                return []
            time.sleep(1.0)
    cards = []
    for tag in _RE_TAG_CARD.findall(html):
        a = dict(_RE_DATA_ATTR.findall(tag))
        cod = (a.get("data-codigo-produto") or "").strip()
        if cod:
            cards.append({"codigo": cod,
                          "nome": (a.get("data-nome") or "").strip(),
                          "marca": (a.get("data-marca") or "").strip()})
    return cards


def _achar_no_site(codigo: str, fornecedor: str):
    """Card cujo código BATE EXATAMENTE com o buscado, ou None."""
    alvo = str(codigo).strip()
    for c in _buscar_codigo_http(codigo, fornecedor):
        if c["codigo"].strip() == alvo:
            return c
    return None


# ----------------------------------------------------------------- extração ---
def _extrair_itens(diretorio: str) -> list[dict]:
    itens: dict = {}
    for f in sorted(glob.glob(os.path.join(diretorio, "*.xlsx"))):
        wb = load_workbook(f, data_only=True)
        ws = wb["PLANILHA MATRIZ"] if "PLANILHA MATRIZ" in wb.sheetnames else wb.active
        hdr, mapa = detectar_colunas(ws)
        if hdr is None:
            continue
        ci, di, mi = mapa.get("codigo"), mapa.get("descricao"), mapa.get("marca")
        for row in ws.iter_rows(min_row=hdr + 2, values_only=True):
            desc = row[di] if di is not None and di < len(row) else None
            if not desc or not str(desc).strip():
                continue
            cod = row[ci] if ci is not None and ci < len(row) else None
            cb, fs = _parse_codigo(cod, None)
            if not cb:
                continue
            marca = row[mi] if mi is not None and mi < len(row) else None
            chave = (cb, fs or "")
            if chave not in itens:
                itens[chave] = {"codigo": cb, "forn_sufixo": fs,
                                "descricao": str(desc).strip(),
                                "marca": str(marca).strip() if marca else None,
                                "arquivo": os.path.basename(f)}
    return list(itens.values())


def _validar_item(it: dict) -> dict:
    base = {k: it[k] for k in ("codigo", "forn_sufixo", "descricao", "marca", "arquivo")}
    candidatos = [it["forn_sufixo"]] if it["forn_sufixo"] else ["almeida", "dantas"]
    encontrado = None
    for forn in candidatos:
        r = _achar_no_site(it["codigo"], forn)
        if r is not None:
            encontrado = (forn, r)
            break
    if encontrado is None:
        base.update(status="NAO_EXISTE", fornecedor=None, score=0,
                    desc_site=None, marca_site=None, preco_site=None, codigo_site=None)
        return base
    forn, r = encontrado
    score = fuzz.token_sort_ratio(
        normalizar_descricao(it["descricao"]),
        normalizar_descricao(r["nome"]))
    mesmo = _mesmo_produto(it["descricao"], r["nome"], r["marca"])
    ok = (mesmo if mesmo is not None else score >= THRESHOLD_MATCH)
    # Sem login não há preço (vem 0.00) — fica de fora; o sistema busca o preço
    # ao vivo por código quando precisa. O que vale aqui é existência+descrição.
    base.update(status="OK" if ok else "DIFERENTE", fornecedor=forn, score=score,
                desc_site=r["nome"], marca_site=r["marca"],
                preco_site=None, codigo_site=r["codigo"])
    return base


# ------------------------------------------------------------------- saídas ---
def _gravar_saidas(resultados: list, saida: str) -> None:
    c = Counter(r["status"] for r in resultados)
    print(f"\n=== {len(resultados)} itens: OK={c['OK']} · DIFERENTE={c['DIFERENTE']} "
          f"· NAO_EXISTE={c['NAO_EXISTE']} ===", flush=True)
    ts = time.strftime("%Y%m%d_%H%M%S")

    wb = Workbook(); ws = wb.active; ws.title = "validacao"
    ws.append(["codigo", "forn_sufixo", "status", "fornecedor_achado", "score",
               "descricao_planilha", "marca_planilha", "descricao_site",
               "marca_site", "preco_site", "codigo_site", "arquivo"])
    for r in resultados:
        ws.append([r["codigo"], r["forn_sufixo"], r["status"], r.get("fornecedor"),
                   r.get("score"), r["descricao"], r.get("marca"), r.get("desc_site"),
                   r.get("marca_site"), r.get("preco_site"), r.get("codigo_site"),
                   r["arquivo"]])
    rel = os.path.join(saida, f"validacao_codigos_{ts}.xlsx")
    wb.save(rel)

    # Header "PRECO" (não "PRECO_REFERENCIA"): o importador mapeia "referencia"
    # para valor_ref (ignorado no histórico); "preco" é o que vira precos_historico.
    wb2 = Workbook(); ws2 = wb2.active; ws2.title = "catalogo_limpo"
    ws2.append(["DESCRICAO", "CODIGO", "MARCA", "FORNECEDOR", "PRECO"])
    n_ok = 0
    for r in resultados:
        if r["status"] != "OK":
            continue
        suf = "A" if r["fornecedor"] == "almeida" else "D"
        ws2.append([r.get("desc_site") or r["descricao"],
                    f"{r.get('codigo_site') or r['codigo']}{suf}",
                    r.get("marca_site") or r.get("marca"),
                    r["fornecedor"], r.get("preco_site")])
        n_ok += 1
    limpo = os.path.join(saida, f"catalogo_limpo_{ts}.xlsx")
    wb2.save(limpo)
    print(f"relatório completo : {rel}")
    print(f"planilha limpa ({n_ok} OK): {limpo}", flush=True)


def _carregar_relatorio(path: str) -> list[dict]:
    ws = load_workbook(path).active
    cols = [c.value for c in next(ws.iter_rows(max_row=1))]
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        d = dict(zip(cols, row))
        out.append({
            "codigo": str(d["codigo"]), "forn_sufixo": d["forn_sufixo"],
            "descricao": d["descricao_planilha"], "marca": d["marca_planilha"],
            "arquivo": d["arquivo"], "status": d["status"],
            "fornecedor": d["fornecedor_achado"], "score": d["score"],
            "desc_site": d["descricao_site"], "marca_site": d["marca_site"],
            "preco_site": d["preco_site"], "codigo_site": d["codigo_site"],
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("diretorio")
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--saida", default="data/validacao")
    ap.add_argument("--recheck", default=None,
                    help="relatório .xlsx: re-valida só os NAO_EXISTE e mescla")
    args = ap.parse_args()
    os.makedirs(args.saida, exist_ok=True)

    if args.recheck:
        previos = _carregar_relatorio(args.recheck)
        por_chave = {(p["codigo"], p["forn_sufixo"] or ""): p for p in previos}
        a_rever = [p for p in previos if p["status"] == "NAO_EXISTE"]
        print(f"recheck: {len(a_rever)} NAO_EXISTE a re-validar (de {len(previos)})",
              flush=True)
        ckpt = os.path.join(args.saida, "recheck.json")
        feitos = {}
        if os.path.exists(ckpt):
            for r in json.load(open(ckpt, encoding="utf-8")):
                feitos[(r["codigo"], r["forn_sufixo"] or "")] = r
            print(f"retomando: {len(feitos)} já re-validados", flush=True)
        t0 = time.time()
        revistos = []
        for i, p in enumerate(a_rever, 1):
            ch = (p["codigo"], p["forn_sufixo"] or "")
            r = feitos.get(ch) or _validar_item(
                {"codigo": p["codigo"], "forn_sufixo": p["forn_sufixo"],
                 "descricao": p["descricao"], "marca": p["marca"], "arquivo": p["arquivo"]})
            revistos.append(r)
            por_chave[ch] = r
            if i % 20 == 0 or i == len(a_rever):
                json.dump(revistos, open(ckpt, "w", encoding="utf-8"), ensure_ascii=False)
                dt = time.time() - t0; novos = i - len(feitos); taxa = dt / max(novos, 1)
                achou = sum(1 for x in revistos if x["status"] != "NAO_EXISTE")
                print(f"  {i}/{len(a_rever)}  ({taxa:.1f}s/item, "
                      f"~{(len(a_rever)-i)*taxa/60:.0f}min) · RECUPERADOS até agora: {achou}",
                      flush=True)
        _gravar_saidas(list(por_chave.values()), args.saida)
        return

    itens = _extrair_itens(args.diretorio)
    if args.limite:
        itens = itens[:args.limite]
    total = len(itens)
    print(f"{total} itens únicos a validar", flush=True)
    ckpt = os.path.join(args.saida, "checkpoint.json")
    feitos = {}
    if os.path.exists(ckpt):
        for r in json.load(open(ckpt, encoding="utf-8")):
            feitos[(r["codigo"], r["forn_sufixo"] or "")] = r
        print(f"retomando: {len(feitos)} já validados", flush=True)
    resultados = []
    t0 = time.time()
    for i, it in enumerate(itens, 1):
        ch = (it["codigo"], it["forn_sufixo"] or "")
        resultados.append(feitos.get(ch) or _validar_item(it))
        if i % 20 == 0 or i == total:
            json.dump(resultados, open(ckpt, "w", encoding="utf-8"), ensure_ascii=False)
            dt = time.time() - t0; novos = i - len(feitos); taxa = dt / max(novos, 1)
            print(f"  {i}/{total}  ({taxa:.1f}s/item, ~{(total-i)*taxa/60:.0f}min)", flush=True)
    _gravar_saidas(resultados, args.saida)


if __name__ == "__main__":
    main()
