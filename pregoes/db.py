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

-- Cache do LLM judge (Camada 3 da Fase 3 / Q12).
-- Chave = SHA256(descricao_normalizada + códigos_dos_candidatos_ordenados).
-- TTL infinito: a semântica "esta descrição casa com este produto" não muda
-- com o tempo. Custo: ~$0.001 por miss; depois zero pra sempre.
CREATE TABLE IF NOT EXISTS cache_llm_judge (
    chave_hash     TEXT PRIMARY KEY,
    descricao      TEXT NOT NULL,            -- só pra debug/auditoria
    payload        TEXT NOT NULL,            -- JSON list[int] de índices
    criado_em      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Aprendizado por feedback do operador (Camada 4 da Fase 3 / Q12).
-- Quando o operador escolhe uma marca diferente da default na UI, gravamos
-- aqui para que próximas buscas de descrição similar (fuzzy >= 95) reusem.
CREATE TABLE IF NOT EXISTS mapeamentos_aprendidos (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    descricao_normalizada    TEXT NOT NULL,
    fornecedor               TEXT NOT NULL,
    codigo_fornecedor        TEXT NOT NULL,
    marca                    TEXT,
    preco                    REAL,            -- preço observado no momento (referência)
    criado_em                TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(descricao_normalizada, fornecedor)
);

CREATE INDEX IF NOT EXISTS idx_aprendidos_desc
    ON mapeamentos_aprendidos(descricao_normalizada);

-- Cache da normalização canônica via LLM (Fase B / alinhamento de nomes).
-- Chave = SHA256 da descrição normalizada. TTL infinito: a forma canônica
-- de "PARAFUSO SXT 516" → "parafuso sextavado 5/16" não muda com o tempo.
-- Tabela NOVA (não coluna) de propósito: evita migration de schema em
-- bancos que já têm produtos importados.
CREATE TABLE IF NOT EXISTS cache_canonico (
    chave_hash    TEXT PRIMARY KEY,
    descricao     TEXT NOT NULL,
    canonico      TEXT NOT NULL,
    criado_em     TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Forma canônica de cada produto do catálogo (Fase B). Tabela separada de
-- `produtos` para não exigir migration. 1:1 com produtos via produto_id.
CREATE TABLE IF NOT EXISTS produtos_canonico (
    produto_id     INTEGER PRIMARY KEY REFERENCES produtos(id) ON DELETE CASCADE,
    canonico       TEXT NOT NULL,
    atualizado_em  TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


# Espera (s) por um lock antes de desistir. A UI (Streamlit) pode ter uma
# conexão de leitura enquanto a busca grava cache/canônico — sem isso,
# `database is locked` aparecia (visto em 2026-06).
_BUSY_TIMEOUT_S = 30


def conectar(path: Path | None = None) -> sqlite3.Connection:
    """Abre uma conexão SQLite blindada: WAL (leitura concorrente com escrita)
    + busy timeout. Usar em todo lugar que abre conexão direta."""
    path = Path(path) if path else config.DB_PATH
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = %d;" % (_BUSY_TIMEOUT_S * 1000))
    return conn


def init_db(path: Path | None = None) -> Path:
    """Cria o banco e as tabelas se não existirem. Idempotente."""
    path = Path(path) if path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=_BUSY_TIMEOUT_S) as conn:
        conn.execute("PRAGMA journal_mode = WAL;")   # persistente no arquivo
        conn.executescript(SCHEMA)
    return path


@contextmanager
def get_conn(path: Path | None = None):
    """Conexão como context manager — fecha sozinha, faz commit/rollback."""
    path = Path(path) if path else config.DB_PATH
    conn = sqlite3.connect(path, timeout=_BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = %d;" % (_BUSY_TIMEOUT_S * 1000))
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


def cache_limpar(
    conn: sqlite3.Connection,
    fornecedor: str | None = None,
) -> int:
    """Apaga entradas do cache de descrição. Útil quando o algoritmo de
    busca muda — resultados antigos podem refletir o comportamento velho.

    Args:
        fornecedor: se especificado, limpa só esse. None = todos.

    Retorna o número de linhas removidas.
    """
    if fornecedor is None:
        cursor = conn.execute("DELETE FROM precos_cache_descricao")
    else:
        cursor = conn.execute(
            "DELETE FROM precos_cache_descricao WHERE fornecedor = ?",
            (fornecedor,),
        )
    return cursor.rowcount


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


# === Cache do LLM judge (Camada 3) ===
# TTL infinito — a "verdade" sobre se o produto X corresponde ao item Y
# do edital não decai com o tempo. Cache por chave hash de (descrição,
# códigos dos candidatos) — esse conjunto é estável o suficiente.


def llm_judge_buscar(
    conn: sqlite3.Connection, chave_hash: str
) -> Optional[str]:
    """Devolve o payload JSON cacheado, ou None."""
    if not chave_hash:
        return None
    row = conn.execute(
        "SELECT payload FROM cache_llm_judge WHERE chave_hash = ?",
        (chave_hash,),
    ).fetchone()
    return row["payload"] if row else None


def llm_judge_gravar(
    conn: sqlite3.Connection,
    chave_hash: str,
    descricao: str,
    payload: str,
) -> None:
    """Insere ou substitui — TTL é infinito mas tolerante a substituição
    caso re-rodemos com prompt atualizado."""
    if not chave_hash:
        return
    conn.execute(
        """
        INSERT INTO cache_llm_judge (chave_hash, descricao, payload, criado_em)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(chave_hash) DO UPDATE SET
            payload = excluded.payload,
            criado_em = CURRENT_TIMESTAMP
        """,
        (chave_hash, descricao, payload),
    )


# === Cache da normalização canônica via LLM (Fase B) ===


def canonico_buscar_muitos(
    conn: sqlite3.Connection, hashes: list[str]
) -> dict[str, str]:
    """Devolve {hash: canonico} para os hashes que estão no cache."""
    if not hashes:
        return {}
    marcas = ",".join("?" * len(hashes))
    rows = conn.execute(
        f"SELECT chave_hash, canonico FROM cache_canonico WHERE chave_hash IN ({marcas})",
        hashes,
    ).fetchall()
    return {r["chave_hash"]: r["canonico"] for r in rows}


def canonico_gravar_muitos(
    conn: sqlite3.Connection, itens: list[tuple[str, str, str]]
) -> None:
    """Grava em lote. `itens` = [(hash, descricao, canonico), ...]."""
    if not itens:
        return
    conn.executemany(
        """
        INSERT INTO cache_canonico (chave_hash, descricao, canonico, criado_em)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(chave_hash) DO UPDATE SET
            canonico = excluded.canonico,
            criado_em = CURRENT_TIMESTAMP
        """,
        itens,
    )


def produto_canonico_gravar(
    conn: sqlite3.Connection, produto_id: int, canonico: str
) -> None:
    """Salva (ou atualiza) a forma canônica de um produto do catálogo."""
    conn.execute(
        """
        INSERT INTO produtos_canonico (produto_id, canonico, atualizado_em)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(produto_id) DO UPDATE SET
            canonico = excluded.canonico,
            atualizado_em = CURRENT_TIMESTAMP
        """,
        (produto_id, canonico),
    )


# === Aprendizado por feedback do operador (Camada 4) ===


def aprendido_buscar(
    conn: sqlite3.Connection,
    descricao_normalizada: str,
    fornecedor: str,
) -> Optional[sqlite3.Row]:
    """Procura mapeamento previamente aprendido para esta descrição+fornecedor.

    Match exato — quem chama pode adicionalmente fazer busca fuzzy >= 95
    via matcher para reuso em descrições próximas (TODO Camada 4 plena).
    """
    if not descricao_normalizada or not fornecedor:
        return None
    row = conn.execute(
        """
        SELECT codigo_fornecedor, marca, preco, criado_em
        FROM mapeamentos_aprendidos
        WHERE descricao_normalizada = ? AND fornecedor = ?
        """,
        (descricao_normalizada, fornecedor),
    ).fetchone()
    return row


def aprendido_gravar(
    conn: sqlite3.Connection,
    descricao_normalizada: str,
    fornecedor: str,
    codigo_fornecedor: str,
    marca: Optional[str] = None,
    preco: Optional[float] = None,
) -> None:
    """Persiste o mapeamento. UPSERT por (descricao, fornecedor)."""
    if not descricao_normalizada or not fornecedor or not codigo_fornecedor:
        return
    conn.execute(
        """
        INSERT INTO mapeamentos_aprendidos
            (descricao_normalizada, fornecedor, codigo_fornecedor,
             marca, preco, criado_em)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(descricao_normalizada, fornecedor) DO UPDATE SET
            codigo_fornecedor = excluded.codigo_fornecedor,
            marca = excluded.marca,
            preco = excluded.preco,
            criado_em = CURRENT_TIMESTAMP
        """,
        (descricao_normalizada, fornecedor, codigo_fornecedor, marca, preco),
    )
