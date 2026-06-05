# Questões pendentes para o cliente final

> Este arquivo é vivo. Cada item resolvido sai daqui e entra como decisão registrada na arquitetura.

## STATUS 2026-06 — o que mudou desde o briefing

- **Q11/Q12 (LLM + busca em 5 camadas): IMPLEMENTADAS e em produção.** O
  Gemini deixou de ser "fallback de extração" e virou **componente central**:
  extração (Camada 2) + canonização do catálogo (Fase B) + judge da busca.
- **Billing pago aprovado.** Custo real **medido**: ~$1-2/mês no volume alvo
  (chamada do judge = 641 tokens ≈ US$0,00006). O free tier (20 req/dia) era
  insuficiente; o tier pago resolve. Substitui a estimativa antiga de "$15/mês".
- **Catálogo populado:** 7 planilhas reais importadas → 1408 produtos (880
  Almeida + 123 Dantas), normalizados pelo Gemini.
- **Confiabilidade medida em edital real da especialidade:** busca por código
  ~89-91% certa, ~2% alucinação, 0 nos itens sem correspondência.
- **Prontidão de produção:** guardrails P0 implementados (migration de schema,
  backup automático, log em arquivo, mensagens de erro para leigo, falhas não
  silenciosas, sinalização de PDF escaneado). Ver plano de produção.
- Docling saiu do `requirements.txt` (→ `requirements-dev.txt`); extração
  automática é pdfplumber + Gemini.

## Bloco 1 — Defaults provisórios em uso (precisam confirmação para continuar fase 2 em diante)

### Q12. Busca robusta de produtos: pipeline em 5 camadas (95%+ qualidade)

**Status (2026-05-27).** ✅ **Infraestrutura implementada.** Todas as 5
camadas existem no código:
- Camada 0: `pregoes/matcher.py` + `pregoes/importador.py` +
  `AlmeidaDistribuidor.buscar_por_codigo` + `DantasDistribuidora.buscar_por_codigo`
  — integrados em `buscar_precos_para_itens`.
- Camada 1+2: scraper textual (já existia) + `normalizador.relevancia`
  agora HÍBRIDA (substring/prefix combinada com rapidfuzz `token_sort_ratio`
  via `max(kw, fuzz*0.9)`).
- Camada 3: `extrator_llm.julgar_candidatos` com cache SHA256 TTL infinito
  em `cache_llm_judge`. Dispara apenas quando o item caiu no fallback
  "top 3 variação" do scraper.
- Camada 4: tabela `mapeamentos_aprendidos` + gravação automática quando
  operador finaliza a planilha + consulta no orquestrador antes da
  Camada 0.

**Trigger restante para ativar Camada 0 plenamente.** Cliente entregar
1+ planilha histórica em `data/historico/` e o operador rodar:
```bash
python -m pregoes.importador --fornecedor=almeida data/historico/*.xlsx
python -m pregoes.importador --fornecedor=dantas  data/historico/*.xlsx
```

**Pendente (não bloqueia funcionamento).**
- Tela UI para acionar o importador (hoje é CLI).
- Dashboard de variação de preço por marca/fornecedor (item extra do plano).
- Mostrar `codigo_hits` e `llm_judge_hits` no painel "Busca concluída"
  da UI para o operador entender o que foi resolvido por cada camada
  (campos já existem em `ResultadoLote`).

---

**Contexto original.** Em 2026-05-27 o operador relatou que o mesmo produto
físico aparece em diferentes editais com nomes DIFERENTES, e o scraper
atual erra muitos casos. Causa: matching textual é cego a variações
naturais:

- Abreviações: `"SOLD."` vs `"SOLDÁVEL"`
- Sinônimos: `"CANO"` vs `"TUBO"` vs `"TUBULAÇÃO"`
- Ordem das palavras: `"PVC ADAPTADOR"` vs `"ADAPTADOR PVC"`
- Erros de digitação: `"PARAFUZO"` em vez de `"PARAFUSO"`
- Dimensão variável: `"1.1/2"` vs `"1 1/2"` vs `"1½"` vs `"11/2"`

