"""Regras das telas de administração (Planilhas geradas, Meu catálogo, Variação
de preços, Configurações) — funções sem tela, trazidas do Streamlit
(`pregoes/ui/app.py`) com a linha de origem no docstring, para conferir a
paridade nos testes.

Nada aqui conhece HTTP nem HTML: as rotas (`rotas_historico.py`,
`rotas_catalogo.py`, `rotas_config.py`) chamam estas funções e montam a tela.

Decisões de desenho:
- O filtro do histórico continua sendo "pedaço de texto dentro do RÓTULO do
  pregão" (o rótulo inclui status e data), como no Streamlit. Só o cálculo
  mudou de lugar: do navegador para o servidor, porque agora há paginação.
- A variação de preços é AGREGADA NO BANCO (um `GROUP BY` por produto e
  fornecedor). A primeira e a última cotação de cada grupo são o menor e o
  maior `id` da tabela: `precos_historico` só recebe inserções (nunca
  `UPDATE`), então a ordem dos ids é a ordem do tempo — e a busca por chave
  primária dispensa ordenar dezenas de milhares de linhas.
- Os textos buscados são comparados sem acento e sem diferença de maiúsculas
  ("conexao" acha "CONEXÃO"); o `LIKE` do SQLite só ignora caixa em ASCII.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlencode

from pregoes import config, db

PREGOES_POR_PAGINA = 30
PRODUTOS_POR_PAGINA = 50
VARIACOES_POR_PAGINA = 50
FORNECEDORES = ("almeida", "dantas")
NOME_FORNECEDOR = {"almeida": "Almeida", "dantas": "Dantas"}
PERIODOS = {"30": 30, "90": 90, "365": 365, "tudo": None}


# --------------------------------------------------------------------------
# Texto e paginação
# --------------------------------------------------------------------------

def sem_acento(texto: object) -> str:
    """Minúsculo e sem acento — a forma em que os filtros comparam."""
    s = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def contem_todas(alvo: str, consulta: str) -> bool:
    """True se `alvo` (já em `sem_acento`) contém TODAS as palavras da consulta."""
    return all(p in alvo for p in sem_acento(consulta).split())


def paginar(itens: list, pagina: int, por_pagina: int) -> tuple[list, int, int]:
    """Devolve `(fatia, pagina_valida, total_de_paginas)`. Página fora do
    intervalo cai na mais próxima (link antigo depois de excluir algo)."""
    total = max(1, -(-len(itens) // por_pagina))
    pagina = max(1, min(int(pagina or 1), total))
    inicio = (pagina - 1) * por_pagina
    return itens[inicio:inicio + por_pagina], pagina, total


def endereco(caminho: str, **filtros: object) -> str:
    """Endereço com os filtros em uso na query string (os vazios ficam de
    fora): `endereco('/precos', periodo='30', q='')` → '/precos?periodo=30'."""
    usados = {k: v for k, v in filtros.items() if v not in (None, "")}
    return caminho + (f"?{urlencode(usados)}" if usados else "")


def prefixo_pagina(caminho: str, **filtros: object) -> str:
    """Endereço da lista com os filtros em uso, terminando em 'p=' — os links
    de página só acrescentam o número ('/historico?q=araruna&p=')."""
    base = endereco(caminho, **filtros)
    return f"{base}{'&' if '?' in base else '?'}p="


def inteiro(valor: object, padrao: int = 1) -> int:
    """Número vindo da URL; qualquer coisa estranha vira o padrão."""
    try:
        return int(str(valor))
    except (TypeError, ValueError):
        return padrao


# --------------------------------------------------------------------------
# Histórico de pregões (app.py L1029-1090, L1127-1243)
# --------------------------------------------------------------------------

def data_amigavel(criado_em: Optional[str]) -> str:
    """Timestamp UTC do SQLite ('AAAA-MM-DD HH:MM:SS') → 'DD/MM HH:MM' no fuso
    do operador. Nulo → 'data desconhecida'; formato inválido → texto cru.
    Porta de `_data_amigavel` (app.py L1029)."""
    if not criado_em:
        return "data desconhecida"
    try:
        utc = datetime.strptime(criado_em[:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc)
        return utc.astimezone(config.FUSO_HORARIO).strftime("%d/%m %H:%M")
    except (ValueError, TypeError):
        return str(criado_em)


def rotulo_pregao(orgao: Optional[str], numero: Optional[str], status: Optional[str],
                  criado_em: Optional[str]) -> str:
    """'Pregão {numero} — {orgao} — {status} em {data}' (partes ausentes
    omitidas; status nulo → 'rascunho'); sem órgão e número → 'Pregão sem
    identificação (de {data})'. Porta de `_rotulo_pregao` (app.py L1046)."""
    orgao = (orgao or "").strip()
    numero = (numero or "").strip()
    quando = data_amigavel(criado_em)
    if not orgao and not numero:
        return f"Pregão sem identificação (de {quando})"
    partes = []
    if numero:
        partes.append(f"Pregão {numero}")
    if orgao:
        partes.append(orgao)
    return f"{' — '.join(partes)} — {(status or 'rascunho').strip()} em {quando}"


