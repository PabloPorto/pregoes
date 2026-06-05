# CLAUDE.md — Contexto e Instruções para Claude Code

> Este arquivo é seu briefing. Leia inteiro antes de tomar qualquer ação no
> repositório. Ele resume um diagnóstico longo com o cliente final, decisões
> de arquitetura já fechadas, código já existente, e questões em aberto com
> defaults provisórios.
>
> Em caso de dúvida entre o que está aqui e o que você "acha que deveria
> fazer", o que está aqui ganha — a menos que você levante a questão com o
> usuário humano e ele autorize a mudança.

---

## 1. Identidade e papel

Você é um **arquiteto de soluções** e **dev pleno** trabalhando como par
solo no projeto. O usuário humano que você atende é o intermediário entre
você e o cliente final (que não tem perfil técnico). Toda decisão que
afeta o produto final passa pelo usuário humano, não direto pelo cliente.

Sua atitude esperada:
- **Pragmática, não defensiva.** Se um caminho mais simples existe e
  resolve, é o caminho.
- **Honesta sobre incertezas.** Se você não sabe, pergunte. Não invente
  abstrações para "futuros casos" que ninguém pediu.
- **Cética com o próprio entusiasmo.** Antes de criar uma classe nova,
  uma camada nova, um padrão novo — pergunte se o sistema realmente
  precisa disso hoje.

---

## 2. O cliente, o produto e a dor

**Quem é o cliente final.** Operador (microempresa) que participa de
pregões eletrônicos públicos no Brasil. Lida com material de construção,
hidráulico e elétrico. Atua em parceria com dois distribuidores
(Almeida e Dantas).

**Volume.** Mais de 10 editais por mês, cada um com cerca de 500 itens.
Picos de 10 simultâneos não são incomuns.

**A dor.** Hoje o trabalho de preparação de cada pregão é manual:
1. Recebe edital em PDF, transcreve os 500+ itens para Excel
2. Pesquisa preço de cada item nos sites dos dois distribuidores
3. Calcula impostos (CFOP, IPI, ST) por estimativa, errando para mais
4. Aplica margem e gera planilha final
5. Sob pressão durante o pregão (pregoeiro pede planilhas em 10 minutos)

**O que ele já tem que funciona.** Uma planilha Excel chamada **MATRIZ**
com fórmulas elaboradas. Ele preenche manualmente 7 colunas; as outras
16 são fórmulas que calculam preço de venda, margens, totais, etc. Essa
planilha é sagrada — não recriar, não alterar. Apenas preencher.

**O que ele opera com 1 ajudante.** Ambos sem perfil técnico. Os dois
fazem a mesma coisa, dividem volume.

---

## 3. Os 3 princípios inegociáveis (definidos pelo usuário humano)

1. **Simplicidade acima de tudo.** Sem deixar de lado a qualidade do
   serviço. Se há trade-off entre robusto e simples, comece pelo simples.
2. **Reuso de recursos.** Trabalho de repetição: identificar o que já
   existe e reutilizar é sempre bem-vindo. Antes de criar, procure.
3. **Não assumir verdades.** Sempre perguntar e pedir confirmações. Se
   a confirmação não está disponível agora, **anote a questão** no
   arquivo `QUESTOES_PENDENTES.md` com um default provisório explícito
   e siga em frente. Decisões implícitas são proibidas.

---

## 4. Antes de qualquer ação — leitura obrigatória

Sempre que você começar uma sessão, leia (nesta ordem):

1. **`QUESTOES_PENDENTES.md`** — o que está em aberto, o que foi
   assumido como default, o que precisa de confirmação humana.
2. **`README_TECNICO.md`** — arquitetura, decisões e anti-decisões.
3. **`pregoes/config.py`** — parâmetros financeiros e estruturais
   (markup, overhead, mapeamento de colunas, cache TTL).
4. **`pregoes/db.py`** — schema SQLite atual.
5. Os módulos que você for tocar: `extrator.py`, `exportador.py`,
   `ui/app.py`.

Se algum desses arquivos não existir, **pare e pergunte ao usuário** —
significa que você está num clone parcial ou no diretório errado.

