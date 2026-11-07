#!/usr/bin/env bash
# Muda o Pregões de VPS, rodando DESTE computador (o do responsável), por SSH.
# Ver MIGRACAO_SERVIDOR.md para o passo a passo completo.
#
#   deploy/migrar_servidor.sh preparar NOVO_IP
#       Instala tudo na VPS nova (código, Python, Chromium, cloudflared, rclone)
#       enquanto a antiga SEGUE NO AR. Pode rodar quantas vezes precisar.
#
#   deploy/migrar_servidor.sh virar NOVO_IP [ANTIGO_IP]
#       A troca (alguns minutos fora do ar): para a antiga, tira dela um pacote
#       FRESCO dos dados, restaura na nova, sobe o app e o túnel lá e desliga a
#       antiga (sem apagar nada dela). Sem ANTIGO_IP (antiga inacessível), usa o
#       pacote mais recente salvo neste computador — avisa o que pode faltar.
#
#   deploy/migrar_servidor.sh salvar IP
#       Só copia os dados da VPS para este computador (sem parar nada).
#
#   deploy/migrar_servidor.sh conferir IP
#       Só confere: serviços, banco, túnel, backup e o site por fora.
#
# O túnel da Cloudflare viaja com as credenciais dele (pasta .cloudflared):
# a VPS nova assume o MESMO endereço, sem mexer no painel da Cloudflare.
# Chave SSH: ~/.ssh/pregoes_vps (cadastre a .pub na VPS nova antes).
set -euo pipefail

CHAVE="${CHAVE:-$HOME/.ssh/pregoes_vps}"
BACKUPS="${BACKUPS:-$HOME/pregoes_servidor_backup}"
SITE="${SITE:-https://pregoes.osvaldoagra.tech}"
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
APP=/home/pregoes/pregoes

ssh_em() { local ip=$1; shift; ssh -i "$CHAVE" -o BatchMode=yes -o ConnectTimeout=15 \
    -o StrictHostKeyChecking=accept-new "root@$ip" "$@"; }
passo() { printf '\n==> %s\n' "$*"; }
falha() { printf '\nERRO: %s\n' "$*" >&2; exit 1; }

checar_acesso() {
    ssh_em "$1" true 2>/dev/null || falha "sem acesso SSH a root@$1 com $CHAVE.
Cadastre a chave pública ($CHAVE.pub) no painel da VPS e tente de novo."
}

enviar_codigo() {
    local ip=$1
    passo "Enviando o código ($(git -C "$RAIZ" describe --always --dirty)) para $ip"
    [ -z "$(git -C "$RAIZ" status --porcelain -- pregoes deploy run_app.py requirements.txt)" ] \
        || falha "há mudanças não commitadas no código — commite antes (o envio usa o commit atual)."
    ssh_em "$ip" "mkdir -p $APP"
    # Sem data/: os dados vêm do pacote na troca, nunca do repositório.
    git -C "$RAIZ" archive HEAD -- . ':(exclude)data' | ssh_em "$ip" "tar -x -C $APP"
    ssh_em "$ip" "mkdir -p $APP/data"
}

preparar() {
    local ip=$1
    checar_acesso "$ip"
    ssh_em "$ip" 'grep -q "Ubuntu 24.04" /etc/os-release' \
        || echo "AVISO: a VPS nova não é Ubuntu 24.04 — o instalador foi testado só nele."
    enviar_codigo "$ip"

    passo "Instalando Python, dependências e Chromium (demora ~5 min)"
    # Sobe o app com data/ VAZIA — inofensivo: sem túnel na nova, ninguém o vê.
    # Na troca os dados reais substituem tudo.
    ssh_em "$ip" "bash $APP/deploy/instalar_servidor.sh"

    passo "Instalando cloudflared (repositório oficial da Cloudflare) e rclone"
    ssh_em "$ip" 'set -e
        export DEBIAN_FRONTEND=noninteractive
        if ! command -v cloudflared >/dev/null; then
            mkdir -p --mode=0755 /usr/share/keyrings
            curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
            echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
                > /etc/apt/sources.list.d/cloudflared.list
            apt-get update -q && apt-get install -y -q cloudflared
        fi
        if ! command -v rclone >/dev/null; then
            curl -fsSL -o /tmp/rclone.deb https://downloads.rclone.org/rclone-current-linux-amd64.deb
            apt-get install -y -q /tmp/rclone.deb && rm -f /tmp/rclone.deb
        fi
        cloudflared --version | head -1; rclone version | head -1'
    passo "VPS nova pronta. A antiga continua no ar. Quando quiser trocar:"
    echo "    deploy/migrar_servidor.sh virar $ip ANTIGO_IP"
}