def titulo_pregao(orgao: Optional[str], numero: Optional[str]) -> str:
    """Só a identificação (sem status/data) — o que vai em negrito na linha."""
    orgao, numero = (orgao or "").strip(), (numero or "").strip()
    partes = ([f"Pregão {numero}"] if numero else []) + ([orgao] if orgao else [])
    return " — ".join(partes) or "Pregão sem identificação"


def listar_editais(conn: sqlite3.Connection) -> list[dict]:
    """Pregões processados, mais recente primeiro — a MESMA consulta do
    Streamlit (app.py L1144): `editais LEFT JOIN exports_log GROUP BY e.id`."""
    linhas = conn.execute(
        """
        SELECT e.id, e.orgao, e.numero_pregao, e.status, e.criado_em,
               COUNT(x.id) AS n_planilhas
        FROM editais e
        LEFT JOIN exports_log x ON x.edital_id = e.id
        GROUP BY e.id
        ORDER BY e.criado_em DESC, e.id DESC
        """).fetchall()
    saida = []
    for r in linhas:
        d = dict(r)
        d["status"] = (d["status"] or "rascunho").strip()
        d["rotulo"] = rotulo_pregao(d["orgao"], d["numero_pregao"], d["status"], d["criado_em"])
        d["titulo"] = titulo_pregao(d["orgao"], d["numero_pregao"])
        saida.append(d)
    return saida


def filtrar_editais(editais: list[dict], filtro: str) -> list[dict]:
    """Pedaço de texto, sem diferença de maiúsculas, sobre o RÓTULO inteiro
    (inclui status e data) — app.py L1167."""
    termo = (filtro or "").strip().lower()
    if not termo:
        return editais
    return [e for e in editais if termo in e["rotulo"].lower()]


def planilhas_dos_editais(conn: sqlite3.Connection, ids: Iterable[int]) -> dict[int, list[dict]]:
    """TODAS as planilhas geradas de cada pregão, a mais recente primeiro (o
    Streamlit só oferecia a última). `existe` é conferido AGORA no disco —
    nada sobre a existência do arquivo é guardado (app.py L1223-1235)."""
    ids = list(ids)
    saida: dict[int, list[dict]] = {i: [] for i in ids}
    if not ids:
        return saida
    marcas = ",".join("?" * len(ids))
    for r in conn.execute(
            f"""
            SELECT id, edital_id, arquivo, n_itens, n_selecoes, criado_em
            FROM exports_log
            WHERE edital_id IN ({marcas})
            ORDER BY criado_em DESC, id DESC
            """, ids):
        d = dict(r)
        d["existe"] = caminho_da_planilha(d["arquivo"]) is not None
        saida[d["edital_id"]].append(d)
    return saida


def caminho_da_planilha(arquivo: Optional[str]) -> Optional[Path]:
    """Caminho do `.xlsx` gerado, ou None se não existe OU se o nome gravado
    aponta para fora de `config.EXPORTS_DIR`. Só o nome do arquivo é guardado
    no banco; o caminho é remontado a cada uso (app.py L1223). A conferência
    de pasta é a trava contra um nome como '../credenciais.json'."""
    if not arquivo:
        return None
    pasta = Path(config.EXPORTS_DIR).resolve()
    try:
        caminho = (pasta / arquivo).resolve()
    except (OSError, ValueError):
        return None
    if caminho.parent != pasta or not caminho.is_file():
        return None
    return caminho


def excluir_pregao(edital_id: int) -> bool:
    """Apaga os `.xlsx` de TODAS as planilhas do pregão e o registro do banco
    (ON DELETE CASCADE leva os itens e o histórico de planilhas). Porta de
    `_excluir_pregao` (app.py L1069). Devolve False se o pregão não existia."""
    with db.get_conn() as conn:
        arquivos = [r["arquivo"] for r in conn.execute(
            "SELECT arquivo FROM exports_log WHERE edital_id = ?", (edital_id,))]
        apagados = conn.execute("DELETE FROM editais WHERE id = ?", (edital_id,)).rowcount
    for arq in arquivos:
        caminho = caminho_da_planilha(arq)      # nunca apaga fora da pasta de planilhas
        if caminho is not None:
            try:
                caminho.unlink(missing_ok=True)
            except OSError:
                pass
    return bool(apagados)


