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
                              aplica markup POR ITEM (ST vs normal),
                              preserva 16 fórmulas
                                    │
                                    ▼
                             Excel matriz pronto
```

O NCM de cada produto (insumo do markup por ST — ver Fase 4) NÃO vem no card
de busca: mora na **página de detalhe** do produto. `scraper.consultar_ncms()`
visita essas páginas sob demanda (reusando a URL capturada no card) e devolve
o NCM por item; `tributacao.classificar_markups()` decide markup ST (1,30) ou
normal (1,45) a partir daí.

### Decisões registradas

| Decisão | Razão |
|---|---|
| Python 3.11+ | Ecossistema único cobre PDF, scraping, Excel, dados |
| pdfplumber (não Camelot, não pdfminer puro) | Melhor balanço entre qualidade e velocidade no português |
| SQLite (não PostgreSQL) | Zero infra, arquivo único, suficiente para milhões de linhas neste caso |
| Streamlit (não Flask + React) | UI web em 1 arquivo Python; cliente leigo, dev solo |
| openpyxl preservando fórmulas (não reescrever em Python) | Template do cliente é sagrado (princípio 2) |
| Local na máquina do cliente | Resposta 13 + zero custo recorrente |
| `pyinstaller` para empacotar — ✅ entregue (ver `BUILD.md`) | Cliente clica .exe, não precisa Python |

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

### User-Agent do scraper — por que não pode ser o default (2026-08-31)

O Chromium headless se anuncia como `HeadlessChrome/<versão>` no User-Agent.
Desde ~agosto/2026 **os dois fornecedores respondem 403** (117 bytes, sem
formulário) a qualquer requisição com essa assinatura. Por isso
`AlmeidaDistribuidor.__enter__` e `DantasDistribuidora.__enter__` passam
`user_agent=_user_agent_real(browser)` ao `new_context()`.

Comprovado com teste controlado — mesmo IP, mesmo instante, trocando só o UA:

| User-Agent | HTTP | `input[name='email']` |
|---|---|---|
| `HeadlessChrome/147...` | 403 | ausente → timeout de 30s |
| `Chrome/131...` | 200 | presente |

`_user_agent_real` deriva a string de `browser.version` em vez de fixá-la, para
não envelhecer a cada atualização do Chromium.

**Armadilha de diagnóstico:** `page.goto()` **não levanta exceção em 403** — para
o Playwright a navegação deu certo. O sintoma chegava ao suporte como
`TimeoutError` no `input[name='email']`, culpando o seletor. Por isso o login
passa por `_abrir_pagina_login`, que confere `response.status` e falha na hora
com o status no texto. Se um dia o campo de e-mail "sumir", **confira o status
HTTP antes de suspeitar do seletor**.

O transporte HTTP (`_http_get`) manda `User-Agent: Mozilla/5.0` e não é afetado.

### Busca de preços em camadas (estado atual, 2026-06)

Por item, do mais preciso ao mais cobertor:
1. **Aprendizado** (Camada 4) → descrição já confirmada pelo operador vira
   código direto. **Consultado ANTES do cache (2026-06-23):** a escolha
   ensinada pelo operador é a inteligência central do produto e tem
   precedência sobre o resultado cru de qualquer busca/cache. Recupera o
   produto por código (preço ao vivo), pré-seleciona e marca
   `veio_por_aprendizado` (badge "✓ sua escolha anterior"). Antes o cache vinha
   primeiro e atropelava o aprendizado dentro da janela de 2 dias.
2. **Cache** (TTL 2 dias) → reusa busca recente. Só para itens **sem** escolha
   aprendida (item aprendido pula o cache de propósito).
3. **Catálogo por código** (Camada 0) → `matcher` casa a descrição do edital
   contra o catálogo histórico (importado das planilhas do cliente). A
   **canonização via Gemini** (`alinhador`) alinha o vocabulário
   (`sxt`→`sextavado`, `cano`↔`tubo`) — sem ela o fuzzy casava ~0%. Com ela,
   busca `/busca?s={codigo}` → produto exato. Salvaguarda valida afinidade.
4. **Busca textual** (Tiers 1-3) → query distintiva + relevância calibrada
   (variante F) + validação de dimensão (inclui mm/pol/tomadas/volts).
5. **Judge** (Gemini) → revisor semântico que rejeita os falsos sutis
   (fita-rotulador ≠ fita-crepe).

**Redes de segurança manuais (por item, na tela de seleção):** "Buscar no meu
catálogo" (produtos que o cliente já tem, preço cotado ao vivo por código) e
"Buscar direto no site" (`scraper.buscar_no_site` — nome ou código, fornecedor
à escolha, **sem filtro de relevância**: o operador vê os cards crus e julga).
Toda escolha manual vira aprendizado → da próxima vez é automática.

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
├── requirements-dev.txt
├── setup.bat                  # Instalação Windows
├── iniciar.bat                # Boot do sistema Windows
├── run_app.py                 # ✅ Launcher do empacotamento .exe (P1.1)
├── pregoes.spec               # ✅ Receita PyInstaller (ver BUILD.md)
├── build_exe.bat              # ✅ Gera o .exe no Windows
├── BUILD.md                   # ✅ Como empacotar/distribuir o .exe
├── pregoes/                   # Pacote Python
│   ├── __init__.py
│   ├── config.py              # Constantes — único arquivo a editar para tweaks
│   │                          # (frozen-aware: dados ao lado do .exe)
│   ├── db.py                  # Schema SQLite + preco_em_cache + gravar_preco
│   ├── migrations.py          # ✅ Migration de schema versionada (P0.1)
│   ├── backup.py              # ✅ Backup automático consistente do .db (P0.2)
│   ├── logconf.py             # ✅ Log em arquivo / observabilidade (P0.3)
│   ├── erros.py               # ✅ Mensagens de erro para leigo (P0.4)
│   ├── extrator.py            # PDF → ItemExtraido (pipeline multi-camada)
│   ├── extrator_docling.py    # ✅ Camada visual Docling/TableFormer (Q6)
│   ├── reconstrutor_docling.py # ✅ Reconstrução Docling multi-página
│   ├── validador.py           # ✅ score_linha / score_tabela (Q6)
│   ├── mapeamento_manual.py   # ✅ mapa por assinatura → mapeamentos_pdf (Q6)
│   ├── exportador.py          # ItemExtraido + SelecaoFornecedor → Excel matriz
│   │                          # (markup POR ITEM: ST vs normal — Fase 4)
│   ├── normalizador.py        # ✅ normalizar_descricao + keywords + relevancia híbrida
│   ├── ranking.py             # ✅ ordenação/relevância de candidatos
│   ├── credenciais.py         # ✅ CRUD de data/credenciais.json (Q8)
│   ├── scraper.py             # ✅ Almeida + Dantas + busca por código (Camada 0)
│   │                          # + consultar_ncms (NCM da pág. de detalhe — Fase 4)
│   ├── matcher.py             # ✅ rapidfuzz contra catálogo (Camada 0)
│   ├── importador.py          # ✅ CLI: importa planilhas históricas (Camada 0),
│   │                          # transacional por arquivo → imports_log
│   ├── alinhador.py           # ✅ canonização de nomes via Gemini (Fase B)
│   ├── extrator_llm.py        # ✅ Camada 2 extração + Camada 3 judge (Q11/Q12)
│   ├── tributacao.py          # ✅ Fase 4 — ST por NCM (eh_st, markup_para,
│   │                          # classificar_markups, ncm_de_texto/ncm_de_card)
│   ├── diagnostico.py         # ✅ diagnóstico interno do pipeline
│   ├── dados/
│   │   └── ncm_st.json        # ✅ 389 NCMs de ST (Anexo 05 RICMS/PB) — referência
│   └── ui/
│       ├── __init__.py
│       └── app.py             # Streamlit — telas: início, upload, revisão,
│                              # mapeamento manual, busca de preços (com painel
│                              # "Markup deste pregão" + "Identificar NCM"),
│                              # histórico (rebaixar planilhas geradas),
│                              # configurações (aba Catálogo histórico)
├── data/
│   ├── pregoes.db             # SQLite (criado em runtime, gitignored)
│   ├── credenciais.json       # email/senha Almeida + Dantas (gitignored)
│   ├── templates/
│   │   └── matriz_template.xlsx
│   ├── editais/               # PDFs originais (gitignored, exceto sintético)
│   └── exports/               # Excels gerados (gitignored)
├── scripts/                   # Utilitários ad-hoc (não rodam em CI)
│   ├── diagnostico_extrator.py    # calibrar heurísticas em editais novos
│   ├── inspecionar_tabelas.py     # ver todas as tabelas detectadas no PDF
│   ├── catalogo_saude.py          # auditar cobertura de códigos do catálogo
│   ├── avaliar_busca.py           # avaliar acerto da busca de preços
│   ├── graduar_diagnostico.py     # graduar/pontuar a qualidade da extração
│   ├── teste_ao_vivo.py           # busca real nos fornecedores (smoke ao vivo)
│   └── inspecionar_ncm.py         # inspecionar NCM na pág. de detalhe (Fase 4)
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

A suíte completa passa (`python -m pytest -v`). Retrato atual: 349 testes
passando. Há 2 casos que são pulados quando o SDK `google-genai` não está
instalado no ambiente.

## Schema SQLite

13 tabelas no total:

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
imports_log               — rastreabilidade da importação transacional (1 linha por
                            planilha importada: arquivo, n_produtos, n_precos, status)
exports_log               — histórico de cada planilha .xlsx GERADA (append-only;
                            espelho de imports_log). 1 linha por geração: SÓ o
                            basename do arquivo (caminho = EXPORTS_DIR/arquivo,
                            reconstruído no uso — imune a mover a pasta portável),
                            n_itens, n_selecoes, markup_global (auditoria), status.
                            FK edital_id → editais (ON DELETE CASCADE). Criada pela
                            migration v2 (aditiva, CREATE IF NOT EXISTS)
```

