# Avaliação técnica do projeto — junho/2026

> Auditoria multi-agente (8 dimensões, ~87 agentes, verificação adversarial por
> lentes independentes) executada em 2026-06-10/11. Cada achado "confirmado"
> sobreviveu a verificadores instruídos a derrubá-lo (3 lentes para alto/crítico:
> correção no código real, reprodução de cenário concreto, impacto no contexto).
> Achados "não verificados" têm evidência forte do revisor mas não passaram pelo
> júri adversarial (limite de uso interrompeu a rodada final).

**Veredito geral: 7,3/10.** Núcleo sólido e fiel aos 3 princípios do briefing.
O padrão dominante dos riscos restantes é **erro silencioso** — o sistema
entregando resultado errado ou incompleto *com aparência de sucesso* (semáforo
verde, banner de êxito). Para operador leigo, é a classe de bug mais perigosa.

| Dimensão | Nota | Confirmados adversarialmente |
|---|---|---|
| Segurança | 8,5 | 1 médio |
| Extração/Exportação | 7,5 | 2 altos, 5 médios |
| Arquitetura/Docs | 7,5 | (verificação parcial) |
| Produção/Deploy | 7,5 | (verificação parcial) |
| Busca/Scraper | 7,0 | 3 altos, 3 médios, 1 refutado |
| Persistência | 7,0 | 6 (médios/baixos) |
| UI/Operador | 7,0 | 3 altos |
| Testes | 6,5 | (verificação parcial) |

## Pontos fortes confirmados

- **Exportador** é o melhor módulo: escrita atômica (`.part` + `os.replace`),
  preservação de fórmulas verificada empiricamente com 503 itens contra o
  template real (G5 intacto, G507 com markup ST traduzido, SUMs ajustadas),
  validação fail-fast em português, anti-injeção de fórmula.
- **Persistência acima da média**: WAL + busy_timeout centralizados, backup via
  API nativa do SQLite, migrations versionadas com backup prévio, importação
  transacional por arquivo.
- **Segurança proporcional ao contexto**: UI presa ao loopback em 3 pontos
  independentes, SQL 100% parametrizado, nenhum segredo no histórico do git,
  trade-off da senha em texto plano documentado honestamente.
- **402 testes verdes** com disciplina de regressão (`test_fix_*.py` com a
  história de cada bug).
- Cultura de documentar porquês e defaults provisórios (QUESTOES_PENDENTES.md).

## Gaps ALTOS confirmados (8)

| # | Achado | Onde | Status |
|---|---|---|---|
| 1 | Reuso de mapa aprendido usa `aplicar_mapa` (fatia única) e perde as demais páginas da tabela — entrega p.ex. 30 de 500 itens com banner "Mapa aprendido reusado" | `ui/app.py:361` | **corrigido 2026-06-11** |
| 2 | Célula multi-linha `"10\n20"` vira quantidade `1020.0` com confiança alta (reproduzido end-to-end com PDF sintético) | `extrator.py:151` | **corrigido 2026-06-11** |
| 3 | Gate de dimensão não cobre litros/kg/metros — cimento 25kg passa com relevância 0,8 para edital de 50kg e pode virar "Melhor match" | `normalizador.py:425` | **corrigido 2026-06-11** (eixos l/kg/m; ml/g convertidos; "BARRA/COMPRIMENTO nM" excluído do eixo m por decisão da revisão cross-brand) |
| 4 | Aprendizado fuzzy (Camada 4) propaga código do produto de medida errada como `exato` e suprime a busca textual | `matcher.py:273` | **corrigido 2026-06-11** (o gate novo flui para `_dimensao_diverge`) |
| 5 | Sessão caída no meio do lote grava cache negativo de 2 dias ("Não encontrado" em massa), sem botão de limpeza | `scraper.py:1129` | **corrigido 2026-06-11** (vazio não grava; vazio legado lido como miss; botão "Buscar novamente") |
| 6 | `markup_overrides` (keyed por offset 0..n) sobrevive entre pregões — markup ST do pregão anterior aplica no item de mesmo offset do novo, planilha ~10% mais barata em silêncio | `ui/app.py:160` | **corrigido 2026-06-11** |
| 7 | Edições de célula da revisão se perdem ao navegar para Configurações e voltar (fluxo induzido pela própria UI) | `ui/app.py:527` | aberto |
| 8 | Sem saída pós-busca: não há "buscar novamente" nem "voltar à revisão" — busca falhada obriga a recomeçar o pregão | `ui/app.py:1094` | **corrigido 2026-06-11** (expander com re-busca + voltar) |

## Gaps MÉDIOS confirmados (seleção)

- Aviso de truncamento do Gemini apagado ao aceitar resultado (`extrator.py:624`) —
  edital de 93 páginas perde itens finais sem sinal.
- `_eh_modelo_de_proposta` descarta tabela de itens legítima com coluna MARCA
  (`extrator.py:262`).
- Blocos de tabela abaixo do threshold descartados sem aviso (`extrator.py:467`).
- Mapeamento manual sempre descarta a 1ª linha da tabela-base como cabeçalho
  (`mapeamento_manual.py:189`) — perde o 1º item se a fatia começa com dados.
- Expansão >500 itens sem teste; ajuste de totais por substituição de string
  (`exportador.py:356`).
