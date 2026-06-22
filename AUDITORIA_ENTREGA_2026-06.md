# Auditoria de Confiabilidade para Entrega — v1.0.0

> Data: junho/2026. Escopo: prontidão do produto para instalação na máquina
> do cliente. Esta auditoria **não repete** a varredura multi-agente profunda
> (em `AVALIACAO_2026-06.md`); ela sintetiza aquele resultado + as correções
> aplicadas e re-verificadas + checagens diretas do estado atual, e converte
> tudo num veredito de entrega.

## Veredito

**GO para piloto acompanhado. NO-GO para "instalar e sumir".**

O núcleo está validado e os riscos de **perda de dados** foram fechados e
re-verificados. O que separa o estado atual de uma entrega tranquila é
**logístico, não funcional**: o `.exe` precisa ser reconstruído com o código
de hoje e testado uma vez no Windows, e o primeiro pregão real precisa ser
acompanhado. Nenhum bug aberto corrompe dados ou trava o operador de forma
irrecuperável.

## Base de evidência

- **433 testes verdes** (`pytest tests/ -q`), incluindo regressão dedicada
  para cada correção desta sessão (`test_fix_*.py`).
- Auditoria multi-agente de 8 dimensões com verificação adversarial
  (`AVALIACAO_2026-06.md`): 8 achados altos + 14 médios, cada um confirmado
  por céticos independentes.
- Correções dos blocos 1–5 aplicadas, e cada bloco re-verificado por céticos
  (2 reprovações pegas e corrigidas antes do commit).
- Preservação de fórmulas da MATRIZ verificada **empiricamente** (503 itens
  contra o template real).

## Confiança por subsistema

| Subsistema | Confiança | Base |
|---|---|---|
| Exportação da MATRIZ (fórmulas) | **Muito alta** | Verificada empiricamente; escrita atômica; validação de template; anti-injeção |
| Persistência / backup | **Alta** | WAL+timeout, backup com `quick_check`, rotação limpa, migrations com backup prévio |
| Extração PDF → itens | **Alta** | Parser BR robusto; célula multi-número agora rejeitada; degradação Gemini→manual |
| Motor de busca | **Alta** | 5 camadas; eixos de medida l/kg/m fechados; cache negativo eliminado |
| Tributação por ST (markup) | **Alta** | Match NCM hierárquico; degradação segura sem o arquivo de ST |
| UI / operador leigo | **Média-alta** | PT-BR, erros traduzidos, semáforos; 1 alto residual (edições perdidas ao navegar) |
| Empacotamento `.exe` | **A validar** | Preparação completa; **rebuild com o código atual ainda não feito** |
| Higiene de testes | **Média** | Sem `conftest.py` com guarda de DB/rede (risco de DEV, não do cliente) |

## O que foi blindado nesta sessão (antes não estava)

Erros silenciosos de dados — os mais perigosos para um operador que confia no
semáforo verde:
- Reuso de mapa aprendido perdia páginas de tabelas multi-página → agora em lote.
- Célula com dois números (`"10\n20"`→`1020`) → agora vira "duvidoso" p/ revisão.
- `markup_overrides` vazava entre pregões (preço ~10% menor em silêncio) → isolado.
- Medida errada (cimento 25kg vs 50kg, caixa 500L vs 1000L) passava no match →
  eixos l/kg/m com comparação numérica (milhar pt-BR e decimais tratados).
- Cache negativo de 2 dias por soluço do site → vazio nunca cacheado + saída
  "buscar novamente".
- Backup contaminável por banco corrompido → `quick_check` como porteiro.
- Build saía sem Chromium (busca morta) → build aborta; deps pinadas.

## Registro de risco residual

| Risco | Severidade p/ entrega | Mitigação | Status |
|---|---|---|---|
| `.exe` não reconstruído com o código atual | **Crítico** | Rebuild + smoke test no Windows **antes** de instalar | **Bloqueia entrega** |
| Busca ao vivo não exercida (login fornecedor com rate-limit nos testes) | Médio | Validar no 1º pregão acompanhado, na máquina do cliente | Aberto |
| Edições da revisão se perdem ao navegar p/ Configurações (alto nº 7) | Médio | Contornável (não corrompe dado); corrigir no próximo ciclo | Aberto |
| `console=True` no `.spec` → janela preta junto do app | Baixo (cosmético) | Trocar p/ `console=False` após o piloto estabilizar | Aberto (proposital) |
| Seed/atualização manual (auto-seed é débito) | Médio (processo) | `GUIA_INSTALACAO_WINDOWS.md` + `scripts/gerar_seed.py` | Mitigado |
| `conftest.py` ausente (suíte pode tocar DB/rede reais) | Baixo (dev) | Não afeta o cliente; criar guarda no próximo ciclo | Aberto |
| Docs internas desatualizadas (README "Fase 1"; QUESTOES diz P1.4 aberto, mas `validar_template` existe) | Baixo | Corrigir textos | Aberto |
| Cross-brand não implementado | Baixo | Busca já resolve ~9/10 sem ele | Aberto (planejado) |
| Credenciais em texto plano (P2.4) | Baixo (contexto local) | Decisão documentada e aceitável | Aceito |

## Gate de pré-entrega (tudo obrigatório)

1. [ ] `python -m pytest tests/ -q` verde na máquina de build.
2. [ ] `python scripts/gerar_seed.py` gera o `data/pregoes_seed.db` do dia.
3. [ ] `build_exe.bat` conclui com `=== PRONTO ===` (aborta sozinho sem Chromium).
4. [ ] Smoke test do `.exe` no Windows: sobe, **versão certa na sidebar**,
       processa um PDF, **busca ao vivo abre o site do fornecedor**, exporta a
       MATRIZ com fórmulas, grava `data\` ao lado do exe.
5. [ ] `data\backups\` recebe o backup diário no 1º boot.
6. [ ] Levar: pasta `dist\Pregoes\` + `pregoes_seed.db` (ver guia).

## Watch list do piloto (primeiras semanas)

- **1º pregão real acompanhado** — é a única forma de validar a busca ao vivo
  nos dois fornecedores em volume.
- Pedir o `data\pregoes.log` após o 1º uso pesado (≥1 edital de ~500 itens) —
  procurar `ERRO`/exceções e tempos de busca.
- Confirmar que o aprendizado cresce (métrica "Nomes padronizados (IA)" sobe e
  o selo "✓ no seu catálogo" aparece em editais seguintes).
- Reforçar a regra de ouro com os 2 operadores: **nunca mexer na pasta `data\`**.

## Rollback e suporte

- **Rollback de versão**: trocar a pasta do programa de volta (preservando
  `data\`); migrations não regridem schema, mas o piloto não deve pular versões.
- **Banco corrompido**: restaurar de `data\backups\` (ver guia, seção emergência).
- **Suporte remoto**: pedir versão (sidebar) + `data\pregoes.log` + print do erro.

## Conclusão

O produto está **funcionalmente pronto e seguro quanto a dados**. A entrega
depende de um único passo operacional ainda não executado — reconstruir e
testar o `.exe` com o código atual — e de acompanhar o primeiro pregão real.
Cumprido o gate acima, é GO para o piloto.
