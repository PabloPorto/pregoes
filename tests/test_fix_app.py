"""Testes de regressão para os fixes do grupo `app.py`.

Cobrem as funções PURAS extraídas de ``pregoes/ui/app.py``:

- ``_fundir_edicoes`` + ``_aplicar_delta_editor`` — preservar as edições de
  célula do operador ao clicar "Marcar/Desmarcar todos" (fix #1).
- ``_sanitizar_nome_arquivo`` — nome determinístico no salvamento do upload
  histórico, garantindo a idempotência da re-importação (fix #10).

Importar ``app.py`` executa o corpo do módulo (st.set_page_config, init_db,
backup diário…). Como aqui só queremos as funções puras, *dublamos* o
Streamlit e os módulos que tocam disco/banco ANTES do import — assim o teste
roda sem rede, sem navegador e sem mexer no banco real (outros agentes editam
em paralelo).
"""
from __future__ import annotations

import sys
import types as _t
from pathlib import Path

import pandas as pd
import pytest


# --- Dublês instalados em sys.modules antes de importar app.py -------------

class _FakeSessionState(dict):
    """session_state do Streamlit: aceita acesso por atributo e por chave."""

    def __getattr__(self, nome):
        try:
            return self[nome]
        except KeyError as exc:  # pragma: no cover - comportamento defensivo
            raise AttributeError(nome) from exc

    def __setattr__(self, nome, valor):
        self[nome] = valor


def _noop(*_args, **_kwargs):
    return None


class _Falsy:
    """Sentinela FALSY usado como retorno default de qualquer widget Streamlit.

    Por que falsy: as funções de tela de app.py rodam no import (o corpo do
    módulo despacha a tela atual). Se ``st.button(...)`` devolvesse algo
    truthy, TODOS os ramos de ação disparariam (salvar credencial, importar…)
    com efeito colateral real. Sendo falsy, nada dispara. Também serve de
    context manager (``with st.spinner():``) e é iterável vazio.
    """

    def __bool__(self):
        return False

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())

    def __getattr__(self, _nome):
        return self


_SESSION_STATE = _FakeSessionState()


class _FakeStreamlit(_t.ModuleType):
    """Streamlit falso: widgets devolvem ``_Falsy`` (nenhuma ação dispara);
    ``columns``/``tabs`` devolvem tuple do tamanho certo p/ unpacking;
    ``session_state`` é um dict-like real; ``column_config`` aceita atributos."""

    def __init__(self):
        super().__init__("streamlit")

    @property
    def session_state(self):
        return _SESSION_STATE

    @property
    def column_config(self):
        return _Falsy()

    def columns(self, *a, **k):
        return _n_falsy(*a, **k)

    def tabs(self, *a, **k):
        return _n_falsy(*a, **k)

    def __getattr__(self, _nome):
        # Qualquer outro atributo (button, text_input, spinner, sidebar…)
        # é um callable/context-manager falsy.
        return _Falsy()


def _n_falsy(*a, **k):
    # N de columns(n)/tabs([...]): int → n; lista → len; default 2.
    if a and isinstance(a[0], int):
        n = a[0]
    elif a and hasattr(a[0], "__len__"):
        n = len(a[0])
    else:
        n = 2
    return tuple(_Falsy() for _ in range(n))


class _FakeCursor:
    """Cursor mínimo: COUNT(*) → 0, agregações → vazio. Suficiente p/ a
    tela 'home' renderizar no import sem banco real."""

    def fetchone(self):
        return (0,)

    def fetchall(self):
        return []


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *a, **k):
        return _FakeCursor()


