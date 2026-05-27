"""
Inspetor detalhado de tabelas em um PDF.

Para cada tabela detectada pelo pdfplumber em cada página, mostra:
- Cabeçalho consolidado (até 3 primeiras linhas concatenadas)
- 2 primeiras linhas de dados
- Última linha de dados
- Nº total de linhas
- "Forma" da tabela: largura média de coluna 0, presença de números, etc.

Uso:
    .venv/bin/python scripts/inspecionar_tabelas.py /caminho/arquivo.pdf
    .venv/bin/python scripts/inspecionar_tabelas.py pasta/   # roda em todos os .pdf
    .venv/bin/python scripts/inspecionar_tabelas.py arquivo.pdf --max-tabelas 20

Ajuda a identificar VISUALMENTE qual tabela é a real de itens — leitura
manual para calibrar heurísticas.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdfplumber


def _flatten(c) -> str:
    if c is None:
        return ""
    return str(c).replace("\n", " | ").strip()[:60]


def inspecionar(caminho: Path, max_tabelas: int = 100) -> None:
    print(f"\n{'#'*80}\n# {caminho.name}\n{'#'*80}\n")

    contador_global = 0
    with pdfplumber.open(caminho) as pdf:
        for n_pag, page in enumerate(pdf.pages, start=1):
            try:
                tabs = page.extract_tables() or []
            except Exception as e:
                print(f"[pág {n_pag}] erro: {e}")
                continue
            for n_tab, tab in enumerate(tabs):
                contador_global += 1
                if contador_global > max_tabelas:
                    print(f"\n[Mostradas {max_tabelas} tabelas — limite. "
                          f"Total no PDF: {sum(len(p.extract_tables() or []) for p in pdf.pages)}]")
                    return
                _imprimir_tabela(n_pag, n_tab, tab, contador_global)


def _imprimir_tabela(n_pag: int, n_tab: int, tab: list, contador: int) -> None:
    print(f"\n--- Tabela #{contador} (página {n_pag}, idx pg {n_tab}) — {len(tab)} linhas ---")

    # Cabeçalho consolidado: 3 primeiras linhas se forem curtas em colunas numéricas
    n_cols_max = max((len(r) for r in tab if r), default=0)
    print(f"  cols: {n_cols_max}")

    # Mostra primeiras 3 linhas como "candidatos a cabeçalho"
    for i, linha in enumerate(tab[:3]):
        celulas = [_flatten(c) for c in (linha or [])]
        print(f"  [linha {i}] {celulas}")

    # Mostra linha 4 (primeiro dado provável) e última (caso seja útil)
    if len(tab) > 3:
        print(f"  [linha 3 (primeiro dado?)] {[_flatten(c) for c in (tab[3] or [])]}")
    if len(tab) > 5:
        print(f"  [linha -1 (última)] {[_flatten(c) for c in (tab[-1] or [])]}")


def coletar_pdfs(args: list[str]) -> tuple[list[Path], int]:
    pdfs: list[Path] = []
    max_t = 100
    for arg in args:
        if arg.startswith("--max-tabelas="):
            max_t = int(arg.split("=", 1)[1])
            continue
        p = Path(arg)
        if p.is_dir():
            pdfs.extend(sorted(p.glob("*.pdf")))
        elif p.is_file() and p.suffix.lower() == ".pdf":
            pdfs.append(p)
    return pdfs, max_t


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    pdfs, max_t = coletar_pdfs(sys.argv[1:])
    for pdf in pdfs:
        try:
            inspecionar(pdf, max_tabelas=max_t)
        except Exception as e:
            print(f"\n❌ Falha em {pdf}: {type(e).__name__}: {e}")
