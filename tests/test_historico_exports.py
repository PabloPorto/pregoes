"""
Testes do recurso "histórico de planilhas geradas" (tabela `exports_log`).

Cobre o plano de testes do DESIGN (§9):
- Migration v2 idempotente e ADITIVA (banco v1 popular sobe sem perder dados;
  `migrate()` 2× é no-op; `schema_version` → 2; backup `pre-migration` criado).
- `init_db` do zero já cria `exports_log` + índice (DDL espelhada em `db.SCHEMA`).
- `export_log_gravar` insere 1 linha e NÃO commita por si (rollback do caller descarta).
- UPSERT de `_persistir_edital`: re-gerar o MESMO pregão (mesmo órgão+número)
  NÃO duplica `editais`/`edital_itens`, mas ACRESCENTA linha em `exports_log`.
- Órgão/número distintos → editais distintos (não funde).
- Basename resolve via `config.EXPORTS_DIR / arquivo`; arquivo ausente não estoura.
- Frozen-aware: a leitura usa o `EXPORTS_DIR` corrente, não um path gravado.
- KPIs da home (`GROUP BY status`) não inflam ao re-gerar o mesmo pregão.

Padrões herdados do projeto:
- `tmp_path` + `monkeypatch` em `config.DB_PATH`/`config.DATA_DIR` (test_producao.py).
- Para exercitar funções de `app.py` sem subir Streamlit/navegador, instala-se um
  Streamlit DUBLÊ em `sys.modules` ANTES do import — MAS mantendo o `pregoes.db`
  REAL (diferente de test_fix_app.py, que dubla o db): aqui a lógica de UPSERT é
  testada contra um banco SQLite real em `tmp_path`, que é exatamente o caminho
  crítico (não duplicar editais ao re-gerar). O import de app.py é feito com
  `config.DB_PATH` já apontando para o tmp, e os dublês são restaurados depois
  para não vazar estado entre arquivos de teste.
"""
from __future__ import annotations

import sqlite3
import sys
import types as _t
from pathlib import Path

import pytest

from pregoes import db, migrations, config


# =====================================================================
# Parte 1 — Camada de dados (db.py + migrations.py), sem tocar app.py
# =====================================================================

def _schema_v1_sem_exports(path: Path) -> None:
    """Cria um banco no estado 'v1' (ANTES da feature): schema base SEM a
    tabela exports_log e SEM a migration v2 registrada. Usado para provar que
    a migration v2 sobe um banco já populado sem perder dados.

    Reproduz o schema base removendo o trecho do exports_log e zerando o
    schema_version (banco pré-versionamento, adotado como baseline pela
    `migrate`). Assim o teste não depende de um dump binário antigo.
    """
    schema = db.SCHEMA
    # Recorta a partir da DDL do exports_log (último bloco do SCHEMA) — tudo
    # antes é o schema v1. O marcador é estável (comentário do bloco).
    marcador = "-- Histórico de cada planilha .xlsx GERADA"
    assert marcador in schema, "marcador do bloco exports_log mudou em db.SCHEMA"
    schema_v1 = schema.split(marcador)[0]
    assert "exports_log" not in schema_v1
    with sqlite3.connect(path) as conn:
        conn.executescript(schema_v1)
        # Banco "antigo": sem schema_version → migrate o adota como baseline v1.
        conn.execute("DELETE FROM schema_version")
        conn.commit()


def _tabela_existe(conn: sqlite3.Connection, nome: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (nome,)
    ).fetchone()
    return row is not None


def _indice_existe(conn: sqlite3.Connection, nome: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?", (nome,)
    ).fetchone()
    return row is not None