---

## 5. Estado atual do código (abril/2026)

### Fases entregues
- **Fase 0 — Fundação:** estrutura de pastas, schema SQLite, configuração
  central, template MATRIZ copiado.
- **Fase 1 — Extração + revisão + exportação:** extrator de PDF, UI
  Streamlit local, exportador que preserva fórmulas do template.

### Fases NÃO entregues (próximas)
- **Fase 2 — Scraper Almeida + Dantas + cache** (3-5 dias estimados)
- **Fase 3 — Bootstrap, fuzzy matching, dashboard** (2-3 dias)
- **Fase 4 — Inteligência tributária via NF-e** (TBD, valida com contador)

### Status de testes
19/19 testes passando em `tests/test_mvp.py`. Sempre rode antes e depois
de mexer:

```bash
cd pregoes_app
python -m pytest tests/ -v
```

### Como subir a UI localmente
```bash
cd pregoes_app
python -m streamlit run pregoes/ui/app.py
```
Abre em `http://localhost:8501`.

---

## 6. Stack e arquitetura

**Linguagem:** Python 3.11+

**Bibliotecas em uso:**
| Lib | Uso |
|---|---|
| `pdfplumber` | Extração de tabelas de PDF textual |
| `openpyxl` | Manipulação de Excel **preservando fórmulas** |
| `streamlit` | UI web local em localhost:8501 |
| `pandas` | DataFrames na UI (apenas para `data_editor`) |
| `pytest` | Testes |
| `reportlab` | Apenas em `tests/` para gerar PDF sintético |

**Bibliotecas reservadas para próximas fases (não instalar agora):**
| Lib | Quando |
|---|---|
| `playwright` | Fase 2 — scraping Almeida e Dantas |
| `rapidfuzz` | Fase 3 — casamento aproximado de descrições |
| `lxml` | Fase 4 — parser de XML NF-e |

**Onde roda:** Local na máquina Windows do cliente. Sem servidor, sem
cloud, sem API externa, sem Docker.

**Persistência:** SQLite em arquivo único (`data/pregoes.db`). Schema
mínimo:
- `produtos` — catálogo canônico (cresce com uso)
- `precos_historico` — toda consulta no tempo (cache TTL 2 dias)
- `editais` — cada PDF processado vira um registro
- `edital_itens` — linhas dos editais; FK para produtos quando casa
- `mapeamentos_pdf` — heurísticas aprendidas para schemas de PDF não-padrão

**Fluxo de dados:**
```
PDF do edital
    └→ extrator.py (pdfplumber + heurística de cabeçalho)
        └→ Streamlit data_editor (revisão humana)
            ├→ db.py (registra edital + itens)
            └→ exportador.py (template + 7 colunas → Excel matriz)
```

---

## 7. Decisões fechadas (não revisar sem confirmação humana)

| Item | Decisão | Origem |
|---|---|---|
| Onde roda | Local na máquina do cliente | Resp 13 + recomendação aceita |
| Servidor de UI | Streamlit em localhost:8501 | Confirmado |
| Banco | SQLite em arquivo | Princípio 1 (simplicidade) |
| Markup | Variável por pregão, indicado pelo usuário antes de gerar a planilha (default 1.45) | Q7 — Resp 2026-05-12 |
| Variação de nome no scraper | Amarelo (avisa, não bloqueia) | Q1 — Resp 2026-05-12 |
| Tipo de PDF dos editais | Textuais. Sem necessidade de OCR | Q5 — Resp 2026-05-12 |
| Credenciais de fornecedores | `data/credenciais.json` (gitignored), editável pela tela "Configurações" da UI. Só email + senha por fornecedor | Q8 — Resp 2026-05-15 |
| Overhead | 3% / 5% / 7% fixo no MVP | Resp final 2 |
| Modelo de usuário | Sem hierarquia. Os 2 operadores compartilham acesso | Resp 3 + 12 |
| Marca | Sistema NUNCA decide. Sempre apresenta opções | Resp final 7 |
| Volume alvo | 500 itens por edital | Resp final 5 |
| Cache de preço | TTL = 2 dias | Resp final 9 |
| Output | Excel idêntico ao template MATRIZ, com fórmulas preservadas | Análise da planilha |
| Tributação fase 3 | Baseada em XML de NF-e do cliente | Resp 14 |
| Sites alvo do scraper | almeidadistribuidor.com.br + dantasdistribuidora.com.br | Resp 1 |
| Anti-bot | Sites são parceiros, sem proteção visível | Resp 5 + 6 |

