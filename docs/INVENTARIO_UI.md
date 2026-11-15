# Inventário funcional da UI atual (Streamlit) — base para a reescrita

> Fonte: leitura integral de `pregoes/ui/app.py` (3937 linhas) e `pregoes/ui/style.py`
> (782 linhas), árvore de trabalho do branch `feat/servidor`, `config.VERSAO_APP = "1.1.16"`.
> Convenção: `L123` = linha de `app.py`; `S123` = linha de `style.py`. Rótulos entre aspas
> são as strings EXATAS da tela. Tudo que está aqui precisa existir na UI nova
> (Starlette + Jinja + JS), reusando o pacote `pregoes/` sem alteração.
> Onde o código diverge do que a documentação promete, está marcado **[ATENÇÃO]**
> e consolidado na seção 9.

## 0. Ciclo de execução (o que roda a cada render)

O Streamlit reexecuta o script inteiro a cada interação. Ordem (topo → fim do arquivo):

1. `st.set_page_config(page_title=config.TITULO_APP, page_icon="📋", layout="wide", initial_sidebar_state="expanded")` (L36) + `style.aplicar_estilo()` (L42).
2. `logconf.configurar()` (L47) — log em `data/pregoes.log`; logger `ui`.
3. `_abrir_banco()` (L51-69) → `db.init_db()` A CADA render. Falha → aviso danger "Não consegui abrir o banco de dados — ele pode estar corrompido. Restaure a cópia mais recente da pasta <b>data/backups</b> (renomeie para <b>pregoes.db</b>) e abra o app de novo. Se o problema persistir, fale com o suporte." + `st.stop()`.
4. Uma vez por sessão (`backup_feito`, L72-88): `db.backup("diario")`; se `not config.MODO_SERVIDOR`, thread daemon `backup.fazer_backup_externo()`.
5. Estado inicial (L91-99): `tela="home"`, `_sessao_nova=True`, `extracao=None`, `df_editavel=None`.
6. Se `_sessao_nova` (L3925): `_retomar_pregao_da_url()`.
7. `_renderizar_sidebar()` (L3927) → `_renderizar_tela()` (L3930) → `_salvar_rascunho()` (L3931, autosave) → `_marcar_pregao_na_url()` (L3932).
8. Se `_confirmar_novo_pregao` em sessão (L3936): abre o diálogo modal.

Fora de `app.py`, mas parte da experiência (a reescrita deve cobrir): `run_app.py`
força `<html lang="pt-BR" translate="no">` + `<meta name="google" content="notranslate">`
(o tradutor do Chrome quebrava a página) e neutraliza os atalhos de tecla "c"/"r" do
Streamlit; escuta só em `127.0.0.1:8501`; sem stacktrace na tela (`client.showErrorDetails=none`).
Modo servidor = env `PREGOES_SERVIDOR=1` (`config.MODO_SERVIDOR`); modo dev = env `PREGOES_DEBUG` (`_DEBUG`, L32).

## 1. Telas e navegação

### 1.1 Telas (`TELAS`, L3890-3898; valor de `st.session_state.tela`)

| `tela` | Função | Título (h1) | Como se chega |
|---|---|---|---|
| `home` | `tela_home` L410 | "Início" | boot; sidebar "Início"; fallback de tela desconhecida (L3908); "Ir para o Início" (erro); `tela_revisao` sem extração (L545); "Voltar para o início" (mapeamento sem tabelas) |
| `historico` | `tela_historico` L1127 | "Histórico de pregões" | sidebar "Histórico"; home "Ver pregões processados" |
| `upload` | `tela_upload` L473 | "Novo pregão" | sidebar "Novo pregão" / home "Iniciar novo pregão" / "Começar um novo pregão" (pós-geração) — sempre via `_novo_pregao()` L348 |
| `revisao` | `tela_revisao` L542 | "Revisão dos itens extraídos" | após extração (L538); "Voltar para revisão" (busca/mapeamento); "Aplicar mapeamento"; retomar rascunho sem busca |
| `mapeamento` | `tela_mapeamento` L3426 | "Mapeamento manual da tabela de itens" | botão "Mapeamento Manual" da revisão (só se `conf < 0.95` e há `tabelas_brutas`) |
| `busca_precos` | `tela_busca_precos` L1433 | "Busca de preços" | "Buscar preços" da revisão; retomar rascunho com busca/seleções |
| `configuracoes` | `tela_configuracoes` L1247 | "Configurações" | sidebar "Configurações" |

Não existem telas de dashboard nem de diagnóstico **[ATENÇÃO]** (ver 5.9 e 5.10).
`ir_para(tela)` (L185) = grava `tela` + `st.rerun()`. Não há histórico de navegação/"voltar" do navegador.

### 1.2 Sidebar (`_renderizar_sidebar`, L361-405) — sempre visível

- Marca (`style.sidebar_marca`, S622): logo "P", "Pregões", "Automação de formação de preços · v{config.VERSAO_APP}".
- Menu (botões `nav_<tela>`, largura total): "Início" → `home`; "Histórico" → `historico`; "Novo pregão" → `_novo_pregao()`; "Configurações" → `configuracoes`. Botão fica `primary` só quando `tela == tela_id` EXATO; em `revisao`/`busca_precos`/`mapeamento` nenhum acende.
- Bloco "Status" (`style.sidebar_status`, S638): "Almeida" e "Dantas" → "Configurada" (success) se `credenciais.carregar(nome)` senão "Pendente" (warning); "IA (Gemini)" → "Configurada" (success) se `credenciais.carregar_chave_api("gemini")` senão "Opcional" (neutro).

### 1.3 Stepper (`style.stepper(PASSOS_FLUXO, atual)`, S513; `PASSOS_FLUXO = ["Edital","Itens","Preços","Planilha"]`, L217)

| Tela | `atual` | Regra |
|---|---|---|
| `upload` | 0 | L474 |
| `revisao` | 1 | L548 |
| `busca_precos` | 2, ou 3 | 3 quando `pregao_gerado_hash is not None and == _hash_pregao()` (L1435-1437) |
| `mapeamento`, `home`, `historico`, `configuracoes` | sem stepper | — |

Só apresentação: passos anteriores "✓" (done), atual numerado/destacado, seguintes pendentes; não é clicável.

### 1.4 Parâmetro de URL

Único parâmetro: `?pregao=<slug>` (`_PARAM_PREGAO`, L2309).
- **Escrita** (`_marcar_pregao_na_url`, L2313, fim de TODO render): se há `_rascunho_slug` e a URL difere → grava; se não há slug e a URL tem o parâmetro → remove.
- **Leitura** (`_retomar_pregao_da_url`, L2324, só no 1º render de sessão nova): valida com `_RE_SLUG = [a-z0-9-]{1,80}` (`fullmatch`) ANTES de virar caminho de arquivo; `rascunho.carregar_pregao(slug)`; achou → `_restaurar_rascunho(estado, slug)` + log "sessão nova: pregão '%s' retomado pela URL (aba recarregada)"; inválido/inexistente → remove o parâmetro e fica no Início.
- A TELA não vai na URL: F5 com pregão carregado cai sempre na tela de trabalho calculada (`_tela_pregao_em_andamento`), mesmo que o operador estivesse em Configurações/Histórico; F5 sem pregão cai no Início. Aba nova (URL limpa) → Início.
- O slug nasce no 1º autosave e some em `_limpar_pregao`/`_limpar_rascunho` (inclusive logo após gerar a planilha — ver 6.6).

### 1.5 "Novo pregão" e diálogo de confirmação

`_novo_pregao()` (L348): se `_tem_pregao_em_andamento()` → flag `_confirmar_novo_pregao` + rerun; senão `_limpar_pregao()` + `ir_para("upload")`.

Diálogo modal `@st.dialog("Começar um novo pregão?")` (`_confirmar_novo_pregao`, L310-345):
- Texto (warning): "Você tem um pregão em andamento. Você pode **guardá-lo para continuar depois** (ele fica na lista de pregões guardados na tela inicial) ou descartá-lo de vez."
- Botões EMPILHADOS, nesta ordem:
  1. "Guardar e começar outro" (primary; help "O pregão atual fica na lista da tela inicial, para continuar depois exatamente de onde parou.") → remove flag; `pop("_rascunho_assin")` (força gravar); `_salvar_rascunho()`; `_limpar_pregao(preservar_rascunho=True)`; toast "Pregão guardado — retome pela tela inicial."; `ir_para("upload")`.
  2. "Continuar o atual" → remove flag; `ir_para(_tela_pregao_em_andamento())`.
  3. "Descartar e começar novo" → remove flag; `_limpar_pregao()` (apaga o arquivo do rascunho); `ir_para("upload")`.

### 1.6 Rede de segurança do roteador (`_renderizar_tela`, L3900-3922)

Qualquer `Exception` numa tela → log `erro inesperado na tela '%s'` + aviso danger
"{erros.mensagem_amigavel(exc, 'mostrar esta tela')}<br><b>Seu trabalho está salvo.</b> Se o erro repetir, vá ao Início e use <b>Continuar</b> no pregão."
+ botões "Tentar de novo" (primary; rerun) e "Ir para o Início" (`ir_para("home")`). O autosave roda depois (L3931).

## 2. Por tela, cada elemento interativo

### 2.1 Início (`tela_home`, L410-440)

Subtítulo: "Visão geral dos pregões processados e atalho para um novo."

| Elemento | Rótulo / conteúdo | Ação / backend |
|---|---|---|
| Lista de pregões guardados | `_banner_rascunhos()` (L2374) — ver abaixo | `rascunho.listar_pregoes()`, `db.pregao_finalizado` |
| 3 métricas | "Pregões registrados" = `SELECT COUNT(*) FROM editais`; "Em rascunho" = contagem `status='rascunho'`; "Precificados" = `status='precificado'` (L420-429) | SQL direto via `db.get_conn()` |
| Botão primário | "Iniciar novo pregão" | `_novo_pregao()` |
| Botão | "Ver pregões processados" | `ir_para("historico")` |

**[ATENÇÃO]** "Em rascunho" conta registros da tabela `editais` gerados SEM preço (status `rascunho`), não os rascunhos em disco.

**Pregões guardados** (`_banner_rascunhos`, L2374-2436) — aparece sempre que há pendentes, inclusive com um pregão carregado:
- Fonte: `rascunho.listar_pregoes()` (mais recente primeiro; cada item `{slug, orgao, numero, n_itens, tem_busca, mtime}`) filtrada por `_filtrar_rascunhos_finalizados` (L2342): esconde quem já tem export (`db.pregao_finalizado(conn, orgao, numero)`); NUNCA apaga arquivo; erro de banco → lista original.
- Aviso info: "<b>{n} pregão guardado</b>" / "<b>{n} pregões guardados</b>" + " — continue de onde parou ou descarte."
- Mostra só os 8 primeiros (`pendentes[:8]`, L2393) — sem paginação nem "ver mais".
- Card por pregão: título "Pregão {numero} — {orgao}" (partes ausentes omitidas; ambos vazios → "Pregão sem identificação"); detalhe cinza "{n} item|itens" + " · busca de preços feita" (se `tem_busca`) + " · em andamento" (se `slug == _rascunho_slug`).
- Botões: "Continuar" (primary) → `rascunho.carregar_pregao(slug)`; ok → `_restaurar_rascunho(estado, slug)`; corrompido → `rascunho.remover_pregao(slug)`; rerun. "Descartar" → liga `rasc_conf_del_{slug}` → troca os botões por "Sim, descartar" (primary) / "Cancelar". "Sim, descartar": se é o pregão ATIVO → `_limpar_pregao()` (senão o autosave recria o arquivo); senão `rascunho.remover_pregao(slug)`.

### 2.2 Novo pregão / upload (`tela_upload`, L473-538)

Subtítulo: "Envie o PDF do edital para extração automática dos itens."

| Elemento | Rótulo | Detalhe |
|---|---|---|
| Uploader | "Arraste o PDF aqui ou clique para escolher" | `type=["pdf"]`, 1 arquivo |
| Texto | "Órgão" | placeholder "Ex: Prefeitura Municipal de Araruna" |
| Texto | "Número do pregão" | placeholder "Ex: 00006/2026" |
| Botão primário | "Extrair itens do PDF" | só aparece com arquivo enviado |

Ao clicar (L494-538):
1. Órgão ou número vazio (após `strip`) → danger "Informe o órgão e o número do pregão para continuar." e para.
2. Spinner "Lendo PDF e detectando tabelas...": grava o PDF em `config.EDITAIS_DIR` com nome aleatório (`tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")` — o nome original se perde; o arquivo nunca é apagado) e chama `extrator.extrair_com_pipeline(tmp_path)`.
3. Exceção → log + danger `erros.mensagem_amigavel(exc, "ler o PDF")`.
4. Se `resultado.confianca_global < 0.95 and resultado.tabelas_brutas` → `_tentar_mapa_aprendido(resultado, conn, str(tmp_path))` (L443).
5. `_limpar_pregao()` (zera TUDO do pregão anterior, inclusive apaga o rascunho dele) → grava `extracao`, `df_editavel=None`, `orgao`, `numero`, `pdf_path` → `ir_para("revisao")`.

### 2.3 Revisão (`tela_revisao`, L542-747)

Sem `extracao` → `ir_para("home")`. Subtítulo: "Confira o que foi lido do PDF, edite o que precisar e marque o que entra na planilha."