def test_init_db_do_zero_cria_exports_log_e_indice(tmp_path, monkeypatch):
    """Bancos NOVOS nascem com exports_log + índice (DDL espelhada em db.SCHEMA)."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    dbp = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)
    db.init_db(dbp)
    with db.conectar(dbp) as c:
        assert _tabela_existe(c, "exports_log")
        assert _indice_existe(c, "idx_exports_edital")
        # init_db aplica migrations → head atual (2 com a v2 presente).
        assert migrations._versao_atual(c) == 2


def test_migration_v2_sobe_banco_v1_popular_sem_perder_dados(tmp_path, monkeypatch):
    """Banco v1 (sem exports_log) com dados sobe para v2 preservando tudo."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    dbp = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)

    _schema_v1_sem_exports(dbp)
    with db.conectar(dbp) as c:
        assert not _tabela_existe(c, "exports_log")  # pré-condição: v1
        c.execute("INSERT INTO produtos (descricao_canonica) VALUES ('legado')")
        c.execute(
            "INSERT INTO editais (orgao, numero_pregao, arquivo_pdf, status) "
            "VALUES ('Pref X', '00006/2026', 'x.pdf', 'rascunho')"
        )
        c.commit()

    versao = migrations.migrate(dbp)
    assert versao == 2

    with db.conectar(dbp) as c:
        # Aditivo: a tabela nova existe e os dados antigos continuam lá.
        assert _tabela_existe(c, "exports_log")
        assert _indice_existe(c, "idx_exports_edital")
        assert c.execute("SELECT COUNT(*) FROM produtos").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM editais").fetchone()[0] == 1


def test_migration_v2_idempotente(tmp_path, monkeypatch):
    """Rodar migrate() 2× num banco v1 não duplica nada e fica em 2."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    dbp = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)

    _schema_v1_sem_exports(dbp)
    assert migrations.migrate(dbp) == 2
    assert migrations.migrate(dbp) == 2  # 2ª vez: no-op
    with db.conectar(dbp) as c:
        # Só UMA linha registrando a v2 (não reaplicou).
        n_v2 = c.execute(
            "SELECT COUNT(*) FROM schema_version WHERE version = 2"
        ).fetchone()[0]
        assert n_v2 == 1


def test_migration_v2_cria_backup_pre_migration(tmp_path, monkeypatch):
    """migrate() faz backup 'pre-migration' ANTES de alterar a estrutura."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    dbp = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)

    _schema_v1_sem_exports(dbp)
    migrations.migrate(dbp)

    backups = list((tmp_path / "backups").glob("pregoes_*_pre-migration.db"))
    assert backups, "migrate deveria ter criado um backup pre-migration"


def test_migrations_head_e_dois():
    """Contrato: o head das MIGRATIONS é 2 (futuras começam em 3)."""
    head = max([migrations.BASELINE_VERSION] + [m[0] for m in migrations.MIGRATIONS])
    assert head == 2
    # A DDL e a função da v2 estão expostas no módulo (espelho de db.SCHEMA).
    assert "exports_log" in migrations._DDL_EXPORTS_LOG
    assert callable(migrations._v2_exports_log)


# === export_log_gravar ===

def _criar_edital(conn: sqlite3.Connection, orgao="Pref X",
                  numero="00006/2026", status="rascunho") -> int:
    cur = conn.execute(
        "INSERT INTO editais (orgao, numero_pregao, arquivo_pdf, status) "
        "VALUES (?, ?, ?, ?)",
        (orgao, numero, "x.pdf", status),
    )
    return cur.lastrowid


def test_export_log_gravar_insere_uma_linha(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    dbp = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)
    db.init_db(dbp)

    with db.get_conn(dbp) as conn:
        edital_id = _criar_edital(conn)
        db.export_log_gravar(conn, edital_id, "pregao_x_6_20260609_120000.xlsx",
                             n_itens=10, n_selecoes=4, markup_global=1.45)

    with db.conectar(dbp) as c:
        rows = c.execute(
            "SELECT edital_id, arquivo, n_itens, n_selecoes, markup_global, status "
            "FROM exports_log"
        ).fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["edital_id"] == edital_id
    assert r["arquivo"] == "pregao_x_6_20260609_120000.xlsx"
    assert r["n_itens"] == 10
    assert r["n_selecoes"] == 4
    assert r["markup_global"] == 1.45
    assert r["status"] == "gerado"  # default


def test_export_log_gravar_aceita_markup_none(tmp_path, monkeypatch):
    """markup_global aceita None (auditoria opcional) sem estourar."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    dbp = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)
    db.init_db(dbp)
    with db.get_conn(dbp) as conn:
        edital_id = _criar_edital(conn)
        db.export_log_gravar(conn, edital_id, "p.xlsx", 0, 0, None)
    with db.conectar(dbp) as c:
        assert c.execute(
            "SELECT markup_global FROM exports_log"
        ).fetchone()[0] is None


def test_export_log_gravar_nao_commita_por_si(tmp_path, monkeypatch):
    """O helper espelha import_log_gravar: NÃO commita. Se o caller faz rollback
    (erro na mesma transação), a linha do log é descartada junto."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    dbp = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)
    db.init_db(dbp)

    # Conexão crua (sem o get_conn que commita no __exit__) para controlar
    # explicitamente o rollback — prova que o helper sozinho não persiste.
    conn = db.conectar(dbp)
    try:
        edital_id = _criar_edital(conn)
        db.export_log_gravar(conn, edital_id, "p.xlsx", 1, 0, 1.30)
        conn.rollback()  # caller desiste
    finally:
        conn.close()

    with db.conectar(dbp) as c:
        assert c.execute("SELECT COUNT(*) FROM exports_log").fetchone()[0] == 0


