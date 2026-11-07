# Questões pendentes para o cliente final

> Este arquivo é vivo. Cada item resolvido sai daqui e entra como decisão registrada na arquitetura.

## STATUS 2026-06 — o que mudou desde o briefing

- **RESOLVIDO (2026-11-06) — dois editais reais que o extrator não lia (v1.1.13).**
  - *Guarnição de João Pessoa 62/2026* (138 págs.): saía com **0 itens**. A
    quantidade fica na coluna **'Soma'** (total das colunas por quartel) e o
    preço em **'VALOR ESTIMADO'** — nenhum dos dois estava no vocabulário. Além
    disso: a lista do órgão participante (itens 261–420) vem sem cabeçalho e com
    7 colunas em vez de 13; as 60 páginas finais (estimativas mín./máx. por
    quartel) repetiam os 260 itens com quantidades erradas; e a descrição que
    atravessa a página virava um item 'duvidoso' a mais. Agora: 420/420 itens,
    100% de confiança, e Σ quantidade × preço bate com os totais do edital
    (R$ 6.288.442,08 + R$ 479.627,78).
  - *Edital PE SRP 020/2026* (Cotolé do Rocha): coluna única 'Item - Código -
    Descrição' → os 717 itens saíam **sem número**, com '1 - 0056802 - 1 -
    0045799 -' na frente do nome e 6 duplicados (723). Agora 717/717 numerados,
    descrição limpa, 0 divergências de quantidade contra o texto do PDF.
  - Default assumido: 'soma' só vale como quantidade quando nenhuma coluna diz
    'quantidade'; a emenda de continuação só acontece em tabela com coluna de
    nº do item. Testes em `tests/test_fix_extrator.py`.
  - **Em aberto (precisa dos logs do servidor):** no 20/2026 a busca voltou 0
    resultado nos 654 itens nos DOIS fornecedores, enquanto o 15/2026 (03/11)
    achou 54/57. Não é a descrição (a query principal sai igual com ou sem o
    prefixo) — suspeita: janela de instabilidade/sessão dos sites durante o
    lote longo. Conferir `journalctl -u pregoes` / `data/pregoes.log` do dia.

- **RESOLVIDO (2026-06-25) — gerar a planilha não concluía o pregão (modal nagava).**
  Depois de gerar a matriz, o sistema seguia tratando o pregão como "em
  andamento" e mostrava o modal "Começar um novo pregão? (vai descartar…)".
  Corrigido marcando a conclusão ao gerar. **Design robusto via HASH dos insumos**
  (`_hash_pregao`: itens revisados + seleções com NCM + markups global/ST/normal/
  overrides): ao gerar, guarda-se o hash; enquanto o estado bate, não avisa; se o
  operador mexe em QUALQUER insumo, o hash muda e o aviso re-arma sozinho. A
  primeira versão (re-armar manualmente em cada ação) tinha **4 furos** achados por
  uma revisão adversarial (workflow) — NCM, painel de markup, edição da revisão,
  re-extração Docling — onde a mudança pós-geração era descartada em silêncio. O
  hash elimina a classe inteira de "ponto de reset esquecido". Botão "Começar um
  novo pregão" na tela de sucesso. Testes: `test_fix_app.py` (incl. markup/NCM
  re-armam). NÃO-bugs confirmados: órgão/número (metadados), re-busca (fase
  anterior), histórico, "Continuar o atual".

- **RESOLVIDO (2026-06-25) — popup "Clear cache" no Ctrl+C (Windows).**
  O Streamlit tem atalhos de teclado embutidos (`C` = Clear cache, `R` = rerun)
  sem config para desligar. No Windows, **copiar é Ctrl+C** → o `C` abria o
  diálogo de cache; no Mac não acontece (copiar é Cmd+C). `run_app.py` agora
  injeta um script no `index.html` que captura `Ctrl/Cmd + C|R` na fase de
  CAPTURA e só impede a PROPAGAÇÃO (sem `preventDefault`) — copiar/colar
  continuam, mas o atalho interno do Streamlit não dispara. `toolbarMode="minimal"`
  (já no config) **não** desliga esses atalhos. Também unifiquei o `iniciar.bat`
  para subir pelo `run_app.py` (antes rodava `streamlit run` direto e pulava
  ESTE fix e o de tradução pt-BR). Testes: `tests/test_run_app.py`. *Validar no
  Windows no próximo .exe — não reproduzível no Mac (Cmd+C não colide).*

