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
[extrator.py] ── pdfplumber + heurísticas de cabeçalho
     │
     ▼
[Streamlit UI] ── revisão humana (data_editor)
     │
     ├──> [db.py / SQLite] ── catálogo, histórico, mapeamentos
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

- ❌ Não usamos LLM externo (LangChain, OpenAI, etc.) — over-engineering
- ❌ Não usamos Docker — cliente não tem perfil técnico
- ❌ Não usamos microserviços — operador único, dor única, app único
- ❌ Não usamos ORM pesado (SQLAlchemy completo) — sqlite3 + raw SQL bastam
- ❌ Não criamos abstrações para "futuros fornecedores" — quando aparecer
  o terceiro fornecedor, refatoramos com contexto real

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
│   ├── db.py                  # Schema SQLite + conexão
│   ├── extrator.py            # PDF → ItemExtraido
│   ├── exportador.py          # ItemExtraido → Excel matriz
│   ├── normalizador.py        # (TODO fase 2)
│   ├── matcher.py             # (TODO fase 3)
│   ├── scraper.py             # (TODO fase 2)
│   ├── importador.py          # (TODO fase 3 — bootstrap histórico)
│   └── ui/
│       ├── __init__.py
│       └── app.py             # Streamlit
├── data/
│   ├── pregoes.db             # SQLite (criado em runtime)
│   ├── templates/
│   │   └── matriz_template.xlsx
│   ├── editais/               # PDFs originais
│   └── exports/               # Excels gerados
└── tests/
    ├── gerar_pdf_teste.py
    └── test_mvp.py            # 19 testes — caminho crítico todo coberto
```

## Schema SQLite

```
produtos              — catálogo canônico (cresce com uso)
precos_historico      — toda consulta com timestamp (alimenta cache TTL=2d)
editais               — cada PDF processado
edital_itens          — linhas dos editais; FK para produtos quando casa
mapeamentos_pdf       — heurísticas aprendidas para schemas não-padrão
```

## Próximas fases (em ordem)

### Fase 2 — Scraper Almeida + Dantas (3-5 dias)
- Playwright para navegar nos sites
- Busca em camadas: descrição exata → limpa → keywords → código
- Cache TTL=2 dias na `precos_historico`
- UI: tela de revisão com preço Almeida vs Dantas, seleção manual de marca
- Trigger: cliente confirma Q1 (amarelo vs vermelho)

### Fase 3 — Bootstrap, fuzzy matching, dashboard (2-3 dias)
- `importador.py` lê N planilhas históricas e popula catálogo
- `rapidfuzz` para casamento de descrições
- Página de dashboard: variação de preço por marca/fornecedor
- Trigger: cliente entrega 10-20 planilhas históricas

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