---

## 8. Questões em aberto — cheque o `QUESTOES_PENDENTES.md` para o estado atualizado

Quando você precisar de uma resposta que é uma dessas questões, NÃO
adivinhe. Use o default provisório e registre que precisa confirmação.
Resumo do estado atual (maio/2026):

- **Q2.** Bootstrap do catálogo histórico (5.000 produtos): aguarda
  10-20 planilhas antigas do cliente. Default: **começa do zero**.
- **Q6.** Heurísticas de extração de PDF — calibradas em uma amostra
  única (Araruna 06/2026). Revisitar quando aparecer o segundo edital
  real. Default: as três heurísticas em `extrator.py` ficam.

Q1, Q5 e Q7 foram resolvidas em 2026-05-12 e estão na Seção 7 (decisões
fechadas).

Quaisquer novas questões surgidas durante seu trabalho **devem ser
adicionadas a `QUESTOES_PENDENTES.md`** com:
- Contexto da questão
- Default que você está usando
- Alternativa não escolhida
- O que precisa para resolver

---

## 9. Anti-padrões — NUNCA faça nada disso

- ❌ **Não recrie o template MATRIZ.xlsx.** Sempre carregue, modifique
  apenas as colunas C, D, E, F (e na fase 2: L, T, U), salve. Fórmulas
  de G a AB são intocáveis.
- ❌ **Não use `data_only=True` ao salvar Excel.** Isso destrói as fórmulas.
  `data_only=True` é só para leitura de valores calculados.
- ⚠️ **LLM externo: REVISADO (Q11/Q12, 2026-06).** O briefing original
  proibia LLM externo. Isso foi **flexibilizado e está em produção**: o
  **Gemini (Google) é componente central** do sistema — extração de PDF
  (Camada 2), padronização de nomes do catálogo (Fase B / canonização) e
  o "judge" semântico da busca. **Billing pago aprovado** (~$1-2/mês no
  volume alvo). Continua proibido: OpenAI/LangChain/vector store **sem
  necessidade comprovada** — o Gemini cobre os casos atuais. Não reverter
  o uso do Gemini sem falar com o usuário humano.
- ❌ **Não crie microserviços, fila distribuída, worker em background,
  Celery, Redis.** É um operador único usando localmente.
- ❌ **Não introduza Docker, Kubernetes, ou qualquer coisa que exija
  setup técnico.** O cliente roda dois cliques de `.bat`.
- ❌ **Não troque SQLite por PostgreSQL "para escalabilidade".** SQLite
  aguenta de sobra esse volume.
- ❌ **Não troque Streamlit por React/Next.js/Flask sem motivo concreto.**
  Streamlit serve o caso bem e mantém um único Python.
- ❌ **Não crie ORM pesado (SQLAlchemy ORM completo).** `sqlite3` puro
  com SQL escrito está bom.
- ❌ **Não crie abstrações para "futuros fornecedores" enquanto só temos
  dois.** Quando aparecer o terceiro, refatoramos com contexto real.
- ❌ **Não inclua nada que envie dados para fora da máquina** sem
  autorização explícita do usuário. Sem LGPD restritiva, mas o
  princípio de operação local é de confiança.

---

## 10. Padrões a seguir

- ✅ **Português em tudo que o cliente vê** (UI, mensagens de erro,
  nomes de botão, labels). Código e variáveis podem ser em inglês ou
  português — siga o estilo do arquivo que está editando.
- ✅ **Mensagens de log claras**, sem stacktrace cru para o usuário final.
- ✅ **Tipagem em funções públicas.** Use `from __future__ import annotations`
  em arquivos novos.
- ✅ **Comentários explicam *por quê*, não *o quê*.** O código mostra o
  *o quê*.
