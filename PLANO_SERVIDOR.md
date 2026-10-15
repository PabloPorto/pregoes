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
- 🤖 **Scraper multiusuário** (1–2 dias): uma thread dona dos navegadores + fila,
  para dois operadores ao mesmo tempo sem relogin em excesso e sem um "Novo
  pregão" fechar o navegador do outro.

### Fase 3 — Acesso para o cliente (👤 ~30 min + 🤖 ~1 h)

1. 👤 Registrar o domínio no registro.br; criar conta grátis na Cloudflare e
   apontar o domínio para ela (trocar os DNS no registro.br).
2. 👤 Me passar os e-mails dos operadores.
3. 🤖 Criar o túnel (`cloudflared` no servidor → `http://127.0.0.1:8501`) e o
   Access (só esses e-mails, login por código, sessão de 7 dias).

**Critério:** `https://pregoes.<dominio>` pede o código; e-mail fora da lista é recusado.

### Fase 4 — Dados do cliente (👤🤖, sem tocar na máquina dele)

1. 👤 Pedir ao cliente para compartilhar o **`Pregoes_backup.zip`** do Google
   Drive dele (o app grava esse backup completo: banco + credenciais +
   histórico). Se o backup externo nunca foi configurado, alguém no escritório
   envia a pasta `data\` zipada.
2. 🤖 Restaurar no servidor, conferir, liberar o link para o cliente.
3. O `.exe` continua instalado no cliente durante a validação (plano de volta).

### Fase 5 — Uso real acompanhado (cliente, restante do mês)

- [ ] Os dois operadores entram pelo link, cada um no seu PC.
- [ ] Busca em lote num PC enquanto o outro faz busca manual.
- [ ] Gerar e baixar a planilha (fórmulas preservadas).
- [ ] Nenhuma queda/lentidão relevante na semana.

**Validou →** contratar por período maior, ligar backup diário off-site
(rclone → Google Drive) e atualizar o CLAUDE.md (§7 "Onde roda").

## Operação (depois de validado)

- **Atualizar:** enviar o código e `systemctl restart pregoes` (vira script
  `deploy/atualizar.sh` quando o fluxo estabilizar).
- **Logs:** `journalctl -u pregoes -f` e `data/pregoes.log`, acessíveis por SSH
  de qualquer lugar.

## Riscos

| Risco | Mitigação |
|---|---|
| Fornecedor bloqueia o IP da VPS | Fase 1 testa antes de qualquer outra coisa; reembolso em 30 dias |
| 1 vCPU lento com dois operadores buscando | Medir na Fase 5; o KVM 2 (2 vCPU / 8 GB) é troca no painel |
| Dados do cliente em servidor de terceiros | Decidido (Q19); só e-mails autorizados entram; porta do app fechada |
| VPS fora do ar | `.exe` segue instalado no cliente durante a validação |
