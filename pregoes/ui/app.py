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

from pregoes import config, credenciais
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

    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        if st.button("➕ Novo pregão", type="primary", use_container_width=True):
            for k in ("extracao", "df_editavel", "resultados_busca",
                      "selecoes", "pagina_busca", "itens_revisados"):
                st.session_state.pop(k, None)
            ir_para("upload")
    with col2:
        if st.button("⚙️ Configurações", use_container_width=True):
            ir_para("configuracoes")

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

    # Badge de confiança da extração — semáforo para o operador entender
    # rapidamente se pode confiar no resultado ou precisa revisar com lupa.
    conf = res.confianca_global
    if conf >= 0.95:
        st.success(f"✅ Confiança da extração: **{conf:.0%}** — pode seguir.")
    elif conf >= 0.80:
        st.warning(f"⚠️ Confiança da extração: **{conf:.0%}** — revise com atenção.")
    elif conf > 0:
        st.error(f"🔴 Confiança da extração: **{conf:.0%}** — recomendado mapeamento manual ou conferência item a item.")
    else:
        st.error("🔴 Não foi possível identificar uma tabela de itens neste PDF.")

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

    # Botões de marcação em massa. Mudar a key do data_editor força o
    # Streamlit a redesenhar a tabela com os novos valores de OK?.
    if "editor_version" not in st.session_state:
        st.session_state.editor_version = 0

    bcol1, bcol2, bcol3 = st.columns([1, 1, 5])
    with bcol1:
        if st.button("☑️ Marcar todos", use_container_width=True):
            st.session_state.df_editavel["OK"] = True
            st.session_state.editor_version += 1
            st.rerun()
    with bcol2:
        if st.button("⬜ Desmarcar todos", use_container_width=True):
            st.session_state.df_editavel["OK"] = False
            st.session_state.editor_version += 1
            st.rerun()
    with bcol3:
        n_marcados = int(st.session_state.df_editavel["OK"].sum())
        st.caption(f"**{n_marcados}** de **{len(st.session_state.df_editavel)}** itens marcados.")

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
        key=f"editor_{st.session_state.editor_version}",
    )
    st.session_state.df_editavel = edited

    st.divider()
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
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

    # Salva estado para a tela seguinte usar
    st.session_state.markup = markup
    st.session_state.itens_revisados = _df_para_itens(edited)

    with col2:
        if st.button("🔍 Buscar preços", type="primary", use_container_width=True,
                     help="Consulta Almeida (e futuramente Dantas). Pode demorar alguns minutos."):
            if not st.session_state.itens_revisados:
                st.error("Nenhum item marcado como OK — nada para buscar.")
            elif credenciais.carregar("almeida") is None:
                st.error("Credenciais Almeida não configuradas. Vá em ⚙️ Configurações.")
            else:
                ir_para("busca_precos")

    with col3:
        if st.button("📊 Gerar sem buscar", use_container_width=True,
                     help="Gera a planilha matriz com colunas L/T/U vazias (preço, código, marca em branco)."):
            _gerar_excel(edited, markup=markup, selecoes={})


def _df_para_itens(df: pd.DataFrame) -> list:
    """Converte DataFrame editado de volta para lista de ItemExtraido (linhas OK)."""
    from pregoes.extrator import ItemExtraido
    df_ok = df[df["OK"] == True]
    return [
        ItemExtraido(
            item_numero=int(r["Item"]) if pd.notna(r["Item"]) else None,
            descricao=str(r["Descrição"]) if pd.notna(r["Descrição"]) else "",
            unidade=str(r["Unidade"]) if pd.notna(r["Unidade"]) else None,
            quantidade=float(r["Quantidade"]) if pd.notna(r["Quantidade"]) else None,
            valor_referencia=float(r["Valor referência"]) if pd.notna(r["Valor referência"]) else None,
        )
        for _, r in df_ok.iterrows()
    ]


def _gerar_excel(df: pd.DataFrame, markup: float, selecoes: dict):
    """Converte DataFrame editado de volta para ItemExtraido e exporta.

    `selecoes` é o mapa {indice_item: SelecaoFornecedor} já preparado pela
    tela de busca. Vazio = planilha sem preço (operador preenche manualmente).
    """
    itens = _df_para_itens(df)
    if not itens:
        st.error("Nenhum item marcado como OK — nada para exportar.")
        return

    with st.spinner("Gerando planilha matriz a partir do template..."):
        try:
            destino = exportar(
                itens,
                orgao=st.session_state.get("orgao"),
                numero_pregao=st.session_state.get("numero"),
                markup=markup,
                selecoes=selecoes,
            )
        except Exception as exc:
            st.error(f"Falha ao gerar planilha: {exc}")
            return

        # Persistir edital + itens no banco (Fase 2: itens vinculados às seleções)
        _persistir_edital(itens, selecoes)

    st.success(f"✅ Planilha gerada: `{destino.name}`")
    if selecoes:
        st.caption(f"Inclui preço/marca/código para {len(selecoes)} de {len(itens)} itens.")
    with open(destino, "rb") as f:
        st.download_button(
            "⬇️ Baixar planilha matriz",
            data=f.read(),
            file_name=destino.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary",
        )


