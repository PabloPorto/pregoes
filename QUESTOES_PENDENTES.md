# Questões pendentes para o cliente final

> Este arquivo é vivo. Cada item resolvido sai daqui e entra como decisão registrada na arquitetura.

## Bloco 1 — Defaults provisórios em uso (precisam confirmação para continuar fase 2 em diante)

### Q10. Etapa 1.0.B (PyMuPDF + Camelot) — adiar até benchmark do Docling

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

### Q2. Bootstrap do catálogo histórico (5.000 produtos mencionados)

**Contexto.** O cliente declarou base mental de ~5.000 produtos acumulados em pregões anteriores.

**Default em uso.** Sistema começa do zero. O importador (`pregoes/importador.py`) ainda não foi escrito — será criado na Fase 3, quando as planilhas históricas chegarem. Sem importador rodado, o catálogo nasce vazio.

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
| Credenciais de fornecedores | `data/credenciais.json` (gitignored). UI tem tela "Configurações" para o operador editar. Só email + senha (CNPJ é one-shot do primeiro acesso, não armazenamos) | Q8 — Resp. 2026-05-15 |
| Almeida: login obrigatório para preço | Login com `email + senha` (campo CNPJ só no 1º acesso). Submit por Enter no campo senha. Sessão via cookie `PHPSESSID + token` | Q8 — Inspeção 2026-05-15 |
| Almeida: parse de produto | Atributos `data-*` em `.product-card__info[data-codigo-produto]`: `data-preco`, `data-marca`, `data-codigo-produto`, `data-nome` | Q8 — Inspeção 2026-05-15 |

---

### Q6. Heurísticas de extração de PDF — refatoração concluída (2026-05-27)

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

### Q3. Tributação por NF-e

Quando entrarmos na fase 3 (inteligência tributária), validar com o contador:
- Como interpretar diferentes CST/CSOSN para identificar "imposto já pago na origem"
- Se variação por estado de origem/destino exige tabela de equivalência
- Limites legais de re-classificação tributária do mesmo NCM

### Q4. Margem mínima do edital

A coluna `AA = F × 0.5` no template assume valor mínimo aceitável = 50% da referência.
Confirmar com o cliente se essa regra é universal ou varia por tipo de pregão.
