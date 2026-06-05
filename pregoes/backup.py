"""
CLI de backup manual do banco (P0.2): `python -m pregoes.backup`.

Gera uma cópia consistente em data/backups/. Útil antes de qualquer operação
arriscada ou para o cliente fazer um snapshot sob demanda.
"""
from __future__ import annotations

import sys

from . import db


def main(argv: list[str] | None = None) -> int:
    motivo = (argv or sys.argv[1:])
    motivo = motivo[0] if motivo else "manual"
    destino = db.backup(motivo)
    if destino is None:
        print("Banco ainda não existe — nada a salvar.")
        return 1
    print(f"Backup criado: {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
