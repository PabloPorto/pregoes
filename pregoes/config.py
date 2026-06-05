"""
Configurações centrais do sistema de automação de pregões.

Todo parâmetro que pode mudar fica aqui. Nada de constantes espalhadas
pelo código. Se o cliente quiser alterar markup, overhead, cache, etc.,
mexe só neste arquivo.
"""
from pathlib import Path
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

# Semente do template: empacotado, o template vem do bundle (read-only) e é
# copiado UMA vez para a pasta gravável, para a exportação e o restauro do
# ORIGINAL funcionarem. Em dev, origem == destino → no-op (não copia nada).
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
PERC_OPERACIONAL = 0.03             # 3% operacional
PERC_COMISSAO = 0.05                # 5% comissão
PERC_IMPOSTOS_FEDERAIS = 0.07       # 7% impostos federais
VALOR_MINIMO_FATOR = 0.5            # Mínimo aceitável = 50% do valor de referência

# === Cache de preços ===
CACHE_PRECO_DIAS = 2                # TTL conforme decisão (resp 9)

# === Scraping ===
SCRAPER_TIMEOUT_SEGUNDOS = 30
# Máximo de candidatos retornados por (item, fornecedor). Calibrado em
# 2026-05-27 após operador reportar que 47-60 candidatos por item poluía
# a UI. Top-N por relevância (depois por preço) é suficiente — o restante
# é ruído de "produto da mesma categoria mas dimensão muito diferente".
SCRAPER_MAX_CANDIDATOS_POR_FORNECEDOR = 5
SCRAPER_RETRY = 2
# Pool de páginas Playwright por fornecedor — paraleliza as buscas
# em um lote. 4 é equilíbrio: ganho linear até ~5 antes do site começar
# a competir por sessões. Acima de 5 não compensa.
SCRAPER_POOL_SIZE = 4
# Throttling: máximo de buscas iniciadas por segundo POR FORNECEDOR.
# Respeita os parceiros — não confunda com pool_size (paralelismo).
# Pool 4 + rate 2 req/s = sustentação de ~2 req/s com burst até 4.
SCRAPER_MAX_REQ_POR_SEGUNDO = 2.0
FORNECEDORES = {
    "almeida": "https://www.almeidadistribuidor.com.br",
    "dantas": "https://www.dantasdistribuidora.com.br",
}

# === Layout do template MATRIZ.xlsx ===
# Linha onde os cabeçalhos reais estão (R4 conforme análise do template)
LINHA_CABECALHO = 4
# Primeira linha de dados
LINHA_DADOS_INICIO = 5
# Mapeamento das colunas conforme template do cliente
COLUNAS = {
    "item":              "B",
    "descricao":         "C",
    "unidade":           "D",
    "quantidade":        "E",
    "valor_referencia":  "F",
    "preco_venda":       "G",   # FÓRMULA — não tocar
    "margem":            "H",   # FÓRMULA
    "faturamento":       "I",   # FÓRMULA
    "lucro_unitario":    "J",   # FÓRMULA
    "lucro_item":        "K",   # FÓRMULA
    "preco_base":        "L",   # ← do scraper
    "perc_st":           "M",   # manual no MVP
    "valor_st":          "N",   # FÓRMULA
    "perc_ipi":          "O",   # manual no MVP
    "valor_ipi":         "P",   # FÓRMULA
    "perc_frete":        "Q",   # manual no MVP
    "outros":            "R",   # FÓRMULA
    "custo_aquisicao":   "S",   # FÓRMULA
    "codigo_fornecedor": "T",   # ← do scraper
    "marca":             "U",   # ← do scraper
}

# Colunas que o RPA preenche (resto é fórmula do Excel)
COLUNAS_PREENCHIDAS_RPA = ["item", "descricao", "unidade", "quantidade",
                            "valor_referencia", "preco_base",
                            "codigo_fornecedor", "marca"]

# === UI Streamlit ===
PORTA_UI = 8501
TITULO_APP = "Pregões — Automação de Formação de Preços"