| Elemento | Rótulo exato | Condição / ação |
|---|---|---|
| Texto (`rev_orgao`) | "Órgão" | editável; regrava `session_state.orgao` a cada render (L561) |
| Texto (`rev_numero`) | "Número do pregão" | idem `numero` (L565) |
| Aviso de confiança | `style.confianca_para_badge(conf)` (S608): ≥0,95 "Alta confiança · NN%" (success) + " — pode seguir para precificar."; ≥0,80 "Confiança média · NN%" (warning) + " — revise com atenção antes de gerar a planilha."; >0 "Baixa confiança · NN%" (danger) + " — recomendado usar o mapeamento manual."; 0 "Sem itens detectados" (danger) | sempre |
| 4 métricas | "Total de itens" (`res.total_itens`), "OK" (total − duvidosos), "Duvidosos" (`res.total_duvidosos`, delta "conferir" se >0), "Tabelas detectadas" (`len(res.tabelas_brutas)`) | sempre |
| Avisos da extração | `style.bloco_avisos(res.avisos, "warning")` (um box por aviso) | se `res.avisos` |
| Aviso info + botão "Mapeamento Manual" | "A confiança automática ficou abaixo de 95%. Você pode usar o <b>Mapeamento Manual</b> para indicar qual é a tabela de itens e o que é cada coluna. O sistema aprende e reaproveita o mapeamento em próximos editais com o mesmo formato." | se `conf < 0.95 and res.tabelas_brutas` → `ir_para("mapeamento")` |
| Aviso danger (PDF escaneado) | "Este PDF parece ser <b>escaneado</b> (imagem), sem texto que o sistema consiga ler. Peça ao órgão o edital em PDF com texto (ou copie a tabela de itens para um PDF/planilha). Leitura de imagem (OCR) ainda não é suportada." | `total_itens == 0` e `not extrator.tem_texto_extraivel(pdf_path)`; a tela PARA aqui |
| Aviso danger (sem itens) | "Nenhum item foi extraído automaticamente. Se o PDF tem uma tabela de itens visível, use o <b>Mapeamento Manual</b> acima." | `total_itens == 0` e PDF tem texto; a tela PARA aqui |
| Legenda | "Confira os itens abaixo. Você pode editar qualquer célula clicando nela. Linhas em amarelo precisam de atenção." | **[ATENÇÃO]** nenhum código colore linhas |
| Botões | "Marcar todos" / "Desmarcar todos" | `_marcar_todos(valor)` L659: funde edições pendentes (`_aplicar_delta_editor` + `_fundir_edicoes`), seta coluna OK, `editor_version += 1`, rerun |
| Tabela editável (`st.data_editor`, key `editor_{editor_version}`, altura 500, linhas fixas, sem índice) | colunas: "Buscar preço?" (checkbox, campo `OK`, default True; help "Marque os itens cujo preço o sistema deve buscar nos fornecedores. TODOS os itens do edital vão para a planilha — os desmarcados ficam sem preço, para preencher à mão."), "Item" (`%d`), "Descrição" (larga), "Unidade", "Quant" (`Quantidade`, `%.2f`), "Ref. (R$)" (`Valor referência`, `%.2f`) | todas as células editáveis; não dá para incluir/excluir linhas |
| Contador | "**{n_marcados}** de **{total}** itens marcados para **buscar preço** · todos os **{total}** vão para a planilha (os demais sem preço)." | recalculado do estado atual do editor |
| Botão primário | "Buscar preços" (help "Busca o preço dos itens MARCADOS em Almeida e Dantas (qualquer um configurado). Pode demorar minutos.") | sem marcados → danger "Nenhum item marcado para buscar preço — marque ao menos um."; sem credencial de nenhum fornecedor → danger "Nenhum fornecedor configurado. Acesse <b>Configurações</b> para cadastrar Almeida e/ou Dantas."; senão `ir_para("busca_precos")` |
| Botão | "Gerar sem buscar" (help "Gera a planilha com o EDITAL INTEIRO, sem preços (todas as colunas de preço/código/marca em branco, para preencher à mão).") | `_gerar_excel(_df_para_itens(edited), markup=config.MARKUP_DEFAULT, selecoes={})` — bloco de resultado (2.6) renderiza dentro da coluna direita |

A cada render (L721-724) a tela regrava `itens_revisados`, `itens_edital`, `mapa_busca_edital` a partir do editor (`_itens_revisao(edited)`).
O DataFrame inicial (L633-641) vem de `res.itens`, com `OK = (item.confianca == "extraido")` — itens duvidosos nascem DESMARCADOS.

### 2.4 Mapeamento manual (`tela_mapeamento`, L3426-3607)

Sem `extracao`/`tabelas_brutas` → danger "Nada para mapear — volte e envie um PDF primeiro." + botão "Voltar para o início".
Subtítulo: "Quando a extração automática não acerta, você indica qual é a tabela certa e o que é cada coluna. O sistema aprende para os próximos editais."

| Elemento | Rótulo | Ação / backend |
|---|---|---|
| Botão | "Voltar para revisão" | `ir_para("revisao")` |
| Expander | "Não encontrou a tabela certa? Carregar com extração profunda (Docling)" | só se `extrator_docling.disponivel()` e `not usando_tabelas_docling`; legenda: "Docling usa um modelo de visão para detectar tabelas que o pdfplumber não viu. Funciona melhor em PDFs com layout complexo, mas demora **5-10 minutos** por edital. Use só quando a tabela certa não está na lista abaixo." |
| Botão (dentro) | "Carregar tabelas com Docling" (só com `pdf_path`) | spinner "Rodando Docling… pode demorar alguns minutos." → `extrator_docling.coletar_tabelas_brutas_docling(pdf_path)`; erro → danger "Docling falhou: {exc}"; ok → substitui `res.tabelas_brutas`, `usando_tabelas_docling=True`, zera `mapeamento_tabela_idx`, success "Docling detectou {n} tabelas. Escolha abaixo.", rerun |
| Aviso info | "Mostrando tabelas detectadas pelo Docling (extração profunda)." | quando `usando_tabelas_docling` |
| **Passo 1** — "Passo 1 — Qual das tabelas é a de itens?" | selectbox "Tabelas detectadas no PDF" (key `mapeamento_tabela_idx`) | opções: "Tabela #{i} (vazia)" ou "Tabela #{i} · {n_cols} colunas · {n_linhas} linhas · {preview}" (preview = 1ª linha não vazia, até 6 células × 25 chars, máx. 120) |
| Aviso | "Tabela vazia — escolha outra." | tabela escolhida vazia (para) |
| Pré-visualização | "**Pré-visualização da Tabela #{i}** (primeiras 5 linhas):" + tabela somente leitura com colunas "Col 1..N" | — |
| **Passo 2** — "Passo 2 — O que é cada coluna?" | legenda "A primeira linha do PDF acima é o cabeçalho — não vai virar item. Para cada coluna, escolha o campo correspondente." | — |
| Aviso success | "Encontrei um mapa salvo desta assinatura de cabeçalho — os campos abaixo já vieram preenchidos. Ajuste se precisar." | se `mapeamento_manual.carregar_mapa(conn, assinatura_cabecalho(tabela[0]))` |
| N selectboxes (grade de até 3 colunas; keys `map_col_{c}`) | "Coluna {c+1} — `{cabeçalho[:30]}`" | opções `mapeamento_manual.CAMPOS` = ignorar, descricao, quantidade, unidade, valor_referencia, codigo_orgao; rótulos `ROTULOS_CAMPOS`: "— ignorar —", "Descrição", "Quantidade", "Unidade", "Valor de referência", "Código (item nº)"; default = campo do mapa salvo ou "ignorar" |
| Aviso | "⚠️ '{rótulo}' já está em outra coluna." | campo repetido (a última coluna vence em `mapa_atual`) |
| **Passo 3** — "Passo 3 — Validar e aplicar" | danger "Mapeamento incompleto — faltam: <b>{…}</b>." | faltando `descricao` e/ou `quantidade` (obrigatórios); para |
| Checkbox | "Salvar este mapeamento para reuso automático em próximos editais com o mesmo formato" | default marcado |
| Botão primário | "Aplicar mapeamento" | `mapeamento_manual.aplicar_mapa_em_lote(res.tabelas_brutas, idx_tabela, mapa_atual, arquivo=res.arquivo)`; se checkbox: `mapeamento_manual.salvar_mapa(conn, assinatura, mapa_atual)` + success "Mapeamento salvo. Próximos PDFs com este cabeçalho usam automaticamente. Itens extraídos: <b>{n}</b>, confiança: <b>{NN%}</b>." (some no rerun); grava `extracao=novo_res`, `df_editavel=None`, `ir_para("revisao")` |

### 2.5 Busca de preços (`tela_busca_precos`, L1433-1579)

Subtítulo: "Escolha a marca e o preço de cada item."

Guardas (nesta ordem):
- Sem `itens_revisados` → danger "Nenhum item para buscar. Volte para a revisão." + botão "Voltar para revisão".
- Nenhum fornecedor com credencial → danger "Nenhum fornecedor configurado. Vá em <b>Configurações</b> para cadastrar Almeida e/ou Dantas." (para).
- `_mostrar_condicao_pagamento(fornecedores)` (L2158): badges neutras "{Fornecedor}: {forma_texto}[ · {prazo_texto}]" para quem tem condição salva; para quem não tem, warning "Sem condição de pagamento configurada para <b>{A, B}</b> — o preço vem da condição <b>default</b> do site (pode não bater com a sua). Configure em <b>Configurações</b>, na aba do fornecedor."

**A) Pré-busca** (sem `resultados_busca` na sessão):

| Elemento | Rótulo | Ação |
|---|---|---|
| 3 cards (`style.resumo_pre_busca`) | "Itens a buscar" = `len(itens)`; "Fornecedores" = "Almeida + Dantas" (os configurados); "Cache de preço" = "{config.CACHE_PRECO_DIAS} dias" | — |
| [dev] checkbox | "[dev] Salvar diagnóstico (XLSX)" | só `_DEBUG`; passa `salvar_diagnostico=True` |
| [dev] checkbox | "[dev] Limpar cache antes" | só `_DEBUG`; `db.cache_limpar(conn)` antes da busca |
| Botão primário | "Iniciar busca" | `_executar_busca(itens, fornecedores_disponiveis, salvar_diagnostico=…)` + rerun |
| Botão | "Voltar para revisão" | `ir_para("revisao")` (não descarta nada) |

**B) Pós-busca** (há `resultados_busca`), de cima para baixo:

| Elemento | Rótulo | Condição / ação |
|---|---|---|
| [dev] download | "Baixar relatório de diagnóstico (XLSX)" + legenda com caminho `data/diagnostico/{nome}` | `_DEBUG` e `diag_path` existe |
| Avisos warning | valores de `avisos_pagamento` (ver 6.4) | condição de pagamento não confirmada |
| Avisos warning | itens de `avisos_lote_vazio` (lote vazio + recusa 405; ver 6.4) | — |
| Botão (key `rebusca_faltantes`) | "Buscar preço do 1 item sem resultado" / "Buscar preços dos {n} itens sem resultado" | `faltantes = [i for i in range(len(itens)) if not resultados.get(i) and i not in itens_nao_usar]` (L1502); → `_executar_busca(itens, fornecedores, indices=faltantes)` + rerun (incremental: mantém resultados e escolhas) |
| Expander | "A busca teve problemas? Re-buscar ou voltar" | legenda: "Os dois caminhos descartam os resultados e as marcas já escolhidas nesta tela — re-buscar é rápido: itens que já têm preço respondem da memória (2 dias) e só os sem resultado são tentados outra vez." |
| Botão (dentro) | "Buscar preços novamente" | remove `resultados_busca`, `selecoes`, `pagina_busca` → volta à pré-busca |
| Botão (dentro) | "Voltar para a revisão" | remove as mesmas 3 chaves → `ir_para("revisao")` |
| Lista de seleção | `_mostrar_selecao(itens, resultados_busca)` | ver 2.5.1 |

**[ATENÇÃO]** os dois botões destrutivos NÃO limpam `itens_nao_usar`, `markup_overrides` nem as buscas manuais por item — todos indexados pela posição do item.

#### 2.5.1 Lista de seleção (`_mostrar_selecao`, L3076-3373)