def _instalar_dubles():
    """Injeta um streamlit falso (e módulos com efeito colateral) em sys.modules."""
    sys.modules["streamlit"] = _FakeStreamlit()

    # db/logconf tocam disco e banco no import — dublar para o teste ser hermético.
    fake_db = _t.ModuleType("pregoes.db")
    fake_db.init_db = _noop
    fake_db.get_conn = lambda *a, **k: _FakeConn()
    fake_db.backup = _noop
    sys.modules["pregoes.db"] = fake_db

    fake_log = _t.ModuleType("pregoes.logconf")
    fake_log.configurar = _noop
    fake_log.get_logger = lambda *_a, **_k: _t.SimpleNamespace(
        exception=_noop, info=_noop, warning=_noop, error=_noop, debug=_noop,
    )
    sys.modules["pregoes.logconf"] = fake_log


# Guarda o estado real ANTES de instalar os dublês, para restaurar depois.
_MODULOS_DUBLADOS = ("streamlit", "pregoes.db", "pregoes.logconf")
_ORIGINAIS = {m: sys.modules.get(m) for m in _MODULOS_DUBLADOS}

_instalar_dubles()

# Import tardio: depois dos dublês estarem no lugar.
from pregoes.ui import app  # noqa: E402

# CRÍTICO: remover os dublês de sys.modules após o import. app.py já rodou seu
# corpo com os fakes; as funções puras que testamos abaixo não tocam db/st.
# Se deixássemos os fakes em sys.modules, OUTROS testes (ex.: test_mvp) que
# importam pregoes.db depois pegariam o init_db no-op / conn falsa — vazamento
# de estado entre arquivos de teste. Restaura o módulo real (ou remove se não
# estava carregado, para o próximo import trazer o real).
for _m, _orig in _ORIGINAIS.items():
    if _orig is None:
        sys.modules.pop(_m, None)
    else:
        sys.modules[_m] = _orig


# === Fix #1 — fusão preserva edições ao marcar/desmarcar todos ===

def _df_base():
    return pd.DataFrame([
        {"OK": True,  "Item": 1, "Descrição": "Cano PVC 50mm", "Unidade": "UN",
         "Quantidade": 10.0, "Valor referência": 5.0},
        {"OK": False, "Item": 2, "Descrição": "Joelho 90 25mm", "Unidade": "PC",
         "Quantidade": 4.0, "Valor referência": 2.5},
    ])


def test_fundir_edicoes_preserva_correcoes_de_dados():
    base = _df_base()
    edited = base.copy()
    # Operador corrigiu descrição e quantidade da linha 0.
    edited.at[0, "Descrição"] = "Cano PVC 50mm REFORÇADO"
    edited.at[0, "Quantidade"] = 12.0

    fundido = app._fundir_edicoes(base, edited)

    assert fundido.at[0, "Descrição"] == "Cano PVC 50mm REFORÇADO"
    assert fundido.at[0, "Quantidade"] == 12.0
    # Base não foi mutada (cópia nova).
    assert base.at[0, "Descrição"] == "Cano PVC 50mm"


def test_fundir_edicoes_nao_copia_coluna_ok():
    base = _df_base()
    edited = base.copy()
    # Mesmo que o widget traga OK diferente, a fusão NÃO mexe em OK —
    # quem decide OK é o botão marcar/desmarcar, depois da fusão.
    edited["OK"] = [False, True]
    edited.at[1, "Unidade"] = "CX"

    fundido = app._fundir_edicoes(base, edited)

    assert list(fundido["OK"]) == [True, False]  # igual ao base
    assert fundido.at[1, "Unidade"] == "CX"      # dado fundido


def test_fundir_edicoes_edited_none_devolve_copia_intacta():
    base = _df_base()
    fundido = app._fundir_edicoes(base, None)
    assert fundido.equals(base)
    assert fundido is not base


