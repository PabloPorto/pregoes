"""
Compara extração nos 5 PDFs com vs sem reconstrução de fatias Docling.

Critério de aceitação:
- NÃO REGREDIR: PDFs que já funcionavam bem na Camada 1 (02, 04) não
  podem piorar.
- MELHORAR OU IGUALAR: PDFs problemáticos (01, 03, 05) podem melhorar.

Métricas reportadas por PDF e cenário:
- nº tabelas no Docling antes/depois
- linhas na tabela com mais linhas (proxy de "tabela principal" após reconstrução)
- itens extraídos pelo pipeline de score
- confiança global
- itens duvidosos

Saída CSV-like ao stdout. Tempo total esperado: ~15-20 minutos.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pregoes.extrator_docling import coletar_tabelas_brutas_docling
from pregoes.extrator import _TabelaBruta, processar_tabelas_brutas


PDFS = [
    "/tmp/editais_amostra/01_edital_anexos.pdf",
    "/tmp/editais_amostra/02_hash.pdf",
    "/tmp/editais_amostra/03_tre_pe.pdf",
    "/tmp/editais_amostra/04_hidraulicos.pdf",
    "/tmp/editais_amostra/05_sei.pdf",
]


def _pipeline(tabelas_brutas, arquivo):
    """Aplica pipeline de score sobre lista de tabelas brutas."""
    brutas = [
        _TabelaBruta(pagina=0, indice_pagina=ix, linhas=t)
        for ix, t in enumerate(tabelas_brutas)
    ]
    return processar_tabelas_brutas(brutas, arquivo=arquivo)


def _resumo(tabelas, res, label, elapsed):
    n_tabelas = len(tabelas)
    n_maior = max((len(t) for t in tabelas), default=0)
    print(
        f"  [{label}]  tab={n_tabelas:3d}  maior={n_maior:3d}  "
        f"itens={res.total_itens:3d}  duvidosos={res.total_duvidosos:3d}  "
        f"conf={res.confianca_global:5.1%}  ({elapsed:.0f}s)",
        flush=True,
    )


def diagnosticar(caminho: str) -> None:
    print(f"\n{Path(caminho).name}", flush=True)

    # Sem reconstrução
    t0 = time.time()
    cruas = coletar_tabelas_brutas_docling(caminho, reconstruir_fatias=False)
    elapsed_a = time.time() - t0
    res_a = _pipeline(cruas, caminho)
    _resumo(cruas, res_a, "SEM ", elapsed_a)

    # Com reconstrução
    t0 = time.time()
    reconst = coletar_tabelas_brutas_docling(caminho, reconstruir_fatias=True)
    elapsed_b = time.time() - t0
    res_b = _pipeline(reconst, caminho)
    _resumo(reconst, res_b, "COM ", elapsed_b)

    # Diff
    delta_itens = res_b.total_itens - res_a.total_itens
    delta_conf = res_b.confianca_global - res_a.confianca_global
    sinal_itens = "+" if delta_itens >= 0 else ""
    sinal_conf = "+" if delta_conf >= 0 else ""
    print(
        f"  Δ itens={sinal_itens}{delta_itens}  Δ conf={sinal_conf}{delta_conf:.1%}",
        flush=True,
    )


if __name__ == "__main__":
    for pdf in PDFS:
        try:
            diagnosticar(pdf)
        except Exception as e:
            print(f"\n❌ {Path(pdf).name}: {type(e).__name__}: {e}", flush=True)
