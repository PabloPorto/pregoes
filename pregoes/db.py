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

-- Cache de resultados do scraper indexado por (descrição_normalizada, fornecedor).
-- Diferente de precos_historico (que é histórico granular por produto_id),
-- este cache evita re-buscar a MESMA descrição no MESMO fornecedor dentro
-- de uma janela curta. Útil antes da Fase 3 existir, quando ainda não há
-- matcher fuzzy ligando ItemExtraido a produto_id.
CREATE TABLE IF NOT EXISTS precos_cache_descricao (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    descricao_normalizada  TEXT NOT NULL,
    fornecedor             TEXT NOT NULL,
    payload                TEXT NOT NULL,    -- JSON list de ResultadoBusca
    consultado_em          TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(descricao_normalizada, fornecedor)
);

CREATE INDEX IF NOT EXISTS idx_cache_desc_data
    ON precos_cache_descricao(descricao_normalizada, fornecedor, consultado_em DESC);
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


# === Cache de resultados do scraper por descrição normalizada ===
# Solução prática enquanto a Fase 3 (matcher fuzzy) não existe: usa a
# descrição já normalizada (`normalizador.normalizar_descricao`) como
# chave. Quando matcher existir, podemos migrar pra `produto_id` e
# manter este cache só como fallback.

def cache_buscar(
    conn: sqlite3.Connection,
    descricao_normalizada: str,
    fornecedor: str,
    ttl_dias: int = config.CACHE_PRECO_DIAS,
) -> Optional[str]:
    """
    Devolve o payload JSON cacheado para a chave `(desc, fornecedor)`
    se ainda dentro do TTL; senão `None`.

    O payload é JSON cru (string). O caller decide como desserializar —
    desacopla este módulo do dataclass `ResultadoBusca`.
    """
    if not descricao_normalizada or not fornecedor:
        return None
    limite = (datetime.utcnow() - timedelta(days=ttl_dias)).isoformat(sep=" ")
    row = conn.execute(
        """
        SELECT payload
        FROM precos_cache_descricao
        WHERE descricao_normalizada = ?
          AND fornecedor = ?
          AND consultado_em >= ?
        ORDER BY consultado_em DESC
        LIMIT 1
        """,
        (descricao_normalizada, fornecedor, limite),
    ).fetchone()
    return row["payload"] if row else None


def cache_gravar(
    conn: sqlite3.Connection,
    descricao_normalizada: str,
    fornecedor: str,
    payload: str,
) -> None:
    """
    Salva (ou substitui) o payload JSON para a chave `(desc, fornecedor)`.
    UPSERT manual: SQLite `INSERT OR REPLACE` mantém o `id` se já existia
    a tupla, mas reseta `consultado_em` para o momento atual via DEFAULT.
    """
    if not descricao_normalizada or not fornecedor:
        return
    conn.execute(
        """
        INSERT INTO precos_cache_descricao
            (descricao_normalizada, fornecedor, payload, consultado_em)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(descricao_normalizada, fornecedor) DO UPDATE SET
            payload = excluded.payload,
            consultado_em = CURRENT_TIMESTAMP
        """,
        (descricao_normalizada, fornecedor, payload),
    )
