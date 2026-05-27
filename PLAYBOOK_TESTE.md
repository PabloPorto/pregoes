# Playbook de testes — UI Pregões

Guia passo a passo para validar o sistema fluxo-a-fluxo. Cada cenário
indica o que esperar em cada tela; se algo divergir, é bug.

## Setup (uma vez)

```bash
cd /Users/pabloporto/Downloads/pregoes_app
source .venv/bin/activate
python -m streamlit run pregoes/ui/app.py
```

Streamlit sobe em `http://localhost:8501`. Abra no navegador.

> **PDFs de teste**: copie os 5 PDFs originais para `/tmp/editais_amostra/`
> (já estão lá se o `inspecionar_dantas.py` foi rodado nesta sessão).
> Caso contrário, refaça o `cp` dos PDFs em `~/Downloads/`.

---

## Cenário 1 — Caminho feliz (PDF 04 Hidráulicos)

**Objetivo**: validar que pdfplumber sozinho resolve 99% dos casos.

1. Sidebar → **"Novo pregão"**
2. Faça upload de `/tmp/editais_amostra/04_hidraulicos.pdf`
3. Preencha "Órgão" = `Hidráulicos Teste`
4. Clique **"Extrair itens do PDF"**

**Esperado:**
- ✅ Banner **verde**: `Confiança da extração: 99% — pode seguir.`
- ✅ Métricas: ~162 itens, 1 duvidoso
- ✅ Tabela com descrições, unidades, quantidades preenchidas

5. Clique **"Gerar sem buscar"** (botão da revisão)

**Esperado:**
- ✅ Notificação `✅ Planilha gerada: pregao_..._YYYYMMDD_HHMMSS.xlsx`
- ✅ Botão `⬇️ Baixar planilha matriz` aparece

6. Baixe a planilha e abra no Excel/LibreOffice

**Esperado:**
- ✅ Cabeçalho preenchido (`ORGÃO = Hidráulicos Teste`)
- ✅ Coluna C (descrição), D (unidade), E (quantidade), F (referência) preenchidas
- ✅ Coluna G (preço de venda) com fórmula `=Sn*1.45` funcionando
- ✅ Colunas L, T, U **vazias** (não buscamos preço)

---

## Cenário 2 — Falha honesta + Mapeamento manual rápido (PDF 02 Hash/PE)

**Objetivo**: validar Camada 1 também em formato diferente.

1. Sidebar → **"Novo pregão"**
2. Upload de `/tmp/editais_amostra/02_hash.pdf`
3. Órgão = `Hash PE`
4. **"Extrair itens do PDF"**

**Esperado:**
- ✅ Banner verde: `Confiança 100%` (era 22% duvidosos antes da Etapa 1.0.A)
- ✅ ~66 itens
- ✅ Coluna "Ref. (R$)" preenchida

5. **"Gerar sem buscar"** → baixe → confira no Excel

---

## Cenário 3 — Mapeamento manual com Docling (PDF 03 TRE-PE)

**Objetivo**: validar fluxo completo de mapeamento manual + reconstrução
de fatias Docling + reuso automático em segundo upload.

### Parte A — Primeira vez (operador mapeia)

1. Sidebar → **"Novo pregão"**
2. Upload de `/tmp/editais_amostra/03_tre_pe.pdf`
3. Órgão = `TRE-PE`
4. **"Extrair itens do PDF"**

**Esperado:**
- 🔴 Banner **vermelho**: `Não foi possível identificar uma tabela de itens`
- ✅ Avisos amarelos sugerindo "Mapeamento Manual"
- ✅ Botão **🛠️ Mapeamento Manual** aparece

5. Clique **"Mapeamento Manual"**

**Esperado:**
- ✅ Tela "🛠️ Mapeamento Manual da Tabela de Itens"
- ✅ Accordion `🔬 Não encontrou a tabela certa? Carregar com extração profunda (Docling)`
- ✅ Passo 1 com selectbox listando tabelas detectadas pelo pdfplumber

6. Expanda o accordion Docling → clique **"▶️ Carregar tabelas com Docling"**

**Esperado:**
- ⏳ Spinner: `Rodando Docling… pode demorar alguns minutos.`
- ⏳ Aguarde **90s-3min** (primeira execução baixa modelos uma vez; depois cacheado)
- ✅ Banner azul: `Mostrando tabelas detectadas pelo Docling (extração profunda).`
- ✅ Selectbox agora lista ~25 tabelas (após reconstrução)

7. Selecione a **Tabela #3 — 52 linhas, 10 cols — Nº Item | Descrição do Item | ...**

**Esperado:**
- ✅ Pré-visualização mostra cabeçalho real + dados de produtos
- ✅ Passo 2 abre selectboxes para 10 colunas

8. Mapeie:
   - Coluna 1 (Nº Item) → **Código (item nº)**
   - Coluna 2 (Descrição do Item) → **Descrição**
   - Coluna 5 (Unidad e) → **Unidade**
   - Coluna 8 (Quant.) → **Quantidade**
   - Coluna 9 (Preço Unitário Máximo) → **Valor de referência**
   - Demais → deixe **— ignorar —**

9. Confirme que **"💾 Salvar este mapeamento"** está marcado
10. Clique **"✅ Aplicar mapeamento"**