- Cache TTL 2d curto-circuita o aprendizado — correção do operador não tem
  efeito por até 2 dias (`scraper.py:1011`).
- Cache do judge: chave insensível à ordem × payload com índices posicionais —
  pode aprovar o candidato que rejeitou (`extrator_llm.py:404`).
- `buscar_por_codigo` aceita o 1º card sem conferir o código devolvido
  (`scraper.py:510`).
- `db.backup()` ignora o dir de origem — `data/backups/` com 622 arquivos, 614
  de migração, maioria de bancos de TESTE protegidos contra rotação (`db.py:259`).
  **Corrigido 2026-06-11**: destino deriva da origem (`path.parent/backups`);
  653 arquivos de teste movidos para `data/backups/_quarentena_testes/`.
- Banco novo adotado como baseline v1 — primeira migration com `ALTER TABLE`
  quebraria o boot de instalação nova (`migrations.py:89`); `executescript`
  commita no meio (`migrations.py:106`).
- Nenhum `PRAGMA integrity_check` antes do backup diário — corrupção silenciosa
  contaminaria as 10 cópias da rotação em ~10 dias (`db.py:239`).
  **Corrigido 2026-06-11**: `quick_check` como porteiro — banco corrompido não
  gera backup e levanta erro claro.
- Seed DB com dados reais do cliente fora do `.gitignore` (**corrigido 2026-06-11**).

**Refutado (falso positivo):** "judge Gemini nunca dispara para sinônimos" — o
gatilho existe como descrito, mas a premissa de que sinônimos zeram a busca
textual não se sustentou.

## Não verificados por júri, confirmados por checagem direta

- `HANDOFF.md` desatualizado em 2 fases ("próxima frente é Fase 3").
- `requirements.txt` com 1 pin em 43 linhas — rebuild do .exe embarca versões
  nunca testadas. **Corrigido 2026-06-11** (9 deps diretas pinadas nas versões
  testadas pela suíte).
- `build_exe.bat:42` não checa erro do `playwright install chromium` — pode
  sair .exe com a busca quebrada. **Corrigido 2026-06-11** (build aborta; o
  `pregoes.spec` também falha sem `ms-playwright`, com override de dev
  `PREGOES_BUILD_SEM_CHROMIUM=1`).
- `sys.modules.pop("pregoes.config")` em `tests/test_fix_config.py:81` — a
  causa-raiz da poluição de DB entre testes (bc557d4) persiste.

## Não verificados (evidência forte, sem júri)

- Testes: suíte abre `data/pregoes.db` real em alguns caminhos; sem
  `conftest.py` com guarda sistêmica de DB/rede; zero fixture de HTML real do
  scraper (mudança de layout do site passa verde).
- UI: F5 descarta o pregão inteiro (nada persiste até gerar planilha); busca
  síncrona sem cancelamento; exceções cruas em caminhos Gemini/Docling.
- Deploy: `.streamlit/config.toml` empacotado em caminho que o .exe não lê;
  atualização manual pode sobrescrever `data\pregoes.db`; sem guarda para
  Program Files/OneDrive; seed por cópia manual com `-wal/-shm`.
- Docs: README.md (cliente) descreve o sistema em meio à Fase 2; CLAUDE.md
  inconsistente sobre Q2; `config.py` documenta paralelismo inexistente.
- Nota honesta: o "lixo AAA→anel" citado pelo revisor de testes **não foi
  encontrado** no banco atual.

## Gaps já conhecidos (fora da auditoria)

- Cross-brand: desenhado e revisado adversarialmente, não implementado.
- Auto-seed do banco na instalação: débito técnico registrado.
- P1.6 do plano de robustez (timeout/cancelamento de busca) — coincide com o
  achado alto nº 8.

## Prioridades recomendadas

1. **Mordidas pequenas, dano alto** (nos 1, 2, 6 + gitignore) — ✅ feitas em 2026-06-11.
2. **Trinca da medida** (nos 3+4): eixos L/kg/m + gate na Camada 4 — ✅ feita em
   2026-06-11 (com exclusão deliberada de "BARRA/COMPRIMENTO nM").
3. **Cache negativo + saída pós-busca** (nos 5+8) — ✅ feita em 2026-06-11.
4. **Rede de segurança dos dados**: `integrity_check` pré-backup + backups de
   teste fora de `data/backups/` — ✅ feitos em 2026-06-11. Pendente: baseline
   de banco novo = versão mais recente (migrations).
5. **Antes de escalar instalação**: requirements pinado + build abortando sem
   Chromium — ✅ feitos em 2026-06-11. Pendente: `conftest.py` com guarda de
   DB/rede.

### Ainda em aberto (próxima rodada)
- Altos nº 7 (edições da revisão se perdem ao navegar) e o restante dos médios
  de extração (avisos engolidos, `_eh_modelo_de_proposta`, mapeamento manual
  1ª linha, >500 itens sem teste).
- Médios de busca: cache TTL curto-circuita aprendizado; cache do judge com
  índices posicionais; `buscar_por_codigo` sem conferir código.
- Migrations: baseline de banco novo; `executescript`.
- `conftest.py` com guarda sistêmica de DB/rede; fixture de HTML real do scraper.
- Cross-brand (desenhado); auto-seed (débito registrado).
