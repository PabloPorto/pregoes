"""
Banco SQLite — schema mínimo, suficiente para o MVP.

Filosofia: começar com poucas tabelas e adicionar quando houver dor real.
Nada de criar tabelas "porque pode precisar".

Tabelas:
- produtos:           catálogo canônico do cliente (cresce com uso)
- precos_historico:   toda consulta de preço no tempo (alimenta cache + dashboard)
- editais:            cada PDF processado vira um edital
- edital_itens:       cada linha de cada edital (vincula a produtos quando casa)
- mapeamentos_pdf:    memória de heurísticas de extração (resp 9 = schemas variáveis)
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from . import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS produtos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    descricao_canonica  TEXT NOT NULL,
    unidade_padrao      TEXT,
    marca               TEXT,
    codigo_almeida      TEXT,
    codigo_dantas       TEXT,
    ncm                 TEXT,
    criado_em           TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(descricao_canonica, marca)
);

CREATE INDEX IF NOT EXISTS idx_produtos_descricao
    ON produtos(descricao_canonica);

CREATE TABLE IF NOT EXISTS precos_historico (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    produto_id      INTEGER NOT NULL REFERENCES produtos(id),
    fornecedor      TEXT NOT NULL,        -- 'almeida' | 'dantas'
    preco           REAL NOT NULL,
    unidade         TEXT,
    consultado_em   TEXT DEFAULT CURRENT_TIMESTAMP,
    fonte           TEXT                  -- url ou 'manual'
);

CREATE INDEX IF NOT EXISTS idx_precos_produto_data
    ON precos_historico(produto_id, consultado_em DESC);

CREATE TABLE IF NOT EXISTS editais (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    orgao           TEXT,
    numero_pregao   TEXT,
    arquivo_pdf     TEXT NOT NULL,
    status          TEXT DEFAULT 'rascunho',  -- rascunho | precificado | exportado | ganho | perdido
    criado_em       TEXT DEFAULT CURRENT_TIMESTAMP,
    atualizado_em   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS edital_itens (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    edital_id            INTEGER NOT NULL REFERENCES editais(id) ON DELETE CASCADE,
    item_numero          INTEGER,
    codigo_orgao         TEXT,
    descricao_original   TEXT NOT NULL,
    unidade              TEXT,
    quantidade           REAL,
    valor_referencia     REAL,
    produto_id           INTEGER REFERENCES produtos(id),  -- NULL quando não casou
    preco_base           REAL,
    confianca_match      TEXT,    -- 'exato' | 'variacao' | 'manual' | NULL
    revisado             INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_itens_edital
    ON edital_itens(edital_id);

CREATE TABLE IF NOT EXISTS mapeamentos_pdf (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    assinatura      TEXT UNIQUE NOT NULL,  -- hash dos cabeçalhos detectados
    mapa_colunas    TEXT NOT NULL,          -- JSON: {"descricao": 1, "quant": 4, ...}
    aprendido_em    TEXT DEFAULT CURRENT_TIMESTAMP,
    usado_count     INTEGER DEFAULT 1
);
"""


def init_db(path: Path | None = None) -> Path:
    """Cria o banco e as tabelas se não existirem. Idempotente."""
    path = Path(path) if path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
    return path


@contextmanager
def get_conn(path: Path | None = None):
    """Conexão como context manager — fecha sozinha, faz commit/rollback."""
    path = Path(path) if path else config.DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def preco_em_cache(
    conn: sqlite3.Connection,
    produto_id: int,
    fornecedor: str,
    ttl_dias: int = config.CACHE_PRECO_DIAS,
) -> Optional[sqlite3.Row]:
    """
    Retorna o registro de preço mais recente de `precos_historico` para
    `(produto_id, fornecedor)` se ele estiver dentro do TTL. Senão, None.

    Usado pelo scraper para evitar re-buscar o mesmo produto no mesmo
    fornecedor dentro da janela de 2 dias (decisão Resp final 9 / Q9).

    Retorna `sqlite3.Row` (acesso por nome) com os campos da tabela:
    id, produto_id, fornecedor, preco, unidade, consultado_em, fonte.
    """
    limite = (datetime.utcnow() - timedelta(days=ttl_dias)).isoformat(sep=" ")
    cursor = conn.execute(
        """
        SELECT id, produto_id, fornecedor, preco, unidade, consultado_em, fonte
        FROM precos_historico
        WHERE produto_id = ?
          AND fornecedor = ?
          AND consultado_em >= ?
        ORDER BY consultado_em DESC
        LIMIT 1
        """,
        (produto_id, fornecedor, limite),
    )
    return cursor.fetchone()


def gravar_preco(
    conn: sqlite3.Connection,
    produto_id: int,
    fornecedor: str,
    preco: float,
    unidade: str | None = None,
    fonte: str | None = None,
) -> int:
    """
    Insere um registro novo em `precos_historico`. Retorna o id gerado.

    O scraper chama isto após cada busca bem-sucedida. Não fazemos UPDATE:
    o histórico precisa preservar todas as consultas para o dashboard de
    variação da Fase 3.
    """
    cursor = conn.execute(
        """
        INSERT INTO precos_historico (produto_id, fornecedor, preco, unidade, fonte)
        VALUES (?, ?, ?, ?, ?)
        """,
        (produto_id, fornecedor, preco, unidade, fonte),
    )
    return cursor.lastrowid