O fix recente (commit `b18eeb2`) tolera prefix/substring, melhorou
casos típicos como `"sold."` → `"soldavel"`, mas não resolve sinônimos
nem semântica complexa.

**Insumo CRÍTICO disponibilizado pelo operador (2026-05-27):**
- O cliente **armazena código do produto** em suas planilhas de
  precificação histórica.
- Buscar `/busca?s={codigo}` no site Almeida ou Dantas retorna o
  produto EXATO sem ambiguidade.
- Isso **deve ser a PRIORIDADE 1** do pipeline — busca por código
  histórico é determinística, rápida, livre de erro de matching textual.

**Arquitetura proposta** — Pipeline em 5 camadas, do mais barato/preciso
ao mais caro:

```
Item do edital (descrição + opcional valor de referência)
   │
   ▼
[Camada 0: catálogo histórico]
   │  Importador lê N planilhas antigas → produtos (desc canônica + codigo_almeida + codigo_dantas)
   │  Matcher fuzzy (rapidfuzz token_sort_ratio >= 90) busca em `produtos`
   │  Se match alto: extrai códigos → busca DIRETA por código no site
   │  → retorna produto exato sem precisar disputar relevância textual
   │
   ├─ acertou? → entrega (variacao se score < 95, exato se >= 95)
   │
   ▼
[Camada 1: match textual aprimorado] (já implementado)
   │  Substring/prefix matching (commit b18eeb2)
   │  Resolve abreviações triviais: "sold." ↔ "soldavel", "1.1/2" ↔ "50x1.1/2"
   │
   ├─ acertou com relevância >= 0.6? → entrega
   │
   ▼
[Camada 2: rapidfuzz scoring]
   │  token_sort_ratio entre descrição do edital e cada candidato
   │  Threshold ~75 (ajustável)
   │  Resolve: ordem das palavras, erros de digitação leves
   │
   ├─ acertou com score >= 75? → entrega
   │
   ▼
[Camada 3: LLM judge (Gemini)]
   │  Manda top-N candidatos pro Gemini com prompt:
   │  "Item do edital: {X}. Estes candidatos correspondem? Devolva os IDs em
   │  ordem de melhor match, ou [] se nenhum corresponde."
   │  Cache por chave semântica (hash da descrição normalizada + cod candidatos)
   │  Custo: ~$0.001/item ANTES do cache; $0 depois
   │
   ├─ Gemini retornou correspondências? → entrega como 'variacao'
   │
   ▼
[Camada 4: aprendizado via operador]
   │  Operador clica em "trocar marca" / "não é esse"
   │  Sistema grava o "ground truth" (descricao_edital → produto_correto)
   │  Próxima busca da mesma descrição (ou similar via fuzzy) usa essa marca
```

**Cobertura esperada:**
- Camada 0 sozinha: ~70% dos itens em editais recorrentes (assumindo
  catálogo bem populado, ≥1000 produtos históricos)
- Camadas 0+1+2: ~85-90%
- Camadas 0+1+2+3: ~95-98%
- Camada 4 fecha o resto com o tempo

**Plano de implementação** (4 etapas, ~3-4 dias totais):

1. **Etapa 3.1 (1-2 dias) — Camada 0: catálogo + matcher + busca por código**
   - `pregoes/importador.py`: CLI lê N planilhas antigas, popula
     `produtos` (descricao_canonica, codigo_almeida, codigo_dantas, marca,
     unidade_padrao, ncm)
   - `pregoes/matcher.py`: `rapidfuzz.process.extractOne(descricao_edital,
     produtos_choices, scorer=token_sort_ratio)`; threshold 90
   - `AlmeidaDistribuidor.buscar_por_codigo(codigo)`: nova função, faz
     `/busca?s={codigo}` e devolve produto exato
   - `DantasDistribuidora.buscar_por_codigo(codigo)`: idem
   - `buscar_precos_para_itens`: na primeira passada, tenta matcher;
     se acha código, usa `buscar_por_codigo`; só cai pra texto se falhar

