"""
Camada de extração via Docling (IBM) — fallback visual para PDFs que
pdfplumber não consegue processar bem.

Quando usar: chamada automática por `extrator.extrair_com_pipeline` quando
a camada pdfplumber retorna `confianca_global < 0.95`. Não chamar
diretamente — o pipeline conhece a ordem certa.

Custo:
- Primeira execução em uma máquina: baixa ~500MB de modelos
  (TableFormer + DocLayNet) para ~/.cache/docling. ~1-3min na primeira vez.
- Execuções subsequentes: ~3-10s por página (CPU). Compare com ~0.2s/pág
  do pdfplumber — só compensa quando pdfplumber falha.

Saída: `ResultadoExtracao` no mesmo formato da camada pdfplumber. O
pipeline de score semântico (validador.score_linha + processar_tabelas_brutas)
é o MESMO — Docling só substitui o passo de "ler tabelas brutas do PDF".
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .extrator import _TabelaBruta, processar_tabelas_brutas, ResultadoExtracao


# Singleton lazy do DocumentConverter — instanciar custa importar PyTorch
# (alguns segundos + ~1GB de RAM). Pagar uma vez por processo.
_converter = None


def _obter_converter():
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter
        _converter = DocumentConverter()
    return _converter


def extrair_via_docling(caminho_pdf: str | Path) -> ResultadoExtracao:
    """Coleta tabelas via Docling e processa com o mesmo pipeline de score
    semântico da camada principal.

    Lança `ImportError` se Docling não estiver instalado — quem chama deve
    capturar (já feito em `extrair_com_pipeline`).
    """
    caminho_pdf = Path(caminho_pdf)
    converter = _obter_converter()
    conv_res = converter.convert(str(caminho_pdf))

    tabelas_brutas: list[_TabelaBruta] = []
    for table in conv_res.document.tables:
        df = table.export_to_dataframe(doc=conv_res.document)
        # Linha 0 do nosso modelo = cabeçalho. DataFrame tem cabeçalho nos
        # nomes das colunas → reconstruímos como primeira linha.
        cabecalho = [str(c) for c in df.columns]
        linhas_dados = [
            [_celula_str(v) for v in row]
            for row in df.values.tolist()
        ]
        linhas = [cabecalho] + linhas_dados

        # Página: Docling não expõe número de página por tabela na API
        # estável. Para nosso pipeline o número exato não é crítico —
        # usamos só para metadado de debug. Marcamos como 0 (desconhecido).
        tabelas_brutas.append(_TabelaBruta(pagina=0, indice_pagina=0, linhas=linhas))

    return processar_tabelas_brutas(tabelas_brutas, arquivo=str(caminho_pdf))


def _celula_str(v) -> str:
    """Normaliza uma célula Docling para string. Trata None, NaN, números."""
    if v is None:
        return ""
    try:
        # NaN do pandas
        import math
        if isinstance(v, float) and math.isnan(v):
            return ""
    except Exception:
        pass
    return str(v).strip()


def disponivel() -> bool:
    """Verifica se Docling está instalado neste ambiente (sem importá-lo
    de verdade — só checa que o pacote existe).
    """
    try:
        import importlib.util
        return importlib.util.find_spec("docling") is not None
    except Exception:
        return False
