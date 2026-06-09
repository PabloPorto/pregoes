# DESIGN — Histórico e rebaixar a planilha gerada

> Status: **desenho aprovado — EM IMPLEMENTAÇÃO (jun/2026).**
> Origem: painel de 3 arquitetos + síntese + revisão adversarial.
> Tudo aqui foi verificado contra o código real (`db.py`, `migrations.py`,
> `config.py`, `ui/app.py`, `exportador.py`). Não viola nenhum anti-padrão.

## 0. DECISÕES CONFIRMADAS PELO CLIENTE (têm precedência sobre o texto abaixo)

1. **Órgão + número do pregão são OBRIGATÓRIOS.** A UI deve impedir chegar à
   geração da planilha sem ambos preenchidos (mensagem clara em PT). Isso garante
   identidade sempre presente.
2. **Reenvio do mesmo edital (mesmo órgão+número) ATUALIZA o registro anterior**
   (UPSERT), não cria outro. A chave de match é normalizada (strip + colapsar
   espaços + casefold), exibindo o valor original.
3. Como (1) garante identidade sempre, a **dedup (ex-Entrega 2) entra JUNTO com o
   histórico agora** — Entrega 1 e Entrega 2 são implementadas na mesma leva.
4. A ressalva de atomicidade (arquivo `.xlsx` órfão raro e inofensivo em caso de
   rollback) é **aceita e documentada**, não bloqueia.

## 1. Problema (confirmado no código)

Hoje, depois que a planilha é gerada, **o programa perde a referência a ela**:

- `exportador.exportar()` salva o `.xlsx` em `config.EXPORTS_DIR` (`data/exports`),
  nome determinístico `pregao_<orgao>_<numero>_<AAAAMMDD_HHMMSS>.xlsx`, e retorna o `Path`.
- A UI (`_gerar_excel`, app.py ~589-617) oferece `st.download_button` **só naquele
  render**. O `Path` nunca entra em `session_state` nem no banco.
- `_persistir_edital` grava em `editais` só o **PDF de entrada** (`arquivo_pdf`) + status.
  Não há coluna nem tabela para o `.xlsx` de **saída**.
- Não há tela de histórico. `_limpar_pregao` ("Novo pregão") zera a sessão.

O **arquivo** sobrevive no disco (achável manualmente), mas não há como, pela interface,
voltar num pregão de semana passada e **rebaixar** a planilha.

## 2. As duas decisões espinha-dorsal (as mais importantes)

### 2.1. Rebaixar = **reler o arquivo do disco**, NUNCA re-exportar
O `.xlsx` já é o artefato final (preço/marca/markup/fórmulas embutidos). Rebaixar
deve servir de volta **os bytes daquele arquivo**.
**Por quê (verificado):** `edital_itens` (db.py:78-91) **não guarda** marca/código/NCM/
markup por item, e `selecoes`/`markups` vivem só em `st.session_state` e somem no
`_limpar_pregao`. Re-rodar `exportar()` seria **lossy** e produziria uma planilha
**diferente** da que o operador validou e entregou. Reler bytes é seguro e idempotente.

### 2.2. Guardar **só o basename**, reconstruir `EXPORTS_DIR/basename` no uso
No banco, gravar apenas o nome do arquivo (`destino.name`), não o caminho absoluto.
**Por quê (verificado):** `config.EXPORTS_DIR` é recomputado a cada boot via
`_raiz_gravavel` (frozen-aware, config.py:13-23,43) — no `.exe`, fica **ao lado do
executável**. Um path absoluto gravado num dia apontaria para o disco antigo e
**quebraria 100% dos rebaixamentos** assim que o cliente movesse a pasta portável.
A existência do arquivo **não** é persistida (envelheceria): é checada com
`Path.exists()` a cada render.

## 3. Modelo de dados — nova tabela `exports_log` (append-only)

Espelho exato de `imports_log` (que já existe): mesma filosofia de rastreabilidade,
mesmo helper sem-commit. **Não** adicionar coluna em `editais` (guardaria só a última
geração e perderia o histórico — que é justamente o que se quer ver).