def atualizar_pregao(edital_id: int, orgao: str, numero: str) -> bool:
    """Troca órgão/número do pregão (aparado; vazio vira NULL, sem validação).
    Porta de `_atualizar_pregao` (app.py L1083)."""
    with db.get_conn() as conn:
        return bool(conn.execute(
            "UPDATE editais SET orgao = ?, numero_pregao = ? WHERE id = ?",
            ((orgao or "").strip() or None, (numero or "").strip() or None,
             edital_id)).rowcount)


def chave_identidade(orgao: Optional[str], numero: Optional[str]) -> tuple[str, str]:
    """Órgão+número aparados e sem diferença de maiúsculas — a mesma
    identidade que o registro do pregão usa ao gerar (app.py L940)."""
    return (orgao or "").strip().lower(), (numero or "").strip().lower()


def rascunhos_por_identidade(rascunhos: list[dict]) -> dict[tuple[str, str], str]:
    """`{(orgao, numero): slug}` dos pregões ainda guardados (o mais recente
    vence; `rascunho.listar_pregoes()` já vem do mais novo para o mais velho).
    Pregão sem identificação não casa com nada."""
    mapa: dict[tuple[str, str], str] = {}
    for r in rascunhos:
        chave = chave_identidade(r.get("orgao"), r.get("numero"))
        if any(chave) and chave not in mapa:
            mapa[chave] = r["slug"]
    return mapa


# --------------------------------------------------------------------------
# Catálogo (app.py L160, L3727-3886)
# --------------------------------------------------------------------------