def test_aplicar_delta_editor_reconstroi_edicoes_pendentes():
    """Regressão direta do bug #1: o delta do data_editor é aplicado e as
    edições do operador sobrevivem ao marcar/desmarcar todos."""
    base = _df_base()
    # Formato real do session_state[key] do st.data_editor.
    delta = {"edited_rows": {0: {"Descrição": "ALTERADO", "Quantidade": 99.0}}}

    edited = app._aplicar_delta_editor(base, delta)
    assert edited.at[0, "Descrição"] == "ALTERADO"
    assert edited.at[0, "Quantidade"] == 99.0

    # Cenário completo: marcar todos depois de uma edição pendente.
    fundido = app._fundir_edicoes(base, edited)
    fundido["OK"] = True
    assert list(fundido["OK"]) == [True, True]
    assert fundido.at[0, "Descrição"] == "ALTERADO"  # NÃO sumiu (antes sumia)


def test_aplicar_delta_editor_delta_vazio_ou_invalido():
    base = _df_base()
    assert app._aplicar_delta_editor(base, None).equals(base)
    assert app._aplicar_delta_editor(base, {}).equals(base)
    # Posição fora do range é ignorada sem estourar.
    fora = {"edited_rows": {99: {"Descrição": "x"}}}
    assert app._aplicar_delta_editor(base, fora).equals(base)


# === Fix #10 — nome determinístico p/ idempotência da re-importação ===

def test_sanitizar_determinismo_mesmo_arquivo_mesmo_nome():
    n1 = app._sanitizar_nome_arquivo("Pregao Araruna 06-2026.xlsx")
    n2 = app._sanitizar_nome_arquivo("Pregao Araruna 06-2026.xlsx")
    assert n1 == n2  # determinístico: re-upload gera o MESMO path.name


def test_sanitizar_troca_caracteres_perigosos():
    nome = app._sanitizar_nome_arquivo("Pregão Almeida 2026.xlsx")
    # Espaços e acentos viram '_', nada de barras.
    assert " " not in nome
    assert "/" not in nome and "\\" not in nome
    assert nome.endswith(".xlsx")


def test_sanitizar_anti_path_traversal():
    nome = app._sanitizar_nome_arquivo("../../etc/passwd.xlsx")
    assert "/" not in nome and ".." not in nome.replace(".xlsx", "")
    assert nome.endswith(".xlsx")


def test_sanitizar_garante_extensao_xlsx():
    assert app._sanitizar_nome_arquivo("planilha").endswith(".xlsx")
    assert app._sanitizar_nome_arquivo("dados.csv").endswith(".xlsx")


def test_sanitizar_nome_so_pontos_nao_vira_oculto_nem_vazio():
    nome = app._sanitizar_nome_arquivo("....xlsx")
    assert not nome.startswith(".")
    assert nome.endswith(".xlsx")


def test_idempotencia_path_name_estavel(tmp_path):
    """Contrato com o importador: fonte='import:<path.name>'. Dois uploads
    do mesmo arquivo precisam resolver ao mesmo basename — base da dedup."""
    pasta = tmp_path / "historico"
    pasta.mkdir()

    def salvar(nome_original: str, conteudo: bytes) -> Path:
        destino = pasta / app._sanitizar_nome_arquivo(nome_original)
        destino.write_bytes(conteudo)
        return destino

    p1 = salvar("Pregão Almeida 2026.xlsx", b"v1")
    p2 = salvar("Pregão Almeida 2026.xlsx", b"v2")  # re-upload do mesmo

    assert p1.name == p2.name
    assert f"import:{p1.name}" == f"import:{p2.name}"


# === Dedup de candidatos duplicados na seleção ===

def test_dedup_opcoes_remove_duplicados_preserva_ordem():
    from types import SimpleNamespace as O
    a = O(fornecedor="dantas", codigo_fornecedor="123", marca="NORDEPLAST", preco=27.76)
    a_dup = O(fornecedor="dantas", codigo_fornecedor="123", marca="NORDEPLAST", preco=27.76)
    b = O(fornecedor="dantas", codigo_fornecedor="456", marca="PLASTUBOS", preco=11.14)
    res = app._dedup_opcoes([a, a_dup, b])
    assert len(res) == 2          # o duplicado some
    assert res[0] is a and res[1] is b   # mantém a 1ª ocorrência, na ordem


