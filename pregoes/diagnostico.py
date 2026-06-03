"""
Diagnóstico instrumentado da busca de preços.

Esta camada é **ortogonal** ao pipeline — quando ativada (passando um
`LogDiagnostico` para `buscar_precos_para_itens`), o algoritmo de busca
NÃO MUDA. Apenas registra o que cada camada fez, para o operador
analisar offline em uma planilha XLSX.

Existe para calibrar o pipeline com base em evidência (frente B do
plano de 2026-05-27), em vez de adivinhar thresholds.

Estrutura:
    LogDiagnostico
      └─ LogItem (1 por item do edital)
           └─ LogItemFornecedor (1 por fornecedor)
                ├─ camada_0 / camada_4: o que match/aprendido sugeriu
                ├─ queries_textuais: cada query interna do scraper
                ├─ judge_chamado / judge_retorno
                └─ candidatos_finais: o que foi mostrado na UI

Exportação:
    `exportar_xlsx(log, destino)` produz planilha com 2 abas:
      - "Resumo": 1 linha/item, colunas-resumo + Avaliação (dropdown) + Observação
      - "Detalhe": 1 linha/candidato final (chegou na UI)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# === Dataclasses do log ===

@dataclass
class LogQueryInterna:
    """Uma tentativa de query DENTRO de `Almeida/DantasDistribuidor.buscar()`.

    O scraper textual tenta múltiplas variações (descrição normalizada,
    top-3 keywords, top-2 keywords). Esta dataclass registra o que cada
    tentativa achou.
    """
    nivel: str           # "exato" | "variacao"
    query: str
    n_brutos: int        # quantos cards o site devolveu
    n_passaram: int      # quantos passaram o threshold de relevância
    melhor_relevancia: float  # melhor score entre os brutos
    parou_aqui: bool     # True se o scraper parou nesta camada (achou relevantes)


@dataclass
class LogCandidatoFinal:
    """Um resultado que efetivamente foi pra UI do operador.

    Cada item tem N candidatos finais (a UI mostra todas marcas/preços).
    """
    descricao_encontrada: str
    marca: str
    preco: float
    codigo_fornecedor: str
    fornecedor: str
    confianca: str           # 'exato' | 'variacao'
    relevancia_vs_original: float
    veio_por_codigo: bool    # True = Camada 0 (catálogo) ou 4 (aprendido)
    aprovado_judge: bool     # True = Camada 3 aprovou


@dataclass
class LogItemFornecedor:
    """O que aconteceu para um par (item, fornecedor).

    Cada item passa pelas mesmas camadas em cada fornecedor; campos vazios
    significam que aquela camada não disparou.
    """
    fornecedor: str

    # Camada 0 — cache de descrição
    cache_hit: bool = False

    # Camada 4 — aprendizado
    aprendido_codigo: Optional[str] = None

    # Camada 0 — matcher fuzzy contra catálogo
    catalogo_produto_id: Optional[int] = None
    catalogo_score: Optional[float] = None
    catalogo_codigo: Optional[str] = None

    # Quando uma das anteriores forneceu código → busca direta por código
    veio_por_codigo: bool = False

    # Camadas 1+2 — busca textual
    queries_textuais: list[LogQueryInterna] = field(default_factory=list)
    caiu_fallback: bool = False     # nada passou threshold, foi pro top-3

    # Camada 3 — Gemini judge
    judge_chamado: bool = False
    judge_n_candidatos_entrada: int = 0
    judge_n_aprovados: int = 0

    # Resultado final desse fornecedor (vai pra UI)
    candidatos_finais: list[LogCandidatoFinal] = field(default_factory=list)


@dataclass
class LogItem:
    """O raio-x completo de um item do edital."""
    idx: int
    descricao_original: str
    descricao_norm: str
    keywords: list[str]
    por_fornecedor: list[LogItemFornecedor] = field(default_factory=list)

    @property
    def n_candidatos_ui(self) -> int:
        return sum(len(f.candidatos_finais) for f in self.por_fornecedor)

    @property
    def melhor_relevancia(self) -> float:
        m = 0.0
        for f in self.por_fornecedor:
            for c in f.candidatos_finais:
                if c.relevancia_vs_original > m:
                    m = c.relevancia_vs_original
        return m

    @property
    def status(self) -> str:
        """Verde / amarelo / vermelho / vazio — espelha a UI."""
        if self.n_candidatos_ui == 0:
            return "vazio"
        # Tem 'exato'? Verde
        for f in self.por_fornecedor:
            for c in f.candidatos_finais:
                if c.confianca == "exato":
                    return "verde"
        return "amarelo"

    @property
    def origem_resumida(self) -> str:
        """Texto curto descrevendo o caminho que resolveu (ou tentou)."""
        partes = []
        for f in self.por_fornecedor:
            tag = []
            if f.cache_hit:
                tag.append("cache")
            elif f.aprendido_codigo:
                tag.append(f"aprendido→{f.aprendido_codigo}")
            elif f.catalogo_codigo:
                tag.append(f"catalogo→{f.catalogo_codigo}")
            elif f.caiu_fallback:
                tag.append("fallback")
                if f.judge_chamado:
                    tag.append(f"judge({f.judge_n_aprovados}/{f.judge_n_candidatos_entrada})")
            else:
                tag.append("textual")
            partes.append(f"{f.fornecedor}={'+'.join(tag)}")
        return " | ".join(partes)


@dataclass
class LogDiagnostico:
    """Container para uma execução completa de busca.

    Mantido leve em memória — o exportador converte para XLSX só ao final.
    """
    arquivo_edital: Optional[str] = None
    fornecedores: list[str] = field(default_factory=list)
    iniciado_em: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    itens: list[LogItem] = field(default_factory=list)

    def get_item(self, idx: int, descricao: str,
                 desc_norm: str, keywords: list[str]) -> LogItem:
        """Recupera (ou cria) o log para um índice de item."""
        # Procura existente
        for it in self.itens:
            if it.idx == idx:
                return it
        novo = LogItem(
            idx=idx,
            descricao_original=descricao,
            descricao_norm=desc_norm,
            keywords=keywords,
        )
        self.itens.append(novo)
        return novo


# === Exportação XLSX ===

# Opções do dropdown da coluna "Avaliação". Mantenha estáveis — vamos
# agrupar pelos valores ao analisar.
_AVALIACAO_OPCOES = ["correto", "errado", "aceitavel"]


def exportar_xlsx(log: LogDiagnostico, destino: Path) -> Path:
    """Gera planilha XLSX com 2 abas (Resumo, Detalhe).

    A aba `Resumo` traz uma linha por item com colunas-resumo do que
    aconteceu, mais 2 colunas em branco: `Avaliacao` (dropdown
    correto/errado/aceitavel) e `Observacao` (texto livre).

    A aba `Detalhe` traz uma linha por candidato que chegou na UI.
    Útil pra entender casos específicos depois de marcar a Avaliação.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.utils import get_column_letter

    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws_res = wb.active
    ws_res.title = "Resumo"

    # === Aba Resumo ===
    cabecalho_res = [
        "Item", "Descrição (PDF)", "Status", "N° candidatos",
        "Melhor relevância", "Origem", "Marca default", "Preço default",
        "Fornecedor default", "Avaliação", "Observação",
    ]
    ws_res.append(cabecalho_res)
    _formatar_cabecalho(ws_res, len(cabecalho_res))

    for it in log.itens:
        # Pega a opção "default" (mais barata entre todos os fornecedores)
        todos = [c for f in it.por_fornecedor for c in f.candidatos_finais]
        default = min(todos, key=lambda c: c.preco) if todos else None

        ws_res.append([
            it.idx + 1,
            it.descricao_original[:200],
            it.status,
            it.n_candidatos_ui,
            round(it.melhor_relevancia, 3),
            it.origem_resumida,
            default.marca if default else "",
            default.preco if default else None,
            default.fornecedor if default else "",
            "",   # Avaliação
            "",   # Observação
        ])

    # Dropdown na coluna Avaliação
    col_avaliacao = get_column_letter(cabecalho_res.index("Avaliação") + 1)
    dv = DataValidation(
        type="list",
        formula1=f'"{",".join(_AVALIACAO_OPCOES)}"',
        allow_blank=True,
    )
    dv.add(f"{col_avaliacao}2:{col_avaliacao}{1 + len(log.itens)}")
    ws_res.add_data_validation(dv)

    # Larguras decentes
    _ajustar_larguras(ws_res, {
        1: 6, 2: 80, 3: 10, 4: 14, 5: 18, 6: 35,
        7: 18, 8: 12, 9: 14, 10: 14, 11: 50,
    })

    # === Aba Detalhe ===
    ws_det = wb.create_sheet("Detalhe")
    cabecalho_det = [
        "Item", "Descrição (PDF)", "Fornecedor", "Descrição encontrada",
        "Marca", "Preço", "Código", "Confiança", "Relevância vs original",
        "Veio por código?", "Aprovado pelo judge?",
    ]
    ws_det.append(cabecalho_det)
    _formatar_cabecalho(ws_det, len(cabecalho_det))

    for it in log.itens:
        for f in it.por_fornecedor:
            for c in f.candidatos_finais:
                ws_det.append([
                    it.idx + 1,
                    it.descricao_original[:200],
                    f.fornecedor,
                    c.descricao_encontrada[:200],
                    c.marca,
                    c.preco,
                    c.codigo_fornecedor,
                    c.confianca,
                    round(c.relevancia_vs_original, 3),
                    "sim" if c.veio_por_codigo else "não",
                    "sim" if c.aprovado_judge else "não",
                ])

    _ajustar_larguras(ws_det, {
        1: 6, 2: 50, 3: 12, 4: 60, 5: 18, 6: 10,
        7: 12, 8: 12, 9: 22, 10: 16, 11: 22,
    })

    # === Aba Camadas ===
    # Visão por (item, fornecedor) das queries internas e camadas que
    # dispararam. Não está em Resumo nem Detalhe porque a granularidade
    # é diferente — uma linha por query interna.
    ws_cam = wb.create_sheet("Queries internas")
    cabecalho_cam = [
        "Item", "Descrição (PDF)", "Fornecedor", "Nível", "Query",
        "N° brutos", "N° passaram", "Melhor relevância", "Parou aqui?",
    ]
    ws_cam.append(cabecalho_cam)
    _formatar_cabecalho(ws_cam, len(cabecalho_cam))

    for it in log.itens:
        for f in it.por_fornecedor:
            for q in f.queries_textuais:
                ws_cam.append([
                    it.idx + 1,
                    it.descricao_original[:120],
                    f.fornecedor,
                    q.nivel,
                    q.query,
                    q.n_brutos,
                    q.n_passaram,
                    round(q.melhor_relevancia, 3),
                    "sim" if q.parou_aqui else "não",
                ])

    _ajustar_larguras(ws_cam, {
        1: 6, 2: 45, 3: 12, 4: 12, 5: 50,
        6: 10, 7: 12, 8: 18, 9: 12,
    })

    # === Aba Meta ===
    ws_meta = wb.create_sheet("Meta")
    ws_meta.append(["Campo", "Valor"])
    _formatar_cabecalho(ws_meta, 2)
    ws_meta.append(["Edital", log.arquivo_edital or ""])
    ws_meta.append(["Iniciado em", log.iniciado_em])
    ws_meta.append(["Fornecedores", ", ".join(log.fornecedores)])
    ws_meta.append(["Itens no edital", len(log.itens)])
    ws_meta.append(["Total candidatos UI",
                    sum(it.n_candidatos_ui for it in log.itens)])
    ws_meta.append(["Itens verdes",
                    sum(1 for it in log.itens if it.status == "verde")])
    ws_meta.append(["Itens amarelos",
                    sum(1 for it in log.itens if it.status == "amarelo")])
    ws_meta.append(["Itens vazios",
                    sum(1 for it in log.itens if it.status == "vazio")])
    _ajustar_larguras(ws_meta, {1: 24, 2: 60})

    wb.save(destino)
    return destino


def _formatar_cabecalho(ws, n_cols: int) -> None:
    from openpyxl.styles import Font, PatternFill, Alignment
    fill = PatternFill("solid", fgColor="222B45")
    font = Font(bold=True, color="FFFFFF")
    align = Alignment(vertical="center", wrap_text=True)
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = fill
        cell.font = font
        cell.alignment = align
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"


def _ajustar_larguras(ws, larguras: dict[int, int]) -> None:
    from openpyxl.utils import get_column_letter
    for col_idx, w in larguras.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = w