- Na 1ª exibição (sem `selecoes` na sessão) cria a seleção default de cada item com opções (`_melhor_opcao`, 6.3).
- Métricas com cor (`style.linha_metricas`, L3118): "Itens" (total, neutro); "Com preço" (com resultado − por variação, success; dica "Casamento exato com a descrição do edital"); "Por variação" (itens com alguma opção `confianca=="variacao"`; warning se >0; dica "Encontrado, mas por busca aproximada — conferir"); "Sem resultado" (danger se >0; dica "Sem preço em nenhum fornecedor — use a re-busca ou a busca manual").
- Expander "Ver erros técnicos ({n})" (só com `erros_busca`): legenda "Erros isolados durante a busca — esses itens ficaram sem preço pelo fornecedor que falhou. Verifique se o site mudou de layout ou se a sessão expirou."; até 50 linhas "- Item **#{indice_item+1}** ({fornecedor}) — `{descricao_resumo}`: {mensagem}"; acima de 50: "... e mais {n} erros."
- **Paginação**: 20 itens por página (`POR_PAGINA`, L3153); `pagina_busca` é limitado ao intervalo válido; navegação NO FIM da lista e só com >1 página: "◀ Anterior" (desabilitado na 1ª), legenda "Página {p} de {n} — itens {ini}–{fim}", "Próxima ▶" (desabilitado na última).
- **Item** = expander FECHADO por padrão. Cabeçalho: ícone de status + "**Item {nº do edital}** · {título legível} · {status colorido}" + (se há escolha e não está "não usar") " · ✓ {marca[:16] ou 'sem marca'} · {preço R$}" em azul.
  - Status (L3184-3195): "sem preço (você marcou)" (cinza, ícone block) se em `itens_nao_usar`; "Não encontrado" (vermelho, cancel) sem opções; "{n} a conferir" (laranja, help) se alguma opção visível é `variacao`; "{n} opção"/"{n} opções" (verde, check_circle).
  - Dentro: caixa cinza com a descrição COMPLETA e literal do edital (escapada).
  - Sem opções: warning "Nenhum resultado nos fornecedores nesta busca. Use a <b>Buscar no meu catálogo</b> abaixo, ou deixe em branco (a coluna de preço fica vazia para preencher manualmente)."
  - Com opções: legenda "Sugerimos a melhor opção — confira e confirme:" + card em DESTAQUE (a opção escolhida; se nenhuma, a 1ª do ranking) + expander "Ver outra opção" / "Ver outras {n} opções" (+ "  (+{k} menos relevantes)" quando há opções além do corte `config.MAX_OPCOES_EXIBIDAS = 4`).
  - **Card de opção** (`_card_opcao`, L2484): marca em negrito (ou "Sem marca") + pílula; linha cinza "{descrição encontrada[:72]…} · {Fornecedor} · cód. {código}"; preço à direita (`_brl`); ação: "✓ escolhido" (texto azul) ou botão "Escolher" (`esc_{idx}_{pos}`) → `_escolher_opcao(idx, o)` + rerun. Card escolhido tem fundo/acento (classe `ppoptsel-*`).
  - Pílulas (`_badge_de`, L3257; prioridade): "✓ sua escolha anterior" (`veio_por_aprendizado`), "✓ no seu catálogo" (`no_catalogo`), "Melhor match" (posição 0 do ranking), "Mais barato" (preço == mínimo das visíveis), nenhuma.
  - Expander "Não é essa? Procurar de outro jeito": seletor segmentado "Onde procurar este item?" com "Meus produtos" (default) e "Site do fornecedor" (key `fonte_busca_{idx}`) — mostra UMA busca por vez (2.5.2 / 2.5.3).
  - "Não usar": botão "Não usar este item (deixar sem preço)" → adiciona a `itens_nao_usar`, remove `selecoes[idx]`, rerun. Quando marcado: info "Este item ficará <b>sem preço</b> na planilha (em branco)." + botão "Voltar a usar este item" → só remove do conjunto (NÃO recria a seleção; operador escolhe de novo).
- Rodapé: legenda "{selecionados} de {total} itens com marca/preço selecionados." → painel de ST (2.5.4) → seletor de margem (2.5.5) → botão primário "Gerar planilha matriz com preços" → `_registrar_aprendizado(itens_revisados, selecoes, resultados_busca)` e depois `_gerar_excel(itens_revisados, markup=session.get("markup", config.MARKUP_DEFAULT), selecoes, markups, itens_com_st)`.

#### 2.5.2 Busca manual no catálogo (`_busca_catalogo_item`, L2839; `_usar_do_catalogo`, L2905)

- Catálogo carregado 1× por sessão (`matcher.carregar_catalogo(conn)` → `catalogo_lista`). Vazio → legenda "Catálogo interno vazio — importe suas planilhas históricas em Configurações para usar esta busca."
- Texto "Procurar por palavras ou código" (key `catq_{idx}`; placeholder "ex.: tubo pvc 75  ·  válvula lavatório  ·  8811"; help "Busque por palavras (acha quem contém todas) ou pelo código do fornecedor. Nos produtos que você já comprou."). Vazio → "Digite palavras (ex.: 'tubo pvc 75') ou um código para procurar nos seus {N} produtos."
- Busca ao confirmar o texto (Enter / sair do campo), sem botão: `_buscar_no_catalogo(catalogo, consulta)` (limite 6; 6.5). Nada → "Nenhum produto encontrado no seu catálogo. Tente outras palavras ou o código do fornecedor."
- Com achados: legenda "O preço abaixo é só **referência da última compra**. Ao usar, o sistema busca o **preço atual** no site do fornecedor pelo código (preço muda todo dia)." Uma linha por (produto × fornecedor que tem código): "**{marca|Sem marca}** · {descrição[:58]}" / "{Fornecedor} · cód. {código} · referência: R$ {preço} (últ. compra {AAAA-MM-DD})" ou "sem referência de preço" (`db.ultima_cotacao(conn, produto_id, forn)`), botão "Usar (preço atual)".
- "Usar (preço atual)": spinner "Buscando o preço atual do código {codigo} na {Fornecedor}…" → `scraper.cotar_por_codigo(forn, codigo)`:
  - exceção → warning "Não consegui consultar o preço atual agora ({mensagem_amigavel}). O preço do catálogo <b>não</b> é usado. Tente de novo em instantes."
  - `None` → warning "O código <code>{codigo}</code> não foi encontrado agora no site da {Fornecedor} — o produto pode ter saído de linha. Confira diretamente no site antes de usar."
  - ok → cria `ResultadoBusca(confianca="exato", no_catalogo=True, produto_catalogo_id, descricao_catalogo, preço/marca/url do site, codigo_fornecedor=codigo)`, ANEXA a `resultados_busca[idx]` e grava `selecoes[idx]` (só `preco`, `codigo_fornecedor`, `marca`, `fornecedor` — **sem `ncm`/`url`**), rerun.

#### 2.5.3 Busca manual no site (`_busca_site_item`, L2953; `_usar_do_site`, L3041)

- Sem credenciais → "Configure as credenciais dos fornecedores em Configurações para buscar direto no site."
- Texto "Nome ou código do produto" (key `siteq_{idx}`; placeholder "ex.: alicate de pressão  ·  8811"; help "Busca AO VIVO no site, sem filtro — você vê o que o site devolve e escolhe. O preço é o atual do site."); rádio horizontal "Fornecedor" (só os configurados, capitalizados; key `siteforn_{idx}`); botão "Buscar no site ao vivo" (key `sitego_{idx}`).
- Clique sem termo → "Digite um nome ou código para buscar." Com termo: spinner "Buscando “{termo}” na {Fornecedor}…" → `scraper.buscar_no_site(forn, termo)` (limite 10, sem filtro de relevância); resultado fica em `site_res_{idx} = (forn, achados)`.
  - exceção com `exc.status == scraper._STATUS_LIMITE` (429) → warning "O site da {Fornecedor} limitou as consultas por alguns minutos (muitas buscas em pouco tempo). <b>Aguarde uns 5 minutos</b> e busque de novo."
  - outra exceção → warning "Não consegui buscar no site agora ({mensagem_amigavel}). Tente de novo em instantes."; ambos limpam `site_res_{idx}`.
- Vazio → "Nada encontrado na {Fornecedor} para esse termo. Tente outras palavras ou o código. Se o produto deveria existir, o site do fornecedor pode estar instável neste momento — tente de novo em alguns minutos."
- Com resultados: "{n} resultado(s) na {Fornecedor} — **preço atual do site**. Clique em **Usar** no escolhido."; linhas "**{marca|Sem marca}** · R$ {preço}" / "{descrição[:64]} · cód. {código}" + botão "Usar" → `_usar_do_site`: anexa a `resultados_busca[idx]`, grava `selecoes[idx]` (com `ncm`, `url`, `fornecedor`), apaga `site_res_{idx}`, rerun. A escolha é aprendida ao gerar a planilha (6.7).

#### 2.5.4 Painel "Substituição Tributária deste pregão" (`_painel_markup`, L2657-2735)

- Título "#### Substituição Tributária deste pregão"; legenda "O preço de venda é calculado na planilha (3 cenários de margem). Aqui você só confirma os itens com ST — eles saem marcados 'SIM' na coluna correspondente e a planilha ajusta o imposto sozinha."
- Resumo: "{normal} normais  ·  {st} com Substituição Tributária  ·  {sem_ncm} sem NCM" (`tributacao.classificar_markups(ncms, MARKUP_ST, MARKUP_NORMAL, overrides)`).
- Botão "Identificar NCM e classificar ({n} itens)" (key `btn_ncm`) — só se há selecionados sem NCM COM `url` → `_identificar_ncm(selecoes, fetchaveis)` + rerun (4.3).
- Expander "{n} itens sem NCM — confira o markup" (ícone warning): por item, rádio horizontal rotulado com a descrição (54 chars; fallback "Item {off+1}") e opções "Normal" / "Com Substituição Tributária" (key `ov_markup_{off}`) → grava `markup_overrides[off] = MARKUP_ST | MARKUP_NORMAL`.
- Aviso info do desconto Dantas (se há seleções Dantas): "<b>Desconto Dantas:</b> {n} item escolhido é|itens escolhidos são da Dantas e sairá|sairão com {pct}% de desconto no custo, já incluído na planilha." (`exportador.eh_dantas`, `config.DESCONTO_DANTAS`).
- Devolve `(markups, com_st)`; `com_st = tributacao.com_st_de_ncms(...)`.

#### 2.5.5 Margem do preço de venda (`_seletor_margem_venda`, L2738-2771)

- Título "#### Margem do preço de venda"; legenda "A planilha já sai com o PREÇO VENDA preenchido nesta margem (você pode alterar célula a célula no Excel). Fica salvo como seu padrão para os próximos pregões."
- Selectbox "Margem aplicada ao preço de venda" (key `sel_margem_venda`): opções = chaves de `config.MARGENS_VENDA` (`Z`=15%, `AA`=12%, `AB`=7%), exibidas como "15%", "12%", "7%"; valor inicial = sessão → `credenciais.carregar_margem_venda()` → `config.MARGEM_VENDA_PADRAO` ("AB").
- Grava `margem_venda_col`; persiste (`credenciais.salvar_margem_venda`) só quando difere do padrão efetivo.

### 2.6 Geração da planilha (`_gerar_excel`, L788-937) — bloco inline, não é tela

Chamado por "Gerar sem buscar" (revisão) e "Gerar planilha matriz com preços" (busca). Saídas na tela:

| Situação | Mensagem |
|---|---|
| Sem itens | danger "Nenhum item no edital — nada para exportar." |
| Órgão/número vazio | danger "Informe o órgão e o número do pregão antes de gerar a planilha — eles identificam o pregão no histórico. Volte e preencha os dois campos." |
| Em andamento | spinner "Gerando planilha matriz a partir do template..." |
| `exportar` falhou | danger `erros.mensagem_amigavel(exc, "gerar a planilha")` |
| Histórico falhou (planilha ok) | warning "A planilha foi gerada — baixe abaixo. Só não consegui registrá-la no <b>histórico</b> agora; gere de novo mais tarde para completar." |
| Sucesso | success "Planilha gerada: <code>{arquivo}</code>"; com seleções: legenda "Inclui preço/marca/código para {n} de {total} itens." e, se há Dantas, "Desconto Dantas de {pct}% aplicado no custo de {n} item/itens da Dantas." (singular/plural) |
| Download | botão primário "Baixar planilha matriz" (bytes do `.xlsx`, `file_name=destino.name`, MIME xlsx) |
| Saída | legenda "Pregão concluído e salvo no histórico. Pode baixar a planilha acima ou começar o próximo." + botão "Começar um novo pregão" (key `novo_apos_gerar`) → `_novo_pregao()` |

O bloco existe só no render do clique (some no rerun seguinte); a planilha continua baixável pelo Histórico.

### 2.7 Histórico (`tela_historico`, L1127-1243)

Subtítulo: "Pregões processados e suas planilhas. Baixe de novo ou exclua."
- Consulta: `editais LEFT JOIN exports_log`, `GROUP BY e.id ORDER BY e.criado_em DESC` (L1144). Vazio → info "Nenhum pregão processado ainda. Comece em <b>Novo pregão</b>."
- Filtro: texto sem rótulo visível (label "Buscar pregão", key `filtro_hist`, placeholder "Filtrar por órgão ou número do pregão…") — substring case-insensitive sobre o RÓTULO inteiro (`_rotulo_pregao`, inclui status e data). Sem resultado → info "Nenhum pregão encontrado para “{filtro}”."
- Sem paginação: lista todos.
- Card por pregão: "**{rótulo}**" (`_rotulo_pregao`, 6.8) + legenda "{n} planilha(s) · {n_itens} itens · {n_selecoes} com preço/marca" (dados do export MAIS RECENTE) ou "Nenhuma planilha registrada — gere de novo a partir do pregão."
- Ícones inline à direita:
  - lápis (help "Editar nome do pregão") → alterna `hist_edit_{id}` (fecha a confirmação de exclusão) → formulário "Editar identificação do pregão": "Órgão", "Número do pregão", "Salvar" (primary → `_atualizar_pregao(id, org, num)`; vazio vira NULL, sem validação) / "Cancelar".
  - download (help "Baixar planilha") → baixa o export MAIS RECENTE lendo `config.EXPORTS_DIR / arquivo` do disco (nunca re-exporta); arquivo ausente → botão desabilitado com help "Arquivo não está na pasta — gere de novo a partir do pregão." (ou "Sem planilha para baixar." sem export).
  - lixeira (help "Excluir pregão") → alterna `conf_excl_{id}` (fecha a edição) → danger "Excluir este pregão e suas planilhas? <b>Não dá para desfazer.</b>" + "Sim, excluir" (primary → `_excluir_pregao(id)`: apaga os `.xlsx` de todos os exports + `DELETE FROM editais` com CASCADE) / "Cancelar".
- **[ATENÇÃO]** só a planilha mais recente é baixável; o README fala em expander com todas as planilhas — o código atual não tem isso. Não há ação "reabrir pregão" a partir do histórico.