def _persistir_edital(itens: list, selecoes: dict) -> None:
    """Grava 1 linha em `editais` + N linhas em `edital_itens`. Idempotência
    não é necessária porque cada geração é considerada um novo registro
    (status = rascunho)."""
    with get_conn() as conn:
        cursor = conn.execute("""
            INSERT INTO editais (orgao, numero_pregao, arquivo_pdf, status)
            VALUES (?, ?, ?, ?)
        """, (
            st.session_state.get("orgao") or "",
            st.session_state.get("numero") or "",
            st.session_state.get("pdf_path") or "",
            "precificado" if selecoes else "rascunho",
        ))
        edital_id = cursor.lastrowid

        for idx, item in enumerate(itens):
            sel = selecoes.get(idx)
            conn.execute("""
                INSERT INTO edital_itens
                    (edital_id, item_numero, descricao_original, unidade,
                     quantidade, valor_referencia, preco_base, confianca_match, revisado)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                edital_id,
                item.item_numero,
                item.descricao,
                item.unidade,
                item.quantidade,
                item.valor_referencia,
                sel.preco if sel else None,
                "manual" if sel else None,
                1,
            ))


# === Tela: CONFIGURAÇÕES ===
def tela_configuracoes():
    if st.button("← Voltar"):
        ir_para("home")

    st.title("Configurações")
    st.caption(
        "Credenciais dos sites de fornecedores. Salvas apenas localmente, "
        "no arquivo `data/credenciais.json` — nunca enviadas para nenhum servidor."
    )

    st.subheader("Almeida Distribuidor")
    atual = credenciais.carregar("almeida") or {"email": "", "senha": ""}
    with st.form("form_almeida"):
        email = st.text_input("E-mail", value=atual.get("email", ""))
        senha = st.text_input("Senha", value=atual.get("senha", ""), type="password")
        salvar_btn = st.form_submit_button("Salvar credenciais", type="primary")
        if salvar_btn:
            if not email or not senha:
                st.error("Preencha e-mail e senha antes de salvar.")
            else:
                credenciais.salvar("almeida", email.strip(), senha)
                st.success("Credenciais da Almeida salvas com sucesso.")

    if atual.get("email"):
        st.caption(f"✅ Credencial atual: `{atual['email']}` (senha oculta).")
    else:
        st.warning("⚠️ Nenhuma credencial salva ainda. O scraper de preços não funcionará até configurar.")

    st.divider()
    st.subheader("Dantas Distribuidora")
    st.info("Configuração será adicionada quando o scraper da Dantas estiver pronto.")


# === Tela: BUSCA DE PREÇOS ===
def tela_busca_precos():
    if st.button("← Voltar para revisão"):
        ir_para("revisao")

    st.title("🔍 Busca de preços nos fornecedores")

    itens = st.session_state.get("itens_revisados") or []
    if not itens:
        st.error("Nenhum item para buscar. Volte para a revisão.")
        return

    st.caption(f"{len(itens)} itens serão buscados na Almeida. "
               "Estimativa: ~1.5s por item (ou seja, ~{:.0f}min para esta lista).".format(
                   len(itens) * 1.5 / 60))

    # Se ainda não buscou, oferecer botão para começar
    if "resultados_busca" not in st.session_state:
        if st.button("▶️ Iniciar busca", type="primary"):
            _executar_busca(itens)
            st.rerun()
        return

    # Já buscou: mostrar resultados + seleção
    _mostrar_selecao(itens, st.session_state.resultados_busca)


def _executar_busca(itens: list) -> None:
    """Roda buscar_precos_para_itens em foreground com barra de progresso."""
    from pregoes.scraper import AlmeidaDistribuidor, buscar_precos_para_itens

    cred = credenciais.carregar("almeida")
    if cred is None:
        st.error("Credenciais Almeida não configuradas.")
        return

    progress = st.progress(0.0, text="Fazendo login na Almeida...")
    status_box = st.empty()

    def cb(atual, total, msg):
        progress.progress(atual / total, text=f"{atual}/{total} — {msg}")

    try:
        with AlmeidaDistribuidor(cred["email"], cred["senha"]) as alm:
            progress.progress(0.0, text="Login OK. Iniciando buscas...")
            resultados = buscar_precos_para_itens(itens, [alm], progresso_callback=cb)
    except Exception as exc:
        st.error(f"Falha técnica na busca: {exc}")
        return

    st.session_state.resultados_busca = resultados
    status_box.success(f"Busca concluída — {len(resultados)} itens processados.")


def _mostrar_selecao(itens: list, resultados: dict) -> None:
    """Tabela paginada com selectbox por linha. Operador escolhe a marca."""
    from pregoes.exportador import SelecaoFornecedor

    # Inicializa estado de seleções (default: primeira opção, ou nenhuma se vazio)
    if "selecoes" not in st.session_state:
        st.session_state.selecoes = {}
        for idx, opcoes in resultados.items():
            if opcoes:
                primeiro = opcoes[0]
                st.session_state.selecoes[idx] = SelecaoFornecedor(
                    preco=primeiro.preco,
                    codigo_fornecedor=primeiro.codigo_fornecedor,
                    marca=primeiro.marca,
                )

    # Resumo
    total = len(itens)
    com_resultado = sum(1 for r in resultados.values() if r)
    com_variacao = sum(1 for r in resultados.values()
                       if r and any(x.confianca == "variacao" for x in r))
    sem_resultado = total - com_resultado
    selecionados = len(st.session_state.selecoes)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Itens", total)
    c2.metric("✅ Com preço", com_resultado - com_variacao,
              help="Casamento exato com a descrição do edital")
    c3.metric("⚠️ Variação", com_variacao,
              help="Encontrado, mas por busca aproximada — conferir")
    c4.metric("❌ Sem resultado", sem_resultado)

    st.divider()

    # Paginação simples
    POR_PAGINA = 20
    n_paginas = (total + POR_PAGINA - 1) // POR_PAGINA
    if "pagina_busca" not in st.session_state:
        st.session_state.pagina_busca = 0
    pag = st.session_state.pagina_busca
    inicio = pag * POR_PAGINA
    fim = min(inicio + POR_PAGINA, total)

    pcol1, pcol2, pcol3 = st.columns([1, 2, 1])
    with pcol1:
        if st.button("◀ Anterior", disabled=(pag == 0)):
            st.session_state.pagina_busca = max(0, pag - 1)
            st.rerun()
    with pcol2:
        st.caption(f"Página {pag + 1} de {n_paginas} — itens {inicio + 1}–{fim}")
    with pcol3:
        if st.button("Próxima ▶", disabled=(pag >= n_paginas - 1)):
            st.session_state.pagina_busca = min(n_paginas - 1, pag + 1)
            st.rerun()

    # Lista de itens da página atual
    for idx in range(inicio, fim):
        item = itens[idx]
        opcoes = resultados.get(idx, [])
        sel_atual = st.session_state.selecoes.get(idx)

        if not opcoes:
            cor = "🔴"
            label_status = "Não encontrado"
        elif any(o.confianca == "variacao" for o in opcoes):
            cor = "🟡"
            label_status = f"{len(opcoes)} resultado(s) por variação"
        else:
            cor = "🟢"
            label_status = f"{len(opcoes)} resultado(s)"

        with st.expander(
            f"{cor} **#{item.item_numero or idx + 1}** — {item.descricao[:80]}  ·  {label_status}",
            expanded=False,
        ):
            if not opcoes:
                st.warning("Nenhum resultado nos fornecedores. Coluna L ficará em branco.")
                continue

            # Selectbox: operador escolhe entre as opções
            labels = ["(não usar)"] + [
                f"{o.marca} — R$ {o.preco:.2f} — código {o.codigo_fornecedor} — {o.descricao_encontrada[:60]}"
                for o in opcoes
            ]
            default_idx = 0
            if sel_atual is not None:
                # Encontra a opção que casa com a seleção atual
                for i, o in enumerate(opcoes):
                    if (o.preco == sel_atual.preco and o.marca == sel_atual.marca
                            and o.codigo_fornecedor == sel_atual.codigo_fornecedor):
                        default_idx = i + 1
                        break

            escolha = st.selectbox(
                "Marca / preço",
                options=range(len(labels)),
                format_func=lambda i: labels[i],
                index=default_idx,
                key=f"sel_{idx}",
            )
            if escolha == 0:
                st.session_state.selecoes.pop(idx, None)
            else:
                op = opcoes[escolha - 1]
                st.session_state.selecoes[idx] = SelecaoFornecedor(
                    preco=op.preco, codigo_fornecedor=op.codigo_fornecedor, marca=op.marca,
                )

    st.divider()
    st.caption(f"{selecionados} de {total} itens com marca/preço selecionados.")

    if st.button("📊 Gerar planilha matriz com preços", type="primary",
                 use_container_width=True):
        df = st.session_state.df_editavel
        markup = st.session_state.get("markup", config.MARKUP_DEFAULT)
        _gerar_excel(df, markup=markup, selecoes=st.session_state.selecoes)


# === Roteador ===
TELAS = {
    "home": tela_home,
    "upload": tela_upload,
    "revisao": tela_revisao,
    "busca_precos": tela_busca_precos,
    "configuracoes": tela_configuracoes,
}

TELAS[st.session_state.tela]()
