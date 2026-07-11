"""
Regressão do 'database is locked' na 1ª busca pós-instalação (Windows 2026-07-11).

Traceback de campo: os workers da busca (ThreadPoolExecutor, uma conexão SQLite
por thread) estouravam em `db.cache_gravar` com `sqlite3.OperationalError:
database is locked`.

Causa: `_invalidar_cache_se_versao_mudou` faz `db.cache_limpar` (um DELETE) na
conexão PRINCIPAL e o sqlite3 (isolation padrão) mantém a transação ABERTA até um
commit. Sem commit, a principal segura o write lock do WAL durante toda a busca e
os workers batem no lock após o `busy_timeout`. Só dispara na 1ª busca após
instalar/atualizar, quando `data/.busca_versao` ainda não existe (versão "mudou")
e a invalidação roda.

Fix: `_invalidar_cache_se_versao_mudou` commita logo após o DELETE; e a busca
commita a conexão principal antes de abrir os workers (defesa em profundidade).

Estes testes FALHAM no código antigo e passam após o fix. Sem rede, sem
navegador — tmp_path + monkeypatch do config.
"""
import json
import sqlite3

import pytest

from pregoes import config, db, scraper


@pytest.fixture
def banco(tmp_path, monkeypatch):
    """Config apontando para um tmp isolado; devolve o caminho do banco."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init_db(tmp_path / "t.db")
    return tmp_path / "t.db"


def test_invalidacao_nao_deixa_transacao_aberta(banco, tmp_path):
    """Após invalidar o cache, a conexão principal NÃO pode ficar com uma
    transação de escrita aberta — senão segura o write lock e trava os workers."""
    # .busca_versao ausente => versão "mudou" => cache_limpar (DELETE) dispara.
    assert not (tmp_path / ".busca_versao").exists()
    conn = db.conectar(banco)
    try:
        scraper._invalidar_cache_se_versao_mudou(conn)
        assert not conn.in_transaction, (
            "conexão principal segurou a transação do DELETE — os workers "
            "baterão em 'database is locked'")
    finally:
        conn.close()


def test_segunda_conexao_grava_apos_invalidacao(banco):
    """Cenário do traceback: com a principal tendo invalidado o cache, uma 2ª
    conexão (worker) grava no cache SEM 'database is locked'."""
    principal = db.conectar(banco)
    worker = sqlite3.connect(banco, timeout=2)
    worker.execute("PRAGMA busy_timeout = 2000;")   # falha RÁPIDO se travado
    try:
        scraper._invalidar_cache_se_versao_mudou(principal)   # principal escreve
        payload = json.dumps({"resultados": [], "consultado_em": "2026-07-11"})
        # No código antigo isto bloqueia 2s e levanta OperationalError.
        db.cache_gravar(worker, "cano pvc 50mm", "almeida", payload)
        worker.commit()
        # Confirma que gravou de fato.
        n = worker.execute(
            "SELECT COUNT(*) FROM precos_cache_descricao").fetchone()[0]
        assert n == 1
    finally:
        worker.close()
        principal.close()


def test_invalidacao_idempotente_nao_reescreve_versao(banco, tmp_path):
    """Segunda chamada (versão já casada) não escreve nada e não abre transação
    — o custo do fix não recai em toda busca, só na 1ª pós-instalação."""
    conn = db.conectar(banco)
    try:
        scraper._invalidar_cache_se_versao_mudou(conn)     # 1ª: grava a versão
        assert (tmp_path / ".busca_versao").exists()
        scraper._invalidar_cache_se_versao_mudou(conn)     # 2ª: no-op
        assert not conn.in_transaction
    finally:
        conn.close()
