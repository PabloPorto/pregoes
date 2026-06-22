# Relatório de QA — Testes E2E dos fluxos críticos

> Junho/2026. Objetivo: demonstrar a confiabilidade do sistema nos fluxos que
> o cliente apontou como prioritários — busca, catálogo, busca por código, NCM
> e markup — testando-os ponta a ponta, como o sistema realmente roda.

## Metodologia

Os testes E2E exercitam o **orquestrador real** (`buscar_precos_para_itens`)
com **banco SQLite real** (isolado em pasta temporária) e
**matcher / tributação / exportador reais**. O único componente simulado é o
**site do fornecedor**, e mesmo assim no ponto mais profundo (`_buscar_query`)
— de modo que **toda** a lógica de relevância, dimensão, marcação por código,
top-N, cache e camadas roda de verdade. Nada de rede, navegador ou IA externa.

Garantia de isolamento confirmada: rodar a suíte **não altera o banco de
produção** (3245 produtos antes e depois) nem polui os backups.

Áreas já validadas manualmente em campo (e por isso só com validação de
fumaça aqui): **login Almeida/Dantas + chave de IA**, **extração de PDF**,
**exportação da planilha MATRIZ**.

## Cobertura por fluxo (14 cenários E2E + suíte total 469 verdes)

| Fluxo | Cenário testado | Status |
|---|---|---|
| **Busca de preço (textual)** | Traz as marcas relevantes; nada irrelevante é inventado | ✅ |
| **Cache (memória 2 dias)** | 2ª busca vem do cache, **sem** re-bater no site | ✅ |
| **Cache negativo** | Resultado vazio (site instável) NÃO é cacheado → re-busca tenta de novo | ✅ |
| **Busca por ID/código (Camada 0)** | Item casa o catálogo → busca pelo **código** (determinística) | ✅ |
| **Salvaguarda do código** | Código que resolve produto sem relação é descartado, cai na textual | ✅ |
| **Aprendizado (Camada 4)** | Escolha anterior é reusada direto pelo código aprendido | ✅ |
| **Precisão de dimensão** | Edital "diâmetro 100" filtra o tubo 25mm | ✅ |
| **Catálogo irrelevante** | Produto de categoria errada com código no catálogo NÃO aparece (fix 2026-06) | ✅ |
| **Consulta NCM (Anexo 05)** | Classifica ST por 8/6/4 dígitos, com e sem pontos | ✅ |
| **Markup por ST → planilha** | NCM de ST → markup 1,30 e coluna "Sim"; normal → 1,45 e "Não" | ✅ |
| **Override × NCM** | Override obsoleto não vence a classificação por NCM (fix 2026-06) | ✅ |
| **Jornada completa** | Itens → busca (código+textual) → seleção → planilha com preço/código/markup/ST | ✅ |
| **Smoke: extração PDF** | PDF sintético extrai itens > 0 com descrição | ✅ |

Arquivo: `tests/test_e2e_fluxos.py`. Roda em ~2s, offline.

## Resultado

- **469 testes verdes** (suíte completa), dos quais **14 são E2E novos**.
- Os fluxos prioritários do cliente estão **operacionais e cobertos** ponta a
  ponta, incluindo os erros silenciosos corrigidos nesta fase.

## Achados (não bloqueiam entrega)

1. **Acerto por código não recebe o selo "no seu catálogo".** Um resultado da
   Camada 0 (resolvido pelo código exato do catálogo — o sinal mais forte de
   "produto que já compro") volta sem o selo, enquanto um match textual cujo
   código está no catálogo recebe. Inconsistência cosmética de UX; o resultado
   está correto (preço, código, marca). Recomendação: marcar `no_catalogo`
   também no caminho por código. *Baixo.*

2. **Filtro de dimensão é penalidade, não corte rígido.** Dimensão divergente
   reduz a relevância (e nos casos claros derruba abaixo do limite, como o
   25mm vs 100mm testado). Um caso de fronteira com dimensão errada mas texto
   muito parecido pode sobreviver — sempre **abaixo** do correto no ranking.
   Suficiente na prática; endurecer (gate por eixo) é melhoria futura. *Baixo.*

3. **Testes de UI "clicando" (AppTest) ficaram fora desta rodada.** O
   `streamlit.testing` existe e funciona, mas rodar o `app.py` direto dispara
   efeitos de boot (init_db/backup no banco de produção) — precisa antes de um
   `conftest.py` com guarda de DB/rede (já listado na auditoria). A lógica que
   a UI aciona está coberta pelos E2E de domínio acima; o que falta é a camada
   de clique. *Médio — próximo passo de QA.*

## Conclusão

Os fluxos de **busca, catálogo, busca por código, NCM e markup** estão
cobertos por testes E2E que exercitam o sistema como ele roda, todos verdes,
de forma isolada e reproduzível. Isso dá uma rede de regressão sólida para a
entrega e para evoluções futuras — qualquer mudança que quebre um desses
fluxos passa a falhar o CI antes de chegar ao cliente.
