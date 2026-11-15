"""
Backup EXTERNO (Google Drive) — o off-site que protege o cliente contra pane/
perda do disco C: (2026-07-09).

O app só grava um `.zip` numa pasta LOCAL que o Google Drive sincroniza — sem
API do Google. Estes testes cobrem o contrato:
  - grava banco + credenciais + histórico num zip;
  - snapshot CONSISTENTE do banco (sobrevive a WAL/escrita);
  - troca ATÔMICA (nunca deixa `.part` para o Drive sincronizar pela metade);
  - best-effort: NUNCA levanta (pasta ausente/Drive fechado → None + log);
  - a pasta é persistida em credenciais.json preservando as outras entradas.

Sem rede, sem navegador, sem Streamlit — tudo em tmp_path + monkeypatch.
"""
import json
import sqlite3
import zipfile

import pytest

from pregoes import backup, config, credenciais


def _criar_banco(dbp):
    with sqlite3.connect(dbp) as c:
        c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        c.execute("INSERT INTO t (v) VALUES ('sagrado')")


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Isola config + credenciais num tmp e devolve (data_dir, drive_dir)."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "historico").mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "pregoes.db")
    monkeypatch.setattr(config, "HISTORICO_DIR", data / "historico")
    monkeypatch.setattr(credenciais, "CAMINHO", data / "credenciais.json")

    _criar_banco(data / "pregoes.db")
    (data / "credenciais.json").write_text(
        json.dumps({"almeida": {"email": "a@b.c", "senha": "x"}}),
        encoding="utf-8")
    (data / "historico" / "planilha1.xlsx").write_bytes(b"conteudo-hist")

    drive = tmp_path / "GoogleDrive" / "Backup Pregoes"
    drive.mkdir(parents=True)
    return data, drive


# === Caminho feliz: o zip completo aparece no destino ===

def test_grava_zip_com_banco_credenciais_e_historico(ambiente):
    _data, drive = ambiente
    destino = backup.fazer_backup_externo(str(drive))
    assert destino is not None and destino.exists()
    assert destino.name == backup.NOME_BACKUP_EXTERNO

    with zipfile.ZipFile(destino) as z:
        nomes = set(z.namelist())
    assert "data/pregoes.db" in nomes
    assert "data/credenciais.json" in nomes
    assert "data/historico/planilha1.xlsx" in nomes


def test_zip_leva_pregoes_guardados_e_planilhas_geradas(ambiente):
    """No servidor o zip é a ÚNICA cópia fora da máquina: tem que levar também
    os pregões em andamento (rascunhos) e as planilhas já geradas (exports)."""
    data, drive = ambiente
    (data / "rascunhos").mkdir()
    (data / "rascunhos" / "001-2026-prefeitura-ab12cd34.pkl").write_bytes(b"pregao")
    (data / "exports" / "sub").mkdir(parents=True)
    (data / "exports" / "sub" / "matriz.xlsx").write_bytes(b"planilha")

    destino = backup.fazer_backup_externo(str(drive))
    with zipfile.ZipFile(destino) as z:
        nomes = set(z.namelist())
    assert "data/rascunhos/001-2026-prefeitura-ab12cd34.pkl" in nomes
    assert "data/exports/sub/matriz.xlsx" in nomes


def test_snapshot_do_banco_e_restauravel(ambiente, tmp_path):
    """O banco dentro do zip é um SQLite íntegro (snapshot consistente)."""
    _data, drive = ambiente
    destino = backup.fazer_backup_externo(str(drive))
    extraido = tmp_path / "restaurado.db"
    with zipfile.ZipFile(destino) as z:
        extraido.write_bytes(z.read("data/pregoes.db"))
    with sqlite3.connect(extraido) as c:
        assert c.execute("SELECT v FROM t").fetchone()[0] == "sagrado"


def test_troca_atomica_nao_deixa_part(ambiente):
    _data, drive = ambiente
    backup.fazer_backup_externo(str(drive))
    assert not list(drive.glob("*.part")), "sobrou um .part (troca não atômica)"


