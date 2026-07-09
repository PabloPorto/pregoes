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

import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell, ILLEGAL_CHARACTERS_RE
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
    # Fornecedor da opção escolhida (almeida/dantas). Gravado direto na seleção
    # para o aprendizado saber a origem SEM re-casar por (preço,código,marca) —
    # o re-match falhava em silêncio (float/espaço) e descartava o aprendizado
    # (achado QA 2026-06). Usado também pelo desconto Dantas (ver eh_dantas).
    fornecedor: str | None = None


def eh_dantas(sel: "SelecaoFornecedor") -> bool:
    """True se a seleção é do fornecedor Dantas — que recebe o desconto
    comercial de 17% no custo (config.DESCONTO_DANTAS)."""
    return (getattr(sel, "fornecedor", None) or "").strip().lower() == "dantas"


def _preco_com_desconto(sel: "SelecaoFornecedor"):
    """Preço base para a coluna L: o custo trazido do site, JÁ com o desconto
    Dantas quando aplicável. Preço ausente/0/não-finito passa intacto (célula
    vazia = sem preço). Arredonda a 2 casas (centavos) — ex.: 5,00 → 4,15."""
    preco = sel.preco
    if (eh_dantas(sel) and isinstance(preco, (int, float))
            and math.isfinite(preco) and preco > 0):
        return round(preco * (1 - config.DESCONTO_DANTAS), 2)
    return preco


# Faixa de linhas do template do contador (2026-07): cabeçalho L2, dados L3..600
# (598 itens), rótulos de total L603, fórmulas de total L604 (=SUM(I3:I600),
# =G604/C604, =SUM(K3:K600)). Antes era L5..504 + totais 505/507.
LINHA_PRIMEIRO_ITEM = config.LINHA_DADOS_INICIO        # 3
LINHA_ULTIMO_ITEM_TEMPLATE = 600                       # template comporta 598 itens (3..600)
LINHA_CABECALHO_TOTAL = 603                            # 'FATURAMENTO', 'MARGEM', 'LUCRO'
LINHA_TOTAL_FINAL = 604                                # =SUM(I3:I600), =G604/C604, =SUM(K3:K600)

# Coluna AC (29): flag NATIVA "SUBST. TRIBUTÁRIA" do modelo do contador. O app
# grava o texto TOKEN_ST ('SIM') nos itens com ST (por NCM) e DEIXA EM BRANCO os
# demais — a própria planilha lê essa flag (IF(AC="SIM",...)) para zerar o
# imposto estadual e ajustar o gross-up de preço. NÃO escrevemos o cabeçalho
# (ele é nativo em AC2) nem inventamos a coluna — só preenchemos a flag.
COL_ST = 29                                            # AC
TOKEN_ST = "SIM"                                       # valor exato que a planilha compara

# Coluna (1-based) mais à direita que o exportador escreve: AC = 29 (a flag de
# ST). Define a largura mínima que o template precisa ter.
_COLUNA_MAX_ESCRITA = 29


