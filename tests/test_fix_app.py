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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
