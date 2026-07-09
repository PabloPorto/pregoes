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
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from . import config, credenciais, db, logconf

_log = logconf.get_logger("backup")

# Nome fixo do zip no destino externo: sobrescrito a cada backup (o Google Drive
# guarda o histórico de versões do arquivo, então não precisamos acumular vários).
NOME_BACKUP_EXTERNO = "Pregoes_backup.zip"


def fazer_backup_externo(destino_dir: "str | Path | None" = None) -> Optional[Path]:
    """Grava um backup COMPLETO (banco + credenciais + histórico) numa pasta
    EXTERNA — tipicamente uma pasta do Google Drive na máquina do cliente, que
    sincroniza para a nuvem sozinha. É a proteção contra pane/perda do disco C:.

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

        # 2) Zip: banco + credenciais + histórico. Escreve num `.part` na PRÓPRIA
        #    pasta destino (mesmo sistema de arquivos) e faz replace ATÔMICO —
        #    o Google Drive nunca sincroniza um zip pela metade.
        with zipfile.ZipFile(zip_part, "w", zipfile.ZIP_DEFLATED) as z:
            if db_snap.exists():
                z.write(db_snap, "data/pregoes.db")
            if Path(credenciais.CAMINHO).exists():
                z.write(credenciais.CAMINHO, "data/credenciais.json")
            hist = Path(config.HISTORICO_DIR)
            if hist.is_dir():
                for arq in hist.rglob("*"):
                    if arq.is_file():
                        z.write(arq, str(Path("data/historico")
                                         / arq.relative_to(hist)))
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
