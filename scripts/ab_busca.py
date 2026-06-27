"""A/B da otimização de busca: espera ATIVA (v1.0.16) vs networkidle+sleep fixo.

Mede se a otimização PERDEU resultados em relação à versão anterior, nos MESMOS
itens reais, na MESMA sessão logada, SEM cache (chama forn.buscar direto, não o
orquestrador). Para cada item roda as DUAS versões e compara o conjunto de
resultados (código + preço). Alterna a ordem (old-first / new-first) para
cancelar viés de ordenação/lentidão do site.

Uso:
    python scripts/ab_busca.py <pdf> [almeida|dantas] [passo]
Ex: python scripts/ab_busca.py "/caminho/Edital 011.pdf" almeida 10
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pregoes import scraper as S, credenciais, config
from pregoes.extrator import extrair_pdf

PDF = sys.argv[1]
FORN = sys.argv[2] if len(sys.argv) > 2 else "almeida"
PASSO = int(sys.argv[3]) if len(sys.argv) > 3 else 10

CLS = {"almeida": S.AlmeidaDistribuidor, "dantas": S.DantasDistribuidora}[FORN]


def _loader_antigo(page, url: str) -> None:
    """Comportamento ANTES da v1.0.16: networkidle + sleep fixo de 1,5s."""
    page.goto(url, wait_until="networkidle",
              timeout=config.SCRAPER_TIMEOUT_SEGUNDOS * 1000)
    page.wait_for_timeout(1_500)


_loader_novo = S._carregar_busca   # espera ativa (atual)


def _chave(r):
    return (r.codigo_fornecedor, round(r.preco, 2))


def main() -> None:
    itens = extrair_pdf(PDF).itens
    amostra = [itens[k] for k in range(0, len(itens), PASSO)]
    cred = credenciais.carregar(FORN)
    if not cred:
        raise SystemExit(f"Sem credencial para {FORN}")

    reg = []   # (num, desc, old_set, new_set)
    t0 = time.perf_counter()
    with CLS(cred["email"], cred["senha"]) as forn:
        def run(loader, desc):
            S._carregar_busca = loader
            try:
                return {_chave(r) for r in forn.buscar(desc)}
            except Exception:
                return set()

        for idx, it in enumerate(amostra):
            d = it.descricao
            if idx % 2 == 0:               # alterna a ordem (cancela viés)
                old, new = run(_loader_antigo, d), run(_loader_novo, d)
            else:
                new, old = run(_loader_novo, d), run(_loader_antigo, d)
            reg.append((it.item_numero, d, old, new))
            print(f"  #{it.item_numero:>3} old={len(old):>2} new={len(new):>2}  {d[:34]}")
    S._carregar_busca = _loader_novo

    found_old = sum(1 for *_, o, _ in reg if o)
    found_new = sum(1 for *_, n in reg if n)
    regress = [(num, d) for num, d, o, n in reg if o and not n]
    melhora = [(num, d) for num, d, o, n in reg if n and not o]
    divergem = [(num, d, o, n) for num, d, o, n in reg if o and n and o != n]

    print(f"\n=== A/B {FORN} | {len(reg)} itens | {time.perf_counter()-t0:.0f}s ===")
    print(f"achados ANTIGO: {found_old}/{len(reg)}   achados NOVO: {found_new}/{len(reg)}")
    print(f"REGRESSÃO (achava e PAROU de achar): {len(regress)}")
    for num, d in regress:
        print(f"   ! #{num} {d[:42]}")
    print(f"MELHORA (passou a achar): {len(melhora)}")
    for num, d in melhora:
        print(f"   + #{num} {d[:42]}")
    print(f"achados por AMBOS mas conjunto DIFERE: {len(divergem)}")
    for num, d, o, n in divergem[:10]:
        print(f"   ~ #{num} {d[:26]} old={sorted(o)} new={sorted(n)}")


if __name__ == "__main__":
    main()
