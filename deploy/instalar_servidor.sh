#!/usr/bin/env bash
# Prepara um Ubuntu 24.04 limpo para rodar o Pregões (rodar como root).
# Pré-requisito: o código já está em /home/pregoes/pregoes (ver PLANO_SERVIDOR.md).
# Idempotente: rodar de novo só reinstala dependências e reinicia o serviço.
set -euo pipefail

APP_USER=pregoes
APP_DIR=/home/$APP_USER/pregoes

timedatectl set-timezone America/Sao_Paulo   # datas de log/backup no horário do cliente
apt-get update -q
apt-get install -y -q python3-venv git

id -u "$APP_USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$APP_USER"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# 4 GB de RAM com dois Chromium abertos (~1,2 GB medidos) cabe, mas um swap
# de folga evita o OOM matar o app num pico.
if ! swapon --show | grep -q .; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile
    mkswap /swapfile && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/python" -m playwright install-deps chromium   # libs do sistema (root)
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" -m playwright install chromium

install -m 644 "$APP_DIR/deploy/pregoes.service" /etc/systemd/system/pregoes.service
systemctl daemon-reload
systemctl enable pregoes
systemctl restart pregoes
sleep 5
systemctl --no-pager --lines=5 status pregoes
