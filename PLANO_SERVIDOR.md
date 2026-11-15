# Plano: Pregões num servidor (vários computadores, manutenção remota)

> Status (2026-10-15, Q19): **validação de 1 mês numa VPS Hostinger KVM 1 em
> São Paulo**, plano mensal. Se validar, renova por período maior.

## Por quê

- O cliente precisa usar o sistema em **mais de um computador** do escritório.
- Manter o `.exe` (build PyInstaller + instalação à distância) ficou caro, e o
  responsável está fora do país — sem acesso à máquina do cliente. Num servidor,
  atualizar vira um comando e os operadores só abrem um link no navegador.

## Por que Hostinger (e não grátis)

| Opção | Por que não |
|---|---|
| Oracle Always Free | Cadastro recusado pelo antifraude (2026-10-15); além disso recolhe máquina ociosa e cortou o limite em 06/2026 |
| AWS Free Tier | Desde 07/2025 conta nova só tem créditos por 6 meses — depois é paga ou encerrada |
| GCP Always Free | e2-micro tem 1 GB de RAM (o app usa ~1,2 GB) e só existe nos EUA |
| PC do escritório | Exige configurar a máquina do cliente no local — inviável estando fora do país |
| **Hostinger KVM 1, São Paulo** | ✅ 1 vCPU, 4 GB, 50 GB NVMe; IP no Brasil; plano mensal para validar; reembolso em 30 dias |

## Arquitetura

```
 PCs do escritório (navegador)                 VPS Hostinger (São Paulo)
 ┌───────────────┐   HTTPS + login por   ┌──────────────┐  túnel de saída  ┌────────────────────────┐
 │ operador 1    │──código no e-mail ───▶│  Cloudflare  │◀────────────────│ cloudflared            │
 │ operador 2    │  (Cloudflare Access)  │ Tunnel+Access│                 │   └▶ 127.0.0.1:8501    │
 └───────────────┘                       └──────────────┘                 │  Streamlit (systemd)   │
                                                                          │  SQLite + data/        │
 dev (fora do país) ── SSH com chave ────────────────────────────────────▶│  Chromium (scraper)    │
                                                                          └────────────────────────┘
```

- O app escuta só em `127.0.0.1`; ninguém acessa a porta 8501 de fora. Os
  operadores entram pelo túnel da Cloudflare, com login por código no e-mail.
- O app roda **do código-fonte** (venv + `deploy/pregoes.service`), sem `.exe`.

## Custo

| Item | Custo |
|---|---|
| Hostinger KVM 1 (validação) | 1 mês no plano mensal — conferir no checkout |
| Domínio `.com.br` (registro.br) — só a partir da Fase 3 | ~R$ 40/ano |
| Cloudflare Tunnel + Access (até 50 usuários) | R$ 0 |
| Depois de validado | KVM 1 por 24 meses: ~R$ 25–28/mês (promo), renovação ~R$ 48/mês |

## Dimensionamento (medido em 2026-10-15)

App ~150 MB + Chromium Almeida ~350 MB + Chromium Dantas ~650 MB = **~1,2 GB**.
4 GB de RAM + 2 GB de swap (criado pelo instalador) dá folga.

## Roteiro da validação

Legenda: 👤 = você · 🤖 = eu

### Fase 1 — Máquina no ar e teste de IP — ✅ CONCLUÍDA em 2026-10-15

> VPS Hostinger em Campinas/SP (IP no painel da Hostinger). Instalador rodou; serviço no ar.
> `teste_ao_vivo` pelo servidor: login + preços nos **dois** fornecedores
> (4 de 5 itens com preço; o 5º é relevância, não bloqueio). Sem recusa no log.


1. 👤 Contratar **KVM 1**, local **São Paulo (Brasil)** — se não aparecer, me
   avisar antes de pagar. Sistema: **Ubuntu 24.04** (sem painel).
2. 👤 No painel da VPS → **Chaves SSH**: cadastrar a chave pública que gerei
   (`~/.ssh/pregoes_vps.pub` desta máquina). Me passar o **IP**.
3. 🤖 Enviar o código (`git archive` daqui), rodar `deploy/instalar_servidor.sh`.
4. 🤖 Teste de IP com as credenciais dos fornecedores:
   `python -m scripts.teste_ao_vivo data/editais/araruna_teste_sintetico.pdf 5`

**Critério:** login e preços nos **dois** fornecedores.
- ✅ Passou → Fase 2.
- ❌ Bloqueou → pedir reembolso (30 dias) e repensar.

### Fase 2 — Validar o app no servidor (🤖, ~1 dia)

- 🤖 Abrir o app pelo túnel SSH (`ssh -L 8501:127.0.0.1:8501`) e repetir os
  testes no navegador: novo pregão, busca em lote, busca manual, gerar planilha,
  F5 no meio do pregão.
