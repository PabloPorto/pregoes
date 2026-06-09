"""
Testes dos guardrails de produção (P0): migration de schema e backup.
"""
from __future__ import annotations

import sqlite3

import pytest

from pregoes import db, migrations, config


# === P0.1 — Migration de schema versionada ===

class TestMigration:
    def test_init_db_marca_baseline(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
        db.init_db(tmp_path / "t.db")
        # init_db aplica as migrations pendentes; o banco fica no HEAD atual
        # (>= baseline). Com a v2 (exports_log) presente, o head é 2.
        head = max([migrations.BASELINE_VERSION] + [m[0] for m in migrations.MIGRATIONS])
        with db.conectar(tmp_path / "t.db") as c:
            assert migrations._versao_atual(c) == head

    def test_migration_adiciona_coluna_sem_perder_dados(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
        dbp = tmp_path / "t.db"
        db.init_db(dbp)
        with db.conectar(dbp) as c:
            c.execute("INSERT INTO produtos (descricao_canonica) VALUES ('x')")
            c.commit()

        # Versão acima do head real para que esta migration-fake de fato aplique
        # (init_db já avançou o banco até as MIGRATIONS reais).
        nova = max([migrations.BASELINE_VERSION] + [m[0] for m in migrations.MIGRATIONS]) + 1

        def _add_peso(conn):
            conn.execute("ALTER TABLE produtos ADD COLUMN peso REAL")
        monkeypatch.setattr(migrations, "MIGRATIONS",
                            list(migrations.MIGRATIONS) + [(nova, "peso", _add_peso)])

        assert migrations.migrate(dbp) == nova
        with db.conectar(dbp) as c:
            cols = [r[1] for r in c.execute("PRAGMA table_info(produtos)")]
            assert "peso" in cols
            assert c.execute("SELECT COUNT(*) FROM produtos").fetchone()[0] == 1

    def test_migration_idempotente(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
        dbp = tmp_path / "t.db"
        db.init_db(dbp)
        nova = max([migrations.BASELINE_VERSION] + [m[0] for m in migrations.MIGRATIONS]) + 1
        monkeypatch.setattr(migrations, "MIGRATIONS",
                            list(migrations.MIGRATIONS) + [(nova, "idx", lambda c: c.execute(
                                "CREATE INDEX IF NOT EXISTS ix ON produtos(marca)"))])
        assert migrations.migrate(dbp) == nova
        assert migrations.migrate(dbp) == nova   # 2ª vez não reaplica

    def test_banco_preexistente_adotado_como_baseline(self, tmp_path, monkeypatch):
        """Banco com dados mas sem schema_version (cliente atual) é adotado
        como baseline sem perder nada."""
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
        dbp = tmp_path / "t.db"
        # Simula banco "antigo": cria schema mas esvazia schema_version
        db.init_db(dbp)
        with db.conectar(dbp) as c:
            c.execute("INSERT INTO produtos (descricao_canonica) VALUES ('legado')")
            c.execute("DELETE FROM schema_version")
            c.commit()
        # Re-migrate adota como baseline e aplica as pendentes até o head, dado intacto
        head = max([migrations.BASELINE_VERSION] + [m[0] for m in migrations.MIGRATIONS])
        assert migrations.migrate(dbp) == head
        with db.conectar(dbp) as c:
            assert c.execute("SELECT COUNT(*) FROM produtos").fetchone()[0] == 1


# === P0.2 — Backup ===

class TestBackup:
    def test_backup_gera_copia_restauravel(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
        dbp = tmp_path / "t.db"
        db.init_db(dbp)
        with db.conectar(dbp) as c:
            c.execute("INSERT INTO produtos (descricao_canonica) VALUES ('importante')")
            c.commit()

        bkp = db.backup("teste", path=dbp)
        assert bkp is not None and bkp.exists()
        # O backup é consistente e contém o dado
        with sqlite3.connect(bkp) as cb:
            n = cb.execute(
                "SELECT COUNT(*) FROM produtos WHERE descricao_canonica='importante'"
            ).fetchone()[0]
        assert n == 1

    def test_backup_sem_banco_retorna_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        assert db.backup("x", path=tmp_path / "naoexiste.db") is None

    def test_rotacao_mantem_apenas_n(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
        dbp = tmp_path / "t.db"
        db.init_db(dbp)
        # 5 backups, manter 3
        for i in range(5):
            db.backup(f"b{i}", path=dbp, manter=3)
        # A rotação só conta backups ROTACIONÁVEIS; protegidos (ex.: o
        # 'pre-migration' criado por init_db ao aplicar a v2) ficam de fora.
        restantes = [p for p in (tmp_path / "backups").glob("pregoes_*.db")
                     if not db._eh_backup_protegido(p)]
        assert len(restantes) <= 3


# === P0.4 — Mensagens de erro para leigo ===

class TestMensagemAmigavel:
    def test_quota_gemini(self):
        from pregoes.erros import mensagem_amigavel
        msg = mensagem_amigavel(RuntimeError("429 RESOURCE_EXHAUSTED quota"))
        assert "limite" in msg.lower() and "stacktrace" not in msg.lower()

    def test_login_fornecedor(self):
        from pregoes.erros import mensagem_amigavel
        msg = mensagem_amigavel(
            RuntimeError("Falha no login Almeida — cookies de sessão não..."))
        assert "entrar no site" in msg.lower()
        assert "almeida" in msg.lower()

    def test_sem_internet(self):
        from pregoes.erros import mensagem_amigavel
        msg = mensagem_amigavel(OSError("getaddrinfo failed"))
        assert "internet" in msg.lower()

    def test_fallback_generico_com_contexto(self):
        from pregoes.erros import mensagem_amigavel
        msg = mensagem_amigavel(ValueError("algo estranho"), "gerar a planilha")
        assert "gerar a planilha" in msg
        assert "pregoes.log" in msg  # aponta para o log

    def test_nunca_vaza_stacktrace_cru(self):
        from pregoes.erros import mensagem_amigavel
        # Mensagem técnica não deve aparecer na saída ao usuário
        msg = mensagem_amigavel(RuntimeError("Traceback object at 0x7f cookies"))
        assert "0x7f" not in msg