def test_dedup_opcoes_mantem_precos_diferentes():
    from types import SimpleNamespace as O
    p25 = O(fornecedor="dantas", codigo_fornecedor="1", marca="X", preco=12.73)
    p32 = O(fornecedor="dantas", codigo_fornecedor="2", marca="X", preco=27.76)
    assert len(app._dedup_opcoes([p25, p32])) == 2   # produtos distintos ficam


# === Escolha fora do corte de exibição SEMPRE aparece (bug do site search 2026-06-27) ===

def _opt(cod, preco, marca="X"):
    from types import SimpleNamespace as O
    return O(fornecedor="almeida", codigo_fornecedor=cod, marca=marca, preco=preco)


def test_mesma_selecao_casa_por_preco_marca_codigo():
    o = _opt("9", 5.0, "A")
    assert app._mesma_selecao(o, _opt("9", 5.0, "A"))
    assert not app._mesma_selecao(o, _opt("8", 5.0, "A"))   # código diferente
    assert not app._mesma_selecao(o, _opt("9", 6.0, "A"))   # preço diferente
    assert not app._mesma_selecao(o, None)


def test_opcoes_visiveis_puxa_escolhida_de_fora_do_corte():
    """A escolha de baixa relevância (fora do top-N) é puxada de volta — senão
    o destaque some e parece que a seleção não pegou (bug do site search)."""
    todas = [_opt(str(i), 10.0 + i) for i in range(8)]    # 0..7
    escolhida = todas[7]                                  # a última, fora do top-3
    sel = _opt(escolhida.codigo_fornecedor, escolhida.preco, escolhida.marca)
    vis = app._opcoes_visiveis(todas, 3, sel)
    assert vis[0] is escolhida                            # entra no topo das visíveis
    assert any(o.codigo_fornecedor == "0" for o in vis)   # top-N continua lá
    assert any(app._mesma_selecao(o, sel) for o in vis)   # a escolha aparece


def test_opcoes_visiveis_nao_duplica_quando_ja_visivel():
    todas = [_opt(str(i), 10.0 + i) for i in range(8)]
    sel = _opt("0", 10.0)                                 # já no top-3
    vis = app._opcoes_visiveis(todas, 3, sel)
    assert len(vis) == 3
    assert sum(1 for o in vis if o.codigo_fornecedor == "0") == 1


def test_opcoes_visiveis_sem_selecao_corta_normal():
    todas = [_opt(str(i), 10.0 + i) for i in range(8)]
    assert len(app._opcoes_visiveis(todas, 3, None)) == 3


# === Sprint P1 / C1 — confirmar antes de descartar o pregão (P1.7) ===
#
# Testa a LÓGICA de detecção/limpeza diretamente sobre o session_state falso
# (app.st.session_state). O fluxo de UI do modal foi validado à parte via
# streamlit.testing.AppTest; aqui travamos as duas funções puras que decidem
# se há trabalho a perder e que apagam o estado do pregão.

def _reset_ss():
    app.st.session_state.clear()


def test_tem_pregao_em_andamento_falso_quando_vazio():
    _reset_ss()
    assert app._tem_pregao_em_andamento() is False


def test_tem_pregao_em_andamento_detecta_extracao_revisao_selecoes():
    _reset_ss()
    app.st.session_state["extracao"] = object()      # PDF já extraído
    assert app._tem_pregao_em_andamento() is True

    _reset_ss()
    app.st.session_state["itens_revisados"] = [1, 2]  # revisão feita
    assert app._tem_pregao_em_andamento() is True

    _reset_ss()
    app.st.session_state["selecoes"] = {0: "x"}       # marca escolhida
    assert app._tem_pregao_em_andamento() is True


