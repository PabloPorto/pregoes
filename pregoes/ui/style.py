"""
Sistema visual da UI — CSS único + componentes de alto nível.

Tudo que é "como o app se parece" mora aqui. Páginas em ``app.py`` chamam
helpers desta camada em vez de empilhar ``st.markdown``/``st.success``
direto. Mantém a UI consistente sem propagar HTML pelas telas.

Princípio: o Streamlit cuida dos widgets (button, input, table). Aqui só
ajustamos espaçamento global e damos componentes que o Streamlit não tem
nativos (badge, kpi-card, header de página, bloco de avisos compactado).
"""
from __future__ import annotations

from typing import Iterable, Literal, Optional

import streamlit as st


# === Paleta — fonte única de cor. Não duplicar valores em outros lugares ===
PALETA = {
    "primary":        "#1E40AF",
    "primary_hover":  "#1E3A8A",
    "primary_light":  "#DBEAFE",
    "bg":             "#F8FAFC",
    "surface":        "#FFFFFF",
    "border":         "#E2E8F0",
    "border_strong":  "#CBD5E1",
    "text":           "#0F172A",
    "text_secondary": "#475569",
    "text_muted":     "#94A3B8",
    "success":        "#15803D",
    "success_bg":     "#DCFCE7",
    "warning":        "#B45309",
    "warning_bg":     "#FEF3C7",
    "danger":         "#B91C1C",
    "danger_bg":      "#FEE2E2",
    "info":           "#1E40AF",
    "info_bg":        "#DBEAFE",
}

TipoBadge = Literal["success", "warning", "danger", "info", "neutro"]
TipoAviso = Literal["success", "warning", "danger", "info"]