def validar_template(template_path: Path) -> None:
    """Confere que o template tem a estrutura que o exportador assume.

    PORQUÊ: o cliente abre o MATRIZ no Excel e pode, sem querer, mover uma
    coluna, apagar uma fórmula ou salvar por cima. Sem esta checagem, o
    exportador escreveria descrição/preço na célula errada ou deixaria de
    aplicar o markup — e a planilha entregue ao pregoeiro sairia corrompida
    EM SILÊNCIO (erro financeiro). Aqui falhamos cedo, com mensagem clara em
    português, ANTES de copiar o template e começar a preencher.

    Invariantes (modelo do contador, 2026-07 — ver AC2=`SUBST. TRIBUTÁRIA` e o
    gross-up em Z3):
    - existe uma aba ativa que alcança ao menos a coluna AC (flag de ST);
    - os cabeçalhos-âncora da linha 2 (B,C,D,E,F,L,T,U e AC) não estão vazios;
    - a coluna AC é a flag nativa de Substituição Tributária;
    - o motor de preço está vivo: a fórmula de gross-up da coluna Z, na primeira
      linha de dados, referencia AC (`IF(...AC...="SIM"...)`). Se esse padrão
      sumiu, o preço sugerido sairia errado.

    NÃO exigimos mais fórmula em G: no modelo novo o PREÇO VENDA é manual.

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
                f"esperado (não alcança a coluna AC de Substituição Tributária). "
                f"{aviso_restaurar}")
        cab = config.LINHA_CABECALHO
        faltando = [c for c in ("B", "C", "D", "E", "F", "L", "T", "U", "AC")
                    if not str(ws[f"{c}{cab}"].value or "").strip()]
        if faltando:
            raise ValueError(
                f"O template '{template_path.name}' está com cabeçalhos vazios "
                f"nas colunas {', '.join(faltando)} (linha {cab}). Provável "
                f"coluna movida ou apagada. {aviso_restaurar}")
        if "TRIBUT" not in str(ws[f"AC{cab}"].value or "").upper():
            raise ValueError(
                f"A coluna AC do template '{template_path.name}' (linha {cab}) "
                f"não é a flag de Substituição Tributária esperada. "
                f"{aviso_restaurar}")
        z = ws[f"Z{LINHA_PRIMEIRO_ITEM}"].value
        if not (isinstance(z, str) and z.startswith("=") and "AC" in z):
            raise ValueError(
                f"A fórmula de preço (coluna Z, linha {LINHA_PRIMEIRO_ITEM}) do "
                f"template '{template_path.name}' não está no formato esperado "
                f"(gross-up que lê a flag AC). Sem ela o preço sugerido sairia "
                f"errado. {aviso_restaurar}")
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

    Modelo do contador (2026-07): o app preenche os INSUMOS (item, descrição,
    unidade, quantidade, valor de referência, custo em L, código, marca) e marca
    'SIM' na coluna AC dos itens com ST — a própria planilha calcula custo,
    imposto estadual e os 3 cenários de preço (Z/AA/AB). O PREÇO VENDA (G) é
    MANUAL: fica em branco para o operador escolher.

    Args:
        itens: lista de ItemExtraido vindos do extrator
        orgao/numero_pregao: identificam o pregão no NOME do arquivo gerado. O
            modelo novo não tem célula de órgão/pregão no topo, então não são
            escritos na planilha.
        markup/markups: DEPRECADOS no modelo novo — o preço não é mais
            `=S*markup` (G é manual). Aceitos por compatibilidade da chamada,
            mas não afetam o preço. A condição tributária vai por `itens_com_st`.
        itens_com_st: índices (offset na lista) dos itens com Substituição
            Tributária → o app grava 'SIM' na coluna AC deles. Complementado por
            `sel.ncm` quando disponível (tributacao.eh_st).
        selecoes: mapa `{offset: SelecaoFornecedor}` com a escolha do operador —
            preenche L (custo), T (código), U (marca). Itens ausentes ficam sem
            preço (o operador completa no Excel).
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

    # Órgão/número NÃO vão na planilha (o modelo do contador não tem esse campo
    # no topo) — a identificação do pregão fica no NOME do arquivo.

    # Expandir só se o edital passar da capacidade nativa (598 itens: 3..600).
    n_itens = len(itens)
    capacidade = LINHA_ULTIMO_ITEM_TEMPLATE - LINHA_PRIMEIRO_ITEM + 1
    if n_itens > capacidade:
        _expandir_linhas(ws, n_extras=n_itens - capacidade)

    itens_com_st = itens_com_st or set()

    # Preencher itens
    selecoes = selecoes or {}
    for offset, item in enumerate(itens):
        linha = LINHA_PRIMEIRO_ITEM + offset
        ws[f"B{linha}"] = item.item_numero if item.item_numero is not None else (offset + 1)
        # C, D, T, U recebem TEXTO LIVRE (descrição do edital, unidade,
        # código/marca raspados). Neutralizamos prefixos de fórmula para o
        # Excel não interpretar o conteúdo como cálculo (ver _escrever_texto).
        _escrever_texto(ws[f"C{linha}"], item.descricao)
        _escrever_texto(ws[f"D{linha}"], item.unidade or "")
        # Quantidade/valor: nan/inf gravavam célula numérica VAZIA em silêncio
        # (totais subnotificados) — tratamos como ausente (adversarial 2026-06).
        qtd = item.quantidade
        ws[f"E{linha}"] = qtd if (isinstance(qtd, (int, float))
                                  and math.isfinite(qtd)) else 0
        vref = item.valor_referencia
        if isinstance(vref, (int, float)) and math.isfinite(vref):
            ws[f"F{linha}"] = vref

        # Colunas vindas do scraper (Fase 2): L=preço (número), T=código, U=marca.
        # T e U são texto raspado do site do fornecedor → mesma neutralização.
        sel = selecoes.get(offset)
        if sel is not None:
            # Desconto Dantas (acordo comercial): o custo trazido do site da
            # Dantas entra com 17% off na coluna L. O markup das fórmulas incide
            # sobre esse custo já descontado — oferta mais competitiva, margem
            # preservada. Almeida/catálogo não recebem desconto.
            ws[f"L{linha}"] = _preco_com_desconto(sel)
            # Marca "D" no código dos itens da DANTAS para o operador identificar
            # a origem direto na planilha (pedido do cliente 2026-06-27). Almeida
            # fica como veio. Mantém a convenção do importador (sufixo D = Dantas),
            # então a retroalimentação continua atribuindo a origem certa.
            codigo = sel.codigo_fornecedor
            if codigo and eh_dantas(sel):
                codigo = f"{codigo}D"
            _escrever_texto(ws[f"T{linha}"], codigo)
            _escrever_texto(ws[f"U{linha}"], sel.marca)

        # Substituição Tributária → flag NATIVA da coluna AC (modelo do contador).
        # Item é ST se veio marcado (itens_com_st) OU se o NCM da opção escolhida
        # consta no Anexo 05 (tributacao.eh_st). Gravamos o token 'SIM' que a
        # planilha compara; item NÃO-ST fica em BRANCO (a fórmula IF(AC="SIM",...)
        # cai no ramo sem-ST). NÃO trocamos mais fórmula de G — o preço é manual.
        eh_st_item = offset in itens_com_st or (
            sel is not None and getattr(sel, "ncm", None)
            and tributacao.eh_st(sel.ncm))
        if eh_st_item:
            ws.cell(row=linha, column=COL_ST).value = TOKEN_ST

    wb.save(destino_tmp)
    os.replace(destino_tmp, destino)   # rename atômico → publicação do destino
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

    Não usar em células numéricas nem de fórmula do template: aqui só passam
    C, D, T, U (descrição/unidade/código/marca), todos texto.
    """
    if isinstance(texto, str):
        # O pdfplumber emite caracteres de controle (form-feed em quebra de
        # página) que o openpyxl REJEITA com IllegalCharacterError — 1 item
        # ruim derrubava a planilha inteira (adversarial 2026-06). Removemos
        # os ilegais antes de gravar, preservando a neutralização de fórmula.
        texto = ILLEGAL_CHARACTERS_RE.sub("", texto)
    if isinstance(texto, str) and texto[:1] in _PREFIXOS_PERIGOSOS:
        celula.value = "'" + texto
    else:
        celula.value = texto