def test_tem_pregao_em_andamento_ignora_colecoes_vazias():
    """dict/lista vazios não contam como trabalho — não vale incomodar com o
    modal de confirmação se não há nada a perder."""
    _reset_ss()
    app.st.session_state["selecoes"] = {}
    app.st.session_state["itens_revisados"] = []
    assert app._tem_pregao_em_andamento() is False


def test_limpar_pregao_remove_chaves_do_pregao_e_preserva_navegacao():
    _reset_ss()
    for k in app.CHAVES_PREGAO:
        app.st.session_state[k] = "algo"
    app.st.session_state["tela"] = "busca_precos"     # navegação, não é do pregão
    app.st.session_state["_confirmar_novo_pregao"] = True

    app._limpar_pregao()

    for k in app.CHAVES_PREGAO:
        assert k not in app.st.session_state, f"{k} deveria ter sido removida"
    # _limpar_pregao só mexe nas chaves do pregão; navegação fica intacta.
    assert app.st.session_state.get("tela") == "busca_precos"
    _reset_ss()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))


# === Busca no catálogo interno: palavras (AND) + código + fallback ===

def test_buscar_no_catalogo_palavras_codigo_fallback():
    from pregoes.matcher import ProdutoCatalogo as PC
    cat = [
        PC(produto_id=1, descricao_canonica="tubo de pvc soldavel esgoto 75mm 6m",
           codigo_almeida=None, codigo_dantas="8811", marca="PLASTUBOS", unidade_padrao="un"),
        PC(produto_id=2, descricao_canonica="tubo de pvc soldavel agua fria 25mm 6m",
           codigo_almeida="1661", codigo_dantas=None, marca="NORDESTE", unidade_padrao="un"),
        PC(produto_id=3, descricao_canonica="valvula plastica para lavatorio",
           codigo_almeida="39013", codigo_dantas=None, marca="SOCEL", unidade_padrao="un"),
    ]
    # 1 palavra acha todos os tubos (antes: token_sort exigia desc quase exata)
    assert {p.produto_id for p in app._buscar_no_catalogo(cat, "Tubo")} == {1, 2}
    # palavras AND: 'tubo 75' acha só o 75mm ('75' bate em '75mm')
    assert [p.produto_id for p in app._buscar_no_catalogo(cat, "tubo 75")] == [1]
    # código exato e por prefixo
    assert [p.produto_id for p in app._buscar_no_catalogo(cat, "8811")] == [1]
    assert 3 in {p.produto_id for p in app._buscar_no_catalogo(cat, "39")}
    # vazios
    assert app._buscar_no_catalogo(cat, "") == []
    assert app._buscar_no_catalogo([], "tubo") == []


# --- Fixes da auditoria 2026-06 (bloco 1) -----------------------------------

def test_chaves_pregao_incluem_markup_overrides():
    """Achado alto nº 6: markup_overrides é keyed por OFFSET do item (0..n).
    Se sobreviver ao 'Novo pregão', o override de ST do item N do pregão
    anterior contamina o item N do pregão novo — planilha ~10% mais barata
    em silêncio. Tem que estar no estado descartado por _limpar_pregao."""
    assert "markup_overrides" in app.CHAVES_PREGAO


