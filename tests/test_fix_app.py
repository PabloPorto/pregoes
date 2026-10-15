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


# === Rascunhos MULTI-PREGÃO: autosave + retomar + trocar (nível app) ========

def _isolar_rascunhos(monkeypatch, tmp_path):
    from pregoes import rascunho
    monkeypatch.setattr(rascunho, "DIR", tmp_path / "rascunhos")
    monkeypatch.setattr(rascunho, "CAMINHO", tmp_path / "legado.pkl")
    return rascunho


def _pregao_na_sessao(ss, orgao, numero, codigo="9185"):
    from pregoes.exportador import SelecaoFornecedor
    from pregoes.extrator import ItemExtraido
    itens = [ItemExtraido(item_numero=1, descricao=f"LUVA {numero}",
                          quantidade=2.0)]
    ss["itens_edital"] = itens
    ss["itens_revisados"] = itens
    ss["resultados_busca"] = {0: []}
    ss["selecoes"] = {0: SelecaoFornecedor(preco=8.32, codigo_fornecedor=codigo,
                                           marca="M", fornecedor="dantas")}
    ss["orgao"] = orgao; ss["numero"] = numero; ss["tela"] = "busca"


def test_rascunho_salva_apagao_e_restaura_identico(monkeypatch, tmp_path):
    """Simula um apagão: autosava o pregão em andamento, ZERA a memória e
    restaura — itens, busca e seleções idênticos, e volta na tela onde parou
    (é o que faz o motor seguir 'igual à primeira busca')."""
    rascunho = _isolar_rascunhos(monkeypatch, tmp_path)
    ss = app.st.session_state
    ss.clear()
    _pregao_na_sessao(ss, "Prefeitura", "001/2026")

    app._salvar_rascunho()
    pendentes = rascunho.listar_pregoes()
    assert len(pendentes) == 1 and pendentes[0]["numero"] == "001/2026"

    ss.clear(); ss["tela"] = "home"                   # APAGÃO: memória zerada
    estado = rascunho.carregar_pregao(pendentes[0]["slug"])
    assert estado is not None
    app._restaurar_rascunho(estado, pendentes[0]["slug"])
    assert ss["itens_revisados"][0].descricao == "LUVA 001/2026"
    assert ss["selecoes"][0].codigo_fornecedor == "9185"
    assert ss["tela"] == "busca_precos"               # volta à tela de trabalho
    ss.clear()


def test_erro_inesperado_na_tela_vira_aviso_em_portugues(monkeypatch):
    """Erro não tratado numa tela: aviso PT-BR com saída, e o script segue
    (o autosave do rascunho roda depois) — não a tela de erro do Streamlit."""
    def _quebra():
        raise ValueError("boom")
    avisos = []
    monkeypatch.setitem(app.TELAS, "revisao", _quebra)
    monkeypatch.setattr(app.style, "bloco_aviso",
                        lambda msg, tipo="info": avisos.append((tipo, msg)))
    ss = app.st.session_state
    ss.clear(); ss["tela"] = "revisao"
    app._renderizar_tela()                            # não levanta
    assert avisos and avisos[0][0] == "danger"
    assert "Seu trabalho está salvo" in avisos[0][1]
    ss.clear()


def test_rede_de_seguranca_nao_engole_rerun_nem_stop(monkeypatch):
    # st.rerun()/st.stop() são BaseException no Streamlit: têm que passar.
    class _Rerun(BaseException):
        pass

    def _tela_que_navega():
        raise _Rerun()
    monkeypatch.setitem(app.TELAS, "revisao", _tela_que_navega)
    ss = app.st.session_state
    ss.clear(); ss["tela"] = "revisao"
    with pytest.raises(_Rerun):
        app._renderizar_tela()
    ss.clear()


def test_tela_desconhecida_cai_no_inicio(monkeypatch):
    # Rascunho antigo com nome de tela que não existe mais não pode quebrar.
    chamadas = []
    monkeypatch.setattr(app, "tela_home", lambda: chamadas.append("home"))
    ss = app.st.session_state
    ss.clear(); ss["tela"] = "tela_que_nao_existe"
    app._renderizar_tela()
    assert chamadas == ["home"]
    ss.clear()


