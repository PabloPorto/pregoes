# Documentação Técnica

## Visão geral

MVP de automação de licitações para um operador de pregões eletrônicos.
Stack inteiramente local, offline-first, sem dependência de serviços externos.

## Filosofia (princípios definidos com o cliente)

1. **Simplicidade acima de tudo.** Se uma decisão tem trade-off entre
   "mais robusto" e "mais simples", começamos pela simples.
2. **Reuso de recursos existentes.** Se o template Excel do cliente já
   calcula 16 colunas via fórmulas, a gente NÃO reimplementa esses cálculos
   em Python. Preserva o template.
3. **Não assumir verdades.** Toda hipótese vira pergunta. Decisões viram
   linhas no `QUESTOES_PENDENTES.md`.

## Arquitetura

```
PDF do edital
     │
     ▼
[extrator.extrair_com_pipeline()]
   1. pdfplumber + score semântico (validador.py) — rápido
   2. Se confiança < 0.95 → tenta extrator_docling (visual, TableFormer)
   3. Se ainda < 0.95 → UI procura mapa salvo em mapeamentos_pdf
   4. Se mapa não existe → operador faz mapeamento manual (UI)
     │
     ▼
[Streamlit UI] ── badge de confiança + revisão humana (data_editor)
     │
     ├──> [db.py / SQLite] ── catálogo, histórico,
     │                         mapeamentos_pdf (memória)
     │
     └──> [exportador.py] ── carrega template, preenche 7 colunas,
                              preserva 16 fórmulas
                                    │
                                    ▼
                             Excel matriz pronto
```

### Decisões registradas

| Decisão | Razão |
|---|---|
| Python 3.11+ | Ecossistema único cobre PDF, scraping, Excel, dados |
| pdfplumber (não Camelot, não pdfminer puro) | Melhor balanço entre qualidade e velocidade no português |
| SQLite (não PostgreSQL) | Zero infra, arquivo único, suficiente para milhões de linhas neste caso |
| Streamlit (não Flask + React) | UI web em 1 arquivo Python; cliente leigo, dev solo |
| openpyxl preservando fórmulas (não reescrever em Python) | Template do cliente é sagrado (princípio 2) |
| Local na máquina do cliente | Resposta 13 + zero custo recorrente |
| `pyinstaller` para empacotar (futuro) | Cliente clica .exe, não precisa Python |

### O que NÃO fazemos (anti-decisões intencionais)

- ✅ **Usamos LLM (Gemini) como componente central** (revisado em Q11/Q12).
  Papéis: extração de PDF (Camada 2), padronização de nomes do catálogo
  (canonização, Fase B) e judge semântico da busca. Billing **pago**
  (~$1-2/mês). Continuamos sem OpenAI/LangChain/vector store enquanto o
  Gemini cobrir — ver "Busca em camadas" abaixo.
- ❌ Não usamos Docker — cliente não tem perfil técnico
- ❌ Não usamos microserviços — operador único, dor única, app único
- ❌ Não usamos ORM pesado (SQLAlchemy completo) — sqlite3 + raw SQL bastam
- ❌ Não criamos abstrações para "futuros fornecedores" — quando aparecer
  o terceiro fornecedor, refatoramos com contexto real

### Busca de preços em camadas (estado atual, 2026-06)

Por item, do mais preciso ao mais cobertor:
1. **Cache** (TTL 2 dias) → reusa busca recente.
2. **Aprendizado** (Camada 4) → descrição já confirmada pelo operador vira
   código direto.
3. **Catálogo por código** (Camada 0) → `matcher` casa a descrição do edital
   contra o catálogo histórico (importado das planilhas do cliente). A
   **canonização via Gemini** (`alinhador`) alinha o vocabulário
   (`sxt`→`sextavado`, `cano`↔`tubo`) — sem ela o fuzzy casava ~0%. Com ela,
   busca `/busca?s={codigo}` → produto exato. Salvaguarda valida afinidade.