_CSS = f"""
<style>
/* ===== Layout global ===== */
.stApp {{
    background-color: {PALETA['bg']};
}}

.main .block-container {{
    padding-top: 1.5rem;
    padding-bottom: 3rem;
    max-width: 1280px;
}}

/* Esconde o cabeçalho/menu do Streamlit (deploy, share, etc.) — interface
   é uso interno do operador, não precisa daquela barra. */
header[data-testid="stHeader"] {{
    background: transparent;
    height: 0;
}}
#MainMenu, footer {{ visibility: hidden; }}

/* ===== Tipografia ===== */
html, body, [class*="css"] {{
    font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    color: {PALETA['text']};
}}

h1 {{ font-weight: 700; font-size: 1.75rem; letter-spacing: -0.02em; }}
h2 {{ font-weight: 600; font-size: 1.25rem; letter-spacing: -0.01em; }}
h3 {{ font-weight: 600; font-size: 1.05rem; }}

/* ===== Sidebar ===== */
section[data-testid="stSidebar"] {{
    background-color: {PALETA['surface']};
    border-right: 1px solid {PALETA['border']};
}}
section[data-testid="stSidebar"] .block-container {{
    padding-top: 1.5rem;
}}

/* ===== Botões ===== */
.stButton > button, .stDownloadButton > button {{
    border-radius: 8px;
    font-weight: 500;
    transition: all 120ms ease;
    border: 1px solid {PALETA['border_strong']};
    background: {PALETA['surface']};
    color: {PALETA['text']};
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
    border-color: {PALETA['primary']};
    color: {PALETA['primary']};
}}
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {{
    background: {PALETA['primary']};
    border-color: {PALETA['primary']};
    color: white;
}}
.stButton > button[kind="primary"]:hover, .stDownloadButton > button[kind="primary"]:hover {{
    background: {PALETA['primary_hover']};
    border-color: {PALETA['primary_hover']};
    color: white;
}}

/* ===== Inputs ===== */
.stTextInput input, .stNumberInput input, .stTextArea textarea {{
    border-radius: 8px;
    border: 1px solid {PALETA['border_strong']};
}}
.stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus {{
    border-color: {PALETA['primary']};
    box-shadow: 0 0 0 3px {PALETA['primary_light']};
}}

/* ===== File uploader: mais convidativo ===== */
[data-testid="stFileUploaderDropzone"] {{
    border: 2px dashed {PALETA['border_strong']};
    border-radius: 12px;
    background: {PALETA['surface']};
    padding: 1.5rem;
}}
[data-testid="stFileUploaderDropzone"]:hover {{
    border-color: {PALETA['primary']};
    background: {PALETA['primary_light']}33;
}}

/* ===== Containers e expanders ===== */
[data-testid="stExpander"] {{
    border: 1px solid {PALETA['border']};
    border-radius: 10px;
    background: {PALETA['surface']};
}}
[data-testid="stExpander"] details > summary {{
    padding: 0.75rem 1rem;
    font-weight: 500;
}}

/* ===== Tabelas / data_editor ===== */
[data-testid="stDataFrame"], [data-testid="stDataFrameContainer"] {{
    border: 1px solid {PALETA['border']};
    border-radius: 10px;
    overflow: hidden;
}}

/* ===== Métricas: limpar a aparência crua do st.metric ===== */
[data-testid="stMetric"] {{
    background: {PALETA['surface']};
    border: 1px solid {PALETA['border']};
    border-radius: 10px;
    padding: 1rem 1.25rem;
}}
[data-testid="stMetricLabel"] {{
    color: {PALETA['text_secondary']};
    font-weight: 500;
    font-size: 0.85rem;
}}
[data-testid="stMetricValue"] {{
    color: {PALETA['text']};
    font-weight: 700;
    font-size: 1.75rem;
}}

/* ===== Componentes custom (badge, card, header, aviso) ===== */
.pp-header {{
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid {PALETA['border']};
}}
.pp-header h1 {{ margin: 0 0 0.25rem 0; }}
.pp-header .pp-sub {{
    color: {PALETA['text_secondary']};
    font-size: 0.95rem;
}}

.pp-badge {{
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.2rem 0.65rem;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 600;
    border: 1px solid transparent;
    line-height: 1.4;
}}
.pp-badge--success {{ background: {PALETA['success_bg']}; color: {PALETA['success']}; }}
.pp-badge--warning {{ background: {PALETA['warning_bg']}; color: {PALETA['warning']}; }}
.pp-badge--danger  {{ background: {PALETA['danger_bg']};  color: {PALETA['danger']};  }}
.pp-badge--info    {{ background: {PALETA['info_bg']};    color: {PALETA['info']};    }}
.pp-badge--neutro  {{ background: #F1F5F9; color: {PALETA['text_secondary']}; }}

.pp-aviso {{
    display: flex;
    gap: 0.75rem;
    padding: 0.9rem 1rem;
    border-radius: 10px;
    border: 1px solid;
    margin-bottom: 0.75rem;
    line-height: 1.5;
}}
.pp-aviso .pp-aviso-ico {{ font-size: 1.1rem; flex-shrink: 0; }}
.pp-aviso--success {{ background: {PALETA['success_bg']}; border-color: {PALETA['success']}33; color: #14532D; }}
.pp-aviso--warning {{ background: {PALETA['warning_bg']}; border-color: {PALETA['warning']}33; color: #78350F; }}
.pp-aviso--danger  {{ background: {PALETA['danger_bg']};  border-color: {PALETA['danger']}33;  color: #7F1D1D; }}
.pp-aviso--info    {{ background: {PALETA['info_bg']};    border-color: {PALETA['info']}33;    color: #1E3A8A; }}

.pp-card {{
    background: {PALETA['surface']};
    border: 1px solid {PALETA['border']};
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1rem;
}}
.pp-card-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.5rem;
}}
.pp-card-titulo {{
    font-weight: 600;
    color: {PALETA['text']};
}}
.pp-card-hint {{
    color: {PALETA['text_secondary']};
    font-size: 0.85rem;
}}

.pp-side-status {{
    padding: 0.75rem 0.9rem;
    border-radius: 10px;
    background: {PALETA['bg']};
    border: 1px solid {PALETA['border']};
    margin-top: 0.75rem;
    font-size: 0.85rem;
}}
.pp-side-status .pp-side-status-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
}}
.pp-side-status .pp-side-status-row + .pp-side-status-row {{
    margin-top: 0.35rem;
}}
.pp-side-label {{ color: {PALETA['text_secondary']}; }}

.pp-brand {{
    display: flex;
    align-items: center;
    gap: 0.6rem;
    padding-bottom: 1rem;
    margin-bottom: 0.75rem;
    border-bottom: 1px solid {PALETA['border']};
}}
.pp-brand-logo {{
    width: 36px; height: 36px;
    border-radius: 9px;
    background: linear-gradient(135deg, {PALETA['primary']}, {PALETA['primary_hover']});
    color: white;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 1rem;
}}
.pp-brand-text {{ line-height: 1.2; }}
.pp-brand-text .pp-brand-name {{ font-weight: 700; font-size: 1.05rem; }}
.pp-brand-text .pp-brand-tagline {{
    color: {PALETA['text_secondary']};
    font-size: 0.78rem;
}}

/* ===== Painel de progresso da busca de preços ===== */
.pp-busca-painel {{
    background: {PALETA['surface']};
    border: 1px solid {PALETA['border']};
    border-radius: 14px;
    padding: 1.5rem 1.75rem;
    margin: 0.5rem 0 1rem 0;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}}
.pp-busca-cab {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1rem;
    margin-bottom: 1rem;
}}
.pp-busca-titulo {{
    font-size: 1.1rem;
    font-weight: 600;
    color: {PALETA['text']};
    line-height: 1.3;
}}
.pp-busca-sub {{
    color: {PALETA['text_secondary']};
    font-size: 0.85rem;
    margin-top: 0.15rem;
}}
.pp-busca-tempo {{
    color: {PALETA['text_secondary']};
    font-variant-numeric: tabular-nums;
    font-size: 0.9rem;
    background: {PALETA['bg']};
    padding: 0.35rem 0.7rem;
    border-radius: 8px;
    border: 1px solid {PALETA['border']};
    white-space: nowrap;
}}
.pp-busca-barra {{
    height: 8px;
    background: {PALETA['bg']};
    border-radius: 999px;
    overflow: hidden;
    margin: 0.25rem 0 1rem 0;
    border: 1px solid {PALETA['border']};
}}
.pp-busca-barra-fill {{
    height: 100%;
    background: linear-gradient(90deg, {PALETA['primary']}, {PALETA['primary_hover']});
    transition: width 240ms ease-out;
    border-radius: 999px;
}}
.pp-busca-counter {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 0.6rem;
}}
.pp-busca-item-label {{
    color: {PALETA['text_secondary']};
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}}
.pp-busca-pct {{
    font-size: 1.5rem;
    font-weight: 700;
    color: {PALETA['primary']};
    font-variant-numeric: tabular-nums;
}}
.pp-busca-item-desc {{
    background: {PALETA['bg']};
    border: 1px solid {PALETA['border']};
    border-left: 3px solid {PALETA['primary']};
    border-radius: 8px;
    padding: 0.85rem 1rem;
    margin-bottom: 1rem;
    color: {PALETA['text']};
    font-size: 0.98rem;
    line-height: 1.45;
    word-break: break-word;
    min-height: 3rem;
    display: flex;
    align-items: center;
}}
.pp-busca-item-desc.is-idle {{
    color: {PALETA['text_secondary']};
    font-style: italic;
    border-left-color: {PALETA['border_strong']};
}}
.pp-busca-stats {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1rem;
    padding-top: 1rem;
    border-top: 1px solid {PALETA['border']};
}}
.pp-busca-stat {{
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
}}
.pp-busca-stat-nome {{
    font-weight: 600;
    color: {PALETA['text']};
    font-size: 0.85rem;
}}

/* Sumário pré-busca: cards informativos lado a lado */
.pp-resumo-pre {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 0.75rem;
    margin: 0.5rem 0 1rem 0;
}}
.pp-resumo-item {{
    background: {PALETA['surface']};
    border: 1px solid {PALETA['border']};
    border-radius: 10px;
    padding: 0.85rem 1rem;
}}
.pp-resumo-item-label {{
    color: {PALETA['text_secondary']};
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
    margin-bottom: 0.2rem;
}}
.pp-resumo-item-valor {{
    color: {PALETA['text']};
    font-size: 1.1rem;
    font-weight: 600;
}}

/* ===== Cards de opção (tela "Buscar preços") ===== */
/* Card escolhido pelo operador: leve tom da cor primária + acento à esquerda,
   para destacar ao bater o olho qual opção já está marcada. O Streamlit aplica
   a classe `st-key-<key>` direto no container com borda (o stVerticalBlock);
   só as opções selecionadas usam o key `ppoptsel-*`. (`st-key-ppopt-*` das
   demais não casa este seletor — "ppoptsel-" não é substring de "ppopt-".) */
div[class*="st-key-ppoptsel-"] {{
    background: {PALETA['primary_light']}66 !important;
    border-color: {PALETA['primary']} !important;
    box-shadow: inset 4px 0 0 {PALETA['primary']};
}}

/* Pílulas de destaque — substituem os emoji (estrela/cifrão), que renderizavam
   diferente entre Windows e Mac, por rótulos de estilo consistente. */
.pp-pill {{
    display: inline-block;
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    margin-left: 0.5rem;
    vertical-align: middle;
    line-height: 1.6;
    white-space: nowrap;
}}
.pp-pill--match  {{ background: {PALETA['success_bg']}; color: {PALETA['success']}; }}
.pp-pill--barato {{ background: {PALETA['info_bg']};    color: {PALETA['info']};    }}
.pp-pill--catalogo {{ background: #EDE9FE; color: #6D28A9; }}
</style>
"""


