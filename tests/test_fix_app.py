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


# === Feedback cliente 2026-06: excluir itens "não usar" + re-indexar ========

def test_filtrar_selecionados_remove_nao_usados_e_reindexa():
    """Itens 1, 3 e 5 sem seleção ("Não usar"/sem resultado) saem; os
    escolhidos (0,2,4) viram 0,1,2 com selecoes/markups/ST realinhados."""
    class _It:
        def __init__(self, n): self.n = n
    itens = [_It(i) for i in range(6)]
    selecoes = {0: "A", 2: "C", 4: "E"}          # só 0,2,4 escolhidos
    markups = {0: 1.30, 2: 1.45, 4: 1.30}
    com_st = {0, 4}
    itens_f, sel_f, mk_f, st_f = app._filtrar_selecionados(
        itens, selecoes, markups, com_st)
    assert [it.n for it in itens_f] == [0, 2, 4]   # ordem original preservada
    assert sel_f == {0: "A", 1: "C", 2: "E"}       # re-indexado 0..2
    assert mk_f == {0: 1.30, 1: 1.45, 2: 1.30}
    assert st_f == {0, 2}                           # offsets 0(orig0) e 2(orig4)


def test_filtrar_selecionados_sem_markup_nem_st():
    class _It:
        pass
    itens = [_It(), _It()]
    itens_f, sel_f, mk_f, st_f = app._filtrar_selecionados(
        itens, {1: "X"}, None, None)
    assert len(itens_f) == 1 and sel_f == {0: "X"}
    assert mk_f is None and st_f is None


def test_filtrar_selecionados_todos_escolhidos_inalterado():
    class _It:
        pass
    itens = [_It(), _It()]
    _, sel_f, mk_f, st_f = app._filtrar_selecionados(
        itens, {0: "A", 1: "B"}, {0: 1.3, 1: 1.45}, {0})
    assert sel_f == {0: "A", 1: "B"} and mk_f == {0: 1.3, 1: 1.45} and st_f == {0}