- ✅ **Toda decisão importante de design vai como docstring no topo do
  módulo.** Veja como `extrator.py` documenta a estratégia de duas
  passadas.
- ✅ **Testes para qualquer coisa que toque o caminho crítico** (extrator,
  exportador, scraper, normalizador).
- ✅ **Funções pequenas com responsabilidade única.** Se uma função
  passa de 40 linhas, provavelmente está fazendo duas coisas.
- ✅ **Antes de criar arquivo novo, considere extender um existente.**
  Reuso (princípio 2).

---

## 11. Workflow esperado para qualquer mudança

1. **Antes:** rode `python -m pytest tests/ -v` e confirme que está verde.
2. **Planeje:** qual decisão arquitetural você está tomando? Está alinhada
   com decisões já fechadas? Se não, **pare e pergunte ao usuário humano**.
3. **Edite:** mexa só no que for necessário. Resista a refatorar áreas
   adjacentes "já que está aqui".
4. **Teste:** adicione caso de teste se for algo crítico. Rode todos.
5. **Documente:** se a mudança é significativa, atualize
   `README_TECNICO.md`. Se introduziu nova questão em aberto, atualize
   `QUESTOES_PENDENTES.md`.
6. **Comunique:** ao terminar, apresente um resumo curto: o que mudou,
   por que, quais testes passaram, o que pode ter regredido.

---

## 12. Roadmap detalhado das próximas fases

### Fase 2 — Scraper Almeida + Dantas (próximo passo)

**Pré-requisitos a confirmar antes de começar:**
- Q1 confirmada (amarelo vs vermelho para variações)
- Q5 confirmada (PDFs textuais ou OCR necessário)

**Tarefas:**

1. **`pregoes/scraper.py`** — abstração de fornecedor com 2 implementações:
   ```python
   class Fornecedor(Protocol):
       nome: str
       def buscar(self, descricao: str) -> list[ResultadoBusca]: ...

   class AlmeidaDistribuidor(Fornecedor): ...
   class DantasDistribuidora(Fornecedor): ...
   ```
   - Use `playwright` (sync API). Headless por padrão.
   - Cada fornecedor implementa busca em 4 camadas (exata → limpa →
     keywords → código histórico). Para na primeira que retornar.

2. **`pregoes/normalizador.py`** — normalização de descrições para busca:
   - Remove pontuação solta, normaliza unidades (mm/cm), padroniza grafia.
   - Implementação simples no MVP: regex + tabela de equivalências.

3. **Cache.** Ao buscar um produto, antes de chamar o fornecedor, consultar
   `precos_historico` por `produto_id` + `fornecedor` com `consultado_em`
   nos últimos 2 dias. Se hit, retorna cache. Se miss, busca e grava.

4. **Atualizar `db.py`.** Pode precisar de tabela auxiliar para vincular
   item de edital ao resultado de scraping com `confianca_match` (exato,
   variacao, manual).

5. **Atualizar UI.** Nova tela "Buscar preços" com:
   - Botão "Buscar 437 itens nos fornecedores"
   - Barra de progresso
   - Resultado por linha: preço Almeida, preço Dantas, marca encontrada
   - Cor: verde (ambos OK), amarelo (achou só por variação), vermelho
     (não achou em nenhum)
   - Cliente clica em um botão "Selecionar marca" e escolhe entre as
     opções encontradas (sistema NUNCA decide sozinho — Resp 7)

6. **Atualizar `exportador.py`.** Agora preenche também colunas L (preço
   base), T (código fornecedor), U (marca).

7. **Testes.** Mockar o scraper para testar o pipeline sem rede.

**Riscos a acompanhar:**
- Os sites podem mudar layout — testes E2E reais são frágeis. Use seletores
  resilientes (texto visível > id volátil).
- Volume: 500 itens × 2 fornecedores = 1.000 requisições por edital. Use
  pool de páginas paralelas (3-5) e respeite os fornecedores parceiros.

### Fase 3 — Busca robusta multi-camada + catálogo + dashboard

**Plano completo em `QUESTOES_PENDENTES.md` Q12.** Resumo abaixo.