# === Helpers ===

def aplicar_estilo() -> None:
    """Injeta o CSS único. Chamar uma vez no topo do ``app.py``."""
    st.markdown(_CSS, unsafe_allow_html=True)


def header_pagina(titulo: str, subtitulo: str | None = None) -> None:
    """Cabeçalho consistente para topo de cada tela."""
    sub = f'<div class="pp-sub">{subtitulo}</div>' if subtitulo else ""
    st.markdown(
        f'<div class="pp-header"><h1>{titulo}</h1>{sub}</div>',
        unsafe_allow_html=True,
    )


def badge(texto: str, tipo: TipoBadge = "neutro") -> str:
    """Retorna HTML de uma badge — útil para embutir em outras renderizações.
    Use ``st.markdown(badge(...), unsafe_allow_html=True)`` ou interpole."""
    return f'<span class="pp-badge pp-badge--{tipo}">{texto}</span>'


def render_badge(texto: str, tipo: TipoBadge = "neutro") -> None:
    """Renderiza badge diretamente como bloco markdown."""
    st.markdown(badge(texto, tipo), unsafe_allow_html=True)


def pill(texto: str, tipo: Literal["match", "barato", "catalogo"]) -> str:
    """Pílula de destaque inline (ex.: 'Melhor match', 'Mais barato').

    Renderiza igual em qualquer SO — substitui os emoji ⭐/💰, que tinham
    aparência diferente entre Windows e Mac. Para embutir em outra string
    HTML: ``f"...{pill('Melhor match', 'match')}..."``.
    """
    return f'<span class="pp-pill pp-pill--{tipo}">{texto}</span>'


