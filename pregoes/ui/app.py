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
import html
import os
import re
import tempfile

import streamlit as st
import pandas as pd

from pregoes import config, credenciais, erros, mapeamento_manual
from pregoes.ranking import bonus_codigo as _bonus_codigo
from pregoes.extrator import extrair_com_pipeline
from pregoes.exportador import exportar
from pregoes.db import init_db, get_conn
from pregoes.ui import style

# Modo de desenvolvimento: expõe controles técnicos (diagnóstico, limpar
# cache) que o operador final NÃO deve ver. Ative com PREGOES_DEBUG=1.
_DEBUG = bool(os.environ.get("PREGOES_DEBUG"))


# === Configuração da página ===
st.set_page_config(
    page_title=config.TITULO_APP,
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)
style.aplicar_estilo()

# Logging em arquivo (data/pregoes.log) para diagnóstico remoto, e backup
# diário no boot — antes de qualquer escrita.
from pregoes import logconf, db as _db
logconf.configurar()
_log = logconf.get_logger("ui")
# Primeira linha de todo log: a versão. O suporte remoto lê o pregoes.log
# do cliente e precisa saber QUAL build gerou cada rastro.
_log.info("Pregões v%s iniciado", config.VERSAO_APP)

# Inicializa/migra o banco no primeiro load
init_db()

# Backup diário automático (1 por dia; rotação cuida do volume).
if "backup_feito" not in st.session_state:
    try:
        _db.backup("diario")
    except Exception:
        _log.exception("falha no backup diário de boot")
    st.session_state.backup_feito = True

# === Estado da sessão ===
if "tela" not in st.session_state:
    st.session_state.tela = "home"
if "extracao" not in st.session_state:
    st.session_state.extracao = None
if "df_editavel" not in st.session_state:
    st.session_state.df_editavel = None


# === Helpers puros (sem Streamlit) ===
# Colunas de DADOS do data_editor de revisão. A coluna "OK" (marcação) é
# tratada à parte: marcar/desmarcar todos só mexe nela, preservando o resto.
_COLUNAS_DADOS_REVISAO = ("Item", "Descrição", "Unidade", "Quantidade", "Valor referência")


def _fundir_edicoes(df_base, edited):
    """Funde as edições de célula do widget de volta no DataFrame base.

    Por que existe: o data_editor é alimentado por ``df_editavel`` (snapshot
    estática) e, deliberadamente, NÃO regravamos ``df_editavel = edited`` a
    cada rerun para não criar race em cliques rápidos. Mas "Marcar/Desmarcar
    todos" precisa mutar a snapshot — e, sem fundir antes, as correções que o
    operador fez nas células de dados (Descrição/Quantidade/Unidade/Valor)
    seriam perdidas. Esta fusão é pontual (só no clique do botão), então não
    reintroduz o race que o comentário do widget evita.

    Retorna uma cópia nova: não muta nenhum dos argumentos. Copia apenas as
    colunas de DADOS (não a coluna OK), alinhando pelo índice das linhas.
    """
    fundido = df_base.copy()
    if edited is None:
        return fundido
    for coluna in _COLUNAS_DADOS_REVISAO:
        if coluna in edited.columns and coluna in fundido.columns:
            # Alinha por índice; se o widget devolveu menos/mais linhas,
            # `reindex` mantém o shape do base e ignora extras com segurança.
            fundido[coluna] = edited[coluna].reindex(fundido.index)
    return fundido


def _aplicar_delta_editor(df_base, estado_editor):
    """Reconstrói o DataFrame editado a partir do delta do data_editor.

    O ``st.data_editor`` guarda no ``session_state[key]`` apenas um *delta*
    (``{"edited_rows": {pos: {coluna: valor}}, ...}``), não o DataFrame
    inteiro. Quando o operador clica "Marcar/Desmarcar todos", o data_editor
    ainda não rodou neste rerun — então lemos esse delta para recuperar as
    edições de célula pendentes e devolvê-las já aplicadas sobre ``df_base``.

    Retorna uma cópia nova; não muta ``df_base``. Ignora ``added_rows`` e
    ``deleted_rows`` porque o editor de revisão usa ``num_rows="fixed"``.
    """
    edited = df_base.copy()
    if not isinstance(estado_editor, dict):
        return edited
    edited_rows = estado_editor.get("edited_rows") or {}
    for pos, mudancas in edited_rows.items():
        try:
            idx = edited.index[int(pos)]
        except (ValueError, IndexError, KeyError):
            continue  # posição fora do range: delta obsoleto, ignora
        for coluna, valor in (mudancas or {}).items():
            if coluna in edited.columns:
                edited.at[idx, coluna] = valor
    return edited


