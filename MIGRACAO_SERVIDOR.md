# Mudar o Pregões de VPS

> Script: `deploy/migrar_servidor.sh` (roda **deste** computador, por SSH).
> Origem atual: VPS Hostinger `179.197.224.46` (`srv2065680`), Ubuntu 24.04.
> Escrito em 2026-11-06 para sair da Hostinger.

## O que viaja

| O quê | Onde fica na VPS | Por quê |
|---|---|---|
| Banco, credenciais dos fornecedores e da IA, rascunhos, histórico, planilhas, editais, log | `/home/pregoes/pregoes/data/` | São os dados do cliente. O banco é copiado pela API de backup do SQLite (cópia consistente). |
| Túnel da Cloudflare (`cert.pem`, `config.yml`, `<id>.json`) | `/home/pregoes/.cloudflared/` | Com eles, a VPS nova assume o **mesmo endereço** sem mexer na Cloudflare. O túnel é de saída: o IP da VPS não importa. |
| Ligação do backup no Google Drive | `/home/pregoes/.config/rclone/rclone.conf` | Sem ela o backup diário para. |
| Estado do backup (data do último envio) | `/home/pregoes/backup-drive/` | A tela Configurações → Backup lê. |

O código **não** viaja no pacote: vai do repositório (commit atual) pelo `git archive`.

**Cópias neste computador:** `~/pregoes_servidor_backup/` (pasta com permissão só
do seu usuário: tem senhas dos fornecedores, chave da IA e credencial do túnel —
**nunca** coloque no repositório, que é público). Cada cópia tem
`pregoes_servidor.tgz` + `extraido/pacote/` + `MANIFESTO.sha256`.

## Antes de começar (você)

1. **VPS nova:** Ubuntu **24.04**, mínimo **4 GB de RAM** (o app com os dois
   Chromium usa ~1,2 GB; o instalador cria 2 GB de swap), de preferência no Brasil.
2. **Chave SSH:** cadastre no painel da VPS nova a chave pública deste Mac:
   `~/.ssh/pregoes_vps.pub`, para entrar como `root`. (A mesma chave está
   guardada também em `~/pregoes_servidor_backup/2026-11-06_vps_hostinger/`.)
3. **Domínio — atenção:** `osvaldoagra.tech` é o domínio **grátis que veio com o
   plano da Hostinger**. Os nameservers estão na Cloudflare (DNS, túnel e login
   continuam funcionando com a VPS nova), mas o **registro** do domínio continua
   na Hostinger. Antes de cancelar a conta de lá, confirme no painel da Hostinger
   (Domínios) que o domínio fica com você e quando vence — ou transfira-o (para a
   própria Cloudflare, por exemplo). Se perder o domínio, o site sai do ar mesmo
   com a VPS nova funcionando; aí é preciso um domínio novo (ver o fim deste guia).

## Passo a passo

```bash
cd ~/projects/pregoes
# 1. Prepara a nova (a antiga segue no ar; ~5–10 min). Pode repetir.
deploy/migrar_servidor.sh preparar NOVO_IP

# 2. A troca: para a antiga, leva os dados FRESCOS, sobe tudo na nova (~3 min fora do ar).
#    Faça fora do horário de uso (ninguém no meio de uma busca).
deploy/migrar_servidor.sh virar NOVO_IP 179.197.224.46

# 3. Conferir a qualquer momento
deploy/migrar_servidor.sh conferir NOVO_IP
```

O `virar` termina com uma conferência: os 3 serviços `active`, o app `HTTP 200`,
o banco `ok` com as **mesmas contagens do pacote**, o backup do Drive ligado, o
túnel com conexões registradas e o **site por fora `HTTP 302`** (é a Cloudflare
pedindo o login; `530`/`502` = túnel fora).

Depois, **abra o site no navegador**, entre e confira: os pregões guardados e o
histórico estão lá, Configurações mostra Almeida/Dantas/IA configuradas, e uma
busca manual devolve preço. Em Configurações → Backup, use "Fazer backup agora"
para confirmar que o envio ao Drive funciona da VPS nova.

**Plano de volta:** a antiga fica só **desligada** (serviços parados, dados
intactos). O `virar` imprime os dois comandos para voltar. Só cancele a antiga
depois de alguns dias de uso normal na nova.

**Antiga inacessível?** `deploy/migrar_servidor.sh virar NOVO_IP` (sem o IP
antigo) restaura a cópia mais recente deste computador. O que foi feito na
antiga depois dessa cópia não vem. Cópia avulsa a qualquer hora, sem parar
nada: `deploy/migrar_servidor.sh salvar IP`.

## Se precisar de um domínio novo

O túnel e o login continuam; muda só o endereço:

1. Adicione o domínio novo na Cloudflare (nameservers).
2. Na VPS: `sudo -u pregoes cloudflared tunnel route dns pregoes pregoes.NOVODOMINIO`
   e troque o `hostname:` em `/home/pregoes/.cloudflared/config.yml`; depois
   `systemctl restart pregoes-tunel`.
3. Zero Trust → Access → Applications → `pregoes`: troque o hostname do aplicativo.
4. Rode o script com `SITE=https://pregoes.NOVODOMINIO deploy/migrar_servidor.sh conferir NOVO_IP`.

## O que não muda

- Os operadores continuam no **mesmo link** e com o **mesmo login** (código no e-mail).
- O limite de consultas dos fornecedores (Q20) é **por conta**, não por IP: mudar
  de VPS não resolve nem piora.