- ✅ **Scraper multiusuário** (2026-10-15, commit 1a4eb70): uma thread dona dos
  navegadores + fila (`scraper.no_navegador`). Validado no servidor com os sites
  reais: 2º clique reusa a sessão (0,7 s, sem relogin), dois operadores ao mesmo
  tempo OK, 1 login por fornecedor, nenhum Chromium órfão ao fechar.
  **Troca aceita:** operações de navegador são uma por vez — a busca de um
  operador pode esperar a do outro (a tela avisa "Aguardando a busca de outro
  operador terminar…"). Medir na Fase 5; se incomodar, a evolução é uma fila
  por fornecedor.

### Fase 3 — Acesso para o cliente — ✅ no ar com login (2026-10-15)

Feito:
- Domínio de teste da Hostinger (1º ano grátis) na conta Cloudflare do
  responsável; nameservers trocados para a Cloudflare (propagando).
- `cloudflared` no servidor; túnel nomeado `pregoes` autorizado pelo
  responsável (`cloudflared tunnel login`); registro DNS criado pelo próprio
  túnel (`tunnel route dns`); config em `/home/pregoes/.cloudflared/config.yml`
  (fora do git) → `http://127.0.0.1:8501`.
- Serviço `deploy/pregoes-tunel.service` (sobe sozinho, reinicia se cair).
- O endereço fica só no servidor e com o responsável — **não versionar**.

**Login ligado (Cloudflare Access, 2026-10-15)**, no time Zero Trust já
existente do responsável (um time por conta; cada aplicativo tem a sua lista):
aplicativo self-hosted `pregoes` no hostname do app, política "Operadores
Pregoes" (Allow só para os e-mails liberados), login por código no e-mail
(One-time PIN), sessão de 1 semana. Verificado de fora: sem login, tudo
redireciona para a tela de login da Cloudflare. Incluir/tirar alguém: editar a
política "Operadores Pregoes" (Access controls → Policies).

### Fase 4 — Dados: o servidor começa do zero (decidido em 2026-10-15)

Sem migração: o cliente não tem dados que precisem ser levados (decisão do
responsável). Nada é pedido da máquina dele. O `.exe` continua instalado no
cliente durante a validação, como plano de volta.

### Fase 5 — Uso real acompanhado (cliente, restante do mês)

- [ ] Os dois operadores entram pelo link, cada um no seu PC.
- [ ] Busca em lote num PC enquanto o outro faz busca manual.
- [ ] Gerar e baixar a planilha (fórmulas preservadas).
- [ ] Nenhuma queda/lentidão relevante na semana.

**Validou →** contratar por período maior e atualizar o CLAUDE.md (§7 "Onde roda").

## Backup (servidor) — ✅ no ar e restauração testada (2026-10-15)

> 1º envio ao Drive do responsável OK; zip baixado DO DRIVE restaurado numa
> pasta temporária: banco íntegro e idêntico ao do servidor. Client ID OAuth
> próprio no Google Cloud (o compartilhado do rclone está sendo desativado em
> 2026), app publicado "Em produção" — no modo teste a autorização cai em 7 dias.

- **Diário dentro do servidor**: o app já faz (data/backups, com rotação) —
  protege contra erro/corrupção do banco, não contra perder a VPS.
- **Fora do servidor** (`deploy/backup_drive.sh`, `pregoes-backup.timer`):
  todo dia às 03:00 e a cada planilha gerada, o zip completo do app (banco,
  credenciais, histórico, pregões guardados, planilhas) vai para o **Google
  Drive do responsável** (decisão 2026-10-15: nada da conta do cliente) como
  `Pregoes_backup_AAAA-MM-DD.zip`, na pasta `Pregoes-servidor`; 30 dias.
- **Acesso mínimo**: escopo `drive.file` — o servidor só enxerga o que ele mesmo
  criou no Drive. A ligação `drive-backup` é fixada (`root_folder_id`) na pasta
  `Pregoes-servidor`, criada pelo rclone; ela pode ser movida no Drive. Sem a
  pasta fixada o script se recusa a rodar.
- **Autorizar (uma vez, no computador do responsável)**: `ssh -t … "sudo -u
  pregoes -H rclone config reconnect drive-backup:"` → responder `n` → rodar no
  próprio PC o `rclone authorize "drive" "…"` que aparecer → aceitar com a conta
  Google → colar o código de volta no ssh.
- **Tela**: Configurações → Backup mostra a data do último envio (alerta com
  mais de 2 dias) e tem "Fazer backup agora".
- **Restaurar**: baixar o zip do dia no Drive → `systemctl stop pregoes` →
  extrair em `/home/pregoes/pregoes` (sobrescreve `data/`) → `chown -R
  pregoes:pregoes` → `systemctl start pregoes`. Numa VPS nova: antes, rodar o
  `deploy/instalar_servidor.sh` e religar o túnel (Fase 3).

## Operação (depois de validado)

- **Atualizar:** enviar o código e `systemctl restart pregoes` (vira script
  `deploy/atualizar.sh` quando o fluxo estabilizar).
- **Mudar de VPS / cópia dos dados:** `deploy/migrar_servidor.sh` — ver `MIGRACAO_SERVIDOR.md`.
- **Logs:** `journalctl -u pregoes -f` e `data/pregoes.log`, acessíveis por SSH
  de qualquer lugar.

## Riscos

| Risco | Mitigação |
|---|---|
| Fornecedor bloqueia o IP da VPS | Fase 1 testa antes de qualquer outra coisa; reembolso em 30 dias |
| 1 vCPU lento com dois operadores buscando | Medir na Fase 5; o KVM 2 (2 vCPU / 8 GB) é troca no painel |
| Dados do cliente em servidor de terceiros | Decidido (Q19); só e-mails autorizados entram; porta do app fechada |
| VPS fora do ar | `.exe` segue instalado no cliente durante a validação |