def _expandir_linhas(ws, n_extras: int):
    """
    Insere N linhas extras antes do bloco de totais, copiando as fórmulas da
    última linha de itens do template (600) com translação de referências
    relativas (o Translator mantém $V$2 absoluto e ajusta L600→L<nova>).
    """
    linha_modelo = LINHA_ULTIMO_ITEM_TEMPLATE            # 600

    # Inserir linhas em branco (openpyxl empurra os totais para baixo junto).
    ws.insert_rows(idx=linha_modelo + 1, amount=n_extras)

    for nova_idx in range(linha_modelo + 1, linha_modelo + 1 + n_extras):
        for col in range(1, ws.max_column + 1):
            valor_modelo = ws.cell(row=linha_modelo, column=col).value
            if isinstance(valor_modelo, str) and valor_modelo.startswith("="):
                origem = f"{get_column_letter(col)}{linha_modelo}"
                t = Translator(valor_modelo, origin=origem)
                destino_ref = f"{get_column_letter(col)}{nova_idx}"
                ws.cell(row=nova_idx, column=col).value = \
                    t.translate_formula(destino_ref)

    _ajustar_totais(ws, n_extras)


def _ajustar_totais(ws, n_extras: int):
    """Estende os SUM do rodapé para a nova última linha de itens.

    Totais do modelo novo (após correção do arquivo-base): C=SUM(I3:I600),
    G=SUM(K3:K600), D=G/C. Ao inserir linhas, a linha de total desce n_extras e
    o limite 600 vira 600+n_extras. Reescreve os ranges ``I3:I600``/``K3:K600``
    e as auto-referências da linha de total."""
    nova_ultima_item = LINHA_ULTIMO_ITEM_TEMPLATE + n_extras     # 600 + extras
    nova_total_final = LINHA_TOTAL_FINAL + n_extras              # 604 + extras
    for col in range(1, ws.max_column + 1):
        v = ws.cell(row=nova_total_final, column=col).value
        if isinstance(v, str) and v.startswith("="):
            atualizada = (
                v.replace(f":I{LINHA_ULTIMO_ITEM_TEMPLATE}", f":I{nova_ultima_item}")
                 .replace(f":K{LINHA_ULTIMO_ITEM_TEMPLATE}", f":K{nova_ultima_item}")
                 .replace(f"G{LINHA_TOTAL_FINAL}", f"G{nova_total_final}")
                 .replace(f"C{LINHA_TOTAL_FINAL}", f"C{nova_total_final}"))
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
