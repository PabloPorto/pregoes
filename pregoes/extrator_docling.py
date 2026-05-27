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


def coletar_tabelas_brutas_docling(
    caminho_pdf: str | Path,
    *,
    reconstruir_fatias: bool = True,
) -> list[list[list[str]]]:
    """Coleta TODAS as tabelas que Docling detecta no PDF, opcionalmente
    aplicando reconstrução de fatias.

    Devolve `list[list[list[str]]]` — formato compatível com
    `ResultadoExtracao.tabelas_brutas`. Cada tabela vem como:
    `[cabeçalho_linha, linha_dados_1, linha_dados_2, ...]`.

    Args:
        caminho_pdf: PDF a processar.
        reconstruir_fatias: se True (default), tenta fundir fatias que
            Docling fragmentou (tabelas multi-página viram peças
            separadas no Docling 2.7). Ver `reconstrutor_docling.reconstruir`
            para a heurística e seus limites. Passe False quando quiser
            inspecionar a saída crua do Docling (debug).

    Usado pela tela de Mapeamento Manual quando o operador clica em
    "Carregar tabelas com Docling" — Docling encontra MAIS tabelas
    (modelo visual) mas fragmenta as multi-página; a reconstrução
    automática funde o que parece continuação para que o operador
    consiga mapear UMA tabela e capturar todos os itens.
    """
    caminho_pdf = Path(caminho_pdf)
    converter = _obter_converter()
    conv_res = converter.convert(str(caminho_pdf))

    cruas: list[list[list[str]]] = []
    for table in conv_res.document.tables:
        df = table.export_to_dataframe(doc=conv_res.document)
        cabecalho = [str(c) for c in df.columns]
        linhas_dados = [[_celula_str(v) for v in row] for row in df.values.tolist()]
        cruas.append([cabecalho] + linhas_dados)

    if not reconstruir_fatias:
        return cruas

    from .reconstrutor_docling import reconstruir
    return reconstruir(cruas)


def extrair_via_docling(caminho_pdf: str | Path) -> ResultadoExtracao:
    """Aplica pipeline automático de score em tabelas Docling.

    Mantido para uso em diagnóstico/script; o caminho usual da UI é
    `coletar_tabelas_brutas_docling` → mapeamento manual.

    Lança `ImportError` se Docling não estiver instalado.
    """
    caminho_pdf = Path(caminho_pdf)
    tabelas_listas = coletar_tabelas_brutas_docling(caminho_pdf)
    tabelas_brutas = [
        _TabelaBruta(pagina=0, indice_pagina=ix, linhas=linhas)
        for ix, linhas in enumerate(tabelas_listas)
    ]
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