**Esperado:**
- ✅ Volta para revisão
- ✅ Banner verde-amarelo: `Confiança ~50%` (alguns duvidosos esperados — fragmentos de descrição que reconstrução absorveu)
- ✅ ~28 itens extraídos

### Parte B — Segunda vez (reuso automático)

11. Sidebar → **"Novo pregão"** (limpa estado)
12. Upload do MESMO `/tmp/editais_amostra/03_tre_pe.pdf`
13. **"Extrair itens do PDF"**

**Esperado:**
- ✅ Aviso azul: `✨ Mapa aprendido reusado (tabela #X, cabeçalho conhecido).`
- ✅ Confiança alta automaticamente — sem precisar entrar no mapeamento manual
- ✅ ~28 itens aparecem direto na revisão

---

## Cenário 4 — Busca em ambos fornecedores + escolha do mais barato

**Pré-requisito**: ter credenciais de **Almeida** E **Dantas** configuradas.

### Configurar credenciais (uma vez)

1. Sidebar → **"Configurações"**
2. Aba **Almeida** → preencha e-mail/senha → **"Salvar credenciais"**
3. Aba **Dantas** → clique **"📋 Usar as mesmas credenciais da Almeida"** (cliente confirmou que é a mesma conta)

**Esperado:**
- ✅ Sidebar → "Almeida: Configurada" verde + "Dantas: Configurada" verde

### Fazer uma busca

4. Use o pregão do **Cenário 1** (PDF 04 Hidráulicos, com itens extraídos)
5. Na revisão, defina **Markup = 1.35**
6. Clique **"Buscar preços"**

**Esperado na tela de busca:**
- ✅ Caption: `162 itens serão buscados em Almeida + Dantas`
- ✅ Estimativa de tempo aparece (com paralelismo)
- ✅ Botão **"Iniciar busca"**

7. Clique **"Iniciar busca"**

**Esperado:**
- ⏳ Barra de progresso `1/162 — ADAPTADOR PVC...` → `162/162`
- ⏳ Estimativa: ~5-10min para 162 itens em 2 fornecedores com paralelismo
- ✅ Banner final: `Busca concluída — 162 itens processados (X do cache, Y buscas reais)`
- ✅ Painel `⚠️ Ver erros técnicos (N)` se houver falhas isoladas

8. Veja os cartões expansíveis

**Esperado**:
- ✅ Cada item tem um expander mostrando opções
- ✅ Cada opção tem `MARCA — R$ XX,XX (almeida|dantas) 💰 mais barato — código N — descrição...`
- ✅ **Opção pré-selecionada = a de menor preço** entre Almeida + Dantas
- ✅ Selectbox tem label "Marca / preço (default = mais barato)"

9. Confira no caption final: `X de 162 itens com marca/preço selecionados`

10. Clique **"Gerar planilha matriz com preços"**

**Esperado:**
- ✅ Planilha gerada com coluna **L = menor preço**, **T = código do fornecedor mais barato**, **U = marca**
- ✅ Coluna G (preço de venda) = L × 1.35 (markup escolhido)

---

## Cenário 5 — Busca com 1 fornecedor caindo (resiliência)

**Objetivo**: validar que erros isolados não quebram o batch.

1. Vá em **Configurações** → aba **Dantas**
2. Altere a senha pra um valor INVÁLIDO (ex: `senha_errada_123`)
3. **"Salvar credenciais"**
4. Repita o **Cenário 4** (busca em ambos)

**Esperado:**
- ❌ Login Dantas falha
- ✅ Almeida continua e processa todos os 162 itens
- ✅ Banner final mostra erro `⚠️ ~162 erro(s) — ver painel abaixo`
- ✅ Expander **"Ver erros técnicos"** lista falhas com mensagem clara
- ✅ Planilha gerada COM preços do Almeida (Dantas vazio onde falhou)

5. Conserte a senha do Dantas
6. Repita a busca → tudo verde de novo

---

## Cenário 6 — Cache de preço (TTL 2 dias)

**Objetivo**: validar que segundo upload do mesmo edital reusa cache.

1. Use o resultado do **Cenário 4** (162 itens buscados)
2. Sidebar → **"Novo pregão"** → upload do mesmo PDF Hidráulicos
3. **"Extrair itens do PDF"** → revisão → **"Buscar preços"** → **"Iniciar busca"**

**Esperado:**
- ⚡ Muito mais rápido que a primeira vez (~1-2min vs 5-10min)
- ✅ Banner: `... (160 do cache, 2 buscas reais)` (ou similar — alguns itens podem ter mudado)

---

## Quick smoke test (3 min — só pra confirmar que sobe)

Se quiser apenas validar que a UI **sobe sem erro** sem fazer fluxo completo:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -c "from pregoes.ui import app"  # importa sem rodar
.venv/bin/python -m streamlit run pregoes/ui/app.py &
sleep 5
curl -s http://localhost:8501 | head -5  # deve retornar HTML
```

Espera: 106 testes verde + import sem erro + curl retornando HTML.

---

## O que reportar se algo der errado

Para cada falha, anote:

1. **Cenário e passo** (ex: `Cenário 3, passo 7`)
2. **O que aconteceu** vs o esperado
3. **Mensagem de erro completa** se houver
4. **Console do navegador** (F12 → Console) se a página travar
5. **Stacktrace no terminal** onde o Streamlit roda

Encaminhe isso e eu reproduzo + corrijo.