2. **Etapa 3.2 (~0.5 dia) — Camada 2: rapidfuzz no scoring textual**
   - Substituir `normalizador.relevancia()` por híbrido:
     - 60%: score atual (substring/prefix)
     - 40%: `fuzz.token_sort_ratio / 100`
   - Threshold combinado mantém em 0.6
   - Resolve ordem de palavras + erros de digitação leves

3. **Etapa 3.3 (1 dia) — Camada 3: LLM judge sob demanda**
   - Função em `pregoes/extrator_llm.py`:
     `julgar_candidatos(desc_edital, candidatos: list[ResultadoBusca]) -> list[int]`
     Retorna IDs dos candidatos que correspondem, em ordem de melhor match.
   - Disparado APENAS quando Camadas 0+1+2 retornaram fallback "top 3
     variação" (commit b18eeb2). Sinal: confiança < 0.6 em todos.
   - Cache em SQLite com chave SHA256 de `(desc_normalizada, [codigos])`
     → TTL infinito (semântica não muda)
   - Custo real: ~$0.001/chamada × 30% dos itens = ~$0.15 por edital
     de 500 itens. Free tier Gemini cobre.

4. **Etapa 3.4 (~0.5 dia) — Camada 4: aprendizado por feedback**
   - Quando operador troca seleção na UI, grava `mapeamento_aprendido`
     (descricao_edital_normalizada, produto_id, fornecedor, codigo,
     marca, data_aprendizado)
   - Próxima busca de descrição similar (via matcher fuzzy >= 95)
     reusa esse mapeamento direto
   - Decai com o tempo se nenhum operador o reusar (sinal de produto
     descontinuado)

**Pré-requisitos para iniciar:**
- ✅ Gemini integrado (Fase 2.7)
- ✅ rapidfuzz já reservado no requirements.txt (`# rapidfuzz>=3.0.0`)
- 🟡 Planilhas históricas do cliente (Q2) — bloqueia Etapa 3.1
  parcialmente: dá pra estruturar o importador sem dados, mas só testa
  end-to-end quando chegar
- ⚠️ Endpoint Almeida/Dantas para busca por código — confirmado
  empiricamente pelo operador que `/busca?s={codigo}` funciona

**Trigger para começar:**
- Cliente enviar pelo menos 1-2 planilhas históricas, OU
- Operador sinalizar que a busca atual está derrubando muitos editais
  na prática

**Trade-offs aceitos pelo operador (2026-05-27):**
- LLM externo (Gemini) já parte do pipeline (igual à extração)
- Custo recorrente até $15/mês no volume máximo previsto
- Latência adicional de ~2s/item na 1ª busca quando Camada 3 dispara
- +1 dep (rapidfuzz, ~2MB, sem complicação no Windows)

**Riscos / pontos de atenção:**
- Catálogo histórico desatualizado: produtos podem ter sido
  descontinuados; código não funciona. Tratar como erro recuperável:
  cai pra Camada 1+ texto.
- Múltiplos códigos para o mesmo produto (Almeida + Dantas diferentes):
  schema já suporta (`codigo_almeida`, `codigo_dantas`).
- LLM judge pode "alucinar" correspondência: SEMPRE validar com
  match textual de pelo menos um token-chave para evitar.
- Cache do LLM ficar enorme: TTL de N dias OU LRU 10k entradas.

---

### Q11. Camada LLM (Gemini 2.0 Flash) — próxima grande frente de extração

