# Questões pendentes para o cliente final

> Este arquivo é vivo. Cada item resolvido sai daqui e entra como decisão registrada na arquitetura.

## Bloco 1 — Defaults provisórios em uso (precisam confirmação para continuar fase 2 em diante)

### Q2. Bootstrap do catálogo histórico (5.000 produtos mencionados)

**Contexto.** O cliente declarou base mental de ~5.000 produtos acumulados em pregões anteriores.

**Default em uso.** Sistema começa do zero. O importador `pregoes/importador.py` está pronto para rodar em lote, mas sem dados de entrada ele não roda.

**Próximo passo necessário.** O cliente nos passar 10-20 planilhas de pregões anteriores no mesmo formato da `MATRIZ`. Idealmente cobrindo categorias variadas: construção, hidráulico, elétrico.

**Impacto.** Sem isso, os primeiros 3-5 pregões serão lentos — nenhum casamento automático. Com isso, ~3-4 mil produtos casados de cara no primeiro uso.

---

## Bloco 2 — Confirmações fechadas (não precisam mais ação)

| Item | Decisão | Origem |
|---|---|---|
| Fornecedores | almeidadistribuidor.com.br + dantasdistribuidora.com.br, navegação pública | Resp. 1, 2 |
| Anti-bot | Sem proteção visível, scraping autorizado pelos parceiros | Resp. 5, 6 |
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

---

### Q6. Heurísticas de extração de PDF — calibradas em uma amostra única

**Contexto.** O `extrator.py` ganhou três filtros para fazer o edital de
Araruna 06/2026 funcionar end-to-end (513 itens, sem repetição, sem fragmento
órfão):

1. **Continuação de tabela entre páginas** — se uma tabela não tem cabeçalho
   próprio mas já vimos um cabeçalho válido antes, assume continuação.
2. **Filtro de "modelo de proposta"** — pula tabelas cujo cabeçalho contém
   `MARCA/MODELO`, para não capturar o anexo de proposta em branco como se
   fossem itens do edital.
3. **Filtro de fragmento órfão** — descarta linhas com descrição < 12 chars
   sem nenhum campo numérico, para eliminar quebras visuais que o pdfplumber
   converte em linha de tabela isolada (ex: `KG.` solto).

**Default em uso.** As três heurísticas estão ativas e foram validadas contra
uma única amostra real (Araruna 06/2026). Funcionam para esse edital.

**Risco.** Editais de outros órgãos podem ter:
- Modelo de proposta com cabeçalho diferente (`FABRICANTE`, `PROCEDÊNCIA`,
  `MODELO/MARCA` em outra ordem) ou idêntico ao do Termo de Referência.
- Tabelas com numeração que começa em outro valor (lotes, sub-itens).
- Itens legítimos com descrição muito curta (raro em material de construção,
  mas possível: "BROCA 1/8", "PARAFUSO 6").
- Linhas de subtotal por categoria que não casam com nosso vocabulário de
  "lixo" (`total`, `subtotal`, `valor global`, `etc.`).

**Próximo passo necessário.** Quando aparecer o **segundo edital real** do
cliente, rodar o extrator nele antes de qualquer fase nova e medir:
- Total de itens extraídos vs. total declarado no edital.
- Falsos positivos (linhas-lixo capturadas).
- Falsos negativos (itens reais descartados pelo filtro de fragmento).

Com 2-3 amostras reais, substituir as heurísticas pontuais por algo mais
robusto. Candidatos: detecção de "número de item retrocede" entre tabelas
consecutivas; parar de extrair após detectar a linha `TOTAL` do Termo de
Referência; concatenar fragmentos órfãos à linha anterior em vez de
descartar; aprender mapeamento por assinatura de cabeçalho via tabela
`mapeamentos_pdf` (que já existe no schema mas ainda não é usada).

**Não bloqueia.** O sistema funciona para o edital atual. É débito técnico
a revisitar quando houver dor real, não preventivamente.

---

## Bloco 3 — Questões para validar com o contador do cliente (fase 3, não bloqueia MVP)

### Q3. Tributação por NF-e

Quando entrarmos na fase 3 (inteligência tributária), validar com o contador:
- Como interpretar diferentes CST/CSOSN para identificar "imposto já pago na origem"
- Se variação por estado de origem/destino exige tabela de equivalência
- Limites legais de re-classificação tributária do mesmo NCM

### Q4. Margem mínima do edital

A coluna `AA = F × 0.5` no template assume valor mínimo aceitável = 50% da referência.
Confirmar com o cliente se essa regra é universal ou varia por tipo de pregão.