def test_tentar_mapa_aprendido_aplica_em_lote_nas_fatias(tmp_path):
    """Achado alto nº 1: o reuso automático de mapa aprendido aplicava o mapa
    a UMA fatia da tabela multi-página e descartava as demais — 2º edital do
    mesmo órgão entregava só os itens da 1ª página com banner de sucesso.
    Agora usa aplicar_mapa_em_lote: itens de TODAS as fatias entram."""
    from pregoes.db import init_db as init_real, get_conn as conn_real
    from pregoes import mapeamento_manual as mm
    from pregoes.extrator import ResultadoExtracao

    cab = ["Item", "Descrição", "Unid.", "Quant.", "Valor Unitário"]
    pag1 = [list(cab),
            ["1", "CIMENTO PORTLAND CP II 50KG", "SC", "100", "38,50"],
            ["2", "AREIA MEDIA LAVADA M3", "M3", "12", "95,00"]]
    pag2 = [list(cab),  # cabeçalho repetido na página seguinte
            ["3", "BRITA 1 GRANITICA M3", "M3", "8", "110,00"],
            ["4", "VERGALHAO CA-50 10MM BARRA 12M", "BR", "50", "42,90"]]
    mapa = {"codigo_orgao": 0, "descricao": 1, "unidade": 2,
            "quantidade": 3, "valor_referencia": 4}

    db = tmp_path / "t.db"
    init_real(db)
    fraco = ResultadoExtracao(arquivo="e.pdf", itens=[],
                              tabelas_brutas=[pag1, pag2],
                              mapa_colunas_detectado={}, avisos=[],
                              confianca_global=0.0)
    with conn_real(db) as conn:
        mm.salvar_mapa(conn, mm.assinatura_cabecalho(cab), mapa)
        melhor = app._tentar_mapa_aprendido(fraco, conn, "e.pdf")

    assert melhor is not fraco
    assert melhor.total_itens == 4, "itens das DUAS páginas devem entrar"
    descrs = [i.descricao for i in melhor.itens]
    assert any("BRITA" in d for d in descrs), "item da 2ª fatia presente"
    assert melhor.tabelas_brutas == [pag1, pag2], "tabelas brutas preservadas"
    assert any("Mapa aprendido" in a for a in melhor.avisos)


def test_tentar_mapa_aprendido_sem_mapa_salvo_devolve_original(tmp_path):
    """Sem mapa aprendido com aquela assinatura, a extração original volta
    intocada (mesmo objeto) — nenhum banner falso de reuso."""
    from pregoes.db import init_db as init_real, get_conn as conn_real
    from pregoes.extrator import ResultadoExtracao

    db = tmp_path / "t.db"
    init_real(db)
    fraco = ResultadoExtracao(
        arquivo="e.pdf", itens=[],
        tabelas_brutas=[[["Item", "Descrição"], ["1", "X"]]],
        mapa_colunas_detectado={}, avisos=[], confianca_global=0.3)
    with conn_real(db) as conn:
        assert app._tentar_mapa_aprendido(fraco, conn, "e.pdf") is fraco


# === Feedback cliente 2026-06 (revisado): cabeçalho da busca usa o ID do edital
# A planilha passou a exportar TODOS os itens (só os selecionados preenchidos) —
# acabou a filtragem/reindexação (_filtrar_selecionados foi removida). E o
# cabeçalho da tela de busca mostra o número do item NO EDITAL (não sequencial),
# para o operador conferir com a coluna ITEM da planilha.

def test_numero_item_edital_usa_item_numero():
    class _It:
        def __init__(self, n): self.item_numero = n
    # IDs não-contíguos do edital (2, 9, 25) aparecem como tais, não 1,2,3.
    assert app._numero_item_edital(_It(2), 0) == 2
    assert app._numero_item_edital(_It(9), 1) == 9
    assert app._numero_item_edital(_It(25), 2) == 25


def test_numero_item_edital_fallback_quando_ausente():
    class _It:
        def __init__(self, n): self.item_numero = n
    # PDF sem número → fallback ao sequencial 1-based (idx+1).
    assert app._numero_item_edital(_It(None), 0) == 1
    assert app._numero_item_edital(_It(None), 4) == 5


def test_numero_item_edital_zero_nao_cai_no_fallback():
    class _It:
        def __init__(self, n): self.item_numero = n
    # item_numero == 0 é válido — checagem 'is not None' (não 'or idx+1').
    assert app._numero_item_edital(_It(0), 3) == 0


