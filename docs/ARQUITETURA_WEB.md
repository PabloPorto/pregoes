# Interface web nova — contrato de arquitetura

> Substitui o Streamlit (`pregoes/ui/`) com paridade TOTAL de funções
> (`docs/INVENTARIO_UI.md` é a lista do que existe hoje — nada pode sumir).
> O backend (`pregoes/*.py`) é reutilizado SEM alteração; os dados em `data/`
> são os mesmos (produção tem dados reais do cliente).

## Pilha e limites

- Starlette + Jinja2 + uvicorn (já instalados; **nenhuma dependência nova**,
  nada de CDN, npm, build). JavaScript puro em `static/app.js`.
- Um processo Python. Rotas `def` (síncronas — o Starlette roda em threadpool);
  `async def` só para ler `multipart` (`await request.form()`), e aí o trabalho
  bloqueante vai em `run_in_threadpool`.
- Tudo o que o operador lê em **português**, sem jargão, sem stacktrace
  (`erros.mensagem_amigavel(exc, "fazer tal coisa")`). Ícones: `m.icone('nome')`
  (SVG em `_macros.html`) — **sem emoji**.
- Visual: só classes de `static/app.css` (tokens em `:root`, tema claro/escuro).
  CSS específico de uma tela vai em arquivo PRÓPRIO `static/<tela>.css`,
  carregado em `{% block estilos %}`. Nunca cor fixa: use as variáveis.

## Arquivos do núcleo (NÃO editar fora do núcleo; precisa de mudança → relate)

| Arquivo | Papel |
|---|---|
| `web/app.py` | monta o app; importa `rotas` de cada `rotas_*.py` |
| `web/nucleo.py` | `pagina(request, modelo, secao=, **ctx)`, `pedaco(request, modelo, **ctx) -> str`, `ok(**campos)`, `falha(msg, status=400)`, `corpo_json(request)` (async), filtros Jinja `brl`, `num`, `quando`, `RAIZ` |
| `web/pregao.py` | `Pregao` (estado do pregão = rascunho em disco), `trava(slug)`, `em_andamento()`, `fornecedores_configurados()`, `titulo_de()` |
| `web/tarefas.py` | tarefas demoradas em thread: `iniciar(tipo, slug, titulo, fn)`, `ativa(tipo, slug)`, `do_pregao(slug)`, `Tarefa.atualizar(fracao, detalhe, **extra)`, `Tarefa.cancelamento_pedido` |
| `web/servicos.py` | operações do núcleo (leitura do PDF) |
| `templates/base.html`, `_macros.html`, `pregao_base.html`, `_tarefa.html` | moldura, peças, etapas do pregão, cartão de progresso |
| `static/app.css`, `static/app.js` | sistema visual e comportamento |
| `tests/conftest.py` | fixture `web` (cliente HTTP + pasta de dados vazia + `web.pregao(n_itens=)`) |

## Estado do pregão (ler `web/pregao.py` inteiro antes de escrever)

- A URL carrega o `slug`: `/pregao/<slug>/…`. `Pregao.abrir(slug)` → `None` se
  inválido/inexistente (responda 404 com `erro.html` ou `falha(..., 404)`).
- Toda alteração: `with trava(slug): p = Pregao.abrir(slug); …; p.salvar()`.
  Não existe estado de sessão no servidor — o que não está no rascunho não
  existe. Estado só de tela (aba aberta, filtro, página) vai na query string.
- Chaves do rascunho = as do Streamlit (inventário 3.2/3.3), mesmos tipos:
  `extracao, itens_edital, itens_revisados, mapa_busca_edital, resultados_busca,
  selecoes, itens_nao_usar, markup_overrides, pagina_busca, pregao_gerado_hash,
  avisos_lote_vazio, usando_tabelas_docling, orgao, numero, pdf_path, tela`.
  Chaves NOVAS permitidas (o Streamlit as ignora): `erros_busca`,
  `avisos_pagamento`, `margem_venda_col` não — margem é preferência global
  (`credenciais`). Não invente outras sem necessidade.
- **Índices:** `resultados_busca`, `selecoes`, `itens_nao_usar`,
  `markup_overrides` usam a posição em `itens_revisados`; a planilha usa a
  posição em `itens_edital`; ponte = `mapa_busca_edital`.

## Convenções de tela