def test_continuar_pregao_parado_na_revisao_nao_quebra(monkeypatch, tmp_path):
    """O rascunho NÃO guarda df_editavel e a restauração removia a chave da
    sessão: a tela de revisão lia st.session_state.df_editavel e quebrava com
    AttributeError ao 'Continuar' (ou retomar pela URL) um pregão parado ali."""
    rascunho = _isolar_rascunhos(monkeypatch, tmp_path)
    ss = app.st.session_state
    ss.clear()
    ss["extracao"] = {"itens": ["LUVA"]}             # extraído, ainda sem busca
    ss["df_editavel"] = "tabela-em-edicao"
    ss["orgao"] = "Prefeitura"; ss["numero"] = "004/2026"; ss["tela"] = "revisao"
    app._salvar_rascunho()
    slug = ss["_rascunho_slug"]

    ss.clear()                                       # sessão nova (boot)
    ss["tela"] = "home"; ss["extracao"] = None; ss["df_editavel"] = None
    app._restaurar_rascunho(rascunho.carregar_pregao(slug), slug)
    assert ss["tela"] == "revisao"
    assert ss["extracao"] == {"itens": ["LUVA"]}
    assert "df_editavel" in ss and ss["df_editavel"] is None   # revisão reconstrói
    ss.clear()


def test_url_acompanha_o_pregao_carregado(monkeypatch, tmp_path):
    """Com um pregão carregado, a URL guarda ?pregao=<slug> (é o que sobrevive
    ao recarregar a aba); ao descartar o pregão, o parâmetro sai."""
    _isolar_rascunhos(monkeypatch, tmp_path)
    qp = {}
    monkeypatch.setattr(app.st, "query_params", qp, raising=False)
    ss = app.st.session_state
    ss.clear()
    _pregao_na_sessao(ss, "Prefeitura", "002/2026")

    app._salvar_rascunho()
    app._marcar_pregao_na_url()
    assert qp["pregao"] == ss["_rascunho_slug"]

    app._limpar_pregao()
    app._marcar_pregao_na_url()
    assert "pregao" not in qp
    ss.clear()


def test_aba_recarregada_retoma_o_pregao_da_url(monkeypatch, tmp_path):
    """Relato de campo 2026-10: recarregar a aba (Chrome descarta abas em
    segundo plano) criava sessão nova e jogava o operador no Início. Com o slug
    na URL, a sessão nova restaura o pregão e volta à tela de trabalho."""
    _isolar_rascunhos(monkeypatch, tmp_path)
    qp = {}
    monkeypatch.setattr(app.st, "query_params", qp, raising=False)
    ss = app.st.session_state
    ss.clear()
    _pregao_na_sessao(ss, "Prefeitura", "003/2026", codigo="7777")
    app._salvar_rascunho()
    app._marcar_pregao_na_url()

    ss.clear(); ss["tela"] = "home"                   # aba recarregada
    app._retomar_pregao_da_url()
    assert ss["tela"] == "busca_precos"
    assert ss["selecoes"][0].codigo_fornecedor == "7777"
    assert qp["pregao"] == ss["_rascunho_slug"]
    ss.clear()


@pytest.mark.parametrize("slug", ["nao-existe", "../../data/pregoes", "X"])
def test_url_com_pregao_inexistente_ou_invalido_fica_no_inicio(monkeypatch, tmp_path, slug):
    """Rascunho descartado em outra aba, ou URL adulterada: não restaura nada,
    não lê fora da pasta de rascunhos, limpa a URL e segue no Início."""
    _isolar_rascunhos(monkeypatch, tmp_path)
    qp = {"pregao": slug}
    monkeypatch.setattr(app.st, "query_params", qp, raising=False)
    ss = app.st.session_state
    ss.clear(); ss["tela"] = "home"
    app._retomar_pregao_da_url()
    assert ss["tela"] == "home"
    assert "itens_revisados" not in ss
    assert "pregao" not in qp
    ss.clear()


def test_rascunho_nao_salva_sem_pregao_em_andamento(monkeypatch, tmp_path):
    rascunho = _isolar_rascunhos(monkeypatch, tmp_path)
    app.st.session_state.clear()                      # nada em andamento
    app._salvar_rascunho()
    assert rascunho.listar_pregoes() == []            # não cria rascunho à toa