```sql
CREATE TABLE IF NOT EXISTS exports_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    edital_id     INTEGER NOT NULL REFERENCES editais(id) ON DELETE CASCADE,
    arquivo       TEXT NOT NULL,        -- SÓ o basename; caminho = EXPORTS_DIR/arquivo
    n_itens       INTEGER DEFAULT 0,
    n_selecoes    INTEGER DEFAULT 0,    -- itens com preço/marca (len(selecoes))
    markup_global REAL,                 -- markup do pregão (auditoria)
    status        TEXT NOT NULL DEFAULT 'gerado',
    criado_em     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_exports_edital ON exports_log(edital_id, criado_em DESC);
```

Helper em `db.py`, espelhando `import_log_gravar` (db.py:563-582) — **não commita**,
o caller controla a transação:

```python
def export_log_gravar(conn, edital_id, arquivo, n_itens, n_selecoes,
                      markup_global, status='gerado'):
    conn.execute(
        '''INSERT INTO exports_log
               (edital_id, arquivo, n_itens, n_selecoes, markup_global, status)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (edital_id, arquivo, n_itens, n_selecoes, markup_global, status))
```

## 4. Sequenciamento — DUAS entregas (recomendação do revisor)

O pedido ("ver lista + rebaixar") é **baixo risco**. A correção da duplicação de
`editais`/`edital_itens` na re-geração é um **segundo problema, real mas não pedido
agora**, e carrega o **único risco sério** do desenho (fundir dois pregões que reusem
o mesmo número/órgão = erro financeiro). Por isso, separar:

### ✅ Entrega 1 — Rastreio + Histórico + Rebaixar (o que foi pedido)
Risco mínimo. **Não mexe** na lógica de `editais` além de **adicionar** a chamada
`export_log_gravar` ao final da transação já existente em `_persistir_edital`.
Mantém o `INSERT` atual. O objetivo é **plenamente atingido** mesmo com a duplicação
de hoje: cada geração vira **uma linha** em `exports_log` com seu basename, e a tela
lista todas por data.

### ⏸️ Entrega 2 — Dedup na re-geração (depois, **com confirmação do cliente**)
Trocar o `INSERT`-cego de `_persistir_edital` por **UPSERT condicional** por
`(orgao, numero_pregao)` — só quando **ambos** preenchidos: reusa o `edital_id`,
`DELETE`+reinsere os itens (CASCADE permite). Sem identidade → `INSERT`-novo atual.
**Por que separar:** hoje órgão/número são **opcionais** (app.py:311-315) e costumam
vir vazios ou inconsistentes (`00006/2026` vs `6/2026`) → a dedup quase nunca dispara,
e quando dispara errado funde pregões distintos. Precisa de decisão do cliente
(§9) antes de ligar.

## 5. Tabela de decisões arquiteturais (com implicâncias)

| Decisão | Escolha | Implicação | Alternativa rejeitada |
|---|---|---|---|
| Onde guardar o `.xlsx` gerado | Tabela `exports_log` (append-only, FK `edital_id`) | Preserva o **histórico** de todas as gerações; reusa molde `imports_log`; zero `ALTER` em `editais` | Coluna em `editais`: só a última geração, perde histórico, mistura entrada/saída |
| Caminho absoluto/relativo/basename | **Só o basename** | Imune a mover a pasta e a atualizar o `.exe` | Path absoluto: stale ao mover a pasta portável (cenário `.exe`) |
| Como rebaixar | **Reler bytes** do arquivo salvo | Zero risco de planilha diferente da validada; reusa padrão de `_gerar_excel` | Re-exportar: lossy (itens não guardam marca/código/markup) |
| Arquivo ausente (movido/apagado) | `Path.exists()` em runtime → aviso amarelo, sem botão | Nunca estoura `FileNotFoundError` na cara do leigo (P0.4/P0.5) | Flag persistida: vira mentira no instante seguinte |
| Quando gravar o log | Na **mesma transação** de `_persistir_edital` (`edital_id=lastrowid`) | Edital e log nascem juntos no banco; rollback descarta ambos | Transação separada: janela de edital sem log |
| Flag "versão atual" por pregão | **Sem flag** — vigente = `ORDER BY criado_em DESC LIMIT 1` | Menos superfície, menos invariante a sincronizar; suficiente p/ ~10/mês | Bit `atual` 0/1: over-engineering p/ o pedido |
| Status ao gerar/rebaixar | **Mantém** rascunho/precificado; rebaixar não grava nada | Rebaixar é idempotente; KPIs da home inalterados | Setar `'exportado'`: muda a semântica que a home conta |

