"""Regressão da ATOMICIDADE POR ARQUIVO do importador (achado medio#4 da
auditoria): um arquivo que falha no meio NÃO pode deixar linhas parciais no
catálogo, os demais arquivos do batch não podem ser afetados, e cada arquivo
é registrado em imports_log. Testa a lógica transacional de `importar()` com
`_importar_arquivo` dublado — isola o controle de transação do parsing."""
from __future__ import annotations

from pregoes import config, db, importador


def _setup_db(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db(db_path)
    return db_path


def test_falha_no_meio_faz_rollback_e_nao_afeta_outros(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    # Dublê: cada arquivo insere 1 produto; o "ruim" insere e DEPOIS levanta
    # (simula falha no meio da planilha, com linha já inserida).
    def fake_import(path, *, fornecedor, conn, stats):
        conn.execute("INSERT INTO produtos (descricao_canonica) VALUES (?)",
                     (path.stem,))
        stats.produtos_criados += 1
        if "ruim" in path.name:
            raise RuntimeError("linha ruim no meio da planilha")

    monkeypatch.setattr(importador, "_importar_arquivo", fake_import)

    bom = tmp_path / "bom.xlsx"; bom.write_bytes(b"x")
    ruim = tmp_path / "ruim.xlsx"; ruim.write_bytes(b"x")
    bom2 = tmp_path / "bom2.xlsx"; bom2.write_bytes(b"x")

    conn = db.conectar()
    stats = importador.importar([bom, ruim, bom2], conn=conn)

    descs = {r[0] for r in conn.execute("SELECT descricao_canonica FROM produtos")}
    assert "bom" in descs and "bom2" in descs          # bons commitados
    assert "ruim" not in descs                          # ruim REVERTIDO (rollback)
    assert stats.arquivos_processados == 2              # só os bons
    assert stats.produtos_criados == 2                  # contador revertido p/ o ruim

    logs = {r["arquivo"]: r["status"]
            for r in conn.execute("SELECT arquivo, status FROM imports_log")}
    assert logs == {"bom.xlsx": "ok", "ruim.xlsx": "falha", "bom2.xlsx": "ok"}
    conn.close()


def test_arquivo_inexistente_loga_nao_encontrado(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    conn = db.conectar()
    stats = importador.importar([tmp_path / "nao_existe.xlsx"], conn=conn)
    assert stats.arquivos_processados == 0
    row = conn.execute(
        "SELECT arquivo, status FROM imports_log").fetchone()
    assert row["arquivo"] == "nao_existe.xlsx"
    assert row["status"] == "nao_encontrado"
    conn.close()


def test_sucesso_registra_contagem_no_log(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    def fake_import(path, *, fornecedor, conn, stats):
        conn.execute("INSERT INTO produtos (descricao_canonica) VALUES (?)",
                     (path.stem,))
        stats.produtos_criados += 1

    monkeypatch.setattr(importador, "_importar_arquivo", fake_import)

    f = tmp_path / "planilha.xlsx"; f.write_bytes(b"x")
    conn = db.conectar()
    importador.importar([f], conn=conn)
    row = conn.execute(
        "SELECT n_produtos, status FROM imports_log").fetchone()
    assert row["status"] == "ok"
    assert row["n_produtos"] == 1
    conn.close()