# Pacote dos dados na VPS antiga: banco copiado pela API de backup do SQLite
# (consistente mesmo em WAL) + túnel + rclone + estado do backup no Drive.
pacote_na_antiga() {
    local ip=$1 destino=$2
    ssh_em "$ip" "set -e
        T=/root/migracao; rm -rf \$T; mkdir -p \$T/pacote/data \$T/pacote/rclone
        cd $APP
        sudo -u pregoes .venv/bin/python -c \"import sqlite3; a=sqlite3.connect('data/pregoes.db'); b=sqlite3.connect('/tmp/pregoes.db.copia'); a.backup(b); b.close(); r=sqlite3.connect('/tmp/pregoes.db.copia').execute('pragma integrity_check').fetchone()[0]; assert r=='ok', r\"
        cp -a data/. \$T/pacote/data/
        rm -f \$T/pacote/data/pregoes.db \$T/pacote/data/pregoes.db-wal \$T/pacote/data/pregoes.db-shm
        mv /tmp/pregoes.db.copia \$T/pacote/data/pregoes.db
        cp -a /home/pregoes/.cloudflared \$T/pacote/cloudflared
        cp -a /home/pregoes/.config/rclone/rclone.conf \$T/pacote/rclone/
        [ -d /home/pregoes/backup-drive ] && cp -a /home/pregoes/backup-drive \$T/pacote/backup-drive
        echo \"origem: \$(hostname) \$(date -Is); \$(grep VERSAO_APP pregoes/config.py)\" > \$T/pacote/ORIGEM.txt
        (cd \$T/pacote && find . -type f ! -name MANIFESTO.sha256 -exec sha256sum {} + | sort -k2) > \$T/pacote/MANIFESTO.sha256
        tar -czf \$T/pregoes_servidor.tgz -C \$T pacote"
    scp -q -i "$CHAVE" -o BatchMode=yes "root@$ip:/root/migracao/pregoes_servidor.tgz" "$destino"
}