def test_tela_pregao_em_andamento_retoma_no_lugar_certo():
    """'Continuar o atual' deve retomar onde o pregão está: busca se já há
    resultados/seleções, revisão se só houve extração, senão upload."""
    ss = app.st.session_state
    ss.clear()
    ss["resultados_busca"] = {0: []}
    assert app._tela_pregao_em_andamento() == "busca_precos"
    ss.clear()
    ss["selecoes"] = {0: object()}
    assert app._tela_pregao_em_andamento() == "busca_precos"
    ss.clear()
    ss["extracao"] = object()
    assert app._tela_pregao_em_andamento() == "revisao"
    ss.clear()
    ss["itens_revisados"] = [object()]
    assert app._tela_pregao_em_andamento() == "revisao"
    ss.clear()
    assert app._tela_pregao_em_andamento() == "upload"
    ss.clear()


# === Feedback cliente 2026-06: gerar a planilha conclui o pregão ============
# Antes, gerar não marcava conclusão → o modal "começar um novo pregão" aparecia
# mesmo depois de concluir. Agora guardamos o HASH dos insumos ao gerar; enquanto
# o estado bate com o hash, não avisa. QUALQUER mudança (marca, preço, NCM,
# markup, revisão) muda o hash e re-arma o aviso sozinha — a revisão de QA achou
# 4 ações (NCM, markup, voltar-à-revisão, Docling) que o re-arme manual perdia.

from types import SimpleNamespace as _NS


def _item_ns(n=1):
    return _NS(item_numero=n, descricao=f"P{n}", unidade="un", quantidade=1,
               valor_referencia=2.0)


def _sel_ns(preco=10.0, cod="A1", marca="DECA", ncm=None):
    return _NS(preco=preco, codigo_fornecedor=cod, marca=marca, ncm=ncm)


def test_pregao_gerado_sem_mudanca_nao_conta_como_em_andamento():
    ss = app.st.session_state
    ss.clear()
    ss["extracao"] = object()
    ss["itens_revisados"] = [_item_ns()]
    ss["selecoes"] = {0: _sel_ns()}
    assert app._tem_pregao_em_andamento() is True
    # gerar guarda o hash → enquanto nada muda, não avisa (trabalho salvo)
    ss["pregao_gerado_hash"] = app._hash_pregao()
    assert app._tem_pregao_em_andamento() is False
    ss.clear()


def test_limpar_pregao_zera_hash_gerado():
    ss = app.st.session_state
    ss.clear()
    ss["pregao_gerado_hash"] = "abc"
    ss["selecoes"] = {0: object()}
    app._limpar_pregao()
    assert "pregao_gerado_hash" not in ss and "selecoes" not in ss
    ss.clear()


def test_trocar_marca_apos_gerar_rearma():
    ss = app.st.session_state
    ss.clear()
    ss["selecoes"] = {0: _sel_ns(marca="DECA")}
    ss["pregao_gerado_hash"] = app._hash_pregao()
    assert app._tem_pregao_em_andamento() is False
    ss["selecoes"][0] = _sel_ns(preco=12.0, cod="A2", marca="DOCOL")   # trocou
    assert app._tem_pregao_em_andamento() is True
    ss.clear()


def test_mudar_markup_apos_gerar_rearma():
    """Furo da revisão: mudar o markup (global/ST/normal) após gerar deve avisar."""
    ss = app.st.session_state
    ss.clear()
    ss["selecoes"] = {0: _sel_ns()}
    ss["mk_st"] = 1.30
    ss["mk_normal"] = 1.45
    ss["pregao_gerado_hash"] = app._hash_pregao()
    assert app._tem_pregao_em_andamento() is False
    ss["mk_st"] = 1.60               # aumentou o markup ST → planilha desatualizada
    assert app._tem_pregao_em_andamento() is True
    ss.clear()


def test_identificar_ncm_apos_gerar_rearma():
    """Furo da revisão: identificar NCM muda a classificação ST → deve avisar."""
    ss = app.st.session_state
    ss.clear()
    ss["selecoes"] = {0: _sel_ns(ncm=None)}
    ss["pregao_gerado_hash"] = app._hash_pregao()
    assert app._tem_pregao_em_andamento() is False
    ss["selecoes"][0] = _sel_ns(ncm="39172300")    # NCM identificado depois
    assert app._tem_pregao_em_andamento() is True
    ss.clear()


