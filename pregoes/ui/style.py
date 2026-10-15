"""
Sistema visual da UI — CSS único + componentes de alto nível.

Tudo que é "como o app se parece" mora aqui. Páginas em ``app.py`` chamam
helpers desta camada em vez de empilhar ``st.markdown``/``st.success``
direto. Mantém a UI consistente sem propagar HTML pelas telas.

Princípio: o Streamlit cuida dos widgets (button, input, table). Aqui só
ajustamos o visual global e damos componentes que o Streamlit não tem
nativos (pílula de status, indicador, cabeçalho, aviso).

Direção visual aprovada em 2026-10 (proposta "Pregões — nova direção
visual"): fonte IBM Plex Sans (declarada no tema, .streamlit/config.toml),
tinta #101828 sobre fundo #F5F6F8, Azul Pregões #1D3F8F para a ação
principal; menu lateral com ícones; pílulas no lugar de caixas coloridas para
o que é normal — caixa de aviso só para problema de verdade.
"""
from __future__ import annotations

from typing import Iterable, Literal

import streamlit as st


# === Paleta — fonte única de cor. Não duplicar valores em outros lugares ===
# (as cores-base do tema em .streamlit/config.toml espelham estas.)
PALETA = {
    "primary":        "#1D3F8F",
    "primary_hover":  "#16326F",
    "primary_light":  "#EAF0FB",
    "bg":             "#F5F6F8",
    "surface":        "#FFFFFF",
    "surface_alt":    "#F9FAFB",
    "border":         "#E4E7EC",
    "border_strong":  "#D0D5DD",
    "text":           "#101828",
    "text_body":      "#344054",
    "text_secondary": "#475467",
    "text_muted":     "#667085",
    "success":        "#067647",
    "success_bg":     "#ECFDF3",
    "success_dot":    "#12B76A",
    "warning":        "#93370D",
    "warning_bg":     "#FFFAEB",
    "warning_border": "#FEDF89",
    "warning_dot":    "#F79009",
    "danger":         "#B42318",
    "danger_bg":      "#FEF3F2",
    "danger_border":  "#FECDCA",
    "danger_dot":     "#F04438",
    "info":           "#1D3F8F",
    "info_bg":        "#EAF0FB",
}

TipoBadge = Literal["success", "warning", "danger", "info", "neutro"]
TipoAviso = Literal["success", "warning", "danger", "info"]

P = PALETA

# Ícones de traço (inline SVG, herdam a cor do texto) — sem emoji, que muda de
# aparência entre Windows e Mac.
_SVG = {
    "check": '<path d="M5 12.5 10 17l9-10"/>',
    "alerta": '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4"/><path d="M12 17h.01"/>',
    "x": '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
    "documento": ('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 '
                  '2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13l2 2 4-4"/>'),
}


