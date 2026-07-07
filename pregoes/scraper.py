"""
Scraper de fornecedores: Almeida + Dantas.

Decisões registradas (CLAUDE.md Seção 7, QUESTOES_PENDENTES.md Bloco 2):
- Sites parceiros, sem CAPTCHA visível, scraping autorizado.
- Login obrigatório no Almeida para ver preço (campo CNPJ só no 1º acesso).
- Variação de nome → confianca='variacao' (UI marca amarelo, não bloqueia).
- Sistema NUNCA decide marca: a UI lista os resultados e o operador escolhe.

Pipeline de busca em 5 camadas (Fase 3 / Q12), aplicado no orquestrador
`buscar_precos_para_itens`:

    0. Cache de descrição (TTL 2d)
    1. Camada 4 — aprendizado: descrição já vista → código direto
    2. Camada 0 — matcher fuzzy contra catálogo histórico → busca por código
    3. Camadas 1+2 — busca textual em N camadas (`buscar(descricao)`):
         a. descrição normalizada
         b. top-3 keywords
         c. top-2 keywords
         d. fallback "top 3 variação" se nada passar o threshold
    4. Camada 3 — Gemini judge sobre o fallback "top 3 variação"

A `buscar()` interna ainda faz as buscas textuais; já a busca por código
(Camada 0/4) usa `buscar_por_codigo(codigo)` que faz `/busca?s={codigo}`
e devolve produto exato sem disputa de relevância.

**Modelo de execução SERIAL.** A tentativa anterior de paralelismo via
ThreadPoolExecutor (commit 0d0d005) quebrou em produção com
`greenlet.error: Cannot switch to a different thread`: Playwright sync
API só pode ser usado na thread que iniciou `sync_playwright()`. Voltamos
ao serial — mais simples, sem regressão. Quando o cache estiver populado
(TTL 2d), o segundo edital do dia roda quase instantâneo. Se for dor real
no volume alvo, refatoramos para asyncio com `playwright.async_api`.

Uso típico:
    cred = credenciais.carregar("almeida")
    with AlmeidaDistribuidor(cred["email"], cred["senha"]) as alm:
        resultados = alm.buscar("ABRAÇADEIRA DE NYLON 100 X 2.5 PCT")
        # ou, se vier do matcher com código conhecido:
        resultados = alm.buscar_por_codigo("11656")
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from typing import Callable, Optional, Protocol
from urllib.parse import quote_plus

from . import config, credenciais, db, tributacao
from .diagnostico import (
    LogCandidatoFinal,
    LogDiagnostico,
    LogItemFornecedor,
    LogQueryInterna,
)
from .extrator import ItemExtraido
from .matcher import (
    ProdutoCatalogo, carregar_catalogo, indice_por_codigo,
    melhor_aprendido_fuzzy, melhor_match,
)
from .normalizador import (
    _ADJETIVOS_MARKETING,
    diametro_para_query,
    keywords,
    keywords_distintivas,
    normalizar_descricao,
    relevancia,
)


@dataclass(frozen=True)
class ResultadoBusca:
    """
    Um resultado vindo do scraper de um fornecedor.

    Um único item de edital pode retornar múltiplos `ResultadoBusca` (várias
    marcas). A escolha entre marcas é sempre do operador humano — o sistema
    NUNCA decide (Resp final 7).
    """
    descricao_encontrada: str
    marca: str
    preco: float
    codigo_fornecedor: str
    confianca: str   # 'exato' | 'variacao'
    fornecedor: str  # 'almeida' | 'dantas'
    ncm: Optional[str] = None  # Fase 4: NCM (preenchido sob demanda da pág. de detalhe)
    url: Optional[str] = None  # Fase 4: link da página de detalhe (onde mora o NCM)
    # Reconhecimento por código (feedback do cliente): preenchidos quando o
    # `codigo_fornecedor` deste candidato bate com um produto já no catálogo do
    # cliente. `no_catalogo=True` aciona o badge "já no seu catálogo", a
    # priorização no ranking e a pré-seleção na UI.
    no_catalogo: bool = False
    produto_catalogo_id: Optional[int] = None
    descricao_catalogo: Optional[str] = None  # descrição interna, para conferência
    # Retroalimentação (Camada 4): True quando este resultado veio do
    # APRENDIZADO — a descrição já foi resolvida pelo operador antes, e o
    # produto foi recuperado pelo código ensinado. Aciona, na UI, a
    # pré-seleção e o badge "✓ sua escolha anterior". É a inteligência que o
    # cliente mais valoriza (feedback 2026-06): "sempre que esse item aparecer
    # de novo, a escolha anterior deve ser fornecida".
    veio_por_aprendizado: bool = False


class Fornecedor(Protocol):
    """Contrato dos scrapers de fornecedor.

    `buscar_por_codigo` é opcional (Camada 0 da Fase 3) — quem chama deve
    detectar a presença via `hasattr` antes de invocar. Mantemos opcional
    para não forçar mocks de teste antigos a implementar a Camada 0.
    """

    nome: str

    def buscar(self, descricao: str) -> list[ResultadoBusca]:
        ...


# === Almeida ===

_logger = logging.getLogger(__name__)

_BASE_URL_ALMEIDA = "https://www.almeidadistribuidor.com.br"
_MAX_RESULTADOS_POR_QUERY = 30

# Threshold mínimo de relevância para aceitar um resultado.
# Recalibrado em 2026-05-27 após diagnóstico em 30 candidatos reais do
# TRE-PE: a função `relevancia()` foi reescrita (variante F — base por
# `min(orig, cand)` + veto/boost de categoria). Naquela base, 0.35
# atingiu 8/9 verdadeiros e 0/21 falsos.
#
# Histórico do antigo 0.6: era calibrado para `casadas / len(original)`,
# que penaliza editais detalhados (descrições com 30+ tokens contratuais
# diluem o score do match real). A função nova já normaliza esse efeito.
# Não voltar para 0.6 sem revisar a função.
_RELEVANCIA_MINIMA = 0.35

# Salvaguarda da busca por código (Q12 / 2026-06): o produto que o site
# devolve para um código precisa ter afinidade textual mínima com o que o
# catálogo dizia ser aquele código. Protege contra fornecedor/código errado
# (ex.: código sem sufixo assumido como Almeida que na verdade era Dantas —
# o mesmo número aponta produtos diferentes em cada site). Threshold BAIXO
# de propósito: o produto pode ter nome bem diferente do edital (é o motivo
# de usar código), mas um código grosseiramente errado traz algo sem
# nenhuma relação (rel ~0). Comparamos contra a descrição canônica do
# catálogo, não a do edital — ambas descrevem o mesmo código/fornecedor.
_RELEVANCIA_SALVAGUARDA_CODIGO = 0.30

# Versão do algoritmo de busca. Bumpar quando MUDA semântica do filtro
# (relevância, threshold, top-N por fornecedor). O orquestrador limpa o
# cache (`precos_cache_descricao`) quando o número aqui difere do salvo
# em `data/.busca_versao`. Evita que resultados de versões antigas do
# algoritmo "vazem" para o operador via cache TTL 2d.
BUSCA_VERSAO = "2026-06-23.13-query-marketing-sinonimo-rede-categoria"


def _montar_camadas(descricao: str) -> list[tuple[str, str]]:
    """Monta as queries de busca em ordem de prioridade.

    Mudança de 2026-06 (Tier 1): a query PRIMÁRIA passou a ser as keywords
    distintivas (curtas), não a descrição completa. Editais têm descrições
    enormes com texto contratual; mandá-las inteiras faz os sites devolverem
    produtos sem relação. Uma query curta ("filtro linha tomadas eletricas")
    devolve resultados muito melhores. A descrição completa vira último
    fallback.

    Retorna lista de `(confianca, query)` na ordem em que devem ser tentadas.
    """
    camadas: list[tuple[str, str]] = []
    vistas: set[str] = set()

    def _add(confianca: str, query: str) -> None:
        q = query.strip()
        if q and q not in vistas:
            vistas.add(q)
            camadas.append((confianca, q))

    distintivas = keywords_distintivas(descricao, 4)
    # Poda adjetivos de MARKETING da QUERY (só aqui, não na relevância):
    # "alicate profissional" -> "alicate", senão o AND do site zera (nenhum
    # produto se chama "profissional"). Guarda dura: se a poda esvaziar, mantém
    # o original (nunca query vazia). Ver `_ADJETIVOS_MARKETING`.
    if distintivas:
        podadas = [k for k in distintivas if k not in _ADJETIVOS_MARKETING]
        if podadas:
            distintivas = podadas
    diam = diametro_para_query(descricao)   # Fase 2: "75mm" se houver diâmetro nominal
    if distintivas:
        base = " ".join(distintivas)
        # 1a. Distintiva + DIÂMETRO (mais precisa) — tentada PRIMEIRO. O '75' de
        # "DIÂMETRO 75" sumia da query e o site trazia qualquer diâmetro; anexar
        # "75mm" traz o certo. Se esta query não achar nada relevante, o
        # buscar() cai na 1b (sem diâmetro) — a Fase 2 só ADICIONA precisão,
        # nunca regride (a camada de fallback garante).
        if diam and diam not in base:
            _add("exato", f"{base} {diam}")
        # 1b. Query distintiva (3-4 keywords) — comportamento atual
        _add("exato", base)
    # 2. Núcleo curto (categoria — 2 primeiras distintivas)
    if len(distintivas) >= 2:
        _add("variacao", " ".join(distintivas[:2]))
    # 3. Último recurso textual: descrição normalizada completa
    _add("variacao", normalizar_descricao(descricao))
    # 4. Rede final de CATEGORIA: UMA keyword distintiva. Só é efetivamente
    # tentada quando TODAS as anteriores zeraram no site (buscar() para na 1ª
    # camada com resultado relevante). Garante que a categoria devolva
    # candidatos para a relevância/judge ranquearem — destrava "alicate
    # profissional" e a hipótese de AND-restritivo em "alicate de pressão".
    # >=4 chars p/ não cair em sigla genérica ("pvc") que traria o catálogo todo.
    if distintivas and len(distintivas[0]) >= 4:
        _add("variacao", distintivas[0])

    return camadas


def _marcar_conhecidos(
    candidatos: list["ResultadoBusca"],
    idx_codigo: Optional[dict] = None,
) -> list["ResultadoBusca"]:
    """Marca (via `replace`, pois é frozen) os candidatos cujo `codigo_fornecedor`
    já está no catálogo do cliente. Sem `idx_codigo`, devolve a lista intacta."""
    if not idx_codigo:
        return candidatos
    marcados = []
    for c in candidatos:
        p = idx_codigo.get((c.fornecedor, (c.codigo_fornecedor or "").strip()))
        if p is not None:
            marcados.append(replace(
                c, no_catalogo=True, produto_catalogo_id=p.produto_id,
                descricao_catalogo=p.descricao_canonica))
        else:
            marcados.append(c)
    return marcados


def _top_n_por_relevancia(
    descricao_original: str,
    candidatos: list["ResultadoBusca"],
    n: int,
    idx_codigo: Optional[dict] = None,
) -> list["ResultadoBusca"]:
    """Ordena candidatos e mantém top-N.

    Ordem (Q-feedback do cliente + Resp 7): primeiro os que JÁ ESTÃO no catálogo
    do cliente por código (sinal forte — ele já comprou), depois por relevância
    (desc) e preço (asc). Os candidatos já passaram pelo filtro de threshold (ou
    foram mantidos por serem conhecidos), mas podem ser dezenas; acima do top-N
    viram ruído visual.

    Mesmo quando `len(candidatos) <= n`, ordenamos: a UI mostra o melhor match
    (ou o conhecido) primeiro independente do limite (o `n` só corta a cauda).
    """
    if not candidatos:
        return []
    candidatos = _marcar_conhecidos(candidatos, idx_codigo)
    pontuados = [
        (c.no_catalogo, relevancia(descricao_original, c.descricao_encontrada),
         c.preco, c)
        for c in candidatos
    ]
    # Conhecido-primeiro (not True == False ordena antes), depois relevância e preço.
    pontuados.sort(key=lambda x: (not x[0], -x[1], x[2]))
    # Dedup ANTES do corte: o site repete cards (visto ao vivo: L.R.METAIS×2,
    # LUCONI×2 no mesmo resultado). Sem deduplicar aqui, as repetições gastam
    # slots do top-N e o operador vê MENOS produtos distintos para escolher.
    # Mantém a 1ª ocorrência (maior relevância). Chave = mesmo produto do mesmo
    # fornecedor (código+marca+descrição+preço).
    vistos: set = set()
    unicos: list[ResultadoBusca] = []
    for _conh, _rel, _preco, c in pontuados:
        chave = (
            (c.codigo_fornecedor or "").strip(),
            (c.marca or "").strip().lower(),
            (c.descricao_encontrada or "").strip().lower(),
            c.preco,
        )
        if chave in vistos:
            continue
        vistos.add(chave)
        unicos.append(c)
    return unicos[:n]


def _parse_card_attributes(attrs: dict, confianca: str) -> Optional[ResultadoBusca]:
    """
    Converte os atributos `data-*` de um `.product-card__info` em ResultadoBusca.

    Esta função é PURA — não toca em Playwright, não chama rede. Testável
    em isolamento com dicts de fixture. Se os atributos exigidos faltarem
    ou estiverem mal-formados, retorna None (o scraper descarta a linha).

    Atributos esperados (mapeados na inspeção de 2026-05-15):
    - data-codigo-produto
    - data-nome
    - data-preco         (string decimal com ponto, ex: '3.18')
    - data-marca
    """
    codigo = (attrs.get("data-codigo-produto") or "").strip()
    nome = (attrs.get("data-nome") or "").strip()
    marca = (attrs.get("data-marca") or "").strip()
    preco_str = (attrs.get("data-preco") or "").strip()

    if not codigo or not nome or not preco_str:
        return None
    try:
        preco = float(preco_str.replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(preco) or preco <= 0:
        return None   # NaN/Infinity do data-preco furavam o '<= 0' (adversarial 2026-06)

    return ResultadoBusca(
        descricao_encontrada=nome,
        marca=marca,
        preco=preco,
        codigo_fornecedor=codigo,
        confianca=confianca,
        fornecedor="almeida",
        url=attrs.get("url"),
    )


class _RateLimiter:
    """Rate limiter com LOCK — intervalo mínimo entre requests do fornecedor.

    Com o paralelismo por item (2026-07) várias threads compartilham o mesmo
    limiter; o lock garante a cadência GLOBAL por fornecedor (dormir segurando
    o lock é proposital: enfileira os demais exatamente no ritmo permitido).
    Continua respeitando os sites parceiros: pool grande não aumenta req/s.
    """

    def __init__(self, max_req_por_segundo: float):
        import threading as _th
        self._min_intervalo = 1.0 / max_req_por_segundo if max_req_por_segundo > 0 else 0.0
        self._ultimo = 0.0
        self._lock = _th.Lock()

    def acquire(self) -> None:
        with self._lock:
            agora = time.monotonic()
            espera = self._min_intervalo - (agora - self._ultimo)
            if espera > 0:
                time.sleep(espera)
                self._ultimo = time.monotonic()
            else:
                self._ultimo = agora


# === Condição de pagamento (Forma + Prazo) ===
# O preço dos produtos VARIA conforme a Forma/Prazo de pagamento escolhidos no
# modal "Selecione suas Preferências" (home de Almeida e Dantas). Sem selecionar,
# o site usa a condição default da sessão — que não é, necessariamente, a que o
# operador usa para comprar. Aplicamos a condição CONFIGURADA (Configurações) uma
# vez por sessão, logo após o login; a escolha grava na sessão (PHPSESSID/token),
# então vale para TODAS as buscas (textual, por código, catálogo, busca no site).
# Seletores estáveis confirmados por inspeção (scripts/inspecionar_pagamento.py):
# os <select> têm `name` iguais nos dois sites; as opções têm `value` estáveis.

_SEL_FORMA = 'select[name="id_forma_pagamento_preferencia"]'
_SEL_PRAZO = 'select[name="id_condicao_pagamento_preferencia"]'
# A aplicação roda UMA vez (re-abrir o modal várias vezes seguidas dispara a
# instabilidade do site). A VERIFICAÇÃO relê a forma salva — e essa leitura (a
# parte instável, mas read-only) é que re-tenta, pois evita o sucesso FALSO do
# código antigo (logava 'aplicada' sem conferir → preço saía na condição errada).
_PAG_LEITURAS_VERIF = 3


def _abrir_modal_preferencias(page) -> bool:
    """Abre o modal "Selecione suas Preferências" e devolve True se os <select>
    ficaram visíveis. Os selects existem no DOM mas só ficam visíveis com o modal
    aberto. Tenta gatilhos resilientes (o que abriu na inspeção foi o texto
    "Preferências") e VERIFICA a visibilidade após cada clique — autocorretivo."""
    if page.locator(f"{_SEL_FORMA}:visible").count():
        return True
    candidatos = [
        lambda: page.get_by_text("Preferências", exact=False),
        lambda: page.locator("header, .header, nav, .topo").get_by_text(
            "BANCO", exact=False),
        lambda: page.get_by_text("Forma de Pagamento", exact=False),
    ]
    for fab in candidatos:
        try:
            loc = fab()
            n = loc.count()
        except Exception:
            continue
        for i in range(min(n, 3)):   # tenta as primeiras ocorrências
            try:
                loc.nth(i).click(timeout=2_500)
                page.wait_for_timeout(700)
                if page.locator(f"{_SEL_FORMA}:visible").count():
                    return True
            except Exception:
                continue
    return False


def _forma_salva(page, base_url: str,
                 leituras: int = _PAG_LEITURAS_VERIF) -> Optional[str]:
    """Lê o VALUE da forma de pagamento atualmente SALVA na conta (re-navega +
    reabre o modal). É a VERIFICAÇÃO real de que a condição pegou — o site às
    vezes não persiste o 'Confirmar'. Re-tenta a LEITURA (a parte instável, mas
    read-only): devolve o 1º valor NÃO-VAZIO lido, ou None se não conseguiu
    (vazio = modal não assentou; não é uma forma de verdade)."""
    for _ in range(leituras):
        try:
            page.goto(base_url, wait_until="networkidle",
                      timeout=config.SCRAPER_TIMEOUT_SEGUNDOS * 1000)
            if _abrir_modal_preferencias(page):
                # O <select> popula o value via JS DEPOIS de abrir o modal — em
                # leitura rápida ele lê '' (vazio) antes de assentar. Espera o
                # value ficar não-vazio antes de ler (campo 2026-06-27, Almeida).
                try:
                    page.wait_for_function(
                        "s => { const e = document.querySelector(s);"
                        " return !!(e && e.value); }",
                        arg=_SEL_FORMA, timeout=3_000)
                except Exception:
                    pass
                valor = page.eval_on_selector(_SEL_FORMA, "el => el.value")
                if valor:
                    return valor
        except Exception:
            pass
        page.wait_for_timeout(1_200)
    return None


def _aplicar_preferencia_pagamento(page, fornecedor: str) -> tuple:
    """Aplica a condição de pagamento configurada e CONFIRMA que pegou.

    Devolve ``(status, forma_texto)`` para a UI mostrar NA TELA quando não
    confirmar (antes o aviso ia só pro log e o operador não via — auditoria
    2026-07): ``confirmada`` | ``nao_pegou`` | ``nao_conferida`` | ``falhou``
    | ``sem_config``.

    Por que CONFERIR relendo a forma: o site às vezes NÃO persiste o clique
    'Confirmar' (modal instável — campo 2026-06-27, Dantas). O código antigo
    logava 'aplicada' sem verificar, então os preços saíam na condição ERRADA
    (ex.: PIX em vez de banco) EM SILÊNCIO. Agora a função é HONESTA: confirma,
    ou avisa que não pegou, ou avisa que não deu pra conferir — nunca mente.

    A aplicação roda UMA vez (reabrir o modal em sequência rápida dispara a
    instabilidade do site); só a LEITURA de verificação re-tenta. Degrada
    gracioso: NUNCA levanta (não pode derrubar o login); sem preferência
    configurada, não mexe (segue no default do site)."""
    pref = credenciais.carregar_preferencia_pagamento(fornecedor)
    if not pref or not pref.get("forma_value") or not pref.get("prazo_value"):
        return ("sem_config", None)
    base_url = config.FORNECEDORES.get(fornecedor)
    if not base_url:
        return ("sem_config", None)
    alvo, alvo_txt = pref["forma_value"], pref.get("forma_texto")
    # VERIFY-FIRST (perf, campo 2026-07): a condição é PERSISTENTE na conta —
    # na maioria dos logins ela JÁ está certa. Conferir antes (1 leitura) pula a
    # aplicação inteira (~10-15s de modal + esperas fixas) e evita mexer no
    # modal instável à toa. Se a leitura falhar (None), aplica normalmente.
    try:
        if _forma_salva(page, base_url, leituras=1) == alvo:
            _logger.info("Condição de pagamento CONFIRMADA (%s): forma %s "
                         "(já estava configurada)", fornecedor, alvo_txt)
            return ("confirmada", alvo_txt)
    except Exception:
        pass
    try:
        page.goto(base_url, wait_until="networkidle",
                  timeout=config.SCRAPER_TIMEOUT_SEGUNDOS * 1000)
        if not _abrir_modal_preferencias(page):
            _logger.warning("Condição de pagamento (%s): não abri o modal — "
                            "preços podem sair no default do site. Confira.",
                            fornecedor)
            return ("falhou", alvo_txt)
        _select_visivel(page, _SEL_FORMA, alvo, alvo_txt)
        # A forma re-renderiza o <select> de Prazo via JS de forma ASSÍNCRONA: o
        # prazo só existe para boleto/cartão; PIX e PAGAMENTO ANTECIPADO o ocultam.
        # Esperamos o re-render assentar e checamos a visibilidade ATUAL (aplicar
        # prazo num select que vai sumir travava ~60s; campo 2026-06).
        page.wait_for_timeout(1_200)
        if pref.get("prazo_value") and page.locator(f"{_SEL_PRAZO}:visible").count():
            _select_visivel(page, _SEL_PRAZO, pref["prazo_value"], pref.get("prazo_texto"))
        botao = page.get_by_role("button", name="Confirmar")
        if not botao.count():
            botao = page.get_by_text("Confirmar", exact=False)
        botao.first.click(timeout=5_000)
        page.wait_for_timeout(2_500)   # sessão atualiza (PHPSESSID/token)
    except Exception as exc:
        # exc_info=exc grava o TRACEBACK completo no log — sem ele (só o nome do
        # tipo) uma falha real na máquina do cliente é impossível de diagnosticar
        # (achado 2026-07-06). Mensagem ao operador segue limpa; o traceback vai
        # só para o log de suporte.
        _logger.warning("Falha ao aplicar a condição de pagamento em %s (%s) — "
                        "preços podem sair no default do site. Confira.",
                        fornecedor, type(exc).__name__, exc_info=exc)
        return ("falhou", alvo_txt)
    # CONFERE o que REALMENTE ficou salvo na conta.
    try:
        salva = _forma_salva(page, base_url)
    except Exception:   # blindagem do contrato "nunca levanta"
        salva = None
    if salva == alvo:
        _logger.info("Condição de pagamento CONFIRMADA (%s): forma %s",
                     fornecedor, alvo_txt)
        return ("confirmada", alvo_txt)
    elif salva is None:
        _logger.warning("Apliquei a condição em %s, mas NÃO consegui CONFERIR "
                        "(modal instável) — confira se o preço saiu em %s.",
                        fornecedor, alvo_txt)
        return ("nao_conferida", alvo_txt)
    else:
        _logger.warning("Condição em %s NÃO pegou: a conta ficou na forma '%s', "
                        "não na configurada '%s' (%s) — preços podem sair "
                        "errados. Confira.", fornecedor, salva, alvo, alvo_txt)
        return ("nao_pegou", alvo_txt)


def _select_visivel(page, seletor: str, value: str, texto: str | None,
                    timeout_ms: int = 4_000) -> None:
    """Seleciona a opção por VALUE no <select> visível; se o value não existir
    mais (lista do site mudou), tenta pelo TEXTO. Levanta se nenhum funcionar
    (o chamador captura e degrada).

    Timeout CURTO por tentativa (~4s) de propósito: um <select> oculto/ausente ou
    uma opção obsoleta deve degradar rápido, não pendurar a busca por 60s (eram
    30s por value + 30s por texto). Achado de campo 2026-06."""
    sel = page.locator(f"{seletor}:visible").first
    try:
        sel.select_option(value=value, timeout=timeout_ms)
    except Exception:
        if texto:
            sel.select_option(label=texto, timeout=timeout_ms)
        else:
            raise


def _esperar_sessao(context, page, timeout_s: int = 15) -> bool:
    """Espera os cookies de sessão (PHPSESSID + token) aparecerem após o submit
    do login, conferindo a cada 0.5s até ``timeout_s``.

    Substitui o sleep FIXO de 4s, que produzia "falha no login" FALSA quando o
    site estava lento: o POST + redirect que grava os cookies às vezes passa de
    4s (campo 2026-06-25 — um login real levou 26s num momento de lentidão).
    Devolve True assim que a sessão é estabelecida (não espera o prazo todo)."""
    for _ in range(max(1, int(timeout_s / 0.5))):
        nomes = {c["name"] for c in context.cookies()}
        if "PHPSESSID" in nomes and "token" in nomes:
            return True
        page.wait_for_timeout(500)
    return False


# Seletor dos cards de produto na página de busca (mesmo nos dois sites). Usado
# tanto na ESPERA ATIVA (`_carregar_busca`) quanto no `page.evaluate` que lê os
# atributos data-* — DEVEM casar.
_SEL_CARD = ".product-card__info[data-codigo-produto]"


def _carregar_busca(page, url: str) -> None:
    """Abre a página de busca e ESPERA ATIVAMENTE os cards aparecerem — em vez do
    sleep FIXO de 1,5s + ``networkidle``, que torrava ~2,9s POR REQUISIÇÃO
    (benchmark de campo 2026-06-25: 52% do tempo era sleep cego).

    Os cards são server-rendered: já estão no DOM ~0,05s após o
    ``domcontentloaded``, então um HIT retorna instantâneo. Num MISS (nenhum
    card = sem resultado) cai no teto curto ``SCRAPER_ESPERA_CARDS_MS`` e segue —
    o ``evaluate`` do chamador devolve ``[]`` SEM perder preço por sair cedo
    demais (a espera garante que, se houver cards, eles já renderizaram)."""
    page.goto(url, wait_until="domcontentloaded",
              timeout=config.SCRAPER_TIMEOUT_SEGUNDOS * 1000)
    try:
        page.wait_for_selector(_SEL_CARD, state="attached",
                               timeout=config.SCRAPER_ESPERA_CARDS_MS)
    except Exception:
        pass   # nenhum card no prazo → sem resultado (evaluate devolve [])


def _sem_duplicatas(resultados: list["ResultadoBusca"]) -> list["ResultadoBusca"]:
    """Remove cards REPETIDOS que o site às vezes devolve na mesma busca (o
    mesmo produto aparece em destaque + na listagem — achado de campo 2026-06-27).
    Chave: código do fornecedor (único por produto); sem código, cai para
    (descrição, marca, preço). Mantém a 1ª ocorrência, preservando a ordem."""
    vistos: set = set()
    out: list = []
    for r in resultados:
        chave = (r.codigo_fornecedor or "").strip() or (
            r.descricao_encontrada, r.marca, r.preco)
        if chave in vistos:
            continue
        vistos.add(chave)
        out.append(r)
    return out


# === Transporte HTTP das consultas (Fase 2 de perf, 2026-07) ===
# Os cards da busca são SERVER-RENDERED (zero XHR — inspeção 2026-06-29): um GET
# com os cookies da sessão logada devolve o MESMO HTML que o navegador, com o
# data-preco da condição de pagamento aplicada (validado em campo: preço via
# HTTP == preço via navegador nos 2 fornecedores). Vantagens: ~1/3 do tempo por
# consulta, MISS instantâneo (sem teto de espera por card) e — crucial — HTTP em
# thread é seguro (o problema de greenlet é do Playwright). O navegador segue
# como transporte de login/pagamento/NCM e como FALLBACK por consulta.

_RE_CARD_TAG = re.compile(r'<[^>]*\bdata-codigo-produto="[^"]*"[^>]*>')
_RE_DATA_ATTR = re.compile(r'(data-[\w-]+)="([^"]*)"')
_RE_HREF = re.compile(r'href="([^"]+)"')


class SessaoHTTPExpirou(RuntimeError):
    """A sessão HTTP deslogou no meio de um lote em modo http-only (paralelo).
    O item vira ErroBusca e a re-busca incremental (que reloga) recupera."""


def _cookie_header_de(context) -> str:
    """Serializa os cookies do contexto Playwright para o header Cookie."""
    return "; ".join(f"{c['name']}={c['value']}" for c in context.cookies())


def _http_get(url: str, cookie_header: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Cookie": cookie_header})
    with urllib.request.urlopen(
            req, timeout=config.SCRAPER_TIMEOUT_SEGUNDOS) as resp:
        return resp.read().decode("utf-8", "ignore")


_RE_A_TAG = re.compile(r"<a\b[^>]*>")


def _cards_do_html(html: str, max_cards: int) -> list[dict]:
    """Extrai os atributos ``data-*`` dos cards do HTML cru + a URL do produto.

    A URL (usada pelo "Identificar NCM") vem do ``<a href>`` do produto. Na
    página LOGADA o anchor vem DEPOIS do card, com ``data-id-produto`` — o
    mapa por id casa card→href sem depender de posição (campo 2026-07-06).
    Fallback: layout deslogado, onde o ``<a>`` EMBRULHA o card (vem antes)."""
    hrefs_por_id: dict[str, str] = {}
    for a in _RE_A_TAG.finditer(html):
        tag = a.group(0)
        if "data-id-produto" not in tag:
            continue
        h = _RE_HREF.search(tag)
        i = re.search(r'data-id-produto="([^"]+)"', tag)
        if h and i and i.group(1) not in hrefs_por_id:
            hrefs_por_id[i.group(1)] = h.group(1)

    out: list[dict] = []
    for m in _RE_CARD_TAG.finditer(html):
        attrs = dict(_RE_DATA_ATTR.findall(m.group(0)))
        url = hrefs_por_id.get((attrs.get("data-id-produto") or "").strip())
        if not url:
            ini = html.rfind("<a ", max(0, m.start() - 3000), m.start())
            if ini >= 0:
                h = _RE_HREF.search(html[ini:m.start()])
                url = h.group(1) if h else None
        attrs["url"] = url
        out.append(attrs)
        if len(out) >= max_cards:
            break
    return out


def _html_deslogado(html: str) -> bool:
    """True se o HTML é de uma sessão DESLOGADA.

    Sinal medido em campo (2026-07-07): a página deslogada é um "login-wall" que
    repete o CTA "Entrar" dezenas de vezes (291 numa medição) e NÃO tem o menu de
    conta "Sair"; a página LOGADA é o oposto ("Entrar"=0, "Sair"=4). Cobre o caso
    em que o deslogado devolve 0 cards (não cards zerados) — que a guarda de
    preços-zerados NÃO pegava, e a busca voltava vazia sem relogar (bug do "2º
    leilão", 2026-07-07).

    CONSERVADOR de propósito (exige AMBOS os sinais): esta função também roda no
    lote automático (via `_consultar_cards_http`), então um falso-positivo
    zeraria o cookie e causaria churn de login — o que já bloqueou a conta uma
    vez. Marcar logado como deslogado é o erro caro; preferir NÃO marcar na
    dúvida (o pior caso vira uma busca vazia, não um bloqueio de conta)."""
    low = html.lower()
    return low.count("entrar") >= 20 and low.count("sair") == 0


def _consultar_cards_http(forn, url: str) -> Optional[list[dict]]:
    """Busca os cards de ``url`` via HTTP com a sessão logada de ``forn``.

    Devolve a lista de atributos (mesmo formato do ``page.evaluate`` do caminho
    navegador), ou ``None`` = "use o navegador" (sem cookie, flag desligada ou
    falha de rede — o fallback preserva o comportamento antigo).

    Guarda anti-deslogado: cards presentes mas TODOS com preço <= 0 é a
    assinatura de sessão HTTP expirada (deslogado o site mostra o card com
    data-preco="0.00"). Nesse caso o HTTP é DESLIGADO para esta sessão (as
    consultas seguem 100% navegador — corretas, só mais lentas) e logamos o
    porquê; em modo http-only (paralelo) levanta SessaoHTTPExpirou."""
    if not config.SCRAPER_HTTP_QUERIES:
        return None
    cookie = getattr(forn, "_http_cookie", None)
    if not cookie:
        return None
    try:
        html = _http_get(url, cookie)
    except Exception as exc:
        if getattr(forn, "_http_only", False):
            raise SessaoHTTPExpirou(
                f"consulta HTTP falhou em modo paralelo ({type(exc).__name__})")
        return None   # rede/HTTP falhou → o navegador cuida desta consulta
    # Deslogado (página de login) → zera o cookie e sinaliza, ANTES de olhar os
    # cards: o deslogado às vezes devolve 0 cards (não cards zerados), o que a
    # guarda de preços-zerados abaixo não pegava (bug do "2º leilão").
    if _html_deslogado(html):
        forn._http_cookie = None
        _logger.warning(
            "Sessão HTTP de %s deslogada (página de login) — consultas seguem "
            "pelo navegador nesta sessão.", getattr(forn, "nome", "?"))
        if getattr(forn, "_http_only", False):
            raise SessaoHTTPExpirou("sessão HTTP deslogou (página de login)")
        return None
    cards = _cards_do_html(html, _MAX_RESULTADOS_POR_QUERY)

    def _preco(c):
        try:
            return float((c.get("data-preco") or "0").replace(",", "."))
        except ValueError:
            return 0.0

    if cards and all(_preco(c) <= 0 for c in cards):
        forn._http_cookie = None   # sessão HTTP morreu — não insistir
        _logger.warning(
            "Sessão HTTP de %s expirou (preços zerados) — consultas seguem "
            "pelo navegador nesta sessão.", getattr(forn, "nome", "?"))
        if getattr(forn, "_http_only", False):
            raise SessaoHTTPExpirou("sessão HTTP deslogou (preços zerados)")
        return None
    return cards


# === Driver Playwright COMPARTILHADO do processo (2026-07-06) ===
# `sync_playwright().start()` só pode existir UMA vez por thread — o 2º start
# falha com "sync API inside the asyncio loop" (validado em campo ao manter
# Almeida E Dantas vivos ao mesmo tempo no cache de sessões). O driver passa a
# ser um singleton do processo: cada fornecedor abre o SEU navegador/contexto
# nele, e fechar uma sessão fecha só o navegador dela (o driver fica).

_pw_driver = None


def _playwright_compartilhado():
    """Driver único do processo (criar/usar sempre na MESMA thread — a
    principal). Se o driver morreu (ex.: crash do subprocesso), recria uma vez."""
    global _pw_driver
    from playwright.sync_api import sync_playwright
    if _pw_driver is None:
        _pw_driver = sync_playwright().start()
    return _pw_driver


def _resetar_playwright_compartilhado() -> None:
    """Derruba o driver (para o próximo uso recriar). Usado quando um launch
    falha — driver zumbi não se recupera sozinho."""
    global _pw_driver
    if _pw_driver is not None:
        try:
            _pw_driver.stop()
        except Exception:
            pass
        _pw_driver = None


class AlmeidaDistribuidor:
    """
    Scraper de almeidadistribuidor.com.br.

    Gerenciado como context manager: o `__enter__` faz o login uma vez,
    `__exit__` fecha o browser. Chamadas sucessivas a `.buscar()` reusam
    a sessão (cookies PHPSESSID + token herdados).

    **Modelo serial**: 1 página por instância. Não é thread-safe — todas
    as chamadas devem vir da mesma thread que criou o context manager
    (limitação do Playwright sync API). Rate limiter dorme entre buscas
    para respeitar parceiros.
    """

    nome = "almeida"

    def __init__(
        self,
        email: str,
        senha: str,
        *,
        headless: bool = True,
        pool_size: int = None,   # aceito mas ignorado (compat. com testes antigos)
        max_req_por_segundo: float = None,
    ):
        self._email = email
        self._senha = senha
        self._headless = headless
        # Mantido para compat. com testes que ainda checam `_pool_size`,
        # mas não usamos pool real — `_page` é única (serial).
        self._pool_size = pool_size or config.SCRAPER_POOL_SIZE
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._http_cookie = None   # header Cookie da sessão (transporte HTTP)
        self._http_only = False    # modo paralelo: sem fallback de navegador
        self._rate_limiter = _RateLimiter(
            max_req_por_segundo or config.SCRAPER_MAX_REQ_POR_SEGUNDO
        )

    def __enter__(self) -> "AlmeidaDistribuidor":
        # Import local para não pagar o custo de carregar Playwright em
        # quem só importa o módulo (ex: testes da função pura).
        from playwright.sync_api import sync_playwright

        # Se qualquer passo falhar (login com senha trocada, site fora,
        # timeout), o `__exit__` NÃO roda — o `with` só registra o
        # contexto se `__enter__` retornar. Sem a limpeza explícita aqui,
        # o Chromium headless + driver ficariam órfãos a cada login falho,
        # acumulando processos na máquina Windows do operador. Por isso
        # fechamos tudo no except antes de re-levantar.
        try:
            # Driver COMPARTILHADO do processo (2º sync_playwright().start() na
            # mesma thread quebra); cada sessão tem só o próprio navegador.
            try:
                self._playwright = _playwright_compartilhado()
                self._browser = self._playwright.chromium.launch(headless=self._headless)
            except Exception:
                # Driver zumbi (crash anterior) — recria uma vez e re-tenta.
                _resetar_playwright_compartilhado()
                self._playwright = _playwright_compartilhado()
                self._browser = self._playwright.chromium.launch(headless=self._headless)
            self._context = self._browser.new_context(viewport={"width": 1280, "height": 900})
            self._page = self._context.new_page()
            # Bound de tempo por operação Playwright (navegação, espera de
            # seletor, clique). Centralizado em config.SCRAPER_TIMEOUT_SEGUNDOS
            # para afinar num lugar só durante o piloto. 30s = comportamento
            # atual (era o default implícito do Playwright) — zero regressão.
            _timeout_ms = config.SCRAPER_TIMEOUT_SEGUNDOS * 1000
            self._page.set_default_timeout(_timeout_ms)
            self._page.set_default_navigation_timeout(_timeout_ms)
            self._login(self._page)
            # Aplica a condição de pagamento configurada (Forma+Prazo) na sessão,
            # senão o preço vem da condição default do site. Gracioso: sem config
            # ou modal ausente → segue no default (não derruba o login).
            self._pagamento_status = _aplicar_preferencia_pagamento(self._page, self.nome)
            # Cookies extraídos DEPOIS do pagamento: a condição vive na sessão
            # do servidor, e o header pronto habilita o transporte HTTP das
            # consultas (Fase 2 de perf; falha aqui só desliga o atalho).
            try:
                self._http_cookie = _cookie_header_de(self._context)
            except Exception:
                self._http_cookie = None
        except BaseException:
            self._fechar()
            raise
        return self

    def __exit__(self, *_args) -> None:
        self._fechar()

    def _fechar(self) -> None:
        """Fecha browser e para o Playwright, tolerando recursos ausentes.

        Compartilhado entre o caminho de sucesso (`__exit__`) e o de falha
        no `__enter__`. Idempotente: zera as referências para não fechar
        duas vezes se chamado de novo."""
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            self._browser = None
            self._context = None
            self._page = None
            # NÃO para o driver: ele é COMPARTILHADO do processo (outra sessão
            # pode estar usando). Só solta a referência.
            self._playwright = None

    def _login(self, page) -> None:
        page.goto(f"{_BASE_URL_ALMEIDA}/entrar",
                  wait_until="networkidle", timeout=config.SCRAPER_TIMEOUT_SEGUNDOS * 1000)
        page.locator("input[name='email']").first.fill(self._email)
        senha_input = page.locator("input[name='senha']").first
        senha_input.fill(self._senha)
        # O submit do form é via JS — pressionar Enter é o jeito mais
        # robusto (o botão "Entrar" tem várias instâncias na página e
        # nenhuma tem role=button bem definido).
        senha_input.press("Enter")
        # Espera ATIVA pelos cookies de sessão (PHPSESSID + token, que só
        # aparecem após login bem-sucedido) — não um sleep fixo de 4s, que
        # falhava falso em dias lentos (ver _esperar_sessao).
        if not _esperar_sessao(self._context, page):
            raise RuntimeError(
                "Falha no login Almeida — cookies de sessão não foram "
                "estabelecidos. Verifique email/senha em data/credenciais.json."
            )

    def buscar(self, descricao: str,
               _log: Optional["LogItemFornecedor"] = None,
               idx_codigo: Optional[dict] = None) -> list[ResultadoBusca]:
        """Tenta as camadas de busca em ordem. Para na primeira que
        produzir resultados relevantes. Aplica rate limit antes de cada
        busca real para respeitar o site parceiro.

        `_log` é opcional — quando fornecido, anexa um `LogQueryInterna`
        por camada tentada. Usado pelo modo diagnóstico (ortogonal ao
        algoritmo: nada do que o método faz depende do log).
        """
        if not descricao or not descricao.strip():
            return []

        # Camadas: query distintiva curta primeiro, descrição completa por
        # último (ver `_montar_camadas`). Antes mandávamos a descrição
        # inteira primeiro, o que poluía os resultados com produtos sem relação.
        camadas = _montar_camadas(descricao)

        for confianca, query in camadas:
            self._rate_limiter.acquire()
            brutos = self._buscar_query(self._page, query, confianca)
            relevantes = []
            melhor_rel = 0.0
            for r in brutos:
                rel = relevancia(descricao, r.descricao_encontrada)
                if rel > melhor_rel:
                    melhor_rel = rel
                # Mantém o candidato só se a relevância textual passa o
                # threshold. ANTES havia um "or conhecido" que furava o filtro:
                # qualquer produto cujo CÓDIGO estivesse no catálogo entrava
                # mesmo sem relação com o item (mangueira de gás 0.00, cap
                # 0.075, adesivo de diesel 0.175 num edital de adesivo PVC) e
                # ainda ia pro topo, pré-selecionado errado (feedback do cliente
                # 2026-06). O reconhecimento "no seu catálogo" continua valendo
                # como PRIORIDADE entre os RELEVANTES (em _top_n_por_relevancia);
                # catálogo com texto pobre tem a Camada 0 (busca por código) e a
                # busca manual no catálogo.
                if rel >= _RELEVANCIA_MINIMA:
                    relevantes.append(r)
            if _log is not None:
                _log.queries_textuais.append(LogQueryInterna(
                    nivel=confianca, query=query,
                    n_brutos=len(brutos),
                    n_passaram=len(relevantes),
                    melhor_relevancia=melhor_rel,
                    parou_aqui=bool(relevantes),
                ))
            if relevantes:
                return _top_n_por_relevancia(
                    descricao, relevantes,
                    config.SCRAPER_MAX_CANDIDATOS_POR_FORNECEDOR,
                    idx_codigo=idx_codigo,
                )

        # Sem candidatos acima do threshold: retorna lista vazia.
        # Decisão de 2026-05-27 após diagnóstico real mostrar que o
        # "top 3 variação" antigo trazia produtos irrelevantes — pior do
        # que nada na perspectiva do operador ("se não acha o certo,
        # melhor mostrar nada e eu busco à mão").
        if _log is not None:
            _log.caiu_fallback = True
        return []

    def buscar_por_codigo(self, codigo: str) -> list[ResultadoBusca]:
        """Busca DETERMINÍSTICA por código de produto — Camada 0 da Fase 3.

        Quando o operador importou planilhas históricas (matcher.py), cada
        produto do edital pode resolver para um `codigo_almeida` conhecido.
        Buscar por código em `/busca?s={codigo}` retorna o produto exato,
        sem disputa de relevância textual.

        Retorna lista com 0 ou 1 resultado:
        - 0 se o código foi descontinuado / não existe mais
        - 1 com `confianca='exato'` quando o site devolve o produto
        - Se o site devolver múltiplos (improvável para código), aceitamos
          o primeiro card. O operador pode trocar na UI se precisar.
        """
        if not codigo or not str(codigo).strip():
            return []
        self._rate_limiter.acquire()
        # Reusa _buscar_query — a infraestrutura é a mesma; só a query muda.
        # `confianca='exato'` porque casamos por código, não por texto.
        resultados = self._buscar_query(self._page, str(codigo).strip(), "exato")
        # Limita ao 1º card: busca por código deveria ser determinística.
        # Se houver mais (caso raro de código compartilhado), o 1º é o relevante.
        return resultados[:1]

    def _buscar_query(self, page, query: str, confianca: str) -> list[ResultadoBusca]:
        """Faz uma busca crua e retorna até N resultados parseados (sem filtro
        de relevância — caller filtra). Transporte: HTTP com a sessão logada
        (rápido); navegador como fallback (comportamento antigo)."""
        url = f"{_BASE_URL_ALMEIDA}/busca?s={quote_plus(query)}"
        cards_attrs = _consultar_cards_http(self, url)
        if cards_attrs is None:
            _carregar_busca(page, url)

            # Pega todos os cards e seus atributos data-* num único round-trip
            # ao browser. Mais barato que `.locator().all()` + `.get_attribute()`
            # por card (que dispara N IPCs).
            cards_attrs = page.evaluate("""
                () => {
                    const cards = document.querySelectorAll(
                        '.product-card__info[data-codigo-produto]'
                    );
                    return Array.from(cards).slice(0, %d).map(el => {
                        const wrap = el.closest('.product-card') || el.parentElement;
                        const a = wrap ? wrap.querySelector('a[href]') : null;
                        return {
                            'data-codigo-produto': el.getAttribute('data-codigo-produto'),
                            'data-nome':           el.getAttribute('data-nome'),
                            'data-preco':          el.getAttribute('data-preco'),
                            'data-marca':          el.getAttribute('data-marca'),
                            'url':                 a ? a.href : null,
                        };
                    });
                }
            """ % _MAX_RESULTADOS_POR_QUERY)

        out = []
        for attrs in cards_attrs:
            r = _parse_card_attributes(attrs, confianca)
            if r is not None:
                out.append(r)
        return _sem_duplicatas(out)


# === Dantas ===
# Site estruturalmente similar ao Almeida (inspeção autônoma em 2026-05-27,
# scripts/inspecionar_dantas.py): mesmo padrão de URL (/entrar, /busca?s=)
# e mesmos atributos data-* em .product-card__info[data-codigo-produto].
# Login também aceita email + senha; cookies relevantes pós-login são
# PHPSESSID + token (mesmo conjunto do Almeida).
#
# Em vez de criar uma abstração para futuros fornecedores (CLAUDE.md §9
# proíbe), repetimos a estrutura — quando aparecer o 3º fornecedor com
# padrão diferente, refatoramos com contexto real.

_BASE_URL_DANTAS = "https://www.dantasdistribuidora.com.br"


def _parse_card_dantas(attrs: dict, confianca: str) -> Optional[ResultadoBusca]:
    """Versão Dantas do parse — idêntica ao Almeida na estrutura,
    diferindo só no `fornecedor` registrado no resultado.

    Mantida como função separada (não parametrizando o Almeida) para
    deixar explícito que são dois sites distintos — facilita debug
    quando um site mudar o layout antes do outro.
    """
    codigo = (attrs.get("data-codigo-produto") or "").strip()
    nome = (attrs.get("data-nome") or "").strip()
    marca = (attrs.get("data-marca") or "").strip()
    preco_str = (attrs.get("data-preco") or "").strip()

    if not codigo or not nome or not preco_str:
        return None
    try:
        preco = float(preco_str.replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(preco) or preco <= 0:
        return None   # NaN/Infinity do data-preco furavam o '<= 0' (adversarial 2026-06)

    return ResultadoBusca(
        descricao_encontrada=nome,
        marca=marca,
        preco=preco,
        codigo_fornecedor=codigo,
        confianca=confianca,
        fornecedor="dantas",
        url=attrs.get("url"),
    )


class DantasDistribuidora:
    """Scraper de dantasdistribuidora.com.br.

    Estrutura idêntica ao `AlmeidaDistribuidor` (ver inspeção em
    scripts/inspecionar_dantas.py). Mantemos como classe separada porque
    são dois sites distintos: se um mudar layout, o outro continua
    funcionando sem mexer no código compartilhado.

    Modelo serial — mesmas restrições do Almeida (1 página, mesma thread).
    """

    nome = "dantas"

    def __init__(
        self,
        email: str,
        senha: str,
        *,
        headless: bool = True,
        pool_size: int = None,   # ignorado, mantido para compat. com testes
        max_req_por_segundo: float = None,
    ):
        self._email = email
        self._senha = senha
        self._headless = headless
        self._pool_size = pool_size or config.SCRAPER_POOL_SIZE
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._http_cookie = None   # header Cookie da sessão (transporte HTTP)
        self._http_only = False    # modo paralelo: sem fallback de navegador
        self._rate_limiter = _RateLimiter(
            max_req_por_segundo or config.SCRAPER_MAX_REQ_POR_SEGUNDO
        )

    def __enter__(self) -> "DantasDistribuidora":
        from playwright.sync_api import sync_playwright

        # Mesma proteção do Almeida: limpa Chromium/driver se o login
        # falhar, senão sobram processos órfãos a cada falha (ver comentário
        # detalhado em AlmeidaDistribuidor.__enter__).
        try:
            # Driver COMPARTILHADO do processo (2º sync_playwright().start() na
            # mesma thread quebra); cada sessão tem só o próprio navegador.
            try:
                self._playwright = _playwright_compartilhado()
                self._browser = self._playwright.chromium.launch(headless=self._headless)
            except Exception:
                # Driver zumbi (crash anterior) — recria uma vez e re-tenta.
                _resetar_playwright_compartilhado()
                self._playwright = _playwright_compartilhado()
                self._browser = self._playwright.chromium.launch(headless=self._headless)
            self._context = self._browser.new_context(viewport={"width": 1280, "height": 900})
            self._page = self._context.new_page()
            # Bound de tempo por operação Playwright (navegação, espera de
            # seletor, clique). Centralizado em config.SCRAPER_TIMEOUT_SEGUNDOS
            # para afinar num lugar só durante o piloto. 30s = comportamento
            # atual (era o default implícito do Playwright) — zero regressão.
            _timeout_ms = config.SCRAPER_TIMEOUT_SEGUNDOS * 1000
            self._page.set_default_timeout(_timeout_ms)
            self._page.set_default_navigation_timeout(_timeout_ms)
            self._login(self._page)
            # Aplica a condição de pagamento configurada (Forma+Prazo) na sessão,
            # senão o preço vem da condição default do site. Gracioso: sem config
            # ou modal ausente → segue no default (não derruba o login).
            self._pagamento_status = _aplicar_preferencia_pagamento(self._page, self.nome)
            # Cookies extraídos DEPOIS do pagamento: a condição vive na sessão
            # do servidor, e o header pronto habilita o transporte HTTP das
            # consultas (Fase 2 de perf; falha aqui só desliga o atalho).
            try:
                self._http_cookie = _cookie_header_de(self._context)
            except Exception:
                self._http_cookie = None
        except BaseException:
            self._fechar()
            raise
        return self

    def __exit__(self, *_args) -> None:
        self._fechar()

    def _fechar(self) -> None:
        """Fecha browser e para o Playwright, tolerando recursos ausentes.

        Compartilhado entre `__exit__` (sucesso) e o except do `__enter__`
        (falha). Idempotente — zera as referências após fechar."""
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            self._browser = None
            self._context = None
            self._page = None
            # NÃO para o driver: ele é COMPARTILHADO do processo (outra sessão
            # pode estar usando). Só solta a referência.
            self._playwright = None

    def _login(self, page) -> None:
        page.goto(f"{_BASE_URL_DANTAS}/entrar",
                  wait_until="networkidle", timeout=config.SCRAPER_TIMEOUT_SEGUNDOS * 1000)
        page.locator("input[name='email']").first.fill(self._email)
        senha_input = page.locator("input[name='senha']").first
        senha_input.fill(self._senha)
        senha_input.press("Enter")
        # Espera ATIVA pelos cookies de sessão (não sleep fixo de 4s — ver
        # _esperar_sessao; falhava falso quando o site estava lento).
        if not _esperar_sessao(self._context, page):
            raise RuntimeError(
                "Falha no login Dantas — cookies de sessão não foram "
                "estabelecidos. Verifique email/senha em data/credenciais.json."
            )

    def buscar(self, descricao: str,
               _log: Optional["LogItemFornecedor"] = None,
               idx_codigo: Optional[dict] = None) -> list[ResultadoBusca]:
        if not descricao or not descricao.strip():
            return []

        # Mesma estratégia do Almeida: query distintiva primeiro (ver
        # `_montar_camadas`).
        camadas = _montar_camadas(descricao)

        for confianca, query in camadas:
            self._rate_limiter.acquire()
            brutos = self._buscar_query(self._page, query, confianca)
            relevantes = []
            melhor_rel = 0.0
            for r in brutos:
                rel = relevancia(descricao, r.descricao_encontrada)
                if rel > melhor_rel:
                    melhor_rel = rel
                # Mantém o candidato só se a relevância textual passa o
                # threshold. ANTES havia um "or conhecido" que furava o filtro:
                # qualquer produto cujo CÓDIGO estivesse no catálogo entrava
                # mesmo sem relação com o item (mangueira de gás 0.00, cap
                # 0.075, adesivo de diesel 0.175 num edital de adesivo PVC) e
                # ainda ia pro topo, pré-selecionado errado (feedback do cliente
                # 2026-06). O reconhecimento "no seu catálogo" continua valendo
                # como PRIORIDADE entre os RELEVANTES (em _top_n_por_relevancia);
                # catálogo com texto pobre tem a Camada 0 (busca por código) e a
                # busca manual no catálogo.
                if rel >= _RELEVANCIA_MINIMA:
                    relevantes.append(r)
            if _log is not None:
                _log.queries_textuais.append(LogQueryInterna(
                    nivel=confianca, query=query,
                    n_brutos=len(brutos),
                    n_passaram=len(relevantes),
                    melhor_relevancia=melhor_rel,
                    parou_aqui=bool(relevantes),
                ))
            if relevantes:
                return _top_n_por_relevancia(
                    descricao, relevantes,
                    config.SCRAPER_MAX_CANDIDATOS_POR_FORNECEDOR,
                    idx_codigo=idx_codigo,
                )

        # Mesma decisão do Almeida: sem candidatos acima do threshold,
        # retorna vazio (ver explicação lá).
        if _log is not None:
            _log.caiu_fallback = True
        return []

    def buscar_por_codigo(self, codigo: str) -> list[ResultadoBusca]:
        """Mesma idea do Almeida — Camada 0 da Fase 3. Ver docstring lá.

        Mantemos como classe separada porque os sites são distintos: se um
        mudar de layout, o outro continua funcionando sem trocar nada.
        """
        if not codigo or not str(codigo).strip():
            return []
        self._rate_limiter.acquire()
        resultados = self._buscar_query(self._page, str(codigo).strip(), "exato")
        return resultados[:1]

    def _buscar_query(self, page, query: str, confianca: str) -> list[ResultadoBusca]:
        url = f"{_BASE_URL_DANTAS}/busca?s={quote_plus(query)}"
        cards_attrs = _consultar_cards_http(self, url)
        if cards_attrs is not None:
            out = []
            for attrs in cards_attrs:
                r = _parse_card_dantas(attrs, confianca)
                if r is not None:
                    out.append(r)
            return _sem_duplicatas(out)
        _carregar_busca(page, url)

        cards_attrs = page.evaluate("""
            () => {
                const cards = document.querySelectorAll(
                    '.product-card__info[data-codigo-produto]'
                );
                return Array.from(cards).slice(0, %d).map(el => {
                    const wrap = el.closest('.product-card') || el.parentElement;
                    const a = wrap ? wrap.querySelector('a[href]') : null;
                    return {
                        'data-codigo-produto': el.getAttribute('data-codigo-produto'),
                        'data-nome':           el.getAttribute('data-nome'),
                        'data-preco':          el.getAttribute('data-preco'),
                        'data-marca':          el.getAttribute('data-marca'),
                        'url':                 a ? a.href : null,
                    };
                });
            }
        """ % _MAX_RESULTADOS_POR_QUERY)

        out = []
        for attrs in cards_attrs:
            r = _parse_card_dantas(attrs, confianca)
            if r is not None:
                out.append(r)
        return _sem_duplicatas(out)


# === Orquestrador em lote ===

ProgressoCallback = Callable[[int, int, str], None]


@dataclass(frozen=True)
class ErroBusca:
    """Falha isolada do scraper em um item — não bloqueia o batch, mas
    aparece no painel de erros da UI."""
    indice_item: int
    fornecedor: str
    descricao_resumo: str   # primeiros chars da descrição (para identificar)
    mensagem: str            # tipo de erro + str do exception


@dataclass
class ResultadoLote:
    """Resultado completo de um batch de busca.

    A UI usa:
    - `resultados` para mostrar opções por item e seleção do operador
    - `erros` para o painel "ver erros (N)" quando o batch teve falhas
    - `cache_hits`/`cache_misses` para o operador entender quanto a memória
       do sistema economizou (transparência sobre cache)
    - `codigo_hits` para quanto a Camada 0 (catálogo histórico) resolveu
       sem precisar de busca textual — mostra valor do importador.
    """
    resultados: dict[int, list[ResultadoBusca]]
    erros: list[ErroBusca] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    codigo_hits: int = 0       # Camada 0: itens resolvidos por busca por código
    llm_judge_hits: int = 0    # Camada 3: itens reordenados/filtrados pelo Gemini


def _payload_to_resultados(
    payload_json: str, fornecedor: str
) -> list[ResultadoBusca]:
    """Reconstrói lista de ResultadoBusca a partir do JSON salvo no cache."""
    try:
        data = json.loads(payload_json)
    except (TypeError, ValueError):
        return []
    out: list[ResultadoBusca] = []
    for d in data:
        try:
            # Reconstrói TODOS os campos: o cache grava via asdict (11 campos);
            # reconstruir só 6 fazia ncm/url e o reconhecimento por código
            # (no_catalogo/produto_catalogo_id/descricao_catalogo) sumirem em
            # cache hit — o badge "no seu catálogo" desaparecia na re-busca.
            out.append(ResultadoBusca(
                descricao_encontrada=d["descricao_encontrada"],
                marca=d.get("marca", ""),
                preco=float(d["preco"]),
                codigo_fornecedor=d["codigo_fornecedor"],
                confianca=d.get("confianca", "exato"),
                fornecedor=d.get("fornecedor", fornecedor),
                ncm=d.get("ncm"),
                url=d.get("url"),
                no_catalogo=d.get("no_catalogo", False),
                produto_catalogo_id=d.get("produto_catalogo_id"),
                descricao_catalogo=d.get("descricao_catalogo"),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def buscar_precos_para_itens(
    itens: list[ItemExtraido],
    fornecedores: list[Fornecedor],
    *,
    progresso_callback: Optional[ProgressoCallback] = None,
    usar_cache: bool = True,
    usar_catalogo: bool = True,
    usar_canonico: bool = True,
    usar_llm_judge: bool = True,
    diagnostico: Optional[LogDiagnostico] = None,
    max_workers: Optional[int] = None,  # itens em paralelo (ver docstring)
) -> ResultadoLote:
    """Busca em batch SERIAL com cache TTL=2d, Camada 0 (catálogo) e
    coleta de erros.

    Pipeline por item × fornecedor (Q12):
    1. Cache TTL=2d em precos_cache_descricao → hit reaproveita
    2. Camada 0: matcher fuzzy contra catálogo histórico → busca por código
    3. Caminho atual: busca textual em N camadas (descrição/keywords)
    4. Aprendizado (Camada 4) — não aqui; consultado no orquestrador da UI

    PARALELISMO POR ITEM (2026-07-06): quando TODOS os fornecedores têm
    transporte HTTP (`_http_cookie`), os itens rodam num pool de threads
    (`max_workers` ou config.SCRAPER_POOL_SIZE) — consultas HTTP são
    thread-safe. Os workers ficam em modo `_http_only` e NUNCA tocam o
    Playwright (que é preso à thread dona — o greenlet error de 2026-05-27);
    item cuja sessão HTTP expirar volta numa 2ª passada SERIAL com o
    fallback de navegador. Callback de progresso SEMPRE dispara na thread
    de quem chamou. Diagnóstico ligado ou fornecedor sem HTTP → serial
    (comportamento histórico, idêntico).

    Erros isolados (timeout, layout mudou) **não** quebram batch — vão
    para `ResultadoLote.erros` que a UI mostra num painel.

    Args:
        itens: lista de ItemExtraido vindos da revisão.
        fornecedores: instâncias já abertas (login feito) de Almeida/Dantas.
        progresso_callback: callback `(atual, total, msg)` a cada item.
        usar_cache: False em testes que querem chamar scraper real.
        usar_catalogo: False desabilita Camada 0 (raro — útil em testes que
            isolam só o caminho textual).
        usar_llm_judge: True ativa Camada 3 (judge do Gemini sob demanda).
            **Default True** — o judge fica ligado por padrão para resolver
            sinônimos que as Camadas 0-2 não pegam. Compartilha a mesma API
            key com extrator_llm.extrair_via_gemini — se a quota daily acabar
            pelo judge, a extração de PDF também é afetada; por isso o judge
            só dispara sob demanda (quando o casamento textual + fuzzy não
            atinge relevância suficiente).
        diagnostico: opcional. Se fornecido, o orquestrador preenche o log
            com o que cada camada fez por item × fornecedor. Use com
            `pregoes.diagnostico.exportar_xlsx` para gerar a planilha de
            análise. Não altera o algoritmo — só observação.
        max_workers: nº de itens em paralelo quando o transporte HTTP
            está ativo (default: config.SCRAPER_POOL_SIZE). 1 força serial.
    """
    resultados: dict[int, list[ResultadoBusca]] = {}
    erros: list[ErroBusca] = []
    cache_hits = 0
    cache_misses = 0
    codigo_hits = 0
    llm_judge_hits = 0
    total = len(itens)

    if total == 0:
        return ResultadoLote(resultados={}, erros=[])

    # Conexão SQLite na thread do orquestrador. Sem lock — serial.
    conn = None
    if usar_cache or usar_catalogo:
        conn = db.conectar()   # WAL + busy timeout (evita 'database is locked')

    # Invalida cache se o algoritmo de busca mudou. Evita o operador
    # ver resultados de versões antigas (e.g. fallback top-3 que retornava
    # lixo). O custo de re-buscar é alto na primeira vez, zero depois.
    if conn is not None and usar_cache:
        _invalidar_cache_se_versao_mudou(conn)

    # Camada 0: carrega catálogo histórico uma vez (em memória) para reuso.
    # Lista vazia (catálogo não populado ainda) é OK — matcher vai retornar
    # None para todos os itens e o pipeline cai no caminho textual.
    catalogo: list[ProdutoCatalogo] = []
    if conn is not None and usar_catalogo:
        try:
            catalogo = carregar_catalogo(conn)
        except Exception:
            # Banco sem schema, corrompido etc — não impede o caminho atual.
            catalogo = []

    # Índice reverso (fornecedor, código) → produto do catálogo: reconhece, entre
    # os candidatos que a busca devolve, quais o cliente JÁ TEM pelo código
    # (feedback do cliente). Vazio quando o catálogo não está populado.
    idx_codigo = indice_por_codigo(catalogo) if catalogo else {}

    # Mapeamentos aprendidos (escolhas anteriores do operador) em memória, para
    # o reuso FUZZY da Camada 4 plena: descrição nova parecida com uma já
    # ensinada → reusa o código direto. Carrega uma vez por lote.
    aprendidos_lista: list = []
    if conn is not None:
        try:
            aprendidos_lista = db.aprendidos_carregar(conn)
        except Exception:
            aprendidos_lista = []

    # Fase B: canoniza as descrições do edital em LOTE (1-N chamadas LLM por
    # edital, cacheadas) para casar com o canônico do catálogo. Só vale a
    # pena se o catálogo foi normalizado (tem `canonico`) e há chave Gemini.
    # Sem isso, `canon_itens` fica vazio e o matcher cai no modo cru — limpo,
    # porque o modo cru compara descricao_canonica (não o canônico).
    canon_itens: dict[str, str] = {}
    if (usar_catalogo and usar_canonico and conn is not None
            and any(p.canonico for p in catalogo)
            and credenciais.carregar_chave_api("gemini")):
        try:
            from .alinhador import normalizar_canonico
            canon_itens = normalizar_canonico(
                [it.descricao for it in itens if it.descricao], conn=conn)
        except Exception:
            canon_itens = {}

    # Detecta uma vez se cada fornecedor aceita o argumento opcional `_log`
    # (mocks de teste antigos não aceitam). Evita try/except por chamada.
    import inspect
    suporta_log = {
        f.nome: "_log" in inspect.signature(f.buscar).parameters
        for f in fornecedores
    }
    suporta_idx = {
        f.nome: "idx_codigo" in inspect.signature(f.buscar).parameters
        for f in fornecedores
    }

    def _processar_item(idx: int, item, conn):
        """Pipeline COMPLETO de um item (aprendizado → cache → código →
        textual → judge) contra a conexão RECEBIDA (a da thread, no modo
        paralelo). Devolve (bag, erros_item, stats) — nenhum estado
        compartilhado é mutado aqui (thread-safe por construção)."""
        erros_item: list[ErroBusca] = []
        stats = {"cache_hits": 0, "cache_misses": 0, "codigo_hits": 0,
                 "llm_judge_hits": 0}
        bag: list[ResultadoBusca] = []
        desc_norm = normalizar_descricao(item.descricao)

        # Log do item (diagnóstico)
        log_item = None
        if diagnostico is not None:
            log_item = diagnostico.get_item(
                idx=idx,
                descricao=item.descricao or "",
                desc_norm=desc_norm,
                keywords=keywords(item.descricao or ""),
            )

        # Camada 0: descobre o produto canônico UMA vez por item (não
        # por fornecedor). Pode falhar em catálogo vazio ou item sem
        # correspondência — segue normal.
        match = None
        if catalogo:
            try:
                match = melhor_match(
                    item.descricao, catalogo,
                    canonico_edital=canon_itens.get(item.descricao),
                )
            except Exception:
                match = None

        for forn in fornecedores:
            # Log por (item, fornecedor) — preenchido ao longo do loop
            log_forn = None
            if log_item is not None:
                log_forn = LogItemFornecedor(fornecedor=forn.nome)
                log_item.por_fornecedor.append(log_forn)

            # Camada 4 (APRENDIZADO) — consultada ANTES do cache: a escolha
            # que o operador ENSINOU tem precedência sobre qualquer resultado
            # cru de busca/cache. Sem isto, um item repetido dentro da janela
            # de cache (2 dias) voltava com o "melhor match" textual em vez da
            # escolha A do operador — a retroalimentação ficava enjaulada
            # atrás do cache (feedback do cliente 2026-06; é a inteligência
            # central do produto). Casa por descrição exata normalizada
            # (rápido, indexado) ou, se não, por reuso FUZZY (descrição
            # parecida + dimensão compatível). O resultado é recuperado por
            # CÓDIGO (preço ao vivo) logo abaixo.
            codigo_aprendido = None
            if conn is not None and desc_norm:
                try:
                    aprendido = db.aprendido_buscar(conn, desc_norm, forn.nome)
                    if aprendido is None and aprendidos_lista:
                        aprendido = melhor_aprendido_fuzzy(
                            item.descricao, forn.nome, aprendidos_lista)
                    if aprendido is not None:
                        codigo_aprendido = aprendido["codigo_fornecedor"]
                except Exception:
                    codigo_aprendido = None
            if log_forn is not None and codigo_aprendido:
                log_forn.aprendido_codigo = codigo_aprendido

            # 1. Cache de descrição (camada mais barata) — só quando NÃO há
            # escolha aprendida para esta descrição. Item aprendido pula o
            # cache de propósito (acima): a escolha do operador vence o
            # resultado cru. Payload VAZIO é tratado como miss: "não achei"
            # não se distingue de falha transitória — honrar "[]" por 2 dias
            # virava "Não encontrado" persistente na hora do pregão
            # (auditoria 2026-06, achado alto nº 5).
            if (not codigo_aprendido and conn is not None
                    and usar_cache and desc_norm):
                payload = db.cache_buscar(conn, desc_norm, forn.nome)
                encontrados_cache = (
                    _payload_to_resultados(payload, forn.nome)
                    if payload is not None else []
                )
                if encontrados_cache:
                    stats["cache_hits"] += 1
                    if log_forn is not None:
                        log_forn.cache_hit = True
                    bag.extend(encontrados_cache)
                    if log_forn is not None:
                        _registrar_candidatos_finais(
                            log_forn, encontrados_cache, item.descricao,
                            veio_por_codigo=False, aprovado_judge=False,
                        )
                    continue
                stats["cache_misses"] += 1

            # Camada 0: matcher contra catálogo
            if log_forn is not None and match is not None:
                log_forn.catalogo_produto_id = match.produto.produto_id
                log_forn.catalogo_score = float(match.score)
                log_forn.catalogo_codigo = match.codigo_para(forn.nome)

            # 2. Busca por código (determinística): o APRENDIZADO (acima) tem
            # prioridade; senão, o código do catálogo (Camada 0). Só dispara
            # se há código para este fornecedor E o scraper suporta
            # `buscar_por_codigo` (mocks antigos não).
            codigo = codigo_aprendido or (
                match.codigo_para(forn.nome) if match else None
            )
            buscar_codigo = getattr(forn, "buscar_por_codigo", None)
            encontrados: list[ResultadoBusca] = []
            veio_por_codigo = False
            if codigo and callable(buscar_codigo):
                try:
                    encontrados = buscar_codigo(codigo) or []
                except SessaoHTTPExpirou:
                    raise   # paralelo: item volta pela via do navegador
                except Exception as exc:
                    # Falha na busca por código não bloqueia — registra
                    # e cai pra busca textual abaixo.
                    erros_item.append(ErroBusca(
                        indice_item=idx,
                        fornecedor=forn.nome,
                        descricao_resumo=(item.descricao or "")[:60],
                        mensagem=f"buscar_por_codigo({codigo}): "
                                 f"{type(exc).__name__}: {exc}",
                    ))
                    encontrados = []
                # Salvaguarda: o produto trazido pelo código tem que casar
                # com o que esperávamos. Referência = descrição canônica
                # do catálogo (veio do matcher) ou do edital (veio de
                # aprendizado). Se não casa, o código/fornecedor está
                # errado — descarta e cai na busca textual.
                if encontrados:
                    desc_ref = (
                        match.produto.descricao_canonica
                        if (match and not codigo_aprendido)
                        else item.descricao
                    )
                    rel_cod = relevancia(
                        desc_ref, encontrados[0].descricao_encontrada)
                    if rel_cod < _RELEVANCIA_SALVAGUARDA_CODIGO:
                        encontrados = []   # produto sem relação — descarta
                if encontrados:
                    veio_por_codigo = True
                    stats["codigo_hits"] += 1
                    # Origem "aprendizado": a UI pré-seleciona e mostra
                    # "✓ sua escolha anterior" (retroalimentação VISÍVEL).
                    # ResultadoBusca é frozen → recria com a marca de origem.
                    if codigo_aprendido:
                        encontrados = [
                            replace(r, veio_por_aprendizado=True)
                            for r in encontrados
                        ]
                    if log_forn is not None:
                        log_forn.veio_por_codigo = True

            # 3. Fallback: busca textual (caminho atual / Camadas 1+2+3
            # internas do scraper).
            if not encontrados:
                try:
                    _kw = {}
                    if suporta_log.get(forn.nome) and log_forn is not None:
                        _kw["_log"] = log_forn
                    if suporta_idx.get(forn.nome) and idx_codigo:
                        _kw["idx_codigo"] = idx_codigo
                    encontrados = forn.buscar(item.descricao, **_kw)
                except SessaoHTTPExpirou:
                    raise   # paralelo: item volta pela via do navegador
                except Exception as exc:
                    erros_item.append(ErroBusca(
                        indice_item=idx,
                        fornecedor=forn.nome,
                        descricao_resumo=(item.descricao or "")[:60],
                        mensagem=f"{type(exc).__name__}: {exc}",
                    ))
                    continue

            bag.extend(encontrados)
            if log_forn is not None:
                _registrar_candidatos_finais(
                    log_forn, encontrados, item.descricao,
                    veio_por_codigo=veio_por_codigo, aprovado_judge=False,
                )

            # 4. Grava no cache — exceto quando veio por código ou
            # quando NÃO achou nada. Cache por descrição não casa bem
            # com resultado por código (vários itens do edital com
            # descrições diferentes podem apontar pro mesmo produto).
            # E resultado VAZIO nunca entra no cache: um logout/soluço
            # do site no meio do lote viraria "Não encontrado" por 2
            # dias (auditoria 2026-06, achado alto nº 5). O custo é
            # re-buscar os legitimamente inexistentes a cada rodada —
            # aceitável e mais seguro.
            if (conn is not None and usar_cache and desc_norm
                    and encontrados and not veio_por_codigo):
                payload = json.dumps(
                    [asdict(r) for r in encontrados], ensure_ascii=False
                )
                db.cache_gravar(conn, desc_norm, forn.nome, payload)

        # Camada 3: LLM judge sob demanda.
        # Dispara só quando o item caiu no fallback "top 3 variação"
        # (sinal de falha das Camadas 1+2 textuais). Não dispara se
        # já há resultado 'exato' — sinal de match confiável.
        todos_variacao = bool(bag) and all(
            r.confianca == "variacao" for r in bag
        )
        if usar_llm_judge and todos_variacao:
            judge_n_entrada = len(bag)
            bag_judged = _aplicar_llm_judge(item.descricao, bag, conn)
            if log_item is not None:
                # Marca em todos os logs por_fornecedor que houve julgamento
                for lf in log_item.por_fornecedor:
                    lf.judge_chamado = True
                    lf.judge_n_candidatos_entrada = judge_n_entrada
            if bag_judged is not None:
                bag = bag_judged
                stats["llm_judge_hits"] += 1
                if log_item is not None:
                    # Reescreve candidatos_finais marcando aprovados
                    _marcar_aprovados_judge(log_item, bag_judged, item.descricao)



        return bag, erros_item, stats

    def _absorver(idx: int, bag, erros_item, stats) -> None:
        nonlocal cache_hits, cache_misses, codigo_hits, llm_judge_hits
        resultados[idx] = bag
        erros.extend(erros_item)
        cache_hits += stats["cache_hits"]
        cache_misses += stats["cache_misses"]
        codigo_hits += stats["codigo_hits"]
        llm_judge_hits += stats["llm_judge_hits"]

    # Quantos itens processar EM PARALELO. Serial (1) quando: diagnóstico
    # ligado (objeto compartilhado), algum fornecedor sem transporte HTTP
    # (o fallback tocaria o Playwright fora da thread dona → greenlet), ou
    # max_workers=1. Fakes de teste não têm _http_cookie → caminho serial
    # IDÊNTICO ao histórico (zero regressão por construção).
    workers = 1
    if diagnostico is None and fornecedores and all(
            getattr(f, "_http_cookie", None) for f in fornecedores):
        workers = max(1, min(max_workers or config.SCRAPER_POOL_SIZE or 1,
                             total))

    try:
        if workers <= 1:
            for idx, item in enumerate(itens):
                bag, erros_item, stats = _processar_item(idx, item, conn)
                _absorver(idx, bag, erros_item, stats)
                # Commit incremental: uma interrupção no meio do lote não
                # descarta o cache dos itens já buscados (WAL = commit barato).
                if conn is not None:
                    conn.commit()
                if progresso_callback is not None:
                    progresso_callback(idx + 1, total,
                                       (item.descricao or "")[:60])
        else:
            import threading
            from concurrent.futures import ThreadPoolExecutor, as_completed

            # Conexão SQLite POR THREAD (sqlite3 proíbe cruzar threads; WAL
            # serializa as escritas). Fechadas no finally.
            _local = threading.local()
            _conns_extra: list = []
            _lk = threading.Lock()

            def _conn_da_thread():
                c = getattr(_local, "conn", None)
                if c is None and (usar_cache or usar_catalogo):
                    c = db.conectar()
                    _local.conn = c
                    with _lk:
                        _conns_extra.append(c)
                return c

            def _no_worker(idx: int, item):
                c = _conn_da_thread()
                try:
                    bag, erros_item, stats = _processar_item(idx, item, c)
                    if c is not None:
                        c.commit()
                    return ("ok", idx, item, bag, erros_item, stats)
                except SessaoHTTPExpirou:
                    # Sessão HTTP caiu p/ este item: volta depois, SERIAL,
                    # pela via do navegador (fallback preservado).
                    return ("navegador", idx, item, None, None, None)

            # Workers só usam HTTP — nunca tocam o Playwright de outra thread.
            _http_only_antes = {f.nome: getattr(f, "_http_only", False)
                                for f in fornecedores}
            for f in fornecedores:
                f._http_only = True
            pendentes_navegador: list = []
            feitos = 0
            try:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    futs = [ex.submit(_no_worker, idx, item)
                            for idx, item in enumerate(itens)]
                    for fut in as_completed(futs):
                        status, idx, item, bag, erros_item, stats = fut.result()
                        if status == "navegador":
                            pendentes_navegador.append((idx, item))
                            continue
                        _absorver(idx, bag, erros_item, stats)
                        feitos += 1
                        if progresso_callback is not None:
                            # No fio de quem chamou (as_completed roda aqui):
                            # seguro para a UI (Streamlit não é thread-safe).
                            progresso_callback(feitos, total,
                                               (item.descricao or "")[:60])
            finally:
                for f in fornecedores:
                    f._http_only = _http_only_antes.get(f.nome, False)
                for c in _conns_extra:
                    try:
                        c.commit()
                        c.close()
                    except Exception:
                        pass

            # 2ª passada: itens cuja sessão HTTP expirou — SERIAL, na thread
            # dona do Playwright, com o fallback de navegador liberado.
            for idx, item in pendentes_navegador:
                bag, erros_item, stats = _processar_item(idx, item, conn)
                _absorver(idx, bag, erros_item, stats)
                if conn is not None:
                    conn.commit()
                feitos += 1
                if progresso_callback is not None:
                    progresso_callback(feitos, total,
                                       (item.descricao or "")[:60])

        if conn is not None:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()

    return ResultadoLote(
        resultados=resultados,
        erros=erros,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        codigo_hits=codigo_hits,
        llm_judge_hits=llm_judge_hits,
    )


def _registrar_candidatos_finais(
    log_forn: "LogItemFornecedor",
    candidatos: list[ResultadoBusca],
    descricao_original: str,
    *,
    veio_por_codigo: bool,
    aprovado_judge: bool,
) -> None:
    """Anexa cada `ResultadoBusca` ao log do fornecedor, calculando a
    relevância contra a descrição original. Usado pelo diagnóstico."""
    for r in candidatos:
        rel = relevancia(descricao_original, r.descricao_encontrada)
        log_forn.candidatos_finais.append(LogCandidatoFinal(
            descricao_encontrada=r.descricao_encontrada,
            marca=r.marca,
            preco=r.preco,
            codigo_fornecedor=r.codigo_fornecedor,
            fornecedor=r.fornecedor,
            confianca=r.confianca,
            relevancia_vs_original=rel,
            veio_por_codigo=veio_por_codigo,
            aprovado_judge=aprovado_judge,
        ))


def _marcar_aprovados_judge(
    log_item, bag_judged: list[ResultadoBusca], descricao_original: str,
) -> None:
    """Após o Gemini judge, reescreve os candidatos_finais de cada
    fornecedor mantendo só os aprovados. Usado pelo diagnóstico."""
    # Index: (fornecedor, codigo, preco) → aprovado?
    aprovados = {
        (r.fornecedor, r.codigo_fornecedor, r.preco) for r in bag_judged
    }
    for lf in log_item.por_fornecedor:
        lf.judge_n_aprovados = sum(
            1 for c in lf.candidatos_finais
            if (c.fornecedor, c.codigo_fornecedor, c.preco) in aprovados
        )
        # Reescreve candidatos_finais marcando aprovados (descarta os rejeitados)
        nova_lista = []
        for c in lf.candidatos_finais:
            if (c.fornecedor, c.codigo_fornecedor, c.preco) in aprovados:
                # cria cópia com aprovado_judge=True
                nova_lista.append(LogCandidatoFinal(
                    descricao_encontrada=c.descricao_encontrada,
                    marca=c.marca, preco=c.preco,
                    codigo_fornecedor=c.codigo_fornecedor,
                    fornecedor=c.fornecedor,
                    confianca=c.confianca,
                    relevancia_vs_original=c.relevancia_vs_original,
                    veio_por_codigo=c.veio_por_codigo,
                    aprovado_judge=True,
                ))
        lf.candidatos_finais = nova_lista


def _invalidar_cache_se_versao_mudou(conn) -> None:
    """Compara a versão hardcoded (`BUSCA_VERSAO`) com a salva em
    `data/.busca_versao`. Se diferentes, apaga `precos_cache_descricao`
    para evitar servir resultados de algoritmo antigo.

    Falha silenciosa — cache não é essencial; pior caso, sobreviveu cache
    velho até o próximo run."""
    try:
        arq = config.DATA_DIR / ".busca_versao"
        atual = arq.read_text().strip() if arq.exists() else ""
        if atual != BUSCA_VERSAO:
            n = db.cache_limpar(conn)
            arq.write_text(BUSCA_VERSAO)
            if n:
                from .logconf import get_logger
                get_logger("scraper").info(
                    "Cache invalidado: %d entradas (versão de busca mudou).", n)
    except Exception:
        from .logconf import get_logger
        get_logger("scraper").exception("falha ao invalidar cache por versão")


def _aplicar_llm_judge(
    descricao: str,
    bag: list[ResultadoBusca],
    conn,
) -> Optional[list[ResultadoBusca]]:
    """Reordena/filtra os candidatos via Gemini judge. Retorna a nova lista
    ou None se o judge não opinou (sem chave, sem rede, etc).

    Política conservadora:
    - Se LLM retorna índices: mantém apenas esses, em ordem do LLM.
    - Se LLM retorna []: mantém a lista original (não rejeita silenciosamente
      — o operador ainda pode escolher).

    Import tardio do judge: evita carregar `google.genai` quando o
    pipeline não vai usar.
    """
    if not bag:
        return None
    try:
        from .extrator_llm import julgar_candidatos
    except ImportError:
        return None
    indices = julgar_candidatos(descricao, bag, conn=conn)
    if not indices:
        return None
    # Reordena conforme o LLM e marca como 'variacao' (operador confere)
    return [bag[i] for i in indices if 0 <= i < len(bag)]


def cotar_por_codigo(fornecedor: str, codigo: str) -> Optional[ResultadoBusca]:
    """Cotação AO VIVO de um produto pelo código, no site do fornecedor.

    Usada pela "busca no meu catálogo": o catálogo fornece o CÓDIGO, mas o
    PREÇO tem que ser o ATUAL do site — preços mudam todo dia, então o preço
    histórico guardado NUNCA pode ser usado na planilha (requisito do cliente,
    2026-06).

    Contrato (proposital, para o chamador não cair em preço velho):
    - Retorna o ``ResultadoBusca`` com o preço atual quando o site devolve o
      produto.
    - Retorna ``None`` quando o site não devolve nada para o código (produto
      fora de linha / código mudou) — fato definitivo.
    - **Levanta exceção** se o login/navegador/rede falhar — é transitório, e
      o chamador deve avisar e NÃO usar o preço do catálogo.
    """
    classes = {"almeida": AlmeidaDistribuidor, "dantas": DantasDistribuidora}
    cls = classes.get(fornecedor)
    cred = credenciais.carregar(fornecedor)
    if not cls:
        raise ValueError(f"Fornecedor desconhecido: {fornecedor!r}")
    if not cred:
        raise RuntimeError(f"Sem credencial configurada para {fornecedor}.")
    with cls(cred["email"], cred["senha"]) as forn:
        achados = forn.buscar_por_codigo(str(codigo).strip())
    return achados[0] if achados else None


# === Sessões manuais REUTILIZÁVEIS (busca "Buscar no site ao vivo") ===
# Antes, cada clique LOGAVA do zero (~15-20s de login). Aqui mantemos UMA sessão
# logada por fornecedor e a REUSAMOS entre buscas: login só na 1ª; as seguintes
# vão direto (~2-3s). Cache módulo-level — o app é LOCAL e single-user. NÃO afeta
# a busca em LOTE (que tem seu próprio ciclo `with`). Em QUALQUER falha de reuso
# (sessão expirada, cookie caiu, thread trocada) caímos para um login fresco —
# pior caso = o comportamento de antes (otimização de UX 2026-06-27).
_FORNECEDORES_VALIDOS = ("almeida", "dantas")
_sessoes_vivas: dict = {}


def _abrir_sessao_viva(fornecedor: str):
    """Cria e LOGA uma sessão (ciclo manual, sem `with`). Levanta se faltar
    credencial ou o login falhar (o __enter__ já limpa o navegador nesse caso)."""
    # Resolve a classe em tempo de CHAMADA (não no import) para respeitar o
    # monkeypatch dos testes que substitui Almeida/Dantas.
    classes = {"almeida": AlmeidaDistribuidor, "dantas": DantasDistribuidora}
    cls = classes.get(fornecedor)
    if cls is None:
        raise ValueError(f"Fornecedor desconhecido: {fornecedor!r}")
    cred = credenciais.carregar(fornecedor)
    if not cred:
        raise RuntimeError(f"Sem credencial configurada para {fornecedor}.")
    forn = cls(cred["email"], cred["senha"])
    forn.__enter__()   # login (sobe o navegador)
    return forn


def _fechar_sessao_viva(fornecedor: str) -> None:
    forn = _sessoes_vivas.pop(fornecedor, None)
    if forn is not None:
        try:
            forn.__exit__(None, None, None)
        except Exception:
            pass


def fechar_sessao_viva(fornecedor: str) -> None:
    """Descarta a sessão em cache de UM fornecedor (ex.: após falha grave no
    lote — o próximo uso reloga fresco)."""
    _fechar_sessao_viva(fornecedor)


def fechar_sessoes_vivas() -> None:
    """Fecha TODAS as sessões manuais em cache. Chamar ao iniciar um novo pregão
    para não deixar um navegador aberto à toa."""
    for fornecedor in list(_sessoes_vivas):
        _fechar_sessao_viva(fornecedor)


def obter_sessao_viva(fornecedor: str):
    """Devolve a sessão LOGADA reutilizável do cache — loga só se preciso.

    Perf (campo 2026-07): a busca em LOTE abria navegador + login + condição de
    pagamento A CADA clique (~30-60s de overhead por busca, e consumia o limite
    de logins do site — que bloqueou a conta em 2026-06-27). Agora lote e busca
    manual compartilham a MESMA sessão viva: login/pagamento ~1× por sessão de
    trabalho.

    Valida com um ping barato antes de devolver; sessão morta → reloga. A
    sessão devolvida NÃO deve ser fechada pelo chamador (o ciclo de vida é do
    cache: `fechar_sessoes_vivas` no novo pregão / `fechar_sessao_viva` em
    falha grave). Levanta se faltar credencial ou o login falhar."""
    if fornecedor not in _FORNECEDORES_VALIDOS:
        raise ValueError(f"Fornecedor desconhecido: {fornecedor!r}")
    forn = _sessoes_vivas.get(fornecedor)
    if forn is not None:
        try:
            forn._page.evaluate("() => 1")   # página responde?
            return forn
        except Exception:
            _logger.info("sessão de %s caiu — relogando", fornecedor)
            _fechar_sessao_viva(fornecedor)
    forn = _abrir_sessao_viva(fornecedor)
    _sessoes_vivas[fornecedor] = forn
    return forn


def buscar_no_site(
    fornecedor: str, termo: str, limite: int = 10
) -> list[ResultadoBusca]:
    """Busca MANUAL ao vivo no site do fornecedor, por NOME ou CÓDIGO.

    Rede de segurança final (pedido do cliente 2026-06): quando nem a busca
    automática nem o catálogo trazem o que o operador espera, ele digita o termo
    e busca direto no site, escolhendo o fornecedor.

    Diferente da busca automática (`buscar`), NÃO aplica o filtro de relevância:
    devolve os cards CRUS do site (até `limite`) para o operador julgar. O preço
    é o ATUAL do site.

    PERF: REUSA a sessão logada entre buscas (ver bloco acima) — login só na 1ª.

    Contrato de falhas:
    - Retorna a lista (possivelmente vazia) quando o site responde.
    - **Levanta exceção** se login/navegador/rede falhar — o chamador avisa e o
      operador re-tenta.
    """
    termo = (termo or "").strip()
    if not termo:
        return []
    if fornecedor not in _FORNECEDORES_VALIDOS:
        raise ValueError(f"Fornecedor desconhecido: {fornecedor!r}")

    def _consultar(forn):
        forn._rate_limiter.acquire()
        # confiança 'variacao' — match manual, não verificado por texto.
        return forn._buscar_query(forn._page, termo, "variacao")

    # 1) REUSA a sessão logada, se houver — é o ganho de velocidade.
    forn = _sessoes_vivas.get(fornecedor)
    if forn is not None:
        tinha_http = bool(getattr(forn, "_http_cookie", None))
        # Sessão reusada SEM cookie HTTP está DEGRADADA para a busca manual: o
        # lote automático pode tê-lo zerado ao ver deslogado, ou o login não o
        # capturou. Como o diagnóstico provou que uma sessão FRESCA acha por HTTP,
        # relogamos para garantir uma sessão logada e HTTP-capaz — em vez de cair
        # no navegador de uma sessão possivelmente deslogada (que voltava VAZIO
        # sem sinal nenhum: era o bug do "2º leilão" após trocar de pregão,
        # 2026-07-07). Só quando o transporte HTTP está ligado.
        if not tinha_http and config.SCRAPER_HTTP_QUERIES:
            _logger.info("busca manual em %s: sessão reusada sem cookie HTTP — "
                         "relogando", fornecedor)
            _fechar_sessao_viva(fornecedor)
        else:
            try:
                achados = _consultar(forn)[:max(1, limite)]
                # RELOGA só quando a sessão comprovadamente DESLOGOU — NÃO por "0
                # resultados" logado (busca legítima que o site não achou). A
                # guarda de `_consultar_cards_http` ZERA `_http_cookie` ao ver a
                # página de login OU cards com preço 0,00 (sinais medidos em campo
                # 2026-07-07). Separa os três casos que ANTES viravam o mesmo "vazio":
                #   • logado, COM resultado  → achados != []              → devolve
                #   • logado, SEM resultado  → achados == [], cookie ok   → devolve []
                #   • DESLOGADO              → cookie zerado → reloga 1x
                # O gate importa: relogar em TODA busca vazia (fix v1.0.40) queimava
                # login à toa — e excesso de logins BLOCKED a conta em 2026-06-27.
                deslogou = tinha_http and getattr(forn, "_http_cookie", None) is None
                _logger.info(
                    "busca manual %s termo=%r: sessão reusada, %d resultado(s)%s",
                    fornecedor, termo, len(achados),
                    " — deslogou, relogando" if deslogou else "")
                if not deslogou:
                    return achados
                _fechar_sessao_viva(fornecedor)
            except Exception:
                _logger.info("sessão manual de %s caiu — relogando", fornecedor)
                _fechar_sessao_viva(fornecedor)

    # 2) Sem cache (ou caiu, ou deslogou, ou degradada): loga FRESCO, cacheia e
    #    busca. Se a busca já falhar na sessão nova, descarta (não deixa sessão
    #    quebrada no cache) e propaga. Um vazio AQUI é definitivo (nova, logada).
    forn = _abrir_sessao_viva(fornecedor)
    _sessoes_vivas[fornecedor] = forn
    try:
        achados = _consultar(forn)[:max(1, limite)]
        _logger.info("busca manual %s termo=%r: sessão FRESCA, %d resultado(s)",
                     fornecedor, termo, len(achados))
        return achados
    except Exception:
        _fechar_sessao_viva(fornecedor)
        raise


# === Fase 4: NCM sob demanda (o NCM mora só na página de DETALHE) ===

RE_NCM_HTML = re.compile(r"C[óo]digo NCM[:\s]*([\d.]{4,})")


def _extrair_ncm_html(html: str, codigo) -> "tuple[str, str | None]":
    """Classifica a página de detalhe baixada por HTTP:
    - ("ok", ncm)   → NCM achado (definitivo)
    - ("ok", None)  → é a página do PRODUTO (código confere) mas SEM NCM
                      (definitivo — igual ao navegador visitando e não achando)
    - ("erro", None)→ ambíguo/quebrado → o navegador decide (fallback)."""
    m = RE_NCM_HTML.search(html)
    if m:
        return ("ok", tributacao.normalizar_ncm(m.group(1)))
    cod = str(codigo or "").strip()
    if cod and f"digo: {cod}" in html:   # "Código: NNNN" da página de produto
        return ("ok", None)
    return ("erro", None)


def consultar_ncms(pedidos, *, progresso=None) -> dict:
    """Busca o NCM dos produtos ESCOLHIDOS — rápido e sem login (2026-07).

    O NCM mora na página de DETALHE do produto (o card de busca não traz) e é
    PÚBLICO (verificado nos 2 fornecedores) e imutável por sessão/pagamento.
    Pipeline em 3 camadas, espelho da busca de preços:

    1. **Cache durável** (``ncm_cache`` por fornecedor+código): produto já
       consultado em QUALQUER pregão responde instantâneo, para sempre.
    2. **HTTP em paralelo** (pool de threads, sem login): GET da página de
       detalhe + extração do "Código NCM". Resultado definitivo entra no cache.
    3. **Fallback navegador** (serial, sessão logada REUSADA via
       ``obter_sessao_viva``) só para o que o HTTP não resolveu.

    Args:
        pedidos: lista de ``(chave, fornecedor, url)`` ou
            ``(chave, fornecedor, url, codigo)`` — o código habilita o cache
            durável (tuplas antigas seguem funcionando, só sem cache).
        progresso: callback ``(feitos, total)`` — SEMPRE na thread chamadora.

    Retorna ``(ncms, falhas)`` com o MESMO contrato de antes:
    - ``ncms[chave] = ncm|None`` só para resultados DEFINITIVOS (cacheáveis);
    - ``falhas`` = transitórias (rede/login) — re-tentáveis, nunca cacheadas.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    norm = []
    for p in pedidos:
        chave, forn, url = p[0], p[1], p[2]
        codigo = p[3] if len(p) > 3 else None
        if url:
            norm.append((chave, forn, url, codigo))

    total = len(norm)
    feitos = 0
    ncms: dict = {}
    falhas: set = set()

    def _avancar():
        nonlocal feitos
        feitos += 1
        if progresso:
            progresso(feitos, total)

    conn = None
    try:
        try:
            conn = db.conectar()
        except Exception:
            conn = None   # sem banco → sem cache durável; o resto funciona

        # 1) Cache durável (thread principal — barato).
        restantes = []
        for chave, forn, url, codigo in norm:
            hit = None
            if conn is not None and codigo:
                try:
                    hit = db.ncm_cache_buscar(conn, forn, codigo)
                except Exception:
                    hit = None
            if hit is not None:
                ncms[chave] = hit[1]
                _avancar()
            else:
                restantes.append((chave, forn, url, codigo))

        # 2) HTTP sem login, em paralelo (thread-safe; educado com o site).
        pendentes_navegador = []

        def _via_http(item):
            chave, forn, url, codigo = item
            time.sleep(0.3)
            try:
                html = _http_get(url, "")
            except Exception:
                return (item, "erro", None)
            status, ncm = _extrair_ncm_html(html, codigo)
            return (item, status, ncm)

        if restantes:
            with ThreadPoolExecutor(
                    max_workers=min(config.SCRAPER_POOL_SIZE or 1,
                                    len(restantes))) as ex:
                futs = [ex.submit(_via_http, it) for it in restantes]
                for fut in as_completed(futs):
                    item, status, ncm = fut.result()
                    chave, forn, url, codigo = item
                    if status == "ok":
                        ncms[chave] = ncm
                        if conn is not None and codigo:
                            try:
                                db.ncm_cache_gravar(conn, forn, codigo, ncm)
                            except Exception:
                                pass
                        _avancar()
                    else:
                        pendentes_navegador.append(item)

        # 3) Fallback navegador — SERIAL na thread dona do Playwright, com a
        # sessão logada REUSADA (não abre/fecha navegador por clique).
        por_forn: dict = {}
        for item in pendentes_navegador:
            por_forn.setdefault(item[1], []).append(item)
        for forn_nome, lista in por_forn.items():
            try:
                forn = obter_sessao_viva(forn_nome)
            except Exception:
                for chave, *_ in lista:
                    falhas.add(chave)
                    _avancar()
                continue
            for chave, _f, url, codigo in lista:
                try:
                    forn._page.goto(url, wait_until="networkidle",
                                    timeout=config.SCRAPER_TIMEOUT_SEGUNDOS * 1000)
                    forn._page.wait_for_timeout(500)
                    txt = forn._page.evaluate("() => document.body.innerText")
                    ncm = tributacao.ncm_de_texto(txt)   # str|None definitivo
                    ncms[chave] = ncm
                    if conn is not None and codigo:
                        try:
                            db.ncm_cache_gravar(conn, forn_nome, codigo, ncm)
                        except Exception:
                            pass
                except Exception:
                    falhas.add(chave)            # transitório — re-tentável
                _avancar()

        if conn is not None:
            try:
                conn.commit()
            except Exception:
                pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return ncms, falhas
