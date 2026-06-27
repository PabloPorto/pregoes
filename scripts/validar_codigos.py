"""Valida os códigos das planilhas de Formação de Preços contra o site dos
fornecedores e gera (1) um RELATÓRIO de status por código e (2) uma PLANILHA
UNIFICADA LIMPA só com os códigos corretos — para realimentar o catálogo do
sistema com dados concretos e coerentes.

Para cada item (código, descrição) das planilhas:
  - busca o código no site (fornecedor do sufixo A/D; sem sufixo → Almeida, com
    fallback para Dantas), reusando a sessão logada (rápido);
  - decide o status:
      OK         — o código EXISTE e a descrição do site BATE com a da planilha.
      DIFERENTE  — o código existe, mas é OUTRO produto (descrição não bate).
      NAO_EXISTE — o código não foi encontrado em nenhum fornecedor.

Robusto: rate-limit respeitado, checkpoint para RETOMAR se interromper, retry em
falha transitória, e nunca derruba — um item problemático vira status, não crash.

Uso:
    python scripts/validar_codigos.py "<dir das planilhas>" [--limite N] [--saida DIR]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import Workbook, load_workbook
from rapidfuzz import fuzz

from pregoes import scraper
from pregoes.importador import detectar_colunas, _parse_codigo
from pregoes.normalizador import normalizar_descricao

THRESHOLD_MATCH = 60   # token_sort_ratio mínimo p/ "a descrição bate" (fallback)

# Gemini (Camada 3 do sistema) decide "é o MESMO produto?" — o fuzzy erra em
# sinônimos do domínio ("adesivo" = "cola"; "adaptador flange" = "adaptador AA
# com flange"). Lazy + gracioso: sem chave/SDK, cai no fuzzy.
_GEMINI = {"client": None, "tentou": False}


def _gemini_client():
    if _GEMINI["tentou"]:
        return _GEMINI["client"]
    _GEMINI["tentou"] = True
    try:
        from pregoes import credenciais
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


def _mesmo_produto(desc_plan: str, desc_site: str, marca_site) -> "bool | None":
    """True se o produto do site é o MESMO da descrição do edital (Gemini).
    Devolve None se o Gemini não estiver disponível/falhar (chamador usa fuzzy)."""
    client = _gemini_client()
    if client is None:
        return None
    marca = f" (marca {marca_site})" if marca_site else ""
    prompt = (
        "Você compara itens de licitação de material de construção, elétrico e "
        "hidráulico. A 'descrição do edital' e o 'produto do site' são o MESMO "
        "produto — mesma categoria, função e DIMENSÃO/medida essencial? Diferença "
        "de redação, marca ou ordem das palavras não importa; o que importa é ser "
        "o mesmo item. Se a medida/bitola do edital claramente não bate com a do "
        "site, responda NAO. Responda SÓ uma palavra: SIM ou NAO.\n\n"
        f"EDITAL: {desc_plan}\nSITE: {desc_site}{marca}"
    )
    try:
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite", contents=prompt)
        return (resp.text or "").strip().upper().startswith("S")
    except Exception:
        return None


def _extrair_itens(diretorio: str) -> list[dict]:
    """Lê todas as planilhas; devolve itens ÚNICOS por (código, sufixo)."""
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
            if chave not in itens:   # dedup; mantém a 1ª descrição vista
                itens[chave] = {
                    "codigo": cb, "forn_sufixo": fs,
                    "descricao": str(desc).strip(),
                    "marca": str(marca).strip() if marca else None,
                    "arquivo": os.path.basename(f),
                }
    return list(itens.values())


def _achar_no_site(codigo: str, fornecedor: str):
    """Devolve o card cujo código BATE EXATAMENTE, ou None (não existe nesse
    fornecedor). Retry simples em falha transitória."""
    res = None
    for tentativa in range(2):
        try:
            res = scraper.buscar_no_site(fornecedor, codigo, limite=4)
            break
        except Exception:
            if tentativa == 0:
                time.sleep(1.0)
                continue
            return None
    alvo = str(codigo).strip()
    for r in (res or []):
        if str(r.codigo_fornecedor).strip() == alvo:
            return r
    return None   # site não trouxe o código exato → não existe aqui


def _validar_item(it: dict) -> dict:
    base = {k: it[k] for k in ("codigo", "forn_sufixo", "descricao", "marca", "arquivo")}
    # Acha o card cujo CÓDIGO bate exatamente. Sufixo A/D manda no fornecedor;
    # sem sufixo, tenta Almeida e depois Dantas (e para no que achar o código).
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
        normalizar_descricao(r.descricao_encontrada or ""))
    # Decisão FINAL pelo Gemini (sinônimos do domínio); sem Gemini, fuzzy.
    mesmo = _mesmo_produto(it["descricao"], r.descricao_encontrada or "", r.marca)
    ok = (mesmo if mesmo is not None else score >= THRESHOLD_MATCH)
    base.update(
        status="OK" if ok else "DIFERENTE", fornecedor=forn, score=score,
        desc_site=r.descricao_encontrada, marca_site=r.marca,
        preco_site=r.preco, codigo_site=r.codigo_fornecedor,
    )
    return base


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("diretorio")
    ap.add_argument("--limite", type=int, default=0)
    ap.add_argument("--saida", default="data/validacao")
    args = ap.parse_args()
    os.makedirs(args.saida, exist_ok=True)

    itens = _extrair_itens(args.diretorio)
    if args.limite:
        itens = itens[:args.limite]
    total = len(itens)
    print(f"{total} itens únicos a validar", flush=True)

    ckpt = os.path.join(args.saida, "checkpoint.json")
    feitos: dict = {}
    if os.path.exists(ckpt):
        for r in json.load(open(ckpt, encoding="utf-8")):
            feitos[(r["codigo"], r["forn_sufixo"] or "")] = r
        print(f"retomando: {len(feitos)} já validados", flush=True)

    resultados: list = []
    t0 = time.time()
    for i, it in enumerate(itens, 1):
        chave = (it["codigo"], it["forn_sufixo"] or "")
        resultados.append(feitos.get(chave) or _validar_item(it))
        if i % 20 == 0 or i == total:
            json.dump(resultados, open(ckpt, "w", encoding="utf-8"), ensure_ascii=False)
            dt = time.time() - t0
            feito_agora = i - len(feitos)
            taxa = dt / max(feito_agora, 1)
            print(f"  {i}/{total}  ({taxa:.1f}s/item, ~{(total-i)*taxa/60:.0f}min restantes)",
                  flush=True)

    try:
        scraper.fechar_sessoes_vivas()
    except Exception:
        pass

    c = Counter(r["status"] for r in resultados)
    print(f"\n=== {total} itens: OK={c['OK']} · DIFERENTE={c['DIFERENTE']} · "
          f"NAO_EXISTE={c['NAO_EXISTE']} ===", flush=True)

    ts = time.strftime("%Y%m%d_%H%M%S")

    # (1) Relatório COMPLETO — planilha × site lado a lado.
    wb = Workbook(); ws = wb.active; ws.title = "validacao"
    ws.append(["codigo", "forn_sufixo", "status", "fornecedor_achado", "score",
               "descricao_planilha", "marca_planilha", "descricao_site",
               "marca_site", "preco_site", "codigo_site", "arquivo"])
    for r in resultados:
        ws.append([r["codigo"], r["forn_sufixo"], r["status"], r.get("fornecedor"),
                   r.get("score"), r["descricao"], r.get("marca"), r.get("desc_site"),
                   r.get("marca_site"), r.get("preco_site"), r.get("codigo_site"),
                   r["arquivo"]])
    rel = os.path.join(args.saida, f"validacao_codigos_{ts}.xlsx")
    wb.save(rel)

    # (2) Planilha LIMPA — só OK, com o código sufixado (A/D) p/ o importador
    # mapear o fornecedor numa passada só.
    wb2 = Workbook(); ws2 = wb2.active; ws2.title = "catalogo_limpo"
    ws2.append(["DESCRICAO", "CODIGO", "MARCA", "FORNECEDOR", "PRECO_REFERENCIA"])
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
    limpo = os.path.join(args.saida, f"catalogo_limpo_{ts}.xlsx")
    wb2.save(limpo)

    print(f"relatório completo : {rel}")
    print(f"planilha limpa ({n_ok} OK): {limpo}", flush=True)


if __name__ == "__main__":
    main()