### 2.8 Configurações (`tela_configuracoes`, L1247-1429)

Subtítulo: modo servidor → "Credenciais dos fornecedores. Ficam guardadas no servidor do sistema, com backup diário."; local → "Credenciais dos fornecedores. Tudo fica salvo apenas no seu computador, sem envio para servidores externos."
Abas: "Almeida", "Dantas", "IA (Gemini)", "Catálogo histórico", "Backup". Detalhe na seção 5.

## 3. Estado

### 3.1 Tipos de dados que circulam (todos do backend — não recriar)

- `extrator.ItemExtraido`: `item_numero, codigo_orgao, descricao, unidade, quantidade, valor_referencia, pagina, confianca ("extraido"|"duvidoso")`.
- `extrator.ResultadoExtracao` (`extracao`): `arquivo, itens[], tabelas_brutas (list[list[list[str]]]), mapa_colunas_detectado, avisos[], confianca_global (0..1)`; propriedades `total_itens`, `total_duvidosos`.
- `scraper.ResultadoBusca` (frozen): `descricao_encontrada, marca, preco, codigo_fornecedor, confianca ("exato"|"variacao"), fornecedor ("almeida"|"dantas"), ncm, url, no_catalogo, produto_catalogo_id, descricao_catalogo, veio_por_aprendizado`.
- `exportador.SelecaoFornecedor` (frozen): `preco, codigo_fornecedor, marca, ncm, url, fornecedor`.
- `scraper.ErroBusca` (frozen): `indice_item, fornecedor, descricao_resumo, mensagem`. `scraper.ResultadoLote`: `resultados, erros, cache_hits, cache_misses, codigo_hits, llm_judge_hits`.

**Índices — regra central:** `resultados_busca`, `selecoes`, `itens_nao_usar`, `markup_overrides`, `erros_busca[].indice_item` e todas as chaves por item (`*_{idx}`) usam a POSIÇÃO em `itens_revisados` (lista de BUSCA, só os marcados). A planilha usa a posição em `itens_edital` (edital inteiro). A ponte é `mapa_busca_edital[i_busca] = i_edital`, aplicada só em `_gerar_excel`.

### 3.2 Chaves de `st.session_state`

**Do pregão** (`CHAVES_PREGAO`, L195 — apagadas em `_limpar_pregao`/`_restaurar_rascunho`):

| Chave | Tipo / forma | Escreve | Lê |
|---|---|---|---|
| `extracao` | `ResultadoExtracao \| None` | upload L533; mapeamento L3467/L3605; boot L97 | revisão, mapeamento, `_tem_pregao_em_andamento`, `_tela_pregao_em_andamento` |
| `df_editavel` | `DataFrame \| None`; colunas `OK, Item, Descrição, Unidade, Quantidade, Valor referência` | revisão L634 (de `res.itens`), `_marcar_todos` L664; zerado no upload/mapeamento/restauração | `st.data_editor` (snapshot inicial; NÃO é regravado a cada edição) |
| `itens_revisados` | `list[ItemExtraido]` (só marcados) | revisão L722 (todo render) | busca, seleção, geração, hash |
| `itens_edital` | `list[ItemExtraido]` (todos) | revisão L723 | `_gerar_excel`, `_hash_pregao`, `rascunho.listar_pregoes` |
| `mapa_busca_edital` | `list[int]` | revisão L724 | `_gerar_excel` |
| `resultados_busca` | `dict[int, list[ResultadoBusca]]` — tem TODAS as posições buscadas (lista vazia = sem resultado) | `_executar_busca` L2038; `_mesclar_busca_incremental`; `_usar_do_catalogo`/`_usar_do_site` (append); removida pelos botões de re-busca | tela de busca; a PRESENÇA da chave decide pré-busca × pós-busca |
| `selecoes` | `dict[int, SelecaoFornecedor]` (só itens com escolha) | `_mostrar_selecao` (default), `_escolher_opcao`, `_usar_do_*`, `_mesclar_busca_incremental`, `_identificar_ncm` (recria com NCM), "Não usar" (pop) | painel ST, geração, aprendizado, hash |
| `pagina_busca` | `int` (0-based) | paginação | `_mostrar_selecao` |
| `itens_nao_usar` | `set[int]` | botões "Não usar"/"Voltar a usar"; `_escolher_opcao` (discard) | status do item, `faltantes`, `_mesclar_busca_incremental`, assinatura do rascunho |
| `markup_overrides` | `dict[int, float]` (`MARKUP_ST` ou `MARKUP_NORMAL`) | rádios do painel ST (para TODO item sem NCM, a cada render); `_identificar_ncm` (pop quando acha NCM) | `classificar_markups`, `com_st_de_ncms`, hash |
| `pregao_gerado_hash` | `str` (sha256) | `_gerar_excel` L902 | `_tem_pregao_em_andamento`, stepper |
| `avisos_lote_vazio` | `list[str]` (HTML) | `_executar_busca` L2054 | pós-busca L1492 |
| `usando_tabelas_docling` | `bool` | mapeamento L3468 | mapeamento |
| `mapeamento_tabela_idx` | `int` (key do selectbox) | widget; pop L3469 | mapeamento |

**Identidade do pregão** (apagadas junto em `_limpar_pregao`): `orgao` (str), `numero` (str) — upload L535-536, revisão L561-567; `pdf_path` (str, caminho do PDF em `data/editais`) — upload L537.

**Controle de rascunho/URL**: `_rascunho_slug` (str; criado no 1º autosave por `rascunho.novo_slug`, ou no restauro), `_rascunho_assin` (str; última assinatura gravada), `_rascunho_disponivel` (só é removida — legado), `_sessao_nova` (bool transitório), `_confirmar_novo_pregao` (bool; abre o modal).

**De sessão (sobrevivem à troca de pregão; NÃO vão para o rascunho):**

| Chave | Tipo | Escreve | Lê |
|---|---|---|---|
| `tela` | str | `ir_para`, `_restaurar_rascunho` | roteador, sidebar (salva no rascunho, mas ignorada no restauro) |
| `backup_feito` | bool | boot L88 | boot |
| `editor_version` | int | revisão | key do editor (`editor_{n}` guarda o delta `{"edited_rows": {pos: {col: valor}}}`) |
| `erros_busca` | `list[ErroBusca]` | `_executar_busca` L2039/L2042 (a incremental SUBSTITUI pelos erros do sub-lote) | expander de erros |
| `avisos_pagamento` | `dict[fornecedor, str HTML]` | `_executar_busca` L2012 | pós-busca L1488 |
| `cache_stats` | `(hits, misses)` | `_executar_busca` L2043 | ninguém (escrita morta) |
| `diag_path` | str | `_executar_busca` L2080 (só diagnóstico) | pós-busca (dev) |
| `ncm_cache` | `dict[url, ncm \| None]` | `_identificar_ncm` | idem |
| `catalogo_lista` | `list[matcher.ProdutoCatalogo]` | `_carregar_catalogo_sessao` (1×/sessão; não invalida após importar) | busca no catálogo |
| `margem_venda_col` | `"Z" \| "AA" \| "AB"` | `_seletor_margem_venda` | `_gerar_excel` L856 |
| `markup`, `mk_st`, `mk_normal` | — | **ninguém escreve** (legado) | `_hash_pregao` L2207; `markup` em L3366 (cai sempre em `config.MARKUP_DEFAULT`) |

**Por item da busca** (prefixos `_PREFIXOS_BUSCA_ITEM`, L225; apagados por `_limpar_busca_por_item` em `_limpar_pregao`/`_restaurar_rascunho`): `site_res_{idx}` = `(fornecedor, list[ResultadoBusca])`; `siteq_{idx}` (termo); `siteforn_{idx}` (fornecedor); `sitego_{idx}`; `catq_{idx}` (consulta do catálogo); `fonte_busca_{idx}` ("Meus produtos"|"Site do fornecedor"); `naousar_{idx}`. Outras keys de widget por item (não limpas explicitamente): `esc_{idx}_{pos}`, `usecat_{idx}_{produto_id}_{forn}`, `siteuse_{idx}_{j}`, `ov_markup_{off}`.

**Demais keys de widget**: `nav_{tela}`, `rev_orgao`, `rev_numero`, `filtro_hist`, `hist_edit_{id}`, `conf_excl_{id}`, `btn_edit_/dl_/dlx_/btn_del_/excl_sim_/excl_nao_{id}`, `rasc_conf_del_/rasc_cont_/rasc_del_/rasc_del_sim_/rasc_del_nao_{slug}`, `rebusca_faltantes`, `pag_ant`, `pag_prox`, `btn_ncm`, `sel_margem_venda`, `novo_apos_gerar`, `map_col_{c}`, `copia_almeida_dantas`, `dantas_email`, `dantas_senha`, `forma_{forn}`, `prazo_{forn}`, `catalogo_uploads`, `backup_servidor_agora`, `erro_tela_de_novo`, `erro_tela_inicio`.

### 3.3 Rascunho (autosave) — o que persiste e quando

- **Chaves gravadas** (`_CHAVES_RASCUNHO`, L212): `CHAVES_PREGAO` MENOS `df_editavel`, MAIS `orgao`, `numero`, `pdf_path`, `tela`. Ou seja: `extracao, resultados_busca, selecoes, pagina_busca, itens_revisados, itens_edital, mapa_busca_edital, itens_nao_usar, usando_tabelas_docling, mapeamento_tabela_idx, markup_overrides, pregao_gerado_hash, avisos_lote_vazio, orgao, numero, pdf_path, tela` (só as presentes na sessão).
- **Formato**: pickle `{"versao": 1, "estado": {...}}` em `data/rascunhos/<slug>.pkl` (`rascunho.salvar`, escrita atômica; nunca levanta). Contém dataclasses de `pregoes.extrator`, `pregoes.scraper`, `pregoes.exportador` — manter os caminhos de módulo garante leitura dos rascunhos existentes.
- **Quando grava** (`_salvar_rascunho`, L2230; fim de TODO render): só se `_tem_pregao_em_andamento()` e a assinatura mudou. `_assinatura_rascunho` (L2215) = `"{_hash_pregao()}:{nº total de resultados}:{orgao}:{numero}:{tuple(sorted(itens_nao_usar))}"`. Mudanças fora disso (ex.: `pagina_busca`, `avisos_lote_vazio`) só vão a disco junto com outra mudança.
- **Slug**: criado uma vez (`rascunho.novo_slug(orgao, numero)` = slug legível + sufixo aleatório) e mantido em `_rascunho_slug`; mudar órgão/número NÃO renomeia o arquivo.
- **Gravação forçada**: "Guardar e começar outro" remove `_rascunho_assin` antes de salvar (L332).
- **Restauração** (`_restaurar_rascunho`, L2267): apaga `CHAVES_PREGAO` + identidade + buscas por item; copia o estado; garante `extracao`/`df_editavel` (None); fixa `_rascunho_slug` e `_rascunho_assin`; define `tela = _tela_pregao_em_andamento()` (IGNORA a `tela` salva). Gatilhos: "Continuar" no Início (L2425) e `?pregao=` em sessão nova (L2324).
- **Remoção** (`_limpar_rascunho`, L2255): após geração com histórico gravado (L889); "Descartar e começar novo"; "Sim, descartar"; `_limpar_pregao()` do upload (L532). Falha ao gravar o histórico → rascunho é MANTIDO.
- **Listagem**: `rascunho.listar_pregoes()` (pula corrompidos; migra o formato antigo `data/rascunho.pkl`).
- **[ATENÇÃO]** `df_editavel` não é salvo e a revisão o reconstrói de `extracao.itens` (L633-641): ao retomar um pregão parado na revisão, edições de célula e marcações feitas antes NÃO voltam para a grade (e `itens_revisados/itens_edital/mapa` são regravados a partir dela). Decidir na reescrita: reconstruir a grade de `itens_edital` + `mapa_busca_edital`.

## 4. Fluxos de longa duração

No Streamlit todos rodam SÍNCRONOS dentro do render (a thread do script fica bloqueada; a tela não aceita outra ação). Na reescrita viram tarefas com progresso consultável.

### 4.1 Extração do PDF (upload, L506-526)

- `extrator.extrair_com_pipeline(tmp_path)` — pdfplumber e, se a confiança < 95% e há chave Gemini, a camada LLM (30–90 s). Progresso na tela: apenas o spinner "Lendo PDF e detectando tabelas...".
- **[ATENÇÃO]** o backend aceita `callback_progresso_llm(pag_atual, total_pag, msg)`, mas a UI atual NÃO passa (L514) — não há barra de progresso da IA hoje. A reescrita pode ligar o callback.
- Depois: `_tentar_mapa_aprendido` (rápido, local).
- Recarregar a aba durante a extração: nada foi salvo ainda (o rascunho nasce no render seguinte) → volta ao Início; o PDF fica órfão em `data/editais`.
- Docling (mapeamento): `extrator_docling.coletar_tabelas_brutas_docling(pdf_path)`, 5–10 min, só spinner.

### 4.2 Busca de preços em lote (`_executar_busca`, L1772-2085)

**Entrada**: `itens` (lista de busca completa), `fornecedores_nomes`, `indices` (None = tudo; lista = incremental), `salvar_diagnostico`.