## 6. Migração (segura — trilho P0.1 já pronto)

Em `migrations.py` (lista `MIGRATIONS` hoje vazia, linha 39):

```python
def _v2_exports_log(conn):
    conn.executescript(_DDL_EXPORTS_LOG)   # CREATE TABLE/INDEX IF NOT EXISTS

MIGRATIONS = [(2, "exports_log (histórico de planilhas geradas)", _v2_exports_log)]
```

`migrate()` (já existe) adota o banco do cliente como **baseline v1** sem tocar nos
dados, faz `db.backup('pre-migration')` **antes** (protegido da rotação), aplica em
transação, é idempotente (`schema_version` barra reaplicação). Espelhar a mesma DDL
em `db.SCHEMA` para bancos **novos** nascerem com a tabela. `init_db` já chama `migrate`.

**Mudança aditiva pura** (`CREATE IF NOT EXISTS`) → **zero risco** aos dados de
produtos/editais/preços.

**Sem backfill:** gerações **anteriores** a esta atualização não entram no histórico
(não havia vínculo persistido e `data/exports` pode ter sido limpo). **Não** adivinhar
arquivos por `glob` — associar a planilha errada ao pregão errado = erro financeiro.
`exports_log` nasce vazio para editais pré-migração; a tela mostra "Nenhuma planilha
registrada — gere novamente". O histórico vale **a partir da adoção**.

## 7. UI — nova tela "Histórico" (reaver/rebaixar, **não** re-editar)

- **Roteador:** registrar `'historico': tela_historico` em `TELAS` (app.py:1936-1943)
  e um item na sidebar entre "Início" e "Novo pregão". Não depende de `session_state`/
  `CHAVES_PREGAO` — lê do banco/disco.
- **`tela_historico`:**
  ```sql
  SELECT e.id, e.orgao, e.numero_pregao, e.status, e.criado_em,
         COUNT(x.id) AS n_planilhas
  FROM editais e LEFT JOIN exports_log x ON x.edital_id = e.id
  GROUP BY e.id ORDER BY e.criado_em DESC
  ```
  Lista (leitura, não `data_editor`), rótulo amigável em PT
  ("Pregão 00006/2026 — Prefeitura de Araruna — precificado em 09/06"); sem
  identificação → "Pregão sem identificação (de DD/MM HH:MM)".
- Cada pregão num `st.expander` revela suas planilhas (`exports_log`, mais recente no
  topo). Para cada: `caminho = config.EXPORTS_DIR / row['arquivo']`; se existe →
  `st.download_button` lendo os bytes (reusa padrão de `_gerar_excel`); se não →
  `style.bloco_aviso` amarelo "Esta planilha não está mais na pasta do programa
  (pode ter sido movida ou apagada). Gere de novo a partir do pregão."
- `home`: botão "Ver pregões processados" → `ir_para('historico')` (+ KPI opcional
  "Planilhas geradas").
- Tudo em PT, sem expor path absoluto (só o nome do arquivo), sem jargão.

## 8. Casos de borda e ressalvas (incl. correções do revisor)

- **Atomicidade é banco-banco, não banco-disco.** `exportar()` grava o `.xlsx` no disco
  (`os.replace`, irreversível) **antes** de `_persistir_edital`. Se o `INSERT`
  rolar back, sobra um **`.xlsx` órfão** no disco (sem registro) — **inofensivo**
  (só não aparece no histórico). Aceitar e documentar; não vale mover `exportar()`
  para dentro da transação só por isso.
- **"Gerar sem buscar"** (planilha em branco, `selecoes={}`, app.py:549) **também**
  cria linha no histórico e arquivo rebaixável. Decisão: **sim** (é um artefato gerado
  legítimo) — registrar para não ser implícita.
- **Arquivo ausente:** registro permanece (append-only); só some o botão de download.
- **Número digitado diferente** entre gerações (`00006/2026` vs `6/2026`): na Entrega 2,
  tratados como pregões distintos. Mitigar normalizando a **chave do match** (strip +
  colapsar espaços + casefold), exibindo o valor original.
- **`edital_itens` sem FK de entrada:** confirmado que nada aponta para `edital_itens.id`
  (só saem `edital_id`→editais e `produto_id`→produtos) → o `DELETE`+reinsert da
  Entrega 2 **não** cria órfãos.