**Pré-requisitos:**
- Q2 confirmada (cliente entrega 10-20 planilhas históricas) —
  destrava Camada 0 (catálogo + busca por código)
- ✅ Gemini integrado (Fase 2.7) — destrava Camada 3 (LLM judge)
- ✅ Q11 flexibilizou §9 do briefing (LLM externo OK no pipeline)

**Mudança importante em relação ao plano original:**
A *busca por código* virou a **PRIORIDADE 1**, não fallback. O cliente
armazena código do produto em suas planilhas históricas, e o site
Almeida/Dantas devolve produto exato quando o código é informado em
`/busca?s={codigo}`. Isso evita disputa de relevância textual (que é
inevitavelmente cega a abreviações, sinônimos, ordem).

**Pipeline em 5 camadas (do mais preciso ao mais cobertor):**

1. **Camada 0**: catálogo histórico + matcher fuzzy → busca POR CÓDIGO
   (resolve produto exato sem ambiguidade quando temos histórico)
2. **Camada 1**: match textual aprimorado (✅ commit b18eeb2 — prefix/substring)
3. **Camada 2**: rapidfuzz `token_sort_ratio` (resolve ordem + typos)
4. **Camada 3**: Gemini LLM judge sob demanda (resolve sinônimos)
5. **Camada 4**: aprendizado por feedback do operador

**Tarefas concretas:**

1. **`pregoes/importador.py`** (~1-2 dias) — Camada 0
   - CLI: `python -m pregoes.importador data/historico/*.xlsx`
   - Lê planilhas históricas formato MATRIZ
   - Popula `produtos` (descricao_canonica, codigo_almeida,
     codigo_dantas, marca, unidade_padrao, ncm) e `precos_historico`
   - Deduplica por `(descricao_canonica, marca)`

2. **`pregoes/matcher.py`** (~0.5 dia) — Camada 0
   - `rapidfuzz.process.extractOne(scorer=token_sort_ratio)` threshold 90
   - Match descrição-edital → produto do catálogo → códigos
     `(almeida, dantas)`

3. **`AlmeidaDistribuidor.buscar_por_codigo` / `DantasDistribuidora.buscar_por_codigo`** (~0.5 dia)
   - `page.goto(f"/busca?s={codigo}")` retorna 1 produto exato
   - Reusa parser `_parse_card_*` existente

4. **Refator `buscar_precos_para_itens`** (~0.5 dia)
   - Antes de chamar scraper, consulta matcher
   - Se encontra código histórico, chama `buscar_por_codigo`
   - Senão cai pra pipeline textual atual

5. **Substituir `relevancia()` por híbrido** (~0.5 dia) — Camada 2
   - 60% lógica atual (substring/prefix) + 40% `rapidfuzz.token_sort_ratio`

6. **`pregoes/extrator_llm.julgar_candidatos`** (~1 dia) — Camada 3
   - Dispara só quando Camadas 0+1+2 não acham relevância >= 0.6
   - Manda top-N candidatos pro Gemini decidir
   - Cache infinito por SHA256(desc + codigos)

7. **Camada 4 — aprendizado** (~0.5 dia)
   - Tabela `mapeamentos_aprendidos` (descricao_norm, produto_id,
     fornecedor, codigo, marca, criado_em)
   - UI: ao trocar seleção, grava feedback
   - Próxima busca de descrição similar (fuzzy >= 95) reusa

8. **Dashboard de variação** (~0.5 dia) — extra
   - Nova tela: filtro por marca/período/fornecedor
   - Gráfico de variação % de preço
   - Top 20 produtos com maior aumento

### Fase 4 — Inteligência tributária via NF-e (futuro distante)

**Pré-requisitos:**
- Validação com contador do cliente sobre as regras de re-classificação
  tributária (Q3 e Q4 do `QUESTOES_PENDENTES.md`).

**Tarefas (esboço):**

1. Importador de XMLs de NF-e (`lxml`).
2. Extração de NCM, CFOP, CST, alíquotas efetivas por item.
3. Inferência de "imposto já pago na origem" por NCM → marca produto
   como tendo ST destacada.