def sanitizar_nome_arquivo(nome: str) -> str:
    """Nome de arquivo seguro e DETERMINÍSTICO para a planilha enviada: só o
    último componente (corta `/` e `\\`), troca `[^A-Za-z0-9._-]` por `_`,
    remove pontos iniciais (vazio → 'planilha') e garante `.xlsx`. O importador
    usa o nome como chave para não duplicar — a mesma planilha tem de virar
    sempre o mesmo nome. Porta de `_sanitizar_nome_arquivo` (app.py L160)."""
    base = (nome or "").replace("\\", "/").split("/")[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    base = base.lstrip(".") or "planilha"
    if not base.lower().endswith(".xlsx"):
        base = f"{base}.xlsx"
    return base


def metricas_catalogo(conn: sqlite3.Connection) -> dict:
    """As 4 métricas da tela (app.py L3747-3764), numa consulta só."""
    linha = conn.execute(
        """
        SELECT COUNT(*),
               SUM(CASE WHEN codigo_almeida IS NOT NULL AND codigo_almeida != '' THEN 1 ELSE 0 END),
               SUM(CASE WHEN codigo_dantas IS NOT NULL AND codigo_dantas != '' THEN 1 ELSE 0 END)
        FROM produtos""").fetchone()
    padronizados = conn.execute("SELECT COUNT(*) FROM produtos_canonico").fetchone()[0]
    return {"total": linha[0] or 0, "com_almeida": linha[1] or 0,
            "com_dantas": linha[2] or 0, "padronizados": padronizados}


def buscar_produtos(conn: sqlite3.Connection, consulta: str = "") -> list[dict]:
    """Produtos do catálogo em ordem alfabética; com `consulta`, só os que têm
    TODAS as palavras na descrição, marca, códigos ou NCM. O catálogo cabe em
    memória (alvo: ~5.000 produtos), então o filtro é feito aqui — sem acento
    e sem diferença de maiúsculas."""
    linhas = [dict(r) for r in conn.execute(
        """
        SELECT id, descricao_canonica, marca, unidade_padrao, codigo_almeida,
               codigo_dantas, ncm
        FROM produtos
        ORDER BY descricao_canonica COLLATE NOCASE, id
        """)]
    if not (consulta or "").strip():
        return linhas
    return [p for p in linhas if contem_todas(sem_acento(" ".join(
        str(p[c] or "") for c in ("descricao_canonica", "marca", "codigo_almeida",
                                  "codigo_dantas", "ncm"))), consulta)]


def anexar_ultimas_cotacoes(conn: sqlite3.Connection, produtos: list[dict]) -> None:
    """Põe em cada produto `cotacoes = {fornecedor: {preco, consultado_em}}`
    com o último preço conhecido — `db.ultima_cotacao`, a mesma referência
    mostrada na busca manual por item (app.py L2873). Chamada só para a página
    visível (no máximo `PRODUTOS_POR_PAGINA` produtos)."""
    for p in produtos:
        p["cotacoes"] = {}
        for forn in FORNECEDORES:
            linha = db.ultima_cotacao(conn, p["id"], forn)
            if linha is not None:
                p["cotacoes"][forn] = {"preco": linha["preco"],
                                       "consultado_em": linha["consultado_em"]}


# --------------------------------------------------------------------------
# Variação de preços (painel novo — roadmap da Fase 3, item 8)
# --------------------------------------------------------------------------

def variacao_pct(primeiro: Optional[float], ultimo: Optional[float]) -> Optional[float]:
    """Quanto o último preço subiu (+) ou caiu (−) sobre o primeiro, em %.
    Sem base (primeiro nulo ou zero) → None."""
    if primeiro is None or ultimo is None or primeiro <= 0:
        return None
    return (ultimo - primeiro) * 100.0 / primeiro


def _inicio_do_periodo(periodo: str, agora: Optional[datetime] = None) -> Optional[str]:
    dias = PERIODOS.get(periodo)
    if dias is None:
        return None
    agora = agora or datetime.now(timezone.utc)
    return (agora - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")


def variacoes_de_preco(conn: sqlite3.Connection, fornecedor: str = "", periodo: str = "tudo",
                       consulta: str = "", ordem: str = "alta",
                       agora: Optional[datetime] = None) -> list[dict]:
    """Uma linha por (produto, fornecedor) com pelo menos 2 cotações no
    período: primeiro e último preço, variação %, nº de cotações e datas.
    `ordem`: 'alta' (maior aumento primeiro, o padrão) ou 'queda'."""
    filtros, params = [], []
    if fornecedor in FORNECEDORES:
        filtros.append("fornecedor = ?")
        params.append(fornecedor)
    desde = _inicio_do_periodo(periodo, agora)
    if desde:
        filtros.append("consultado_em >= ?")
        params.append(desde)
    onde = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    linhas = [dict(r) for r in conn.execute(
        f"""
        SELECT g.produto_id, g.fornecedor, g.n,
               a.preco AS primeiro, a.consultado_em AS primeira_data,
               b.preco AS ultimo, b.consultado_em AS ultima_data,
               p.descricao_canonica, p.marca, p.unidade_padrao
        FROM (SELECT produto_id, fornecedor, COUNT(*) AS n,
                     MIN(id) AS id_primeiro, MAX(id) AS id_ultimo
              FROM precos_historico {onde}
              GROUP BY produto_id, fornecedor
              HAVING COUNT(*) >= 2) g
        JOIN precos_historico a ON a.id = g.id_primeiro
        JOIN precos_historico b ON b.id = g.id_ultimo
        JOIN produtos p ON p.id = g.produto_id
        """, params)]
    if (consulta or "").strip():
        linhas = [v for v in linhas if contem_todas(
            sem_acento(f"{v['descricao_canonica']} {v['marca'] or ''}"), consulta)]
    for v in linhas:
        v["variacao"] = variacao_pct(v["primeiro"], v["ultimo"])
    sinal = 1 if ordem == "queda" else -1
    # Sem base de cálculo vai para o fim; empate desfaz pela descrição.
    linhas.sort(key=lambda v: (v["variacao"] is None,
                               sinal * (v["variacao"] or 0.0),
                               sem_acento(v["descricao_canonica"])))
    return linhas


def resumo_variacoes(variacoes: list[dict]) -> dict:
    """Contagens do topo do painel (preço igual até o centavo = estável)."""
    diferencas = [round(v["ultimo"] - v["primeiro"], 2) for v in variacoes]
    subiram = sum(1 for d in diferencas if d > 0)
    cairam = sum(1 for d in diferencas if d < 0)
    return {"total": len(variacoes), "subiram": subiram, "cairam": cairam,
            "estaveis": len(variacoes) - subiram - cairam}


def anexar_series(conn: sqlite3.Connection, variacoes: list[dict], periodo: str = "tudo",
                  agora: Optional[datetime] = None, maximo: int = 24) -> None:
    """Põe em cada linha da PÁGINA VISÍVEL `pontos` = desenho da evolução do
    preço (as últimas `maximo` cotações) para o minigráfico."""
    if not variacoes:
        return
    ids = sorted({v["produto_id"] for v in variacoes})
    params: list = list(ids)
    desde = _inicio_do_periodo(periodo, agora)
    filtro_data = ""
    if desde:
        filtro_data = "AND consultado_em >= ?"
        params.append(desde)
    series: dict[tuple[int, str], list[float]] = {}
    for r in conn.execute(
            f"""
            SELECT produto_id, fornecedor, preco FROM precos_historico
            WHERE produto_id IN ({",".join("?" * len(ids))}) {filtro_data}
            ORDER BY id
            """, params):
        series.setdefault((r["produto_id"], r["fornecedor"]), []).append(r["preco"])
    for v in variacoes:
        v["pontos"] = pontos_do_minigrafico(
            series.get((v["produto_id"], v["fornecedor"]), [])[-maximo:])


def pontos_do_minigrafico(precos: list[float], largura: int = 96, altura: int = 26,
                          margem: int = 3) -> str:
    """Coordenadas `x,y x,y …` de uma linha SVG que cabe em largura×altura.
    Preço constante vira uma linha reta no meio."""
    if len(precos) < 2:
        return ""
    menor, maior = min(precos), max(precos)
    faixa = (maior - menor) or 1.0
    passo = (largura - 2 * margem) / (len(precos) - 1)
    pontos = []
    for i, preco in enumerate(precos):
        y = altura / 2 if maior == menor else (
            altura - margem - (preco - menor) / faixa * (altura - 2 * margem))
        pontos.append(f"{margem + i * passo:.1f},{y:.1f}")
    return " ".join(pontos)


# --------------------------------------------------------------------------
# Configurações (app.py L1247-1429, L2095-2155) e aba Sistema
# --------------------------------------------------------------------------

SEM_PRAZO = {"value": "", "texto": "— Não se aplica (PIX, à vista, antecipado) —"}


def mascarar_chave(chave: Optional[str]) -> str:
    """'AIzaSyAB…wxyz' — os 8 primeiros e os 4 últimos caracteres, como no
    Streamlit (app.py L1333). Chave curta demais para isso mostra só o começo
    (a máscara nunca devolve a chave inteira)."""
    chave = chave or ""
    if len(chave) <= 16:
        return f"{chave[:2]}…" if chave else ""
    return f"{chave[:8]}…{chave[-4:]}"


def opcao_por_valor(opcoes: list[dict], valor: Optional[str]) -> Optional[dict]:
    return next((o for o in opcoes if o.get("value") == valor), None)


def texto_condicao(forma_texto: Optional[str], prazo_texto: Optional[str]) -> str:
    """'{forma}[ · {prazo}]' — app.py L2139 e L2148."""
    return (forma_texto or "") + (f" · {prazo_texto}" if prazo_texto else "")


def ocultar_segredos(texto: str, segredos: Iterable[Optional[str]]) -> str:
    """Troca por '•••' qualquer senha/chave salva que apareça no texto (o
    registro de atividades é mostrado na tela; segredo nenhum vai junto)."""
    for s in segredos:
        if s and len(s) >= 4:
            texto = texto.replace(s, "•••")
    return texto


_RE_NIVEL = re.compile(r"\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]")


def ultimas_linhas_do_registro(caminho: Path, quantas: int = 50,
                               segredos: Iterable[Optional[str]] = ()) -> list[dict]:
    """As últimas `quantas` linhas de `data/pregoes.log`, cada uma com o
    `nivel` ('erro', 'alerta' ou '') para destacar. Linha de continuação (o
    detalhe técnico de um erro ocupa várias) herda o nível da anterior. Lê só
    o fim do arquivo — ele pode ter vários MB."""
    try:
        with open(caminho, "rb") as f:
            f.seek(0, 2)
            tamanho = f.tell()
            f.seek(max(0, tamanho - 256 * 1024))
            bruto = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    segredos = list(segredos)
    nivel, saida = "", []
    for linha in bruto.splitlines():
        achado = _RE_NIVEL.search(linha[:80])
        if achado:
            nivel = {"WARNING": "alerta", "ERROR": "erro", "CRITICAL": "erro"}.get(
                achado.group(1), "")
        saida.append({"texto": ocultar_segredos(linha, segredos), "nivel": nivel})
    return saida[-quantas:]


def tamanho_legivel(n_bytes: Optional[int]) -> str:
    """1536 → '1,5 KB' (padrão brasileiro)."""
    if n_bytes is None:
        return "—"
    valor = float(n_bytes)
    for unidade in ("bytes", "KB", "MB", "GB"):
        if valor < 1024 or unidade == "GB":
            if unidade == "bytes":
                return f"{int(valor)} bytes"
            return f"{valor:.1f}".replace(".", ",") + f" {unidade}"
        valor /= 1024
    return "—"