def test_pregao_novo_nao_sobrescreve_rascunho_guardado(monkeypatch, tmp_path):
    """MULTI-PREGÃO (cenário do cliente): edital A de 1000 itens em curso,
    chega o edital B. 'Guardar e começar outro' preserva o A; o autosave do B
    vai para OUTRO arquivo — os dois coexistem e o A volta intacto."""
    rascunho = _isolar_rascunhos(monkeypatch, tmp_path)
    ss = app.st.session_state
    ss.clear()
    _pregao_na_sessao(ss, "Prefeitura A", "001/2026", codigo="AAA")
    app._salvar_rascunho()

    app._limpar_pregao(preservar_rascunho=True)         # "Guardar e começar outro"
    assert len(rascunho.listar_pregoes()) == 1          # A ficou guardado

    _pregao_na_sessao(ss, "Prefeitura B", "002/2026", codigo="BBB")
    app._salvar_rascunho()
    pendentes = {p["numero"]: p for p in rascunho.listar_pregoes()}
    assert set(pendentes) == {"001/2026", "002/2026"}   # os DOIS coexistem
    a = rascunho.carregar_pregao(pendentes["001/2026"]["slug"])
    assert a["selecoes"][0].codigo_fornecedor == "AAA"  # A intacto
    ss.clear()


def test_rascunho_slug_estavel_ao_preencher_identidade(monkeypatch, tmp_path):
    """PDF recém-subido (sem órgão/número) salva num slug ÚNICO (não no slot
    compartilhado 'sem-identificacao'). Ao preencher a identidade depois, o MESMO
    arquivo é reusado (slug estável) — 1 rascunho, sem duplicar e sem migração; a
    identidade nova vive no CONTEÚDO. (Fim da colisão de slug — achado 2026-07.)"""
    rascunho = _isolar_rascunhos(monkeypatch, tmp_path)
    ss = app.st.session_state
    ss.clear()
    _pregao_na_sessao(ss, None, None)
    ss.pop("orgao"); ss.pop("numero")
    app._salvar_rascunho()
    slug1 = ss["_rascunho_slug"]
    assert slug1 and slug1 != "sem-identificacao"       # slug ÚNICO, não o slot
    assert len(rascunho.listar_pregoes()) == 1

    ss["orgao"] = "Prefeitura"; ss["numero"] = "007/2026"   # identificou
    app._salvar_rascunho()
    pendentes = rascunho.listar_pregoes()
    assert len(pendentes) == 1                          # mesmo arquivo, não duplicou
    assert ss["_rascunho_slug"] == slug1                # slug estável (sem migração)
    assert pendentes[0]["numero"] == "007/2026"         # identidade nova no conteúdo
    ss.clear()


def test_dois_pregoes_sem_identidade_nao_colidem(monkeypatch, tmp_path):
    """DOIS pregões ainda SEM órgão/número (dois PDFs recém-subidos) NÃO podem
    compartilhar arquivo — antes ambos caíam no slot 'sem-identificacao' e o 2º
    autosave sobrescrevia o 1º (perda silenciosa, achado 2026-07)."""
    rascunho = _isolar_rascunhos(monkeypatch, tmp_path)
    ss = app.st.session_state
    ss.clear()
    _pregao_na_sessao(ss, None, None, codigo="AAA")
    ss.pop("orgao"); ss.pop("numero")
    app._salvar_rascunho()

    app._limpar_pregao(preservar_rascunho=True)         # "Guardar e começar outro"
    _pregao_na_sessao(ss, None, None, codigo="BBB")
    ss.pop("orgao"); ss.pop("numero")
    app._salvar_rascunho()

    pendentes = rascunho.listar_pregoes()
    assert len(pendentes) == 2, "dois sem-identidade têm de coexistir, não colidir"
    codigos = {rascunho.carregar_pregao(p["slug"])["selecoes"][0].codigo_fornecedor
               for p in pendentes}
    assert codigos == {"AAA", "BBB"}                    # nenhum foi sobrescrito
    ss.clear()


def test_limpar_pregao_default_remove_so_o_rascunho_atual(monkeypatch, tmp_path):
    """Descartar o pregão atual remove SÓ o arquivo dele; o guardado fica."""
    rascunho = _isolar_rascunhos(monkeypatch, tmp_path)
    ss = app.st.session_state
    ss.clear()
    _pregao_na_sessao(ss, "Prefeitura A", "001/2026")
    app._salvar_rascunho()
    app._limpar_pregao(preservar_rascunho=True)         # guarda A

    _pregao_na_sessao(ss, "Prefeitura B", "002/2026")
    app._salvar_rascunho()
    app._limpar_pregao()                                # DESCARTA o B
    numeros = [p["numero"] for p in rascunho.listar_pregoes()]
    assert numeros == ["001/2026"]                      # A sobrevive, B some
    assert "orgao" not in ss and "numero" not in ss     # identidade sai junto
    ss.clear()


