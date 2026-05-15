# Pregões — Sistema de Automação de Formação de Preços

Sistema para automatizar a parte mais chata da preparação de pregões:
extrair os itens do edital em PDF e gerar a planilha matriz preenchida.

## O que esta versão entrega (MVP — primeira parte)

- ✅ Você arrasta o **PDF do edital** para o sistema
- ✅ Ele detecta a tabela de itens automaticamente e extrai todos
- ✅ Você confere e edita o que quiser numa tabela na tela
- ✅ Ele gera o **arquivo Excel da matriz**, no mesmo formato que você já usa,
   com todas as suas fórmulas preservadas

## O que vem depois (próximas entregas)

- ⏳ Consulta automática de preços em **Almeida** e **Dantas**
- ⏳ Memória dos produtos já vistos antes (não pesquisa duas vezes a mesma coisa)
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

### 3. Usar
- Clique em **"Novo pregão"**.
- Arraste o PDF do edital para a área indicada.
- Preencha o nome do órgão e número do pregão (opcional).
- Clique em **"Extrair itens do PDF"**.
- Confira a tabela. Edite o que quiser. Desmarque o quadradinho "OK?"
  para descartar uma linha.
- Clique em **"Gerar planilha matriz"**.
- Clique em **"Baixar planilha matriz"** para salvar o Excel.

### 4. Para encerrar
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
- A coluna **PREÇO** (L), **CÓDIGO INTERNO** (T) e **MARCA** (U) ainda
  ficam em branco — vai ser preenchido pelo robô das próximas entregas.
- Os percentuais de **ST**, **IPI** e **FRETE** seguem em branco também
  (você preenche manualmente por enquanto, como já faz hoje).
