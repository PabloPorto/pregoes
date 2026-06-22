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

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.formula.translate import Translator
from openpyxl.utils import get_column_letter

from . import config, tributacao
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
    ncm: str | None = None   # Fase 4: NCM do produto escolhido → markup por ST
    url: str | None = None   # Fase 4: link da página de detalhe (p/ buscar o NCM)


# Faixa de linhas que o template já tem prontas
LINHA_PRIMEIRO_ITEM = config.LINHA_DADOS_INICIO        # 5
LINHA_ULTIMO_ITEM_TEMPLATE = 504                       # template suporta 500 itens (5..504)
LINHA_TOTAL_REFERENCIA = 505                           # =SUM(Y5:Y504)
LINHA_CABECALHO_TOTAL = 506                            # 'FATURAMENTO', 'MARGEM', 'LUCRO'
LINHA_TOTAL_FINAL = 507                                # =SUM(I5:I504), =G507/C507, =SUM(K5:K504)

# Coluna AC (29): indicador "foi gerado com Substituição Tributária?" (pedido
# do cliente, 2026-06). Fica DEPOIS da última coluna do template (AB=28), então
# não desloca nenhuma fórmula G..AB — é escrita só no arquivo exportado, o
# template em disco continua intocado.
COL_ST_INDICADOR = 29                                  # AC
CABECALHO_ST = "SUBST. TRIBUTÁRIA"

# Coluna (1-based) mais à direita que o exportador escreve: U = 21 (preço,
# código e marca vindos do scraper). Define a largura mínima do template.
_COLUNA_MAX_ESCRITA = 21


def validar_template(template_path: Path) -> None:
    """Confere que o template tem a estrutura que o exportador assume.

    PORQUÊ: o cliente abre o MATRIZ no Excel e pode, sem querer, mover uma
    coluna, apagar uma fórmula ou salvar por cima. Sem esta checagem, o
    exportador escreveria descrição/preço na célula errada ou deixaria de
    aplicar o markup — e a planilha entregue ao pregoeiro sairia corrompida
    EM SILÊNCIO (erro financeiro). Aqui falhamos cedo, com mensagem clara em
    português, ANTES de copiar o template e começar a preencher.

    Invariantes (calibrados no template real do cliente — ver G5=`=S5*1.45`):
    - existe uma aba ativa que alcança ao menos a coluna U (preço/cód/marca);
    - os cabeçalhos-âncora da linha 4 (B,C,D,E,F,L,T,U) não estão vazios;
    - a fórmula de PREÇO VENDA da coluna G, na primeira e na última linha do
      bloco de itens, é do tipo `=S{n}*<markup>` — a base que o markup por
      item reescreve. Se esse padrão sumiu, o markup falharia calado.

    Levanta ValueError (mensagem para leigo) se algo não bate.
    """
    aviso_restaurar = ("Restaure uma cópia limpa do MATRIZ (reinstale ou "
                       "peça o arquivo original) e tente de novo.")
    try:
        wb = load_workbook(template_path)   # com fórmulas (sem data_only)
    except Exception as exc:
        raise ValueError(
            f"Não foi possível abrir o template '{template_path.name}' "
            f"({type(exc).__name__}). O arquivo pode estar corrompido. "
            f"{aviso_restaurar}"
        ) from exc
    try:
        ws = wb.active
        if ws is None:
            raise ValueError(
                f"O template '{template_path.name}' não tem planilha ativa. "
                f"{aviso_restaurar}")
        if (ws.max_column or 0) < _COLUNA_MAX_ESCRITA:
            raise ValueError(
                f"O template '{template_path.name}' tem colunas a menos que o "
                f"esperado (não alcança a coluna U de preço/marca). "
                f"{aviso_restaurar}")
        faltando = [c for c in ("B", "C", "D", "E", "F", "L", "T", "U")
                    if not str(ws[f"{c}{config.LINHA_CABECALHO}"].value
                               or "").strip()]
        if faltando:
            raise ValueError(
                f"O template '{template_path.name}' está com cabeçalhos vazios "
                f"nas colunas {', '.join(faltando)} (linha "
                f"{config.LINHA_CABECALHO}). Provável coluna movida ou apagada. "
                f"{aviso_restaurar}")
        for linha in (LINHA_PRIMEIRO_ITEM, LINHA_ULTIMO_ITEM_TEMPLATE):
            valor = ws[f"G{linha}"].value
            if not (isinstance(valor, str)
                    and re.search(r"S\d+", valor)
                    and re.search(r"\*\s*\d+(?:\.\d+)?", valor)):
                raise ValueError(
                    f"A fórmula de PREÇO VENDA da coluna G (linha {linha}) do "
                    f"template '{template_path.name}' não está no formato "
                    f"esperado (=S{linha}*markup). Sem ela o preço de venda "
                    f"sairia errado. {aviso_restaurar}")
    finally:
        wb.close()


