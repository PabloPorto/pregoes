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
import time
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
    """Rate limiter serial — intervalo mínimo entre requests.

    Não precisa de lock porque o scraper é serial agora (commit ____).
    Mantém o uso para não sobrecarregar os sites parceiros: guarda
    timestamp da última chamada e dorme se a próxima vem cedo demais.
    """

    def __init__(self, max_req_por_segundo: float):
        self._min_intervalo = 1.0 / max_req_por_segundo if max_req_por_segundo > 0 else 0.0
        self._ultimo = 0.0

    def acquire(self) -> None:
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


def _aplicar_preferencia_pagamento(page, fornecedor: str) -> None:
    """Aplica a condição de pagamento configurada para `fornecedor` na sessão.

    Degrada graciosamente: sem preferência configurada, ou se o modal/select não
    aparecer, NÃO mexe — segue no default do site (comportamento anterior, zero
    regressão). NUNCA levanta exceção (não pode derrubar o login)."""
    pref = credenciais.carregar_preferencia_pagamento(fornecedor)
    if not pref or not pref.get("forma_value") or not pref.get("prazo_value"):
        return
    base_url = config.FORNECEDORES.get(fornecedor)
    if not base_url:
        return
    try:
        page.goto(base_url, wait_until="networkidle",
                  timeout=config.SCRAPER_TIMEOUT_SEGUNDOS * 1000)
        if not _abrir_modal_preferencias(page):
            _logger.warning(
                "Condição de pagamento (%s): não abri o modal de preferências — "
                "seguindo no default do site.", fornecedor)
            return
        _select_visivel(page, _SEL_FORMA, pref["forma_value"], pref.get("forma_texto"))
        # A forma re-renderiza o <select> de Condição/Prazo via JS (mostra/oculta
        # e popula) de forma ASSÍNCRONA. Esperamos o re-render ASSENTAR e só então
        # checamos a visibilidade ATUAL: o prazo só existe para boleto/cartão; PIX
        # e PAGAMENTO ANTECIPADO o ocultam. Aplicar prazo num select que vai ser
        # ocultado travava ~60s e voltava preço no default (achado de campo
        # 2026-06: forma PIX não tem prazo).
        page.wait_for_timeout(1_200)
        tem_prazo = bool(pref.get("prazo_value")
                         and page.locator(f"{_SEL_PRAZO}:visible").count())
        if tem_prazo:
            _select_visivel(page, _SEL_PRAZO, pref["prazo_value"], pref.get("prazo_texto"))
        botao = page.get_by_role("button", name="Confirmar")
        if not botao.count():
            botao = page.get_by_text("Confirmar", exact=False)
        botao.first.click(timeout=5_000)
        page.wait_for_timeout(2_500)   # sessão atualiza (PHPSESSID/token)
        _logger.info("Condição de pagamento aplicada (%s): %s | %s", fornecedor,
                     pref.get("forma_texto"),
                     pref.get("prazo_texto") if tem_prazo else "(forma sem prazo)")
    except Exception as exc:
        _logger.warning(
            "Não consegui aplicar a condição de pagamento em %s (%s) — seguindo "
            "no default do site.", fornecedor, type(exc).__name__)


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
            self._playwright = sync_playwright().start()
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
            _aplicar_preferencia_pagamento(self._page, self.nome)
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
            if self._playwright is not None:
                self._playwright.stop()
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
        """Faz uma busca crua usando a página recebida e retorna até N
        resultados parseados (sem filtro de relevância — caller filtra)."""
        url = f"{_BASE_URL_ALMEIDA}/busca?s={quote_plus(query)}"
        page.goto(url, wait_until="networkidle", timeout=config.SCRAPER_TIMEOUT_SEGUNDOS * 1000)
        page.wait_for_timeout(1_500)

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
        return out


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
        self._rate_limiter = _RateLimiter(
            max_req_por_segundo or config.SCRAPER_MAX_REQ_POR_SEGUNDO
        )

    def __enter__(self) -> "DantasDistribuidora":
        from playwright.sync_api import sync_playwright

        # Mesma proteção do Almeida: limpa Chromium/driver se o login
        # falhar, senão sobram processos órfãos a cada falha (ver comentário
        # detalhado em AlmeidaDistribuidor.__enter__).
        try:
            self._playwright = sync_playwright().start()
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
            _aplicar_preferencia_pagamento(self._page, self.nome)
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
            if self._playwright is not None:
                self._playwright.stop()
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
        page.goto(url, wait_until="networkidle", timeout=config.SCRAPER_TIMEOUT_SEGUNDOS * 1000)
        page.wait_for_timeout(1_500)

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
        return out


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
    max_workers: Optional[int] = None,  # aceito mas ignorado (compat. legacy)
) -> ResultadoLote:
    """Busca em batch SERIAL com cache TTL=2d, Camada 0 (catálogo) e
    coleta de erros.

    Pipeline por item × fornecedor (Q12):
    1. Cache TTL=2d em precos_cache_descricao → hit reaproveita
    2. Camada 0: matcher fuzzy contra catálogo histórico → busca por código
    3. Caminho atual: busca textual em N camadas (descrição/keywords)
    4. Aprendizado (Camada 4) — não aqui; consultado no orquestrador da UI

    Por que serial: Playwright sync API atrela o browser à thread que
    chamou `sync_playwright().start()`. Tentar paralelizar com
    ThreadPoolExecutor gera `greenlet.error: Cannot switch to a different
    thread` (visto em produção em 2026-05-27). Para retomar paralelismo,
    refatorar para `playwright.async_api`.

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
        max_workers: ignorado (mantido na assinatura para compat. com
            chamadores antigos que ainda passam o argumento).
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

    try:
        for idx, item in enumerate(itens):
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
                        cache_hits += 1
                        if log_forn is not None:
                            log_forn.cache_hit = True
                        bag.extend(encontrados_cache)
                        if log_forn is not None:
                            _registrar_candidatos_finais(
                                log_forn, encontrados_cache, item.descricao,
                                veio_por_codigo=False, aprovado_judge=False,
                            )
                        continue
                    cache_misses += 1

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
                    except Exception as exc:
                        # Falha na busca por código não bloqueia — registra
                        # e cai pra busca textual abaixo.
                        erros.append(ErroBusca(
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
                        codigo_hits += 1
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
                    except Exception as exc:
                        erros.append(ErroBusca(
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
                    llm_judge_hits += 1
                    if log_item is not None:
                        # Reescreve candidatos_finais marcando aprovados
                        _marcar_aprovados_judge(log_item, bag_judged, item.descricao)

            resultados[idx] = bag

            # Commit incremental: persiste já o cache deste item. Sem isto, uma
            # interrupção no meio do lote (operador fecha a janela, queda de
            # rede, timeout) descartaria TODAS as buscas da rodada — meia hora
            # de trabalho perdida. Com WAL o custo por commit é baixo; o ganho
            # é a re-busca retomar do cache de onde parou.
            if conn is not None:
                conn.commit()

            if progresso_callback is not None:
                progresso_callback(idx + 1, total, (item.descricao or "")[:60])

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


def buscar_no_site(
    fornecedor: str, termo: str, limite: int = 10
) -> list[ResultadoBusca]:
    """Busca MANUAL ao vivo no site do fornecedor, por NOME ou CÓDIGO.

    Rede de segurança final (pedido do cliente 2026-06): quando nem a busca
    automática nem o catálogo trazem o que o operador espera, ele digita o termo
    e busca direto no site, escolhendo o fornecedor. Também é a mitigação para
    casos em que o filtro automático erra (ex.: alicate).

    Diferente da busca automática (`buscar`), NÃO aplica o filtro de relevância:
    devolve os cards CRUS do site (até `limite`) para o operador julgar — é o
    ponto: ver exatamente o que o site retorna. O preço é o ATUAL do site.

    Contrato de falhas idêntico a `cotar_por_codigo`:
    - Retorna a lista (possivelmente vazia) quando o site responde.
    - **Levanta exceção** se login/navegador/rede falhar (transitório) — o
      chamador avisa e o operador re-tenta.
    """
    termo = (termo or "").strip()
    if not termo:
        return []
    classes = {"almeida": AlmeidaDistribuidor, "dantas": DantasDistribuidora}
    cls = classes.get(fornecedor)
    if not cls:
        raise ValueError(f"Fornecedor desconhecido: {fornecedor!r}")
    cred = credenciais.carregar(fornecedor)
    if not cred:
        raise RuntimeError(f"Sem credencial configurada para {fornecedor}.")
    with cls(cred["email"], cred["senha"]) as forn:
        forn._rate_limiter.acquire()
        # confiança 'variacao' — é match manual, não verificado por texto.
        brutos = forn._buscar_query(forn._page, termo, "variacao")
    return brutos[:max(1, limite)]


# === Fase 4: NCM sob demanda (o NCM mora só na página de DETALHE) ===

def consultar_ncms(pedidos, *, progresso=None) -> dict:
    """Busca o NCM dos produtos ESCOLHIDOS, visitando a página de detalhe.

    O card de busca não traz o NCM (confirmado em Almeida e Dantas) — ele está
    só na página do produto (``Código NCM: ...``). Aqui, para os itens que o
    operador escolheu, agrupamos por fornecedor, logamos UMA vez em cada e
    visitamos a URL de cada produto, extraindo o NCM com
    ``tributacao.ncm_de_texto``.

    Args:
        pedidos: lista de ``(chave, fornecedor, url)`` dos produtos escolhidos.
        progresso: callback opcional ``(feitos, total)`` para barra na UI.

    Retorna ``(ncms, falhas)``:
    - ``ncms``: ``{chave: ncm|None}`` só dos itens REALMENTE visitados. ``None``
      aqui é definitivo ("visitei a página e ela não traz NCM") — seguro de
      cachear.
    - ``falhas``: conjunto de chaves que NÃO puderam ser consultadas (login do
      fornecedor falhou, navegador não subiu, ou o ``goto`` deu timeout). São
      transitórias — o chamador NÃO deve cacheá-las e deve permitir re-tentar.

    Separar os dois é o que evita o "cache negativo": antes, uma falha de login
    virava ``None`` indistinguível de "produto sem NCM", o operador não sabia
    por que nada foi classificado e o re-clique não re-tentava (auditoria
    2026-06, mesmo padrão do cache de busca / bloco 3).
    """
    from collections import defaultdict

    por_forn = defaultdict(list)
    for chave, forn, url in pedidos:
        if url:
            por_forn[forn].append((chave, url))

    classes = {"almeida": AlmeidaDistribuidor, "dantas": DantasDistribuidora}
    total = sum(len(v) for v in por_forn.values())
    feitos = 0
    ncms: dict = {}
    falhas: set = set()

    def _avancar():
        nonlocal feitos
        feitos += 1
        if progresso:
            progresso(feitos, total)

    for forn, lista in por_forn.items():
        cred = credenciais.carregar(forn)
        cls = classes.get(forn)
        if not cred or not cls:
            for chave, _ in lista:
                falhas.add(chave)
                _avancar()
            continue
        try:
            with cls(cred["email"], cred["senha"]) as scraper:
                for chave, url in lista:
                    try:
                        scraper._page.goto(url, wait_until="networkidle",
                                           timeout=config.SCRAPER_TIMEOUT_SEGUNDOS * 1000)
                        scraper._page.wait_for_timeout(500)
                        txt = scraper._page.evaluate("() => document.body.innerText")
                        ncms[chave] = tributacao.ncm_de_texto(txt)  # str|None definitivo
                    except Exception:
                        falhas.add(chave)            # transitório — re-tentável
                    _avancar()
        except Exception:
            # Login/navegador falhou para este fornecedor — todos ficam pendentes.
            for chave, _ in lista:
                falhas.add(chave)
                _avancar()
    return ncms, falhas
