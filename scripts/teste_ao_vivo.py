"""
Teste ao vivo da busca: extrai um edital, amostra N itens e roda a busca REAL
(Almeida + Dantas via Playwright + Gemini), exportando um diagnóstico .xlsx
que pode ser graduado por scripts/graduar_diagnostico.py.

Uso: python -m scripts.teste_ao_vivo <edital.pdf> [N]
"""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pregoes import config, credenciais
from pregoes.extrator import extrair_com_pipeline


def _amostra(itens, n, seed=42):
    rnd = random.Random(seed)
    por_cat = defaultdict(list)
    for it in itens:
        d = (getattr(it, "descricao", "") or "")
        cat = d.split()[0].upper() if d.split() else "?"
        por_cat[cat].append(it)
    cats = list(por_cat); rnd.shuffle(cats)
    out, i = [], 0
    while len(out) < n and any(por_cat.values()):
        c = cats[i % len(cats)]
        if por_cat[c]:
            out.append(por_cat[c].pop())
        i += 1
        if i > n * 50:
            break
    return out[:n]


def main():
    pdf = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    print(f"Extraindo {pdf}...", flush=True)
    r = extrair_com_pipeline(pdf)
    itens_todos = r.itens if hasattr(r, "itens") else r
    print(f"  {len(itens_todos)} itens extraídos", flush=True)
    amostra = _amostra(itens_todos, n)
    print(f"Amostra de {len(amostra)} itens:", flush=True)
    for it in amostra:
        print(f"  - {(it.descricao or '')[:60]}", flush=True)

    from pregoes.scraper import (AlmeidaDistribuidor, DantasDistribuidora,
                                 buscar_precos_para_itens)
    from pregoes.diagnostico import LogDiagnostico, exportar_xlsx

    classes = {"almeida": AlmeidaDistribuidor, "dantas": DantasDistribuidora}
    log = LogDiagnostico(arquivo_edital=pdf, fornecedores=list(classes))

    for nome, cls in classes.items():
        cred = credenciais.carregar(nome)
        if not cred:
            print(f"[{nome}] sem credencial — pulando", flush=True)
            continue
        print(f"\n[{nome}] logando e buscando {len(amostra)} itens ao vivo...", flush=True)
        try:
            with cls(cred["email"], cred["senha"]) as forn:
                def cb(atual, total, msg, _n=nome):
                    print(f"  [{_n}] {atual}/{total} {str(msg)[:50]}", flush=True)
                buscar_precos_para_itens(amostra, [forn], progresso_callback=cb,
                                         diagnostico=log)
        except Exception as exc:
            print(f"[{nome}] FALHOU: {type(exc).__name__}: {exc}", flush=True)

    destino = (config.DATA_DIR / "diagnostico" /
               f"aovivo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
    destino.parent.mkdir(parents=True, exist_ok=True)
    exportar_xlsx(log, destino)
    print(f"\nDIAGNÓSTICO SALVO: {destino}", flush=True)


if __name__ == "__main__":
    main()