4. **Busca textual** (Tiers 1-3) → query distintiva + relevância calibrada
   (variante F) + validação de dimensão (inclui mm/pol/tomadas/volts).
5. **Judge** (Gemini) → revisor semântico que rejeita os falsos sutis
   (fita-rotulador ≠ fita-crepe).

**Confiabilidade medida (edital real da especialidade do cliente):**
~88-90% certa para piloto supervisionado, após o sprint pré-piloto de
correções. Nos itens sem correspondência diz "não encontrado" (não inventa).
Degrada com transparência sem chave/quota/rede (cai para o textual, que não
alucina).

## Estrutura de pastas

```
pregoes_app/
├── README.md                  # Documentação para o cliente leigo
├── README_TECNICO.md          # Este arquivo
├── QUESTOES_PENDENTES.md      # Tudo que ainda não foi decidido
├── requirements.txt
├── setup.bat                  # Instalação Windows
├── iniciar.bat                # Boot do sistema Windows
├── pregoes/                   # Pacote Python
│   ├── __init__.py
│   ├── config.py              # Constantes — único arquivo a editar para tweaks
│   ├── db.py                  # Schema SQLite + preco_em_cache + gravar_preco
│   ├── extrator.py            # PDF → ItemExtraido (pipeline multi-camada)
│   ├── extrator_docling.py    # ✅ Camada visual Docling/TableFormer (Q6)
│   ├── validador.py           # ✅ score_linha / score_tabela (Q6)
│   ├── mapeamento_manual.py   # ✅ mapa por assinatura → mapeamentos_pdf (Q6)
│   ├── exportador.py          # ItemExtraido + SelecaoFornecedor → Excel matriz
│   ├── normalizador.py        # ✅ normalizar_descricao + keywords + relevancia híbrida
│   ├── credenciais.py         # ✅ CRUD de data/credenciais.json (Q8)
│   ├── scraper.py             # ✅ Almeida + Dantas + busca por código (Camada 0)
│   ├── matcher.py             # ✅ rapidfuzz contra catálogo (Camada 0)
│   ├── importador.py          # ✅ CLI: importa planilhas históricas (Camada 0)
│   ├── alinhador.py           # ✅ canonização de nomes via Gemini (Fase B)
│   ├── extrator_llm.py        # ✅ Camada 2 extração + Camada 3 judge (Q11/Q12)
│   └── ui/
│       ├── __init__.py
│       └── app.py             # Streamlit — 6 telas (home, upload, revisão,
│                              # mapeamento manual, busca de preços,
│                              # configurações)
├── data/
│   ├── pregoes.db             # SQLite (criado em runtime, gitignored)
│   ├── credenciais.json       # email/senha Almeida + Dantas (gitignored)
│   ├── templates/
│   │   └── matriz_template.xlsx
│   ├── editais/               # PDFs originais (gitignored, exceto sintético)
│   └── exports/               # Excels gerados (gitignored)
├── scripts/                   # Utilitários ad-hoc (não rodam em CI)
│   ├── diagnostico_extrator.py    # calibrar heurísticas em editais novos
│   └── inspecionar_tabelas.py     # ver todas as tabelas detectadas no PDF
└── tests/
    ├── gerar_pdf_teste.py
    ├── test_mvp.py            # Fase 1 + extrator novo + validador +
    │                          # mapeamento manual + pipeline
    ├── test_fase2_inicial.py  # Fase 2 (scraper, normalizador, credenciais,
    │                          # orquestrador)
    ├── test_extrator_llm.py   # Camada 2 (Gemini)
    ├── test_reconstrutor.py   # Reconstrução Docling multi-página
    └── test_fase3.py          # Fase 3 — matcher, importador, Camadas 0-4
```

A suíte completa passa (`python -m pytest -v`). Retrato atual: 325 testes
coletados, 323 passando — os 2 pulados dependem do SDK `google-genai`, que
o CI roda sem ele instalado.

## Schema SQLite

11 tabelas no total:

