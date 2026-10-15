"""
Configurações centrais do sistema de automação de pregões.

Todo parâmetro que pode mudar fica aqui. Nada de constantes espalhadas
pelo código. Se o cliente quiser alterar markup, overhead, cache, etc.,
mexe só neste arquivo.
"""
from datetime import timedelta, timezone
from pathlib import Path
import os
import shutil
import sys


def _raiz_gravavel() -> Path:
    """Raiz dos dados GRAVÁVEIS (db, backups, exports, credenciais, log).

    - Empacotado (.exe via PyInstaller, ``sys.frozen``): pasta AO LADO do
      executável — a pasta "Pregões" é portável e os dados do cliente persistem
      entre atualizações do .exe. (Dentro do bundle é somente-leitura/temp.)
    - Desenvolvimento: a raiz do repositório — comportamento INALTERADO.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _raiz_bundle() -> Path:
    """Raiz dos recursos EMPACOTADOS, somente leitura (o template MATRIZ).

    PyInstaller extrai os dados em ``sys._MEIPASS`` (onefile) ou na pasta do exe
    (onedir). Em dev, é a raiz do repositório.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS",
                            Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


# Versão do aplicativo — aparece na sidebar e no log de boot. Sem ela, o
# suporte remoto fica às cegas sobre o que o cliente está rodando (auditoria
# 2026-06). Bump a cada .exe entregue ao cliente.
VERSAO_APP = "1.1.12"

# Rodando no servidor (deploy/pregoes.service define PREGOES_SERVIDOR=1) em vez
# do .exe na máquina do cliente: muda o que é verdade para o operador (onde os
# dados ficam, como é o backup — ver backup.backup_externo_agora).
MODO_SERVIDOR = bool(os.environ.get("PREGOES_SERVIDOR"))

# Fuso das datas/horas mostradas ao operador. O SQLite grava em UTC
# (CURRENT_TIMESTAMP) — sem converter, o Histórico mostrava tudo 3h
# adiantado. Offset fixo: o Brasil não tem horário de verão desde 2019, e
# assim não dependemos da base de fusos (tzdata), ausente no Windows.
FUSO_HORARIO = timezone(timedelta(hours=-3), "BRT")

# Estrutura de diretórios
BASE_DIR = _raiz_gravavel()
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = DATA_DIR / "templates"
EDITAIS_DIR = DATA_DIR / "editais"
EXPORTS_DIR = DATA_DIR / "exports"
# Planilhas históricas do cliente (formato MATRIZ) que alimentam a Camada 0
# da busca (catálogo + códigos). Precisa existir já numa instalação nova,
# senão o importador (python -m pregoes.importador data/historico/*.xlsx)
# falha por pasta inexistente antes mesmo de o operador colocar arquivos.
HISTORICO_DIR = DATA_DIR / "historico"
DB_PATH = DATA_DIR / "pregoes.db"
TEMPLATE_MATRIZ = TEMPLATES_DIR / "matriz_template.xlsx"

