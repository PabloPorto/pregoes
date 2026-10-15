#!/usr/bin/env bash
# Backup diário do servidor para o Google Drive do RESPONSÁVEL (roda como pregoes,
# pelo pregoes-backup.timer às 03:00). Gera o zip completo do app (banco,
# credenciais, histórico, pregões guardados, planilhas geradas) e envia como
# Pregoes_backup_AAAA-MM-DD.zip na pasta Pregoes-servidor; apaga cópias com mais de 30 dias.
# O rclone usa o escopo drive.file: só enxerga o que ele mesmo criou no Drive.
# Restaurar: baixar o zip do dia e extrair por cima de /home/pregoes/pregoes
# (com o serviço parado) — ver PLANO_SERVIDOR.md.
set -euo pipefail

APP=/home/pregoes/pregoes
LOCAL=/home/pregoes/backup-drive
# A ligação "drive-backup" é fixada (root_folder_id) na pasta Pregoes-servidor,
# criada pelo próprio rclone: com o escopo drive.file ele só pode usar o que
# criou. Fixada pelo ID, a pasta pode ser movida no Drive sem quebrar o backup.
REMOTO=drive-backup:

# Sem a pasta fixada, gravaria (e apagaria antigos) na raiz do Drive: recusa.
if ! rclone config show drive-backup | grep -q '^root_folder_id = .'; then
    echo "A ligação drive-backup não tem a pasta Pregoes-servidor fixada (root_folder_id). Ver PLANO_SERVIDOR.md." >&2
    exit 1
fi

mkdir -p "$LOCAL"
# Um envio por vez (o das 03:00 e o disparado pelo app ao gerar planilha).
exec 9>"$LOCAL/.trava"
flock -w 600 9

cd "$APP"
.venv/bin/python -c "import sys; from pregoes import backup; sys.exit(0 if backup.fazer_backup_externo('$LOCAL') else 1)"

dia=$(date +%F)
rclone copyto "$LOCAL/Pregoes_backup.zip" "${REMOTO}Pregoes_backup_$dia.zip"
rclone delete --min-age 30d "$REMOTO"
date -Iseconds > "$LOCAL/ultimo_envio.txt"   # a tela Configurações → Backup lê
echo "Backup enviado: Pregoes-servidor/Pregoes_backup_$dia.zip"