### Histórico de planilhas geradas (exports_log + tela "Histórico")

Antes, depois de gerar a planilha o programa perdia a referência ao `.xlsx`:
o `download_button` só existia naquele render. Agora cada geração grava 1 linha
em `exports_log` (append-only, na MESMA transação de `_persistir_edital` — o
rollback descarta edital e log juntos) e há a tela **"Histórico"** (item na
sidebar, entre "Início" e "Novo pregão"):

- **Ver:** lista os pregões processados (`editais LEFT JOIN exports_log`,
  `ORDER BY criado_em DESC`), com rótulo amigável em PT. Cada pregão num
  `expander` revela suas planilhas (mais recente no topo).
- **Rebaixar = reler o arquivo do disco, NUNCA re-exportar.** O `.xlsx` é o
  artefato final que o operador validou (preço/marca/markup/fórmulas embutidos);
  re-rodar `exportar()` seria *lossy* (os itens não guardam marca/código/NCM/
  markup). O caminho é reconstruído a cada render como `config.EXPORTS_DIR /
  arquivo` (só o basename é persistido). Arquivo ausente (movido/apagado):
  `Path.exists()` em runtime → aviso amarelo, sem botão; nunca estoura erro.

Mudanças de schema/lógica que acompanham:

- **`_persistir_edital` deixou de ser INSERT-cego → virou UPSERT por
  `(orgao, numero_pregao)` NORMALIZADO** (trim + lower, tolera `00006/2026`
  vs ` 00006/2026 ` e caixa do órgão): re-gerar o MESMO pregão ATUALIZA o
  registro anterior (reusa `edital_id`, atualiza status/PDF, `DELETE`+reinsere
  os itens — CASCADE evita órfãos) em vez de duplicar. Sem identidade, cai no
  INSERT-novo (não funde pregões).