# === Re-busca INCREMENTAL (perf 2026-07): mescla sem tocar no que já existe ===

def _opt_forn(cod, preco, forn="almeida"):
    return _NS(preco=preco, codigo_fornecedor=cod, marca="M",
               ncm=None, url=None, fornecedor=forn,
               descricao_encontrada="X", confianca="exato")


def test_mesclar_incremental_preserva_existentes_e_preenche_default():
    ss = app.st.session_state
    ss.clear()
    itens = [_NS(descricao="TUBO 50"), _NS(descricao="LUVA 25"),
             _NS(descricao="CANO 100")]
    sel_original = object()   # sentinela: escolha do operador no item 0
    ss["resultados_busca"] = {0: [_opt_forn("A", 1.0)], 1: [], 2: []}
    ss["selecoes"] = {0: sel_original}
    # re-busca dos faltantes 1 e 2: o 1 achou, o 2 continua vazio
    app._mesclar_busca_incremental(
        itens, [1, 2], {1: [_opt_forn("B", 2.0)], 2: []})
    assert ss["resultados_busca"][0][0].codigo_fornecedor == "A"  # intacto
    assert ss["resultados_busca"][1][0].codigo_fornecedor == "B"  # novo
    assert ss["resultados_busca"][2] == []                        # segue vazio
    assert ss["selecoes"][0] is sel_original                      # escolha intacta
    assert ss["selecoes"][1].codigo_fornecedor == "B"             # default novo
    assert 2 not in ss["selecoes"]                                # vazio sem default
    ss.clear()


def test_mesclar_incremental_respeita_nao_usar():
    ss = app.st.session_state
    ss.clear()
    itens = [_NS(descricao="TUBO 50")]
    ss["resultados_busca"] = {0: []}
    ss["selecoes"] = {}
    ss["itens_nao_usar"] = {0}
    app._mesclar_busca_incremental(itens, [0], {0: [_opt_forn("A", 1.0)]})
    assert 0 not in ss["selecoes"], "'não usar' não ganha default"
    assert ss["resultados_busca"][0], "mas o resultado fica disponível"
    ss.clear()


# === Aviso NA TELA quando a condição de pagamento não confirmou ==============

def test_avisos_pagamento_so_para_status_ruins():
    class _F:
        def __init__(self, status):
            self._pagamento_status = status
    sessoes = {
        "almeida": _F(("confirmada", "PIX")),
        "dantas": _F(("nao_pegou", "BANCO DO BRASIL")),
    }
    avisos = app._avisos_pagamento_de(sessoes)
    assert "almeida" not in avisos, "confirmada não gera alerta"
    assert "Dantas" in avisos["dantas"] and "BANCO DO BRASIL" in avisos["dantas"]


def test_avisos_pagamento_nao_conferida_e_sem_status():
    class _F:
        def __init__(self, status=None):
            if status:
                self._pagamento_status = status
    avisos = app._avisos_pagamento_de({
        "almeida": _F(("nao_conferida", "PIX")),
        "dantas": _F(),                        # sessão antiga sem o atributo
    })
    assert "confirmar" in avisos["almeida"].lower()
    assert "dantas" not in avisos, "sem status = sem_config = sem alerta"


def test_rascunho_nao_carrega_dataframe(monkeypatch, tmp_path):
    """Regressão de campo (2026-07-06): pickle de DataFrame quebra entre
    versões de pandas/numpy e os rascunhos sumiam EM SILÊNCIO após atualizar o
    ambiente. df_editavel fica FORA do snapshot (é derivável da extração)."""
    import pandas as pd
    rascunho = _isolar_rascunhos(monkeypatch, tmp_path)
    ss = app.st.session_state
    ss.clear()
    _pregao_na_sessao(ss, "Prefeitura", "010/2026")
    ss["df_editavel"] = pd.DataFrame({"a": [1, 2]})   # snapshot NÃO pode levar
    app._salvar_rascunho()
    estado = rascunho.carregar_pregoes = rascunho.carregar_pregao(
        rascunho.listar_pregoes()[0]["slug"])
    assert "df_editavel" not in estado, "DataFrame não pode entrar no snapshot"
    assert estado["itens_edital"], "o resto do estado continua completo"
    ss.clear()