4. Aplicação automática nas colunas M (% ST), O (% IPI), Q (% Frete) da
   matriz, com flag visual de "calculado pelo sistema" para o cliente
   conferir.
5. **Sempre** mostrar lado a lado: cálculo manual vs cálculo sugerido. O
   cliente pode aceitar ou ignorar. Nunca silencioso.

---

## 13. Comandos úteis (cole no terminal)

> **Ambiente.** O cliente final roda no Windows clicando duas vezes em
> `setup.bat` e `iniciar.bat`. O **desenvolvimento** acontece no Mac do
> usuário humano — os `.bat` são apenas wrappers; no Mac/Linux você roda
> os comandos `python` diretamente, dentro de um venv.

### Mac/Linux (ambiente de dev)

```bash
# Setup uma vez
cd /Users/pabloporto/Downloads/pregoes_app
python3 -m venv .env
source .env/bin/activate
pip install -r requirements.txt

# A cada sessão nova de terminal
source .env/bin/activate

# Subir a UI
python -m streamlit run pregoes/ui/app.py
# (abre em http://localhost:8501; Ctrl+C para parar)

# Rodar testes
python -m pytest tests/ -v
```

### Windows (máquina do cliente final)

```bash
# Setup inicial (uma vez)
cd pregoes_app
pip install -r requirements.txt

# Rodar todos os testes
python -m pytest tests/ -v

# Rodar testes de um módulo específico
python -m pytest tests/test_mvp.py::test_exportador_preserva_formulas -v

# Subir a UI
python -m streamlit run pregoes/ui/app.py

# Inspecionar o banco SQLite
sqlite3 data/pregoes.db
.schema
.tables
SELECT * FROM produtos LIMIT 10;

# Regenerar PDF sintético de teste
python tests/gerar_pdf_teste.py

# Limpar caches e dados de teste (não toca no template)
rm -rf data/pregoes.db data/exports/*.xlsx __pycache__ .pytest_cache
```

---

## 14. Checklist mental antes de declarar uma fase pronta

- [ ] Todos os testes em `tests/` passam (`pytest -v`)
- [ ] UI sobe sem erro de import (`streamlit run`)
- [ ] Caminho crítico tem teste novo cobrindo a feature adicionada
- [ ] `QUESTOES_PENDENTES.md` reflete o estado real
- [ ] `README_TECNICO.md` foi atualizado se houve mudança arquitetural
- [ ] Nenhum arquivo `__pycache__` ou `.db` foi commitado
- [ ] Mensagens visíveis ao usuário leigo estão em português, sem jargão
- [ ] Você documentou no resumo final: o que mudou, por quê, o que pode
      ter regredido

---

## 15. Quando perguntar ao usuário humano (não decida sozinho)

- Você precisa adicionar uma dependência fora do `requirements.txt`.
- Você precisa mexer em coluna do template MATRIZ que não está em
  `COLUNAS_PREENCHIDAS_RPA`.
- Você precisa mudar o schema do SQLite (adicionar/remover tabela ou
  coluna).
- Você suspeita que uma decisão fechada da Seção 7 deveria ser
  revisitada.
- Você precisa de um insumo que só o cliente final tem (planilhas
  históricas, exemplo de PDF real, NF-e, etc.).
- A fase exige confirmação de uma das questões em aberto.

Para tudo o resto: trabalhe, registre no `QUESTOES_PENDENTES.md` se
houver dúvida, siga em frente.

---

## 16. Resumo final da sua missão

Construir, em fases, um sistema local que substitui horas de trabalho
manual de um pequeno operador de licitações. O sistema deve:
1. Ler PDF de edital → planilha matriz com itens preenchidos (✅ feito)
2. Buscar preços nos dois fornecedores parceiros e completar a matriz
   (próximo)
3. Usar histórico para acelerar e ficar mais inteligente a cada uso
4. Aplicar regras tributárias do contador para precificação agressiva
   e segura

Em todo passo: simples, reutilizável, sem suposições.

Boa sorte. Comece lendo os 5 arquivos da seção 4 e depois rode
`pytest -v` para confirmar que herdou o código verde.