def exportar(
    itens: Iterable[ItemExtraido],
    *,
    orgao: str | None = None,
    numero_pregao: str | None = None,
    markup: float | None = None,
    markups: dict[int, float] | None = None,
    itens_com_st: set[int] | None = None,
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
    # Falha cedo e com mensagem clara se o cliente moveu/apagou coluna ou
    # corrompeu o template — antes de copiar e preencher (P1.4).
    validar_template(template_path)

    itens = list(itens)
    if not itens:
        raise ValueError("Lista de itens vazia — nada para exportar.")

    markup_efetivo = markup if markup is not None else config.MARKUP_DEFAULT

    if destino is None:
        nome = _nome_default(orgao, numero_pregao)
        destino = config.EXPORTS_DIR / nome
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    # Escrita ATÔMICA: monta tudo num arquivo temporário e só "publica" no
    # destino final com um rename atômico no fim. Se algo falhar no meio do
    # preenchimento/save, o destino nunca aparece pela metade (e, se já existia
    # de uma exportação anterior, fica intacto). (P1.5)
    # Mantém o sufixo .xlsx no temporário — o openpyxl valida a extensão ao
    # abrir/salvar e recusaria um ".tmp".
    destino_tmp = destino.with_name(f"{destino.stem}.part{destino.suffix}")
    destino_tmp.unlink(missing_ok=True)   # remove lixo de uma falha anterior

    # Cópia binária do template — preserva tudo (estilos, fórmulas, mesclagens)
    shutil.copyfile(template_path, destino_tmp)

    wb = load_workbook(destino_tmp)   # SEM data_only — mantém fórmulas
    ws = wb.active

    # Preencher cabeçalho (C1, C2) — também texto livre, mesma neutralização
    if orgao:
        _escrever_texto(ws["C1"], orgao)
    if numero_pregao:
        _escrever_texto(ws["C2"], numero_pregao)

    # Markup GLOBAL do pregão (template-wide): comportamento legado — todas as
    # linhas G recebem o multiplicador. Aplicado ANTES de expandir, para as
    # linhas novas herdarem. O markup POR ITEM (Fase 4) sobrescreve linhas
    # específicas depois, no laço.
    if markup_efetivo != config.MARKUP_DEFAULT:
        _aplicar_markup(ws, markup_efetivo)

    # Expandir template se o edital tiver mais que 500 itens.
    n_itens = len(itens)
    if n_itens > 500:
        _expandir_linhas(ws, n_extras=n_itens - 500)

    # Cabeçalho da coluna indicadora de ST (AC4), com o visual dos vizinhos.
    itens_com_st = itens_com_st or set()
    _escrever_cabecalho_st(ws)

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

        # Markup POR ITEM (Fase 4) — sobrescreve a fórmula G SÓ desta linha
        # quando há (1) override explícito do operador na UI, ou (2) default
        # por classificação tributária (NCM com ST → markup menor). Sem isso, a
        # linha mantém o markup GLOBAL do pregão já aplicado acima.
        m_item = None
        if markups is not None and offset in markups:
            m_item = markups[offset]
        elif sel is not None and getattr(sel, "ncm", None):
            m_item = tributacao.markup_para(sel.ncm)
        if m_item is not None:
            _aplicar_markup_em_linha(ws, linha, m_item)

        # Coluna AC: foi gerado com Substituição Tributária? (pedido do cliente)
        ws.cell(row=linha, column=COL_ST_INDICADOR).value = (
            "Sim" if offset in itens_com_st else "Não")

    wb.save(destino_tmp)
    os.replace(destino_tmp, destino)   # rename atômico → publicação do destino
    return destino


def _escrever_cabecalho_st(ws) -> None:
    """Escreve o cabeçalho da coluna AC ("SUBST. TRIBUTÁRIA"), copiando o
    visual de um cabeçalho vizinho (U4) para a coluna parecer nativa. Falha de
    estilo NUNCA derruba a exportação — o que importa é o texto do cabeçalho."""
    import copy
    alvo = ws.cell(row=4, column=COL_ST_INDICADOR)
    alvo.value = CABECALHO_ST
    try:
        modelo = ws["U4"]   # 'MARCA' — último cabeçalho de texto do template
        alvo.font = copy.copy(modelo.font)
        alvo.fill = copy.copy(modelo.fill)
        alvo.border = copy.copy(modelo.border)
        alvo.alignment = copy.copy(modelo.alignment)
    except Exception:
        pass


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
    for linha in range(LINHA_PRIMEIRO_ITEM, LINHA_ULTIMO_ITEM_TEMPLATE + 1):
        _aplicar_markup_em_linha(ws, linha, markup)


def _aplicar_markup_em_linha(ws, linha: int, markup: float) -> None:
    """Reescreve o multiplicador do markup SÓ na fórmula G desta linha.

    Base do markup POR ITEM (Fase 4): cada linha pode ter um markup diferente
    (ST vs normal). As demais fórmulas (H, I, …) referenciam G e se ajustam.
    """
    celula = ws[f"G{linha}"]
    valor = celula.value
    if not isinstance(valor, str):
        return
    # Casa o "* <número>" que multiplica a referência Sn (ex.: *1.45, *1.5, *2).
    # O '*' obrigatório impede pegar os dígitos de "S504" (sem '*' antes).
    nova = re.sub(r"\*\s*\d+(?:\.\d+)?", f"*{_formatar_markup(markup)}",
                  valor, count=1)
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
