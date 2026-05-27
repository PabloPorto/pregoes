"""
Diagnóstico do extrator atual contra uma pasta de PDFs de edital.

Uso:
    .venv/bin/python scripts/diagnostico_extrator.py /caminho/para/pasta_de_pdfs
    .venv/bin/python scripts/diagnostico_extrator.py pdf1.pdf pdf2.pdf

Para cada PDF, mede:
- Páginas e estimativa texto vs imagem (heurística simples para flagar OCR)
- Tabelas detectadas por pdfplumber.extract_tables
- Resultado de pregoes.extrator.extrair_pdf (itens, duvidosos, avisos)

Usado para calibrar heurísticas em editais reais quando o cliente fornece
novos formatos — não roda em CI.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Permite rodar `python scripts/diagnostico_extrator.py ...` sem PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdfplumber

from pregoes.extrator import extrair_pdf


def diagnosticar(caminho: Path) -> None:
    print(f"\n{'='*80}\nPDF: {caminho.name}\n{'='*80}")

    with pdfplumber.open(caminho) as pdf:
        n_pages = len(pdf.pages)
        paginas_com_texto = 0
        chars_total = 0
        for p in pdf.pages:
            t = p.extract_text() or ""
            chars_total += len(t)
            if len(t.strip()) > 50:
                paginas_com_texto += 1
        print(f"Páginas: {n_pages} | com texto extraível: {paginas_com_texto}/{n_pages}"
              f" | chars: {chars_total:,}")
        if paginas_com_texto < n_pages * 0.5:
            print("⚠️ PROVAVELMENTE ESCANEADO — precisa OCR")

        n_tabelas = 0
        amostras = []
        for i, p in enumerate(pdf.pages):
            try:
                tabs = p.extract_tables() or []
            except Exception as e:
                print(f"  Pág {i+1}: pdfplumber.extract_tables erro: {e}")
                continue
            n_tabelas += len(tabs)
            for t in tabs:
                if t and t[0] and len(amostras) < 5:
                    cab = [str(c)[:30] if c else "" for c in t[0][:8]]
                    amostras.append((i + 1, cab))
        print(f"Tabelas detectadas (pdfplumber): {n_tabelas}")
        if amostras:
            print("Primeiros cabeçalhos detectados:")
            for pag, cab in amostras:
                print(f"  Pág {pag}: {cab}")

    print("--- nosso extrair_pdf() ---")
    try:
        res = extrair_pdf(caminho)
    except Exception as e:
        print(f"❌ extrair_pdf falhou: {type(e).__name__}: {e}")
        return

    print(f"Itens: {res.total_itens} | duvidosos: {res.total_duvidosos}"
          f" | confiança: {res.confianca_global:.2%}"
          f" | mapa: {res.mapa_colunas_detectado}")
    if res.avisos:
        for a in res.avisos:
            print(f"  ⚠️ {a}")
    if res.itens:
        for item in res.itens[:3]:
            desc = (item.descricao or "")[:60]
            print(f"  #{item.item_numero} {desc!r} qtd={item.quantidade}"
                  f" ref={item.valor_referencia}")
        if res.total_itens > 3:
            ult = res.itens[-1]
            desc = (ult.descricao or "")[:60]
            print(f"  ...")
            print(f"  #{ult.item_numero} {desc!r} qtd={ult.quantidade}"
                  f" ref={ult.valor_referencia}")


def coletar_pdfs(args: list[str]) -> list[Path]:
    pdfs: list[Path] = []
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            pdfs.extend(sorted(p.glob("*.pdf")))
        elif p.is_file() and p.suffix.lower() == ".pdf":
            pdfs.append(p)
    return pdfs


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for pdf in coletar_pdfs(sys.argv[1:]):
        try:
            diagnosticar(pdf)
        except Exception as e:
            print(f"\n❌ Falha em {pdf}: {type(e).__name__}: {e}")
