#!/usr/bin/env bash
# Backup diário do servidor para o Google Drive do CLIENTE (roda como pregoes,
# pelo pregoes-backup.timer às 03:00). Gera o zip completo do app (banco,
# credenciais, histórico, pregões guardados, planilhas geradas) e envia como
# Pregoes-servidor/Pregoes_backup_AAAA-MM-DD.zip; apaga cópias com mais de 30 dias.
# O rclone usa o escopo drive.file: só enxerga o que ele mesmo criou no Drive.
# Restaurar: baixar o zip do dia e extrair por cima de /home/pregoes/pregoes
# (com o serviço parado) — ver PLANO_SERVIDOR.md.
set -euo pipefail

APP=/home/pregoes/pregoes
LOCAL=/home/pregoes/backup-drive
REMOTO=drive-cliente:Pregoes-servidor

mkdir -p "$LOCAL"
cd "$APP"
.venv/bin/python -c "import sys; from pregoes import backup; sys.exit(0 if backup.fazer_backup_externo('$LOCAL') else 1)"

dia=$(date +%F)
rclone copyto "$LOCAL/Pregoes_backup.zip" "$REMOTO/Pregoes_backup_$dia.zip"
rclone delete --min-age 30d "$REMOTO"
echo "Backup enviado: $REMOTO/Pregoes_backup_$dia.zip"
