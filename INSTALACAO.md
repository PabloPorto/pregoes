# Checklist de instalação na máquina do cliente

> Para quem instala (você, não o cliente). O processo é manual por enquanto —
> o auto-seed está registrado como débito técnico (QUESTOES_PENDENTES.md).
> A regra de ouro: **`data\` do cliente é sagrada** — ela acumula o catálogo,
> o aprendizado e o histórico; sobrescrevê-la é perda irreversível.

## 0. Antes de sair de casa (no Mac/Windows de build)

- [ ] Suíte verde: `python -m pytest tests/ -q`.
- [ ] Build do `.exe` no Windows: `build_exe.bat` (ele aborta sozinho se o
      Chromium não baixar — não prossiga sem o `=== PRONTO ===`).
- [ ] Smoke test no Windows de build: dois cliques no `Pregoes.exe`, conferir
      a **versão na sidebar** (deve bater com `config.VERSAO_APP`), fazer o
      fluxo PDF sintético → busca (pode falhar sem credencial, ok) → fechar.
- [ ] Seed novo: `python scripts/gerar_seed.py` (gera `data/pregoes_seed.db`
      a partir do banco real — catálogo + aprendizado, sem pregões antigos).
- [ ] Levar: pasta `dist\Pregoes\` + `pregoes_seed.db` (pendrive).

## 1. Instalação NOVA (máquina sem o sistema)

- [ ] Escolher a pasta: **`C:\Pregoes`** (criar). NUNCA em `Program Files`
      (sem permissão de escrita — o app grava `data\` ao lado do exe) nem em
      pasta sincronizada (OneDrive/Dropbox/Desktop sincronizado — o sync
      corrompe SQLite aberto).
- [ ] Copiar o CONTEÚDO de `dist\Pregoes\` para `C:\Pregoes\`.
- [ ] Copiar `pregoes_seed.db` para `C:\Pregoes\data\pregoes.db`
      (criar a pasta `data\` se não existir). **Só em instalação nova** — se
      já existe um `pregoes.db`, você está no fluxo errado: vá para a seção 2.
- [ ] Dois cliques em `Pregoes.exe`. Primeira abertura demora mais (extração).
- [ ] Na tela **Configurações**: e-mail/senha da Almeida, da Dantas e a chave
      do Gemini. Conferir os três selos verdes "Configurada" na sidebar.
- [ ] Verificação (5 min): enviar um PDF de edital real pequeno → revisão →
      buscar preços de alguns itens → conferir que aparecem opções com o selo
      "✓ no seu catálogo" (prova de que o seed entrou) → gerar a planilha →
      abrir no Excel e conferir as fórmulas da MATRIZ.
- [ ] Conferir que `C:\Pregoes\data\backups\` ganhou o backup diário do boot.
- [ ] Atalho do `Pregoes.exe` na área de trabalho dos DOIS operadores.

## 2. ATUALIZAÇÃO (máquina que já tem o sistema)

- [ ] **Fechar o app** (janela do navegador E o console preto, se houver).
- [ ] **Backup externo antes de tudo**: copiar a pasta `C:\Pregoes\data\`
      inteira para o pendrive (ou zipar com a data no nome).
- [ ] Substituir os arquivos de PROGRAMA: apagar tudo em `C:\Pregoes\`
      **EXCETO a pasta `data\`**, e copiar o conteúdo novo de `dist\Pregoes\`.
      **NUNCA copiar `data\` do pendrive por cima** — o `pregoes.db` do
      cliente tem mais aprendizado que o seu seed.
- [ ] Abrir o app e conferir na sidebar que a **versão subiu**.
- [ ] Migração de banco (se houver) roda sozinha no boot, com backup
      automático `pre-migration` antes. Se o app não abrir, ver seção 3.
- [ ] Verificação rápida: histórico de pregões continua lá; uma busca de
      teste funciona.

## 3. Restauração de emergência (banco corrompido/perdido)

- [ ] Fechar o app.
- [ ] Em `C:\Pregoes\data\backups\`, pegar o `.db` **mais recente** (nome
      `pregoes_AAAAMMDD_HHMMSS_*.db` — prefira `diario`; os `pre-migration`
      são redes de segurança mais antigas).
- [ ] Copiar por cima de `C:\Pregoes\data\pregoes.db` (apagar antes os
      arquivos `pregoes.db-wal` e `pregoes.db-shm`, se existirem).
- [ ] Abrir o app e conferir o histórico.
- [ ] Se a pasta `backups\` também se perdeu: usar o backup externo do último
      pendrive (por isso o passo de backup na seção 2 não é opcional).

## 4. O que NUNCA fazer (colar na parede)

- ❌ Apagar ou "limpar" a pasta `data\` — é o cérebro acumulado do sistema.
- ❌ Instalar/mover para OneDrive, Dropbox ou Program Files.
- ❌ Copiar um `pregoes.db` antigo por cima do atual ("downgrade" de
      aprendizado).
- ❌ Editar o `matriz_template.xlsx` em `data\templates\` (existe o
      `matriz_template.ORIGINAL.xlsx` de referência se acontecer).
- ❌ Abrir o `pregoes.db` no Excel/DB Browser com o app rodando.

## 5. Suporte remoto

Quando o cliente reportar problema, pedir:
1. A **versão** que aparece na sidebar (ex.: "v1.0.0").
2. O arquivo `C:\Pregoes\data\pregoes.log` (e os `.log.1/.2/.3` se existirem).
3. Print da tela com a mensagem de erro.