def test_sobrescreve_backup_anterior(ambiente):
    """Nome fixo: cada backup substitui o anterior (o Drive versiona)."""
    _data, drive = ambiente
    backup.fazer_backup_externo(str(drive))
    backup.fazer_backup_externo(str(drive))
    zips = list(drive.glob("*.zip"))
    assert len(zips) == 1


# === Best-effort: NUNCA levanta ===

def test_pasta_inexistente_retorna_none(ambiente):
    _data, _drive = ambiente
    assert backup.fazer_backup_externo("/nao/existe/zzz-drive") is None


def test_sem_pasta_configurada_retorna_none(ambiente, monkeypatch):
    """destino_dir=None e nada em credenciais → None (backup desligado)."""
    monkeypatch.setattr(credenciais, "carregar_pasta_backup", lambda: None)
    assert backup.fazer_backup_externo(None) is None


def test_le_pasta_das_credenciais_quando_nao_recebe_arg(ambiente):
    _data, drive = ambiente
    credenciais.salvar_pasta_backup(str(drive))
    destino = backup.fazer_backup_externo()  # sem arg → lê de credenciais
    assert destino is not None and destino.exists()


def test_banco_ausente_nao_derruba(ambiente):
    """Sem banco ainda (cliente recém-instalado): grava o resto, não levanta."""
    _data, drive = ambiente
    config.DB_PATH.unlink()
    destino = backup.fazer_backup_externo(str(drive))
    assert destino is not None  # zip sai com credenciais + histórico
    with zipfile.ZipFile(destino) as z:
        assert "data/pregoes.db" not in z.namelist()


# === Persistência da pasta em credenciais.json ===

def test_salvar_e_carregar_pasta_preserva_outras_entradas(ambiente):
    _data, drive = ambiente
    credenciais.salvar_pasta_backup(str(drive))
    assert credenciais.carregar_pasta_backup() == str(drive)
    # A credencial que já existia continua lá.
    assert credenciais.carregar("almeida") == {"email": "a@b.c", "senha": "x"}


def test_pasta_em_branco_desliga(ambiente):
    _data, _drive = ambiente
    credenciais.salvar_pasta_backup("")
    assert credenciais.carregar_pasta_backup() is None


# === Servidor: backup no Google Drive via deploy/backup_drive.sh (2026-10) ===

def test_ultimo_envio_servidor_le_a_data_gravada_pelo_script(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "PASTA_BACKUP_SERVIDOR", tmp_path)
    assert backup.ultimo_envio_servidor() is None            # nunca enviou
    (tmp_path / "ultimo_envio.txt").write_text("2026-10-15T03:01:22-03:00\n",
                                               encoding="utf-8")
    quando = backup.ultimo_envio_servidor()
    assert (quando.day, quando.hour, quando.minute) == (15, 3, 1)
    (tmp_path / "ultimo_envio.txt").write_text("lixo", encoding="utf-8")
    assert backup.ultimo_envio_servidor() is None            # ilegível ≠ ok


@pytest.mark.parametrize("codigo,esperado", [(0, True), (1, False)])
def test_enviar_backup_servidor_reflete_o_script(monkeypatch, codigo, esperado):
    import subprocess as _sp
    monkeypatch.setattr(backup.subprocess, "run", lambda *a, **k:
                        _sp.CompletedProcess(a, codigo, "Backup enviado", "erro"))
    assert backup.enviar_backup_servidor() is esperado


def test_enviar_backup_servidor_nunca_levanta(monkeypatch):
    def _explode(*_a, **_k):
        raise OSError("bash ausente")
    monkeypatch.setattr(backup.subprocess, "run", _explode)
    assert backup.enviar_backup_servidor() is False


@pytest.mark.parametrize("servidor", [True, False])
def test_backup_externo_agora_escolhe_o_caminho_certo(monkeypatch, servidor):
    chamou = []
    monkeypatch.setattr(config, "MODO_SERVIDOR", servidor)
    monkeypatch.setattr(backup, "enviar_backup_servidor",
                        lambda: chamou.append("drive") or True)
    monkeypatch.setattr(backup, "fazer_backup_externo",
                        lambda *a: chamou.append("pasta") or "zip")
    assert backup.backup_externo_agora() is True
    assert chamou == (["drive"] if servidor else ["pasta"])