- **Órgão + número do pregão agora são OBRIGATÓRIOS** na UI: o fluxo não chega
  à geração da planilha sem ambos preenchidos (mensagem clara em PT). Isso
  garante que a identidade do UPSERT está sempre presente.
- **Sem backfill:** planilhas geradas ANTES desta mudança não aparecem no
  histórico (`exports_log` nasce vazio para editais pré-migração). Adivinhar
  arquivos por `glob` foi descartado — associar a planilha errada ao pregão
  errado é erro financeiro. O histórico vale a partir da adoção.

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

**Pré-requisito de operação — atendido (Q2):** o cliente já entregou
planilhas históricas e o catálogo foi populado (retrato atual: ~1.408
produtos). A Camada 0 dispara. Cobertura de código por fornecedor é
desigual — Almeida ~62%, Dantas ~9% (alavanca dependente de mais dado do
cliente); onde falta código, o pipeline cai na busca textual (Camadas 1+2
+ Camada 3).

**Pendente (fora do escopo desta entrega):**
- Página de dashboard de variação de preço por marca/fornecedor
- Telemetria visível ao operador (codigo_hits / llm_judge_hits) — campos
  já existem no `ResultadoLote`, falta exibir na UI

### Fase 4 — Inteligência tributária: markup por ST — ✅ entregue (variante markup)
Entregue a fatia que move o ponteiro do cliente: **markup por Substituição
Tributária (ST)**, por item, sem mexer nas colunas de imposto da matriz.

- ✅ `tributacao.py` — regra do contador: NCM consta na lista de ST do estado
  → markup MENOR (ICMS já recolhido na origem); senão markup normal. Match
  **hierárquico por prefixo** (posições de 4/6/8 dígitos): `eh_st`,
  `markup_para`, `classificar_markups`, `ncm_de_texto`, `ncm_de_card`.
- ✅ `pregoes/dados/ncm_st.json` — 389 NCMs de ST extraídos do Anexo 05 do
  RICMS/PB. Dado de **referência** versionado junto do código (bundla no .exe,
  não depende de banco). Degrada seguro: arquivo ausente → nada vira ST.
- ✅ `config.MARKUP_ST = 1.30` e `config.MARKUP_NORMAL = 1.45`.
- ✅ `exportador.py` aplica markup **POR ITEM** (param `markups` +
  `_aplicar_markup_em_linha`), sobre o markup global do pregão.
- ✅ `scraper.consultar_ncms()` busca o NCM na **página de detalhe** do produto
  (o card não traz NCM) reusando a URL capturada na busca.
- ✅ UI: painel **"Markup deste pregão"** + botão **"Identificar NCM"** na tela
  de busca de preços. O sistema classifica, mas o operador confere/ajusta os
  dois markups (Resp 7 — sistema nunca decide sozinho).
- Testes: `tests/test_tributacao.py`, `tests/test_fase4_exportador.py`.

**Fora do escopo desta entrega (decisão do cliente):** a Fase 4 "completa" —
importador de XML de NF-e (`lxml`), inferência por NCM do imposto já pago na
origem e preenchimento automático das colunas %ST/%IPI/%Frete via fórmula do
contador — NÃO foi feita. Fica como trabalho futuro caso o cliente peça.

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
