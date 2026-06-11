"""
Regressão do achado #11 em db.py (dois defeitos ligados na política de backup):

(a) _rotacionar_backups apagava QUALQUER backup além de 10 pelo mtime, sem
    olhar o motivo — expurgando o backup crítico 'pre-migration'/'pre-import'
    (a rede de segurança contra migration/import ruim) junto com os diários.

(b) backup('diario') não deduplicava por data. Como a guarda em app.py é por
    sessão Streamlit (não por dia), reabrir a UI várias vezes no mesmo dia
    criava vários diários, estourando a rotação de 10 e empurrando o
    pre-migration para fora.

Estes testes FALHAM no código antigo e passam após o fix. Sem rede, sem
navegador, sem Streamlit — usam tmp_path + monkeypatch do config.
"""
import sqlite3

import pytest

from pregoes import config, db


def _criar_banco(dbp):
    """Cria um banco mínimo, restaurável, para os backups copiarem."""
    with sqlite3.connect(dbp) as c:
        c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        c.execute("INSERT INTO t (v) VALUES ('x')")


@pytest.fixture
def banco(tmp_path, monkeypatch):
    """Aponta config para um tmp isolado e devolve o caminho do banco."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    dbp = tmp_path / "t.db"
    _criar_banco(dbp)
    return dbp


def _backups(tmp_path):
    return list((tmp_path / "backups").glob("pregoes_*.db"))


# === Defeito (a): backup protegido sobrevive à rotação ===

def test_pre_migration_sobrevive_a_rotacao(banco, tmp_path):
    """1 pre-migration + 12 diario, rotação manter=10 → o pre-migration fica.

    No código antigo o pre-migration era o mais ANTIGO e caía na rotação por
    volume. Aqui forçamos exatamente o cenário do achado.
    """
    # Rede de segurança criada primeiro (será o mtime mais antigo).
    protegido = db.backup("pre-migration", path=banco)
    assert protegido is not None and protegido.exists()

    # 12 backups de rotina depois — passam do limite de 10.
    for i in range(12):
        db.backup(f"auto{i}", path=banco)

    # O pre-migration NÃO pode ter sido apagado.
    assert protegido.exists(), "backup pre-migration foi expurgado pela rotação"

    # E a rotação de fato limitou os de rotina a 10 (sem contar o protegido).
    rotativos = [p for p in _backups(tmp_path) if "pre-migration" not in p.name]
    assert len(rotativos) <= 10


def test_pre_import_tambem_protegido(banco, tmp_path):
    """O segundo motivo crítico ('pre-import') tem a mesma proteção."""
    protegido = db.backup("pre-import", path=banco)
    for i in range(15):
        db.backup(f"diario{i}", path=banco)
    assert protegido.exists(), "backup pre-import foi expurgado pela rotação"


def test_rotacao_ainda_apaga_backups_de_rotina(banco, tmp_path):
    """Garante que o fix não 'preservou tudo': os de rotina ainda rotacionam."""
    for i in range(14):
        db.backup(f"auto{i}", path=banco, manter=10)
    rotativos = [p for p in _backups(tmp_path)
                 if "pre-migration" not in p.name and "pre-import" not in p.name]
    assert len(rotativos) <= 10


# === Defeito (b): diário deduplica por dia ===

def test_diario_duas_vezes_no_mesmo_dia_cria_um(banco, tmp_path):
    """Chamar backup('diario') 2x no mesmo dia cria só 1 arquivo diário."""
    primeiro = db.backup("diario", path=banco)
    segundo = db.backup("diario", path=banco)

    assert primeiro is not None
    # A segunda chamada não cria novo arquivo: retorna o já existente.
    assert segundo == primeiro

    diarios = [p for p in _backups(tmp_path) if p.name.endswith("_diario.db")]
    assert len(diarios) == 1, f"esperava 1 diário, achei {len(diarios)}"


def test_diario_repetido_nao_estoura_rotacao_e_preserva_protegido(banco, tmp_path):
    """Cenário real: pre-migration + muitas reaberturas no mesmo dia.

    Com a dedup do diário, reabrir a UI N vezes vira 1 só diário, então o
    pre-migration jamais é empurrado para fora da rotação.
    """
    protegido = db.backup("pre-migration", path=banco)
    for _ in range(30):  # 30 'boots' da UI no mesmo dia
        db.backup("diario", path=banco)

    assert protegido.exists()
    diarios = [p for p in _backups(tmp_path) if p.name.endswith("_diario.db")]
    assert len(diarios) == 1


def test_outros_motivos_nao_sao_deduplicados_por_dia(banco, tmp_path):
    """A dedup por dia é EXCLUSIVA do 'diario'.

    Dois 'auto' no mesmo dia (motivos distintos para não colidir no nome,
    já que o timestamp é por segundo) geram dois arquivos — diferente do
    'diario', que pularia o segundo.
    """
    db.backup("auto-a", path=banco)
    db.backup("auto-b", path=banco)
    autos = [p for p in _backups(tmp_path)
             if p.name.endswith("_auto-a.db") or p.name.endswith("_auto-b.db")]
    assert len(autos) == 2


# === Auditoria 2026-06: destino deriva da ORIGEM + porteiro de integridade ===

def test_backup_de_banco_fora_do_data_dir_nao_polui_producao(tmp_path, monkeypatch):
    """Banco de teste em sandbox → backup em sandbox/backups, NUNCA na pasta
    de produção (era a causa dos 600+ 'pre-migration' de teste protegidos
    acumulados em data/backups/ — auditoria 2026-06)."""
    producao = tmp_path / "producao"
    producao.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", producao)
    monkeypatch.setattr(config, "DB_PATH", producao / "pregoes.db")

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    dbp = sandbox / "t.db"
    _criar_banco(dbp)

    destino = db.backup("pre-migration", path=dbp)
    assert destino is not None
    assert destino.parent == sandbox / "backups"
    pasta_prod = producao / "backups"
    assert not pasta_prod.exists() or not list(pasta_prod.glob("pregoes_*.db"))


def test_banco_corrompido_nao_gera_backup_e_levanta_erro(tmp_path):
    """Arquivo que não é um SQLite válido: quick_check falha, NENHUM backup
    é criado (não contamina a rotação) e o erro explica o que fazer."""
    dbp = tmp_path / "c.db"
    dbp.write_bytes(b"isto definitivamente nao e um sqlite valido " * 64)
    with pytest.raises(RuntimeError, match="integridade"):
        db.backup("auto", path=dbp)
    pasta = tmp_path / "backups"
    assert not pasta.exists() or not list(pasta.glob("pregoes_*.db"))


def test_quick_check_aprova_banco_integro_e_backup_sai(tmp_path):
    """Caminho feliz inalterado: banco íntegro passa no porteiro."""
    dbp = tmp_path / "ok.db"
    _criar_banco(dbp)
    destino = db.backup("auto", path=dbp)
    assert destino is not None and destino.exists()