# Garantir que pastas existam
for d in (DATA_DIR, TEMPLATES_DIR, EDITAIS_DIR, EXPORTS_DIR, HISTORICO_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Semente do template: vem de DENTRO do pacote (`pregoes/dados/`), que viaja
# SEMPRE junto com o código. A pasta `data/` (fora do pacote) pode não ser
# copiada por inteiro — o Google Drive deixou de baixar o `matriz_template.xlsx`
# numa cópia de pasta (incidente 2026-07-11), quebrando o build/exportação.
# Copiado UMA vez para a pasta gravável `data/templates/`. Em dev, se o destino
# já existe, é no-op. Fallback: bundles antigos traziam o template em
# data/templates/ dentro do _MEIPASS.
_TEMPLATE_BUNDLE = Path(__file__).resolve().parent / "dados" / "matriz_template.xlsx"
if not _TEMPLATE_BUNDLE.exists():
    _TEMPLATE_BUNDLE = _raiz_bundle() / "data" / "templates" / "matriz_template.xlsx"
if (not TEMPLATE_MATRIZ.exists() and _TEMPLATE_BUNDLE.exists()
        and _TEMPLATE_BUNDLE.resolve() != TEMPLATE_MATRIZ.resolve()):
    shutil.copy2(_TEMPLATE_BUNDLE, TEMPLATE_MATRIZ)

# === Parâmetros financeiros (espelham o template MATRIZ) ===
MARKUP_DEFAULT = 1.45               # Multiplicador padrão (= MARKUP_NORMAL; retrocompat)
# Markup por classificação tributária (Fase 4). Item COM Substituição
# Tributária usa markup MENOR — o ICMS já foi recolhido na origem, então a
# margem de formação de preço é diferente. Defaults editáveis por pregão na UI.
MARKUP_NORMAL = 1.45                # item SEM substituição tributária
MARKUP_ST = 1.30                    # item COM ST (NCM consta no Anexo 05 do estado)
# Acordo comercial com a Dantas: o preço trazido do site da Dantas entra com
# este desconto no CUSTO (coluna L). O markup incide sobre o custo já com
# desconto. Almeida e catálogo não recebem (decisão do cliente 2026-06-27).
DESCONTO_DANTAS = 0.17              # 17% de desconto no custo dos itens da Dantas

# === Margem do preço de venda (modelo do contador, 2026-07) ===
# A planilha calcula 3 preços sugeridos por gross-up, um por margem-alvo, nas
# colunas Z/AA/AB. O app pré-preenche o PREÇO VENDA (G) apontando por FÓRMULA
# para a coluna do cenário que o operador escolher (G = "=Z{linha}" etc.) — sai
# pronto, já ajustado para ST, e continua editável. Espelha Z2/AA2/AB2 do
# template: se o contador mudar as margens lá, atualizar aqui.
MARGENS_VENDA = {"Z": 0.15, "AA": 0.12, "AB": 0.07}   # coluna → margem-alvo (ordem = exibição)
MARGEM_VENDA_PADRAO = "AB"                            # padrão de fábrica: 7% (decisão 2026-07-11)

PERC_OPERACIONAL = 0.03             # 3% operacional
PERC_COMISSAO = 0.05                # 5% comissão
PERC_IMPOSTOS_FEDERAIS = 0.07       # 7% impostos federais
VALOR_MINIMO_FATOR = 0.5            # Mínimo aceitável = 50% do valor de referência

# === Cache de preços ===
CACHE_PRECO_DIAS = 2                # TTL conforme decisão (resp 9)

# Máximo de opções EXIBIDAS por item na tela de seleção. A busca traz até 5
# por fornecedor (×2 = 10), o que confunde o operador (feedback do cliente
# 2026-06). Mostramos as N mais relevantes (o ranking já põe "no seu catálogo"
# e o melhor match no topo); para outra marca, o operador usa a "Buscar no
# meu catálogo" por item. Ajuste aqui se quiser mostrar mais/menos.
MAX_OPCOES_EXIBIDAS = 4

# === Scraping ===
SCRAPER_TIMEOUT_SEGUNDOS = 30
# Teto da ESPERA ATIVA pelos cards de produto na página de busca. Os cards são
# server-rendered (presentes ~0,05s após o domcontentloaded — benchmark de campo
# 2026-06-25), então um HIT retorna instantâneo; este teto só é pago num MISS
# (nenhum card = sem resultado). Substitui o antigo sleep FIXO de 1,5s +
# networkidle, que desperdiçava ~2,9s POR REQUISIÇÃO (52% do tempo era sleep).
#
# Valor: 1000ms. Como o card aparece em ~0,05s, isto é uma margem de ~20× — só
# acelera os MISSES (que cascateiam várias queries). SE uma re-rodada limpa
# mostrar COBERTURA menor que a esperada (o A/B com 1500ms achou 30/31), o
# primeiro suspeito é este teto: volte para 1500. Abaixar mais exige validar
# (rodar scripts/ab_busca.py comparando coberturas) — não baixe no escuro.
SCRAPER_ESPERA_CARDS_MS = 1000
# Máximo de candidatos retornados por (item, fornecedor). Calibrado em
# 2026-05-27 após operador reportar que 47-60 candidatos por item poluía
# a UI. Top-N por relevância (depois por preço) é suficiente — o restante
# é ruído de "produto da mesma categoria mas dimensão muito diferente".
SCRAPER_MAX_CANDIDATOS_POR_FORNECEDOR = 5
SCRAPER_RETRY = 2
# Itens buscados EM PARALELO por fornecedor (threads de consultas HTTP —
# 2026-07). O rate limiter global por fornecedor continua valendo (2 req/s),
# então o pool encurta a espera de rede, não a cadência no site. 4 é
# equilíbrio; 1 desliga o paralelismo por item.
SCRAPER_POOL_SIZE = 4
# Throttling: máximo de buscas iniciadas por segundo POR FORNECEDOR.
# Respeita os parceiros — não confunda com pool_size (paralelismo).
# Pool 4 + rate 2 req/s = sustentação de ~2 req/s com burst até 4.
SCRAPER_MAX_REQ_POR_SEGUNDO = 2.0
# Consultas de busca via HTTP com os cookies da sessão logada (Fase 2 de perf,
# 2026-07): os cards são server-rendered — um GET devolve o MESMO conteúdo que o
# navegador (preço logado validado em campo nos 2 fornecedores), em ~1/3 do
# tempo e com miss instantâneo. O navegador continua sendo usado para login,
# condição de pagamento, NCM e como FALLBACK de qualquer consulta HTTP que
# falhe. Desligue (False) para voltar 100% ao navegador (rollback de campo).
SCRAPER_HTTP_QUERIES = True
FORNECEDORES = {
    "almeida": "https://www.almeidadistribuidor.com.br",
    "dantas": "https://www.dantasdistribuidora.com.br",
}

# === Layout do template MATRIZ.xlsx ===
# MODELO DO CONTADOR (2026-07): a planilha-base foi refeita pelo contador do
# cliente e é a especificação vigente. Mudou em relação ao modelo antigo:
#  - cabeçalho na LINHA 2 (era 4); dados começam na LINHA 3 (era 5);
#  - o PREÇO VENDA (G) deixou de ser fórmula (=S*1,45) e é MANUAL — o operador
#    escolhe entre 3 cenários de margem (colunas Z/AA/AB = 15/12/7%);
#  - a Substituição Tributária é NATIVA: a coluna AC ('SUBST. TRIBUTÁRIA')
#    recebe o texto 'SIM' e a própria planilha zera o imposto estadual e ajusta
#    o gross-up. O app grava 'SIM' em AC por NCM (não troca mais multiplicador).
# O exportador foi migrado para este layout (ver pregoes/exportador.py).
LINHA_CABECALHO = 2
# Primeira linha de dados
LINHA_DADOS_INICIO = 3
# Mapeamento das colunas conforme template do cliente
COLUNAS = {
    "item":              "B",
    "descricao":         "C",
    "unidade":           "D",
    "quantidade":        "E",
    "valor_referencia":  "F",
    "preco_venda":       "G",   # MANUAL no modelo novo (operador digita; ver Z/AA/AB)
    "margem":            "H",   # FÓRMULA
    "faturamento":       "I",   # FÓRMULA
    "lucro_unitario":    "J",   # FÓRMULA
    "lucro_item":        "K",   # FÓRMULA
    "preco_base":        "L",   # ← do scraper (custo)
    "perc_st":           "M",   # manual no MVP
    "valor_st":          "N",   # FÓRMULA
    "perc_ipi":          "O",   # manual no MVP
    "valor_ipi":         "P",   # FÓRMULA
    "perc_frete":        "Q",   # manual no MVP
    "outros":            "R",   # FÓRMULA
    "custo_aquisicao":   "S",   # FÓRMULA
    "codigo_fornecedor": "T",   # ← do scraper
    "marca":             "U",   # ← do scraper
    "subst_tributaria":  "AC",  # ← do app: 'SIM' p/ item com ST (dispara a planilha)
}

# Colunas que o RPA preenche (resto é fórmula do Excel)
COLUNAS_PREENCHIDAS_RPA = ["item", "descricao", "unidade", "quantidade",
                            "valor_referencia", "preco_base",
                            "codigo_fornecedor", "marca", "subst_tributaria"]

# === UI Streamlit ===
PORTA_UI = 8501
TITULO_APP = "Pregões — Automação de Formação de Preços"


def carregar_opcoes_pagamento() -> dict:
    """Opções de Forma/Prazo de pagamento por fornecedor — bundladas, capturadas
    do site por ``scripts/inspecionar_pagamento.py``. Formato:
    ``{fornecedor: {"forma": [{value, texto}], "prazo": [{value, texto}]}}``.
    Devolve {} se o arquivo faltar (a UI degrada para o que houver)."""
    import json
    # Mesmo padrão do ncm_st.json (bundla no .exe ao lado do pacote pregoes).
    arq = Path(__file__).resolve().parent / "dados" / "opcoes_pagamento.json"
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
