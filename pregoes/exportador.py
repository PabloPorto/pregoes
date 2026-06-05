"""
Exportador de itens extraídos para a planilha matriz do cliente.

REGRA OURO: o template do cliente é sagrado. Não criamos nada do zero.
- Carregamos `matriz_template.xlsx`
- Preenchemos apenas as colunas C, D, E, F (descrição, unidade, qtd, ref)
- Deixamos L, T, U para o scraper preencher depois
- TODAS as fórmulas existentes (G, H, I, J, K, N, P, R, S, V-AB, totais) ficam intactas
- Salvamos como novo arquivo

Edge case >500 itens: copia as fórmulas das linhas existentes para as novas
usando openpyxl.Translator (mantém $V$4 absoluto e ajusta L504→L505 relativo).
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.formula.translate import Translator
from openpyxl.utils import get_column_letter

from . import config
from .extrator import ItemExtraido

# Caracteres que, no início de um TEXTO, fazem o Excel/openpyxl interpretar a
# célula como fórmula viva (data_type 'f'). Uma descrição de edital começando
# com '=' (ou '+', '-', '@') corromperia a planilha entregue ao pregoeiro — ou,
# pior, viraria injeção de fórmula (=cmd|...). Por isso prefixamos apóstrofo,
# o mesmo recurso que o Excel usa para "forçar texto" numa célula.
_PREFIXOS_PERIGOSOS = ("=", "+", "-", "@")


@dataclass(frozen=True)
class SelecaoFornecedor:
    """
    Escolha do operador para um item do edital, vinda da tela de busca.

    O operador SEMPRE escolhe entre as opções retornadas pelos scrapers — o
    sistema nunca decide marca (Resp final 7). Esta dataclass empacota a
    escolha para o exportador preencher as colunas L, T, U da planilha.
    """
    preco: float
    codigo_fornecedor: str
    marca: str


# Faixa de linhas que o template já tem prontas
LINHA_PRIMEIRO_ITEM = config.LINHA_DADOS_INICIO        # 5
LINHA_ULTIMO_ITEM_TEMPLATE = 504                       # template suporta 500 itens (5..504)
LINHA_TOTAL_REFERENCIA = 505                           # =SUM(Y5:Y504)
LINHA_CABECALHO_TOTAL = 506                            # 'FATURAMENTO', 'MARGEM', 'LUCRO'
LINHA_TOTAL_FINAL = 507                                # =SUM(I5:I504), =G507/C507, =SUM(K5:K504)


def exportar(
    itens: Iterable[ItemExtraido],
    *,
    orgao: str | None = None,
    numero_pregao: str | None = None,
    markup: float | None = None,
    selecoes: dict[int, "SelecaoFornecedor"] | None = None,
    destino: Path | str | None = None,
    template: Path | str | None = None,
) -> Path:
    """
    Gera planilha matriz preenchida.

    Args:
        itens: lista de ItemExtraido vindos do extrator
        orgao: nome do órgão (vai na célula C1 'ORGÃO')
        numero_pregao: número do pregão (vai na célula C2 'PREGÃO')
        markup: multiplicador sobre o custo de aquisição. Default = valor de
            `config.MARKUP_DEFAULT` (1.45). O cliente pode passar 1.30 para
            um pregão com concorrência apertada, etc. Substitui as fórmulas
            `G = =S*1.45` do template por `G = =S*<markup>`.
        selecoes: opcional, mapa `{índice_do_item: SelecaoFornecedor}` com a
            marca/preço escolhida pelo operador para cada item após a busca
            nos fornecedores. Quando presente, preenche colunas L (preço),
            T (código fornecedor) e U (marca). Itens ausentes do mapa ficam
            sem preço (operador preenche manualmente no Excel, comportamento
            pré-Fase 2).
        destino: caminho de saída (default: data/exports/<auto>.xlsx)
        template: caminho do template (default: config.TEMPLATE_MATRIZ)

    Returns:
        Path do arquivo gerado.
    """
    template_path = Path(template) if template else config.TEMPLATE_MATRIZ
    if not template_path.exists():
        raise FileNotFoundError(f"Template não encontrado: {template_path}")

    itens = list(itens)
    if not itens:
        raise ValueError("Lista de itens vazia — nada para exportar.")

    markup_efetivo = markup if markup is not None else config.MARKUP_DEFAULT

    if destino is None:
        nome = _nome_default(orgao, numero_pregao)
        destino = config.EXPORTS_DIR / nome
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    # Cópia binária do template — preserva tudo (estilos, fórmulas, mesclagens)
    shutil.copyfile(template_path, destino)

    wb = load_workbook(destino)   # SEM data_only — mantém fórmulas
    ws = wb.active

    # Preencher cabeçalho (C1, C2) — também texto livre, mesma neutralização
    if orgao:
        _escrever_texto(ws["C1"], orgao)
    if numero_pregao:
        _escrever_texto(ws["C2"], numero_pregao)

    # Aplicar markup ANTES de expandir: assim, quando _expandir_linhas copiar
    # a fórmula da linha modelo (504) via Translator, ela já estará com o
    # markup correto e as linhas novas herdam o valor sem retrabalho.
    if markup_efetivo != config.MARKUP_DEFAULT:
        _aplicar_markup(ws, markup_efetivo)

    # Expandir template se o edital tiver mais que 500 itens
    n_itens = len(itens)
    if n_itens > 500:
        _expandir_linhas(ws, n_extras=n_itens - 500)

    # Preencher itens
    selecoes = selecoes or {}
    for offset, item in enumerate(itens):
        linha = LINHA_PRIMEIRO_ITEM + offset
        ws[f"A{linha}"] = offset + 1
        ws[f"B{linha}"] = item.item_numero if item.item_numero is not None else (offset + 1)
        # C, D, T, U recebem TEXTO LIVRE (descrição do edital, unidade,
        # código/marca raspados). Neutralizamos prefixos de fórmula para o
        # Excel não interpretar o conteúdo como cálculo (ver _escrever_texto).
        _escrever_texto(ws[f"C{linha}"], item.descricao)
        _escrever_texto(ws[f"D{linha}"], item.unidade or "")
        ws[f"E{linha}"] = item.quantidade if item.quantidade is not None else 0
        if item.valor_referencia is not None:
            ws[f"F{linha}"] = item.valor_referencia

        # Colunas vindas do scraper (Fase 2): L=preço (número), T=código, U=marca.
        # T e U são texto raspado do site do fornecedor → mesma neutralização.
        sel = selecoes.get(offset)
        if sel is not None:
            ws[f"L{linha}"] = sel.preco
            _escrever_texto(ws[f"T{linha}"], sel.codigo_fornecedor)
            _escrever_texto(ws[f"U{linha}"], sel.marca)

    wb.save(destino)
    return destino


def _escrever_texto(celula: "Cell", texto: object) -> None:
    """
    Escreve TEXTO LIVRE numa célula neutralizando injeção de fórmula.

    PORQUÊ: openpyxl trata qualquer string iniciada por '=', '+', '-' ou '@'
    como fórmula viva (data_type 'f'). Uma descrição de edital ou um campo
    raspado do site do fornecedor que comece com esses caracteres
    (ex.: "=cmd|calc", "-3/4 POL") corromperia a planilha entregue ao
    pregoeiro. Prefixamos apóstrofo — o mesmo recurso de "forçar texto" do
    Excel — para que o conteúdo seja exibido literalmente.

    Não usar em células numéricas nem de fórmula do template (G..AB): aqui só
    passam C, D, T, U e os cabeçalhos C1/C2, todos texto.
    """
    if isinstance(texto, str) and texto[:1] in _PREFIXOS_PERIGOSOS:
        celula.value = "'" + texto
    else:
        celula.value = texto


def _aplicar_markup(ws, markup: float) -> None:
    """
    Substitui o multiplicador do markup nas fórmulas da coluna G (preço de venda).

    Template original: G5 = =S5*1.45, G6 = =S6*1.45, ... G504 = =S504*1.45.
    Após esta função, todas viram =Sn*<markup>. As outras fórmulas (H, I, etc.)
    referenciam G e portanto se ajustam automaticamente pelo Excel.

    PORQUÊ do regex (e não derivar de MARKUP_DEFAULT): o multiplicador real
    está na própria célula (=S5*1.45). Se derivássemos o sufixo a trocar de
    config.MARKUP_DEFAULT e alguém editasse esse valor para 1.5, a busca por
    "*1.5" jamais casaria "=S5*1.45" e o markup deixaria de ser aplicado em
    SILÊNCIO — erro financeiro em toda a planilha. O regex troca o número que
    multiplica Sn diretamente, qualquer que seja o literal do template.
    """
    novo_mult = _formatar_markup(markup)
    for linha in range(LINHA_PRIMEIRO_ITEM, LINHA_ULTIMO_ITEM_TEMPLATE + 1):
        celula = ws[f"G{linha}"]
        valor = celula.value
        if not isinstance(valor, str):
            continue
        # Casa o "* <número>" que multiplica a referência Sn (ex.: *1.45, *1.5,
        # *2). O '*' obrigatório impede pegar os dígitos de "S504" (sem '*'
        # antes). Substitui só a 1ª ocorrência.
        nova = re.sub(r"\*\s*\d+(?:\.\d+)?", f"*{novo_mult}", valor, count=1)
        if nova != valor:
            celula.value = nova


def _formatar_markup(markup: float) -> str:
    """
    Formata o markup como literal de fórmula Excel sem zeros/ponto supérfluos.

    1.30 → '1.3', 1.6 → '1.6', 2.0 → '2'. Mantém compatibilidade com o
    formato do template e com os testes (=S5*1.3, não =S5*1.30).
    """
    return f"{markup:g}"


def _expandir_linhas(ws, n_extras: int):
    """
    Insere N linhas adicionais antes da linha de totais, copiando as fórmulas
    da última linha de itens existente (504) com translação de referências
    relativas.
    """
    # Linha modelo (já tem todas as fórmulas certinhas)
    linha_modelo = LINHA_ULTIMO_ITEM_TEMPLATE
    nova_linha_total_ref = LINHA_TOTAL_REFERENCIA + n_extras
    nova_linha_total_final = LINHA_TOTAL_FINAL + n_extras

    # Inserir linhas em branco (openpyxl empurra tudo para baixo, inclusive totais)
    ws.insert_rows(idx=linha_modelo + 1, amount=n_extras)

    # Replicar fórmulas/conteúdo da linha modelo para as novas
    for nova_idx in range(linha_modelo + 1, linha_modelo + 1 + n_extras):
        for col in range(1, ws.max_column + 1):
            valor_modelo = ws.cell(row=linha_modelo, column=col).value
            if valor_modelo is None:
                continue
            if isinstance(valor_modelo, str) and valor_modelo.startswith("="):
                # Tradutor mantém $V$4 absoluto e ajusta L504→L<nova> relativo
                origem = f"{get_column_letter(col)}{linha_modelo}"
                t = Translator(valor_modelo, origin=origem)
                destino_ref = f"{get_column_letter(col)}{nova_idx}"
                nova_formula = t.translate_formula(destino_ref)
                ws.cell(row=nova_idx, column=col).value = nova_formula
            # valores constantes (ex: número da linha em A) — pular, vamos preencher depois

    # Atualizar fórmulas de total que referenciavam :504
    _ajustar_totais(ws, n_extras)


def _ajustar_totais(ws, n_extras: int):
    """Ajusta SUM(:504) para SUM(:novo_limite) nas linhas de total que foram
    empurradas para baixo pela inserção."""
    nova_ultima_item = LINHA_ULTIMO_ITEM_TEMPLATE + n_extras
    nova_total_ref = LINHA_TOTAL_REFERENCIA + n_extras
    nova_total_final = LINHA_TOTAL_FINAL + n_extras

    # Linha 505 antes / nova_total_ref agora: =SUM(Y5:Y504) → =SUM(Y5:Y<nova>)
    for col in range(1, ws.max_column + 1):
        v = ws.cell(row=nova_total_ref, column=col).value
        if isinstance(v, str) and ":" in v and "504" in v:
            ws.cell(row=nova_total_ref, column=col).value = v.replace("504", str(nova_ultima_item))

    # Linha 507 antes: =SUM(I5:I504), =G507/C507, =SUM(K5:K504)
    for col in range(1, ws.max_column + 1):
        v = ws.cell(row=nova_total_final, column=col).value
        if isinstance(v, str) and v.startswith("="):
            atualizada = v.replace(":I504", f":I{nova_ultima_item}") \
                          .replace(":K504", f":K{nova_ultima_item}") \
                          .replace("G507", f"G{nova_total_final}") \
                          .replace("C507", f"C{nova_total_final}")
            ws.cell(row=nova_total_final, column=col).value = atualizada


def _nome_default(orgao: str | None, numero: str | None) -> str:
    from datetime import datetime
    partes = ["pregao"]
    if orgao:
        partes.append("".join(c for c in orgao if c.isalnum())[:20])
    if numero:
        partes.append("".join(c for c in numero if c.isalnum())[:20])
    partes.append(datetime.now().strftime("%Y%m%d_%H%M%S"))
    return "_".join(partes) + ".xlsx"
