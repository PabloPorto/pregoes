"""
CLI de backup manual do banco (P0.2): `python -m pregoes.backup`.

Gera uma cópia consistente em data/backups/. Útil antes de qualquer operação
arriscada ou para o cliente fazer um snapshot sob demanda.

Também expõe `fazer_backup_externo` (2026-07-09): copia um backup COMPLETO para
uma pasta EXTERNA (Google Drive na máquina do cliente) — o off-site que protege
contra pane/perda do disco C: (os backups de data/backups/ ficam no MESMO disco).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config, credenciais, db, logconf

_log = logconf.get_logger("backup")

# Nome fixo do zip no destino externo: sobrescrito a cada backup (o Google Drive
# guarda o histórico de versões do arquivo, então não precisamos acumular vários).
NOME_BACKUP_EXTERNO = "Pregoes_backup.zip"


# Pastas de data/ que entram no backup, além do banco e das credenciais.
# rascunhos = pregões guardados/em andamento; exports = planilhas já geradas
# (a tela Histórico as baixa de novo). Entraram em 2026-10: no servidor, este
# zip é a ÚNICA cópia fora da máquina — sem elas, perder a VPS levaria junto o
# trabalho em andamento e as planilhas entregues.
PASTAS_NO_BACKUP = ("historico", "rascunhos", "exports")


def fazer_backup_externo(destino_dir: "str | Path | None" = None) -> Optional[Path]:
    """Grava um backup COMPLETO (banco + credenciais + PASTAS_NO_BACKUP) numa
    pasta EXTERNA — no Windows, uma pasta do Google Drive que sincroniza sozinha;
    no servidor, uma pasta local que o deploy/backup_drive.sh envia ao Drive.
    É a proteção contra pane/perda do disco.

    O app NÃO fala com a API do Google — só grava um `.zip` numa pasta LOCAL que
    o Google Drive Desktop sincroniza (a conta é do cliente). Best-effort: NUNCA
    levanta; devolve o Path do zip, ou None (com log) se a pasta não existe / não
    dá para escrever (ex.: Google Drive fechado, unidade desconectada)."""
    if destino_dir is None:
        destino_dir = credenciais.carregar_pasta_backup()
    if not destino_dir:
        return None
    destino_dir = Path(destino_dir)
    if not destino_dir.is_dir():
        _log.warning("Backup externo: pasta '%s' não encontrada (o Google Drive "
                     "está aberto? a unidade está conectada?).", destino_dir)
        return None

    tmp_dir = None
    zip_part = destino_dir / (NOME_BACKUP_EXTERNO + ".part")
    try:
        # 1) Snapshot CONSISTENTE do banco (mesmo com escrita em andamento/WAL) —
        #    a API sqlite backup garante um arquivo íntegro.
        tmp_dir = Path(tempfile.mkdtemp(prefix="pregoes_bkp_"))
        db_snap = tmp_dir / "pregoes.db"
        if Path(config.DB_PATH).exists():
            with sqlite3.connect(str(config.DB_PATH)) as _src, \
                    sqlite3.connect(str(db_snap)) as _dst:
                _src.backup(_dst)

        # 2) Zip: banco + credenciais + pastas. Escreve num `.part` na PRÓPRIA
        #    pasta destino (mesmo sistema de arquivos) e faz replace ATÔMICO —
        #    o Google Drive nunca sincroniza um zip pela metade.
        with zipfile.ZipFile(zip_part, "w", zipfile.ZIP_DEFLATED) as z:
            if db_snap.exists():
                z.write(db_snap, "data/pregoes.db")
            if Path(credenciais.CAMINHO).exists():
                z.write(credenciais.CAMINHO, "data/credenciais.json")
            for nome in PASTAS_NO_BACKUP:
                pasta = Path(config.DATA_DIR) / nome
                if pasta.is_dir():
                    for arq in pasta.rglob("*"):
                        if arq.is_file():
                            z.write(arq, (Path("data") / nome
                                          / arq.relative_to(pasta)).as_posix())
        destino = destino_dir / NOME_BACKUP_EXTERNO
        os.replace(zip_part, destino)
        _log.info("Backup externo gravado: %s", destino)
        return destino
    except Exception:
        _log.warning("Backup externo falhou (pasta: %s)", destino_dir, exc_info=True)
        try:
            zip_part.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# === Servidor: backup no Google Drive do responsável (deploy/backup_drive.sh) ===
# No servidor não há Google Drive Desktop: o script gera o mesmo zip numa pasta
# local e o rclone envia ao Drive (todo dia às 03:00 pelo pregoes-backup.timer).
PASTA_BACKUP_SERVIDOR = Path.home() / "backup-drive"
_ULTIMO_ENVIO = "ultimo_envio.txt"      # gravado pelo script após enviar


def _script_backup_servidor() -> Path:
    return config.BASE_DIR / "deploy" / "backup_drive.sh"


def ultimo_envio_servidor() -> Optional[datetime]:
    """Quando o último backup chegou ao Drive (None se nunca, ou ilegível)."""
    try:
        texto = (PASTA_BACKUP_SERVIDOR / _ULTIMO_ENVIO).read_text(encoding="utf-8")
        return datetime.fromisoformat(texto.strip())
    except (OSError, ValueError):
        return None


def enviar_backup_servidor() -> bool:
    """Roda o envio ao Drive na hora. Nunca levanta: falha vira log + False."""
    try:
        r = subprocess.run(["bash", str(_script_backup_servidor())],
                           capture_output=True, text=True, timeout=600)
    except Exception:
        _log.warning("Backup no Google Drive não rodou", exc_info=True)
        return False
    if r.returncode != 0:
        _log.warning("Backup no Google Drive falhou (código %s): %s",
                     r.returncode, (r.stderr or r.stdout).strip()[-800:])
        return False
    _log.info("Backup no Google Drive: %s", r.stdout.strip()[-200:])
    return True


def backup_externo_agora() -> bool:
    """Cópia FORA da máquina, no meio do uso (ex.: logo após gerar uma
    planilha). No servidor, envia ao Drive do responsável; no .exe, grava na pasta
    do Google Drive configurada. Best-effort, nunca levanta."""
    if config.MODO_SERVIDOR:
        return enviar_backup_servidor()
    return fazer_backup_externo() is not None


def main(argv: list[str] | None = None) -> int:
    motivo = (argv or sys.argv[1:])
    motivo = motivo[0] if motivo else "manual"
    try:
        destino = db.backup(motivo)
    except RuntimeError as exc:
        # Banco corrompido (porteiro de integridade): mensagem limpa em
        # português, sem stacktrace cru para o operador (CLAUDE.md §10).
        print(f"ERRO: {exc}")
        return 2
    if destino is None:
        print("Banco ainda não existe — nada a salvar.")
        return 1
    print(f"Backup criado: {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