- **RESOLVIDO (2026-06-25) — CONDIÇÃO DE PAGAMENTO agora é aplicada (não pega mais o default).**
  Almeida e Dantas têm o modal "Selecione suas Preferências" (Forma + Prazo); trocar
  a condição muda o preço de todos os produtos. O scraper só logava e buscava →
  pegava o default da sessão (custo possivelmente errado na matriz). Resolvido por
  **condição FIXA por fornecedor** (decisão do cliente), com a inspeção primeiro
  (`scripts/inspecionar_pagamento.py`) que revelou: `<select>` de **name estável e
  igual nos 2 sites** (`id_forma_pagamento_preferencia` / `id_condicao_pagamento_preferencia`),
  opções com **value estável**, e que **Confirmar grava na sessão** (PHPSESSID/token).
  Implementação:
  - `pregoes/dados/opcoes_pagamento.json` — opções (Forma/Prazo, value+texto) por
    fornecedor, capturadas do site. Bundla no .exe (datas=("pregoes","pregoes")).
  - `credenciais.salvar/carregar_preferencia_pagamento(fornecedor)` — guarda a
    escolha (seção `preferencias_pagamento`).
  - Configurações: seletor de Forma + Prazo por fornecedor (`_form_condicao_pagamento`).
  - Scraper: `_aplicar_preferencia_pagamento(page, fornecedor)` **após o login** nos
    dois `__enter__` — abre o modal (gatilho resiliente + verifica visibilidade),
    seleciona por value (fallback p/ texto), Confirmar. 1× por sessão → vale p/
    buscar/por_codigo/catálogo/busca-no-site. **Degradação graciosa**: sem config ou
    modal ausente → segue no default (sem regressão); nunca derruba o login.
  - Transparência: a tela de busca mostra "💳 Preços para a condição → …" e avisa
    quando não configurada. Testes: `tests/test_pagamento.py` (8).
  - **A VALIDAR EM CAMPO:** confirmar na máquina do cliente que o modal abre com o
    gatilho escolhido e o preço muda (o log diz "Condição aplicada" ou "não abri o
    modal"). A inspeção provou a mecânica (cookies/preço), mas o gatilho exato do
    modal não foi 100% isolado — se falhar, o log aponta e ajustamos o seletor.

- **RESOLVIDO (2026-06-24) — planilha matriz volta a conter o edital INTEIRO; IDs do edital na busca.**
  Correção de DOIS entendimentos anteriores meus, esclarecidos pelo cliente:
  - **Exporta TODOS os itens do edital** (na ordem original), com **apenas os
    selecionados preenchidos** (preço/marca/código/NCM/markup). Antes eu havia
    feito o "Gerar com preços" EXCLUIR os não-selecionados (`apenas_selecionados`
    + `_filtrar_selecionados`, que filtrava e reindexava). O pregoeiro precisa do
    edital completo na matriz. Implementação: o `exportar` já iterava todos os
    itens e só preenchia L/T/U/markup/AC onde `selecoes.get(offset)` existe —
    bastou **remover a filtragem** (param `apenas_selecionados`, função
    `_filtrar_selecionados` e a guarda de seleção-vazia foram apagados; código
    morto). O histórico (`_persistir_edital`) passa a gravar todos os itens
    (preço NULL nos não-selecionados) — mais fiel ao edital.
  - **Cabeçalho da busca mostra o ID do EDITAL** (`item.item_numero`), não a
    numeração sequencial 1..N que eu havia colocado. O operador identifica "que
    produto é este no edital?" pelo mesmo número da coluna ITEM da planilha
    (helper `_numero_item_edital`, fallback 1-based quando o PDF não traz número).
  - **Nota de UX aceita:** linhas não-selecionadas mostram `#DIV/0!` na coluna
    MARGEM (fórmula `J/G` com preço vazio) — idêntico ao que o "Gerar sem buscar"
    já produz. Template é sagrado (não mexer em fórmula); se incomodar, vira
    questão futura, fora do escopo desta mudança.

- **NOVO (2026-06-23) — suíte de QA do APRENDIZADO (`tests/test_aprendizado.py`, 25 testes).**
  Cobre as 8 frentes da retroalimentação: DB (guards+UPSERT), reuso fuzzy,
  orquestrador (aprendizado antes do cache, **preço AO VIVO** não o salvo, item
  aprendido não grava cache), salvaguarda 0.30 (fora-de-linha/exceção caem na
  textual sem travar), **captura na UI**, **auto-correção (UPSERT ponta a ponta)**,
  pré-seleção/ranking, e **reuso entre editais**. Dois ajustes de produção que a
  suíte destravou/consolidou:
  - **Fragilidade de captura corrigida:** `_registrar_aprendizado` recuperava o
    fornecedor por re-match `(preço,código,marca)` em `resultados` e descartava o
    aprendizado em SILÊNCIO quando não casava (float/espaço) ou em colisão entre
    fornecedores. Agora `SelecaoFornecedor` carrega o `fornecedor` direto (gravado
    nas 4 construções da UI); o re-match fica só como fallback legado.
  - **Refactor testável:** `_chave_default`/`_melhor` saíram de dentro de
    `_mostrar_selecao` para nível de módulo (`_chave_default_opcao`/`_melhor_opcao`)
    — o ranking de pré-seleção (aprendido > catálogo > código > relevância > preço)
    agora é testado sem `AppTest`.

- **RESOLVIDO (2026-06-23) — QA: eixos elétricos liam medida errada (milhar pt-BR).**
  Auditoria de QA (workflow) achou e verificou ao vivo: volt/watt/ampere eram
  inteiro-puro (`(\d+)`) e colapsavam o milhar pt-BR — `1.500 W` virava
  `('w','500')` (=500W!) e casava um 500W com **relevância 1.0** (produto de
  potência errada como "match perfeito"); `12,5 V` virava `('v','5')`. Pior: o
  reuso fuzzy do APRENDIZADO propagava o código de 500W para um edital de 1500W
  como "sua escolha anterior". Corrigido para decimal+milhar (`(\d+(?:\.\d+)?)`
  + `(?<![/.])`), igual kg/litro — `_norm_valor_dim` normaliza `1.500`→`1500`.
  Também: `_RE_MM` ganhou `(?<![/.])` (o `5/16MM` vazava um `('mm','16')`
  fantasma). +28 testes (eixos, dimensão, acentos, discriminador, fuzzy, E2E).

- **LIMITAÇÃO CONHECIDA (2026-06-23, NÃO corrigida — decisão consciente):**
  - **Milhar de DOIS grupos** (`1.000.000 L` = 1 milhão) some do eixo
    litro/kg/metro (o regex só casa um grupo `.ddd`). Itens de milhão de
    litros/kg não ocorrem no perfil do cliente (microempresa de construção);
    fixar mexeria em regex calibrado por um caso que não aparece. Mantido como
    limitação aceita.
  - **Relevância de medida divergente nem sempre cruza o corte 0.35** (penaliza,
    não some): `CANO 50mm` vs `100mm`=0.40; `ELETRODO E6013` vs `E7018`=0.51;
    `PARAFUSO 5mm` vs `8mm`=0.36. O produto certo **rankeia acima** quando
    existe (o errado vira "clutter", não auto-seleção — o operador escolhe); e
    a calibração atual (8/9 verdadeiros, 0/21 falsos) **não deve ser afrouxada**
    sem dado. Candidato a um "passe de precisão" futuro com dados reais.

- **RESOLVIDO (2026-06-23) — Onda 1: query de busca não destravava 3 casos reais.**
  Diagnóstico em código (texto exato do edital → query): (a) "Alicate
  profissional" → a palavra-ruído "profissional" zerava o AND do site;
  (b) "Alicate de pressão" → AND de 2 termos restritivo; (c) "Revestimento
  textura" → sinônimo (o site usa "grafiato"). **Tudo determinístico, zero IA
  nova, mudanças aditivas (sem regressão):**
  - `_ADJETIVOS_MARKETING` (lista NOVA e estreita: profissional/premium) podada
    só na QUERY (`scraper._montar_camadas`), nunca na relevância, com guarda de
    não-esvaziar. **Não** reusa `_ATRIBUTOS_GENERICOS` (contém aço/cabo/isolante
    → regrediria "cabo de aço").
  - Rede final de **1 keyword de categoria** ("alicate") no fim das camadas — só
    dispara quando todas as anteriores zeram; dá candidatos para a relevância/
    judge ranquearem.
  - `_SINONIMOS_DOMINIO` por **substituição** (textura→grafiato) no
    `normalizar_descricao`. Substituição (não expansão) é obrigatória: medido
    `relevancia("revestimento textura","Grafiato 25kg")=0.0` (filtro mata o
    acerto) vs `0.5` com a substituição (passa). Testes:
    `tests/test_fix_query_busca.py` + `test_fix_normalizador.py` (incl.
    não-regressão cabo/aço e fita/isolante).

- **DECISÃO (2026-06-23) — Onda 2 (IA de re-query) DESCARTADA a favor da
  retroalimentação.** Avaliou-se (workflow adversarial) usar o Gemini para
  sugerir termos de busca quando a busca zera. **O cliente optou por investir
  todos os recursos na RETROALIMENTAÇÃO sustentável** (o sistema aprende com os
  inputs do operador até a assertividade se aproximar de ~100%). Racional que
  sustenta a escolha: o loop já **cura o vocabulário sozinho** — o operador
  resolve um sinônimo novo UMA vez na caixa "Buscar direto no site" e o sistema
  aprende; sem custo de IA por chamada, sem tabela manual a manter. A IA de
  re-query fica como ideia arquivada (exigiria tabela nova no SQLite, §15).

- **QUESTÃO PENDENTE (2026-06-23) — sites Almeida/Dantas são sensíveis a acento?**
  Toda query já vai SEM acento ("pressão"→"pressao", via NFD em
  `normalizar_descricao`). Se o site exigir acento, "alicate de pressão" pode
  continuar zerando mesmo com a rede de 1 keyword. **Default: manter sem acento**
  (não mexer no `normalizar_descricao` às cegas — regrediria dezenas de itens que
  funcionam). **Insumo p/ resolver:** rodar "alicate de pressao" vs "alicate de
  pressão" nas 2 buscas reais (só o ambiente do cliente tem). **Rede imediata:**
  a caixa "Buscar direto no site" já entregue.

- **QUESTÃO PENDENTE (2026-06-23) — curadoria de `_SINONIMOS_DOMINIO` e
  `_ADJETIVOS_MARKETING`.** São tabelas mínimas e curadas; o cliente é quem
  conhece o vocabulário do catálogo. Confirmar: `textura↔grafiato` é seguro? há
  outros pares (ex.: outro nome comercial p/ o mesmo produto)? algum termo de
  marketing a podar além de profissional/premium — sem incluir palavra que seja
  nome de linha de produto? O long-tail fica para a retroalimentação, não para
  inflar estas tabelas.

- **RESOLVIDO (2026-06-23) — retroalimentação ficava enjaulada atrás do cache.**
  A escolha que o operador ensina para um item ("Textura X, cor W → marca A")
  é a inteligência central do produto (o cliente está disposto a investir tempo
  alimentando-a). O orquestrador consultava o **cache (TTL 2 dias) ANTES** do
  aprendizado e dava `continue` — então um item repetido dentro da janela
  voltava com o "melhor match" textual, não com a escolha A. Invertida a ordem
  em [`scraper.py`](pregoes/scraper.py): **aprendizado primeiro, pulando o
  cache**; o resultado aprendido é recuperado por código (preço ao vivo),
  **pré-selecionado** e exibido com o badge **"✓ sua escolha anterior"**
  (`veio_por_aprendizado`). Regressão travada por
  `test_aprendizado_vence_o_cache_na_segunda_busca`.

- **NOVO (2026-06-23) — caixa de busca AO VIVO no site (rede de segurança final).**
  Quando nem a busca automática nem o catálogo trazem o esperado, o operador
  digita **nome ou código**, escolhe o **fornecedor** e busca direto no site,
  **sem filtro de relevância** (vê os cards crus e julga). É também a mitigação
  para casos em que o filtro automático erra. `scraper.buscar_no_site()` +
  UI `_busca_site_item` (por item, ao lado da "Buscar no meu catálogo"). O
  resultado escolhido é APRENDIDO → na próxima vez vem sozinho.

- **DÉBITO TÉCNICO (2026-06-23) — alguns produtos não voltam da busca automática
  apesar de existirem no site (ex.: ALICATE de pressão/corte; REVESTIMENTO
  textura volta só tinta).** Confirmado em código que NÃO é o filtro de
  relevância (cards de alicate passam 0,5–1,0) nem o "número" (não vira
  dimensão). A falha está na **camada viva** (o que o site devolve para o termo
  ou o parse do card) — não reproduzível fora da máquina do cliente. **Para
  isolar:** um item de edital real (texto exato) OU o diagnóstico rodando no
  cliente. Mitigação já entregue: a busca AO VIVO no site (acima). Aguarda
  exemplo do cliente.

- **RESOLVIDO (2026-06) — matcher casava subproduto errado em eixos numéricos.**
  (Era o débito #2 da passada adversarial.) A mineração do catálogo real mudou
  o quadro: RAL e grão-de-lixa **não existem** no catálogo; o caso real é
  **bitola por "nº"** (arame nº12 ≠ nº16, bucha nº10 ≠ nº8, brita n°19 — 45
  produtos) e **classe AWS de eletrodo** (E6013 ≠ E7018). Adicionados dois
  eixos ancorados em `extrair_dimensoes`: `nº<num>` (símbolo º/° inequívoco,
  EXCLUINDO faixas tipo "nº 37 a 44" de botas/EPI, que fariam o tamanho 42
  divergir falsamente) e `e<60xx/70xx>` (classe AWS específica). Calibrado com
  diff catálogo-wide (só arame/bucha/brita/eletrodo mudaram) e travado pela
  rede de caracterização `tests/test_caracterizacao_busca.py`.


- **Q11/Q12 (LLM + busca em 5 camadas): IMPLEMENTADAS e em produção.** O
  Gemini deixou de ser "fallback de extração" e virou **componente central**:
  extração (Camada 2) + canonização do catálogo (Fase B) + judge da busca.
- **Billing pago aprovado.** Custo real **medido**: ~$1-2/mês no volume alvo
  (chamada do judge = 641 tokens ≈ US$0,00006). O free tier (20 req/dia) era
  insuficiente; o tier pago resolve. Substitui a estimativa antiga de "$15/mês".
- **Catálogo populado:** 7 planilhas reais importadas → 1408 produtos
  normalizados pelo Gemini (cobertura de código: 880 com código Almeida ~62%,
  123 com código Dantas ~9% — não é partição do total; alavanca 1).
- **Confiabilidade medida em edital real da especialidade:** busca por código
  ~89-91% certa, ~2% alucinação, 0 nos itens sem correspondência.
- **Prontidão de produção:** guardrails P0 implementados (migration de schema,
  backup automático, log em arquivo, mensagens de erro para leigo, falhas não
  silenciosas, sinalização de PDF escaneado). Ver plano de produção.
- **Empacotamento .exe (P1.1): ENTREGUE e testado no Windows pelo cliente.**
  `run_app.py` (launcher), `pregoes.spec` (PyInstaller), `build_exe.bat`,
  `BUILD.md`; `config.py` ciente de execução *frozen* (dados ao lado do
  `.exe`). Pendências de robustez seguem abertas (P1.4 validar template,
  P1.6 cancelar busca em andamento).
- **DÉBITO TÉCNICO — auto-seed do banco na instalação (implementar após mais
  testes).** Hoje, para levar os dados acumulados (catálogo, aprendizado,
  preços) à máquina do cliente, copia-se manualmente o arquivo
  `data/pregoes_seed.db` → `<pasta do .exe>/data/pregoes.db`. O auto-seed
  embutiria esse seed no `.exe` (via `pregoes.spec`, como já é feito com o
  template MATRIZ) e o `config.py` o copiaria no 1º boot **se não existir**
  `pregoes.db` — zero cópia manual, à prova de erro. NÃO sobrescrever banco
  existente (preserva o que o cliente acumulou). Adiado de propósito até
  estabilizar com mais testes de campo.
  *Mitigação enquanto isso (2026-06-11):* o processo manual está documentado
  passo a passo em `INSTALACAO.md` (instalação nova vs atualização vs
  restauração) e o seed é gerado por `python scripts/gerar_seed.py` (cópia
  consistente via API de backup + limpeza do volátil), não mais à mão.
- **Fase 4 (markup por Substituição Tributária): ENTREGUE e funcionando.**
  Itens cujo NCM consta na lista de ST do estado usam markup 1,30; os demais,
  1,45 — POR ITEM, editável por pregão na UI. Componentes:
  `pregoes/tributacao.py`, `pregoes/dados/ncm_st.json` (389 NCMs), painel
  "Markup deste pregão" + botão "Identificar NCM" na UI. Ver Q3/decisões
  abaixo. **Escopo entregue = SÓ o markup**; a Fase 4 completa (importador de
  NF-e via lxml + preenchimento de %ST/%IPI/%Frete pela fórmula do contador)
  ficou FORA de escopo por decisão do cliente.
- **Catálogo populado:** 7 planilhas reais importadas → 1408 produtos,
  normalizados pelo Gemini. Camada 0 ativa. Cobertura de código Almeida ~62%,
  Dantas só ~9% (alavanca 1 — depende de mais dado do cliente).
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
- ✅ **Tela UI para acionar o importador: RESOLVIDA.** Há tela de importação
  na UI; a importação é transacional por arquivo (commit/rollback por
  planilha) e fica rastreada na tabela `imports_log`. O CLI continua
  disponível.
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

### Q11. Camada LLM (Gemini 2.0 Flash) — ✅ RESOLVIDA (implementada e em produção)

> **Status atual.** O plano abaixo foi **executado**. O Gemini está integrado
> e em produção (extração Camada 2 + canonização do catálogo + judge da busca),
> com billing pago aprovado (ver bloco STATUS no topo). O texto original fica
> como registro histórico da decisão; o "Default em uso: sistema sem LLM"
> abaixo está **superado**.

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

### Q10. Etapa 1.0.B (PyMuPDF + Camelot) — ✅ RESOLVIDA (fora de escopo, decisão fechada)

> **Status atual.** O pipeline de extração estabilizou em pdfplumber + Gemini
> (Docling moveu-se para `requirements-dev.txt`). PyMuPDF e Camelot **não**
> foram integrados e ficam **permanentemente fora do escopo** — a dor que
> justificaria a Camada 1.5 não se materializou. Questão encerrada.

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

### Q2. Bootstrap do catálogo histórico (5.000 produtos mencionados) — 🟡 PARCIALMENTE RESOLVIDA

**Contexto.** O cliente declarou base mental de ~5.000 produtos acumulados em pregões anteriores.

**Status atual (2026-06).** Deixou de ser "começa do zero". O cliente entregou
**7 planilhas históricas**; o importador (`pregoes/importador.py`) rodou e o
catálogo tem **1408 produtos** normalizados pelo Gemini. **Camada 0 ativa** —
busca por código histórico já resolve a maior parte dos itens em editais
recorrentes da especialidade.

**Pendência restante.** Cobertura de código baixa no lado **Dantas** (~9% dos
produtos têm `codigo_dantas`, contra ~62% no Almeida). É a **alavanca 1** de
melhoria da busca e depende de mais dado do cliente — idealmente planilhas
históricas que tragam os códigos Dantas, ou um mapeamento Almeida↔Dantas.

**Próximo passo necessário.** O cliente nos passar mais planilhas de pregões
anteriores no mesmo formato da `MATRIZ`, priorizando as que contenham códigos
do fornecedor **Dantas**. Idealmente cobrindo categorias variadas: construção,
hidráulico, elétrico.

**Impacto.** Quanto maior a cobertura de código (sobretudo Dantas), mais itens
são resolvidos por busca determinística por código, sem disputa de relevância
textual.

---

## Bloco 2 — Confirmações fechadas (não precisam mais ação)

| Item | Decisão | Origem |
|---|---|---|
| Fornecedores | almeidadistribuidor.com.br + dantasdistribuidora.com.br, navegação pública | Resp. 1, 2 |
| Anti-bot | Scraping autorizado pelos parceiros, mas **NÃO é mais "sem proteção"**: desde ~ago/2026 os dois sites respondem 403 a User-Agent com `HeadlessChrome`. Resolvido em v1.1.9 mandando UA de Chrome comum | Resp. 5, 6 + campo 2026-08-31 |
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

### Q6. Heurísticas de extração de PDF — ✅ RESOLVIDA (refatoração concluída, 2026-05-27)

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

### Q3. Tributação por NF-e — ⏳ ABERTA (nunca foi ao contador)

A inteligência tributária via NF-e (importador de XML com `lxml`, extração de
NCM/CFOP/CST/alíquotas, preenchimento das colunas %ST/%IPI/%Frete pela fórmula
do contador) **ficou fora do escopo entregue por decisão do cliente**. O que
entrou da "Fase 4" foi apenas o **markup por ST** (ver decisões abaixo). Se um
dia retomarmos a tributação por NF-e, validar com o contador:
- Como interpretar diferentes CST/CSOSN para identificar "imposto já pago na origem"
- Se variação por estado de origem/destino exige tabela de equivalência
- Limites legais de re-classificação tributária do mesmo NCM

**Status.** ABERTA. Estas perguntas **nunca foram formalmente ao contador**.

### Q4. Margem mínima do edital — ⏳ ABERTA (default virou permanente sem confirmação)

A coluna `AA = F × 0.5` no template assume valor mínimo aceitável = 50% da
referência. Confirmar com o cliente se essa regra é universal ou varia por
tipo de pregão.

**Status.** ABERTA. O fator `0.5` virou **default permanente** no código
(`config.VALOR_MINIMO_FATOR = 0.5`) **sem confirmação do cliente** — segue
valendo até alguém revisar com ele.

---

## Bloco 4 — Decisões da Fase 4 (markup por ST) — APLICADAS, pendentes de aval do contador

> A "Fase 4" entregue cobre **só o markup por Substituição Tributária**
> (1,30 vs 1,45, por item). As duas decisões abaixo já estão em produção, mas
> dependem de uma confirmação formal do contador do cliente para "dirigir
> preço real" com segurança. O cliente **decidiu prosseguir sem essa validação
> por ora** (validação adiada).

### Q13. Markup de Substituição Tributária (ST)

**Decisão.** Itens cujo NCM consta na lista de ST do estado (Anexo 05 do
RICMS/PB) usam markup **1,30**; os demais, **1,45** — **POR ITEM**, editável
por pregão na UI (painel "Markup deste pregão").

**Origem.** Instrução do usuário humano repassando a regra do contador do
cliente (2026-06).

**Status.** ✅ APLICADO em produção (`config.MARKUP_ST = 1.30`,
`config.MARKUP_NORMAL = 1.45`; aplicado item a item por `pregoes/tributacao.py`
+ `pregoes/exportador.py`).

**O que falta.** Confirmação **FORMAL do contador** sobre o valor **1,30**.
Default em uso até lá: 1,30 (ST) / 1,45 (normal).

### Q14. Lista de NCMs com ST (`pregoes/dados/ncm_st.json`)

**Decisão.** A classificação ST se baseia em uma lista de **389 NCMs**
extraídos automaticamente (extração semântica multi-agente) do **Anexo 05 do
RICMS/PB**, bundlada como dado de referência. `eh_st` casa hierarquicamente por
prefixo (NCM completo → 6 dígitos → 4 dígitos), porque o Anexo usa posições
(`'3917'` cobre `'3917.40.90'`). Cobertura: **502/509 CESTs** (7 gaps em
pneumáticos/alimentícios, fora do domínio do cliente).

**Status.** ✅ EM USO. Arquivo versionado no repositório.

**O que falta.** O **contador do cliente REVISAR/ASSINAR** a lista antes de
ela dirigir preço real. O cliente **DECIDIU prosseguir sem isso por ora**
(validação adiada). Atualização quando o Anexo 05 mudar = **re-extrair** a
lista (procedimento futuro, ainda não automatizado).

**Falha de campo 2026-07-06 (corrigida em v1.0.30).** Cliente flagrou tinta
acrílica (NCM 3209.10.10) saindo SEM ST enquanto o esmalte (3208.10.10) da
MESMA linha do Anexo ("Tintas, vernizes — NCM 3208, 3209, 3210.00") saía com
ST: a extração original emitiu só PARTE dos códigos de algumas linhas.
Correção: **+9 NCMs** (389→397, com dedupe) — 7 recuperados por auditoria
interna (códigos citados na `descricao` da própria linha mas não emitidos:
3209, 3206, 4016.93, 0402.2, 0404, 1901, 2106) e 2 por conferência visual do
Anexo (3212, 8702.10.00). A auditoria virou **teste-invariante permanente**
(`test_nenhum_ncm_citado_na_descricao_fica_sem_cobertura`). **Risco residual**:
6 linhas têm a descrição TRUNCADA no meio da lista de NCMs (01.009.00,
10.059.00, 13.001.00, 13.016.00, 17.012.00, 20.040.00) — códigos cortados são
irrecuperáveis do arquivo; só a revisão do contador (acima) fecha esses.

---

## Bloco 5 — Histórico de planilhas geradas (exports_log + tela "Histórico")

> Recurso entregue (jun/2026): cada planilha `.xlsx` gerada passa a ser
> registrada (`exports_log`, append-only) e há tela "Histórico" para
> rebaixá-la. Rebaixar = reler o arquivo do disco (NUNCA re-exportar).
> Design completo em `DESIGN_historico_planilhas.md`.

### Q15. Identidade do pregão e dedup na re-geração — ✅ RESOLVIDA (decisão do cliente)

**Decisão tomada (cliente, jun/2026).**

(a) **Órgão + número do pregão são OBRIGATÓRIOS.** A UI impede chegar à geração
da planilha sem ambos preenchidos (mensagem clara em PT). Garante que a
identidade do pregão está sempre presente.

(b) **Reenvio do mesmo edital (mesmo órgão+número) ATUALIZA o registro anterior
(UPSERT)**, não cria outro. `_persistir_edital` deixou de ser INSERT-cego: a
chave de match é normalizada (trim + lower — tolera `00006/2026` vs
` 00006/2026 ` e diferenças de caixa), exibindo o valor original; reusa o
`edital_id`, atualiza status/PDF e troca os itens (DELETE + reinsere, CASCADE
evita órfãos). Cada geração sempre acrescenta 1 linha em `exports_log`.

**Status.** ✅ APLICADO. Como órgão+número são obrigatórios, a identidade está
sempre presente; ainda assim, se ambos vierem vazios, cai no INSERT-novo
(segurança — não fundir pregões sem identidade).

**O que falta.** Nada — questão encerrada. (Originalmente era um default
provisório de "dedup forte só quando órgão E número preenchidos"; o cliente
confirmou ambas as perguntas.)

### Q16. Planilhas geradas ANTES desta mudança não aparecem no histórico — 🟢 ABERTA, aceita

**Nota.** `exports_log` nasce vazio para editais pré-migração: não havia vínculo
persistido entre o pregão e o `.xlsx`, e `data/exports` pode ter sido limpo.
**Sem backfill.** Adivinhar arquivos por `glob` foi descartado — associar a
planilha errada ao pregão errado é **erro financeiro**. O histórico vale **a
partir da adoção**. A tela mostra "Nenhuma planilha registrada — gere
novamente para rebaixar".

**Status.** Aceita. Comunicar ao cliente que o histórico começa do zero agora.

### Q17. Acúmulo de arquivos em `data/exports` (sem rotação) — 🟢 ABERTA, aceita

**Nota.** Os `.xlsx` gerados acumulam em `data/exports` sem rotação. É
**proposital** — são as versões reaveis pela tela "Histórico"; no volume alvo
(~10 pregões/mês) o acúmulo é irrelevante por anos. Se o cliente reclamar de
espaço, avaliar rotação opcional (manter N últimos por pregão) — futuro, fora
deste escopo.

**Status.** Aceita. Sem ação por ora.

### Q18. Deduplicação de itens repetidos + limitação do "modelo de proposta" — 🟢 ABERTA, parcial

**Contexto (avança a Q6 — 2º edital real).** Muitos editais trazem a lista de
itens **duas vezes**: o **Termo de Referência** (com preço) e o **Modelo de
Proposta** (em branco). Visto no *Edital 011 - 2026 (Gurjão/PB)*: 580 linhas /
53% de confiança, sendo **100% dos "duvidosos" cópias** dos itens já lidos —
só ~305 itens únicos. O cliente relata que essa duplicação é **recorrente**.

**Entregue (v1.0.15).** `extrator._deduplicar_itens` colapsa cópias do mesmo
item por chave `(item_numero, descrição normalizada)`, mantendo a leitura mais
completa e preservando a ordem do edital; quantidades válidas distintas (mesmo
item em lotes diferentes) ficam separadas. Resultado no Edital 011: **305
itens / 100%**, itens distintos de descrição idêntica (#150/#151) preservados.

**Limitação registrada (não corrigida).** O Modelo de Proposta às vezes tem uma
coluna **MARCA/MODELO a mais** (7 colunas), e o mapeamento de cabeçalho não a
reconhece — as colunas saem **deslocadas** (a quantidade cai no campo de preço).
Hoje a **dedup mascara** isso (descarta a cópia desalinhada quando existe a cópia
boa do termo de referência). **Risco residual:** um edital que traga SÓ o modelo
de proposta (sem a tabela com preço) sairia com esses itens mal-parseados.
**Default provisório:** aceitar (raro — editais quase sempre têm o termo de
referência). **Resolver se aparecer:** ensinar o detector de cabeçalho a mapear
a coluna MARCA/MODELO (alinhar UNIDADE/QUANTIDADE/PREÇO corretamente na tabela
de 7 colunas).

### Q19. Sair da máquina única para um servidor — ✅ DECIDIDA (2026-10-15, pelo responsável)

**Contexto.** O cliente precisa usar o sistema em mais de um computador do
escritório, e manter o `.exe` (build + instalação à distância) ficou caro. O
responsável está fora do país, sem acesso à máquina do cliente. Plano e roteiro
em **`PLANO_SERVIDOR.md`**. Revisa a decisão fechada "Onde roda: local na
máquina do cliente" (CLAUDE.md §7) e o princípio de não levar dados para fora da
máquina (§9) — atualizar o CLAUDE.md quando o servidor for validado.

**Decisão (o responsável tem autonomia para decidir pelo cliente):**
1. VPS Hostinger KVM 1 em São Paulo, 1 mês no plano mensal para validar.
   Descartados: Oracle Free (cadastro recusado), AWS (créditos de só 6 meses),
   GCP (1 GB de RAM, só EUA), PC do escritório (exige configurar no local).
2. Acesso pelo Cloudflare Tunnel + Access (código no e-mail, só e-mails
   autorizados), com domínio `.com.br` próprio.
3. Um único login por fornecedor: a fila única do scraper mantém UMA sessão por
   fornecedor, como hoje.
4. Sem migração: o servidor começa do zero — o cliente não tem dados que
   precisem ser levados (decidido em 2026-10-15).
5. Caminho mais simples sempre.

**Condição.** Vale se o teste de IP (Fase 1) passar; se os fornecedores
bloquearem o IP da VPS, pedir reembolso e repensar.

### Q20. Fornecedores limitam consultas por sessão (405) — 🟡 ABERTA, precisa decisão

**Contexto (medido no servidor, 2026-11-06).** Desde 30/10 toda busca em LOTE
falhava (12 tentativas no log). Teste controlado no ritmo do lote (4 threads ×
2 consultas/s por fornecedor): os dois sites aceitaram ~420 consultas cada em
~3,5 min e passaram a responder **405** à sessão logada por **~7 min**. Sem
cookie, a mesma busca seguia 200 → o limite é da SESSÃO/CONTA, não do IP (mudar
de VPS não resolve). Não é o texto da busca. Um edital de ~650 itens faz ~1.500
consultas por fornecedor — estoura o limite com folga. Os preços do Itatuba
(03/11) saíram da busca MANUAL, item a item.

**Já corrigido (v1.1.14):** a recusa não derruba mais o fornecedor inteiro —
antes a exceção da 2ª passada descartava TUDO o que já tinha sido achado (0 de
654 no Pregão 20/2026). Agora o que foi achado fica, o lote para de consultar
depois de poucas recusas (insistir prolonga o bloqueio), os itens restantes
viram erro do item e a tela avisa quantos ficaram sem preço e o que fazer.

**Default provisório:** ritmo do lote inalterado; o operador re-busca os "sem
resultado" depois de ~10 min.

**Alternativa não escolhida (precisa decisão do responsável/cliente):** ficar
ABAIXO do limite — pausa automática de ~7 min a cada ~400 consultas, ou ritmo
menor. Um edital de 650 itens passaria a levar ~15–25 min, mas sairia completo
numa só rodada, sem intervenção. Calibrar o ritmo exige mais 1–2 testes de carga
(cada um custa ~7 min de bloqueio da conta — fazer fora do horário de uso).