**Contexto.** Diagnóstico com 5 PDFs reais (2026-05-27) mostrou:
- 2 dos 5 editais resolvem 100% automático
- 1 resolve parcial (60%)
- 2 dos 5 exigem mapeamento manual + uso do Docling (5-10min/edital)

Etapa 1.0.D (reconstrução de fatias Docling) trouxe ganho marginal,
mas ainda há **bugs estruturais** observados em uso real:
- Encoding: Docling produz `'DOBRADI˙A'` em vez de `'DOBRADIÇA'` em
  alguns PDFs.
- Multi-página: extrator às vezes fragmenta a tabela em N peças;
  operador mapeava só uma (corrigido em commit 907bbb3, mas é band-aid).
- Falsos positivos: tabelas-lixo (contatos, rubricas, totais) ainda
  contaminam alguns resultados.
- Necessidade de mapeamento manual cresce linearmente com a variedade
  de formatos de edital. **Com 5 amostras, 40% pedem intervenção. Com
  20-50, isso provavelmente vira a regra, não exceção.** Quando o
  trabalho humano por edital cresce, o sistema vira fardo em vez de
  ferramenta.

**Conclusão do operador (2026-05-27)**: a inviabilização do sistema
em volume real é **dor previsível, não hipotética**.

**Default em uso.** Sistema sem LLM. Sistema atual está commitado e
estável; LLM fica como próxima frente prioritária.

**Plano de implementação (ativar logo após Fase 3 ou conforme dor):**

Provedor: **Gemini 2.0 Flash** (Google).
- Free tier cobre folgadamente o volume alvo (10-30 editais/mês usa
  <1% do limite diário gratuito de 1500 req/dia).