**Painel** (`style.painel_progresso_busca`, redesenhado a cada callback num placeholder de largura total):
- título "Buscando preços" → "Busca concluída"; subtítulo "{Fornecedor} · fornecedor {i} de {n}" ou "Todos os fornecedores" (modo paralelo/fim);
- tempo: decorrido ("MM:SS" ou "{h}h {mm}min") + " · " + `_tempo_restante` ("faltam ~N min" / "faltam ~N,N h"; 6.4);
- barra + percentual; rótulo "Aguardando início" (item_atual ≤ 0) ou "Item {a} de {t}"; caixa com a mensagem do item (descrição que vem do scraper; "Preparando ambiente…"; "Fazendo login em {Fornecedor}…"; "Aguardando a busca de outro operador terminar…"; no paralelo "Almeida {a}/{t} · Dantas {a}/{t}"; em pausa `_MSG_PAUSA_LIMITE` = "O site dos fornecedores limitou as consultas. A busca pausou e continua sozinha em alguns minutos — não feche esta tela.");
- rodapé por fornecedor concluído: chips "{n} encontrados" (success), "{n} por variação" (warning), "{n} sem resultado" (danger).

**Execução** — tudo dentro de UMA operação `scraper.no_navegador(_buscar_na_thread_dona, progresso=_render, aguardando=…)` (L2012):
1. Login por fornecedor com credencial: `scraper.obter_sessao_viva(nome)` (reusa sessão logada + condição de pagamento); fração = `forn_idx / n_forn * 0.05`. Falha → `_registrar_falha_grave` (L1911): `scraper.fechar_sessao_viva(nome)`, `ErroBusca(indice_item=-1, descricao_resumo="(login/inicialização)", mensagem=erros.mensagem_amigavel(exc, "buscar na {Fornecedor}"))`, stats `(nome, 0, 0, total)`.
2. **Paralelo** se `len(sessoes) > 1 and log_diag is None and all(f._http_cookie)` (L1954): `_buscar_fornecedores_paralelo` (L1679) — 1 thread por fornecedor com `forn._http_only = True`, cada uma chama `scraper.buscar_precos_para_itens(itens_alvo, [forn], progresso_callback=…)`; os workers só enfileiram; o laço principal (thread dona) repassa o progresso; a cada 5 s sem novidade, se `scraper.pausa_por_limite() > 0`, mostra `_MSG_PAUSA_LIMITE`. Fração = média de `atual/total` dos fornecedores; contador = soma. Devolve `{nome: ResultadoLote | Exception}`.
3. **Sequencial** (1 fornecedor, diagnóstico ligado ou sessão sem HTTP): por fornecedor, `buscar_precos_para_itens(itens_alvo, [forn], progresso_callback=cb, diagnostico=log_diag)`; fração = `(forn_idx + atual/total) / n_forn`.
4. `_absorver_lote` (L1890): `n_ok` = itens com ≥1 resultado; `n_var` = itens com alguma `variacao`; stats `(nome, n_ok − n_var, n_var, total − n_ok)`; resultados anexados por índice ORIGINAL (`alvo_indices[sub_idx]`); erros com `indice_item` remapeado.
5. Retorno da operação: `_avisos_pagamento_de(sessoes)` → `session.avisos_pagamento`.

**Depois**: painel final (100%, `encerrado=True`); busca completa → `resultados_busca = lote.resultados`; incremental → `_mesclar_busca_incremental`; `erros_busca`, `cache_stats`; log "LOTE: …"; `avisos_lote_vazio = _avisos_lote_vazio(stats, total) + _avisos_recusa(erros)`; success "Busca concluída — {n} itens processados ({hits} do cache, {misses} buscas reais · {k} erro(s) técnico(s))." (partes opcionais); diagnóstico → `diagnostico.exportar_xlsx(log_diag, data/diagnostico/diagnostico_AAAAMMDD_HHMMSS.xlsx)` (falha → warning "Diagnóstico falhou ao exportar: …"); o chamador faz rerun → tela de seleção.

**Concorrência entre operadores**: `scraper.no_navegador` = thread única e permanente dona dos navegadores, com fila; o lote inteiro (login + todos os fornecedores) é UMA operação. Quem chega depois espera; `aguardando()` é chamado uma vez e o painel mostra "Aguardando a busca de outro operador terminar…". Busca manual no site (`buscar_no_site`), cotação por código (`cotar_por_codigo`) e o fallback de NCM usam a MESMA fila — e a UI NÃO passa `aguardando` nesses casos (só o spinner gira enquanto o lote do outro operador não acaba). `fechar_sessoes_vivas(esperar=False)` só agenda (não trava a tela).

**Interrupção / recarregar a aba**: os callbacks de progresso rodam na thread da tela; se ela morreu (rerun/aba recarregada), `no_navegador` marca `interrompida` e a operação aborta no próximo aviso (`scraper.OperacaoInterrompida`, BaseException), liberando a fila. Consequências:
- `resultados_busca` do lote interrompido NÃO chega à sessão nem ao rascunho; o que já foi consultado fica no cache de preços do backend (TTL 2 dias), então refazer é rápido.
- A aba recarregada restaura o rascunho pela URL: busca completa interrompida → cai na REVISÃO (não havia resultados); incremental interrompida → volta à seleção com o estado anterior.
- **[ATENÇÃO]** no modo paralelo os workers (daemon) não são cancelados na interrupção: seguem consultando até o fim do lote e o resultado é descartado (só alimenta o cache).
- Não existe botão "cancelar busca" nem retomada de progresso parcial na tela.

### 4.3 Identificação de NCM (`_identificar_ncm`, L2590-2654)

- Para cada selecionado sem NCM e com `url`: usa `ncm_cache[url]` da sessão ou monta o pedido `(offset, fornecedor, url, codigo_fornecedor)`; fornecedor deduzido da URL (`"almeidadistribuidor" in url` → almeida; `"dantasdistribuidora"` → dantas; outro → ignora).
- `scraper.consultar_ncms(pedidos, progresso=_cb)` (cache durável → HTTP paralelo sem login → fallback navegador). Barra `st.progress`: "Consultando NCM dos produtos escolhidos…" → "Consultando NCM… {feitos}/{total}".
- Só resultados definitivos entram em `ncm_cache` e nas seleções (`dataclasses.replace(sel, ncm=…)`); NCM encontrado remove o `markup_overrides[off]`. Falhas transitórias → warning "{n} item(ns) não puderam ser consultados agora (login do fornecedor ou navegador). Os demais foram classificados. Clique em <b>Identificar NCM</b> de novo para tentar os que faltaram." Exceção geral → warning `erros.mensagem_amigavel(exc, "consultar o NCM")`.

### 4.4 Geração/exportação (`_gerar_excel`, L788-937)

Síncrona, spinner. Sequência: remapeia índices → valida → `exportador.exportar(...)` → `_persistir_edital` (mesma transação: edital + itens + `db.export_log_gravar`) → `_limpar_rascunho()` → `pregao_gerado_hash` → thread daemon `backup.backup_externo_agora()` → bloco de sucesso/download. Dois operadores gerando o MESMO (órgão, número) atualizam o mesmo registro (UPSERT); não há trava.

### 4.5 Outros demorados

| Fluxo | Chamada | Progresso |
|---|---|---|
| Importar planilhas | `importador.importar(paths, fornecedor=…)` | spinner "Importando {n} planilha(s)…" |
| Padronizar catálogo (IA) | `alinhador.normalizar_catalogo(callback=(feitos,total))` | barra "Etapa 2 de 2 — preparando o catálogo… {f}/{t}" |
| Teste da chave Gemini | `genai.Client(...).models.generate_content` (timeout 60 s) | spinner "Testando chave…" |
| Backup servidor | `backup.enviar_backup_servidor()` (subprocesso, timeout 600 s) | spinner "Fazendo o backup…" |
| Backup externo local | `backup.fazer_backup_externo(dir)` | spinner "Copiando para o Google Drive…" |
| Cotação por código / busca no site | `scraper.cotar_por_codigo` / `scraper.buscar_no_site` | spinner (pode ficar na fila do navegador) |

## 5. Configurações e recursos auxiliares

### 5.1 Credenciais Almeida / Dantas (L1260-1328)

- Topo: success "Credencial ativa: <code>{email}</code> (senha oculta)." ou warning — Almeida: "Nenhuma credencial salva. A busca de preços não funciona até configurar."; Dantas: "Nenhuma credencial salva. A busca não inclui Dantas até configurar."
- Só na aba Dantas, sem credencial Dantas e com credencial Almeida: botão "📋 Usar as mesmas credenciais da Almeida" → `credenciais.salvar("dantas", email, senha)` + rerun.
- Formulário: "E-mail" e "Senha" (password) PRÉ-PREENCHIDOS com o valor salvo; botão "Salvar credenciais" (primary). Vazio → danger "Preencha e-mail e senha antes de salvar."; ok → `credenciais.salvar(forn, email.strip(), senha)` + success "Credenciais da Almeida salvas com sucesso." / "Credenciais da Dantas salvas com sucesso."
- Não há botão de remover credencial nem de testar login. Salvar credencial NÃO fecha a sessão viva do fornecedor (só a condição de pagamento fecha).

### 5.2 Condição de pagamento por fornecedor (`_form_condicao_pagamento`, L2095-2155)

- Opções: `config.carregar_opcoes_pagamento()[fornecedor]` = `{"forma": [{value, texto}], "prazo": [{value, texto}]}`; sem formas → o bloco não aparece.
- Texto: "**Condição de pagamento** — o preço no site **muda** conforme a forma (e o prazo, quando houver). Escolha a condição sob a qual você realmente compra; o sistema busca os preços nela. **PIX e pagamento antecipado não têm prazo** — deixe o prazo em *Não se aplica*."
- Selects: "Forma de Pagamento"; "Prazo de Pagamento (só para boleto/cartão)" com 1ª opção fixa "— Não se aplica (PIX, à vista, antecipado) —" (value vazio); pré-seleção pela preferência salva (`credenciais.carregar_preferencia_pagamento`).
- Botão "Salvar condição de pagamento" (primary) → `credenciais.salvar_preferencia_pagamento(forn, forma.value, forma.texto, prazo.value, prazo.texto se value senão "")` → `scraper.fechar_sessoes_vivas(esperar=False)` (próxima busca reloga na nova condição) → success "Condição salva: <b>{forma}[ · {prazo}]</b>. As próximas buscas usam essa condição."
- Rodapé: "Condição atual: {forma}[ · {prazo}]" ou "Ainda não configurado — o sistema usa a condição **default** do site (o preço pode não bater com a sua)."
- Reflexos na busca: badges/aviso de `_mostrar_condicao_pagamento` e avisos pós-busca de `_avisos_pagamento_de` (6.4).

### 5.3 IA (Gemini) (L1330-1420)

- Topo: success "Chave configurada: <code>{8 primeiros}…{4 últimos}</code>. A IA roda automaticamente quando a extração rápida ficar abaixo de 95%." ou warning "Sem chave configurada. A IA fica desligada — o sistema usa só extração rápida + mapeamento manual."
- Instruções fixas: "**Como obter a chave gratuita (5min):**" (4 passos, aistudio.google.com → "Get API key" → "Create API key") + "**Custo:** *Gratuito* dentro do free tier do Google (1500 requisições/dia — folga ampla para 10–30 editais/mês)."
- Formulário: "Chave de API (Gemini)" (password, pré-preenchida; help "Começa com 'AIza...'. Salva apenas no seu computador."); botões "Salvar chave" (primary) e "Salvar e testar".
- Regras: vazio → danger "Cole a chave antes de salvar."; não começa com "AIza" → warning "Chave parece inválida (esperado começar com <code>AIza</code>). Salva mesmo assim, mas verifique." (SALVA); ok → `credenciais.salvar_chave_api("gemini", chave.strip())` + success "Chave Gemini salva com sucesso."
- "Salvar e testar": chamada direta ao SDK (`google.genai`, modelo `extrator_llm.MODELO_DEFAULT`, prompt "Responda apenas 'OK'.", timeout 60 s): success "Conexão com Gemini funcionando."; warning "Resposta inesperada: {80 chars}"; danger "Falha ao chamar Gemini: {Tipo}: {exc}".

### 5.4 Catálogo histórico / importador de planilhas (`_aba_catalogo`, L3727-3886)

- Info: "O <b>catálogo histórico</b> acelera a busca de preços nos próximos pregões. Quando um item do edital casa com um produto que você já comprou no passado, o sistema vai direto pelo <b>código do produto</b> nos fornecedores — sem precisar adivinhar pela descrição."
- 4 métricas (SQL direto): "Produtos no catálogo" (`COUNT produtos`), "Com código Almeida", "Com código Dantas" (código não nulo/não vazio), "Nomes padronizados (IA)" (`COUNT produtos_canonico`). (`COUNT precos_historico` é consultado e não exibido.)
- [dev] botão "[dev] Padronizar {n} nomes (IA)" (`_DEBUG` e há faltantes) → `alinhador.normalizar_catalogo()`.
- Texto: "**Importe suas planilhas de pregões anteriores** (formato MATRIZ). O sistema lê descrição, código, marca e preço, e usa esse histórico para encontrar os produtos mais rápido nos próximos editais. Re-enviar a mesma planilha não duplica nada."
- Uploader múltiplo "Planilhas históricas (.xlsx)" (key `catalogo_uploads`); rádio horizontal "Fornecedor principal destas planilhas:" (`almeida` default | `dantas`; help "Os códigos sem a letra no fim são deste fornecedor. Códigos que terminam em D vão para a Dantas e A para a Almeida automaticamente.").
- Botão "Importar planilhas" (primary; só com upload):
  1. salva cada arquivo em `config.DATA_DIR/"historico"` com `_sanitizar_nome_arquivo(nome)` (nome determinístico = chave de dedup); erro → danger `mensagem_amigavel(exc, "salvar as planilhas para importação")`;
  2. `importador.importar(paths, fornecedor=…)`; erro → danger `mensagem_amigavel(exc, "importar as planilhas")`;
  3. success: com chave Gemini "<b>Etapa 1 de 2 — importação concluída e salva.</b><br>Agora o sistema prepara o catálogo para a busca (etapa 2, abaixo). Os produtos já estão salvos mesmo se você sair.<br>" + `stats.resumo()`; sem chave "<b>Importação concluída e salva.</b><br>" + `stats.resumo()`;
  4. expander "⚠️ Avisos ({n})" com `stats.avisos`;
  5. com chave: `alinhador.normalizar_catalogo(callback=…)` com barra; fim "Etapa 2 de 2 concluída — catálogo pronto para a busca."; `falhos` > 0 → warning "{n} produto(s) não foram padronizados (limite da IA ou internet). A importação está salva e a busca funciona; para cobertura total clique em importar de novo mais tarde."; exceção → warning "A importação está salva, mas não consegui padronizar os nomes agora ({mensagem}). A busca funciona mesmo assim; tente importar de novo mais tarde.";
  6. `st.rerun()` (as mensagens acima somem).
