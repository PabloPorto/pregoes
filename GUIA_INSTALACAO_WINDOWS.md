# Guia detalhado de instalação no Windows

> Guia passo a passo, do zero, para **você** (intermediário técnico) levar o
> sistema a uma máquina Windows do cliente. É a versão narrada e completa;
> `INSTALACAO.md` é o checklist enxuto do mesmo processo, e `BUILD.md` é a
> referência técnica do empacotamento.
>
> **Regra de ouro, vale para tudo abaixo:** a pasta `data\` guarda o cérebro
> acumulado do sistema (catálogo, aprendizado, histórico de preços). Apagá-la
> ou sobrescrevê-la é **perda irreversível**. Quando em dúvida, faça backup
> dela antes.

O processo tem **duas máquinas e dois momentos**:

- **Máquina de BUILD** (seu Windows): onde você gera o `Pregoes.exe`. Precisa
  de Python e internet. Você faz isso UMA vez por versão.
- **Máquina do CLIENTE**: onde o app roda. **Não** precisa de Python nem de
  internet para instalar — só recebe a pasta pronta.

---

## Parte A — Preparar o pacote (na máquina de BUILD)

### A1. Instalar o Python 3.11+ (uma vez na vida da máquina de build)

1. Baixe em <https://www.python.org/downloads/windows/> a versão 3.11 ou mais
   nova (64-bit).
2. No instalador, **na primeira tela, marque "Add python.exe to PATH"** (sem
   isso, os comandos abaixo não funcionam).
3. Conclua, abra um **novo** Prompt de Comando (tecla Windows → digite `cmd`)
   e confirme:
   ```bat
   py -3 --version
   ```
   Deve aparecer `Python 3.11.x` (ou maior). Se disser "não reconhecido",
   reabra o Prompt ou reinstale marcando o PATH.

### A2. Obter o código do projeto

Copie a pasta do projeto (`pregoes_app`) para a máquina de build — via Git
(`git clone <repo>`) ou pendrive. Abra o Prompt de Comando **dentro** dela:
```bat
cd C:\caminho\para\pregoes_app
```

### A3. Conferir que a suíte está verde (sanidade)

```bat
py -3 -m venv .env
.env\Scripts\activate.bat
pip install -r requirements.txt
python -m pytest tests\ -q
```
Espere `XXX passed` (hoje: **433 passed**). Se algo falhar, **pare** — não
empacote uma versão quebrada. (Este passo é opcional se você já roda os testes
no Mac, mas é a rede de segurança final.)

### A4. Gerar o "seed" do banco (os dados que o cliente herda)

O seed é o catálogo + aprendizado + histórico, **sem** os pregões antigos.
Ele faz a instalação nova "nascer esperta".

```bat
python scripts\gerar_seed.py
```
Saída esperada: `Seed gerado: ...\data\pregoes_seed.db` com as contagens
(produtos, aprendizados, etc.). Esse arquivo **contém dados do cliente** — é
gitignored de propósito; trate como confidencial.

> Se você gera o seed no **Mac** de desenvolvimento, o comando é o mesmo
> (`python scripts/gerar_seed.py`); leve o `data/pregoes_seed.db` resultante.

### A5. Construir o executável

```bat
build_exe.bat
```
O script faz tudo sozinho: cria um ambiente isolado, instala as dependências,
**baixa o Chromium** do Playwright e roda o PyInstaller. Acompanhe o console:

- Deve aparecer `[spec] Chromium do Playwright empacotado de: ...`.
- Se o download do Chromium falhar, o build **aborta de propósito** (sem ele a
  busca de preços não funcionaria) — resolva a internet/proxy e rode de novo.
- No fim: `=== PRONTO ===` e o caminho `dist\Pregoes\Pregoes.exe`.

> A pasta `dist\Pregoes\` sai grande (centenas de MB: Streamlit + pandas +
> Chromium). É normal.

### A6. Smoke test do `.exe` na própria máquina de build

**Não pule este passo** — é a única validação real antes do cliente.

1. Dois cliques em `dist\Pregoes\Pregoes.exe`. O navegador abre em
   `http://localhost:8501`. (Uma janela preta de console pode aparecer junto —
   é esperado nesta versão de piloto; serve para ver erros.)
2. Confira a **versão na barra lateral** (ex.: "v1.0.0").
3. Em **Configurações**, coloque uma chave Gemini de teste e credenciais (pode
   ser as reais) e veja os selos "Configurada".
4. **Busca ao vivo (o teste que mais importa):** processe um PDF de edital e
   mande buscar alguns itens — confirme que o site do fornecedor abre e voltam
   preços. Se der "Executable doesn't exist", o Chromium não foi empacotado
   (ver `BUILD.md` → seção Playwright).