def test_restaurar_rascunho_limpa_estado_do_pregao_anterior(monkeypatch, tmp_path):
    """Trocar de pregão pela lista do Início NÃO pode vazar estado do anterior:
    chaves que só existiam no pregão A (ex.: itens_nao_usar) não podem
    sobreviver ao restaurar o pregão B (bug de multi-pregão 2026-07-06)."""
    rascunho = _isolar_rascunhos(monkeypatch, tmp_path)
    ss = app.st.session_state
    ss.clear()
    # Pregão A ativo, com um item marcado "não usar".
    _pregao_na_sessao(ss, "Pref A", "001/2026", codigo="AAA")
    ss["itens_nao_usar"] = {3}
    ss["_rascunho_slug"] = "a"
    # Pregão B (salvo SEM itens_nao_usar).
    estado_b = {"itens_edital": [1], "itens_revisados": [1],
                "resultados_busca": {0: ["x"]}, "selecoes": {},
                "orgao": "Pref B", "numero": "002/2026", "tela": "home"}
    app._restaurar_rascunho(estado_b, "b")
    assert "itens_nao_usar" not in ss, "estado do pregão anterior VAZOU para o novo"
    assert ss["numero"] == "002/2026" and ss["tela"] == "busca_precos"
    assert ss["_rascunho_slug"] == "b"
    ss.clear()


def test_continuar_vai_para_tela_de_precos_ignora_home_salva(monkeypatch, tmp_path):
    """Continuar um pregão guardado leva à tela de TRABALHO (preços), não à
    'home' que o autosave capturou enquanto o operador olhava o Início
    (feedback 2026-07-06: 'Continuar' deixava o usuário preso no Início)."""
    _isolar_rascunhos(monkeypatch, tmp_path)
    ss = app.st.session_state
    ss.clear()
    estado = {"itens_edital": [1], "itens_revisados": [1],
              "resultados_busca": {0: ["x"]},   # busca feita → tela de preços
              "selecoes": {}, "orgao": "P", "numero": "1",
              "tela": "home"}                    # tela STALE capturada no Início
    app._restaurar_rascunho(estado, "s")
    assert ss["tela"] == "busca_precos", "deve ir à tela de preços, não à home"
    ss.clear()


def test_continuar_pregao_so_revisado_vai_para_revisao(monkeypatch, tmp_path):
    _isolar_rascunhos(monkeypatch, tmp_path)
    ss = app.st.session_state
    ss.clear()
    estado = {"itens_edital": [1], "itens_revisados": [1], "selecoes": {},
              "orgao": "P", "numero": "2", "tela": "home"}   # sem busca ainda
    app._restaurar_rascunho(estado, "s2")
    assert ss["tela"] == "revisao"
    ss.clear()


def test_identificar_ncm_desempacota_pedidos_de_4_tuplas(monkeypatch):
    """Regressão de PRODUÇÃO (2026-07-06): consultar_ncms recebe pedidos de 4
    elementos (off, forn, url, codigo) desde o cache durável (v1.0.31); o laço
    de pós-processamento de _identificar_ncm ainda desempacotava 3 e crashava
    ('too many values to unpack') ao clicar 'Identificar NCM'."""
    from pregoes.exportador import SelecaoFornecedor
    # consultar_ncms é importado DENTRO da função; monkeypatch no módulo pega.
    monkeypatch.setattr(
        "pregoes.scraper.consultar_ncms",
        lambda pedidos, progresso=None: ({p[0]: "39172300" for p in pedidos}, set()))
    ss = app.st.session_state
    ss.clear()
    sel = SelecaoFornecedor(
        preco=1.0, codigo_fornecedor="9185", marca="M", fornecedor="almeida",
        url="https://www.almeidadistribuidor.com.br/p/9185", ncm=None)
    selecoes = {0: sel}
    app._identificar_ncm(selecoes, [0])          # NÃO pode levantar ValueError
    assert selecoes[0].ncm == "39172300"
    ss.clear()