- **CASCADE inerte hoje:** não há `DELETE FROM editais` em lugar nenhum. Se um dia
  houver "apagar pregão" na UI, o histórico de planilhas some junto (provavelmente
  desejável) — anotar.
- **Docstring de `db.py`:** já está **desatualizado** (diz "11 tabelas", mas hoje são
  **12**). Pós-feature serão **13**. Corrigir com a contagem real + listar `exports_log`.
- **Colisão de basename no mesmo segundo:** 2 linhas apontando o mesmo arquivo (a 2ª
  sobrescreve no disco). Raro no fluxo manual; anotado, sem ação.

## 9. Plano de testes (novo `tests/test_historico_exports.py`)

- Migration v2 **idempotente e aditiva**: rodar `migrate()` 2× num banco v1 popular
  não duplica tabela nem perde dados; `schema_version`→2; backup `pre-migration` criado.
- `db.SCHEMA` novo: `init_db` do zero já cria `exports_log` + índice.
- `export_log_gravar`: insere 1 linha; **não** commita por si (rollback do caller descarta).
- **Atomicidade:** forçar erro no log e confirmar que o edital também rola back.
- **Basename resolve:** gravar basename, reconstruir `EXPORTS_DIR/basename`, abrir → bytes batem.
- **Arquivo ausente:** registro existe mas arquivo apagado → render não levanta exceção.
- **Frozen-aware:** monkeypatch `config.EXPORTS_DIR` e confirmar que a leitura usa o
  `EXPORTS_DIR` corrente, não um path gravado.
- **Não-regressão dos KPIs da home** (`GROUP BY status`) — especialmente se a Entrega 2 entrar.
- (Entrega 2) Re-gerar mesmo pregão identificado → 1 edital + 1 conjunto de itens + N exports.
- (Entrega 2) Gerar sem identidade → editais distintos (não funde) + N exports.
- Suíte completa verde antes e depois (sem tocar `exportador.py`/template).

## 10. Mudanças por arquivo

| Arquivo | Mudança |
|---|---|
| `pregoes/migrations.py` | `_DDL_EXPORTS_LOG` + `_v2_exports_log`; `MIGRATIONS=[(2,...)]` |
| `pregoes/db.py` | Espelhar DDL em `SCHEMA`; helper `export_log_gravar`; corrigir docstring (12→13 tabelas) |
| `pregoes/ui/app.py` | `_gerar_excel`→ passa `destino`/`markup` a `_persistir_edital`; este chama `export_log_gravar` na mesma transação (Entrega 1) e faz UPSERT condicional (Entrega 2); nova `tela_historico`; registrar em `TELAS`/sidebar; atalho na home |
| `tests/test_historico_exports.py` | novo |
| `README_TECNICO.md` / `QUESTOES_PENDENTES.md` | documentar `exports_log` + tela; registrar §9 |

## 11. Esforço e garantias

- **Entrega 1:** ~0,5 dia (migration + helper + tela + rebaixar + testes).
- **Entrega 2:** ~+0,5 dia (UPSERT + testes de dedup), **após confirmação do cliente**.
- **Zero-regressão:** `exportador.py`/template/nome de arquivo **intocados**; schema só
  aditivo via migration com backup protegido; SQLite/Streamlit/`sqlite3` puro mantidos;
  nenhuma dependência nova; nenhuma abstração prematura.

## 12. A registrar em `QUESTOES_PENDENTES.md` (antes de implementar)

**Q — Identidade do pregão e dedup na re-geração.** Default provisório: dedup forte
(UPSERT) só quando `orgao` E `numero_pregao` preenchidos; senão `INSERT`-novo (atual).
Cada geração sempre acrescenta 1 linha em `exports_log`. *Precisa confirmar com o
cliente:* (a) tornar órgão+número obrigatórios? (b) mesmo edital reenviado deve
atualizar o registro anterior ou criar outro?

**Q — Planilhas geradas antes desta atualização não aparecem no histórico.** Não há
vínculo persistido e `data/exports` pode ter sido limpo; backfill por `glob` é
arriscado (erro financeiro). Histórico vale a partir da adoção. Comunicar ao cliente.

**Q — Acúmulo de arquivos em `data/exports` (sem rotação).** Proposital (versões
reaveis); irrelevante no volume por anos. Se o cliente reclamar, avaliar rotação
opcional (manter N últimos por pregão) — futuro, fora deste escopo.