- Não há tela para listar/editar/excluir produtos do catálogo; o único acesso de leitura é a busca manual por item (2.5.2).

### 5.5 Backup — modo servidor (`_aba_backup_servidor`, L3612-3647)

- Info: "O sistema faz backup <b>sozinho</b>: todo dia de madrugada e a cada planilha gerada, uma cópia completa (pregões, catálogo, histórico, planilhas e credenciais) é guardada <b>fora do servidor</b>, com os últimos 30 dias. Não precisa configurar nada."
- Estado (`backup.ultimo_envio_servidor()`): nunca → warning "Ainda não houve nenhum backup. O primeiro acontece esta madrugada — ou clique em <b>Fazer backup agora</b>."; mais de 2 dias → danger "O último backup é de <b>{dd/mm/aaaa às HH:MM}</b> — há mais de 2 dias. Clique em <b>Fazer backup agora</b>; se não funcionar, fale com o suporte."; senão success "Último backup: <b>{dd/mm/aaaa às HH:MM}</b>."
- Botão "Fazer backup agora" → `backup.enviar_backup_servidor()`; ok → rerun; falha → danger "Não consegui fazer o backup agora. Tente de novo em alguns minutos; se persistir, fale com o suporte."

### 5.6 Backup — modo local (`_aba_backup_externo`, L3650-3722)

- Info sobre pasta do Google Drive + instruções "**Como preparar (uma vez):**" (3 passos).
- Formulário: "Pasta de backup (Google Drive)" (placeholder `G:\Meu Drive\Backup Pregões`; help "Pasta no seu computador que o Google Drive sincroniza. Deixe em branco para desligar o backup automático."); botões "Salvar pasta" (primary) e "Fazer backup agora".
- "Salvar pasta" → `credenciais.salvar_pasta_backup(caminho)`: pasta existe → success "Pasta de backup salva. A partir de agora o sistema guarda uma cópia lá sozinho."; não existe → warning "Pasta salva, mas <b>não encontrei</b> ela no computador agora. Confira se o Google Drive está aberto e se o caminho está certo — o backup só grava quando a pasta existe."; em branco → info "Backup automático desligado (pasta em branco)."
- "Fazer backup agora" → salva o caminho do campo (se houver) e `backup.fazer_backup_externo(dir)`: success "Backup feito: <code>{arquivo}</code> na pasta indicada. O Google Drive vai enviar para a nuvem em seguida."; falha → danger "Não consegui gravar o backup. Verifique se o <b>Google Drive está aberto</b> e se o caminho da pasta está correto."

### 5.7 Backups automáticos (sem tela)

- Diário do banco: `db.backup("diario")` uma vez por sessão de navegador (L72-76).
- Externo no boot (só modo local): thread `backup.fazer_backup_externo()` (L81-87).
- Externo pós-geração (ambos os modos): thread `backup.backup_externo_agora()` (L906-911).

### 5.8 Histórico de planilhas — ver 2.7. Mapeamento manual — ver 2.4.

### 5.9 Dashboard

**Não existe** em `app.py` (nenhuma tela, gráfico ou filtro de variação de preço). O "dashboard de variação" do roadmap não foi implementado na UI — não há paridade a manter.

### 5.10 Diagnóstico (só `PREGOES_DEBUG`)

- Checkbox "[dev] Salvar diagnóstico (XLSX)" → `diagnostico.LogDiagnostico(arquivo_edital=pdf_path, fornecedores=[…])` passado ao scraper (força modo sequencial) → `diagnostico.exportar_xlsx` em `data/diagnostico/` → botão "Baixar relatório de diagnóstico (XLSX)" + legenda "Arquivo salvo em `data/diagnostico/{nome}`. Abra no Excel/LibreOffice, anote `correto`/`errado`/`aceitavel` na coluna **Avaliação** da aba **Resumo** e devolva para calibrar."
- Checkbox "[dev] Limpar cache antes" → `db.cache_limpar(conn)`.
- "[dev] Padronizar {n} nomes (IA)" (aba Catálogo).
- Para o operador: log em `data/pregoes.log` (sem tela).

## 6. Regras de negócio que vivem na UI (portar uma a uma)

### 6.1 Ciclo de vida e identidade do pregão

| Função (linha) | Regra |
|---|---|
| upload L499; `_gerar_excel` L835 | Órgão E número obrigatórios (após `strip`) em dois pontos: antes de extrair e antes de gerar (a revisão permite apagá-los). |
| `_tem_pregao_em_andamento` L270 | Falso se `pregao_gerado_hash == _hash_pregao()` (gerado e intocado); senão verdadeiro se `extracao is not None` ou `itens_revisados` ou `selecoes`. |
| `_tela_pregao_em_andamento` L298 | `resultados_busca` ou `selecoes` → `busca_precos`; `extracao`/`itens_revisados` → `revisao`; senão `upload`. Usada por "Continuar o atual", "Continuar" (Início) e retomada por URL. |
| `_hash_pregao` L2186 | sha256 de `repr((itens, selecoes, markup, mk_st, mk_normal, overrides))`: itens = `itens_edital` (ou `itens_revisados`) como tuplas `(item_numero, descricao, unidade, quantidade, valor_referencia)`; seleções = tuplas ordenadas `(offset, preco, codigo_fornecedor, marca, ncm)`; overrides ordenados. NÃO entram: `margem_venda_col`, `itens_nao_usar`, órgão/número, `fornecedor`/`url` da seleção. |
| `_limpar_pregao` L238 | Apaga `CHAVES_PREGAO` + `orgao/numero/pdf_path` + chaves por item; `preservar_rascunho=True` solta só os controles (`_rascunho_assin/_slug/_disponivel`) sem apagar o arquivo; senão `_limpar_rascunho()`. Sempre chama `scraper.fechar_sessoes_vivas(esperar=False)`. |
| `_novo_pregao` L348 | Só pede confirmação se há trabalho a perder; pregão gerado e intocado limpa direto. |
| upload L532 | Toda extração nova começa com `_limpar_pregao()` (nada do pregão anterior vaza). |

### 6.2 Extração e revisão

| Função (linha) | Regra |
|---|---|
| upload L523; revisão L598 | Limiar 0,95 de `confianca_global`: abaixo dele tenta mapas aprendidos (upload) e oferece Mapeamento Manual (revisão) — ambos só se há `tabelas_brutas`. |
| `_tentar_mapa_aprendido` L443 | Para cada tabela bruta com cabeçalho: `mapeamento_manual.assinatura_cabecalho(tab[0])` → `carregar_mapa` → `aplicar_mapa_em_lote(tabelas, idx, mapa, arquivo=…)`; adota o 1º cujo `confianca_global` seja ESTRITAMENTE maior; preserva as `tabelas_brutas` originais; insere no topo dos avisos "✨ Mapa aprendido reusado (tabela #{n} e continuações, cabeçalho conhecido)." |
| `style.confianca_para_badge` S608 | ≥0,95 alta (success); ≥0,80 média (warning); >0 baixa (danger); 0 "Sem itens detectados". |
| revisão L609-627 | 0 itens: distingue PDF escaneado (`extrator.tem_texto_extraivel(pdf_path)` falso) de "tabela não achada". |
| revisão L633-641 | Grade inicial de `res.itens`; `OK = (confianca == "extraido")`. |
| `_aplicar_delta_editor` L133 / `_fundir_edicoes` L108 | Marcar/desmarcar todos preserva edições de célula pendentes: aplica o delta `edited_rows` por posição (ignora posições inválidas) e copia só as colunas de dados (`_COLUNAS_DADOS_REVISAO`, L105), depois sobrescreve `OK`. |
| `_linha_para_item` L750 | Linha → `ItemExtraido`: `Item`→int, `Quantidade`/`Valor referência`→float, vazios (NaN) → `None` (descrição → `""`). `codigo_orgao`, `pagina` e `confianca` do item original são DESCARTADOS (confiança volta a "extraido"). |
| `_itens_revisao` L762 | Devolve `(edital, busca, mapa)`: edital = TODAS as linhas na ordem; busca = linhas com `OK == True`; mapa = índice no edital de cada item da busca. "OK" = "buscar preço", NÃO "incluir na planilha". |
| revisão L731-741 | "Buscar preços" exige ≥1 item marcado e ≥1 fornecedor com credencial. |

### 6.3 Ranking, seleção default e apresentação das opções

| Função (linha) | Regra |
|---|---|
| `_chave_default_opcao` L3055 | Chave de ordenação (menor vence): `(-veio_por_aprendizado, -no_catalogo, -ranking.bonus_codigo(desc_item, desc_encontrada), -normalizador.relevancia(desc_item, desc_encontrada), preco)`. Ordem: escolha anterior > catálogo > código forte do edital > relevância > menor preço. |
| `_melhor_opcao` L3071 | `min(opcoes, key=_chave_default_opcao)`. É o contrato de pré-seleção da planilha. |
| `_mostrar_selecao` L3096-3108 | Sem `selecoes` na sessão: cria seleção default (`_melhor_opcao`) para TODO item com opções — o operador pode gerar sem abrir nenhum item. |
| `_dedup_opcoes` L2571 | Remove duplicatas por `(fornecedor, codigo_fornecedor, marca, preco)`, preservando ordem. |
| `_mostrar_selecao` L3171-3180 | Ordena pela chave default e corta em `config.MAX_OPCOES_EXIBIDAS` (4); contador de ocultas = total − visíveis. |
| `_opcoes_visiveis` L2555 | A opção ESCOLHIDA sempre aparece: se ficou fora do corte, entra no topo (visíveis podem ser 5). |
| `_mesma_selecao` L2546 | Identidade visual da escolha = `preco` + `marca` + `codigo_fornecedor` (sem fornecedor). |
| L3272-3275 | Card em destaque = opção escolhida; sem escolha = posição 0 do ranking. |
| `_badge_de` L3257 | Uma pílula por opção, por prioridade: aprendizado > catálogo > posição 0 ("Melhor match") > preço mínimo entre as visíveis ("Mais barato"). |
| `_escolher_opcao` L2439 | Grava `SelecaoFornecedor(preco, codigo_fornecedor, marca, ncm, url, fornecedor)` e tira o item de `itens_nao_usar`. |
| "Não usar" L3319-3333 | Marcar: entra no conjunto e REMOVE a seleção. Desmarcar: só sai do conjunto. |
| status do item L3184-3195 | Precedência: não usar > sem opções > alguma `variacao` visível > ok. |
| métricas L3111-3128 | `com_resultado` = itens com lista não vazia; `com_variacao` = itens com alguma opção `variacao`; "Com preço" = diferença; `sem_resultado` = total − com_resultado; `selecionados = len(selecoes)`. |
| `_numero_item_edital` L2450 | Exibe `item.item_numero` (inclusive 0); `None` → posição + 1. Mesma regra da coluna ITEM da planilha. |
| `_titulo_item` L2463 | Título: tira espaços e ` .;,-` do fim; palavra TODA maiúscula vira Capitalizada, exceto tokens "técnicos" (têm dígito, têm "/", ou ≤3 letras); corta em 95 chars + "…"; 1ª letra maiúscula. |
| `_brl` L2088 | `1234.5` → "R$ 1.234,50". **[ATENÇÃO]** as linhas da busca manual (catálogo L2882, site L3031) usam `R$ {preco:.2f}` com PONTO decimal. |
| `_card_opcao` L2484 | Marca vazia → "Sem marca"; descrição encontrada cortada em 72 + "…"; fornecedor capitalizado; código só se houver. |
| paginação L3153-3160 | 20 por página; página atual limitada a `[0, n_paginas−1]`. |

### 6.4 Busca em lote: mesclagem, estatísticas, avisos e tempo