```
schema_version            — versão da migration aplicada (MAX(version) = atual)
produtos                  — catálogo canônico (cresce com uso + importador histórico)
precos_historico          — preços das planilhas históricas do cliente. Hoje só o
                            importador grava aqui; o scraper NÃO escreve nesta tabela
precos_cache_descricao    — cache TTL=2d das buscas do scraper por (descrição
                            normalizada, fornecedor)
editais                   — cada PDF processado
edital_itens              — linhas dos editais; FK para produtos quando casa
mapeamentos_pdf           — heurísticas aprendidas para schemas não-padrão
cache_canonico            — Fase B: cache da normalização canônica via Gemini
produtos_canonico         — Fase B: forma canônica de cada produto do catálogo (1:1)
cache_llm_judge           — Camada 3: cache TTL infinito por SHA256(desc + códigos)
mapeamentos_aprendidos    — Camada 4: descrição_norm → código escolhido pelo operador
```

## Próximas fases (em ordem)

### Fase 2 — Scraper Almeida + Dantas (3-5 dias) — ✅ entregue
- Playwright para navegar nos sites — ✅ pronto (sync API, headless)
- Busca textual em camadas: descrição normalizada → top-3 keywords → top-2
  keywords. A busca por código histórico (Camada 0) entrou na Fase 3 — ver
  "Busca de preços em camadas" acima.
- Almeida (`AlmeidaDistribuidor`) — ✅ login via cookies PHPSESSID+token, parse via `data-*` em `.product-card__info`
- Dantas (`DantasDistribuidora`) — ✅ implementada (login, `buscar`, `buscar_por_codigo`, parser `_parse_card_dantas`)
- Cache TTL=2 dias (`precos_cache_descricao`) — ✅ integrado: é o 1º passo de
  `scraper.buscar_precos_para_itens` (hit reaproveita, miss busca e grava o
  cache). Distinto de `precos_historico`, que só o importador alimenta.
- UI: tela de busca de preços com seleção manual de marca por item — ✅ pronta

### Fase 3 — Busca robusta em 5 camadas — ✅ entregue
Implementado e commitado:
- ✅ `importador.py` — CLI lê planilhas MATRIZ históricas e popula `produtos`
- ✅ `matcher.py` — `rapidfuzz.token_sort_ratio` contra catálogo (threshold 90)
- ✅ Camada 0 — `buscar_por_codigo` em Almeida e Dantas (`/busca?s={codigo}`)
- ✅ Camada 2 — relevância híbrida (substring/prefix + rapidfuzz)
- ✅ Camada 3 — `julgar_candidatos` via Gemini com cache SHA256 TTL infinito
- ✅ Camada 4 — `mapeamentos_aprendidos` (descrição_norm → código), gravado
  pela UI quando o operador escolhe uma marca

**Pré-requisito de operação:** cliente entregar 1+ planilha histórica
(Q2) para popular o catálogo. Sem isso, todas as camadas funcionam
mas a Camada 0 não dispara — pipeline cai direto na busca textual
(Camadas 1+2 + Camada 3).

**Pendente (fora do escopo desta entrega):**
- Página de dashboard de variação de preço por marca/fornecedor
- Telemetria visível ao operador (codigo_hits / llm_judge_hits) — campos
  já existem no `ResultadoLote`, falta exibir na UI

### Fase 4 — Inteligência tributária (TBD)
- Parser XML NF-e
- Inferência de "imposto já pago na origem" por NCM
- Aplicação automática nos %ST/%IPI/%Frete
- Trigger: validar lógica com o contador do cliente

## Como rodar testes

```bash
cd pregoes_app
python -m pytest tests/ -v
```

## Como adicionar um campo novo na planilha

1. Adicionar coluna no template `data/templates/matriz_template.xlsx`
2. Mapear em `pregoes/config.py:COLUNAS`
3. Se for campo preenchido pelo RPA, adicionar a `COLUNAS_PREENCHIDAS_RPA`
4. Atualizar `exportador.py` se a fórmula precisar de tratamento especial
5. Rodar testes — `test_exportador_preserva_formulas` quebra se fórmula sumiu