def bloco_aviso(mensagem: str, tipo: TipoAviso = "info") -> None:
    """
    Substitui ``st.success/warning/error/info`` com visual consistente.

    Usa um único componente — para múltiplos avisos, chamar repetidamente
    (ou agrupar via ``bloco_avisos`` quando vier uma lista).
    """
    icones = {"success": "✓", "warning": "⚠", "danger": "✕", "info": "ℹ"}
    ico = icones.get(tipo, "ℹ")
    st.markdown(
        f'<div class="pp-aviso pp-aviso--{tipo}">'
        f'<span class="pp-aviso-ico">{ico}</span>'
        f'<span>{mensagem}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def bloco_avisos(mensagens: Iterable[str], tipo: TipoAviso = "warning") -> None:
    """Renderiza uma sequência de avisos do mesmo tipo, sem empilhar boxes."""
    for m in mensagens:
        if m:
            bloco_aviso(m, tipo)


def card_inicio(titulo: str | None = None, hint: str | None = None) -> None:
    """Abre um card visual. Combine com ``card_fim()``.

    Uso típico:
        card_inicio("Credenciais Almeida", "Salvas localmente")
        st.text_input(...)
        card_fim()
    """
    cab = ""
    if titulo or hint:
        partes = []
        if titulo:
            partes.append(f'<span class="pp-card-titulo">{titulo}</span>')
        if hint:
            partes.append(f'<span class="pp-card-hint">{hint}</span>')
        cab = f'<div class="pp-card-header">{"".join(partes)}</div>'
    st.markdown(f'<div class="pp-card">{cab}', unsafe_allow_html=True)


def card_fim() -> None:
    st.markdown("</div>", unsafe_allow_html=True)


def confianca_para_badge(conf: float) -> tuple[str, TipoBadge]:
    """
    Mapeia confiança de extração [0,1] para par (rótulo, tipo de badge).
    Espelha o semáforo já existente: ≥0.95 verde, ≥0.80 amarelo, <0.80 vermelho.
    """
    if conf >= 0.95:
        return (f"Alta confiança · {conf:.0%}", "success")
    if conf >= 0.80:
        return (f"Confiança média · {conf:.0%}", "warning")
    if conf > 0:
        return (f"Baixa confiança · {conf:.0%}", "danger")
    return ("Sem itens detectados", "danger")


def sidebar_marca() -> None:
    """Logo + nome do app no topo da sidebar."""
    st.markdown(
        '<div class="pp-brand">'
        '<div class="pp-brand-logo">P</div>'
        '<div class="pp-brand-text">'
        '<div class="pp-brand-name">Pregões</div>'
        '<div class="pp-brand-tagline">Automação de formação de preços</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def sidebar_status(itens: list[tuple[str, str, TipoBadge]]) -> None:
    """
    Painel de status da sidebar (credenciais, ambiente).

    ``itens`` = lista de tuplas (rótulo, valor, tipo_de_badge).
    Renderiza como uma caixa única — não como múltiplos ``st.write``.
    """
    rows = "".join(
        f'<div class="pp-side-status-row">'
        f'<span class="pp-side-label">{rotulo}</span>'
        f'{badge(valor, tipo)}'
        f'</div>'
        for rotulo, valor, tipo in itens
    )
    st.markdown(f'<div class="pp-side-status">{rows}</div>', unsafe_allow_html=True)


def resumo_pre_busca(itens: list[tuple[str, str]]) -> None:
    """
    Bloco de cards informativos lado a lado, usado antes da busca começar.

    ``itens`` = lista de tuplas (rótulo, valor). Ex: [("Itens", "27"),
    ("Fornecedores", "Almeida + Dantas"), ("Cache", "2 dias")].
    """
    cards = "".join(
        f'<div class="pp-resumo-item">'
        f'<div class="pp-resumo-item-label">{rotulo}</div>'
        f'<div class="pp-resumo-item-valor">{valor}</div>'
        f'</div>'
        for rotulo, valor in itens
    )
    st.markdown(f'<div class="pp-resumo-pre">{cards}</div>', unsafe_allow_html=True)


def painel_progresso_busca(
    *,
    frac: float,
    titulo: str,
    sub: str,
    tempo: str,
    item_atual: int,
    item_total: int,
    item_descricao: str,
    stats: list[tuple[str, int, int, int]] | None = None,
    encerrado: bool = False,
) -> None:
    """
    Painel grande de progresso ocupando full-width — substitui o ``st.progress``
    cru espremido em coluna estreita.

    Renderiza:
    - Título + subtítulo (ex.: "Buscando preços" / "Almeida (1 de 2)").
    - Tempo decorrido em pílula.
    - Barra de progresso fina + percentual destacado.
    - Card com descrição completa do item atual (não truncada).
    - Resumo dos fornecedores já concluídos (chips por status).

    Args:
        frac: progresso global [0, 1].
        titulo: ex.: "Buscando preços".
        sub: ex.: "Almeida (fornecedor 1 de 2)".
        tempo: ex.: "01:42".
        item_atual: índice 1-based do item em processamento.
        item_total: total de itens.
        item_descricao: descrição completa do item atual (ou texto-stub
            quando entre fornecedores).
        stats: lista de tuplas ``(fornecedor, n_ok, n_variacao, n_vazio)``
            para fornecedores já concluídos. Renderiza como chips no rodapé.
        encerrado: se True, esconde a linha de "item atual" (busca acabou).
    """
    pct = max(0, min(100, int(round(frac * 100))))

    stats_html = ""
    if stats:
        partes = []
        for nome, n_ok, n_var, n_vazio in stats:
            chips = badge(f"{n_ok} encontrados", "success")
            if n_var:
                chips += " " + badge(f"{n_var} por variação", "warning")
            if n_vazio:
                chips += " " + badge(f"{n_vazio} sem resultado", "danger")
            partes.append(
                f'<div class="pp-busca-stat">'
                f'<span class="pp-busca-stat-nome">{nome.capitalize()}</span>'
                f'<span>{chips}</span>'
                f'</div>'
            )
        stats_html = f'<div class="pp-busca-stats">{"".join(partes)}</div>'

    if encerrado:
        item_block = ""
    else:
        idle = item_atual <= 0
        cls_idle = " is-idle" if idle else ""
        rotulo = (
            "Aguardando início" if idle
            else f"Item {item_atual} de {item_total}"
        )
        item_block = (
            f'<div class="pp-busca-counter">'
            f'  <span class="pp-busca-item-label">{rotulo}</span>'
            f'  <span class="pp-busca-pct">{pct}%</span>'
            f'</div>'
            f'<div class="pp-busca-item-desc{cls_idle}">{item_descricao or "—"}</div>'
        )

    st.markdown(
        f'<div class="pp-busca-painel">'
        f'  <div class="pp-busca-cab">'
        f'    <div>'
        f'      <div class="pp-busca-titulo">{titulo}</div>'
        f'      <div class="pp-busca-sub">{sub}</div>'
        f'    </div>'
        f'    <div class="pp-busca-tempo">{tempo}</div>'
        f'  </div>'
        f'  <div class="pp-busca-barra">'
        f'    <div class="pp-busca-barra-fill" style="width:{pct}%"></div>'
        f'  </div>'
        f'  {item_block}'
        f'  {stats_html}'
        f'</div>',
        unsafe_allow_html=True,
    )