| Função (linha) | Regra |
|---|---|
| `faltantes` L1502 | Itens com lista vazia/ausente em `resultados_busca` e fora de `itens_nao_usar`. |
| `_mesclar_busca_incremental` L1742 | `resultados_busca.update(novos)` (só os índices re-buscados); item que agora tem opções, sem seleção e fora de "não usar" ganha a seleção default; nenhuma escolha existente é tocada. |
| `_absorver_lote` L1890 | Stats por fornecedor `(ok sem variação, por variação, vazios)`; concatena opções dos fornecedores no mesmo item; remapeia índice do sub-lote → índice original. |
| `_registrar_falha_grave` L1911 | Fornecedor inteiro falhou: fecha a sessão viva, erro com `indice_item=-1`, stats zeradas — o outro fornecedor segue. |
| L1954 | Paralelo só com >1 sessão, sem diagnóstico e todas com `_http_cookie`. |
| `_avisos_pagamento_de` L1582 | Lê `forn._pagamento_status = (status, forma)` (default `("sem_config", None)`). `nao_pegou` → "<b>Atenção — {Forn}:</b> a condição de pagamento NÃO foi aplicada[ (configurada: {forma})]. Os preços podem estar na condição errada do site. Confira um item conhecido antes de gerar a planilha." `nao_conferida`/`falhou` → "<b>Atenção — {Forn}:</b> não consegui confirmar a condição de pagamento[ (configurada: {forma})] agora — normalmente é instabilidade momentânea do site do fornecedor, não erro da condição. Tente buscar de novo em alguns minutos; se persistir, confira um item conhecido antes de gerar a planilha." |
| `_avisos_lote_vazio` L1607 | Lote ≥5 itens e fornecedor com `ok + variação == 0` → "<b>Atenção — {Forn}:</b> nenhum resultado em NENHUM dos {n} itens. Isso normalmente é o site do fornecedor instável ou fora do ar, não falta de produto. Aguarde alguns minutos e use <b>Buscar preços dos itens sem resultado</b> antes de confiar nos itens marcados como não encontrados." |
| `_avisos_recusa` L1635 | Erros cuja `mensagem` começa com `scraper._MENSAGEM_RECUSA`; agrupa por fornecedor (`e.fornecedor.split(",")`), conta índices distintos → "<b>Atenção — {Forn}:</b> o site recusou consultas (limite de acessos) e <b>{n} itens</b> ficaram sem preço deste fornecedor. O que já foi encontrado está mantido. Aguarde uns 10 minutos e use <b>Buscar preços dos itens sem resultado</b>." |
| `_tempo_restante` L1655 | Amostras `(instante, fração)` só com fração > 0, janela de 10 min; usa a amostra mais antiga dos últimos 300 s; vazio se a janela tem < 120 s, sem avanço, ou fração ≥ 1; `restante = (1 − frac) × Δt / Δfrac`; ≥ 5400 s → "faltam ~N,N h"; senão "faltam ~N min" (mínimo 1). |
| `_tempo` L1830 | Decorrido "MM:SS"; a partir de 60 min "{h}h {mm}min". |
| `_mostrar_condicao_pagamento` L2158 | Condição mostrada = `forma_texto` + " · " + `prazo_texto` (se houver). |
| botões de re-busca L1530-1537 | Descartam `resultados_busca`, `selecoes`, `pagina_busca` (índices podem mudar se a revisão for editada). |

### 6.5 Buscas manuais por item

| Função (linha) | Regra |
|---|---|
| `_buscar_no_catalogo` L2787 | (1) Consulta só com dígitos (≥2, espaços removidos) → `codigo_almeida`/`codigo_dantas` igual ou com esse prefixo. (2) Senão normaliza (`normalizador.normalizar_descricao`), palavras ≥2 chars; produtos cuja `descricao_canonica` contém TODAS (substring), ordenados por `-fuzz.token_set_ratio` e depois menor descrição. (3) Sem nenhum: fuzzy `token_set_ratio` com corte 60. Limite 6. |
| `_busca_catalogo_item` L2873-2902 | Uma linha por fornecedor com código não vazio; referência = `db.ultima_cotacao` (preço + data `consultado_em[:10]`), só informativa. |
| `_usar_do_catalogo` L2905 | NUNCA usa o preço histórico: cota ao vivo (`scraper.cotar_por_codigo`); falha ou `None` → não grava nada. Opção criada com `no_catalogo=True`, `confianca="exato"`, descrição/marca do site com fallback do catálogo. Seleção gravada SEM `ncm`/`url` (o item cai em "sem NCM" e não é consultável). |
| `_busca_site_item` L2953 | Busca crua (sem filtro de relevância), 1 fornecedor por vez; resultado guardado por item até usar/rebuscar; 429 tratado como "aguarde 5 minutos". |
| `_usar_do_site` L3041 | Anexa o resultado às opções do item e seleciona; limpa o resultado da busca. |
| L2946/L3046 | Opções manuais são ANEXADAS a `resultados_busca[idx]` (entram no ranking/dedup/paginação normais e somem se a busca for refeita do zero). |

### 6.6 Geração da planilha e histórico

| Função (linha) | Regra |
|---|---|
| `_gerar_excel` L814-823 | Se há `itens_edital` e `len(mapa) == len(itens)`: `selecoes`, `markups` e `itens_com_st` são remapeados `i_busca → mapa[i_busca]` e `itens` vira o EDITAL INTEIRO. A planilha sempre tem todos os itens; preço/código/marca só nos selecionados. |
| `_gerar_excel` L856 | Margem: `margem_venda_col` → `credenciais.carregar_margem_venda()` → `config.MARGEM_VENDA_PADRAO`. |
| `_gerar_excel` L862 | `exportador.exportar(itens, orgao=, numero_pregao=, markup=, markups=, itens_com_st=, selecoes=, margem_venda=)` → `Path` do `.xlsx` em `data/exports`. |
| L3366 | `markup` = `session["markup"]` (nunca definido) → `config.MARKUP_DEFAULT`; "Gerar sem buscar" passa `MARKUP_DEFAULT` e `selecoes={}`. |
| `_gerar_excel` L884-895 | Histórico é best-effort: falha ao persistir não bloqueia o download e MANTÉM o rascunho. |
| `_gerar_excel` L902-911 | Após gerar: grava `pregao_gerado_hash`; dispara backup externo em thread. Como o rascunho foi apagado, o slug some da URL; qualquer mudança posterior muda o hash, re-arma "em andamento" e cria rascunho NOVO. |
| `_gerar_excel` L914-920 | Contagem Dantas com `exportador.eh_dantas`; percentual `int(round(config.DESCONTO_DANTAS * 100))` (o desconto em si é aplicado pelo exportador). |
| `_persistir_edital` L940 | UPSERT por `(orgao, numero_pregao)` com `lower(trim())` (pega o `id` mais alto): existente → `UPDATE status, arquivo_pdf, atualizado_em` + `DELETE` dos itens; senão `INSERT`. `status = "precificado"` se há seleções, senão `"rascunho"`. Itens: `preco_base = sel.preco` (preço do site, sem desconto), `confianca_match = "manual"` se selecionado, `revisado = 1`. Mesma transação: `db.export_log_gravar(conn, edital_id, destino.name, len(itens), len(selecoes), markup)`. |
| `_painel_markup` L2657 | ST por item: com NCM → `tributacao.eh_st` decide (override ignorado); sem NCM → operador decide (default Normal). `com_st` vem de `tributacao.com_st_de_ncms` (explícito, não inferido do markup). `MARKUP_ST`/`MARKUP_NORMAL` são só sentinelas. |
| `_seletor_margem_venda` L2766-2771 | Persiste a margem só quando difere do padrão efetivo (evita gravar ao abrir a tela). |

### 6.7 Aprendizado (retroalimentação)

`_registrar_aprendizado` (L3376), chamado no clique de "Gerar planilha matriz com preços" ANTES das validações de `_gerar_excel` (grava mesmo se a geração for barrada): para cada item de `itens_revisados` com seleção — `desc_norm = normalizador.normalizar_descricao(item.descricao)` (vazia → pula); fornecedor = `sel.fornecedor`, com fallback por igualdade `(preco, codigo_fornecedor, marca)` nas opções do item; sem fornecedor → pula; `db.aprendido_gravar(conn, desc_norm, fornecedor, sel.codigo_fornecedor, sel.marca, sel.preco)`. Vale para escolhas default e manuais. Exceção → só log. ("Gerar sem buscar" não registra nada.)

### 6.8 Histórico

| Função (linha) | Regra |
|---|---|
| `_data_amigavel` L1029 | Timestamp UTC do SQLite (`AAAA-MM-DD HH:MM:SS`) → `config.FUSO_HORARIO` (UTC−3) em "DD/MM HH:MM"; nulo → "data desconhecida"; formato inválido → texto cru. |
| `_rotulo_pregao` L1046 | "Pregão {numero} — {orgao} — {status} em {data}" (partes ausentes omitidas; status nulo → "rascunho"); sem órgão e número → "Pregão sem identificação (de {data})". |
| L1167 | Filtro = substring minúscula sobre o rótulo. |
| L1223-1235 | Download relê `config.EXPORTS_DIR / arquivo` (só o basename é persistido); `exists()` verificado a cada render. |
| `_excluir_pregao` L1069 | Apaga os arquivos listados em `exports_log` e o edital (CASCADE). |
| `_atualizar_pregao` L1083 | `UPDATE editais SET orgao, numero_pregao` (strip; vazio → NULL). |

### 6.9 Rascunhos e retomada

| Função (linha) | Regra |
|---|---|
| `_assinatura_rascunho` L2215 / `_salvar_rascunho` L2230 | Ver 3.3 (grava só quando a assinatura muda). |
| `_restaurar_rascunho` L2267 | Limpa antes de carregar; vai para a tela calculada, não a salva. |
| `_filtrar_rascunhos_finalizados` L2342 | Esconde (sem apagar) rascunho cujo (órgão, número) já tem export. Consequência aceita: refazer um pregão já gerado não aparece no Início. |
| `_retomar_pregao_da_url` L2324 / `_RE_SLUG` L2310 | Slug validado antes de tocar o disco. |
| `_banner_rascunhos` L2393-2402 | Máx. 8; rótulo/detalhe conforme 2.1; descartar o ativo limpa também a sessão. |

### 6.10 NCM

| Função (linha) | Regra |
|---|---|
| `_painel_markup` L2692 | "Identificáveis" = selecionados sem NCM que têm `url`. |
| `_identificar_ncm` L2607 | Fornecedor deduzido do domínio na URL; cache de sessão por URL; só resultado definitivo é cacheado/aplicado; NCM achado apaga o override manual do item. |

### 6.11 Configurações

