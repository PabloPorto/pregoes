"""
Testes de regressão do sprint P1 (robustez do piloto) para o exportador:

(B1 / P1.4) validar_template — o cliente abre o MATRIZ no Excel e pode mover
    uma coluna, apagar uma fórmula ou salvar por cima. Sem validação, o
    exportador escreveria na célula errada ou não aplicaria o markup, gerando
    uma planilha corrompida EM SILÊNCIO. Agora falha cedo, com mensagem clara.

(B2 / P1.5) escrita atômica — a planilha final só "aparece" completa (monta
    num .part.xlsx e publica com rename atômico). Sem isso, um crash no meio do
    save deixaria um .xlsx pela metade que o pregoeiro tentaria abrir.

Sem rede/navegador/Streamlit. Usa o template real como base e o adultera em
arquivos temporários para os cenários de corrupção.
"""
from __future__ import annotations

import pytest
from openpyxl import load_workbook

from pregoes import config
from pregoes.exportador import (
    exportar,
    validar_template,
    LINHA_PRIMEIRO_ITEM,
    LINHA_ULTIMO_ITEM_TEMPLATE,
)
from pregoes.extrator import ItemExtraido


def _item(descricao="PARAFUSO 1/2", **kw) -> ItemExtraido:
    base = dict(descricao=descricao, unidade="UN", quantidade=10.0,
                valor_referencia=1.0, item_numero=1)
    base.update(kw)
    return ItemExtraido(**base)


def _template_adulterado(tmp_path, mutacao) -> "Path":
    """Carrega o template real, aplica `mutacao(ws)` e salva numa cópia tmp."""
    destino = tmp_path / "corrompido.xlsx"
    wb = load_workbook(config.TEMPLATE_MATRIZ)
    mutacao(wb.active)
    wb.save(destino)
    return destino


# === B1 — validar_template ==========================================

def test_validar_template_aceita_o_template_real():
    """O template legítimo do cliente passa sem erro — guarda contra
    falso-positivo (um validador estrito demais bloquearia TODA exportação)."""
    validar_template(config.TEMPLATE_MATRIZ)   # não deve levantar


def test_validar_template_rejeita_formula_g_quebrada(tmp_path):
    """Se a fórmula de PREÇO VENDA (=Sn*markup) some, o markup falharia calado
    → validar_template barra com mensagem sobre a coluna G."""
    corrompido = _template_adulterado(
        tmp_path,
        lambda ws: ws.__setitem__(f"G{LINHA_PRIMEIRO_ITEM}", 123),  # não é fórmula
    )
    with pytest.raises(ValueError, match="G"):
        validar_template(corrompido)


def test_validar_template_rejeita_cabecalho_apagado(tmp_path):
    """Coluna de cabeçalho apagada (provável coluna movida) → erro nomeando a
    coluna afetada."""
    corrompido = _template_adulterado(
        tmp_path,
        lambda ws: ws.__setitem__(f"C{config.LINHA_CABECALHO}", None),
    )
    with pytest.raises(ValueError, match="C"):
        validar_template(corrompido)


def test_exportar_barra_template_corrompido_sem_escrever_destino(tmp_path):
    """exportar() valida ANTES de copiar/preencher: com template inválido,
    levanta ValueError e NÃO deixa nenhum arquivo no destino."""
    corrompido = _template_adulterado(
        tmp_path,
        lambda ws: ws.__setitem__(f"G{LINHA_ULTIMO_ITEM_TEMPLATE}", 0),
    )
    destino = tmp_path / "saida.xlsx"
    with pytest.raises(ValueError):
        exportar([_item()], template=corrompido, destino=destino)
    assert not destino.exists()


# === B2 — escrita atômica ===========================================

def test_exportacao_bem_sucedida_publica_destino_e_nao_deixa_part(tmp_path):
    """No caminho feliz, o destino final existe e nenhum .part.xlsx
    intermediário fica para trás."""
    destino = tmp_path / "ok.xlsx"
    ret = exportar([_item()], destino=destino)
    assert ret == destino and destino.exists()
    intermediario = tmp_path / "ok.part.xlsx"
    assert not intermediario.exists()
    # E o arquivo publicado é um xlsx íntegro (abre e tem a fórmula G).
    wb = load_workbook(destino)
    assert isinstance(wb.active[f"G{LINHA_PRIMEIRO_ITEM}"].value, str)