def test_escolher_opcao_grava_selecao():
    ss = app.st.session_state
    ss.clear()
    ss["selecoes"] = {}
    o = _NS(preco=12.5, codigo_fornecedor="A1", marca="DECA",
            ncm=None, url=None, fornecedor="almeida")
    app._escolher_opcao(3, o)
    sel = ss["selecoes"][3]
    assert sel.preco == 12.5 and sel.marca == "DECA" and sel.fornecedor == "almeida"
    ss.clear()


# === Rascunho anti-apagão: autosave + recuperação (nível app) ===============

def test_rascunho_salva_apagao_e_restaura_identico(monkeypatch, tmp_path):
    """Simula um apagão: autosava o pregão em andamento, ZERA a memória e
    restaura — itens, busca e seleções idênticos, e volta na tela onde parou
    (é o que faz o motor seguir 'igual à primeira busca')."""
    from pregoes import rascunho
    from pregoes.exportador import SelecaoFornecedor
    from pregoes.extrator import ItemExtraido
    monkeypatch.setattr(rascunho, "CAMINHO", tmp_path / "rascunho.pkl")
    ss = app.st.session_state
    ss.clear()
    itens = [ItemExtraido(item_numero=1, descricao="LUVA", quantidade=2.0)]
    ss["itens_edital"] = itens
    ss["itens_revisados"] = itens
    ss["resultados_busca"] = {0: []}                 # busca de preços já feita
    ss["selecoes"] = {0: SelecaoFornecedor(preco=8.32, codigo_fornecedor="9185",
                                           marca="M", fornecedor="dantas")}
    ss["orgao"] = "Prefeitura"; ss["numero"] = "001/2026"; ss["tela"] = "busca"

    app._salvar_rascunho()
    assert rascunho.existe()                          # gravou no disco

    ss.clear(); ss["tela"] = "home"                   # APAGÃO: memória zerada
    estado = rascunho.carregar()
    assert estado is not None
    app._restaurar_rascunho(estado)
    assert ss["itens_revisados"][0].descricao == "LUVA"
    assert ss["selecoes"][0].codigo_fornecedor == "9185"
    assert ss["tela"] == "busca"                      # volta onde parou
    ss.clear()


def test_rascunho_nao_salva_sem_pregao_em_andamento(monkeypatch, tmp_path):
    from pregoes import rascunho
    monkeypatch.setattr(rascunho, "CAMINHO", tmp_path / "rascunho.pkl")
    app.st.session_state.clear()                      # nada em andamento
    app._salvar_rascunho()
    assert not (tmp_path / "rascunho.pkl").exists()   # não cria rascunho à toa


def test_rascunho_nao_sobrescreve_enquanto_recuperacao_pendente(monkeypatch, tmp_path):
    """Com a faixa de recuperação pendente, o autosave NÃO sobrescreve o
    rascunho recuperável — senão começar algo novo apagaria o que se recupera."""
    from pregoes import rascunho
    from pregoes.exportador import SelecaoFornecedor
    monkeypatch.setattr(rascunho, "CAMINHO", tmp_path / "rascunho.pkl")
    rascunho.salvar({"itens_edital": [], "selecoes": {0: "ANTIGO"}})
    ss = app.st.session_state
    ss.clear()
    ss["_rascunho_disponivel"] = True                 # recuperação pendente
    ss["itens_revisados"] = [1]                       # operador mexeu em algo
    ss["selecoes"] = {0: SelecaoFornecedor(preco=1.0, codigo_fornecedor="X",
                                           marca="Y", fornecedor="almeida")}
    app._salvar_rascunho()
    assert rascunho.carregar()["selecoes"][0] == "ANTIGO"   # disco intocado
    ss.clear()
