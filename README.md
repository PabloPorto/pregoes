# Pregões — Sistema de Automação de Formação de Preços

Sistema para automatizar a parte mais chata da preparação de pregões:
extrair os itens do edital em PDF e gerar a planilha matriz preenchida.

## O que esta versão entrega

**Da primeira entrega (Fase 1):**
- ✅ Você arrasta o **PDF do edital** para o sistema
- ✅ Ele detecta a tabela de itens automaticamente e extrai todos
- ✅ Você confere e edita o que quiser numa tabela na tela
- ✅ Ele gera o **arquivo Excel da matriz**, no mesmo formato que você já usa,
   com todas as suas fórmulas preservadas

**Em andamento (Fase 2):**
- ✅ Busca automática de preços na **Almeida** com seleção de marca pelo operador
- ✅ Tela de **Configurações** para você guardar a senha do site
- 🟡 Busca automática na **Dantas** (em andamento, próxima entrega)
- ⏳ Memória dos produtos já vistos antes (cache de 2 dias) — chega junto com a Dantas

## O que vem depois (próximas entregas)

- ⏳ Bootstrap do histórico (importar seus pregões antigos para o catálogo)
- ⏳ Inteligência de impostos baseada nas notas fiscais que você recebe
- ⏳ Painel de variação de preços por marca

## Como usar (3 passos)

### 1. Instalar (uma única vez)
- Tenha o Python instalado (versão 3.11 ou superior). Se não tiver,
  baixe em https://www.python.org/downloads/ e marque a opção
  **"Add Python to PATH"** durante a instalação.
- Dê dois cliques em **`setup.bat`**. Vai demorar 2-3 minutos
  na primeira vez. Aguarde.

### 2. Iniciar o sistema (sempre que for usar)
- Dê dois cliques em **`iniciar.bat`**.
- Vai aparecer uma janela preta com texto. **Não feche essa janela.**
  Ela é o "motor" do sistema rodando.
- O navegador (Chrome / Edge / Firefox) abre automaticamente no sistema.

### 3. Configurar senhas dos fornecedores (uma única vez)
- Clique em **"Configurações"**.
- Preencha seu **e-mail e senha da Almeida** e clique em "Salvar credenciais".
- A senha fica salva só no seu computador, em `data/credenciais.json` —
  nada vai para a internet.

### 4. Usar
- Clique em **"Novo pregão"**.
- Arraste o PDF do edital para a área indicada.
- Preencha o nome do órgão e número do pregão (opcional).
- Clique em **"Extrair itens do PDF"**.
- Veja o **selo de confiança** no topo: 🟢 verde (≥95%) significa
  extração limpa, 🟡 amarelo precisa atenção, 🔴 vermelho precisa
  conferência cuidadosa ou mapeamento manual.
- Se aparecer o botão **"🛠️ Mapeamento Manual"**, clique para
  indicar manualmente qual é a tabela de itens e o que é cada coluna.
  O sistema **aprende** o formato e usa automaticamente em próximos
  editais do mesmo órgão.
- Confira a tabela. Edite o que quiser. Desmarque o quadradinho "OK?"
  para descartar uma linha.
- Ajuste o **markup** se for o caso (padrão 1.45).
- Clique em **"🔍 Buscar preços"** para o robô pesquisar na Almeida
  (ou em **"📊 Gerar sem buscar"** para gerar a planilha em branco
  e preencher preços à mão como antes).
- Quando a busca terminar, escolha a **marca** desejada para cada item
  e clique em **"Gerar planilha matriz com preços"**.
- Clique em **"Baixar planilha matriz"** para salvar o Excel.

### 5. Para encerrar
- Apenas feche a janela preta. Pode fechar o navegador normalmente.

## Onde os arquivos ficam

Tudo dentro da pasta `data/`:
- `data/editais/` — os PDFs que você subiu
- `data/exports/` — as planilhas Excel geradas
- `data/templates/matriz_template.xlsx` — o template oficial. Não mexa.
- `data/pregoes.db` — o "cérebro" do sistema. Não mexa, mas faça
  backup dele de vez em quando (basta copiar para o OneDrive / Drive).

## Algo deu errado?

| Problema | O que fazer |
|---|---|
| "Python não encontrado" no setup.bat | Instale o Python conforme passo 1 |
| Navegador não abre sozinho | Abra manualmente: http://localhost:8501 |
| PDF não extrai nenhum item | Provavelmente é PDF escaneado. Avise o time técnico — vamos adicionar OCR |
| Sistema travou | Feche a janela preta e abra o iniciar.bat de novo. Seus dados estão seguros |
| Outro problema | Tire um print da janela preta e da tela e nos envie |

## Limitações conhecidas desta versão

- Suporta editais com **até 500 itens** com fórmulas pré-preparadas;
  acima disso, o sistema expande automaticamente o template (testar com
  cuidado nos primeiros usos).
- A busca de preços hoje é **só na Almeida**. Dantas vem na próxima entrega.
- Os percentuais de **ST**, **IPI** e **FRETE** seguem em branco
  (você preenche manualmente por enquanto, como já faz hoje).
- Sem credenciais Almeida configuradas, o botão **"Buscar preços"** fica
  indisponível — use **"Gerar sem buscar"** para a planilha em branco.