def test_filtrar_rascunhos_finalizados_esconde_sem_apagar(monkeypatch, tmp_path):
    """Pregão no HISTÓRICO (planilha gerada) some do Início; o não-finalizado
    permanece. E NENHUM arquivo é apagado — o Início OCULTA, jamais destrói
    trabalho (achado 2026-07: a versão que deletava perdia rascunho incompleto).
    Regra SIMPLES por presença de export: a v1.0.42 tentou distinguir por mtime e
    reexibia o finalizado em campo (Natuba 202) porque o autosave regrava o
    rascunho após gerar — por isso voltamos ao sinal robusto."""
    from pregoes import rascunho
    monkeypatch.setattr(rascunho, "DIR", tmp_path / "rascunhos")
    for org, num in (("Gerado", "001"), ("Aberto", "002")):
        rascunho.salvar_pregao({"orgao": org, "numero": num, "itens_edital": [1]})
    # Só "Gerado" tem planilha no histórico.
    monkeypatch.setattr(
        app._db, "pregao_finalizado",
        lambda conn, o, n: (o == "Gerado"), raising=False)

    pendentes = rascunho.listar_pregoes()
    assert len(pendentes) == 2
    viveis = app._filtrar_rascunhos_finalizados(pendentes)
    assert {p["orgao"] for p in viveis} == {"Aberto"}, \
        "o finalizado some; o não-finalizado permanece"
    # NENHUM arquivo apagado — os dois .pkl continuam no disco
    slugs = {p.stem for p in (tmp_path / "rascunhos").glob("*.pkl")}
    assert slugs == {"001-gerado", "002-aberto"}


def test_filtrar_rascunhos_degrada_seguro_em_erro_de_banco(monkeypatch):
    """Se o banco falhar, mostra TODOS (melhor um extra do que esconder)."""
    def _boom(*a, **k):
        raise RuntimeError("db off")
    monkeypatch.setattr(app._db, "pregao_finalizado", _boom, raising=False)
    pend = [{"slug": "s", "orgao": "X", "numero": "1"}]
    assert app._filtrar_rascunhos_finalizados(pend) == pend


def test_novo_pregao_limpa_estado_das_buscas_manuais(monkeypatch, tmp_path):
    """As chaves por-item das buscas manuais (site_res_/siteq_/fonte_busca_...)
    NÃO podem vazar para o pregão novo — senão o mesmo índice mostra o resultado
    do pregão anterior ('Nada encontrado' em cache) sem re-buscar (bug 2026-07)."""
    _isolar_rascunhos(monkeypatch, tmp_path)
    ss = app.st.session_state
    ss.clear()
    _pregao_na_sessao(ss, "Pref A", "001/2026")
    # estado deixado por buscas manuais do pregão A no item 19
    ss["site_res_19"] = ("almeida", [])          # "Nada encontrado" em cache
    ss["siteq_19"] = "dobradiça"
    ss["siteforn_19"] = "almeida"
    ss["fonte_busca_19"] = "Site do fornecedor"
    ss["catq_3"] = "tubo"
    app._limpar_pregao()                          # começa outro pregão
    for k in ("site_res_19", "siteq_19", "siteforn_19", "fonte_busca_19", "catq_3"):
        assert k not in ss, f"{k} vazou para o pregão novo"
    ss.clear()


def test_restaurar_pregao_limpa_busca_manual_de_outro(monkeypatch, tmp_path):
    """Continuar um pregão também zera buscas manuais que ficaram de outro."""
    _isolar_rascunhos(monkeypatch, tmp_path)
    ss = app.st.session_state
    ss.clear()
    ss["site_res_5"] = ("dantas", [])            # lixo de um pregão anterior
    ss["siteq_5"] = "cano"
    app._restaurar_rascunho({"itens_edital": [1], "itens_revisados": [1],
                             "resultados_busca": {0: ["x"]}, "selecoes": {},
                             "orgao": "P", "numero": "9", "tela": "home"}, "s")
    assert "site_res_5" not in ss and "siteq_5" not in ss
    ss.clear()


# === Honestidade quando o SITE do fornecedor está instável (2026-07-09) ===

def test_avisos_lote_vazio_fornecedor_zerado_gera_aviso():
    """Fornecedor com ZERO acerto em TODOS os itens de um lote relevante →
    aviso de site instável (provado em campo: janela de ~10 min em que a
    Almeida logada devolvia vazio para qualquer termo e voltou sozinha).
    Fornecedor com acertos (ou lote pequeno) → sem aviso."""
    stats = [("almeida", 0, 0, 120), ("dantas", 80, 15, 25)]
    avisos = app._avisos_lote_vazio(stats, 120)
    assert len(avisos) == 1 and "Almeida" in avisos[0]
    assert "instável" in avisos[0] or "fora do ar" in avisos[0]
    # lote pequeno: zero real é plausível — não alarma
    assert app._avisos_lote_vazio([("almeida", 0, 0, 2)], 2) == []
    # todos acharam algo — sem aviso
    assert app._avisos_lote_vazio([("almeida", 5, 1, 4)], 10) == []
    # match só por variação ainda é resultado (ok=0, var>0) — sem aviso
    assert app._avisos_lote_vazio([("dantas", 0, 3, 7)], 10) == []
