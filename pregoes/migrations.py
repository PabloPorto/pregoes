"""
Migration versionada do schema SQLite (P0.1).

O schema base (`db.SCHEMA`, criado por `init_db` com CREATE TABLE IF NOT
EXISTS) é a BASELINE v1. Mudanças ESTRUTURAIS futuras — adicionar coluna,
recriar tabela, novos índices — entram aqui como migrations numeradas; cada
uma leva o banco da versão N-1 para N.

Por quê: CREATE TABLE IF NOT EXISTS cria tabelas novas, mas NÃO adiciona
colunas a tabelas que já existem no banco do cliente. Sem migration, uma
atualização que adicione, por exemplo, `produtos.peso` quebraria o banco que
já acumulou meses de dados em produção.

Padrão (sqlite3 puro, coerente com o projeto — sem Alembic):
- `schema_version` guarda a MAX(version) aplicada.
- `migrate()` faz BACKUP antes, aplica as pendentes em transação, registra
  cada versão. Idempotente.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

from . import config, db


# O schema base atual corresponde à versão 1.
BASELINE_VERSION = 1


# --- v2: exports_log (histórico de planilhas geradas) ---
# Mesma DDL espelhada em `db.SCHEMA` (bancos novos nascem com a tabela); aqui
# ela leva bancos JÁ existentes da v1 para a v2. Aditivo puro (IF NOT EXISTS):
# zero risco aos dados de produtos/editais/preços.
_DDL_EXPORTS_LOG = """
CREATE TABLE IF NOT EXISTS exports_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    edital_id     INTEGER NOT NULL REFERENCES editais(id) ON DELETE CASCADE,
    arquivo       TEXT NOT NULL,        -- SÓ o basename; caminho = EXPORTS_DIR/arquivo
    n_itens       INTEGER DEFAULT 0,
    n_selecoes    INTEGER DEFAULT 0,    -- itens com preço/marca (len(selecoes))
    markup_global REAL,                 -- markup do pregão (auditoria)
    status        TEXT NOT NULL DEFAULT 'gerado',
    criado_em     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_exports_edital ON exports_log(edital_id, criado_em DESC);
"""


def _v2_exports_log(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL_EXPORTS_LOG)   # CREATE TABLE/INDEX IF NOT EXISTS


# Migrations futuras: (versao, descricao, funcao(conn)).
# A função recebe a conexão e aplica o ALTER/CREATE necessário. NÃO precisa
# abrir transação — `migrate()` já envolve cada uma numa.
#
# Exemplo de migration futura:
#   def _v3_add_peso(conn):
#       conn.execute("ALTER TABLE produtos ADD COLUMN peso REAL")
#   MIGRATIONS = [..., (3, "produtos.peso", _v3_add_peso)]
_DDL_NCM_CACHE = """
CREATE TABLE IF NOT EXISTS ncm_cache (
    fornecedor    TEXT NOT NULL,
    codigo        TEXT NOT NULL,
    ncm           TEXT,
    consultado_em TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (fornecedor, codigo)
);
"""


def _v3_ncm_cache(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL_NCM_CACHE)   # CREATE TABLE IF NOT EXISTS


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (2, "exports_log (historico de planilhas geradas)", _v2_exports_log),
    (3, "ncm_cache (NCM duravel por produto)", _v3_ncm_cache),
]


def _versao_atual(conn: sqlite3.Connection) -> int:
    """MAX(version) registrada, ou 0 se nunca versionado."""
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] if row and row[0] is not None else 0


def _registrar_versao(conn: sqlite3.Connection, versao: int) -> None:
    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (versao,))


def migrate(path: Path | None = None) -> int:
    """Aplica as migrations pendentes. Retorna a versão final do schema.

    Idempotente: rodar 2× não reaplica nada. Faz BACKUP antes de qualquer
    alteração estrutural. Bancos pré-existentes (com dados, sem
    `schema_version`) são adotados como BASELINE sem alteração.
    """
    p = Path(path) if path else config.DB_PATH
    conn = db.conectar(p)
    try:
        atual = _versao_atual(conn)
        if atual == 0:
            # Banco novo OU pré-versionamento — já tem o schema completo via
            # CREATE IF NOT EXISTS. Adota como baseline, sem tocar nos dados.
            with conn:
                _registrar_versao(conn, BASELINE_VERSION)
            atual = BASELINE_VERSION

        pendentes = sorted(
            (m for m in MIGRATIONS if m[0] > atual), key=lambda m: m[0]
        )
        if not pendentes:
            return atual

        # Backup ANTES de mexer na estrutura — protege contra migration ruim.
        db.backup("pre-migration", path=p)

        for versao, _desc, funcao in pendentes:
            with conn:                       # transação por migration
                funcao(conn)
                _registrar_versao(conn, versao)
            atual = versao
        return atual
    finally:
        conn.close()
