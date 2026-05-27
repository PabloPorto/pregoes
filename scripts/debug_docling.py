"""
Debug do que Docling realmente extrai vs como interpretamos.

Para um PDF, mostra:
- Nº de tabelas que Docling detectou
- Cabeçalho de cada tabela (linha 0 do DataFrame.columns)
- Primeiras 3 linhas de dados
- Mapa que `_identifica_cabecalho_multilinha` reconhece

Uso:
    .venv/bin/python scripts/debug_docling.py /caminho/arquivo.pdf [--max-tabelas N]

Ajuda a entender por que Docling extraiu "27 itens" com confusão de
colunas — vendo o DataFrame bruto identificamos onde o pipeline pifa.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pregoes.extrator import _identifica_cabecalho_multilinha


def debug(caminho: Path, max_tabelas: int = 20) -> None:
    from docling.document_converter import DocumentConverter

    print(f"\n{'#'*80}\n# DEBUG DOCLING — {caminho.name}\n{'#'*80}\n")

    converter = DocumentConverter()
    conv_res = converter.convert(str(caminho))

    n_tabs = len(conv_res.document.tables)
    print(f"Docling detectou {n_tabs} tabelas no total.\n")

    for ix, table in enumerate(conv_res.document.tables):
        if ix >= max_tabelas:
            print(f"\n... limite de {max_tabelas} tabelas atingido.")
            break

        df = table.export_to_dataframe(doc=conv_res.document)

        print(f"--- Tabela #{ix + 1} — {len(df)} linhas, {len(df.columns)} colunas ---")
        print("  Colunas (cabeçalho do Docling):")
        for c, col_name in enumerate(df.columns):
            preview = str(col_name)[:50].replace("\n", " | ")
            print(f"    [{c}] {preview!r}")

        # Como ficaria a linha 0 que nosso extrator considera "cabeçalho"?
        # No extrator_docling, criamos linhas como [cabeçalho] + df.values.tolist()
        cabecalho_linha = [str(c) for c in df.columns]
        linhas_dados = [[str(v) for v in row] for row in df.values.tolist()]
        tabela_bruta = [cabecalho_linha] + linhas_dados

        mapa, n_consumidas = _identifica_cabecalho_multilinha(tabela_bruta)
        print(f"  → Mapa detectado pelo extrator: {mapa}")
        print(f"  → Linhas-cabeçalho consumidas: {n_consumidas}")

        # Primeiras 3 linhas de dados
        if len(df) > 0:
            print(f"  Primeiras 3 linhas de dados:")
            for r_idx, row in enumerate(df.head(3).values.tolist()):
                cells = [str(v)[:30].replace("\n", " | ") for v in row]
                print(f"    [linha {r_idx}] {cells}")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    max_t = 20
    args = []
    for a in sys.argv[1:]:
        if a.startswith("--max-tabelas="):
            max_t = int(a.split("=", 1)[1])
        else:
            args.append(a)
    for pdf in args:
        debug(Path(pdf), max_tabelas=max_t)