5. Gere a planilha e abra no Excel: as fórmulas da MATRIZ devem estar lá.
6. Confirme que apareceram `dist\Pregoes\data\pregoes.db` e
   `dist\Pregoes\data\backups\` (dados ao lado do exe, não em pasta temporária).

### A7. Montar o pacote de entrega

Leve para a máquina do cliente (pendrive ou rede):
- a pasta **`dist\Pregoes\`** inteira;
- o arquivo **`pregoes_seed.db`** (de A4) — separado, fora da pasta.

---

## Parte B — Instalar na máquina do CLIENTE (instalação NOVA)

> "Instalação nova" = a máquina nunca teve o sistema. Se já existe um
> `pregoes.db`, **não é instalação nova** — vá para a Parte C (Atualização).

### B1. Escolher o lugar certo

Crie a pasta **`C:\Pregoes`** e copie para dentro dela **todo o conteúdo** de
`dist\Pregoes\`.

- ❌ **Nunca** em `C:\Program Files\...` — o Windows bloqueia escrita lá e o app
  precisa gravar a pasta `data\` ao lado do exe.
- ❌ **Nunca** dentro de OneDrive / Dropbox / Desktop sincronizado — o
  sincronizador corrompe o banco SQLite enquanto ele está aberto.
- ✅ `C:\Pregoes` (ou outra pasta local simples, fora de sincronização).

### B2. Semear o banco (só na instalação nova)

1. Dentro de `C:\Pregoes`, crie a pasta `data` se ela ainda não existir.
2. Copie o `pregoes_seed.db` para lá **renomeando para `pregoes.db`**:
   `C:\Pregoes\data\pregoes.db`.

> ⚠️ Faça isso **antes** do primeiro boot e **apenas** se não existir um
> `pregoes.db`. Se já existir, pare: você está no fluxo de atualização.

### B3. Primeira execução

Dois cliques em `C:\Pregoes\Pregoes.exe`. A **primeira** abertura é mais lenta
(o app se prepara). O navegador abre na tela inicial.

### B4. Configurar credenciais

Vá em **Configurações** e preencha:
- **Almeida**: e-mail e senha do portal.
- **Dantas**: e-mail e senha do portal.
- **IA (Gemini)**: a chave de API.

Confirme os **três selos verdes "Configurada"** na barra lateral. Tudo fica
salvo só nessa máquina (arquivo local), sem envio para fora.

### B5. Verificação pós-instalação (5 minutos)

1. Envie um PDF de edital real pequeno → tela de Revisão mostra os itens.
2. "Buscar preços" em alguns itens → devem aparecer opções; procure o selo
   roxo **"✓ no seu catálogo"** — é a prova de que o seed entrou.
3. Gere a planilha → abra no Excel → confira as fórmulas da MATRIZ.
4. Confirme que `C:\Pregoes\data\backups\` ganhou o backup diário do boot.

### B6. Atalhos

Crie um atalho de `C:\Pregoes\Pregoes.exe` na área de trabalho dos **dois**
operadores.

---

## Parte C — Atualizar uma máquina que já roda o sistema

> O objetivo é trocar o **programa** sem encostar nos **dados** do cliente.

1. **Feche o app**: a janela do navegador **e** a janela preta de console (se
   houver).
2. **Backup externo primeiro** (inegociável): copie a pasta inteira
   `C:\Pregoes\data\` para um pendrive (ou zipe com a data no nome).
3. **Troque só o programa**: apague tudo dentro de `C:\Pregoes\`
   **EXCETO a pasta `data\`**, e copie o conteúdo novo de `dist\Pregoes\`.
   - ❌ **Nunca** copie um `data\` ou `pregoes.db` antigo por cima — o banco do
     cliente tem mais aprendizado que o seu seed.
4. Abra o app e confirme que a **versão subiu** na barra lateral.
5. Se houver evolução de banco, a migração roda sozinha no boot, com um backup
   `pre-migration` automático antes. Se o app não abrir, vá para a Parte D.
6. Verificação rápida: o histórico de pregões continua lá e uma busca de teste
   funciona.

---

## Parte D — Restauração de emergência (banco corrompido ou perdido)

1. Feche o app.
2. Em `C:\Pregoes\data\backups\`, escolha o `.db` **mais recente** (nome
   `pregoes_AAAAMMDD_HHMMSS_*.db` — prefira os `diario`; os `pre-migration` são
   redes de segurança mais antigas).
3. Apague `C:\Pregoes\data\pregoes.db` e, se existirem, `pregoes.db-wal` e
   `pregoes.db-shm`.
4. Copie o backup escolhido para `C:\Pregoes\data\pregoes.db`.
5. Abra o app e confira o histórico.
6. Se a pasta `backups\` também se perdeu: use o backup externo do último
   pendrive (Parte C, passo 2 — por isso ele não é opcional).

---

## Parte E — Solução de problemas

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| App não abre / fecha na hora | Pasta em Program Files/OneDrive | Mover a pasta para `C:\Pregoes` |
| Busca não traz nada de nenhum item | Chromium ausente no `.exe` | Rebuild com Chromium (`BUILD.md`) |
| "Executable doesn't exist" | idem | idem |
| Selos "Configurada" não aparecem | Credenciais/chave não salvas | Reabrir Configurações e salvar |
| Itens não acham "no seu catálogo" | Seed não foi semeado (Parte B2) | Conferir `data\pregoes.db`; reimportar planilhas em Configurações |
| Janela preta junto do app | `console=True` (piloto) | Normal; será removido na versão final |
| App lento na 1ª abertura | Extração do `.exe` | Normal só na primeira vez |
| Erro técnico em inglês na tela | Caso não tratado | Pegar `data\pregoes.log` e o print (ver Parte F) |

---

## Parte F — Suporte remoto

Quando o cliente reportar um problema, peça os três:
1. A **versão** na barra lateral (ex.: "v1.0.0").
2. O arquivo **`C:\Pregoes\data\pregoes.log`** (e os `pregoes.log.1/.2/.3` se
   existirem) — ele tem o histórico de erros, sem stacktrace cru na tela do
   operador.
3. Um **print** da tela com a mensagem.

---

## O que NUNCA fazer (resumo para colar na parede)

- ❌ Apagar ou "limpar" a pasta `data\`.
- ❌ Instalar/mover para Program Files, OneDrive ou Dropbox.
- ❌ Copiar um `pregoes.db` antigo por cima do atual.
- ❌ Editar o `matriz_template.xlsx` em `data\templates\`.
- ❌ Abrir o `pregoes.db` no Excel/DB Browser com o app rodando.
- ❌ Entregar um `.exe` sem ter feito o smoke test (Parte A6).
