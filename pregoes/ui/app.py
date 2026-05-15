"""
UI Streamlit — interface para usuário leigo.

Filosofia:
- Sem jargão técnico em nenhum botão ou label
- Tudo em português
- Cada tela cabe sem rolar (na medida do possível)
- Cores e estados visuais claros (verde = ok, amarelo = conferir, vermelho = problema)
"""
from pathlib import Path
import tempfile

import streamlit as st
import pandas as pd

from pregoes import config
from pregoes.extrator import extrair_pdf
from pregoes.exportador import exportar
from pregoes.db import init_db, get_conn


# === Configuração da página ===
st.set_page_config(
    page_title=config.TITULO_APP,
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Inicializa banco no primeiro load
init_db()

# === Estado da sessão ===
if "tela" not in st.session_state:
    st.session_state.tela = "home"
if "extracao" not in st.session_state:
    st.session_state.extracao = None
if "df_editavel" not in st.session_state:
    st.session_state.df_editavel = None


# === Helpers de navegação ===
def ir_para(tela: str):
    st.session_state.tela = tela
    st.rerun()


# === Tela: HOME ===
def tela_home():
    st.title("Pregões")
    st.caption("Automação da formação de preços para licitações")

    col1, col2 = st.columns([1, 2])
    with col1:
        if st.button("➕ Novo pregão", type="primary", use_container_width=True):
            st.session_state.extracao = None
            st.session_state.df_editavel = None
            ir_para("upload")

    st.divider()
    st.subheader("Pregões recentes")

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, orgao, numero_pregao, status, criado_em
            FROM editais
            ORDER BY criado_em DESC
            LIMIT 20
        """).fetchall()

    if not rows:
        st.info("Nenhum pregão registrado ainda. Clique em **Novo pregão** para começar.")
        return

    df = pd.DataFrame([dict(r) for r in rows])
    df.columns = ["ID", "Órgão", "Pregão", "Status", "Criado em"]
    st.dataframe(df, use_container_width=True, hide_index=True)


# === Tela: UPLOAD ===
def tela_upload():
    if st.button("← Voltar"):
        ir_para("home")

    st.title("Novo pregão — upload do edital")

    arquivo = st.file_uploader(
        "Arraste o PDF do edital aqui ou clique para escolher",
        type=["pdf"],
        accept_multiple_files=False,
    )

    col1, col2 = st.columns(2)
    with col1:
        orgao = st.text_input("Órgão (opcional)",
                              placeholder="Ex: Prefeitura Municipal de Araruna")
    with col2:
        numero = st.text_input("Número do pregão (opcional)",
                               placeholder="Ex: 00006/2026")

    if arquivo and st.button("Extrair itens do PDF", type="primary"):
        with st.spinner("Lendo PDF e detectando tabelas..."):
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf",
                                              dir=str(config.EDITAIS_DIR))
            tmp.write(arquivo.read())
            tmp.close()
            tmp_path = Path(tmp.name)

            try:
                resultado = extrair_pdf(tmp_path)
            except Exception as exc:
                st.error(f"Falha ao ler PDF: {exc}")
                return

        st.session_state.extracao = resultado
        # Reset do df_editavel: sem isto, uma extração nova reaproveita o
        # DataFrame em cache da extração anterior e o usuário vê itens
        # desatualizados na revisão (o data_editor não repopula sozinho).
        st.session_state.df_editavel = None
        st.session_state.orgao = orgao
        st.session_state.numero = numero
        st.session_state.pdf_path = str(tmp_path)
        ir_para("revisao")


# === Tela: REVISÃO ===
def tela_revisao():
    res = st.session_state.extracao
    if res is None:
        ir_para("home")
        return

    if st.button("← Voltar"):
        ir_para("upload")

    st.title("Revisão dos itens extraídos")

    # Indicadores
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total de itens", res.total_itens)
    c2.metric("OK", res.total_itens - res.total_duvidosos)
    c3.metric("Duvidosos", res.total_duvidosos,
              delta="conferir" if res.total_duvidosos else None,
              delta_color="off")
    c4.metric("Tabelas detectadas", len(res.tabelas_brutas))

    if res.avisos:
        for aviso in res.avisos:
            st.warning(aviso)

    if res.total_itens == 0:
        st.error("Nenhum item foi extraído. Pode ser PDF escaneado (precisa OCR) "
                 "ou layout que a heurística não reconheceu. "
                 "Funcionalidade de mapeamento manual será adicionada na próxima entrega.")
        return

    st.caption("Confira os itens abaixo. Você pode editar qualquer célula clicando nela. "
               "Linhas em amarelo precisam de atenção.")

    # Construir DataFrame editável
    if st.session_state.df_editavel is None:
        st.session_state.df_editavel = pd.DataFrame([{
            "OK": item.confianca == "extraido",
            "Item": item.item_numero,
            "Descrição": item.descricao,
            "Unidade": item.unidade,
            "Quantidade": item.quantidade,
            "Valor referência": item.valor_referencia,
        } for item in res.itens])

    edited = st.data_editor(
        st.session_state.df_editavel,
        use_container_width=True,
        height=500,
        num_rows="dynamic",
        column_config={
            "OK": st.column_config.CheckboxColumn("OK?", default=True),
            "Item": st.column_config.NumberColumn("Item", format="%d"),
            "Descrição": st.column_config.TextColumn("Descrição", width="large"),
            "Unidade": st.column_config.TextColumn("Unidade"),
            "Quantidade": st.column_config.NumberColumn("Quant", format="%.2f"),
            "Valor referência": st.column_config.NumberColumn("Ref. (R$)", format="%.2f"),
        },
        hide_index=True,
        key="editor",
    )
    st.session_state.df_editavel = edited

    st.divider()
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        st.caption(f"💡 Próxima etapa (fase 2): consulta automática de preços em "
                   f"Almeida e Dantas. Por enquanto, gera planilha matriz com "
                   f"valores de referência preenchidos e preços em branco.")

    with col2:
        markup = st.number_input(
            "Markup deste pregão",
            min_value=1.00,
            max_value=3.00,
            value=float(config.MARKUP_DEFAULT),
            step=0.05,
            help=(
                "Multiplicador sobre o custo de aquisição. "
                f"Padrão {config.MARKUP_DEFAULT}; reduza para 1.30 em "
                "pregões com concorrência apertada."
            ),
        )

    with col3:
        if st.button("📊 Gerar planilha matriz", type="primary",
                     use_container_width=True):
            _gerar_excel(edited, markup=markup)


def _gerar_excel(df: pd.DataFrame, markup: float):
    """Converte DataFrame editado de volta para ItemExtraido e exporta."""
    from pregoes.extrator import ItemExtraido

    df_ok = df[df["OK"] == True]
    if df_ok.empty:
        st.error("Nenhum item marcado como OK — nada para exportar.")
        return

    itens = [
        ItemExtraido(
            item_numero=int(r["Item"]) if pd.notna(r["Item"]) else None,
            descricao=str(r["Descrição"]) if pd.notna(r["Descrição"]) else "",
            unidade=str(r["Unidade"]) if pd.notna(r["Unidade"]) else None,
            quantidade=float(r["Quantidade"]) if pd.notna(r["Quantidade"]) else None,
            valor_referencia=float(r["Valor referência"]) if pd.notna(r["Valor referência"]) else None,
        )
        for _, r in df_ok.iterrows()
    ]

    with st.spinner("Gerando planilha matriz a partir do template..."):
        try:
            destino = exportar(
                itens,
                orgao=st.session_state.get("orgao"),
                numero_pregao=st.session_state.get("numero"),
                markup=markup,
            )
        except Exception as exc:
            st.error(f"Falha ao gerar planilha: {exc}")
            return

        # Persistir edital no banco (registro mínimo)
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO editais (orgao, numero_pregao, arquivo_pdf, status)
                VALUES (?, ?, ?, ?)
            """, (
                st.session_state.get("orgao") or "",
                st.session_state.get("numero") or "",
                st.session_state.get("pdf_path") or "",
                "rascunho",
            ))

    st.success(f"✅ Planilha gerada: `{destino.name}`")
    with open(destino, "rb") as f:
        st.download_button(
            "⬇️ Baixar planilha matriz",
            data=f.read(),
            file_name=destino.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary",
        )


# === Roteador ===
TELAS = {
    "home": tela_home,
    "upload": tela_upload,
    "revisao": tela_revisao,
}

TELAS[st.session_state.tela]()