def _sanitizar_nome_arquivo(nome: str) -> str:
    """Devolve um basename seguro e DETERMINÍSTICO para salvar em disco.

    Por que existe (idempotência da re-importação): o importador deriva a
    chave de dedup do basename (``fonte = "import:<basename>"``). Se cada
    upload virasse um nome aleatório (tempfile), re-enviar a MESMA planilha
    criaria uma fonte nova e duplicaria os preços — quebrando a promessa da
    UI ("re-enviar a mesma planilha não duplica nada"). Sanitizando o nome
    ORIGINAL, o mesmo arquivo gera sempre o mesmo path.name e a dedup funciona.

    Remove componentes de diretório (anti path-traversal) e troca tudo que
    não for ``[A-Za-z0-9._-]`` por ``_``. Garante a extensão ``.xlsx``.
    """
    import re

    # Só o último componente: derruba "../" e barras de qualquer sistema.
    base = nome.replace("\\", "/").split("/")[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    base = base.lstrip(".") or "planilha"  # evita nome oculto / vazio
    if not base.lower().endswith(".xlsx"):
        base = f"{base}.xlsx"
    return base


# === Helpers de navegação ===
def ir_para(tela: str):
    st.session_state.tela = tela
    st.rerun()


# Estado de sessão pertencente ao pregão em andamento (extração, revisão,
# busca, seleções). "Novo pregão" descarta tudo isto.
# markup_overrides é keyed por OFFSET do item (0..n) — vazar para o pregão
# seguinte aplicaria o markup ST do item de mesmo offset do pregão anterior,
# em silêncio (auditoria 2026-06, achado alto nº 6).
CHAVES_PREGAO = ("extracao", "df_editavel", "resultados_busca",
                 "selecoes", "pagina_busca", "itens_revisados",
                 "itens_edital", "mapa_busca_edital",
                 "usando_tabelas_docling", "mapeamento_tabela_idx",
                 "markup_overrides", "pregao_gerado_hash")

# Passos do fluxo principal — alimentam o stepper de progresso no topo das telas
# do fluxo (redesign UX 2026-06). "Planilha" é o passo-meta (concluído ao gerar).
PASSOS_FLUXO = ["Edital", "Itens", "Preços", "Planilha"]


def _limpar_pregao() -> None:
    """Descarta o estado do pregão atual da sessão."""
    for k in CHAVES_PREGAO:
        st.session_state.pop(k, None)


def _tem_pregao_em_andamento() -> bool:
    """Há trabalho que seria perdido ao começar um novo pregão?

    Sinais: um PDF já extraído, itens já revisados ou marcas/preços já
    escolhidos. (Não inspeciona `df_editavel` porque a verdade de um
    DataFrame é ambígua e levantaria exceção.)

    Exceção: se a planilha foi GERADA e NADA mudou desde então
    (`pregao_gerado_hash` == hash atual dos insumos), o trabalho está salvo
    (arquivo + histórico) — começar um novo não perde nada, então não avisamos.
    Sem isto, o operador via o modal "pregão em andamento" mesmo DEPOIS de
    concluir (feedback do cliente 2026-06). Se ele mexer em QUALQUER insumo
    (marca, preço, NCM, markup, revisão…), o hash muda e o aviso volta — sem
    precisar instrumentar cada ação (a revisão de QA achou 4 ações que ficavam
    de fora quando o re-arme era manual).
    """
    gerado = st.session_state.get("pregao_gerado_hash")
    if gerado is not None and gerado == _hash_pregao():
        return False
    if st.session_state.get("extracao") is not None:
        return True
    if st.session_state.get("itens_revisados"):
        return True
    if st.session_state.get("selecoes"):
        return True
    return False


def _tela_pregao_em_andamento() -> str:
    """A tela onde está o trabalho do pregão atual, para 'Continuar o atual'
    RETOMAR de onde parou (não basta fechar o modal — o operador pode ter
    aberto o diálogo a partir do Início e precisa voltar ao trabalho)."""
    ss = st.session_state
    if ss.get("resultados_busca") or ss.get("selecoes"):
        return "busca_precos"
    if ss.get("extracao") is not None or ss.get("itens_revisados"):
        return "revisao"
    return "upload"


@st.dialog("Começar um novo pregão?")
def _confirmar_novo_pregao() -> None:
    """Confirmação antes de descartar um pregão em andamento (P1.7).

    Evita que um clique acidental em 'Novo pregão' apague extração, revisão e
    seleções de marca já feitas — trabalho de minutos perdido em silêncio.

    Aberto no nível superior do script (não dentro da sidebar) via a flag
    `_confirmar_novo_pregao`, para o modal não renderizar dentro da sidebar.
    """
    st.warning("Você tem um pregão em andamento. Começar um novo vai descartar "
               "a extração, a revisão e as marcas/preços já escolhidos.")
    col_sim, col_nao = st.columns(2)
    if col_sim.button("Descartar e começar novo", type="primary",
                      width="stretch"):
        st.session_state.pop("_confirmar_novo_pregao", None)
        _limpar_pregao()
        ir_para("upload")          # ir_para já faz st.rerun()
    if col_nao.button("Continuar o atual", width="stretch"):
        st.session_state.pop("_confirmar_novo_pregao", None)
        # Retoma o trabalho em andamento (não só fecha o modal): leva à tela
        # onde o pregão está. ir_para já faz st.rerun() e, com a flag removida,
        # o diálogo não reabre.
        ir_para(_tela_pregao_em_andamento())


def _novo_pregao() -> None:
    """Inicia um novo pregão. Se há trabalho a perder, sinaliza para abrir o
    modal de confirmação (no nível superior); senão limpa direto e navega.
    Usado pelos atalhos da sidebar e da home."""
    if _tem_pregao_em_andamento():
        st.session_state["_confirmar_novo_pregao"] = True
        st.rerun()
    else:
        _limpar_pregao()
        ir_para("upload")


# === Sidebar fixa ===
def _renderizar_sidebar():
    """Navegação global + status do operador. Sempre visível."""
    with st.sidebar:
        style.sidebar_marca()

        # Navegação principal — botão "ativo" via type='primary'
        tela_atual = st.session_state.tela
        nav_items = [
            ("home",          "Início"),
            ("historico",     "Histórico"),
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
                width="stretch",
                type="primary" if ativo else "secondary",
            ):
                # "Novo pregão" pede confirmação se há trabalho a perder
                # (extração/revisão/seleções); as demais telas só navegam.
                if tela_id == "upload":
                    _novo_pregao()
                else:
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
    col_novo, col_hist = st.columns(2)
    with col_novo:
        if st.button("Iniciar novo pregão", type="primary", width="stretch"):
            _novo_pregao()
    with col_hist:
        if st.button("Ver pregões processados", width="stretch"):
            ir_para("historico")


def _tentar_mapa_aprendido(resultado, conn, arquivo: str):
    """Tenta melhorar uma extração fraca com mapas aprendidos (assinatura de
    cabeçalho já vista). Aplica em LOTE — a tabela casada + as fatias-
    continuação das outras páginas. Aplicar numa fatia única descartava as
    demais páginas da tabela multi-página, entregando p.ex. 30 de 500 itens
    com banner de sucesso (auditoria 2026-06, achado alto nº 1).

    Retorna o melhor ResultadoExtracao (o original, se nenhum mapa ganhar).
    """
    for idx, tab in enumerate(resultado.tabelas_brutas):
        if not tab or not tab[0]:
            continue
        sig = mapeamento_manual.assinatura_cabecalho(tab[0])
        mapa_salvo = mapeamento_manual.carregar_mapa(conn, sig)
        if not mapa_salvo:
            continue
        novo = mapeamento_manual.aplicar_mapa_em_lote(
            resultado.tabelas_brutas, idx, mapa_salvo, arquivo=arquivo)
        if novo.confianca_global > resultado.confianca_global:
            novo.tabelas_brutas = resultado.tabelas_brutas
            novo.avisos.insert(
                0,
                f"✨ Mapa aprendido reusado (tabela #{idx + 1} e "
                f"continuações, cabeçalho conhecido)."
            )
            return novo
    return resultado


# === Tela: UPLOAD ===
def tela_upload():
    style.stepper(PASSOS_FLUXO, 0)
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
        orgao = st.text_input("Órgão",
                              placeholder="Ex: Prefeitura Municipal de Araruna")
    with col2:
        numero = st.text_input("Número do pregão",
                               placeholder="Ex: 00006/2026")

    if arquivo and st.button("Extrair itens do PDF", type="primary"):
        # Órgão + número são OBRIGATÓRIOS (decisão do cliente): garantem que
        # cada pregão tem identidade desde o início — base do histórico e da
        # dedup por re-geração. Barrar aqui (antes da extração) pega o caso
        # comum cedo; a geração revalida (a revisão deixa editar/limpar).
        if not (orgao or "").strip() or not (numero or "").strip():
            style.bloco_aviso(
                "Informe o órgão e o número do pregão para continuar.",
                tipo="danger",
            )
            return

        with st.spinner("Lendo PDF e detectando tabelas..."):
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf",
                                              dir=str(config.EDITAIS_DIR))
            tmp.write(arquivo.read())
            tmp.close()
            tmp_path = Path(tmp.name)

            try:
                resultado = extrair_com_pipeline(tmp_path)
            except Exception as exc:
                _log.exception("falha ao extrair PDF")
                style.bloco_aviso(erros.mensagem_amigavel(exc, "ler o PDF"),
                                  tipo="danger")
                return

        # Pipeline automático ficou abaixo do limite? Tenta mapas aprendidos
        # de PDFs anteriores com a mesma assinatura de cabeçalho.
        if resultado.confianca_global < 0.95 and resultado.tabelas_brutas:
            with get_conn() as conn:
                resultado = _tentar_mapa_aprendido(
                    resultado, conn, str(tmp_path))

        # Nenhum estado do pregão ANTERIOR pode vazar no novo (seleções,
        # resultados de busca, overrides de markup keyed por offset...).
        # Também reseta df_editavel: sem isto, uma extração nova reaproveita
        # o DataFrame em cache e o usuário vê itens desatualizados na revisão.
        _limpar_pregao()
        st.session_state.extracao = resultado
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

    style.stepper(PASSOS_FLUXO, 1)
    style.header_pagina(
        "Revisão dos itens extraídos",
        "Confira o que foi lido do PDF, edite o que precisar e marque o que entra na planilha.",
    )

    # Órgão + número continuam EDITÁVEIS aqui (já foram exigidos no upload):
    # o operador pode corrigir antes de gerar a planilha. Reescreve no
    # session_state a cada render para o exportador/persistência usarem o
    # valor atual. Se o operador limpar um deles, _gerar_excel revalida e
    # barra a geração — a identidade nunca chega vazia ao histórico.
    col_org, col_num = st.columns(2)
    with col_org:
        st.session_state.orgao = st.text_input(
            "Órgão", value=st.session_state.get("orgao", ""),
            key="rev_orgao")
    with col_num:
        st.session_state.numero = st.text_input(
            "Número do pregão", value=st.session_state.get("numero", ""),
            key="rev_numero")

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
        from pregoes.extrator import tem_texto_extraivel
        pdf_path = st.session_state.get("pdf_path")
        escaneado = pdf_path and not tem_texto_extraivel(pdf_path)
        if escaneado:
            style.bloco_aviso(
                "Este PDF parece ser <b>escaneado</b> (imagem), sem texto que o "
                "sistema consiga ler. Peça ao órgão o edital em PDF com texto "
                "(ou copie a tabela de itens para um PDF/planilha). Leitura de "
                "imagem (OCR) ainda não é suportada.",
                tipo="danger",
            )
        else:
            style.bloco_aviso(
                "Nenhum item foi extraído automaticamente. Se o PDF tem uma "
                "tabela de itens visível, use o <b>Mapeamento Manual</b> acima.",
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

    # Antes de marcar/desmarcar todos, FUNDIMOS as edições de célula pendentes
    # (Descrição/Quantidade/Unidade/Valor) que o operador fez no widget de
    # volta na snapshot df_editavel. Sem isso, recriar o data_editor (via
    # editor_version) descartaria essas correções — bug reportado pelo
    # operador. A fusão é pontual (só no clique), então não reintroduz o race
    # que evitamos ao não regravar df_editavel a cada rerun.
    _editor_key = f"editor_{st.session_state.editor_version}"

    def _marcar_todos(valor: bool):
        delta = st.session_state.get(_editor_key)
        edited_atual = _aplicar_delta_editor(st.session_state.df_editavel, delta)
        fundido = _fundir_edicoes(st.session_state.df_editavel, edited_atual)
        fundido["OK"] = valor
        st.session_state.df_editavel = fundido
        st.session_state.editor_version += 1
        st.rerun()

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("Marcar todos", width="stretch"):
            _marcar_todos(True)
    with bcol2:
        if st.button("Desmarcar todos", width="stretch"):
            _marcar_todos(False)

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
        width="stretch",
        height=500,
        column_config={
            "OK": st.column_config.CheckboxColumn(
                "Buscar preço?", default=True,
                help="Marque os itens cujo preço o sistema deve buscar nos "
                     "fornecedores. TODOS os itens do edital vão para a planilha "
                     "— os desmarcados ficam sem preço, para preencher à mão."),
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
    st.caption(f"**{n_marcados}** de **{len(edited)}** itens marcados para "
               f"**buscar preço** · todos os **{len(edited)}** vão para a "
               f"planilha (os demais sem preço).")

    st.divider()

    # O markup é definido só na tela de Buscar preços (por item, via
    # Substituição Tributária). Aqui é só revisão/extração.
    # `itens_revisados` = itens a BUSCAR (marcados OK). `itens_edital` = edital
    # INTEIRO (todos) que vai para a matriz; `mapa_busca_edital` alinha um ao
    # outro na exportação (OK = "buscar preço", não "incluir na matriz").
    edital, busca, mapa = _itens_revisao(edited)
    st.session_state.itens_revisados = busca
    st.session_state.itens_edital = edital
    st.session_state.mapa_busca_edital = mapa

    # Botões em linha própria, com proporções iguais.
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("Buscar preços", type="primary", width="stretch",
                     help="Busca o preço dos itens MARCADOS em Almeida e Dantas (qualquer um configurado). Pode demorar minutos."):
            if not st.session_state.itens_revisados:
                style.bloco_aviso("Nenhum item marcado para buscar preço — marque ao menos um.", tipo="danger")
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
        if st.button("Gerar sem buscar", width="stretch",
                     help="Gera a planilha com o EDITAL INTEIRO, sem preços (todas as colunas de preço/código/marca em branco, para preencher à mão)."):
            _gerar_excel(_df_para_itens(edited), markup=config.MARKUP_DEFAULT,
                         selecoes={})


def _linha_para_item(r):
    """Uma linha do data_editor de revisão → ItemExtraido."""
    from pregoes.extrator import ItemExtraido
    return ItemExtraido(
        item_numero=int(r["Item"]) if pd.notna(r["Item"]) else None,
        descricao=str(r["Descrição"]) if pd.notna(r["Descrição"]) else "",
        unidade=str(r["Unidade"]) if pd.notna(r["Unidade"]) else None,
        quantidade=float(r["Quantidade"]) if pd.notna(r["Quantidade"]) else None,
        valor_referencia=float(r["Valor referência"]) if pd.notna(r["Valor referência"]) else None,
    )


def _itens_revisao(df: pd.DataFrame):
    """Do df editado da revisão, devolve ``(edital, busca, mapa)``.

    - ``edital``: TODOS os itens (todas as linhas), na ordem do edital → vão para
      a MATRIZ inteira (pedido do cliente: o pregoeiro confere o edital todo,
      mesmo sem preço).
    - ``busca``: só os marcados "OK" ("buscar preço deste item") → lista de
      busca/seleção nos fornecedores.
    - ``mapa``: índice em ``edital`` de cada item de ``busca``, na ordem — para
      re-mapear ``selecoes``/``markups``/``itens_com_st`` do índice-de-busca p/ o
      índice-no-edital ao exportar (OK = "buscar preço", NÃO "incluir na matriz").
    """
    edital, busca, mapa = [], [], []
    for _, r in df.iterrows():
        edital.append(_linha_para_item(r))
        if r["OK"] == True:
            mapa.append(len(edital) - 1)
            busca.append(edital[-1])
    return edital, busca, mapa


def _df_para_itens(df: pd.DataFrame) -> list:
    """Itens marcados "OK" ("buscar preço") — a lista de busca/seleção."""
    return _itens_revisao(df)[1]


def _gerar_excel(itens: list, markup: float, selecoes: dict,
                 markups: dict | None = None,
                 itens_com_st: set | None = None):
    """Exporta uma lista de ItemExtraido para a planilha matriz.

    A planilha contém SEMPRE **todos** os itens do edital, na ordem original
    (pedido do cliente 2026-06: o pregoeiro confere o edital inteiro). `selecoes`
    é o mapa {indice_item: SelecaoFornecedor} preparado pela tela de busca; os
    itens FORA desse mapa ficam na planilha com preço/marca/código em branco
    (operador preenche à mão ou o item não entra no lance). O exportador escreve
    descrição/unidade/qtd/ref de todos e L/T/U/markup só dos presentes em
    `selecoes` — não há filtragem aqui.

    Aceita lista de ItemExtraido (não DataFrame). O caller é responsável
    por converter `edited` do data_editor via `_df_para_itens(edited)`.
    Isso evita inconsistência entre o estado do widget e `df_editavel`
    que existia antes (bug visto na busca de preços: operador marca 5
    itens, busca preços, mas planilha não exportava porque pegava
    snapshot antiga do df).
    """
    # A MATRIZ contém o EDITAL INTEIRO (todos os itens), com preço só nos itens
    # buscados/selecionados. `itens` aqui é a lista de BUSCA (marcados "OK");
    # trocamos pelo edital completo e re-mapeamos selecoes/markups/ST do índice
    # de busca para o índice no edital (OK = "buscar preço", não "incluir na
    # matriz" — pedido do cliente 2026-06-25). Sem edital armazenado (caminho de
    # teste/legado), segue com `itens` como veio.
    edital = st.session_state.get("itens_edital")
    mapa = st.session_state.get("mapa_busca_edital")
    if edital and mapa is not None and len(mapa) == len(itens):
        def _remap_dict(d):
            return {mapa[k]: v for k, v in d.items() if 0 <= k < len(mapa)}
        selecoes = _remap_dict(selecoes or {})
        markups = _remap_dict(markups) if markups else markups
        if itens_com_st:
            itens_com_st = {mapa[k] for k in itens_com_st if 0 <= k < len(mapa)}
        itens = edital

    if not itens:
        style.bloco_aviso("Nenhum item no edital — nada para exportar.", tipo="danger")
        return

    # Órgão + número são OBRIGATÓRIOS (decisão do cliente): identificam o pregão
    # no histórico e são a chave da dedup por re-geração. Revalidamos AQUI — não
    # só no upload — porque a tela de revisão deixa o operador editar/limpar
    # esses campos. Como os 3 botões de geração ("Gerar sem buscar", "Gerar com
    # preços", etc.) passam por _gerar_excel, este é o ponto ÚNICO que garante a
    # regra: sem identidade, não gera (e não cai no INSERT-sem-identidade).
    if not (st.session_state.get("orgao") or "").strip() or \
       not (st.session_state.get("numero") or "").strip():
        style.bloco_aviso(
            "Informe o órgão e o número do pregão antes de gerar a planilha — "
            "eles identificam o pregão no histórico. Volte e preencha os dois campos.",
            tipo="danger")
        return

    # Diagnóstico (auditoria 2026-06): por que a coluna ST sai "Não". Registra
    # o NCM de cada seleção, o markup por item e o conjunto ST que chega aqui.
    _log.info(
        "GERAR planilha: %d itens | ncm_por_selecao=%s | markups=%s | itens_com_st=%s",
        len(itens),
        {off: getattr(s, "ncm", None) for off, s in (selecoes or {}).items()},
        markups,
        sorted(itens_com_st) if itens_com_st else itens_com_st,
    )

    with st.spinner("Gerando planilha matriz a partir do template..."):
        try:
            destino = exportar(
                itens,
                orgao=st.session_state.get("orgao"),
                numero_pregao=st.session_state.get("numero"),
                markup=markup,
                markups=markups,
                itens_com_st=itens_com_st,
                selecoes=selecoes,
            )
        except Exception as exc:
            _log.exception("falha ao gerar planilha")
            style.bloco_aviso(erros.mensagem_amigavel(exc, "gerar a planilha"),
                              tipo="danger")
            return

        # Persistir edital + itens + histórico da planilha (mesma transação).
        # BEST-EFFORT: a planilha JÁ foi gerada (arquivo no disco) — uma falha ao
        # salvar o HISTÓRICO (DB travado/disco) não pode bloquear o download nem
        # derrubar a tela na hora do pregão (achado QA D3, 2026-06-25). Loga,
        # avisa e SEGUE para o sucesso/download; o histórico completa-se na
        # próxima geração.
        try:
            _persistir_edital(itens, selecoes, destino, markup)
        except Exception:
            _log.exception("falha ao salvar o histórico do pregão (planilha OK)")
            style.bloco_aviso(
                "A planilha foi gerada — baixe abaixo. Só não consegui registrá-la "
                "no <b>histórico</b> agora; gere de novo mais tarde para completar.",
                tipo="warning")

    # Pregão CONCLUÍDO: o trabalho está salvo (arquivo + histórico). Guarda a
    # IMPRESSÃO DIGITAL dos insumos gerados — enquanto o estado bater com ela, o
    # sistema não trata mais como "em andamento" (senão o modal "começar um novo
    # pregão" aparecia mesmo depois de gerar — feedback do cliente 2026-06). Se o
    # operador mexer em qualquer insumo, o hash deixa de bater e o aviso volta.
    st.session_state["pregao_gerado_hash"] = _hash_pregao()

    style.bloco_aviso(f"Planilha gerada: <code>{destino.name}</code>", tipo="success")
    if selecoes:
        st.caption(f"Inclui preço/marca/código para {len(selecoes)} de {len(itens)} itens.")
    with open(destino, "rb") as f:
        st.download_button(
            "Baixar planilha matriz",
            data=f.read(),
            file_name=destino.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
            type="primary",
        )

    # Caminho claro de saída: o pregão acabou. Como o hash gerado bate com o
    # estado atual, `_novo_pregao` limpa e navega direto (sem o modal "perder
    # trabalho").
    st.caption("Pregão concluído e salvo no histórico. Pode baixar a planilha "
               "acima ou começar o próximo.")
    if st.button("Começar um novo pregão", width="stretch", key="novo_apos_gerar"):
        _novo_pregao()


def _persistir_edital(itens: list, selecoes: dict, destino, markup) -> None:
    """Grava o edital + seus itens + 1 linha de histórico da planilha gerada,
    tudo na MESMA transação (rollback descarta os três juntos).

    UPSERT por identidade `(orgao, numero_pregao)` NORMALIZADA (trim + lower):
    re-gerar o MESMO pregão (mesmo órgão+número) ATUALIZA o registro anterior
    em vez de criar um duplicado — reusa o `edital_id`, atualiza status/PDF e
    troca os itens (DELETE + reinsere; CASCADE garante que nada fica órfão).
    Como órgão+número são obrigatórios no fluxo, a identidade está sempre
    presente; ainda assim, se ambos vierem vazios, cai no INSERT-novo (segurança
    — não fundir pregões sem identidade).

    `destino` é o Path do .xlsx gerado; gravamos SÓ o basename (`destino.name`)
    no histórico, e `markup` (global do pregão) para auditoria.
    """
    orgao = (st.session_state.get("orgao") or "").strip()
    numero = (st.session_state.get("numero") or "").strip()
    pdf_path = st.session_state.get("pdf_path") or ""
    status = "precificado" if selecoes else "rascunho"

    with get_conn() as conn:
        edital_id = None
        # Identidade presente → procura um edital já existente para atualizar.
        # Match normalizado (trim + lower) tolera "00006/2026" vs " 00006/2026 "
        # e diferenças de caixa no nome do órgão.
        if orgao and numero:
            row = conn.execute(
                """
                SELECT id FROM editais
                WHERE lower(trim(orgao)) = lower(trim(?))
                  AND lower(trim(numero_pregao)) = lower(trim(?))
                ORDER BY id DESC LIMIT 1
                """,
                (orgao, numero),
            ).fetchone()
            if row is not None:
                edital_id = row[0]

        if edital_id is not None:
            # Reaproveita o edital: atualiza metadados e substitui os itens.
            conn.execute(
                """
                UPDATE editais
                SET status = ?, arquivo_pdf = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, pdf_path, edital_id),
            )
            conn.execute(
                "DELETE FROM edital_itens WHERE edital_id = ?", (edital_id,)
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO editais (orgao, numero_pregao, arquivo_pdf, status)
                VALUES (?, ?, ?, ?)
                """,
                (orgao, numero, pdf_path, status),
            )
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

        # Histórico da planilha gerada — mesma transação (nasce junto do
        # edital; rollback descarta ambos). Guarda só o basename.
        _db.export_log_gravar(
            conn, edital_id, destino.name,
            len(itens), len(selecoes or {}), markup,
        )


# === Tela: HISTÓRICO ===
def _data_amigavel(criado_em: str | None) -> str:
    """Converte o timestamp do banco ('AAAA-MM-DD HH:MM:SS') para 'DD/MM HH:MM'.

    Tolerante: se o formato não bater (banco antigo, valor nulo), devolve o
    texto cru — nunca estoura exceção na cara do operador.
    """
    if not criado_em:
        return "data desconhecida"
    try:
        from datetime import datetime as _dt
        return _dt.strptime(criado_em[:19], "%Y-%m-%d %H:%M:%S").strftime("%d/%m %H:%M")
    except (ValueError, TypeError):
        return str(criado_em)


def _rotulo_pregao(orgao: str | None, numero: str | None, status: str | None,
                   criado_em: str | None) -> str:
    """Rótulo amigável em PT para um pregão na lista do histórico.

    Ex.: 'Pregão 00006/2026 — Prefeitura de Araruna — precificado em 09/06 14:30'.
    Sem órgão/número (registros pré-obrigatoriedade), cai num rótulo neutro
    com a data, para nunca exibir linha em branco.
    """
    orgao = (orgao or "").strip()
    numero = (numero or "").strip()
    quando = _data_amigavel(criado_em)
    if not orgao and not numero:
        return f"Pregão sem identificação (de {quando})"
    partes = []
    if numero:
        partes.append(f"Pregão {numero}")
    if orgao:
        partes.append(orgao)
    cabeca = " — ".join(partes)
    rotulo_status = (status or "rascunho").strip()
    return f"{cabeca} — {rotulo_status} em {quando}"


def tela_historico():
    """Lista os pregões processados e permite rebaixar as planilhas geradas.

    Rebaixar = reler os BYTES do .xlsx salvo no disco (NUNCA re-exportar): o
    arquivo é o artefato final que o operador validou. O caminho é reconstruído
    a cada render como `config.EXPORTS_DIR / arquivo` (só o basename é
    persistido), imune a mover a pasta portável. Arquivo ausente → aviso, sem
    botão (Path.exists() em runtime; nada é persistido sobre existência).

    Tela de LEITURA (não edita pregão): só consulta o banco e serve arquivos.
    """
    style.header_pagina(
        "Histórico de pregões",
        "Pregões já processados e as planilhas geradas. Você pode baixar de novo "
        "qualquer planilha que ainda esteja na pasta do programa.",
    )

    with get_conn() as conn:
        editais = conn.execute(
            """
            SELECT e.id, e.orgao, e.numero_pregao, e.status, e.criado_em,
                   COUNT(x.id) AS n_planilhas
            FROM editais e
            LEFT JOIN exports_log x ON x.edital_id = e.id
            GROUP BY e.id
            ORDER BY e.criado_em DESC
            """
        ).fetchall()

    if not editais:
        style.bloco_aviso(
            "Nenhum pregão processado ainda. Comece em <b>Novo pregão</b>.",
            tipo="info",
        )
        return

    _MIME_XLSX = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    for ed in editais:
        rotulo = _rotulo_pregao(ed["orgao"], ed["numero_pregao"],
                                ed["status"], ed["criado_em"])
        n_planilhas = ed["n_planilhas"] or 0
        sufixo = (f" · {n_planilhas} planilha"
                  + ("s" if n_planilhas != 1 else ""))
        with st.expander(rotulo + sufixo, expanded=False):
            if n_planilhas == 0:
                style.bloco_aviso(
                    "Nenhuma planilha registrada — gere novamente para "
                    "rebaixar.",
                    tipo="warning",
                )
                continue

            # Planilhas deste pregão, mais recente no topo (índice cobre isto).
            with get_conn() as conn:
                planilhas = conn.execute(
                    """
                    SELECT arquivo, n_itens, n_selecoes, criado_em
                    FROM exports_log
                    WHERE edital_id = ?
                    ORDER BY criado_em DESC, id DESC
                    """,
                    (ed["id"],),
                ).fetchall()

            for i, pl in enumerate(planilhas):
                arquivo = pl["arquivo"]
                quando = _data_amigavel(pl["criado_em"])
                st.caption(
                    f"Gerada em {quando} · {pl['n_itens']} itens · "
                    f"{pl['n_selecoes']} com preço/marca"
                )
                # Reconstrói o caminho AGORA (frozen-aware): nunca confiar num
                # path absoluto gravado, que quebraria ao mover a pasta portável.
                caminho = config.EXPORTS_DIR / arquivo
                if caminho.exists():
                    with open(caminho, "rb") as f:
                        st.download_button(
                            "Baixar planilha",
                            data=f.read(),
                            file_name=arquivo,
                            mime=_MIME_XLSX,
                            key=f"dl_{ed['id']}_{i}",
                            width="stretch",
                        )
                else:
                    style.bloco_aviso(
                        "Esta planilha não está mais na pasta do programa "
                        "(pode ter sido movida ou apagada). Gere de novo a "
                        "partir do pregão.",
                        tipo="warning",
                    )


# === Tela: CONFIGURAÇÕES ===
def tela_configuracoes():
    style.header_pagina(
        "Configurações",
        "Credenciais dos fornecedores. Tudo fica salvo apenas no seu computador "
        "(arquivo <code>data/credenciais.json</code>), sem envio para servidores externos.",
    )

    aba_almeida, aba_dantas, aba_ia, aba_cat = st.tabs(
        ["Almeida", "Dantas", "IA (Gemini)", "Catálogo histórico"]
    )

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

        _form_condicao_pagamento("almeida")

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

        _form_condicao_pagamento("dantas")

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
                            from google.genai import types
                            from pregoes.extrator_llm import MODELO_DEFAULT
                            # Timeout explícito (60s em ms): sem isto, um
                            # soluço de rede deixaria o spinner "Testando…"
                            # eterno, sem cancelar. Mesmo padrão dos demais
                            # módulos (extrator_llm).
                            client = genai.Client(
                                api_key=chave_input.strip(),
                                http_options=types.HttpOptions(timeout=60_000),
                            )
                            resp = client.models.generate_content(
                                # Mesmo modelo do pipeline (gemini-2.5-flash-lite):
                                # o 2.0-flash perdeu o free tier para contas novas
                                # e falharia justo na tela de validação da chave.
                                model=MODELO_DEFAULT,
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

    with aba_cat:
        _aba_catalogo()


# === Tela: BUSCA DE PREÇOS ===
def tela_busca_precos():
    style.stepper(PASSOS_FLUXO, 2)
    style.header_pagina(
        "Busca de preços nos fornecedores",
        "Consulta cada item nos fornecedores configurados e lista as marcas "
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

    # Transparência: a CONDIÇÃO DE PAGAMENTO define o preço raspado.
    _mostrar_condicao_pagamento(fornecedores_disponiveis)

    # Já buscou: pula direto para a tela de seleção.
    if "resultados_busca" in st.session_state:
        diag_path = st.session_state.get("diag_path")
        if _DEBUG and diag_path:
            from pathlib import Path as _Path
            p = _Path(diag_path)
            if p.exists():
                with open(p, "rb") as f:
                    st.download_button(
                        "Baixar relatório de diagnóstico (XLSX)",
                        data=f.read(),
                        file_name=p.name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                st.caption(
                    f"Arquivo salvo em `data/diagnostico/{p.name}`. "
                    "Abra no Excel/LibreOffice, anote `correto`/`errado`/`aceitavel` "
                    "na coluna **Avaliação** da aba **Resumo** e devolva para calibrar."
                )
        # Saída pós-busca (auditoria 2026-06, achado alto nº 8): se o site
        # falhou no meio do lote, o operador precisa de um caminho que não
        # seja recomeçar o pregão do zero. Re-buscar é barato: o que já tem
        # preço responde do cache (commit incremental por item) e só o que
        # ficou vazio é consultado de novo (vazio não entra no cache).
        with st.expander("A busca teve problemas? Re-buscar ou voltar"):
            st.caption(
                "Os dois caminhos descartam os resultados e as marcas já "
                "escolhidas nesta tela — re-buscar é rápido: itens que já têm "
                "preço respondem da memória (2 dias) e só os sem resultado "
                "são tentados outra vez."
            )
            col_re, col_volta = st.columns(2)
            # Ambos os botões DESCARTAM resultados/seleções: eles são
            # indexados pela POSIÇÃO do item, e voltar à revisão permite
            # remover/editar itens — manter o estado antigo pareava preço
            # do item errado na planilha (verificação adversarial 2026-06).
            if col_re.button("Buscar preços novamente", width="stretch"):
                for k in ("resultados_busca", "selecoes", "pagina_busca"):
                    st.session_state.pop(k, None)
                st.rerun()
            if col_volta.button("Voltar para a revisão", width="stretch"):
                for k in ("resultados_busca", "selecoes", "pagina_busca"):
                    st.session_state.pop(k, None)
                ir_para("revisao")
        _mostrar_selecao(itens, st.session_state.resultados_busca)
        return

    # === Pré-busca: cards de resumo + CTA ===
    # Não usamos mais "estimativa de tempo" (variava muito com latência),
    # mas mostramos o que o operador precisa saber antes de começar.
    fornec_str = " + ".join(f.capitalize() for f in fornecedores_disponiveis)
    style.resumo_pre_busca([
        ("Itens a buscar", str(len(itens))),
        ("Fornecedores", fornec_str),
        ("Cache de preço", f"{config.CACHE_PRECO_DIAS} dias"),
    ])

    # Controles TÉCNICOS (diagnóstico, limpar cache) só aparecem em modo de
    # desenvolvimento (env PREGOES_DEBUG). Para o operador, a UI é limpa: a
    # IA e o cache são automáticos, sem opção de ligar/desligar.
    salvar_diag = False
    limpar_cache_antes = False
    if _DEBUG:
        cda, cdb = st.columns(2)
        with cda:
            salvar_diag = st.checkbox("[dev] Salvar diagnóstico (XLSX)", value=False)
        with cdb:
            limpar_cache_antes = st.checkbox("[dev] Limpar cache antes", value=False)

    col_a, col_b, _spacer = st.columns([2, 2, 6])
    with col_a:
        iniciar = st.button("Iniciar busca", type="primary", width="stretch")
    with col_b:
        if st.button("Voltar para revisão", width="stretch"):
            ir_para("revisao")
            return
    if iniciar:
        if limpar_cache_antes:
            from pregoes import db as _db
            with get_conn() as _conn:
                _db.cache_limpar(_conn)
        # IMPORTANTE: chamar fora de `with col_a:` evita que o painel
        # de progresso herde a largura estreita da coluna do botão.
        _executar_busca(itens, fornecedores_disponiveis,
                        salvar_diagnostico=salvar_diag)
        st.rerun()


def _executar_busca(itens: list, fornecedores_nomes: list[str],
                     *, salvar_diagnostico: bool = False) -> None:
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

    UI: usa ``style.painel_progresso_busca`` em um placeholder
    full-width — não dentro de ``st.columns`` — para o nome do item
    aparecer inteiro, com tempo decorrido e sumário por fornecedor.
    """
    import time

    from pregoes.scraper import (
        AlmeidaDistribuidor,
        DantasDistribuidora,
        ResultadoLote,
        buscar_precos_para_itens,
    )
    from pregoes.diagnostico import LogDiagnostico

    classes = {"almeida": AlmeidaDistribuidor, "dantas": DantasDistribuidora}
    n_forn = len(fornecedores_nomes)
    t_inicio = time.monotonic()

    # Log compartilhado entre todos os fornecedores (concatena por item)
    log_diag = None
    if salvar_diagnostico:
        log_diag = LogDiagnostico(
            arquivo_edital=st.session_state.get("pdf_path"),
            fornecedores=list(fornecedores_nomes),
        )

    # Placeholder único, full-width: o painel inteiro é redesenhado a cada
    # callback, mantendo header, barra, item atual e stats coerentes.
    painel = st.empty()
    status_final = st.empty()

    def _tempo() -> str:
        secs = int(time.monotonic() - t_inicio)
        m, s = divmod(secs, 60)
        if m >= 60:
            h, m = divmod(m, 60)
            return f"{h:d}h {m:02d}min"
        return f"{m:02d}:{s:02d}"

    def _render(
        frac: float, fornecedor: str, forn_idx: int,
        item_atual: int, item_total: int, item_desc: str,
        stats_concluidos: list, encerrado: bool = False,
    ) -> None:
        with painel.container():
            style.painel_progresso_busca(
                frac=frac,
                titulo="Buscando preços" if not encerrado else "Busca concluída",
                sub=(
                    f"{fornecedor.capitalize()} · fornecedor "
                    f"{forn_idx + 1} de {n_forn}"
                    if fornecedor else
                    "Todos os fornecedores"
                ),
                tempo=_tempo(),
                item_atual=item_atual,
                item_total=item_total,
                item_descricao=item_desc,
                stats=stats_concluidos or None,
                encerrado=encerrado,
            )

    # Acumuladores ===
    resultados_agregados: dict[int, list] = {idx: [] for idx in range(len(itens))}
    erros_agregados: list = []
    cache_hits_total = 0
    cache_misses_total = 0
    stats_concluidos: list[tuple[str, int, int, int]] = []
    total_itens = len(itens)

    # Estado inicial visível antes do primeiro callback do scraper
    _render(
        frac=0.0,
        fornecedor=fornecedores_nomes[0] if fornecedores_nomes else "",
        forn_idx=0,
        item_atual=0, item_total=total_itens,
        item_desc="Preparando ambiente…",
        stats_concluidos=stats_concluidos,
    )

    for forn_idx, nome in enumerate(fornecedores_nomes):
        cred = credenciais.carregar(nome)
        if cred is None:
            continue
        cls = classes[nome]

        _render(
            frac=forn_idx / max(n_forn, 1),
            fornecedor=nome, forn_idx=forn_idx,
            item_atual=0, item_total=total_itens,
            item_desc=f"Fazendo login em {nome.capitalize()}…",
            stats_concluidos=stats_concluidos,
        )

        def cb(atual, total, msg, _forn_idx=forn_idx, _nome=nome):
            frac_dentro = atual / max(total, 1)
            frac_total = (_forn_idx + frac_dentro) / max(n_forn, 1)
            _render(
                frac=frac_total,
                fornecedor=_nome, forn_idx=_forn_idx,
                item_atual=atual, item_total=total,
                item_desc=msg,
                stats_concluidos=stats_concluidos,
            )

        try:
            with cls(cred["email"], cred["senha"]) as forn:
                # judge/canônico ligados por padrão no orquestrador — sem
                # controle de UI (operador não precisa saber).
                lote = buscar_precos_para_itens(
                    itens, [forn], progresso_callback=cb,
                    diagnostico=log_diag,
                )
        except Exception as exc:
            # Falha grave (login, layout mudou) — registra como erro
            # global mas segue para o próximo fornecedor.
            _log.exception("falha grave na busca em %s", nome)
            from pregoes.scraper import ErroBusca
            erros_agregados.append(ErroBusca(
                indice_item=-1,
                fornecedor=nome,
                descricao_resumo="(login/inicialização)",
                mensagem=erros.mensagem_amigavel(exc, f"buscar na {nome.capitalize()}"),
            ))
            stats_concluidos.append((nome, 0, 0, total_itens))
            continue

        # Sumário deste fornecedor (alimenta a faixa de chips no painel)
        n_ok = sum(1 for bag in lote.resultados.values() if bag)
        n_var = sum(
            1 for bag in lote.resultados.values()
            if bag and any(r.confianca == "variacao" for r in bag)
        )
        n_vazio = total_itens - n_ok
        # "ok" exclui os por variação para refletir o badge da seleção
        stats_concluidos.append((nome, n_ok - n_var, n_var, n_vazio))

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

    # Painel final — barra cheia, sem item atual, com sumário consolidado.
    _render(
        frac=1.0, fornecedor="", forn_idx=n_forn - 1,
        item_atual=total_itens, item_total=total_itens, item_desc="",
        stats_concluidos=stats_concluidos, encerrado=True,
    )

    st.session_state.resultados_busca = lote.resultados
    st.session_state.erros_busca = lote.erros
    st.session_state.cache_stats = (lote.cache_hits, lote.cache_misses)
    with status_final.container():
        hits, misses = lote.cache_hits, lote.cache_misses
        extras = []
        if hits or misses:
            extras.append(f"{hits} do cache, {misses} buscas reais")
        if lote.erros:
            extras.append(f"{len(lote.erros)} erro(s) técnico(s)")
        sub = f" ({' · '.join(extras)})" if extras else ""
        style.bloco_aviso(
            f"Busca concluída — {len(lote.resultados)} itens processados{sub}.",
            tipo="success",
        )

    # Exporta diagnóstico, se foi solicitado.
    if log_diag is not None and log_diag.itens:
        from pregoes.diagnostico import exportar_xlsx as _exp
        from datetime import datetime
        diag_dir = config.DATA_DIR / "diagnostico"
        diag_dir.mkdir(parents=True, exist_ok=True)
        nome = f"diagnostico_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        destino = diag_dir / nome
        try:
            _exp(log_diag, destino)
            st.session_state.diag_path = str(destino)
        except Exception as exc:
            style.bloco_aviso(
                f"Diagnóstico falhou ao exportar: {type(exc).__name__}: {exc}",
                tipo="warning",
            )


def _brl(valor: float) -> str:
    """Formata em Real brasileiro: 1234.5 -> 'R$ 1.234,50'."""
    return "R$ " + (
        f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    )


def _form_condicao_pagamento(fornecedor: str) -> None:
    """Seletor da CONDIÇÃO DE PAGAMENTO (Forma + Prazo) por fornecedor. O preço no
    site varia conforme a condição; aqui o operador fixa a que ele usa, e o scraper
    aplica em toda busca. Opções bundladas (config.carregar_opcoes_pagamento)."""
    opcoes = config.carregar_opcoes_pagamento().get(fornecedor, {})
    forma_ops = opcoes.get("forma", [])
    prazo_ops = opcoes.get("prazo", [])
    if not forma_ops or not prazo_ops:
        return   # sem opções bundladas (raro) — não mostra o seletor
    pref = credenciais.carregar_preferencia_pagamento(fornecedor) or {}

    def _idx(ops, value):
        for i, o in enumerate(ops):
            if o.get("value") == value:
                return i
        return 0

    st.markdown("**Condição de pagamento** — o preço no site **muda** conforme a "
                "forma e o prazo. Escolha a condição sob a qual você realmente "
                "compra; o sistema busca os preços nela.")
    with st.form(f"form_pagto_{fornecedor}"):
        forma = st.selectbox(
            "Forma de Pagamento", forma_ops,
            index=_idx(forma_ops, pref.get("forma_value")),
            format_func=lambda o: o["texto"], key=f"forma_{fornecedor}")
        prazo = st.selectbox(
            "Prazo de Pagamento", prazo_ops,
            index=_idx(prazo_ops, pref.get("prazo_value")),
            format_func=lambda o: o["texto"], key=f"prazo_{fornecedor}")
        if st.form_submit_button("Salvar condição de pagamento", type="primary"):
            credenciais.salvar_preferencia_pagamento(
                fornecedor, forma["value"], forma["texto"],
                prazo["value"], prazo["texto"])
            style.bloco_aviso(
                f"Condição salva: <b>{html.escape(forma['texto'])}</b> · "
                f"<b>{html.escape(prazo['texto'])}</b>. As próximas buscas usam "
                "essa condição.", tipo="success")
            pref = {"forma_texto": forma["texto"], "prazo_texto": prazo["texto"]}
    if pref:
        st.caption(f"Condição atual: {pref.get('forma_texto')} · "
                   f"{pref.get('prazo_texto')}")
    else:
        st.caption("Ainda não configurado — o sistema usa a condição **default** "
                   "do site (o preço pode não bater com a sua).")


def _mostrar_condicao_pagamento(fornecedores: list) -> None:
    """Na tela de busca, mostra a condição de pagamento que será usada por
    fornecedor (o preço raspado é o dessa condição). Avisa quando não há condição
    configurada — aí o preço vem do default do site, que pode não bater com o do
    cliente."""
    configuradas, falta = [], []
    for nome in fornecedores:
        pref = credenciais.carregar_preferencia_pagamento(nome)
        if pref:
            configuradas.append(
                f"{nome.capitalize()}: {pref['forma_texto']} · {pref['prazo_texto']}")
        else:
            falta.append(nome.capitalize())
    if configuradas:
        st.caption("💳 Preços para a condição de pagamento → "
                   + "   |   ".join(configuradas))
    if falta:
        style.bloco_aviso(
            "Sem condição de pagamento configurada para <b>" + ", ".join(falta) +
            "</b> — o preço vem da condição <b>default</b> do site (pode não bater "
            "com a sua). Configure em <b>Configurações</b>, na aba do fornecedor.",
            tipo="warning")


def _hash_pregao() -> str:
    """Impressão digital dos INSUMOS da planilha: itens revisados, seleções (com
    NCM) e os markups (global, ST, normal, overrides). Serve para saber se o
    estado MUDOU desde a última geração — QUALQUER mudança (marca, preço, NCM,
    markup, edição da revisão, re-extração) altera o hash e re-arma o aviso de
    'pregão em andamento' sozinha, sem instrumentar cada ação (o que deixava
    furos — achado da revisão de QA 2026-06)."""
    import hashlib
    ss = st.session_state
    # Hash sobre o EDITAL completo (o que de fato vai na matriz); cai para a
    # lista de busca se o edital ainda não foi montado.
    itens = ss.get("itens_edital") or ss.get("itens_revisados") or []
    sel = ss.get("selecoes") or {}
    partes = (
        tuple((getattr(i, "item_numero", None), getattr(i, "descricao", None),
               getattr(i, "unidade", None), getattr(i, "quantidade", None),
               getattr(i, "valor_referencia", None)) for i in itens),
        tuple(sorted((off, getattr(v, "preco", None),
                      getattr(v, "codigo_fornecedor", None),
                      getattr(v, "marca", None), getattr(v, "ncm", None))
                     for off, v in sel.items())),
        ss.get("markup"), ss.get("mk_st"), ss.get("mk_normal"),
        tuple(sorted((ss.get("markup_overrides") or {}).items())),
    )
    return hashlib.sha256(repr(partes).encode("utf-8")).hexdigest()


def _escolher_opcao(idx: int, o) -> None:
    """Registra a marca/preço que o operador escolheu para um item."""
    from pregoes.exportador import SelecaoFornecedor
    st.session_state.selecoes[idx] = SelecaoFornecedor(
        preco=o.preco, codigo_fornecedor=o.codigo_fornecedor, marca=o.marca,
        ncm=getattr(o, "ncm", None), url=getattr(o, "url", None),
        fornecedor=getattr(o, "fornecedor", None))


def _numero_item_edital(item, idx: int) -> int:
    """Número do item NO EDITAL para exibição na tela de busca — a mesma fonte
    da coluna ITEM (B) da planilha exportada, para o operador conferir "que
    produto é este no edital?" pelo ID.

    Fallback ao sequencial 1-based quando o PDF não trouxe `item_numero`.
    Checagem explícita `is not None` (e NÃO `item.item_numero or idx+1`): um
    item_numero válido igual a 0 não pode cair no fallback. Espelha a mesma
    regra do exportador (coluna B)."""
    n = getattr(item, "item_numero", None)
    return n if n is not None else idx + 1


def _titulo_item(descricao: str) -> str:
    """Apresenta a descrição do edital como um título legível.

    Editais chegam em CAIXA ALTA ('CILINDRO PARA FECHADURA 803/1401'), o que
    soa agressivo num título. Convertemos cada palavra inteiramente maiúscula
    e 'comprida' para capitalização normal (CILINDRO -> Cilindro), mas
    preservamos os tokens técnicos — siglas curtas (PVC, CR), medidas (53MM)
    e códigos com '/' (803/1401) — que perderiam sentido se mexidos.
    """
    texto = (descricao or "").strip().rstrip(" .;,-")
    palavras = []
    for p in texto.split():
        tecnico = any(c.isdigit() for c in p) or "/" in p or len(p) <= 3
        palavras.append(p.capitalize() if (p.isupper() and not tecnico) else p)
    titulo = " ".join(palavras)
    # Encurta só o CABEÇALHO (a descrição completa aparece dentro do item).
    if len(titulo) > 95:
        titulo = titulo[:95].rstrip() + "…"
    return (titulo[:1].upper() + titulo[1:]) if titulo else titulo


def _card_opcao(idx: int, pos: int, opcao, *, badge_html: str,
                escolhido: bool, on_escolher) -> None:
    """Renderiza uma opção de marca/preço como card selecionável.

    Layout decidido com o cliente (2026-06): o que decide a compra fica em
    destaque — **marca** e **preço**; a descrição encontrada, o fornecedor e o
    **código do fornecedor** ficam secundários (cinza, menores) para
    conferência. O código passou a aparecer por FEEDBACK DO CLIENTE
    (2026-06-09): ele precisa vê-lo para diferenciar produtos parecidos e
    cruzar com as tabelas internas. A relevância do algoritmo (`rel=`) continua
    oculta (métrica interna).

    O card escolhido recebe um `key` `ppoptsel-*` para o CSS destacá-lo
    (fundo + acento). ``badge_html`` é uma pílula de ``style.pill`` (ou "").
    """
    marca = (opcao.marca or "Sem marca").strip()
    fornecedor = (opcao.fornecedor or "").capitalize()
    codigo = (opcao.codigo_fornecedor or "").strip()
    desc = (opcao.descricao_encontrada or "").strip()
    if len(desc) > 72:
        desc = desc[:72].rstrip() + "…"

    # Key distinto para o selecionado → CSS o destaca (ver style.py).
    key_card = (f"ppoptsel-{idx}-{pos}" if escolhido
                else f"ppopt-{idx}-{pos}")
    with st.container(border=True, key=key_card):
        c_info, c_preco, c_acao = st.columns(
            [5.2, 1.8, 1.6], vertical_alignment="center")
        with c_info:
            sufixo = f" {badge_html}" if badge_html else ""
            # Linha secundária: descrição · fornecedor · código (o código entra
            # por feedback do cliente, para diferenciar produtos e cruzar com as
            # tabelas internas). O código vai em destaque leve (monoespaçado).
            meta = " · ".join(p for p in [fornecedor] if p)
            cod_html = (f" · <code style='color:#475569;font-size:0.8rem'>"
                        f"cód. {html.escape(codigo)}</code>") if codigo else ""
            sub = desc + (f" · {meta}" if meta else "") + cod_html
            st.markdown(
                f"<span style='font-weight:600;font-size:1.02rem'>{marca}</span>"
                f"{sufixo}<br>"
                f"<span style='color:#64748b;font-size:0.85rem'>{sub}</span>",
                unsafe_allow_html=True,
            )
        with c_preco:
            st.markdown(
                "<div style='text-align:right;font-weight:600;"
                f"font-size:1.05rem'>{_brl(opcao.preco)}</div>",
                unsafe_allow_html=True,
            )
        with c_acao:
            if escolhido:
                st.markdown(
                    "<div style='text-align:right;color:#2563eb;"
                    "font-weight:600;padding-top:0.25rem'>✓ escolhido</div>",
                    unsafe_allow_html=True,
                )
            elif st.button("Escolher", key=f"esc_{idx}_{pos}",
                           width="stretch"):
                on_escolher()
                st.rerun()


def _dedup_opcoes(opcoes: list) -> list:
    """Remove candidatos DUPLICADOS, preservando a ordem.

    O pipeline de busca às vezes traz o MESMO produto mais de uma vez (queries
    ou camadas diferentes que casam o mesmo card). Mostrar o duplicado confunde
    o operador e — como a seleção casa por VALOR (fornecedor+código+marca+preço)
    — faz DOIS cards idênticos aparecerem "escolhido". A chave de dedup é
    exatamente essa identidade de seleção, garantindo no máximo um card marcado.
    """
    vistos: set = set()
    unicas: list = []
    for o in opcoes:
        chave = (o.fornecedor, o.codigo_fornecedor, o.marca, o.preco)
        if chave not in vistos:
            vistos.add(chave)
            unicas.append(o)
    return unicas


def _identificar_ncm(selecoes: dict, offsets: list) -> None:
    """Busca o NCM dos produtos escolhidos (que ainda não têm) na página de
    DETALHE do fornecedor — é lá que o NCM mora, não no card de busca. Cacheia
    por URL e atualiza as seleções (SelecaoFornecedor é frozen → recria)."""
    from dataclasses import replace
    from pregoes.scraper import consultar_ncms

    cache = st.session_state.setdefault("ncm_cache", {})
    pedidos = []
    for off in offsets:
        sel = selecoes.get(off)
        url = getattr(sel, "url", None)
        if not url:
            continue
        if url in cache:                       # já consultado neste pregão
            selecoes[off] = replace(sel, ncm=cache[url])
            continue
        forn = ("almeida" if "almeidadistribuidor" in url
                else "dantas" if "dantasdistribuidora" in url else None)
        if forn:
            pedidos.append((off, forn, url))
    if not pedidos:
        return

    barra = st.progress(0.0, text="Consultando NCM dos produtos escolhidos…")

    def _cb(feitos, total):
        barra.progress(feitos / max(total, 1),
                       text=f"Consultando NCM… {feitos}/{total}")

    try:
        ncms, falhas = consultar_ncms(pedidos, progresso=_cb)
    except Exception as exc:
        barra.empty()
        _log.exception("falha ao consultar NCM")
        style.bloco_aviso(erros.mensagem_amigavel(exc, "consultar o NCM"),
                          tipo="warning")
        return
    barra.empty()
    # Só os resultados DEFINITIVOS (item visitado) entram no cache e nas
    # seleções. Os transitórios (login/navegador falhou) NÃO são cacheados —
    # senão um soluço travava o item "sem NCM" pelo resto da sessão e o
    # re-clique não re-tentava (auditoria 2026-06, mesmo padrão do bloco 3).
    overrides = st.session_state.get("markup_overrides", {})
    for off, _forn, url in pedidos:
        if off in falhas:
            continue
        ncm = ncms.get(off)
        cache[url] = ncm
        selecoes[off] = replace(selecoes[off], ncm=ncm)
        # Identificar o NCM SUPERA o palpite manual feito quando o item era
        # "sem NCM": limpa o override obsoleto para a classificação automática
        # valer (defesa em profundidade junto com classificar_markups).
        if ncm:
            overrides.pop(off, None)
    if falhas:
        style.bloco_aviso(
            f"{len(falhas)} item(ns) não puderam ser consultados agora "
            "(login do fornecedor ou navegador). Os demais foram "
            "classificados. Clique em <b>Identificar NCM</b> de novo para "
            "tentar os que faltaram.",
            tipo="warning")


def _painel_markup(selecoes: dict, itens: list) -> tuple[dict, set]:
    """Painel "Markup deste pregão" (Fase 4 — Substituição Tributária).

    Gerencia POR EXCEÇÃO: o automático classifica cada item pelo NCM (ST →
    markup menor; normal → maior); o operador só ajusta os DOIS markups do
    pregão e decide os itens SEM NCM (nada é presumido ST em silêncio —
    princípio Resp 7). Devolve ``(markups {offset -> markup}, com_st {offsets
    com Substituição Tributária})`` para o exportador.
    """
    from pregoes import tributacao

    st.markdown("#### Markup deste pregão")
    c1, c2 = st.columns(2)
    m_st = c1.number_input(
        "Itens com Substituição Tributária", min_value=1.0, step=0.05,
        value=float(st.session_state.get("mk_st", config.MARKUP_ST)),
        format="%.2f", key="mk_st",
        help="O NCM consta na lista de ST do estado → ICMS já recolhido na "
             "origem, markup menor.")
    m_normal = c2.number_input(
        "Itens normais", min_value=1.0, step=0.05,
        value=float(st.session_state.get("mk_normal", config.MARKUP_NORMAL)),
        format="%.2f", key="mk_normal")

    overrides = st.session_state.setdefault("markup_overrides", {})
    ncms = {off: getattr(s, "ncm", None) for off, s in selecoes.items()}
    markups, resumo, sem_ncm = tributacao.classificar_markups(
        ncms, m_st, m_normal, overrides)

    st.caption(
        f":green[{resumo['normal']} normais]  ·  "
        f":blue[{resumo['st']} com Substituição Tributária]  ·  "
        f":orange[{resumo['sem_ncm']} sem NCM identificado]")

    # O NCM mora na página de detalhe do fornecedor (não no card). Este botão
    # visita os produtos ESCOLHIDOS sem NCM e os classifica (cache por pregão).
    fetchaveis = [off for off in sem_ncm if getattr(selecoes.get(off), "url", None)]
    if fetchaveis:
        if st.button(
                f"Identificar NCM de {len(fetchaveis)} item(ns) e classificar "
                "Substituição Tributária", key="btn_ncm"):
            _identificar_ncm(selecoes, fetchaveis)
            st.rerun()

    # Exceções: itens SEM NCM — o operador decide (default = normal).
    if sem_ncm:
        with st.expander(f"{len(sem_ncm)} itens sem NCM — confira o markup",
                         icon=":material/warning:"):
            for off in sem_ncm:
                item = itens[off] if 0 <= off < len(itens) else None
                desc = (getattr(item, "descricao", None) or f"Item {off + 1}")[:54]
                escolha = st.radio(
                    desc, ("Normal", "Com Substituição Tributária"),
                    horizontal=True,
                    index=1 if overrides.get(off) == m_st else 0,
                    key=f"ov_markup_{off}")
                overrides[off] = (m_st if escolha == "Com Substituição Tributária"
                                  else m_normal)
        markups, _, _ = tributacao.classificar_markups(
            ncms, m_st, m_normal, overrides)
    # Conjunto ST decidido EXPLICITAMENTE (por NCM ou pela escolha do operador),
    # NÃO inferido do valor do markup — é a fonte da coluna "SUBST. TRIBUTÁRIA"
    # da planilha. Assim a coluna não mente quando os dois markups são iguais
    # (achado QA M1): item normal por NCM nunca é marcado ST.
    com_st = tributacao.com_st_de_ncms(ncms, m_st, m_normal, overrides)
    return markups, com_st


def _carregar_catalogo_sessao():
    """Carrega o catálogo do cliente (produtos) UMA vez por sessão para a
    busca manual. Lista vazia se o catálogo não foi populado."""
    if "catalogo_lista" not in st.session_state:
        from pregoes.matcher import carregar_catalogo
        try:
            with get_conn() as conn:
                st.session_state.catalogo_lista = carregar_catalogo(conn)
        except Exception:
            st.session_state.catalogo_lista = []
    return st.session_state.catalogo_lista


def _buscar_no_catalogo(catalogo: list, consulta: str, limite: int = 6) -> list:
    """Busca um termo no catálogo do cliente. Retorna ProdutoCatalogo ordenados.

    Estilo de busca de loja (pedido do cliente — antes exigia a descrição quase
    exata):
    - CÓDIGO: consulta só com dígitos → casa codigo_almeida/dantas (exato ou
      prefixo). Ex.: "8811".
    - PALAVRAS: acha descrições que contêm TODAS as palavras (substring), assim
      "tubo" acha tubos e "tubo pvc 75" acha o de 75mm (o '75' bate em '75mm').
      Ordena por proximidade (token_set_ratio) e tamanho.
    - FALLBACK: se nenhuma descrição contém todas as palavras (typo/variação),
      cai num fuzzy tolerante (token_set_ratio) para ainda sugerir algo.
    """
    from rapidfuzz import process, fuzz
    from pregoes.normalizador import normalizar_descricao
    q = (consulta or "").strip()
    if not q or not catalogo:
        return []
    # 1) Busca por CÓDIGO.
    so_digitos = q.replace(" ", "")
    if so_digitos.isdigit() and len(so_digitos) >= 2:
        return [
            p for p in catalogo
            if any((c or "").strip() == so_digitos
                   or (c or "").strip().startswith(so_digitos)
                   for c in (p.codigo_almeida, p.codigo_dantas))
        ][:limite]
    # 2) Busca por PALAVRAS — descrição contém TODAS (AND por substring).
    alvo = normalizar_descricao(q)
    palavras = [w for w in alvo.split() if len(w) >= 2]
    escolhas = {p.produto_id: (p.descricao_canonica or "") for p in catalogo}
    por_id = {p.produto_id: p for p in catalogo}
    if palavras:
        contendo = [(pid, d) for pid, d in escolhas.items()
                    if all(w in d for w in palavras)]
        if contendo:
            contendo.sort(key=lambda x: (-fuzz.token_set_ratio(alvo, x[1]),
                                         len(x[1])))
            return [por_id[pid] for pid, _ in contendo[:limite]]
    # 3) Fallback fuzzy (typo/variação).
    r = process.extract(alvo, escolhas, scorer=fuzz.token_set_ratio,
                        limit=limite, score_cutoff=60)
    return [por_id[pid] for _, _, pid in r]


def _busca_catalogo_item(idx: int, item) -> None:
    """Busca MANUAL no catálogo interno do cliente, por item (rede de segurança
    quando a busca automática nos fornecedores não satisfaz — pedido do cliente).

    Procura produtos que o cliente JÁ TEM (por palavras OU código); mostra marca,
    código do fornecedor e a última cotação conhecida; e permite escolher um
    como opção do item (entra com o badge 'no seu catálogo' e já pré-selecionado).
    O preço da opção é cotado AO VIVO ao usar — ver _usar_do_catalogo.
    """
    catalogo = _carregar_catalogo_sessao()
    with st.expander("🔍 Buscar no meu catálogo (produtos que já tenho)"):
        if not catalogo:
            st.caption("Catálogo interno vazio — importe suas planilhas "
                       "históricas em Configurações para usar esta busca.")
            return
        consulta = st.text_input(
            "Procurar por palavras ou código", value="", key=f"catq_{idx}",
            placeholder="ex.: tubo pvc 75  ·  válvula lavatório  ·  8811",
            help="Busque por palavras (acha quem contém todas) ou pelo código "
                 "do fornecedor. Nos produtos que você já comprou.",
        )
        if not consulta.strip():
            st.caption(f"Digite palavras (ex.: 'tubo pvc 75') ou um código para "
                       f"procurar nos seus {len(catalogo)} produtos.")
            return

        achados = _buscar_no_catalogo(catalogo, consulta)
        if not achados:
            st.caption("Nenhum produto encontrado no seu catálogo. Tente outras "
                       "palavras ou o código do fornecedor.")
            return
        st.caption("O preço abaixo é só **referência da última compra**. Ao "
                   "usar, o sistema busca o **preço atual** no site do "
                   "fornecedor pelo código (preço muda todo dia).")
        with get_conn() as conn:
            for p in achados:
                for forn, codigo in (("almeida", p.codigo_almeida),
                                     ("dantas", p.codigo_dantas)):
                    if not (codigo and str(codigo).strip()):
                        continue
                    codigo = str(codigo).strip()
                    cot = _db.ultima_cotacao(conn, p.produto_id, forn)
                    if cot:
                        ref_txt = (f"referência: R$ {float(cot['preco']):.2f} "
                                   f"(últ. compra {cot['consultado_em'][:10]})")
                    else:
                        ref_txt = "sem referência de preço"
                    c1, c2 = st.columns([5, 1.4], vertical_alignment="center")
                    with c1:
                        st.markdown(
                            f"**{html.escape(p.marca or 'Sem marca')}** · "
                            f"{html.escape((p.descricao_canonica or '')[:58])}<br>"
                            f"<span style='color:#64748b;font-size:0.82rem'>"
                            f"{forn.capitalize()} · "
                            f"<code>cód. {html.escape(codigo)}</code> · "
                            f"{ref_txt}</span>",
                            unsafe_allow_html=True)
                    with c2:
                        usar = st.button(
                            "Usar (preço atual)",
                            key=f"usecat_{idx}_{p.produto_id}_{forn}",
                            width="stretch")
                    if usar:
                        _usar_do_catalogo(idx, p, forn, codigo)


def _usar_do_catalogo(idx: int, produto, forn: str, codigo: str) -> None:
    """Cota o produto do catálogo AO VIVO (pelo código) e o adiciona como opção
    escolhida do item. NUNCA usa o preço histórico do catálogo — preço muda
    todo dia (requisito do cliente, 2026-06). Se a cotação ao vivo falhar,
    avisa e não grava preço algum (não cai no preço velho)."""
    from pregoes.scraper import ResultadoBusca, cotar_por_codigo
    from pregoes.exportador import SelecaoFornecedor

    with st.spinner(f"Buscando o preço atual do código {codigo} na "
                    f"{forn.capitalize()}…"):
        try:
            ao_vivo = cotar_por_codigo(forn, codigo)
        except Exception as exc:
            _log.exception("falha ao cotar por código no catálogo")
            style.bloco_aviso(
                "Não consegui consultar o preço atual agora "
                f"({erros.mensagem_amigavel(exc)}). O preço do catálogo "
                "<b>não</b> é usado. Tente de novo em instantes.",
                tipo="warning")
            return

    if ao_vivo is None:
        style.bloco_aviso(
            f"O código <code>{html.escape(codigo)}</code> não foi encontrado "
            f"agora no site da {forn.capitalize()} — o produto pode ter saído "
            "de linha. Confira diretamente no site antes de usar.",
            tipo="warning")
        return

    # Preço e marca vêm do site (atuais); o vínculo com o catálogo (badge +
    # produto_id) é preservado para o reconhecimento "no seu catálogo".
    novo = ResultadoBusca(
        descricao_encontrada=ao_vivo.descricao_encontrada
        or produto.descricao_canonica or "",
        marca=ao_vivo.marca or produto.marca or "",
        preco=ao_vivo.preco,
        codigo_fornecedor=codigo,
        confianca="exato", fornecedor=forn,
        url=getattr(ao_vivo, "url", None),
        no_catalogo=True, produto_catalogo_id=produto.produto_id,
        descricao_catalogo=produto.descricao_canonica)
    st.session_state.resultados_busca.setdefault(idx, []).append(novo)
    st.session_state.selecoes[idx] = SelecaoFornecedor(
        preco=novo.preco, codigo_fornecedor=novo.codigo_fornecedor,
        marca=novo.marca, fornecedor=novo.fornecedor)
    st.rerun()


def _busca_site_item(idx: int, item) -> None:
    """Busca MANUAL ao vivo no site dos fornecedores, por nome OU código
    (rede de segurança final — pedido do cliente 2026-06). Quando nem a busca
    automática nem o catálogo trazem o esperado, o operador digita o termo,
    escolhe o fornecedor e busca direto no site, SEM filtro (vê o que o site
    devolve). O resultado escolhido entra como opção do item — e, ao gerar a
    planilha, é APRENDIDO: na próxima vez vem sozinho (retroalimentação)."""
    fornecedores = [
        nome for nome in ("almeida", "dantas")
        if credenciais.carregar(nome) is not None
    ]
    with st.expander("🌐 Buscar direto no site do fornecedor (nome ou código)"):
        if not fornecedores:
            st.caption("Configure as credenciais dos fornecedores em "
                       "Configurações para buscar direto no site.")
            return
        c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
        with c1:
            termo = st.text_input(
                "Nome ou código do produto", value="", key=f"siteq_{idx}",
                placeholder="ex.: alicate de pressão  ·  8811",
                help="Busca AO VIVO no site, sem filtro — você vê o que o site "
                     "devolve e escolhe. O preço é o atual do site.")
        with c2:
            forn_sel = st.radio(
                "Fornecedor", fornecedores,
                format_func=lambda f: f.capitalize(),
                horizontal=True, key=f"siteforn_{idx}")
        buscar = st.button("Buscar no site ao vivo", key=f"sitego_{idx}")
        chave_res = f"site_res_{idx}"
        if buscar and termo.strip():
            from pregoes.scraper import buscar_no_site
            with st.spinner(f"Buscando “{termo.strip()}” na "
                            f"{forn_sel.capitalize()}…"):
                try:
                    achados = buscar_no_site(forn_sel, termo.strip())
                    st.session_state[chave_res] = (forn_sel, achados)
                except Exception as exc:
                    _log.exception("falha na busca manual no site")
                    style.bloco_aviso(
                        "Não consegui buscar no site agora "
                        f"({erros.mensagem_amigavel(exc)}). Tente de novo em "
                        "instantes.", tipo="warning")
                    st.session_state.pop(chave_res, None)
        elif buscar:
            st.caption("Digite um nome ou código para buscar.")

        dados = st.session_state.get(chave_res)
        if not dados:
            return
        forn_res, achados = dados
        if not achados:
            st.caption(f"Nada encontrado na {forn_res.capitalize()} para esse "
                       "termo. Tente outras palavras ou o código.")
            return
        st.caption(f"{len(achados)} resultado(s) na {forn_res.capitalize()} — "
                   "**preço atual do site**. Clique em **Usar** no escolhido.")
        for j, r in enumerate(achados):
            cc1, cc2 = st.columns([5, 1.4], vertical_alignment="center")
            with cc1:
                st.markdown(
                    f"**{html.escape(r.marca or 'Sem marca')}** · "
                    f"R$ {r.preco:.2f}<br>"
                    f"<span style='color:#64748b;font-size:0.82rem'>"
                    f"{html.escape((r.descricao_encontrada or '')[:64])} · "
                    f"<code>cód. {html.escape(r.codigo_fornecedor or '')}</code>"
                    f"</span>", unsafe_allow_html=True)
            with cc2:
                if st.button("Usar", key=f"siteuse_{idx}_{j}", width="stretch"):
                    _usar_do_site(idx, r)


def _usar_do_site(idx: int, r) -> None:
    """Adiciona o resultado da busca manual no site como opção escolhida do
    item (preço e marca atuais do site). Limpa o resultado da busca para a
    tela voltar limpa."""
    from pregoes.exportador import SelecaoFornecedor
    st.session_state.resultados_busca.setdefault(idx, []).append(r)
    st.session_state.selecoes[idx] = SelecaoFornecedor(
        preco=r.preco, codigo_fornecedor=r.codigo_fornecedor,
        marca=r.marca, ncm=getattr(r, "ncm", None), url=getattr(r, "url", None),
        fornecedor=getattr(r, "fornecedor", None))
    st.session_state.pop(f"site_res_{idx}", None)
    st.rerun()


def _chave_default_opcao(item_desc: str, o):
    """Ordena candidatos para o DEFAULT e para o topo da lista:
    1º a ESCOLHA ANTERIOR do operador para esta descrição (retroalimentação —
    a inteligência central; sempre vence), 2º o que JÁ ESTÁ no catálogo do
    cliente por código, 3º código forte do edital, 4º relevância, 5º menor
    preço. Sem nada disso, idêntico ao critério anterior (relevância, preço).

    Nível de módulo (extraído de _mostrar_selecao em 2026-06) para ser testável
    sem streamlit.testing.AppTest — é o contrato de pré-seleção da planilha."""
    from pregoes.normalizador import relevancia as _relev
    return (-int(getattr(o, "veio_por_aprendizado", False)),
            -int(getattr(o, "no_catalogo", False)),
            -_bonus_codigo(item_desc, o.descricao_encontrada),
            -_relev(item_desc, o.descricao_encontrada), o.preco)


def _melhor_opcao(item_desc: str, opcoes: list):
    """Melhor default: aprendido > catálogo > código > relevância > preço."""
    return min(opcoes, key=lambda o: _chave_default_opcao(item_desc, o))


def _mostrar_selecao(itens: list, resultados: dict) -> None:
    """Lista paginada de itens; cada candidato vira um card. Operador escolhe.

    Default de seleção = candidato com MAIOR RELEVÂNCIA contra a descrição
    do edital, com PREÇO como tiebreaker. Mudou de "mais barato" para
    "melhor match" em 2026-05-27 após operador reportar que itens com
    múltiplas categorias semelhantes (ex.: chave de fenda comum vs chave-
    teste) escolhiam por preço o produto mais simples — geralmente o
    errado.

    Mantém o princípio do briefing (CLAUDE.md §7): o sistema só sugere,
    o operador escolhe.
    """
    from pregoes.exportador import SelecaoFornecedor

    # Ranking extraído para nível de módulo (testável sem AppTest).
    _chave_default = _chave_default_opcao
    _melhor = _melhor_opcao

    # Inicializa estado de seleções com a opção de MAIOR RELEVÂNCIA por item
    if "selecoes" not in st.session_state:
        st.session_state.selecoes = {}
        for idx, opcoes in resultados.items():
            if opcoes:
                melhor = _melhor(itens[idx].descricao, opcoes)
                st.session_state.selecoes[idx] = SelecaoFornecedor(
                    preco=melhor.preco,
                    codigo_fornecedor=melhor.codigo_fornecedor,
                    marca=melhor.marca,
                    ncm=getattr(melhor, "ncm", None),
                    url=getattr(melhor, "url", None),
                    fornecedor=getattr(melhor, "fornecedor", None),
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
        # Dedup já aqui: o contador "X opções" e os cards exibidos usam a mesma
        # lista, e nenhum produto duplicado acende dois "escolhido".
        opcoes_todas = _dedup_opcoes(resultados.get(idx, []))
        # Mostra só as N mais relevantes (ordena pela MESMA chave do default —
        # 'no seu catálogo' e melhor match no topo). Reduz o ruído de 10 opções
        # (feedback do cliente 2026-06); para outra marca há a busca no catálogo.
        opcoes_todas = sorted(
            opcoes_todas, key=lambda o: _chave_default(item.descricao, o))
        n_total = len(opcoes_todas)
        opcoes = opcoes_todas[:config.MAX_OPCOES_EXIBIDAS]
        n_ocultas = n_total - len(opcoes)
        sel_atual = st.session_state.selecoes.get(idx)

        n_op = len(opcoes)
        if not opcoes:
            label_status = "Não encontrado"
            tipo_status: style.TipoBadge = "danger"
        elif any(o.confianca == "variacao" for o in opcoes):
            label_status = f"{n_op} a conferir"
            tipo_status = "warning"
        else:
            label_status = f"{n_op} {'opção' if n_op == 1 else 'opções'}"
            tipo_status = "success"

        # Cabeçalho: ícone de status (Material Symbols, consistente entre SOs)
        # + "Item N" (em vez de "#N", que soava pouco profissional) + título
        # legível + status colorido.
        cor_status = {"success": "green", "warning": "orange",
                      "danger": "red"}[tipo_status]
        icone_status = {"success": ":material/check_circle:",
                        "warning": ":material/help:",
                        "danger": ":material/cancel:"}[tipo_status]
        # Mostra o NÚMERO DO ITEM NO EDITAL (item.item_numero) — pedido do
        # cliente 2026-06: ele identifica "que produto é este no edital?" pelo
        # ID, igual ao da coluna ITEM da planilha. Fallback ao sequencial 1-based
        # quando o PDF não trouxe o número (ver _numero_item_edital).
        cabecalho = (
            f"**Item {_numero_item_edital(item, idx)}** · "
            f"{_titulo_item(item.descricao)} · :{cor_status}[{label_status}]"
        )
        with st.expander(cabecalho, expanded=False, icon=icone_status):
            # Descrição COMPLETA e exata do edital. O cabeçalho encurta para
            # caber na linha; aqui o operador lê o item inteiro (como veio do
            # edital, sem capitalização nem corte) para julgar os candidatos.
            st.markdown(
                "<div style='background:#f8fafc;border:1px solid #e2e8f0;"
                "border-left:3px solid #1E40AF;border-radius:8px;"
                "padding:0.6rem 0.85rem;margin-bottom:0.75rem;font-size:0.9rem;"
                "color:#334155;line-height:1.5'>"
                f"{html.escape(item.descricao or '')}</div>",
                unsafe_allow_html=True,
            )
            # Sem resultado dos fornecedores: NÃO pula o item (antes havia um
            # `continue` que escondia a busca no catálogo e o "Não usar").
            # Mostra o aviso e segue para a busca manual no catálogo (pedido do
            # cliente 2026-06: poder buscar no catálogo igual aos demais casos).
            if not opcoes:
                style.bloco_aviso(
                    "Nenhum resultado nos fornecedores nesta busca. Use a "
                    "<b>Buscar no meu catálogo</b> abaixo, ou deixe em branco "
                    "(a coluna de preço fica vazia para preencher manualmente).",
                    tipo="warning",
                )
            else:
                # Mesma chave do default (_chave_default): código forte do edital,
                # depois relevância, depois preço — para o topo da lista coincidir
                # com a opção pré-selecionada.
                item_desc_local = item.descricao
                opcoes_ordenadas = sorted(
                    opcoes, key=lambda o: _chave_default(item_desc_local, o))
                preco_min = min(o.preco for o in opcoes_ordenadas)

                def _esta_selecionado(o, s=sel_atual):
                    return (s is not None and o.preco == s.preco
                            and o.marca == s.marca
                            and o.codigo_fornecedor == s.codigo_fornecedor)

                st.caption("Escolha a marca e o preço (sugerimos o melhor match):")
                if n_ocultas > 0:
                    st.caption(
                        f"Mostrando as {len(opcoes)} opções mais relevantes "
                        f"(+{n_ocultas} ocultas). Precisa de outra marca? Use "
                        "**Buscar no meu catálogo** abaixo.")
                for pos, o in enumerate(opcoes_ordenadas):
                    escolhido = _esta_selecionado(o)
                    # "Sua escolha anterior" (retroalimentação) é o sinal MAIS
                    # forte — o próprio operador já resolveu este item antes.
                    # Depois "Já no seu catálogo" (produto que ele compra, casado
                    # pelo código do fornecedor).
                    if getattr(o, "veio_por_aprendizado", False):
                        badge_html = style.pill("✓ sua escolha anterior", "aprendido")
                    elif getattr(o, "no_catalogo", False):
                        badge_html = style.pill("✓ no seu catálogo", "catalogo")
                    elif pos == 0:
                        badge_html = style.pill("Melhor match", "match")
                    elif o.preco == preco_min:
                        badge_html = style.pill("Mais barato", "barato")
                    else:
                        badge_html = ""
                    _card_opcao(
                        idx, pos, o, badge_html=badge_html, escolhido=escolhido,
                        on_escolher=lambda o=o: _escolher_opcao(idx, o),
                    )

            # Rede de segurança: buscar no catálogo interno do cliente — SEMPRE
            # disponível, inclusive quando não houve resultado nos fornecedores
            # (pedido do cliente 2026-06).
            _busca_catalogo_item(idx, item)

            # Rede de segurança final: buscar AO VIVO direto no site (nome ou
            # código), escolhendo o fornecedor — quando nem a busca automática
            # nem o catálogo trazem o esperado (pedido do cliente 2026-06).
            _busca_site_item(idx, item)

            # Opção de não usar este item
            sem_sel = sel_atual is None
            if st.button(("✓ " if sem_sel else "") + "Não usar este item",
                         key=f"naousar_{idx}", width="stretch"):
                st.session_state.selecoes.pop(idx, None)
                st.rerun()

    st.divider()
    st.caption(f"{selecionados} de {total} itens com marca/preço selecionados.")

    # Markup por item via Substituição Tributária (Fase 4).
    markups, itens_com_st = _painel_markup(st.session_state.selecoes, itens)

    if st.button("Gerar planilha matriz com preços", type="primary",
                 width="stretch"):
        # Usa itens_revisados (populado em tela_revisao a partir do
        # estado ATUAL do data_editor, via `edited`). df_editavel não
        # serve aqui porque é só snapshot inicial — não reflete as
        # marcações do operador feitas em tempo real no widget.
        itens = st.session_state.get("itens_revisados") or []
        markup = st.session_state.get("markup", config.MARKUP_DEFAULT)
        # Camada 4 (Q12): registra escolhas como aprendizado. Próximas
        # buscas da MESMA descrição reusam direto a marca/código via
        # `db.aprendido_buscar`, pulando scraper textual.
        _registrar_aprendizado(itens, st.session_state.selecoes,
                                st.session_state.get("resultados_busca") or {})
        _gerar_excel(itens, markup=markup, selecoes=st.session_state.selecoes,
                     markups=markups, itens_com_st=itens_com_st)


def _registrar_aprendizado(itens: list, selecoes: dict, resultados: dict) -> None:
    """Grava em `mapeamentos_aprendidos` cada (descrição → marca/código)
    escolhida pelo operador. Idempotente: UPSERT por (descricao_norm, forn).

    Persistir tanto seleções 'default' (mais barato) quanto manuais — a
    intenção é a mesma: "para esta descrição, esta foi a escolha válida".
    Volume previsto: 500 itens × 2 fornecedores = ≤1000 registros/edital,
    insignificante no SQLite.
    """
    from pregoes import db as _db
    from pregoes.normalizador import normalizar_descricao as _norm

    if not itens or not selecoes:
        return
    try:
        with get_conn() as conn:
            for idx, item in enumerate(itens):
                sel = selecoes.get(idx)
                if sel is None:
                    continue
                desc_norm = _norm(item.descricao)
                if not desc_norm:
                    continue
                # Fornecedor: gravado DIRETO na seleção (robusto). Fallback ao
                # re-match por (preço,código,marca) — só para seleções legadas
                # sem o campo. Antes, o re-match era a ÚNICA via e descartava o
                # aprendizado em silêncio quando não casava (float/espaço na
                # marca) — furo da retroalimentação (achado QA 2026-06).
                fornecedor = getattr(sel, "fornecedor", None)
                if not fornecedor:
                    for o in resultados.get(idx, []):
                        if (o.preco == sel.preco
                                and o.codigo_fornecedor == sel.codigo_fornecedor
                                and o.marca == sel.marca):
                            fornecedor = o.fornecedor
                            break
                if not fornecedor:
                    continue
                _db.aprendido_gravar(
                    conn, desc_norm, fornecedor,
                    sel.codigo_fornecedor, sel.marca, sel.preco,
                )
    except Exception:
        # Aprendizado é melhoria progressiva — falha aqui não bloqueia a
        # geração da planilha. Vai para data/pregoes.log (diagnóstico
        # remoto), a UI segue normal.
        _log.exception("falha ao registrar aprendizado de seleções")


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
    st.dataframe(df_preview, width="stretch", hide_index=True)

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


# === Aba Catálogo (em Configurações) ===

def _aba_catalogo():
    """Importa planilhas históricas (formato MATRIZ) para popular a
    Camada 0 da busca de preços.

    Mostra status: quantos produtos no catálogo, quantos com código por
    fornecedor. Após upload + clique em "Importar", chama o módulo
    `pregoes.importador` e mostra os números retornados.
    """
    from pregoes import importador as _importador

    style.bloco_aviso(
        "O <b>catálogo histórico</b> acelera a busca de preços nos "
        "próximos pregões. Quando um item do edital casa com um produto "
        "que você já comprou no passado, o sistema vai direto pelo "
        "<b>código do produto</b> nos fornecedores — sem precisar "
        "adivinhar pela descrição.",
        tipo="info",
    )

    # Estatísticas atuais
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
        com_almeida = conn.execute(
            "SELECT COUNT(*) FROM produtos WHERE codigo_almeida IS NOT NULL AND codigo_almeida != ''"
        ).fetchone()[0]
        com_dantas = conn.execute(
            "SELECT COUNT(*) FROM produtos WHERE codigo_dantas IS NOT NULL AND codigo_dantas != ''"
        ).fetchone()[0]
        n_historico = conn.execute("SELECT COUNT(*) FROM precos_historico").fetchone()[0]

    with get_conn() as conn:
        n_canon = conn.execute("SELECT COUNT(*) FROM produtos_canonico").fetchone()[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Produtos no catálogo", total)
    c2.metric("Com código Almeida", com_almeida)
    c3.metric("Com código Dantas", com_dantas)
    c4.metric("Nomes padronizados (IA)", n_canon)

    # A padronização de nomes (IA) roda AUTOMÁTICA ao importar — invisível ao
    # operador. Em modo dev, um botão permite re-rodar/forçar.
    if _DEBUG and total > 0 and (total - n_canon) > 0:
        if st.button(f"[dev] Padronizar {total - n_canon} nomes (IA)"):
            from pregoes import alinhador
            with st.spinner("Padronizando…"):
                alinhador.normalizar_catalogo()
            st.rerun()

    st.divider()
    st.markdown(
        "**Importe suas planilhas de pregões anteriores** (formato MATRIZ). "
        "O sistema lê descrição, código, marca e preço, e usa esse histórico "
        "para encontrar os produtos mais rápido nos próximos editais. "
        "Re-enviar a mesma planilha não duplica nada."
    )

    uploads = st.file_uploader(
        "Planilhas históricas (.xlsx)",
        type=["xlsx"],
        accept_multiple_files=True,
        key="catalogo_uploads",
    )
    fornecedor = st.radio(
        "Fornecedor principal destas planilhas:",
        options=["almeida", "dantas"],
        index=0,
        horizontal=True,
        help=(
            "Os códigos sem a letra no fim são deste fornecedor. Códigos "
            "que terminam em D vão para a Dantas e A para a Almeida "
            "automaticamente."
        ),
    )

    if uploads and st.button("Importar planilhas", type="primary"):
        # Salva uploads em data/historico com nome DETERMINÍSTICO (= nome
        # original sanitizado). O importador deriva a chave de dedup do
        # basename (fonte="import:<basename>"), então re-enviar a MESMA
        # planilha precisa gerar o MESMO path.name — senão a dedup não
        # reconhece e os preços duplicam (a UI promete que não duplica).
        # Nome aleatório (tempfile) quebrava essa idempotência.
        pasta_historico = config.DATA_DIR / "historico"
        paths = []
        try:
            # Numa instalação nova a pasta pode não existir: criar antes de
            # escrever evita FileNotFoundError cru para o operador.
            pasta_historico.mkdir(parents=True, exist_ok=True)
            for upl in uploads:
                destino = pasta_historico / _sanitizar_nome_arquivo(upl.name)
                destino.write_bytes(upl.read())
                paths.append(destino)
        except Exception as exc:
            _log.exception("falha ao salvar planilhas para importação")
            style.bloco_aviso(
                erros.mensagem_amigavel(exc, "salvar as planilhas para importação"),
                tipo="danger",
            )
            return

        forn_arg = None if fornecedor.startswith("nenhum") else fornecedor

        with st.spinner(f"Importando {len(paths)} planilha(s)…"):
            try:
                stats = _importador.importar(paths, fornecedor=forn_arg)
            except Exception as exc:
                _log.exception("falha ao importar planilhas")
                style.bloco_aviso(
                    erros.mensagem_amigavel(exc, "importar as planilhas"),
                    tipo="danger",
                )
                return

        # A padronização (etapa 2) só roda se houver chave Gemini. O texto
        # do sucesso é condicional a isso: anunciar "Etapa 1 de 2" sem que a
        # etapa 2 vá acontecer confundiria o operador (a tela mostrava um
        # "✓ concluído" verde e um "preparando…" ao mesmo tempo — ambíguo).
        vai_padronizar = bool(credenciais.carregar_chave_api("gemini"))
        if vai_padronizar:
            cabecalho = ("<b>Etapa 1 de 2 — importação concluída e salva.</b><br>"
                         "Agora o sistema prepara o catálogo para a busca "
                         "(etapa 2, abaixo). Os produtos já estão salvos mesmo "
                         "se você sair.<br>")
        else:
            cabecalho = "<b>Importação concluída e salva.</b><br>"
        style.bloco_aviso(cabecalho + stats.resumo(), tipo="success")
        if stats.avisos:
            with st.expander(f"⚠️ Avisos ({len(stats.avisos)})"):
                for av in stats.avisos:
                    st.markdown(f"- {av}")

        # Padroniza os nomes automaticamente (Fase B) — prepara o catálogo
        # para a busca por código. Invisível ao operador; se falhar (sem
        # chave/quota), a busca apenas cai no modo textual.
        if vai_padronizar:
            from pregoes import alinhador
            barra = st.progress(
                0.0, text="Etapa 2 de 2 — preparando o catálogo para a busca…")
            try:
                stats_norm = alinhador.normalizar_catalogo(
                    callback=lambda f, t: barra.progress(
                        min(f / max(t, 1), 1.0),
                        text=f"Etapa 2 de 2 — preparando o catálogo… {f}/{t}"))
                barra.progress(
                    1.0, text="Etapa 2 de 2 concluída — catálogo pronto para a busca.")
                # Cobertura real — avisa se ficou parcial (quota/rede).
                if stats_norm.get("falhos"):
                    style.bloco_aviso(
                        f"{stats_norm['falhos']} produto(s) não foram "
                        "padronizados (limite da IA ou internet). A importação "
                        "está salva e a busca funciona; para cobertura total "
                        "clique em importar de novo mais tarde.", tipo="warning")
            except Exception as exc:
                _log.exception("falha ao padronizar catálogo")
                barra.empty()
                style.bloco_aviso(
                    "A importação está salva, mas não consegui padronizar os "
                    f"nomes agora ({erros.mensagem_amigavel(exc)}). A busca "
                    "funciona mesmo assim; tente importar de novo mais tarde.",
                    tipo="warning")
        st.rerun()


# === Roteador ===
TELAS = {
    "home": tela_home,
    "historico": tela_historico,
    "upload": tela_upload,
    "revisao": tela_revisao,
    "mapeamento": tela_mapeamento,
    "busca_precos": tela_busca_precos,
    "configuracoes": tela_configuracoes,
}

_renderizar_sidebar()
TELAS[st.session_state.tela]()

# Confirmação de "Novo pregão" — aberta aqui, no nível superior do script
# (fora do `with st.sidebar`), para o modal não renderizar dentro da sidebar.
if st.session_state.get("_confirmar_novo_pregao"):
    _confirmar_novo_pregao()