- Vision multimodal nativo, estado da arte em tabelas estruturadas.
- API estável (GA desde fev/2025).
- Cliente cria chave gratuita em [aistudio.google.com](https://aistudio.google.com).

Implementação (estimativa 1.5-2 dias):
- `pregoes/extrator_llm.py` (~250 linhas): chamada por página com
  prompt estruturado + parsing JSON validado.
- `pregoes/credenciais.py`: slot para `gemini_api_key`.
- UI: aba "IA" em Configurações + opção na revisão.
- Cache: reusa infra de cache existente (TTL=2d por descrição).
- Posicionamento no pipeline: **Camada 2 automática** quando pdfplumber
  ficar < 95%. Substitui ou complementa Docling.
- Fallback se Gemini falhar (sem internet, rate limit, key inválida):
  cai para Docling/manual.

Ganhos esperados:
- Encoding correto (modelo lê visual, não depende de fonte do PDF).
- Multi-página resolvida (modelo entende contexto).
- ~98-99% acurácia em tabelas BR (testado em casos similares).
- ~30-90s/edital vs ~5-10min do Docling.
- Setup mais leve para o cliente.

Trade-offs aceitos pelo operador:
- Dependência de internet para Camada 2 (Camada 1 continua offline).
- Dados do edital vão para servidor externo do Google — aceitável
  porque editais são públicos por definição.
- Flexibiliza CLAUDE.md §9 ("Não adicione LLM externo") — confirmado
  pelo operador como decisão revisável quando há ganho real.

**Trigger para implementar.** Acende quando UM acontecer:
- Operador reportar fadiga com mapeamento manual após 10+ editais.
- 3+ formatos novos quebrando heurística em sequência.
- Cliente perguntar "por que demora tanto" / "tem como ser mais
  preciso".
- Fase 3 (importador + matcher) concluída — abre janela natural
  para reorganizar pipeline.

---

### Q10. Etapa 1.0.B (PyMuPDF + Camelot) — adiar até benchmark do Docling

**Contexto.** Em 2026-05-27, na proposta de 3 etapas para robustez de
extração (1.0.A score semântico, 1.0.B PyMuPDF+Camelot, 1.0.C Docling +
mapeamento manual), o usuário optou por pular a 1.0.B e ir direto para
1.0.C. A 1.0.A e 1.0.C foram entregues.

**Default em uso.** Pipeline atual tem 3 camadas:
pdfplumber (Camada 1) → Docling visual (Camada 2) → mapeamento manual
com aprendizado (Camada 3). PyMuPDF e Camelot **não** estão integrados.

**Próximo passo necessário.** Após o diagnóstico final dos 5 PDFs reais
com Docling ativo, decidir:
- Se Docling resolve com latência aceitável (< 30s/edital típico) →
  Etapa 1.0.B fica permanentemente fora do escopo. Princípio "não
  inventar abstração antes da dor real".
- Se Docling for lento demais ou deixar buracos → adicionar PyMuPDF
  e Camelot stream como Camada 1.5 (entre pdfplumber e Docling).

**Não bloqueia.** Sistema atual já funciona em 3 dos 5 PDFs de teste;
mapeamento manual cobre os 2 restantes com aprendizado para reuso.

---

### Q2. Bootstrap do catálogo histórico (5.000 produtos mencionados)

**Contexto.** O cliente declarou base mental de ~5.000 produtos acumulados em pregões anteriores.

**Default em uso.** Sistema começa do zero. O importador (`pregoes/importador.py`) ainda não foi escrito — será criado na Fase 3, quando as planilhas históricas chegarem. Sem importador rodado, o catálogo nasce vazio.

**Próximo passo necessário.** O cliente nos passar 10-20 planilhas de pregões anteriores no mesmo formato da `MATRIZ`. Idealmente cobrindo categorias variadas: construção, hidráulico, elétrico.

**Impacto.** Sem isso, os primeiros 3-5 pregões serão lentos — nenhum casamento automático. Com isso, ~3-4 mil produtos casados de cara no primeiro uso.

---

## Bloco 2 — Confirmações fechadas (não precisam mais ação)

| Item | Decisão | Origem |
|---|---|---|
| Fornecedores | almeidadistribuidor.com.br + dantasdistribuidora.com.br, navegação pública | Resp. 1, 2 |
| Anti-bot | Sem proteção visível, scraping autorizado pelos parceiros | Resp. 5, 6 |
| Onde roda | Local na máquina do cliente, Streamlit em localhost:8501 | Resp. 11 + recomendação |
| Operadores | Ele + 1 ajudante, dividem volume, mesmas permissões | Resp. 3, 12 |
| Stack | 100% local, grátis, offline-first | Resp. 13 |
| LGPD | Sem restrição | Resp. 15 |
| Markup | Variável por pregão, indicado pelo usuário antes de gerar a planilha (default 1.45) | Q7 — Resp. 2026-05-12 |
| Overhead | 3% / 5% / 7% fixo no MVP | Resp. final 2 |
| Marca | Sistema NUNCA decide. Sempre apresenta opções | Resp. final 7 |
| Volume alvo | 500 itens/edital, 10+ editais/mês | Resp. final 5, e Resp. 10 |
| Cache de preço | TTL de 2 dias | Resp. final 9 |
| Variação de nome no scraper | **Amarelo** (avisa, não bloqueia). Tag "encontrado por variação — conferir" | Q1 — Resp. 2026-05-12 |
| Tipo de PDF dos editais | Textuais (como `anexo_1.pdf`). Sem necessidade de OCR | Q5 — Resp. 2026-05-12 |
| Credenciais de fornecedores | `data/credenciais.json` (gitignored). UI tem tela "Configurações" para o operador editar. Só email + senha (CNPJ é one-shot do primeiro acesso, não armazenamos) | Q8 — Resp. 2026-05-15 |
| Almeida: login obrigatório para preço | Login com `email + senha` (campo CNPJ só no 1º acesso). Submit por Enter no campo senha. Sessão via cookie `PHPSESSID + token` | Q8 — Inspeção 2026-05-15 |
| Almeida: parse de produto | Atributos `data-*` em `.product-card__info[data-codigo-produto]`: `data-preco`, `data-marca`, `data-codigo-produto`, `data-nome` | Q8 — Inspeção 2026-05-15 |

---

### Q6. Heurísticas de extração de PDF — refatoração concluída (2026-05-27)

**Histórico.** Versão inicial do `extrator.py` foi calibrada na amostra
única do edital Araruna 06/2026 com 3 heurísticas pontuais. Testes com
5 editais reais de outros órgãos (TRE-PE, SEI/HU, Hidráulicos, etc.)
mostraram que essas heurísticas quebravam em massa — 2 dos 5 PDFs
produziam 100% de lixo, 1 produzia 85% de lixo.

**Solução implementada (Etapa 1.0.A + 1.0.C, 2026-05-27).** Pipeline
multi-camada com seleção por score semântico:

1. **Camada 1 — pdfplumber + validador semântico** ([extrator.py](pregoes/extrator.py)
   + [validador.py](pregoes/validador.py)). Coleta todas as tabelas brutas,
   identifica cabeçalho (com concatenação multi-linha), scoreia cada
   tabela por densidade de linhas que parecem itens reais (descrição
   longa + quantidade > 0 + unidade conhecida em catálogo brasileiro).
   Aceita só tabelas com score ≥ 0.5; descarta linhas com score < 0.4.
2. **Camada 2 — Docling visual** ([extrator_docling.py](pregoes/extrator_docling.py)).
   Modelo TableFormer + DocLayNet (IBM). Chamado automaticamente quando
   Camada 1 retorna confiança < 0.95. Roda CPU-only, offline após
   primeiro download de modelos.
3. **Camada 3 — mapeamento manual com aprendizado** ([mapeamento_manual.py](pregoes/mapeamento_manual.py)
   + tela `mapeamento` em [ui/app.py](pregoes/ui/app.py)). UI mostra
   tabelas brutas, operador escolhe a tabela de itens e mapeia cada
   coluna. Mapa fica salvo em `mapeamentos_pdf` por assinatura de
   cabeçalho — próximos PDFs com o mesmo cabeçalho usam automaticamente.

**Métrica publicada.** `ResultadoExtracao.confianca_global ∈ [0,1]` (fração
de itens com score ≥ 0.7 sobre total aceito). UI mostra semáforo
(verde ≥ 0.95, amarelo ≥ 0.80, vermelho < 0.80). Quando vermelho,
sistema oferece mapeamento manual antes do operador prosseguir.

**Resultados nos 5 PDFs reais (validação de 2026-05-27):**
- PDF 02 (Hash/PE): de 22% duvidosos para 100% confiança
- PDF 04 (Hidráulicos): 99.38% mantido
- PDF 01 (Edital+Anexos): 85% de lixo → 60% confiança (75% de redução
  de lixo; ainda dependente de Docling para resolver completamente)
- PDFs 03 (TRE-PE) e 05 (SEI/HU): reconhecem falha honestamente (0%
  confiança + aviso) e oferecem mapeamento manual; antes entregavam
  100% lixo silenciosamente.

**Não bloqueia.** Sistema sempre devolve resultado utilizável: ou
extração automática de alta confiança, ou aviso explícito + mapeamento
manual disponível. Calibração futura conforme mais editais aparecerem.

---

## Bloco 3 — Questões para validar com o contador do cliente (fase 3, não bloqueia MVP)

### Q3. Tributação por NF-e

Quando entrarmos na fase 3 (inteligência tributária), validar com o contador:
- Como interpretar diferentes CST/CSOSN para identificar "imposto já pago na origem"
- Se variação por estado de origem/destino exige tabela de equivalência
- Limites legais de re-classificação tributária do mesmo NCM

### Q4. Margem mínima do edital

A coluna `AA = F × 0.5` no template assume valor mínimo aceitável = 50% da referência.
Confirmar com o cliente se essa regra é universal ou varia por tipo de pregão.