- Tela de pregão: `{% extends "pregao_base.html" %}`, conteúdo em
  `{% block etapa %}`, passar `pregao=` e `etapa=` (1 Itens, 2 Preços, 3
  Planilha). Demais telas: `{% extends "base.html" %}` com `secao=`.
  Sempre `{% import "_macros.html" as m with context %}` no topo.
- Ações sem recarregar (ver `static/app.js`):
  - `<form action="/caminho" data-async>` → POST JSON com os campos;
    `enctype="multipart/form-data"` → envia como formulário.
  - `<button data-acao="/caminho" data-dados='{"k":1}' data-confirmar="Tem certeza?">`.
  - `<input name="campo" data-salvar="/caminho" data-dados='{"i":3}'>` salva ao mudar.
  - `data-modal="/caminho"` abre o HTML devolvido num `<dialog>`; `data-fechar` fecha.
  - `data-filtrar=".seletor"` filtra linhas no navegador.
  - Resposta das ações: `ok(aviso=, tipo=, ir=, recarregar=True, fechar_modal=True,
    tarefa=<id>, html={"#id": "<…>"})`. `html` troca o elemento `#id` pelo HTML
    devolvido (que precisa ter o MESMO id na raiz) — use `pedaco()` com um
    modelo `_parcial.html`. Erro: `falha("mensagem")`.
  - Caminhos no HTML/JS começam com `/` e SEM `{{ raiz }}` em `action`,
    `data-acao`, `data-salvar`, `data-modal`, `ir=` (o JS prefixa). Em `href` e
    `src` use `{{ raiz }}/…`.
- Tarefa demorada: `t = tarefas.iniciar(...)`; devolva `ok(tarefa=t.id, recarregar=True)`
  ou renderize `{% set t = retrato %}{% set ao_terminar = '/pregao/…' %}{% include "_tarefa.html" %}`.
  A função da tarefa recebe a `Tarefa`, chama `tarefa.atualizar(...)` e grava
  o resultado no rascunho sob `trava(slug)`. Uma tarefa nunca mexe em tela.
- Regras de negócio trazidas de `pregoes/ui/app.py` (inventário seção 6):
  copie como função PURA para `web/regras_<area>.py`, com a linha de origem no
  docstring. NÃO importe `pregoes.ui.app` no código do app (ele sobe o
  Streamlit). Nos TESTES pode importar para conferir paridade.
- Cada `rotas_<area>.py` expõe `rotas: list[starlette.routing.Route]`.
- Testes: `tests/test_web_<area>.py` com a fixture `web`. Sem rede: troque as
  funções do `scraper`/IA por dublês com `monkeypatch`. A suíte inteira
  (`.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`) tem de continuar verde.

## Donos dos arquivos

| Área | Arquivos |
|---|---|
| Revisão + mapeamento | `rotas_revisao.py`, `regras_revisao.py`, `templates/revisao*.html`, `templates/mapeamento*.html`, `static/revisao.*`, `tests/test_web_revisao.py` |
| Preços (busca + escolha) | `rotas_precos.py`, `servico_busca.py`, `regras_selecao.py`, `templates/precos*.html`, `static/precos.*`, `tests/test_web_precos.py` |
| Planilha (ST/NCM, margem, gerar) | `rotas_planilha.py`, `regras_planilha.py`, `templates/planilha*.html`, `static/planilha.*`, `tests/test_web_planilha.py` |
| Histórico, catálogo, configurações | `rotas_historico.py`, `rotas_catalogo.py`, `rotas_config.py`, `regras_historico.py`, `templates/historico*.html`, `templates/catalogo*.html`, `templates/config*.html`, `static/admin.*`, `tests/test_web_admin.py` |

Fronteiras entre áreas:
- Revisão → Preços: botão "Buscar preços" leva a `/pregao/<slug>/precos`.
  "Gerar sem buscar" leva a `/pregao/<slug>/planilha?sem_precos=1`.
- Preços → Planilha: botão "Continuar para a planilha" leva a
  `/pregao/<slug>/planilha`. A tela Planilha lê `selecoes` etc. do rascunho.
- Planilha → Histórico: o download usa `GET /historico/planilha/<export_id>`
  (rota da área Histórico; devolve o `.xlsx` de `config.EXPORTS_DIR`).
- Preços usa, por item, "Buscar no meu catálogo": a busca no catálogo
  (`regras_selecao.buscar_no_catalogo`) é da área Preços; a tela `/catalogo`
  (listar/importar) é da área Histórico/catálogo/config.