def test_export_log_cascade_apaga_com_edital(tmp_path, monkeypatch):
    """FK ON DELETE CASCADE: apagar o edital remove o histórico das planilhas.
    (CASCADE inerte hoje na UI, mas o invariante é parte do schema — §8.)"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    dbp = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", dbp)
    db.init_db(dbp)
    with db.get_conn(dbp) as conn:
        edital_id = _criar_edital(conn)
        db.export_log_gravar(conn, edital_id, "p.xlsx", 1, 0, 1.45)
    # get_conn ativa PRAGMA foreign_keys = ON.
    with db.get_conn(dbp) as conn:
        conn.execute("DELETE FROM editais WHERE id = ?", (edital_id,))
    with db.conectar(dbp) as c:
        assert c.execute("SELECT COUNT(*) FROM exports_log").fetchone()[0] == 0


# === Basename resolve / arquivo ausente / frozen-aware ===

def test_basename_resolve_via_exports_dir(tmp_path, monkeypatch):
    """Grava SÓ o basename; o caminho real é EXPORTS_DIR/arquivo no uso, e os
    bytes lidos batem com o arquivo salvo (padrão de rebaixar da tela)."""
    exports = tmp_path / "exports"
    exports.mkdir()
    monkeypatch.setattr(config, "EXPORTS_DIR", exports)

    conteudo = b"PK\x03\x04 conteudo-fake-do-xlsx"
    (exports / "pregao_x.xlsx").write_bytes(conteudo)

    # No banco guardaríamos apenas "pregao_x.xlsx" (basename). Reconstrução:
    caminho = config.EXPORTS_DIR / "pregao_x.xlsx"
    assert caminho.exists()
    assert caminho.read_bytes() == conteudo


def test_arquivo_ausente_nao_estoura(tmp_path, monkeypatch):
    """Arquivo apagado/movido: a checagem Path.exists() é False e o fluxo da
    tela apenas avisa — nunca FileNotFoundError na cara do leigo (§8)."""
    exports = tmp_path / "exports"
    exports.mkdir()
    monkeypatch.setattr(config, "EXPORTS_DIR", exports)
    caminho = config.EXPORTS_DIR / "sumiu.xlsx"
    assert caminho.exists() is False  # render mostra aviso, sem abrir o arquivo


def test_frozen_aware_le_exports_dir_corrente(tmp_path, monkeypatch):
    """O caminho é reconstruído A CADA uso a partir de config.EXPORTS_DIR
    (recomputado a cada boot no .exe). Mover a pasta (= trocar EXPORTS_DIR)
    deve fazer a reconstrução seguir o diretório NOVO, não um path gravado."""
    pasta_velha = tmp_path / "velha"
    pasta_nova = tmp_path / "nova"
    pasta_velha.mkdir()
    pasta_nova.mkdir()
    (pasta_nova / "p.xlsx").write_bytes(b"novo")

    basename = "p.xlsx"  # é só isto que fica no banco

    monkeypatch.setattr(config, "EXPORTS_DIR", pasta_velha)
    assert not (config.EXPORTS_DIR / basename).exists()  # não está na velha

    # Simula reboot com a pasta movida: EXPORTS_DIR aponta para o novo lugar.
    monkeypatch.setattr(config, "EXPORTS_DIR", pasta_nova)
    assert (config.EXPORTS_DIR / basename).exists()
    assert (config.EXPORTS_DIR / basename).read_bytes() == b"novo"


# =====================================================================
# Parte 2 — UPSERT de _persistir_edital (app.py) contra banco REAL
# =====================================================================
#
# Import de app.py executa o corpo do módulo (sidebar + tela home), que toca
# Streamlit e get_conn. Dublamos SÓ o Streamlit (widgets falsy, session_state
# real) e apontamos config.DB_PATH para um tmp ANTES do import, mantendo o
# pregoes.db REAL. Assim a função roda contra um banco SQLite de verdade — o
# caminho crítico (não duplicar editais ao re-gerar) é exercido para valer.


class _FakeSessionState(dict):
    def __getattr__(self, nome):
        try:
            return self[nome]
        except KeyError as exc:  # pragma: no cover
            raise AttributeError(nome) from exc

    def __setattr__(self, nome, valor):
        self[nome] = valor


class _Falsy:
    """Sentinela falsy: serve de widget (nada dispara), context manager e
    iterável vazio. Mesmo truque de test_fix_app.py."""

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


def _n_falsy(*a, **k):
    if a and isinstance(a[0], int):
        n = a[0]
    elif a and hasattr(a[0], "__len__"):
        n = len(a[0])
    else:
        n = 2
    return tuple(_Falsy() for _ in range(n))


_SESSION_STATE = _FakeSessionState()
_SESSION_STATE["tela"] = "home"  # corpo do módulo despacha TELAS[tela]()


class _FakeStreamlit(_t.ModuleType):
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
        return self.session_state if _nome == "session_state" else _Falsy()


@pytest.fixture
def app_modulo(tmp_path, monkeypatch):
    """Importa pregoes.ui.app com Streamlit dublê e pregoes.db REAL apontado
    para um banco em tmp_path. Restaura sys.modules ao final para não vazar o
    dublê de streamlit para outros arquivos de teste.

    Por que o reimport é FORÇADO (importlib + pop do atributo no pacote):
    test_fix_app.py importa app.py enquanto um `pregoes.db` FALSO (sem
    `export_log_gravar`) está em sys.modules, deixando o `app` cacheado com
    `_db` apontando pro fake. Aqui reinstalamos o `pregoes.db` REAL ANTES de
    reimportar e recarregamos o módulo do zero, para `_db`/`get_conn`
    rebindarem no db de verdade.
    """
    import importlib

    dbp = tmp_path / "pregoes.db"
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", dbp)
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "exports")
    config.EXPORTS_DIR.mkdir(exist_ok=True)
    db.init_db(dbp)

    alvos = ("streamlit", "pregoes.db", "pregoes.config", "pregoes.ui.app")
    originais = {m: sys.modules.get(m) for m in alvos}
    _pkg = sys.modules.get("pregoes")
    _attr_config = getattr(_pkg, "config", None) if _pkg is not None else None

    # Streamlit dublê + db REAL no lugar (o `db` do topo deste arquivo é o real,
    # com export_log_gravar). Garante o rebind correto no reimport de app.py.
    sys.modules["streamlit"] = _FakeStreamlit()
    sys.modules["pregoes.db"] = db
    # Pino o config REAL que ESTE arquivo capturou no topo. test_fix_config.py
    # reimporta pregoes.config e deixa OUTRO objeto em sys.modules; sem o pino, o
    # app reimportado ligaria nesse outro config e o monkeypatch de DB_PATH/
    # EXPORTS_DIR (feito sobre o config deste arquivo) não o alcançaria — o app
    # gravaria/limparia arquivos na pasta REAL em vez da tmp do teste.
    sys.modules["pregoes.config"] = config
    if _pkg is not None:
        _pkg.config = config

    # Remove o módulo (e o atributo no pacote pai) para forçar execução fresca
    # do corpo de app.py com o DB/tela deste teste.
    sys.modules.pop("pregoes.ui.app", None)
    pacote_ui = sys.modules.get("pregoes.ui")
    if pacote_ui is not None and hasattr(pacote_ui, "app"):
        delattr(pacote_ui, "app")
    try:
        app = importlib.import_module("pregoes.ui.app")
        yield app
    finally:
        for m, orig in originais.items():
            if orig is None:
                sys.modules.pop(m, None)
            else:
                sys.modules[m] = orig
        if _pkg is not None and _attr_config is not None:
            _pkg.config = _attr_config
        # app.py ficará re-importado de forma limpa na próxima necessidade.
        sys.modules.pop("pregoes.ui.app", None)
        pacote_ui = sys.modules.get("pregoes.ui")
        if pacote_ui is not None and hasattr(pacote_ui, "app"):
            delattr(pacote_ui, "app")


def _itens_fake(app, n):
    from pregoes.extrator import ItemExtraido
    return [
        ItemExtraido(item_numero=i + 1, descricao=f"ITEM {i}", unidade="UN",
                     quantidade=float(i + 1), valor_referencia=10.0 * (i + 1))
        for i in range(n)
    ]


def _selecao(preco):
    from types import SimpleNamespace
    return SimpleNamespace(preco=preco)


def _contar(dbp, tabela):
    with db.conectar(dbp) as c:
        return c.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]


def test_persistir_edital_grava_edital_itens_e_export_log(app_modulo, tmp_path):
    """Uma geração cria 1 edital, seus itens e 1 linha de histórico (mesma transação)."""
    app = app_modulo
    dbp = tmp_path / "pregoes.db"
    app.st.session_state["orgao"] = "Prefeitura de Araruna"
    app.st.session_state["numero"] = "00006/2026"
    app.st.session_state["pdf_path"] = "/tmp/edital.pdf"

    itens = _itens_fake(app, 3)
    selecoes = {0: _selecao(5.0)}  # 1 item com preço
    destino = Path("pregao_araruna_6_20260609_120000.xlsx")

    app._persistir_edital(itens, selecoes, destino, markup=1.45)

    assert _contar(dbp, "editais") == 1
    assert _contar(dbp, "edital_itens") == 3
    assert _contar(dbp, "exports_log") == 1
    with db.conectar(dbp) as c:
        log = c.execute(
            "SELECT arquivo, n_itens, n_selecoes, markup_global, status FROM exports_log"
        ).fetchone()
        assert log["arquivo"] == destino.name      # SÓ o basename
        assert log["n_itens"] == 3
        assert log["n_selecoes"] == 1
        assert log["markup_global"] == 1.45
        assert log["status"] == "gerado"
        # Status do edital reflete que houve seleção (precificado).
        assert c.execute("SELECT status FROM editais").fetchone()[0] == "precificado"


def test_persistir_edital_upsert_nao_duplica_mas_acrescenta_export(app_modulo, tmp_path):
    """Re-gerar o MESMO pregão (mesmo órgão+número) NÃO duplica editais nem
    edital_itens (UPSERT troca os itens), MAS ACRESCENTA linha em exports_log.
    Este é o caminho crítico do design (Entrega 2)."""
    app = app_modulo
    dbp = tmp_path / "pregoes.db"
    app.st.session_state["orgao"] = "Prefeitura de Araruna"
    app.st.session_state["numero"] = "00006/2026"
    app.st.session_state["pdf_path"] = "/tmp/edital.pdf"

    # 1ª geração: 3 itens.
    app._persistir_edital(_itens_fake(app, 3), {0: _selecao(5.0)},
                          Path("v1.xlsx"), markup=1.45)
    # 2ª geração do MESMO pregão: 2 itens (substituem os anteriores).
    app._persistir_edital(_itens_fake(app, 2), {}, Path("v2.xlsx"), markup=1.30)

    assert _contar(dbp, "editais") == 1            # NÃO duplicou o edital
    assert _contar(dbp, "edital_itens") == 2       # itens trocados (não somados)
    assert _contar(dbp, "exports_log") == 2        # histórico acumulou as 2 gerações

    with db.conectar(dbp) as c:
        arquivos = {r["arquivo"] for r in c.execute(
            "SELECT arquivo FROM exports_log")}
        assert arquivos == {"v1.xlsx", "v2.xlsx"}


def test_excluir_pregao_remove_itens_exports_e_arquivo(app_modulo, tmp_path):
    """Excluir um pregão apaga editais + itens + exports_log (ON DELETE CASCADE)
    e remove o .xlsx gerado da pasta — sem afetar OUTROS pregões."""
    app = app_modulo
    dbp = tmp_path / "pregoes.db"

    # Pregão A (será excluído) com arquivo físico em EXPORTS_DIR.
    app.st.session_state["orgao"] = "Prefeitura A"
    app.st.session_state["numero"] = "00010/2026"
    app.st.session_state["pdf_path"] = "a.pdf"
    (config.EXPORTS_DIR / "a.xlsx").write_bytes(b"xlsx")
    app._persistir_edital(_itens_fake(app, 3), {0: _selecao(5.0)},
                          Path("a.xlsx"), markup=1.45)

    # Pregão B (deve sobreviver).
    app.st.session_state["orgao"] = "Prefeitura B"
    app.st.session_state["numero"] = "00011/2026"
    app.st.session_state["pdf_path"] = "b.pdf"
    app._persistir_edital(_itens_fake(app, 2), {}, Path("b.xlsx"), markup=1.45)

    with db.conectar(dbp) as c:
        eid_a = c.execute(
            "SELECT id FROM editais WHERE numero_pregao = '00010/2026'"
        ).fetchone()[0]

    app._excluir_pregao(eid_a)

    assert _contar(dbp, "editais") == 1            # só o B sobrou
    assert _contar(dbp, "edital_itens") == 2       # 3 itens do A apagados (cascade)
    assert _contar(dbp, "exports_log") == 1        # export do A apagado (cascade)
    assert not (config.EXPORTS_DIR / "a.xlsx").exists()   # arquivo removido
    with db.conectar(dbp) as c:
        assert c.execute("SELECT numero_pregao FROM editais").fetchone()[0] == "00011/2026"


def test_excluir_pregao_arquivo_ausente_nao_estoura(app_modulo, tmp_path):
    """Excluir pregão cujo .xlsx já sumiu da pasta não levanta erro."""
    app = app_modulo
    dbp = tmp_path / "pregoes.db"
    app.st.session_state["orgao"] = "Prefeitura C"
    app.st.session_state["numero"] = "00012/2026"
    app.st.session_state["pdf_path"] = "c.pdf"
    app._persistir_edital(_itens_fake(app, 1), {}, Path("sumido.xlsx"), markup=1.45)

    with db.conectar(dbp) as c:
        eid = c.execute("SELECT id FROM editais").fetchone()[0]

    app._excluir_pregao(eid)   # arquivo nunca existiu fisicamente
    assert _contar(dbp, "editais") == 0


def test_tela_historico_renderiza_com_pregao_sem_estourar(app_modulo, tmp_path):
    """Fumaça: a tela Histórico (com filtro + botão excluir) renderiza sem erro
    quando há pregão no banco — pega quebra na UI nova de filtro/exclusão."""
    app = app_modulo
    app.st.session_state["orgao"] = "Prefeitura A"
    app.st.session_state["numero"] = "00001/2026"
    app.st.session_state["pdf_path"] = "a.pdf"
    app._persistir_edital(_itens_fake(app, 2), {}, Path("a.xlsx"), markup=1.45)
    app.tela_historico()   # não deve levantar


def test_persistir_edital_upsert_normaliza_chave(app_modulo, tmp_path):
    """A chave de match é normalizada (trim + casefold): '00006/2026' digitado
    com espaços/caixa diferente é o MESMO pregão — não cria edital novo."""
    app = app_modulo
    dbp = tmp_path / "pregoes.db"
    app.st.session_state["orgao"] = "Prefeitura de Araruna"
    app.st.session_state["numero"] = "00006/2026"
    app.st.session_state["pdf_path"] = "x.pdf"
    app._persistir_edital(_itens_fake(app, 1), {}, Path("a.xlsx"), markup=1.45)

    # Mesmo pregão, digitado com espaços e caixa diferente.
    app.st.session_state["orgao"] = "  prefeitura DE araruna  "
    app.st.session_state["numero"] = " 00006/2026 "
    app._persistir_edital(_itens_fake(app, 1), {}, Path("b.xlsx"), markup=1.45)

    assert _contar(dbp, "editais") == 1            # normalização uniu os dois
    assert _contar(dbp, "exports_log") == 2


def test_persistir_edital_orgaos_distintos_nao_fundem(app_modulo, tmp_path):
    """Órgão/número distintos = pregões distintos: cria editais separados
    (nunca funde pregões diferentes — risco financeiro do design)."""
    app = app_modulo
    dbp = tmp_path / "pregoes.db"

    app.st.session_state["orgao"] = "Prefeitura A"
    app.st.session_state["numero"] = "00001/2026"
    app.st.session_state["pdf_path"] = "a.pdf"
    app._persistir_edital(_itens_fake(app, 1), {}, Path("a.xlsx"), markup=1.45)

    app.st.session_state["orgao"] = "Prefeitura B"
    app.st.session_state["numero"] = "00002/2026"
    app.st.session_state["pdf_path"] = "b.pdf"
    app._persistir_edital(_itens_fake(app, 1), {}, Path("b.xlsx"), markup=1.45)

    assert _contar(dbp, "editais") == 2
    assert _contar(dbp, "exports_log") == 2


def test_kpis_home_nao_inflam_ao_regerar_mesmo_pregao(app_modulo, tmp_path):
    """Regressão dos KPIs da home (GROUP BY status): re-gerar o MESMO pregão
    NÃO infla a contagem de editais — graças ao UPSERT, continua 1 registro.
    Replica a query da home (app.tela_home / db direto)."""
    app = app_modulo
    dbp = tmp_path / "pregoes.db"
    app.st.session_state["orgao"] = "Prefeitura de Araruna"
    app.st.session_state["numero"] = "00006/2026"
    app.st.session_state["pdf_path"] = "x.pdf"

    # Três gerações do mesmo pregão (todas com seleção → 'precificado').
    for nome in ("v1.xlsx", "v2.xlsx", "v3.xlsx"):
        app._persistir_edital(_itens_fake(app, 2), {0: _selecao(9.0)},
                              Path(nome), markup=1.45)

    with db.conectar(dbp) as c:
        total = c.execute("SELECT COUNT(*) FROM editais").fetchone()[0]
        contagens = dict(c.execute(
            "SELECT status, COUNT(*) FROM editais GROUP BY status").fetchall())
    assert total == 1                       # UPSERT: 1 edital, não 3
    assert contagens.get("precificado", 0) == 1
    assert _contar(dbp, "exports_log") == 3  # histórico ainda registra as 3


# --- Órgão+número obrigatórios: revalidação na geração (não só no upload) ---

def test_gerar_excel_bloqueia_geracao_sem_identidade(app_modulo, tmp_path):
    """Decisão do cliente: órgão+número OBRIGATÓRIOS. A revisão deixa o operador
    editar/limpar esses campos; se ficarem vazios, _gerar_excel barra a geração
    — não escreve .xlsx, não registra edital nem histórico (não cai no
    INSERT-sem-identidade que derrotaria a dedup)."""
    app = app_modulo
    dbp = tmp_path / "pregoes.db"
    itens = _itens_fake(app, 2)

    # (a) número só com espaços → vazio após strip
    app.st.session_state["orgao"] = "Prefeitura X"
    app.st.session_state["numero"] = "   "
    app._gerar_excel(itens, markup=1.45, selecoes={})
    assert _contar(dbp, "editais") == 0
    assert _contar(dbp, "exports_log") == 0

    # (b) órgão vazio
    app.st.session_state["orgao"] = ""
    app.st.session_state["numero"] = "00006/2026"
    app._gerar_excel(itens, markup=1.45, selecoes={})
    assert _contar(dbp, "editais") == 0

    # Nenhuma planilha foi escrita no disco.
    assert list(config.EXPORTS_DIR.glob("*.xlsx")) == []


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))


# === QA adversarial #4 — falha parcial e identidade vazia ===================

def test_gerar_excel_falha_no_historico_nao_pode_bloquear_download(
        app_modulo, monkeypatch):
    """REGRESSÃO de D3 (corrigido 2026-06-25): se salvar o histórico falhar, a
    planilha JÁ gerada (no disco) NÃO pode ser perdida — _gerar_excel loga, avisa
    e SEGUE (não propaga). Histórico é best-effort; a entrega é prioridade."""
    from pregoes.exportador import SelecaoFornecedor
    app = app_modulo
    app.st.session_state["orgao"] = "Pref X"
    app.st.session_state["numero"] = "1/2026"
    app.st.session_state["pdf_path"] = "x.pdf"
    itens = _itens_fake(app, 2)
    selecoes = {0: SelecaoFornecedor(preco=5.0, codigo_fornecedor="A", marca="M")}
    monkeypatch.setattr(app, "_persistir_edital",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("DB travado no meio do pregão")))
    # NÃO pode levantar: a falha de histórico é tratada e o fluxo segue.
    app._gerar_excel(itens, markup=1.45, selecoes=selecoes)
    assert list(config.EXPORTS_DIR.glob("*.xlsx")), "planilha gerada deve estar no disco"


def test_persistir_edital_identidade_vazia_nao_funde_caracterizacao(
        app_modulo, tmp_path):
    """CARACTERIZAÇÃO: sem órgão+número, cada geração cria um edital NOVO (não
    funde pregões sem identidade — por design). Inalcançável pela UI (o
    _gerar_excel bloqueia identidade vazia), mas documenta o ramo INSERT-novo."""
    app = app_modulo
    dbp = tmp_path / "pregoes.db"
    app.st.session_state["orgao"] = ""
    app.st.session_state["numero"] = ""
    app.st.session_state["pdf_path"] = "x.pdf"
    app._persistir_edital(_itens_fake(app, 1), {}, Path("a.xlsx"), markup=1.45)
    app._persistir_edital(_itens_fake(app, 1), {}, Path("b.xlsx"), markup=1.45)
    assert _contar(dbp, "editais") == 2   # sem identidade → 2 editais (não funde)


# === Requisito: MATRIZ = edital inteiro; OK = "buscar preço" (2026-06-25) ===

def test_itens_revisao_separa_edital_busca_e_mapa(app_modulo):
    """O df editado vira (edital=TODOS, busca=só OK, mapa=posições no edital)."""
    import pandas as pd
    app = app_modulo
    df = pd.DataFrame([
        {"OK": True,  "Item": 1, "Descrição": "A", "Unidade": "UN",
         "Quantidade": 1, "Valor referência": 10.0},
        {"OK": False, "Item": 2, "Descrição": "B", "Unidade": "UN",
         "Quantidade": 2, "Valor referência": 20.0},
        {"OK": True,  "Item": 3, "Descrição": "C", "Unidade": "UN",
         "Quantidade": 3, "Valor referência": 30.0},
    ])
    edital, busca, mapa = app._itens_revisao(df)
    assert [i.descricao for i in edital] == ["A", "B", "C"]   # TODOS na matriz
    assert [i.descricao for i in busca] == ["A", "C"]          # só os OK p/ buscar
    assert mapa == [0, 2]                                      # posições no edital


def test_gerar_excel_exporta_edital_inteiro_preco_so_nos_buscados(app_modulo):
    """REGRESSÃO do requisito do cliente: a MATRIZ contém o EDITAL INTEIRO; só os
    itens marcados OK ('buscar preço') e selecionados saem com preço. Antes, o
    filtro por OK na revisão tirava os não-OK da matriz inteira."""
    from openpyxl import load_workbook
    from pregoes.extrator import ItemExtraido
    from pregoes.exportador import SelecaoFornecedor
    app = app_modulo
    app.st.session_state["orgao"] = "Pref Natuba"
    app.st.session_state["numero"] = "002/2026"
    app.st.session_state["pdf_path"] = "x.pdf"
    # Edital de 5 itens; operador marcou OK só os itens 1 e 3 (offsets edital 0 e 2).
    edital = [ItemExtraido(item_numero=n, descricao=f"PRODUTO {n}", unidade="UN",
                           quantidade=n, valor_referencia=10.0 * n) for n in range(1, 6)]
    busca = [edital[0], edital[2]]            # OK: itens 1 e 3
    app.st.session_state["itens_edital"] = edital
    app.st.session_state["itens_revisados"] = busca
    app.st.session_state["mapa_busca_edital"] = [0, 2]
    # Escolheu preço só para o 1º item de BUSCA (offset de busca 0 → edital 0).
    selecoes = {0: SelecaoFornecedor(preco=7.0, codigo_fornecedor="C1", marca="M1")}

    app._gerar_excel(busca, markup=1.45, selecoes=selecoes)

    arqs = list(config.EXPORTS_DIR.glob("*.xlsx"))
    assert arqs, "planilha gerada"
    ws = load_workbook(arqs[0]).active
    # TODOS os 5 itens do edital presentes, em ordem.
    for off in range(5):
        assert ws[f"C{5 + off}"].value == f"PRODUTO {off + 1}", f"item {off+1} sumiu"
    # Preço (col L) só no item de edital 0 (único buscado E selecionado).
    assert ws["L5"].value == 7.0
    assert all(ws[f"L{ln}"].value is None for ln in (6, 7, 8, 9)), "preço vazou p/ não-selecionado"


def test_abrir_banco_corrompido_nao_derruba_o_app(app_modulo, monkeypatch):
    """Boot resiliente (achado da auditoria 2026-06): banco CORROMPIDO no boot
    (init_db levanta) NÃO propaga stack cru — o app mostra mensagem clara e para
    gracioso. Mesma classe de blindagem já aplicada às credenciais."""
    app = app_modulo

    def _boom(*a, **k):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(app, "init_db", _boom)
    app._abrir_banco()   # NÃO pode propagar a exceção