def icone(nome: str, tamanho: int = 16, espessura: float = 2) -> str:
    """SVG inline de traço (decorativo: aria-hidden)."""
    return (f'<svg width="{tamanho}" height="{tamanho}" viewBox="0 0 24 24" '
            f'fill="none" stroke="currentColor" stroke-width="{espessura}" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'{_SVG[nome]}</svg>')


_CSS = f"""
<style>
/* ===== Layout global ===== */
.stApp {{ background-color: {P['bg']}; }}

.main .block-container, [data-testid="stMainBlockContainer"] {{
    padding-top: 2rem;
    padding-bottom: 3rem;
    max-width: 1120px;
}}

/* Esconde o cabeçalho/menu do Streamlit — uso interno do operador. */
header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
#MainMenu, footer {{ visibility: hidden; }}

/* ===== Tipografia (a fonte vem do tema) ===== */
.stApp {{ color: {P['text']}; }}
h1 {{ font-weight: 600; font-size: 1.625rem; letter-spacing: -0.01em; }}
h2 {{ font-weight: 600; font-size: 1.125rem; }}
h3 {{ font-weight: 600; font-size: 1rem; }}
.pp-num {{ font-variant-numeric: tabular-nums; }}

/* ===== Menu lateral ===== */
section[data-testid="stSidebar"] {{
    background-color: {P['surface']};
    border-right: 1px solid {P['border']};
}}
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {{
    padding-top: 1.25rem;
}}
/* Itens de navegação: botões do Streamlit vestidos de item de menu. */
section[data-testid="stSidebar"] .stButton > button {{
    justify-content: flex-start;
    gap: 0.75rem;
    height: 2.5rem;
    min-height: 2.5rem;
    padding: 0 0.75rem;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: {P['text_body']};
    font-weight: 500;
    box-shadow: none;
}}
section[data-testid="stSidebar"] .stButton > button:hover {{
    background: #F2F4F7;
    color: {P['text']};
}}
section[data-testid="stSidebar"] .stButton > button[kind="primary"] {{
    background: {P['primary_light']};
    color: {P['primary']};
    font-weight: 600;
}}
section[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {{
    background: {P['primary_light']};
    color: {P['primary']};
}}
/* O conteúdo do botão (ícone + rótulo) vem num div/span próprios, centrados
   pelo Streamlit — alinhar à esquerda exige mirar neles, não no <button>. */
section[data-testid="stSidebar"] .stButton > button > div,
section[data-testid="stSidebar"] .stButton > button > div > span {{
    width: 100%;
    justify-content: flex-start;
    gap: 0.75rem;
}}
section[data-testid="stSidebar"] .stButton > button p {{
    font-size: 0.9375rem;
    text-align: left;
}}
section[data-testid="stSidebar"] .stButton {{ margin-bottom: -0.5rem; }}

/* ===== Botões ===== */
.stButton > button:disabled, .stDownloadButton > button:disabled {{
    opacity: 0.45;
    cursor: not-allowed;
}}
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
    border-radius: 8px;
    font-weight: 500;
    min-height: 2.5rem;
    transition: background 120ms ease, border-color 120ms ease, color 120ms ease;
}}
.stButton > button[kind="secondary"], .stDownloadButton > button[kind="secondary"],
.stFormSubmitButton > button[kind="secondaryFormSubmit"] {{
    border: 1px solid {P['border_strong']};
    background: {P['surface']};
    color: {P['text']};
}}
.stButton > button[kind="secondary"]:hover, .stDownloadButton > button[kind="secondary"]:hover {{
    border-color: {P['primary']};
    color: {P['primary']};
}}
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"],
.stFormSubmitButton > button[kind="primaryFormSubmit"] {{
    background: {P['primary']};
    border: 1px solid {P['primary']};
    color: #FFFFFF;
    font-weight: 600;
}}
.stButton > button[kind="primary"]:hover, .stDownloadButton > button[kind="primary"]:hover,
.stFormSubmitButton > button[kind="primaryFormSubmit"]:hover {{
    background: {P['primary_hover']};
    border-color: {P['primary_hover']};
    color: #FFFFFF;
}}
.stButton > button[kind="tertiary"] {{
    color: {P['primary']};
    font-weight: 500;
}}

/* ===== Campos ===== */
.stTextInput input, .stNumberInput input, .stTextArea textarea {{
    border-radius: 8px;
}}
.stTextInput label p, .stNumberInput label p, .stTextArea label p,
.stSelectbox label p, .stRadio > label p {{
    font-weight: 500;
    color: {P['text']};
}}

/* ===== Envio de arquivo (os textos do componente vêm em inglês) ===== */
[data-testid="stFileUploaderDropzone"] {{
    border: 1.5px dashed {P['border_strong']};
    border-radius: 12px;
    background: {P['surface']};
    padding: 1.25rem 1.5rem;
}}
[data-testid="stFileUploaderDropzone"]:hover {{
    border-color: {P['primary']};
    background: {P['primary_light']};
}}
/* Streamlit 1.57: a instrução é um <span> ("200MB per file • PDF") e o rótulo
   do botão um <p> ("Upload") ao lado do ícone. Esconde os dois e escreve em
   português via ::after. */
[data-testid="stFileUploaderDropzoneInstructions"] > div > span,
[data-testid="stFileUploaderDropzoneInstructions"] small {{ display: none; }}
[data-testid="stFileUploaderDropzoneInstructions"] > div::after {{
    content: "PDF do edital · até 200 MB";
    color: {P['text_secondary']};
    font-size: 0.875rem;
}}
[data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] {{ display: none; }}
[data-testid="stFileUploaderDropzone"] button span[data-has-shortcut]::after {{
    content: "Escolher arquivo";
    font-size: 0.9375rem;
}}

/* ===== Blocos sanfonados, cards e tabelas ===== */
[data-testid="stExpander"] details {{
    border: 1px solid {P['border']};
    border-radius: 12px;
    background: {P['surface']};
}}
[data-testid="stExpander"] details > summary {{ padding: 0.8rem 1rem; }}
[data-testid="stExpander"] details > summary p {{ font-weight: 500; }}
/* Containers com key "ppcard-*" viram cards brancos (o padrão é transparente). */
div[class*="st-key-ppcard"] {{
    background: {P['surface']};
    border-radius: 12px;
}}
[data-testid="stDataFrame"], [data-testid="stDataFrameContainer"] {{
    border: 1px solid {P['border']};
    border-radius: 12px;
    overflow: hidden;
}}

/* ===== Indicadores ===== */
[data-testid="stMetric"] {{
    background: {P['surface']};
    border: 1px solid {P['border']};
    border-radius: 12px;
    padding: 1rem 1.25rem;
}}
[data-testid="stMetricLabel"] p {{
    color: {P['text_secondary']};
    font-weight: 600;
    font-size: 0.75rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}}
[data-testid="stMetricValue"] {{
    color: {P['text']};
    font-weight: 600;
    font-size: 1.75rem;
    font-variant-numeric: tabular-nums;
}}
.pp-metrics {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 1rem;
    margin: 0.25rem 0 1rem;
}}
.pp-metric {{
    background: {P['surface']};
    border: 1px solid {P['border']};
    border-radius: 12px;
    padding: 1rem 1.25rem;
}}
.pp-metric-label {{
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: {P['text_secondary']};
}}
.pp-metric-valor {{
    font-size: 1.75rem;
    font-weight: 600;
    line-height: 1.2;
    margin-top: 0.25rem;
    font-variant-numeric: tabular-nums;
}}
.pp-metric-dica {{ font-size: 0.875rem; color: {P['text_secondary']}; }}

/* ===== Cabeçalho da tela ===== */
.pp-header {{ margin: 0 0 1.5rem 0; }}
.pp-header .pp-contexto {{
    font-size: 0.875rem;
    color: {P['text_secondary']};
    margin-bottom: 0.25rem;
}}
/* Contexto com "Editar" ao lado (linha própria, acima do título). */
div[class*="st-key-ppctx-"] {{ margin-bottom: -0.75rem; }}
div[class*="st-key-ppctx-"] .pp-contexto {{
    font-size: 0.875rem;
    color: {P['text_secondary']};
}}
div[class*="st-key-ppctx-"] [data-testid="stMarkdownContainer"] {{ margin-bottom: 0; }}
div[class*="st-key-ppctx-"] [data-testid="stPopoverButton"] {{
    min-height: 1.75rem;
    height: 1.75rem;
    padding: 0 0.375rem;
    color: {P['primary']};
}}
div[class*="st-key-ppctx-"] [data-testid="stPopoverButton"] p {{ font-size: 0.8125rem; }}
.pp-header h1 {{
    margin: 0;
    padding: 0;
    font-size: 1.625rem;
    font-weight: 600;
    letter-spacing: -0.01em;
    line-height: 1.25;
}}
.pp-header .pp-sub {{
    color: {P['text_secondary']};
    font-size: 0.9375rem;
    margin-top: 0.35rem;
}}
.pp-secao {{
    font-size: 1.125rem;
    font-weight: 600;
    margin: 1.25rem 0 0.5rem;
}}
/* Título de seção com ação à direita: a margem sai do título e vai para a
   linha, para o título e o botão ficarem centrados entre si. */
/* O st.markdown tem margin-bottom: -1rem; os 1rem aqui o anulam, senão o
   título fica 'mais baixo' que o botão ao centralizar. */
.pp-secao.pp-secao-linha {{ margin: 0 0 1rem; }}
div[class*="st-key-ppsecaolinha-"] {{ margin-top: 1.25rem; }}
hr.pp-sep {{
    margin: 0.15rem 0;
    border: 0;
    border-top: 1px solid #F2F4F7;
}}

/* ===== Pílulas de status ===== */
.pp-badge {{
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    height: 1.6rem;
    padding: 0 0.65rem;
    border-radius: 999px;
    font-size: 0.8125rem;
    font-weight: 500;
    line-height: 1;
    white-space: nowrap;
    vertical-align: middle;
}}
.pp-badge--success {{ background: {P['success_bg']}; color: {P['success']}; }}
.pp-badge--warning {{ background: {P['warning_bg']}; color: {P['warning']}; }}
.pp-badge--danger  {{ background: {P['danger_bg']};  color: {P['danger']};  }}
.pp-badge--info    {{ background: {P['info_bg']};    color: {P['info']};    }}
.pp-badge--neutro  {{ background: {P['surface']}; color: {P['text_body']};
                     border: 1px solid {P['border']}; }}
.pp-badge--success::before, .pp-badge--warning::before, .pp-badge--danger::before {{
    content: "";
    width: 6px; height: 6px; border-radius: 50%;
    flex: none;
}}
.pp-badge--success::before {{ background: {P['success_dot']}; }}
.pp-badge--warning::before {{ background: {P['warning_dot']}; }}
.pp-badge--danger::before  {{ background: {P['danger_dot']}; }}
.pp-chips {{ display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; margin: 0.25rem 0 0.75rem; }}

/* Pílulas de destaque dentro dos cards de opção. */
.pp-pill {{
    display: inline-flex;
    align-items: center;
    height: 1.35rem;
    font-size: 0.75rem;
    font-weight: 600;
    padding: 0 0.5rem;
    border-radius: 999px;
    margin-left: 0.4rem;
    vertical-align: middle;
    white-space: nowrap;
}}
.pp-pill--match     {{ background: {P['success_bg']}; color: {P['success']}; }}
.pp-pill--barato    {{ background: #F2F4F7; color: {P['text_body']}; }}
.pp-pill--catalogo  {{ background: {P['info_bg']}; color: {P['info']}; }}
.pp-pill--aprendido {{ background: {P['warning_bg']}; color: {P['warning']}; }}

/* ===== Avisos — só para o que pede atenção ===== */
.pp-aviso {{
    display: flex;
    gap: 0.65rem;
    align-items: flex-start;
    padding: 0.75rem 0.9rem;
    border-radius: 10px;
    border: 1px solid;
    margin-bottom: 0.75rem;
    font-size: 0.9375rem;
    line-height: 1.5;
}}
.pp-aviso .pp-aviso-ico {{ flex: none; display: flex; margin-top: 0.15rem; }}
.pp-aviso--success {{ background: {P['success_bg']}; border-color: #ABEFC6; color: #085D3A; }}
.pp-aviso--warning {{ background: {P['warning_bg']}; border-color: {P['warning_border']}; color: {P['warning']}; }}
.pp-aviso--danger  {{ background: {P['danger_bg']};  border-color: {P['danger_border']};  color: {P['danger']}; }}
.pp-aviso--info    {{ background: {P['surface']};    border-color: {P['border']};       color: {P['text_body']}; }}
.pp-aviso--info .pp-aviso-ico {{ color: {P['primary']}; }}

/* ===== Barra de progresso ===== */
.pp-barra {{
    height: 6px;
    border-radius: 999px;
    background: {P['border']};
    overflow: hidden;
}}
.pp-barra > div {{ height: 100%; border-radius: 999px; background: {P['primary']}; }}

/* ===== Cards de opção (tela "Busca de preços") ===== */
/* Card escolhido: borda Azul Pregões de 2px. O Streamlit aplica a classe
   `st-key-<key>` no container com borda; só as opções selecionadas usam o key
   `ppoptsel-*` ("ppoptsel-" não é substring de "ppopt-"). */
div[class*="st-key-ppopt"] {{
    background: {P['surface']};
    padding: 0.5rem 1rem !important;
}}
div[class*="st-key-ppoptsel-"] {{
    border: 2px solid {P['primary']} !important;
}}
.pp-op-marca {{ font-weight: 600; font-size: 0.9375rem; line-height: 1.4; }}
.pp-op-sub {{
    color: {P['text_secondary']};
    font-size: 0.8125rem;
    line-height: 1.45;
    margin-top: 0.1rem;
}}
.pp-op-sub code {{
    font-size: 0.75rem;
    color: {P['text_body']};
    background: #F2F4F7;
    padding: 0.05rem 0.3rem;
    border-radius: 4px;
    white-space: nowrap;
}}
.pp-op-preco {{
    text-align: right;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}}
.pp-op-escolhido {{
    display: flex;
    justify-content: flex-end;
    align-items: center;
    gap: 0.3rem;
    color: {P['primary']};
    font-weight: 600;
    font-size: 0.875rem;
}}
.pp-desc-completa {{
    background: {P['surface_alt']};
    border: 1px solid {P['border']};
    border-radius: 8px;
    padding: 0.55rem 0.8rem;
    font-size: 0.8125rem;
    color: {P['text_body']};
    line-height: 1.5;
}}

/* ===== Lista de itens (Busca de preços) ===== */
div[class*="st-key-pplinha-"], div[class*="st-key-pplinhaab-"] {{
    background: {P['surface']};
    border-radius: 10px;
    padding: 0.4rem 0.75rem !important;
}}
div[class*="st-key-pplinhaab-"] {{
    border-color: {P['border_strong']} !important;
    padding-bottom: 0.75rem !important;
}}
/* O st.markdown tem margin-bottom: -1rem (compensa o <p>); nas células da
   lista o conteúdo é um <div>, e a margem negativa cortava a 2ª linha da
   descrição na borda da linha. */
div[class*="st-key-pplinha"] [data-testid="stMarkdownContainer"],
div[class*="st-key-pplista-cab"] [data-testid="stMarkdownContainer"] {{ margin-bottom: 0; }}
/* Cabeçalho da lista: mesmo recuo das linhas (padding + borda). */
div[class*="st-key-pplista-cab"] {{ padding: 0 calc(0.75rem + 1px); margin-bottom: -0.5rem; }}
.pp-li-num {{ color: {P['text_secondary']}; font-variant-numeric: tabular-nums; font-size: 0.875rem; }}
/* Descrição COMO VEIO NO EDITAL, em até 2 linhas (inteira ao abrir a linha). */
.pp-li-desc {{
    font-size: 0.875rem;
    line-height: 1.4;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    overflow-wrap: anywhere;
}}
.pp-li-escolha {{ font-size: 0.875rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.pp-li-escolha b {{ font-weight: 600; }}
.pp-li-escolha span {{ color: {P['text_secondary']}; }}
.pp-li-preco {{
    font-size: 0.875rem;
    font-weight: 600;
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}}
.pp-li-preco.pp-li-vazio {{ color: {P['text_muted']}; font-weight: 400; }}
.pp-li-cab {{
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: {P['text_secondary']};
}}
/* Botão de abrir/fechar a linha: só o ícone, compacto. */
div[class*="st-key-tg_"] button {{ min-height: 2rem; height: 2rem; padding: 0 0.35rem; }}

/* Progresso da busca ("16 de 23 itens com preço" + barra). */
.pp-progresso {{ min-width: 220px; }}
.pp-progresso-txt {{ font-size: 0.9375rem; margin-bottom: 0.4rem; }}
.pp-progresso-txt b {{ font-weight: 600; }}
.pp-progresso .pp-barra {{ max-width: 360px; }}
div[class*="st-key-ppbusca-topo"] [data-testid="stMarkdownContainer"] {{ margin-bottom: 0; }}
.pp-chips-rotulo {{ font-size: 0.8125rem; color: {P['text_secondary']}; }}

/* ===== Indicador de etapas (compacto) =====
   Seletores com [data-testid=stMarkdownContainer]: o CSS do Streamlit para
   ol/li do markdown é mais específico que uma classe só (o ol saía em bloco,
   com as etapas uma embaixo da outra). */
[data-testid="stMarkdownContainer"] ol.pp-stepper {{
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.6rem;
    margin: 0 0 1.25rem;
    font-size: 0.875rem;
    list-style: none;
    padding: 0;
}}
[data-testid="stMarkdownContainer"] ol.pp-stepper > li {{
    display: flex; align-items: center; gap: 0.5rem;
    margin: 0; padding: 0;
}}
[data-testid="stMarkdownContainer"] ol.pp-stepper > li::before {{ content: none; }}
.pp-step-circle {{
    width: 22px; height: 22px; border-radius: 50%;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 0.75rem; font-weight: 600; box-sizing: border-box;
}}
.pp-step--done {{ color: {P['success']}; font-weight: 500; }}
.pp-step--done .pp-step-circle {{ background: {P['success_bg']}; }}
.pp-step--ativo {{ color: {P['primary']}; font-weight: 600; }}
.pp-step--ativo .pp-step-circle {{ background: {P['primary']}; color: #FFFFFF; }}
.pp-step--pendente {{ color: {P['text_secondary']}; }}
.pp-step--pendente .pp-step-circle {{ border: 1px solid {P['border_strong']}; }}
.pp-step-conector {{ width: 32px; height: 1px; background: {P['border_strong']}; }}

/* ===== Marca e conexões (menu lateral) ===== */
.pp-brand {{
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0 0.5rem 1.25rem;
}}
.pp-brand-logo {{
    width: 36px; height: 36px;
    border-radius: 9px;
    background: {P['primary']};
    color: #FFFFFF;
    display: flex; align-items: center; justify-content: center;
    flex: none;
}}
.pp-brand-text {{ line-height: 1.25; }}
.pp-brand-text .pp-brand-name {{ font-weight: 700; font-size: 1.0625rem; }}
.pp-brand-text .pp-brand-tagline {{ color: {P['text_secondary']}; font-size: 0.8125rem; }}

.pp-side-status {{ padding: 1.5rem 0.5rem 0; font-size: 0.875rem; }}
.pp-side-titulo {{
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: {P['text_secondary']};
    margin-bottom: 0.6rem;
}}
.pp-side-status-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
}}
.pp-side-status-row + .pp-side-status-row {{ margin-top: 0.45rem; }}
.pp-side-valor {{ display: inline-flex; align-items: center; gap: 0.4rem; }}
.pp-side-valor::before {{ content: ""; width: 7px; height: 7px; border-radius: 50%; }}
.pp-side-valor--success {{ color: {P['success']}; }}
.pp-side-valor--success::before {{ background: {P['success_dot']}; }}
.pp-side-valor--warning {{ color: {P['warning']}; }}
.pp-side-valor--warning::before {{ background: {P['warning_dot']}; }}
.pp-side-valor--danger {{ color: {P['danger']}; }}
.pp-side-valor--danger::before {{ background: {P['danger_dot']}; }}
.pp-side-valor--neutro, .pp-side-valor--info {{ color: {P['text_secondary']}; }}
.pp-side-valor--neutro::before, .pp-side-valor--info::before {{ background: {P['border_strong']}; }}
.pp-side-versao {{ color: {P['text_muted']}; font-size: 0.75rem; margin-top: 0.9rem; }}

/* ===== Painel de progresso da busca de preços ===== */
.pp-busca-painel {{
    background: {P['surface']};
    border: 1px solid {P['border']};
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin: 0.5rem 0 1rem 0;
}}
.pp-busca-cab {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 1rem;
    margin-bottom: 1rem;
}}
.pp-busca-titulo {{ font-size: 1.0625rem; font-weight: 600; line-height: 1.3; }}
.pp-busca-sub {{ color: {P['text_secondary']}; font-size: 0.875rem; margin-top: 0.15rem; }}
.pp-busca-tempo {{
    color: {P['text_secondary']};
    font-variant-numeric: tabular-nums;
    font-size: 0.875rem;
    background: {P['surface_alt']};
    padding: 0.3rem 0.65rem;
    border-radius: 8px;
    border: 1px solid {P['border']};
    white-space: nowrap;
}}
.pp-busca-barra {{
    height: 6px;
    background: {P['border']};
    border-radius: 999px;
    overflow: hidden;
    margin: 0.25rem 0 1rem 0;
}}
.pp-busca-barra-fill {{
    height: 100%;
    background: {P['primary']};
    transition: width 240ms ease-out;
    border-radius: 999px;
}}
.pp-busca-counter {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 0.5rem;
}}
.pp-busca-item-label {{
    color: {P['text_secondary']};
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
}}
.pp-busca-pct {{
    font-size: 1.375rem;
    font-weight: 600;
    color: {P['primary']};
    font-variant-numeric: tabular-nums;
}}
.pp-busca-item-desc {{
    background: {P['surface_alt']};
    border: 1px solid {P['border']};
    border-radius: 8px;
    padding: 0.75rem 0.9rem;
    margin-bottom: 1rem;
    font-size: 0.9375rem;
    line-height: 1.45;
    word-break: break-word;
    min-height: 3rem;
    display: flex;
    align-items: center;
}}
.pp-busca-item-desc.is-idle {{ color: {P['text_secondary']}; font-style: italic; }}
.pp-busca-stats {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1rem;
    padding-top: 1rem;
    border-top: 1px solid {P['border']};
}}
.pp-busca-stat {{ display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }}
.pp-busca-stat-nome {{ font-weight: 600; font-size: 0.875rem; }}

/* Sumário pré-busca: cards informativos lado a lado */
.pp-resumo-pre {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem;
    margin: 0.5rem 0 1rem 0;
}}
.pp-resumo-item {{
    background: {P['surface']};
    border: 1px solid {P['border']};
    border-radius: 12px;
    padding: 1rem 1.25rem;
}}
.pp-resumo-item-label {{
    color: {P['text_secondary']};
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 600;
    margin-bottom: 0.25rem;
}}
.pp-resumo-item-valor {{ font-size: 1.0625rem; font-weight: 600; }}
</style>
"""


# === Helpers ===

def aplicar_estilo() -> None:
    """Injeta o CSS único. Chamar uma vez no topo do ``app.py``."""
    st.markdown(_CSS, unsafe_allow_html=True)


def header_pagina(titulo: str, subtitulo: str | None = None,
                  contexto: str | None = None) -> None:
    """Cabeçalho da tela: ``contexto`` (linha pequena acima, ex.: o pregão em
    curso), título e subtítulo."""
    ctx = f'<div class="pp-contexto">{contexto}</div>' if contexto else ""
    sub = f'<div class="pp-sub">{subtitulo}</div>' if subtitulo else ""
    st.markdown(
        f'<div class="pp-header">{ctx}<h1>{titulo}</h1>{sub}</div>',
        unsafe_allow_html=True,
    )


def secao(titulo: str) -> None:
    """Título de seção dentro da tela (18 / 600)."""
    st.markdown(f'<div class="pp-secao">{titulo}</div>', unsafe_allow_html=True)


def secao_com_acao(titulo: str, chave: str):
    """Título de seção com espaço para uma ação à direita (ex.: 'Ver
    histórico'). Devolve um container alinhado à direita para o botão."""
    with st.container(key=f"ppsecaolinha-{chave}"):
        c_tit, c_acao = st.columns([3, 1], vertical_alignment="center")
        c_tit.markdown(f'<div class="pp-secao pp-secao-linha">{titulo}</div>',
                       unsafe_allow_html=True)
        with c_acao:
            return st.container(horizontal=True, horizontal_alignment="right")


def stepper(passos: list[str], atual: int) -> None:
    """Indicador de etapas do fluxo no topo das telas ("onde estou?").

    ``atual`` é o índice 0-based do passo corrente: anteriores saem como
    concluídos (✓), o atual destacado, os demais pendentes. Só apresentação."""
    partes: list[str] = []
    for i, nome in enumerate(passos):
        if i < atual:
            estado, miolo = "done", icone("check", 13, 3)
        elif i == atual:
            estado, miolo = "ativo", str(i + 1)
        else:
            estado, miolo = "pendente", str(i + 1)
        atributo = ' aria-current="step"' if i == atual else ""
        partes.append(
            f'<li class="pp-step pp-step--{estado}"{atributo}>'
            f'<span class="pp-step-circle">{miolo}</span>'
            f'<span class="pp-step-label">{nome}</span></li>'
        )
        if i < len(passos) - 1:
            partes.append('<li class="pp-step-conector" aria-hidden="true"></li>')
    st.markdown(f'<ol class="pp-stepper" aria-label="Etapas do pregão">'
                f'{"".join(partes)}</ol>', unsafe_allow_html=True)


def badge(texto: str, tipo: TipoBadge = "neutro") -> str:
    """HTML de uma pílula de status — para embutir em outras renderizações.
    success/warning/danger levam o ponto de cor; info/neutro, não."""
    return f'<span class="pp-badge pp-badge--{tipo}">{texto}</span>'


def render_badge(texto: str, tipo: TipoBadge = "neutro") -> None:
    """Renderiza uma pílula diretamente."""
    st.markdown(badge(texto, tipo), unsafe_allow_html=True)


def linha_chips(chips: Iterable[tuple[str, TipoBadge]]) -> None:
    """Várias pílulas lado a lado — no lugar de caixas coloridas para estados
    normais (ex.: "Leitura 97%", "1 item para conferir")."""
    html = "".join(badge(t, k) for t, k in chips if t)
    if html:
        st.markdown(f'<div class="pp-chips">{html}</div>', unsafe_allow_html=True)


def pill(texto: str, tipo: Literal["match", "barato", "catalogo", "aprendido"]) -> str:
    """Pílula de destaque inline nos cards de opção (ex.: 'No seu catálogo')."""
    return f'<span class="pp-pill pp-pill--{tipo}">{texto}</span>'


def barra_progresso(frac: float) -> str:
    """HTML de uma barra de progresso fina (0..1)."""
    pct = max(0, min(100, int(round(frac * 100))))
    return f'<div class="pp-barra"><div style="width:{pct}%"></div></div>'


def bloco_aviso(mensagem: str, tipo: TipoAviso = "info") -> None:
    """Aviso em caixa — reservar para o que PEDE atenção (problema, ação
    necessária). Estado normal ("deu certo", "configurado") vai em pílula
    (``linha_chips``) ou texto simples."""
    icones = {"success": "check", "warning": "alerta", "danger": "x", "info": "info"}
    st.markdown(
        f'<div class="pp-aviso pp-aviso--{tipo}">'
        f'<span class="pp-aviso-ico">{icone(icones.get(tipo, "info"), 18)}</span>'
        f'<span>{mensagem}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def bloco_avisos(mensagens: Iterable[str], tipo: TipoAviso = "warning") -> None:
    """Renderiza uma sequência de avisos do mesmo tipo."""
    for m in mensagens:
        if m:
            bloco_aviso(m, tipo)


def card_inicio(titulo: str | None = None, hint: str | None = None) -> None:
    """Compatibilidade: abria um card visual em HTML. Hoje as telas usam
    ``st.container(border=True)``; mantido para não quebrar chamadas antigas."""
    if titulo:
        secao(titulo)
    if hint:
        st.caption(hint)


def card_fim() -> None:
    """Compatibilidade (ver ``card_inicio``)."""


def confianca_para_badge(conf: float) -> tuple[str, TipoBadge]:
    """Mapeia a confiança da leitura do PDF [0,1] para (rótulo, tipo de pílula).
    Semáforo: ≥0.95 verde, ≥0.80 âmbar, <0.80 vermelho."""
    if conf >= 0.95:
        return (f"Leitura {conf:.0%}", "success")
    if conf >= 0.80:
        return (f"Leitura {conf:.0%} — confira", "warning")
    if conf > 0:
        return (f"Leitura {conf:.0%} — revise com atenção", "danger")
    return ("Nenhum item lido", "danger")


def sidebar_marca() -> None:
    """Marca no topo do menu lateral."""
    st.markdown(
        '<div class="pp-brand">'
        f'<div class="pp-brand-logo">{icone("documento", 20)}</div>'
        '<div class="pp-brand-text">'
        '<div class="pp-brand-name">Pregões</div>'
        '<div class="pp-brand-tagline">Formação de preços</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def sidebar_status(itens: list[tuple[str, str, TipoBadge]]) -> None:
    """Conexões no pé do menu lateral (fornecedores, IA) + versão do app.

    ``itens`` = lista de (rótulo, valor, tipo); o tipo dá a cor do ponto."""
    from pregoes import config
    rows = "".join(
        f'<div class="pp-side-status-row">'
        f'<span>{rotulo}</span>'
        f'<span class="pp-side-valor pp-side-valor--{tipo}">{valor}</span>'
        f'</div>'
        for rotulo, valor, tipo in itens
    )
    st.markdown(
        f'<div class="pp-side-status"><div class="pp-side-titulo">Conexões</div>'
        f'{rows}<div class="pp-side-versao">v{config.VERSAO_APP}</div></div>',
        unsafe_allow_html=True)


def resumo_pre_busca(itens: list[tuple[str, str]]) -> None:
    """Cards informativos lado a lado, antes da busca começar.

    ``itens`` = lista de (rótulo, valor)."""
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
    Painel de progresso da busca de preços, largura total.

    Args:
        frac: progresso global [0, 1].
        titulo: ex.: "Buscando preços".
        sub: ex.: "Almeida (fornecedor 1 de 2)".
        tempo: ex.: "01:42".
        item_atual: índice 1-based do item em processamento.
        item_total: total de itens.
        item_descricao: descrição completa do item atual (ou texto-stub
            quando entre fornecedores).
        stats: lista de ``(fornecedor, n_ok, n_variacao, n_vazio)`` dos
            fornecedores já concluídos — viram pílulas no rodapé.
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


def linha_metricas(metricas) -> None:
    """Linha de indicadores. ``metricas``: lista de ``(rotulo, valor, tipo,
    dica)`` com tipo em neutro|success|warning|danger — a cor vai no NÚMERO
    ("7 sem resultado" em vermelho puxa o olho); a dica aparece embaixo."""
    cores = {
        "neutro": P["text"],
        "success": P["success"],
        "warning": P["warning"],
        "danger": P["danger"],
    }
    cards = "".join(
        f'<div class="pp-metric">'
        f'<div class="pp-metric-label">{rotulo}</div>'
        f'<div class="pp-metric-valor" style="color:{cores.get(tipo, cores["neutro"])}">'
        f'{valor}</div>'
        + (f'<div class="pp-metric-dica">{dica}</div>' if dica else '')
        + '</div>'
        for rotulo, valor, tipo, dica in metricas
    )
    st.markdown(f'<div class="pp-metrics">{cards}</div>', unsafe_allow_html=True)
