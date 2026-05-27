"""
UI Streamlit — interface para usuário leigo.

Filosofia:
- Sem jargão técnico em nenhum botão ou label
- Tudo em português
- Cada tela cabe sem rolar (na medida do possível)
- Cores e estados visuais claros (verde = ok, amarelo = conferir, vermelho = problema)

A camada visual (CSS, badges, headers, cards) mora em ``pregoes/ui/style.py``.
Este arquivo cuida apenas de **roteamento, estado e composição** das telas —
não escreve HTML cru nem repete cores hex.
"""
from pathlib import Path
import tempfile

import streamlit as st
import pandas as pd

from pregoes import config, credenciais, mapeamento_manual
from pregoes.extrator import extrair_com_pipeline
from pregoes.exportador import exportar
from pregoes.db import init_db, get_conn
from pregoes.ui import style


# === Configuração da página ===
st.set_page_config(
    page_title=config.TITULO_APP,
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)
style.aplicar_estilo()

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


# === Sidebar fixa ===
def _renderizar_sidebar():
    """Navegação global + status do operador. Sempre visível."""
    with st.sidebar:
        style.sidebar_marca()

        # Navegação principal — botão "ativo" via type='primary'
        tela_atual = st.session_state.tela
        nav_items = [
            ("home",          "Início"),
            ("upload",        "Novo pregão"),
            ("configuracoes", "Configurações"),
        ]
        # Telas de meio-de-fluxo não viram itens de menu, mas mantêm a navegação
        # consistente sem botão "ativo" (revisão, busca_precos, mapeamento).
        for tela_id, rotulo in nav_items:
            ativo = tela_atual == tela_id or (
                tela_id == "upload" and tela_atual in {"revisao", "mapeamento", "busca_precos"}
            )
            if st.button(
                rotulo,
                key=f"nav_{tela_id}",
                use_container_width=True,
                type="primary" if ativo else "secondary",
            ):
                # "Novo pregão" pelo menu sempre limpa estado (mesma intenção do
                # botão antigo na home).
                if tela_id == "upload":
                    for k in ("extracao", "df_editavel", "resultados_busca",
                              "selecoes", "pagina_busca", "itens_revisados",
                              "usando_tabelas_docling", "mapeamento_tabela_idx"):
                        st.session_state.pop(k, None)
                ir_para(tela_id)

        st.markdown("###### Status", unsafe_allow_html=True)
        cred_almeida = credenciais.carregar("almeida")
        cred_dantas = credenciais.carregar("dantas")
        cred_gemini = credenciais.carregar_chave_api("gemini")
        almeida_status = ("Configurada", "success") if cred_almeida else ("Pendente", "warning")
        dantas_status = ("Configurada", "success") if cred_dantas else ("Pendente", "warning")
        gemini_status = ("Configurada", "success") if cred_gemini else ("Opcional", "neutro")
        style.sidebar_status([
            ("Almeida", almeida_status[0], almeida_status[1]),
            ("Dantas", dantas_status[0], dantas_status[1]),
            ("IA (Gemini)", gemini_status[0], gemini_status[1]),
        ])



# === Tela: HOME ===
def tela_home():
    style.header_pagina(
        "Início",
        "Visão geral dos pregões processados e atalho para um novo.",
    )

    # KPIs simples — contagem total e por status relevante para o operador.
    # Usa COUNT direto no SQL (mais barato que carregar todas as linhas).
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM editais").fetchone()[0]
        contagens = dict(conn.execute("""
            SELECT status, COUNT(*) FROM editais GROUP BY status
        """).fetchall())

    c1, c2, c3 = st.columns(3)
    c1.metric("Pregões registrados", total)
    c2.metric("Em rascunho", contagens.get("rascunho", 0))
    c3.metric("Precificados", contagens.get("precificado", 0))

    # Atalho principal — sidebar tem o mesmo, mas o CTA principal aqui ajuda
    # quem chega na tela home e quer começar imediatamente.
    st.write("")
    if st.button("Iniciar novo pregão", type="primary"):
        for k in ("extracao", "df_editavel", "resultados_busca",
                  "selecoes", "pagina_busca", "itens_revisados",
                  "usando_tabelas_docling", "mapeamento_tabela_idx"):
            st.session_state.pop(k, None)
        ir_para("upload")