| Função (linha) | Regra |
|---|---|
| L1278 / L1319 | E-mail e senha obrigatórios; e-mail com `strip`. |
| L1370-1383 | Chave Gemini: vazia barra; sem prefixo "AIza" avisa MAS salva. |
| `_form_condicao_pagamento` L2105-2132 | Prazo opcional (opção vazia "Não se aplica"); texto do prazo gravado vazio quando o value é vazio; pré-seleção cai na 1ª opção se o valor salvo não existe mais. |
| `_aba_backup_servidor` L3631 | Alerta vermelho se o último envio tem mais de 2 dias. |
| `_aba_backup_externo` L3706-3712 | "Fazer backup agora" salva o caminho digitado antes de copiar. |
| `_sanitizar_nome_arquivo` L160 | Só o basename (corta `/` e `\`), troca `[^A-Za-z0-9._-]` por `_`, remove pontos iniciais (vazio → "planilha"), garante `.xlsx`. Nome determinístico = dedup da reimportação. |
| `_aba_catalogo` L3843 | Etapa 2 (padronização IA) só com chave Gemini; o texto de sucesso muda conforme isso. |

### 6.12 Mapeamento manual

| Linha | Regra |
|---|---|
| L3485-3495 | Rótulo de cada tabela (colunas = maior linha; preview da 1ª linha não vazia). |
| L3513-3516 | Pré-visualização: 5 primeiras linhas, completadas/cortadas para `n_cols`. |
| L3544-3549 | Default de cada coluna vem do mapa salvo para a assinatura do cabeçalho. |
| L3559-3563 | Campo repetido avisa; a última coluna escolhida vence. |
| L3569 | Obrigatórios: `descricao` e `quantidade`. |
| L3590 | Aplicação em LOTE (tabela + fatias-continuação de outras páginas). |

### 6.13 Erros

Toda falha de backend vira `erros.mensagem_amigavel(exc, contexto)` + `_log.exception(...)`. Contextos usados: "ler o PDF", "gerar a planilha", "buscar na {Fornecedor}", "consultar o NCM", "salvar as planilhas para importação", "importar as planilhas", "mostrar esta tela", e sem contexto nas buscas manuais/padronização. Exceções: Docling e teste do Gemini mostram o texto cru da exceção.

## 7. Visual (`pregoes/ui/style.py`)

### 7.1 Tokens

`PALETA` (S20): `primary #1E40AF`, `primary_hover #1E3A8A`, `primary_light #DBEAFE`, `bg #F8FAFC`, `surface #FFFFFF`, `border #E2E8F0`, `border_strong #CBD5E1`, `text #0F172A`, `text_secondary #475569`, `text_muted #94A3B8`, `success #15803D` / `success_bg #DCFCE7`, `warning #B45309` / `warning_bg #FEF3C7`, `danger #B91C1C` / `danger_bg #FEE2E2`, `info #1E40AF` / `info_bg #DBEAFE`.
Cores fora da paleta: texto dos avisos `#14532D`/`#78350F`/`#7F1D1D`/`#1E3A8A`; badge neutro `#F1F5F9`; pílula catálogo `#EDE9FE`/`#6D28A9`; pílula aprendido `#FEF3C7`/`#92400E`; inline em `app.py`: `#64748b` (texto secundário dos cards), `#475569` (código), `#2563eb` ("✓ escolhido"), caixa da descrição `#f8fafc`/`#e2e8f0`.
Tipos: `TipoBadge = success|warning|danger|info|neutro`; `TipoAviso = success|warning|danger|info`.

Tipografia: pilha de sistema (`-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`); h1 1.75rem/700, h2 1.25rem/600, h3 1.05rem/600. Tema claro apenas.
Layout: conteúdo com `max-width: 1280px`; sidebar branca com borda direita; cabeçalho/menu/rodapé do Streamlit ocultos; raios 8px (botões/inputs), 10px (cards/avisos/expanders/métricas), 12–14px (uploader/painel de busca); botão primário azul com hover; foco de input com anel `primary_light`; dropzone tracejada.

### 7.2 Componentes

| Helper (linha) | Classe CSS | Uso |
|---|---|---|
| `aplicar_estilo` S499 | — | injeta o CSS único |
| `header_pagina(titulo, subtitulo)` S504 | `.pp-header`, `.pp-sub` | topo de cada tela |
| `stepper(passos, atual)` S513 | `.pp-stepper`, `.pp-step--done/ativo/pendente`, `.pp-step-conector[--done]` | progresso do fluxo |
| `badge(texto, tipo)` S539 / `render_badge` S545 | `.pp-badge--{tipo}` | status da sidebar, chips do painel, condição de pagamento (`render_badge` sem uso) |
| `pill(texto, tipo)` S550 | `.pp-pill--match/barato/catalogo/aprendido` | destaque das opções |
| `bloco_aviso(msg, tipo)` S560 | `.pp-aviso--{tipo}`, ícones ✓ ⚠ ✕ ℹ | TODOS os avisos (aceita HTML: `<b>`, `<code>`, `<br>`) |
| `bloco_avisos(msgs, tipo)` S578 | idem | lista de avisos da extração |
| `card_inicio`/`card_fim` S585/S604 | `.pp-card*` | sem uso em `app.py` |
| `confianca_para_badge(conf)` S608 | — | semáforo da extração (regra em 6.2) |
| `sidebar_marca()` S622 | `.pp-brand*` | logo "P" + nome + versão |
| `sidebar_status(itens)` S638 | `.pp-side-status*` | bloco Status |
| `resumo_pre_busca(itens)` S655 | `.pp-resumo-pre`, `.pp-resumo-item*` | 3 cards antes da busca |
| `painel_progresso_busca(...)` S672 | `.pp-busca-painel/cab/titulo/sub/tempo/barra/barra-fill/counter/item-label/pct/item-desc[.is-idle]/stats/stat` | painel da busca (args: `frac, titulo, sub, tempo, item_atual, item_total, item_descricao, stats, encerrado`; pct = `round(frac*100)` limitado a 0–100; "Aguardando início" quando `item_atual <= 0`; sem bloco de item quando `encerrado`) |
| `linha_metricas(metricas)` S763 | `.pp-metrics`, `.pp-metric*` | métricas coloridas com tooltip (`title`) |
| — | `div[class*="st-key-ppoptsel-"]` (S418) | card de opção ESCOLHIDO: fundo `primary_light`, borda `primary`, faixa de 4px à esquerda |
| — | `div[class*="st-key-ppopt"]` (S427) | densidade dos cards de opção |

Ícones: Material Symbols (`check_circle`, `help`, `cancel`, `block`, `warning`, `edit`, `download`, `delete`); emoji só em "📋" (ícone da página e botão de copiar credenciais), "⚠️", "✨". Mensagens efêmeras: `st.toast` (1 uso), `st.spinner`, `st.progress`.

## 8. Lista de verificação de paridade

**Geral**
- [ ] Página em pt-BR, sem tradução automática do navegador, sem stacktrace na tela
- [ ] Menu lateral: Início, Histórico, Novo pregão, Configurações (ativo só na tela exata)
- [ ] Marca + versão do app no menu
- [ ] Status no menu: Almeida, Dantas (Configurada/Pendente), IA (Gemini) (Configurada/Opcional)
- [ ] Stepper Edital → Itens → Preços → Planilha (passo 4 quando gerado e intocado)
- [ ] Banco corrompido no boot: mensagem clara e parada
- [ ] Erro inesperado em tela: aviso + "Tentar de novo" / "Ir para o Início"
- [ ] Backup diário do banco automático; backup externo no boot (local) e após cada geração
- [ ] Modo servidor × local (textos de Configurações e aba Backup); modo dev (`PREGOES_DEBUG`)

**Início**
- [ ] Métricas: Pregões registrados, Em rascunho, Precificados
- [ ] Botões "Iniciar novo pregão" e "Ver pregões processados"
- [ ] Lista de pregões guardados (até 8), com itens / "busca de preços feita" / "em andamento"
- [ ] "Continuar" restaura o pregão na tela de trabalho
- [ ] "Descartar" com confirmação ("Sim, descartar" / "Cancelar"); descartar o ativo limpa a sessão
- [ ] Pregão já gerado não aparece como guardado (sem apagar o arquivo)

**Novo pregão**
- [ ] Modal "Começar um novo pregão?" com Guardar / Continuar / Descartar quando há trabalho
- [ ] Sem modal quando o pregão foi gerado e não mudou
- [ ] Upload de 1 PDF + Órgão + Número (obrigatórios)
- [ ] Extração com indicação de andamento e erro amigável
- [ ] Reuso automático de mapa aprendido quando a confiança < 95%
- [ ] Nova extração zera todo o estado do pregão anterior

**Revisão**
- [ ] Órgão e número editáveis
- [ ] Semáforo de confiança (alta/média/baixa/sem itens) e 4 métricas
- [ ] Avisos da extração
- [ ] Oferta de Mapeamento Manual (confiança < 95% com tabelas)
- [ ] Mensagens distintas para PDF escaneado × tabela não encontrada
- [ ] Grade editável (buscar preço?, item, descrição, unidade, quantidade, valor de referência), sem incluir/excluir linhas
- [ ] Itens duvidosos nascem desmarcados
- [ ] "Marcar todos" / "Desmarcar todos" sem perder edições de célula
- [ ] Contador "N de M itens marcados…"
- [ ] "Buscar preços" com validações (≥1 marcado, ≥1 fornecedor)
- [ ] "Gerar sem buscar" (edital inteiro sem preços)

**Mapeamento manual**
- [ ] Escolha da tabela com rótulo-resumo e pré-visualização de 5 linhas
- [ ] Carregar tabelas com Docling (quando disponível), com aviso de 5–10 min
- [ ] Campo por coluna (6 opções), pré-preenchido por mapa salvo
- [ ] Aviso de campo repetido; obrigatórios Descrição e Quantidade
- [ ] Opção de salvar o mapa para reuso
- [ ] Aplicar em lote (tabela multi-página) e voltar à revisão

**Busca de preços**
- [ ] Condição de pagamento por fornecedor visível; aviso quando não configurada
- [ ] Resumo pré-busca (itens, fornecedores, cache em dias) + "Iniciar busca" + "Voltar para revisão"
- [ ] Painel de progresso: fornecedor, tempo decorrido, tempo restante, %, item atual, chips por fornecedor
- [ ] Mensagens: login, aguardando outro operador, pausa por limite do site
- [ ] Busca paralela (HTTP) ou sequencial conforme as sessões
- [ ] Falha de um fornecedor não derruba o outro
- [ ] Avisos pós-busca: condição de pagamento não confirmada, fornecedor com zero em tudo, site recusou consultas
- [ ] Re-busca incremental só dos itens sem resultado (mantém escolhas)
- [ ] "Buscar preços novamente" e "Voltar para a revisão" (descartam resultados e escolhas)
- [ ] Métricas coloridas: Itens, Com preço, Por variação, Sem resultado
- [ ] Painel "Ver erros técnicos (N)" (até 50)
- [ ] Lista paginada (20/página), navegação no fim
- [ ] Cabeçalho do item: nº do edital, título legível, status colorido, escolha atual (marca + preço)
- [ ] Descrição completa do edital dentro do item
- [ ] Seleção default automática para todos os itens com opções
- [ ] Card em destaque + "Ver outras N opções" (+ menos relevantes)
- [ ] Pílulas: sua escolha anterior, no seu catálogo, Melhor match, Mais barato
- [ ] Card: marca, descrição encontrada, fornecedor, código, preço em R$
- [ ] "Escolher" troca a seleção; escolhido destacado e sempre visível
- [ ] Opções duplicadas removidas; máximo de opções exibidas
- [ ] "Não é essa? Procurar de outro jeito": Meus produtos × Site do fornecedor
- [ ] Busca no catálogo por palavras (todas) ou código, com referência da última compra
- [ ] "Usar (preço atual)": cotação ao vivo por código; nunca usa preço antigo
- [ ] Busca ao vivo no site por nome/código, por fornecedor; "Usar"
- [ ] Tratamento de limite de consultas (429) e de site instável
- [ ] "Não usar este item" / "Voltar a usar este item"
- [ ] Contador "N de M itens com marca/preço selecionados"
- [ ] Painel de Substituição Tributária: resumo normais / ST / sem NCM
- [ ] "Identificar NCM e classificar (N itens)" com progresso e re-tentativa das falhas
- [ ] Decisão manual Normal × Com ST para itens sem NCM
- [ ] Aviso do desconto Dantas
- [ ] Seletor de margem do preço de venda (15/12/7%), lembrado como padrão
- [ ] "Gerar planilha matriz com preços"

**Geração**
- [ ] Planilha com o edital inteiro; preço/código/marca só nos selecionados
- [ ] Bloqueio sem órgão/número
- [ ] Aprendizado das escolhas gravado ao gerar
- [ ] Registro no histórico (re-gerar o mesmo órgão+número atualiza, não duplica)
- [ ] Falha no histórico não impede o download
- [ ] Download da planilha + resumo (itens com preço, desconto Dantas)
- [ ] "Começar um novo pregão" após gerar

**Rascunho / retomada**
- [ ] Autosave do pregão a cada mudança relevante
- [ ] `?pregao=<slug>` na URL; recarregar a aba retoma o pregão
- [ ] Slug inválido/inexistente limpa a URL e vai ao Início
- [ ] Vários pregões guardados ao mesmo tempo; alternar entre eles
- [ ] Rascunho removido ao gerar com sucesso; mantido se o histórico falhar

**Histórico**
- [ ] Lista de pregões (mais recentes primeiro) com rótulo, status e data em horário de Brasília
- [ ] Resumo: nº de planilhas, itens, com preço/marca
- [ ] Filtro por texto
- [ ] Baixar a planilha mais recente (arquivo do disco); estado desabilitado se o arquivo sumiu
- [ ] Editar órgão/número
- [ ] Excluir pregão + planilhas com confirmação

**Configurações**
- [ ] Credenciais Almeida e Dantas (e-mail + senha), com estado atual
- [ ] "Usar as mesmas credenciais da Almeida" na Dantas
- [ ] Condição de pagamento (forma + prazo opcional) por fornecedor; fecha sessões ao salvar
- [ ] Chave Gemini: salvar, salvar e testar, validação de prefixo, instruções
- [ ] Catálogo: 4 métricas, upload múltiplo de .xlsx, fornecedor principal, importar
- [ ] Importação idempotente (nome de arquivo determinístico) + avisos + padronização IA com progresso
- [ ] Backup servidor: data do último envio, alerta > 2 dias, "Fazer backup agora"
- [ ] Backup local: pasta do Google Drive, salvar, "Fazer backup agora"

**Dev**
- [ ] Salvar diagnóstico (XLSX) + download
- [ ] Limpar cache antes da busca
- [ ] Padronizar nomes do catálogo sob demanda

## 9. Pontos de atenção encontrados na leitura (decidir na reescrita)

1. **Sem dashboard** em `app.py`; nada a portar.
2. **Progresso da IA na extração não é exibido**: `callback_progresso_llm` existe no backend e não é usado (L514).
3. **Retomar pregão na revisão perde edições da grade** (`df_editavel` fora do rascunho; reconstruído de `extracao.itens`, L633).
4. **"Não usar" + busca manual**: `_usar_do_catalogo` e `_usar_do_site` gravam a seleção sem tirar o item de `itens_nao_usar`; a tela continua dizendo "sem preço (você marcou)", mas a planilha sai COM preço (a exportação só olha `selecoes`).
5. **Seleção vinda do catálogo não leva `url`/`ncm`** (L2947): o item nunca é "identificável" pelo botão de NCM.
6. **Re-busca destrutiva não limpa** `itens_nao_usar`, `markup_overrides` nem `site_res_*` (posicionais).
7. **Chaves de sessão que não trocam com o pregão**: `erros_busca`, `avisos_pagamento`, `ncm_cache`, `catalogo_lista` (não recarrega após importar), `margem_venda_col`.
8. **Busca incremental sobrescreve `erros_busca`** com os erros só do sub-lote.
9. **Workers do modo paralelo não são cancelados** quando a tela é interrompida.
10. **Buscas manuais esperam na fila do navegador sem aviso** quando outro operador roda um lote.
11. **"Erros técnicos" mostra `#posição+1`**, não o número do item no edital usado no cabeçalho dos itens.
12. **Pregões guardados: só 8 visíveis**, sem "ver mais".
13. **Histórico baixa só a planilha mais recente**; o README descreve um expander com todas.
14. **Legenda "Linhas em amarelo…"** na revisão sem implementação; expander de ST ainda diz "confira o markup".
15. **Formatação de preço inconsistente** (`_brl` × `R$ 0.00` nas buscas manuais).
16. **Legado sem efeito**: `markup`/`mk_st`/`mk_normal`, `cache_stats`, `_rascunho_disponivel`, ramo `fornecedor.startswith("nenhum")` (L3826), `style.render_badge`/`card_inicio`/`card_fim`.
17. **Aprendizado é gravado antes da validação** de órgão/número da geração.
18. **PDFs enviados** ficam em `data/editais` com nome aleatório e nunca são removidos; salvar credencial não derruba a sessão logada antiga.