virar() {
    local novo=$1 antigo=${2:-}
    checar_acesso "$novo"
    ssh_em "$novo" "test -x $APP/.venv/bin/python && command -v cloudflared >/dev/null && command -v rclone >/dev/null" \
        || falha "a VPS nova não está preparada — rode antes: deploy/migrar_servidor.sh preparar $novo"
    umask 077
    local pasta="$BACKUPS/$(date +%Y-%m-%d_%H%M)_troca"
    mkdir -p "$pasta"
    local pacote="$pasta/pregoes_servidor.tgz"

    if [ -n "$antigo" ]; then
        checar_acesso "$antigo"
        passo "Parando a VPS antiga ($antigo) — o site sai do ar a partir daqui"
        ssh_em "$antigo" 'systemctl stop pregoes-backup.timer pregoes-tunel pregoes'
        passo "Tirando o pacote FRESCO dos dados da antiga"
        pacote_na_antiga "$antigo" "$pacote"
    else
        local ultimo
        ultimo=$(ls -t "$BACKUPS"/*/pregoes_servidor.tgz 2>/dev/null | head -1 || true)
        [ -n "$ultimo" ] || falha "sem ANTIGO_IP e sem pacote salvo em $BACKUPS."
        echo "AVISO: usando o pacote salvo $ultimo — o que foi feito na antiga DEPOIS dele não vem."
        cp "$ultimo" "$pacote"
    fi

    passo "Conferindo o pacote neste computador"
    mkdir -p "$pasta/extraido" && tar -xzf "$pacote" -C "$pasta/extraido"
    (cd "$pasta/extraido/pacote" && shasum -a 256 -c MANIFESTO.sha256 --quiet) \
        || falha "o pacote não confere com o manifesto — NÃO restaurado. A antiga está parada: religue com
    ssh -i $CHAVE root@$antigo 'systemctl start pregoes pregoes-tunel pregoes-backup.timer'"

    passo "Restaurando na VPS nova ($novo)"
    scp -q -i "$CHAVE" -o BatchMode=yes "$pacote" "root@$novo:/root/pregoes_servidor.tgz"
    ssh_em "$novo" "set -e
        systemctl stop pregoes pregoes-tunel pregoes-backup.timer 2>/dev/null || true
        rm -rf /root/restaurar && mkdir -p /root/restaurar
        tar -xzf /root/pregoes_servidor.tgz -C /root/restaurar
        P=/root/restaurar/pacote
        (cd \$P && sha256sum -c MANIFESTO.sha256 --quiet)
        rm -rf $APP/data && cp -a \$P/data $APP/data
        rm -rf /home/pregoes/.cloudflared && cp -a \$P/cloudflared /home/pregoes/.cloudflared
        mkdir -p /home/pregoes/.config/rclone && cp -a \$P/rclone/rclone.conf /home/pregoes/.config/rclone/
        [ -d \$P/backup-drive ] && rm -rf /home/pregoes/backup-drive && cp -a \$P/backup-drive /home/pregoes/backup-drive
        chown -R pregoes:pregoes /home/pregoes
        chmod 700 /home/pregoes/.cloudflared
        install -m 644 $APP/deploy/pregoes-tunel.service $APP/deploy/pregoes-backup.service \
            $APP/deploy/pregoes-backup.timer /etc/systemd/system/
        systemctl daemon-reload
        systemctl enable --now pregoes pregoes-tunel pregoes-backup.timer
        systemctl restart pregoes"

    if [ -n "$antigo" ]; then
        passo "Desligando os serviços da antiga (sem apagar dados: ela é o plano de volta)"
        ssh_em "$antigo" 'systemctl disable pregoes pregoes-tunel pregoes-backup.timer' || true
    fi
    conferir "$novo" "$pasta/extraido/pacote/data/pregoes.db"
    echo
    echo "Pacote desta troca guardado em: $pasta"
    if [ -n "$antigo" ]; then
        echo "Plano de volta (se algo der errado na nova):"
        echo "    ssh -i $CHAVE root@$novo 'systemctl disable --now pregoes-tunel pregoes pregoes-backup.timer'"
        echo "    ssh -i $CHAVE root@$antigo 'systemctl enable --now pregoes pregoes-tunel pregoes-backup.timer'"
    fi
}

salvar() {
    local ip=$1
    checar_acesso "$ip"
    umask 077
    local pasta="$BACKUPS/$(date +%Y-%m-%d_%H%M)_copia"
    mkdir -p "$pasta/extraido"
    passo "Copiando os dados de $ip (o app segue no ar)"
    pacote_na_antiga "$ip" "$pasta/pregoes_servidor.tgz"
    tar -xzf "$pasta/pregoes_servidor.tgz" -C "$pasta/extraido"
    (cd "$pasta/extraido/pacote" && shasum -a 256 -c MANIFESTO.sha256 --quiet) \
        || falha "a cópia não confere com o manifesto"
    echo "Cópia conferida: $pasta"
}

conferir() {
    local ip=$1 banco_ref=${2:-}
    checar_acesso "$ip"
    passo "Conferindo a VPS $ip"
    sleep 8
    ssh_em "$ip" "set -e
        for s in pregoes pregoes-tunel pregoes-backup.timer; do printf '  %-22s %s\n' \$s \$(systemctl is-active \$s); done
        printf '  %-22s %s\n' 'app (127.0.0.1:8501)' \"\$(curl -s -o /dev/null -w 'HTTP %{http_code}' http://127.0.0.1:8501/_stcore/health)\"
        printf '  %-22s %s\n' versão \"\$(grep VERSAO_APP $APP/pregoes/config.py | grep -o '[0-9][0-9.]*')\"
        cd $APP && sudo -u pregoes .venv/bin/python -c \"import sqlite3; c=sqlite3.connect('data/pregoes.db'); print('  banco', c.execute('pragma integrity_check').fetchone()[0], {t: c.execute(f'select count(*) from {t}').fetchone()[0] for t in ('produtos','mapeamentos_aprendidos','editais','precos_cache_descricao')})\"
        sudo -u pregoes -H rclone config show drive-backup | grep -q '^root_folder_id = .' && echo '  backup Drive: ligação ok (pasta fixada)' || echo '  backup Drive: SEM ligação — ver MIGRACAO_SERVIDOR.md'
        journalctl -u pregoes-tunel -b --no-pager | grep -c 'Registered tunnel connection' | sed 's/^/  túnel: conexões registradas desde o boot = /' || true"
    if [ -n "$banco_ref" ]; then
        /usr/bin/env python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print('  pacote (referência)', {t: c.execute(f'select count(*) from {t}').fetchone()[0] for t in ('produtos','mapeamentos_aprendidos','editais','precos_cache_descricao')})" "$banco_ref"
    fi
    # Por fora: com o túnel no ar a Cloudflare responde com o login do Access
    # (302); 530/502 = túnel fora.
    printf '  %-22s %s\n' "site por fora" "$(curl -s -o /dev/null -w 'HTTP %{http_code}' "$SITE")  (302 = no ar, pedindo login; 530/502 = túnel fora)"
}

case "${1:-}" in
    preparar) [ $# -eq 2 ] || falha "uso: $0 preparar NOVO_IP"; preparar "$2" ;;
    virar)    [ $# -ge 2 ] || falha "uso: $0 virar NOVO_IP [ANTIGO_IP]"; virar "$2" "${3:-}" ;;
    salvar)   [ $# -eq 2 ] || falha "uso: $0 salvar IP"; salvar "$2" ;;
    conferir) [ $# -eq 2 ] || falha "uso: $0 conferir IP"; conferir "$2" ;;
    *) sed -n '2,23p' "$0"; exit 1 ;;
esac