# === Tela: UPLOAD ===
def tela_upload():
    style.header_pagina(
        "Novo pregão",
        "Envie o PDF do edital para extração automática dos itens.",
    )

    arquivo = st.file_uploader(
        "Arraste o PDF aqui ou clique para escolher",
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
                resultado = extrair_com_pipeline(tmp_path)
            except Exception as exc:
                style.bloco_aviso(f"Falha ao ler PDF: {exc}", tipo="danger")
                return

        # Pipeline automático ficou abaixo do limite? Tenta mapas aprendidos
        # de PDFs anteriores com a mesma assinatura de cabeçalho.
        if resultado.confianca_global < 0.95 and resultado.tabelas_brutas:
            with get_conn() as conn:
                for idx, tab in enumerate(resultado.tabelas_brutas):
                    if not tab or not tab[0]:
                        continue
                    sig = mapeamento_manual.assinatura_cabecalho(tab[0])
                    mapa_salvo = mapeamento_manual.carregar_mapa(conn, sig)
                    if not mapa_salvo:
                        continue
                    novo = mapeamento_manual.aplicar_mapa(
                        tab, mapa_salvo,
                        arquivo=str(tmp_path),
                        pular_cabecalho=True,
                    )
                    if novo.confianca_global > resultado.confianca_global:
                        novo.tabelas_brutas = resultado.tabelas_brutas
                        novo.avisos.insert(
                            0,
                            f"✨ Mapa aprendido reusado (tabela #{idx + 1}, "
                            f"cabeçalho conhecido)."
                        )
                        resultado = novo
                        break

        st.session_state.extracao = resultado
        # Reset do df_editavel: sem isto, uma extração nova reaproveita o
        # DataFrame em cache da extração anterior e o usuário vê itens
        # desatualizados na revisão (o data_editor não repopula sozinho).
        st.session_state.df_editavel = None
        st.session_state.pop("usando_tabelas_docling", None)
        st.session_state.pop("mapeamento_tabela_idx", None)
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

    style.header_pagina(
        "Revisão dos itens extraídos",
        "Confira o que foi lido do PDF, edite o que precisar e marque o que entra na planilha.",
    )

    # Badge de confiança da extração — semáforo para o operador entender
    # rapidamente se pode confiar no resultado ou precisa revisar com lupa.
    conf = res.confianca_global
    rotulo_conf, tipo_conf = style.confianca_para_badge(conf)
    style.bloco_aviso(
        {
            "success": f"<b>{rotulo_conf}</b> — pode seguir para precificar.",
            "warning": f"<b>{rotulo_conf}</b> — revise com atenção antes de gerar a planilha.",
            "danger":  f"<b>{rotulo_conf}</b> — recomendado usar o mapeamento manual.",
        }[tipo_conf],
        tipo=tipo_conf,
    )

    # Indicadores
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total de itens", res.total_itens)
    c2.metric("OK", res.total_itens - res.total_duvidosos)
    c3.metric("Duvidosos", res.total_duvidosos,
              delta="conferir" if res.total_duvidosos else None,
              delta_color="off")
    c4.metric("Tabelas detectadas", len(res.tabelas_brutas))

    if res.avisos:
        style.bloco_avisos(res.avisos, tipo="warning")

    # Quando a confiança automática não é alta o bastante OU não saiu item
    # algum, oferece mapeamento manual. O operador escolhe a tabela bruta
    # certa e indica o que é cada coluna — o sistema aprende e usa em
    # próximos editais com mesmo cabeçalho.
    if conf < 0.95 and res.tabelas_brutas:
        style.bloco_aviso(
            "A confiança automática ficou abaixo de 95%. Você pode usar o "
            "<b>Mapeamento Manual</b> para indicar qual é a tabela de itens "
            "e o que é cada coluna. O sistema aprende e reaproveita o "
            "mapeamento em próximos editais com o mesmo formato.",
            tipo="info",
        )
        if st.button("Mapeamento Manual"):
            ir_para("mapeamento")

    if res.total_itens == 0:
        style.bloco_aviso(
            "Nenhum item foi extraído automaticamente. Se o PDF tem uma "
            "tabela de itens visível, use o <b>Mapeamento Manual</b> acima. "
            "Se o PDF está escaneado (imagem), precisa OCR — avise o time.",
            tipo="danger",
        )
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

    # Botões de marcação em massa: incrementam `editor_version` para
    # forçar Streamlit a recriar o data_editor com novos valores de OK?.
    # Sem essa key dinâmica, o widget cacheia o estado anterior e os
    # cliques individuais ficavam inconsistentes (bug reportado pelo
    # operador, 2026-05-27).
    if "editor_version" not in st.session_state:
        st.session_state.editor_version = 0

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("Marcar todos", use_container_width=True):
            st.session_state.df_editavel["OK"] = True
            st.session_state.editor_version += 1
            st.rerun()
    with bcol2:
        if st.button("Desmarcar todos", use_container_width=True):
            st.session_state.df_editavel["OK"] = False
            st.session_state.editor_version += 1
            st.rerun()

    # `num_rows="fixed"` (default) em vez de "dynamic": evita que o
    # data_editor crie linhas vazias quando o usuário clica em uma área
    # vazia — comportamento que confundia o checkbox individual.
    #
    # ATENÇÃO: NÃO atribuir `st.session_state.df_editavel = edited` aqui!
    # Isso causa race condition em cliques rápidos sucessivos. O widget
    # Streamlit já mantém o estado de edição via `key`, então `edited`
    # sempre reflete os checkboxes atuais. df_editavel é só a *snapshot
    # inicial* — atualizada apenas em "Marcar/Desmarcar todos" ou ao
    # carregar nova extração.
    edited = st.data_editor(
        st.session_state.df_editavel,
        use_container_width=True,
        height=500,
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

    # Contador DEPOIS do data_editor — agora vê o estado atual via `edited`,
    # que é a única fonte de verdade dos checkboxes (não tocamos em
    # df_editavel para evitar race conditions).
    n_marcados = int(edited["OK"].sum())
    st.caption(f"**{n_marcados}** de **{len(edited)}** itens marcados.")

    st.divider()

    # Markup em linha própria — antes ficava espremido na mesma linha
    # dos botões e o componente "- + 1,45" ficava maior que os botões,
    # quebrando o alinhamento (reportado pelo operador, 2026-05-27).
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
    st.session_state.markup = markup
    st.session_state.itens_revisados = _df_para_itens(edited)

    # Botões em linha própria, com proporções iguais.
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("Buscar preços", type="primary", use_container_width=True,
                     help="Consulta Almeida e Dantas (qualquer um configurado). Pode demorar minutos."):
            if not st.session_state.itens_revisados:
                style.bloco_aviso("Nenhum item marcado como OK — nada para buscar.", tipo="danger")
            elif (credenciais.carregar("almeida") is None
                  and credenciais.carregar("dantas") is None):
                style.bloco_aviso(
                    "Nenhum fornecedor configurado. Acesse <b>Configurações</b> "
                    "para cadastrar Almeida e/ou Dantas.",
                    tipo="danger",
                )
            else:
                ir_para("busca_precos")

    with col_btn2:
        if st.button("Gerar sem buscar", use_container_width=True,
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
        style.bloco_aviso("Nenhum item marcado como OK — nada para exportar.", tipo="danger")
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
            style.bloco_aviso(f"Falha ao gerar planilha: {exc}", tipo="danger")
            return

        # Persistir edital + itens no banco (Fase 2: itens vinculados às seleções)
        _persistir_edital(itens, selecoes)

    style.bloco_aviso(f"Planilha gerada: <code>{destino.name}</code>", tipo="success")
    if selecoes:
        st.caption(f"Inclui preço/marca/código para {len(selecoes)} de {len(itens)} itens.")
    with open(destino, "rb") as f:
        st.download_button(
            "Baixar planilha matriz",
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
    style.header_pagina(
        "Configurações",
        "Credenciais dos fornecedores. Tudo fica salvo apenas no seu computador "
        "(arquivo <code>data/credenciais.json</code>), sem envio para servidores externos.",
    )

    aba_almeida, aba_dantas, aba_ia = st.tabs(["Almeida", "Dantas", "IA (Gemini)"])

    with aba_almeida:
        atual = credenciais.carregar("almeida") or {"email": "", "senha": ""}
        if atual.get("email"):
            style.bloco_aviso(
                f"Credencial ativa: <code>{atual['email']}</code> (senha oculta).",
                tipo="success",
            )
        else:
            style.bloco_aviso(
                "Nenhuma credencial salva. A busca de preços não funciona até configurar.",
                tipo="warning",
            )

        with st.form("form_almeida"):
            email = st.text_input("E-mail", value=atual.get("email", ""))
            senha = st.text_input("Senha", value=atual.get("senha", ""), type="password")
            salvar_btn = st.form_submit_button("Salvar credenciais", type="primary")
            if salvar_btn:
                if not email or not senha:
                    style.bloco_aviso("Preencha e-mail e senha antes de salvar.", tipo="danger")
                else:
                    credenciais.salvar("almeida", email.strip(), senha)
                    style.bloco_aviso(
                        "Credenciais da Almeida salvas com sucesso.", tipo="success",
                    )

    with aba_dantas:
        atual_dantas = credenciais.carregar("dantas") or {"email": "", "senha": ""}
        if atual_dantas.get("email"):
            style.bloco_aviso(
                f"Credencial ativa: <code>{atual_dantas['email']}</code> (senha oculta).",
                tipo="success",
            )
        else:
            style.bloco_aviso(
                "Nenhuma credencial salva. A busca não inclui Dantas até configurar.",
                tipo="warning",
            )
            # Atalho útil: muitos operadores usam a mesma conta nas duas
            cred_almeida_existente = credenciais.carregar("almeida")
            if cred_almeida_existente:
                if st.button("📋 Usar as mesmas credenciais da Almeida",
                             key="copia_almeida_dantas"):
                    credenciais.salvar(
                        "dantas",
                        cred_almeida_existente["email"],
                        cred_almeida_existente["senha"],
                    )
                    st.rerun()

        with st.form("form_dantas"):
            email_d = st.text_input("E-mail", value=atual_dantas.get("email", ""),
                                    key="dantas_email")
            senha_d = st.text_input("Senha", value=atual_dantas.get("senha", ""),
                                    type="password", key="dantas_senha")
            salvar_d = st.form_submit_button("Salvar credenciais", type="primary")
            if salvar_d:
                if not email_d or not senha_d:
                    style.bloco_aviso("Preencha e-mail e senha antes de salvar.",
                                      tipo="danger")
                else:
                    credenciais.salvar("dantas", email_d.strip(), senha_d)
                    style.bloco_aviso(
                        "Credenciais da Dantas salvas com sucesso.", tipo="success",
                    )

    with aba_ia:
        gemini_key = credenciais.carregar_chave_api("gemini") or ""
        if gemini_key:
            style.bloco_aviso(
                f"Chave configurada: <code>{gemini_key[:8]}…{gemini_key[-4:]}</code>. "
                "A IA roda automaticamente quando a extração rápida ficar abaixo "
                "de 95%.",
                tipo="success",
            )
        else:
            style.bloco_aviso(
                "Sem chave configurada. A IA fica desligada — o sistema usa "
                "só extração rápida + mapeamento manual.",
                tipo="warning",
            )

        st.markdown(
            "**Como obter a chave gratuita (5min):**\n"
            "1. Acesse https://aistudio.google.com\n"
            "2. Faça login com sua conta Google\n"
            "3. Clique em **Get API key** → **Create API key**\n"
            "4. Copie a chave e cole abaixo\n\n"
            "**Custo:** *Gratuito* dentro do free tier do Google "
            "(1500 requisições/dia — folga ampla para 10–30 editais/mês)."
        )

        with st.form("form_gemini"):
            chave_input = st.text_input(
                "Chave de API (Gemini)",
                value=gemini_key,
                type="password",
                help="Começa com 'AIza...'. Salva apenas no seu computador.",
            )
            col_a, col_b = st.columns(2)
            with col_a:
                salvar_g = st.form_submit_button("Salvar chave", type="primary")
            with col_b:
                testar_g = st.form_submit_button("Salvar e testar")

            if salvar_g or testar_g:
                if not chave_input.strip():
                    style.bloco_aviso("Cole a chave antes de salvar.", tipo="danger")
                elif not chave_input.startswith("AIza"):
                    style.bloco_aviso(
                        "Chave parece inválida (esperado começar com <code>AIza</code>). "
                        "Salva mesmo assim, mas verifique.",
                        tipo="warning",
                    )
                    credenciais.salvar_chave_api("gemini", chave_input.strip())
                else:
                    credenciais.salvar_chave_api("gemini", chave_input.strip())
                    style.bloco_aviso(
                        "Chave Gemini salva com sucesso.", tipo="success",
                    )

                if testar_g and chave_input.strip():
                    with st.spinner("Testando chave…"):
                        try:
                            from google import genai
                            client = genai.Client(api_key=chave_input.strip())
                            resp = client.models.generate_content(
                                model="gemini-2.0-flash",
                                contents="Responda apenas 'OK'.",
                            )
                            if resp.text and "OK" in resp.text.upper():
                                style.bloco_aviso(
                                    "Conexão com Gemini funcionando.",
                                    tipo="success",
                                )
                            else:
                                style.bloco_aviso(
                                    f"Resposta inesperada: {resp.text[:80] if resp.text else '(vazio)'}",
                                    tipo="warning",
                                )
                        except Exception as exc:
                            style.bloco_aviso(
                                f"Falha ao chamar Gemini: {type(exc).__name__}: {exc}",
                                tipo="danger",
                            )


# === Tela: BUSCA DE PREÇOS ===
def tela_busca_precos():
    style.header_pagina(
        "Busca de preços nos fornecedores",
        "Consulta cada item nos fornecedores configurados (Almeida e/ou Dantas) e mostra as marcas "
        "disponíveis. Você escolhe qual marca entra na planilha.",
    )

    itens = st.session_state.get("itens_revisados") or []
    if not itens:
        style.bloco_aviso(
            "Nenhum item para buscar. Volte para a revisão.", tipo="danger"
        )
        if st.button("Voltar para revisão"):
            ir_para("revisao")
        return

    fornecedores_disponiveis = [
        nome for nome in ("almeida", "dantas")
        if credenciais.carregar(nome) is not None
    ]
    if not fornecedores_disponiveis:
        style.bloco_aviso(
            "Nenhum fornecedor configurado. Vá em <b>Configurações</b> "
            "para cadastrar Almeida e/ou Dantas.",
            tipo="danger",
        )
        return

    # Estimativa: ~1,5s por item por fornecedor, com pool 4 paralelizando
    # ≈ rate limiter 2 req/s → ~0,5s por item efetivo por fornecedor.
    n_forn = len(fornecedores_disponiveis)
    minutos_est = len(itens) * 0.5 * n_forn / 60
    fornec_str = " + ".join(f.capitalize() for f in fornecedores_disponiveis)
    st.caption(
        f"{len(itens)} itens serão buscados em {fornec_str}. "
        f"Estimativa: ~{minutos_est:.0f} min nesta lista (com paralelismo)."
    )

    # Se ainda não buscou, oferecer botão para começar
    if "resultados_busca" not in st.session_state:
        col_a, col_b = st.columns([1, 5])
        with col_a:
            if st.button("Iniciar busca", type="primary"):
                _executar_busca(itens, fornecedores_disponiveis)
                st.rerun()
        with col_b:
            if st.button("Voltar para revisão"):
                ir_para("revisao")
        return

    # Já buscou: mostrar resultados + seleção
    _mostrar_selecao(itens, st.session_state.resultados_busca)


def _executar_busca(itens: list, fornecedores_nomes: list[str]) -> None:
    """Roda a busca em cada fornecedor SEQUENCIALMENTE, abrindo e fechando
    o Playwright entre eles.

    Por que sequencial e não simultâneo: Playwright sync API permite
    apenas UMA instância por thread. Abrir Almeida + Dantas ao mesmo
    tempo (via ExitStack) falha com erro do tipo 'sync API inside the
    asyncio loop' já na 2ª chamada. Bug reportado em produção
    (operador, vídeo de 2026-05-27).

    Os resultados de cada fornecedor são concatenados — cada item do
    edital pode acabar com opções de Almeida E de Dantas, e a UI mostra
    todas para o operador escolher (default: mais barato).
    """
    from pregoes.scraper import (
        AlmeidaDistribuidor,
        DantasDistribuidora,
        ResultadoLote,
        buscar_precos_para_itens,
    )

    classes = {"almeida": AlmeidaDistribuidor, "dantas": DantasDistribuidora}

    progress = st.progress(0.0, text="Iniciando…")
    status_box = st.empty()

    # Acumula resultados de cada fornecedor
    resultados_agregados: dict[int, list] = {idx: [] for idx in range(len(itens))}
    erros_agregados: list = []
    cache_hits_total = 0
    cache_misses_total = 0

    n_forn = len(fornecedores_nomes)

    for forn_idx, nome in enumerate(fornecedores_nomes):
        cred = credenciais.carregar(nome)
        if cred is None:
            continue
        cls = classes[nome]

        progress.progress(
            forn_idx / max(n_forn, 1),
            text=f"Fazendo login em {nome.capitalize()} "
                 f"({forn_idx + 1}/{n_forn})…"
        )

        # callback de progresso DENTRO desse fornecedor — composto:
        # avança proporcional ao total de fornecedores
        def cb(atual, total, msg, _forn_idx=forn_idx, _nome=nome):
            frac_dentro = atual / max(total, 1)
            frac_total = (_forn_idx + frac_dentro) / max(n_forn, 1)
            progress.progress(
                frac_total,
                text=f"{_nome.capitalize()} {atual}/{total} — {msg}"
            )

        try:
            with cls(cred["email"], cred["senha"]) as forn:
                lote = buscar_precos_para_itens(
                    itens, [forn], progresso_callback=cb,
                )
        except Exception as exc:
            # Falha grave (login, layout mudou) — registra como erro
            # global mas segue para o próximo fornecedor.
            from pregoes.scraper import ErroBusca
            erros_agregados.append(ErroBusca(
                indice_item=-1,
                fornecedor=nome,
                descricao_resumo="(login/inicialização)",
                mensagem=f"{type(exc).__name__}: {exc}",
            ))
            continue

        # Mescla resultados do fornecedor no agregado
        for idx, bag in lote.resultados.items():
            resultados_agregados[idx].extend(bag)
        erros_agregados.extend(lote.erros)
        cache_hits_total += lote.cache_hits
        cache_misses_total += lote.cache_misses

    # Empacota como ResultadoLote para preservar API que a UI espera
    lote = ResultadoLote(
        resultados=resultados_agregados,
        erros=erros_agregados,
        cache_hits=cache_hits_total,
        cache_misses=cache_misses_total,
    )

    progress.progress(1.0, text="Busca concluída.")

    # Compatível com UI anterior (dict de resultados) + dados extras
    st.session_state.resultados_busca = lote.resultados
    st.session_state.erros_busca = lote.erros
    st.session_state.cache_stats = (lote.cache_hits, lote.cache_misses)
    with status_box.container():
        hits, misses = lote.cache_hits, lote.cache_misses
        extras = []
        if hits or misses:
            extras.append(f"{hits} do cache, {misses} buscas reais")
        if lote.erros:
            extras.append(f"⚠️ {len(lote.erros)} erro(s) — ver painel abaixo")
        sub = f" ({' · '.join(extras)})" if extras else ""
        style.bloco_aviso(
            f"Busca concluída — {len(lote.resultados)} itens processados{sub}.",
            tipo="success",
        )


def _mostrar_selecao(itens: list, resultados: dict) -> None:
    """Tabela paginada com selectbox por linha. Operador escolhe a marca.

    Default de seleção = MENOR preço entre todas as opções de todos os
    fornecedores. Atende o caso comum ("quero o mais barato") sem violar
    o princípio do briefing (CLAUDE.md §7) — o sistema apresenta todas
    as opções e o operador pode trocar quando precisar marca específica.
    """
    from pregoes.exportador import SelecaoFornecedor

    # Inicializa estado de seleções com a opção de MENOR preço por item
    if "selecoes" not in st.session_state:
        st.session_state.selecoes = {}
        for idx, opcoes in resultados.items():
            if opcoes:
                mais_barato = min(opcoes, key=lambda o: o.preco)
                st.session_state.selecoes[idx] = SelecaoFornecedor(
                    preco=mais_barato.preco,
                    codigo_fornecedor=mais_barato.codigo_fornecedor,
                    marca=mais_barato.marca,
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
    c2.metric("Com preço", com_resultado - com_variacao,
              help="Casamento exato com a descrição do edital")
    c3.metric("Por variação", com_variacao,
              help="Encontrado, mas por busca aproximada — conferir")
    c4.metric("Sem resultado", sem_resultado)

    # Painel de erros do batch — só aparece quando há falhas técnicas.
    # Não bloqueia o fluxo: o operador pode prosseguir com o que deu certo.
    erros_busca = st.session_state.get("erros_busca") or []
    if erros_busca:
        with st.expander(f"⚠️ Ver erros técnicos ({len(erros_busca)})"):
            st.caption(
                "Erros isolados durante a busca — esses itens ficaram sem "
                "preço pelo fornecedor que falhou. Verifique se o site mudou "
                "de layout ou se a sessão expirou."
            )
            for err in erros_busca[:50]:
                st.markdown(
                    f"- Item **#{err.indice_item + 1}** ({err.fornecedor}) — "
                    f"`{err.descricao_resumo}`: {err.mensagem}"
                )
            if len(erros_busca) > 50:
                st.caption(f"... e mais {len(erros_busca) - 50} erros.")

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
            label_status = "Não encontrado"
            tipo_status: style.TipoBadge = "danger"
        elif any(o.confianca == "variacao" for o in opcoes):
            label_status = f"{len(opcoes)} por variação"
            tipo_status = "warning"
        else:
            label_status = f"{len(opcoes)} encontrado(s)"
            tipo_status = "success"

        with st.expander(
            f"#{item.item_numero or idx + 1} · {item.descricao[:80]}  —  {label_status}",
            expanded=False,
        ):
            if not opcoes:
                style.bloco_aviso(
                    "Nenhum resultado nos fornecedores. Coluna L ficará em branco.",
                    tipo="warning",
                )
                continue

            # Ordena opções por preço ascendente — operador vê o mais barato
            # no topo e o sistema marca explicitamente qual é o melhor preço.
            opcoes_ordenadas = sorted(opcoes, key=lambda o: o.preco)
            preco_min = opcoes_ordenadas[0].preco

            def _label(o):
                tag_min = " 💰 mais barato" if o.preco == preco_min else ""
                return (
                    f"{o.marca} — R$ {o.preco:.2f} ({o.fornecedor})"
                    f"{tag_min} — código {o.codigo_fornecedor} — "
                    f"{o.descricao_encontrada[:50]}"
                )

            labels = ["(não usar)"] + [_label(o) for o in opcoes_ordenadas]
            default_idx = 0
            if sel_atual is not None:
                # Encontra a opção que casa com a seleção atual
                for i, o in enumerate(opcoes_ordenadas):
                    if (o.preco == sel_atual.preco and o.marca == sel_atual.marca
                            and o.codigo_fornecedor == sel_atual.codigo_fornecedor):
                        default_idx = i + 1
                        break

            escolha = st.selectbox(
                "Marca / preço (default = mais barato)",
                options=range(len(labels)),
                format_func=lambda i: labels[i],
                index=default_idx,
                key=f"sel_{idx}",
            )
            # Substitui a lista usada abaixo para já estar ordenada
            opcoes = opcoes_ordenadas
            if escolha == 0:
                st.session_state.selecoes.pop(idx, None)
            else:
                op = opcoes[escolha - 1]
                st.session_state.selecoes[idx] = SelecaoFornecedor(
                    preco=op.preco, codigo_fornecedor=op.codigo_fornecedor, marca=op.marca,
                )

    st.divider()
    st.caption(f"{selecionados} de {total} itens com marca/preço selecionados.")

    if st.button("Gerar planilha matriz com preços", type="primary",
                 use_container_width=True):
        df = st.session_state.df_editavel
        markup = st.session_state.get("markup", config.MARKUP_DEFAULT)
        _gerar_excel(df, markup=markup, selecoes=st.session_state.selecoes)


# === Tela: MAPEAMENTO MANUAL ===
def tela_mapeamento():
    from pregoes import mapeamento_manual

    res = st.session_state.extracao
    if res is None or not res.tabelas_brutas:
        style.bloco_aviso(
            "Nada para mapear — volte e envie um PDF primeiro.", tipo="danger",
        )
        if st.button("Voltar para o início"):
            ir_para("home")
        return

    style.header_pagina(
        "Mapeamento manual da tabela de itens",
        "Quando a extração automática não acerta, você indica qual é a tabela "
        "certa e o que é cada coluna. O sistema aprende para os próximos editais.",
    )

    if st.button("Voltar para revisão"):
        ir_para("revisao")

    # === Opcional: recarregar tabelas com Docling (mais preciso, mais lento) ===
    from pregoes import extrator_docling
    if extrator_docling.disponivel() and not st.session_state.get("usando_tabelas_docling"):
        with st.expander("Não encontrou a tabela certa? Carregar com extração profunda (Docling)"):
            st.caption(
                "Docling usa um modelo de visão para detectar tabelas que o "
                "pdfplumber não viu. Funciona melhor em PDFs com layout "
                "complexo, mas demora **5-10 minutos** por edital. Use só "
                "quando a tabela certa não está na lista abaixo."
            )
            pdf_path = st.session_state.get("pdf_path")
            if pdf_path and st.button("Carregar tabelas com Docling"):
                with st.spinner("Rodando Docling… pode demorar alguns minutos."):
                    try:
                        tabs = extrator_docling.coletar_tabelas_brutas_docling(pdf_path)
                    except Exception as exc:
                        style.bloco_aviso(f"Docling falhou: {exc}", tipo="danger")
                        return
                # Substitui as tabelas brutas — operador escolhe entre as do Docling
                res.tabelas_brutas = tabs
                st.session_state.extracao = res
                st.session_state.usando_tabelas_docling = True
                st.session_state.pop("mapeamento_tabela_idx", None)
                style.bloco_aviso(
                    f"Docling detectou {len(tabs)} tabelas. Escolha abaixo.",
                    tipo="success",
                )
                st.rerun()
    elif st.session_state.get("usando_tabelas_docling"):
        style.bloco_aviso(
            "Mostrando tabelas detectadas pelo Docling (extração profunda).",
            tipo="info",
        )

    # === Passo 1 ===
    st.subheader("Passo 1 — Qual das tabelas é a de itens?")

    # Mostra preview de cada tabela: cabeçalho + 1 linha de dados
    opcoes_label = []
    for idx, tab in enumerate(res.tabelas_brutas):
        if not tab:
            opcoes_label.append(f"Tabela #{idx + 1} (vazia)")
            continue
        n_cols = max(len(r) for r in tab if r) if tab else 0
        n_linhas = len(tab)
        # Pré-visualização: primeira linha não vazia
        primeira = next((r for r in tab if r and any(c for c in r)), [])
        preview = " | ".join(str(c)[:25] for c in primeira[:6] if c)[:120]
        opcoes_label.append(f"Tabela #{idx + 1} · {n_cols} colunas · {n_linhas} linhas · {preview}")

    idx_tabela = st.selectbox(
        "Tabelas detectadas no PDF",
        options=range(len(res.tabelas_brutas)),
        format_func=lambda i: opcoes_label[i],
        key="mapeamento_tabela_idx",
    )
    tabela_alvo = res.tabelas_brutas[idx_tabela]

    if not tabela_alvo:
        st.warning("Tabela vazia — escolha outra.")
        return

    n_cols_tabela = max(len(r) for r in tabela_alvo if r)

    # Pré-visualização da tabela escolhida
    st.caption(f"**Pré-visualização da Tabela #{idx_tabela + 1}** (primeiras 5 linhas):")
    df_preview = pd.DataFrame(
        [(linha + [""] * n_cols_tabela)[:n_cols_tabela] for linha in tabela_alvo[:5]],
        columns=[f"Col {i+1}" for i in range(n_cols_tabela)],
    )
    st.dataframe(df_preview, use_container_width=True, hide_index=True)

    # === Passo 2 ===
    st.subheader("Passo 2 — O que é cada coluna?")
    st.caption(
        "A primeira linha do PDF acima é o cabeçalho — não vai virar item. "
        "Para cada coluna, escolha o campo correspondente."
    )

    # Tenta puxar mapa salvo para esta assinatura. Se houver, usa como default.
    assinatura = mapeamento_manual.assinatura_cabecalho(tabela_alvo[0])
    with get_conn() as conn:
        mapa_salvo = mapeamento_manual.carregar_mapa(conn, assinatura)

    if mapa_salvo:
        style.bloco_aviso(
            "Encontrei um mapa salvo desta assinatura de cabeçalho — "
            "os campos abaixo já vieram preenchidos. Ajuste se precisar.",
            tipo="success",
        )

    mapa_atual: dict[str, int] = {}
    cabecalho = tabela_alvo[0] if tabela_alvo else []
    cols_ui = st.columns(min(n_cols_tabela, 3))
    for c in range(n_cols_tabela):
        label_cab = str(cabecalho[c])[:30] if c < len(cabecalho) else f"Col {c+1}"
        # Default: se o mapa salvo aponta essa coluna pra algum campo, usa
        default_campo = "ignorar"
        if mapa_salvo:
            for campo, idx_salvo in mapa_salvo.items():
                if idx_salvo == c:
                    default_campo = campo
                    break

        with cols_ui[c % len(cols_ui)]:
            campo = st.selectbox(
                f"Coluna {c + 1} — `{label_cab}`",
                options=mapeamento_manual.CAMPOS,
                format_func=lambda c=None: mapeamento_manual.ROTULOS_CAMPOS[c] if c else "",
                index=mapeamento_manual.CAMPOS.index(default_campo),
                key=f"map_col_{c}",
            )
            if campo != "ignorar":
                if campo in mapa_atual:
                    st.warning(f"⚠️ '{mapeamento_manual.ROTULOS_CAMPOS[campo]}' "
                               f"já está em outra coluna.")
                mapa_atual[campo] = c

    # === Passo 3 ===
    st.divider()
    st.subheader("Passo 3 — Validar e aplicar")

    obrigatorios = ["descricao", "quantidade"]
    faltando = [mapeamento_manual.ROTULOS_CAMPOS[c]
                for c in obrigatorios if c not in mapa_atual]
    if faltando:
        style.bloco_aviso(
            f"Mapeamento incompleto — faltam: <b>{', '.join(faltando)}</b>.",
            tipo="danger",
        )
        return

    salvar_mapa_check = st.checkbox(
        "Salvar este mapeamento para reuso automático em próximos editais "
        "com o mesmo formato",
        value=True,
    )

    if st.button("Aplicar mapeamento", type="primary"):
        # Aplicar em LOTE: pega a tabela escolhida + todas as fatias
        # continuação que vêm com ela (tabelas multi-página comuns em
        # editais grandes). aplicar_mapa_em_lote detecta as irmãs pelo
        # cabeçalho repetido OU cabeçalho fake/vazio.
        novo_res = mapeamento_manual.aplicar_mapa_em_lote(
            res.tabelas_brutas, idx_tabela, mapa_atual,
            arquivo=res.arquivo,
        )

        if salvar_mapa_check:
            with get_conn() as conn:
                mapeamento_manual.salvar_mapa(conn, assinatura, mapa_atual)
            style.bloco_aviso(
                f"Mapeamento salvo. Próximos PDFs com este cabeçalho usam "
                f"automaticamente. Itens extraídos: <b>{novo_res.total_itens}</b>, "
                f"confiança: <b>{novo_res.confianca_global:.0%}</b>.",
                tipo="success",
            )

        st.session_state.extracao = novo_res
        st.session_state.df_editavel = None  # força refresh do data_editor
        ir_para("revisao")


# === Roteador ===
TELAS = {
    "home": tela_home,
    "upload": tela_upload,
    "revisao": tela_revisao,
    "mapeamento": tela_mapeamento,
    "busca_precos": tela_busca_precos,
    "configuracoes": tela_configuracoes,
}

_renderizar_sidebar()
TELAS[st.session_state.tela]()
