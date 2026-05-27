"""
Scraper de fornecedores: Almeida + Dantas.

Decisões registradas (CLAUDE.md Seção 7, QUESTOES_PENDENTES.md Bloco 2):
- Sites parceiros, sem CAPTCHA visível, scraping autorizado.
- Login obrigatório no Almeida para ver preço (campo CNPJ só no 1º acesso).
- Variação de nome → confianca='variacao' (UI marca amarelo, não bloqueia).
- Sistema NUNCA decide marca: a UI lista os resultados e o operador escolhe.

Estratégia de busca em 3 camadas por fornecedor:
1. **exata**       — descrição como veio do edital
2. **variacao**    — descrição normalizada (acentos, frações, prefixo `NN)`)
3. **variacao**    — keywords (2-3 tokens distintivos)

A camada 4 (código histórico) prevista no roadmap fica para a Fase 3, quando
`produtos.codigo_almeida` estiver populado pelo `importador.py`.

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
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional, Protocol
from urllib.parse import quote_plus

from . import config, db
from .extrator import ItemExtraido
from .normalizador import keywords, normalizar_descricao, relevancia


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


class Fornecedor(Protocol):
    """Contrato dos scrapers de fornecedor."""

    nome: str

    def buscar(self, descricao: str) -> list[ResultadoBusca]:
        ...


# === Almeida ===

_BASE_URL_ALMEIDA = "https://www.almeidadistribuidor.com.br"
_MAX_RESULTADOS_POR_QUERY = 30

# Threshold mínimo de relevância para aceitar um resultado.
# 0.6 = pelo menos 60% das palavras-chave da descrição original aparece
# no nome do produto retornado. Calibrado empiricamente:
# - 0.5 deixava passar lixo (ex: "CIMENTO COM 50KG" → "CIMENTO QUEIMADO ...",
#   onde só "cimento" coincide entre 2 kws = 0.5).
# - 0.6 corta esses casos genéricos mas mantém matches verdadeiros como
#   abraçadeira de nylon (score ~0.67).
# Caso passe a faltar resultado em itens válidos, considere afrouxar para
# 0.5 e adicionar filtro adicional por categoria.
_RELEVANCIA_MINIMA = 0.6


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
    if preco <= 0:
        return None

    return ResultadoBusca(
        descricao_encontrada=nome,
        marca=marca,
        preco=preco,
        codigo_fornecedor=codigo,
        confianca=confianca,
        fornecedor="almeida",
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

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(viewport={"width": 1280, "height": 900})
        self._page = self._context.new_page()
        self._login(self._page)
        return self

    def __exit__(self, *_args) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()

    def _login(self, page) -> None:
        page.goto(f"{_BASE_URL_ALMEIDA}/entrar",
                  wait_until="networkidle", timeout=30_000)
        page.locator("input[name='email']").first.fill(self._email)
        senha_input = page.locator("input[name='senha']").first
        senha_input.fill(self._senha)
        # O submit do form é via JS — pressionar Enter é o jeito mais
        # robusto (o botão "Entrar" tem várias instâncias na página e
        # nenhuma tem role=button bem definido).
        senha_input.press("Enter")
        page.wait_for_timeout(4_000)

        # Valida que a sessão foi estabelecida. O sinal mais confiável é
        # a presença do cookie PHPSESSID + token (ambos só aparecem após
        # login bem-sucedido).
        cookies = {c["name"]: c["value"] for c in self._context.cookies()}
        if "PHPSESSID" not in cookies or "token" not in cookies:
            raise RuntimeError(
                "Falha no login Almeida — cookies de sessão não foram "
                "estabelecidos. Verifique email/senha em data/credenciais.json."
            )

    def buscar(self, descricao: str) -> list[ResultadoBusca]:
        """Tenta as camadas de busca em ordem. Para na primeira que
        produzir resultados relevantes. Aplica rate limit antes de cada
        busca real para respeitar o site parceiro.
        """
        if not descricao or not descricao.strip():
            return []

        # Camadas: começa pela descrição NORMALIZADA (não a crua) — o site
        # se confunde com vírgulas e prefixo "NN)" do edital.
        normalizada = normalizar_descricao(descricao)
        camadas: list[tuple[str, str]] = []
        if normalizada:
            camadas.append(("exato", normalizada))

        kws = keywords(descricao)[:3]
        if kws:
            kws_query = " ".join(kws)
            if kws_query != normalizada:
                camadas.append(("variacao", kws_query))

        # Fallback final: 1-2 keywords mais distintivas (caso top-3 não ache)
        if len(kws) >= 2:
            kws_curto = " ".join(kws[:2])
            if kws_curto not in (normalizada, " ".join(kws[:3])):
                camadas.append(("variacao", kws_curto))

        # Acumula o melhor `bruto` visto em qualquer camada — usado como
        # fallback quando filtro estrito não deixa passar nenhum.
        melhor_pool: list[tuple[float, ResultadoBusca]] = []

        for confianca, query in camadas:
            self._rate_limiter.acquire()
            brutos = self._buscar_query(self._page, query, confianca)
            relevantes = []
            for r in brutos:
                rel = relevancia(descricao, r.descricao_encontrada)
                if rel >= _RELEVANCIA_MINIMA:
                    relevantes.append(r)
                else:
                    melhor_pool.append((rel, r))
            if relevantes:
                return relevantes

        # Fallback: nenhum resultado passou no filtro estrito. Em vez de
        # entregar lista vazia (UI mostra "Não encontrado"), devolve os
        # top 3 candidatos vistos marcados como 'variacao' (amarelo), pra
        # operador decidir manualmente. Cenário típico: produto certo
        # existe mas tem nome só parcialmente parecido com o do edital.
        melhor_pool.sort(key=lambda x: x[0], reverse=True)
        top = []
        ja_vistos: set[tuple[str, float]] = set()
        for rel, r in melhor_pool:
            chave = (r.codigo_fornecedor, r.preco)
            if chave in ja_vistos:
                continue
            ja_vistos.add(chave)
            # Reforça que estes resultados são aproximados
            top.append(ResultadoBusca(
                descricao_encontrada=r.descricao_encontrada,
                marca=r.marca,
                preco=r.preco,
                codigo_fornecedor=r.codigo_fornecedor,
                confianca="variacao",
                fornecedor=r.fornecedor,
            ))
            if len(top) >= 3:
                break
        return top

    def _buscar_query(self, page, query: str, confianca: str) -> list[ResultadoBusca]:
        """Faz uma busca crua usando a página recebida e retorna até N
        resultados parseados (sem filtro de relevância — caller filtra)."""
        url = f"{_BASE_URL_ALMEIDA}/busca?s={quote_plus(query)}"
        page.goto(url, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(1_500)

        # Pega todos os cards e seus atributos data-* num único round-trip
        # ao browser. Mais barato que `.locator().all()` + `.get_attribute()`
        # por card (que dispara N IPCs).
        cards_attrs = page.evaluate("""
            () => {
                const cards = document.querySelectorAll(
                    '.product-card__info[data-codigo-produto]'
                );
                return Array.from(cards).slice(0, %d).map(el => ({
                    'data-codigo-produto': el.getAttribute('data-codigo-produto'),
                    'data-nome':           el.getAttribute('data-nome'),
                    'data-preco':          el.getAttribute('data-preco'),
                    'data-marca':          el.getAttribute('data-marca'),
                }));
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
    if preco <= 0:
        return None

    return ResultadoBusca(
        descricao_encontrada=nome,
        marca=marca,
        preco=preco,
        codigo_fornecedor=codigo,
        confianca=confianca,
        fornecedor="dantas",
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

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(viewport={"width": 1280, "height": 900})
        self._page = self._context.new_page()
        self._login(self._page)
        return self

    def __exit__(self, *_args) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()

    def _login(self, page) -> None:
        page.goto(f"{_BASE_URL_DANTAS}/entrar",
                  wait_until="networkidle", timeout=30_000)
        page.locator("input[name='email']").first.fill(self._email)
        senha_input = page.locator("input[name='senha']").first
        senha_input.fill(self._senha)
        senha_input.press("Enter")
        page.wait_for_timeout(4_000)

        cookies = {c["name"]: c["value"] for c in self._context.cookies()}
        if "PHPSESSID" not in cookies or "token" not in cookies:
            raise RuntimeError(
                "Falha no login Dantas — cookies de sessão não foram "
                "estabelecidos. Verifique email/senha em data/credenciais.json."
            )

    def buscar(self, descricao: str) -> list[ResultadoBusca]:
        if not descricao or not descricao.strip():
            return []

        normalizada = normalizar_descricao(descricao)
        camadas: list[tuple[str, str]] = []
        if normalizada:
            camadas.append(("exato", normalizada))

        kws = keywords(descricao)[:3]
        if kws:
            kws_query = " ".join(kws)
            if kws_query != normalizada:
                camadas.append(("variacao", kws_query))

        if len(kws) >= 2:
            kws_curto = " ".join(kws[:2])
            if kws_curto not in (normalizada, " ".join(kws[:3])):
                camadas.append(("variacao", kws_curto))

        # Mesma estratégia do Almeida (ver comentário lá): acumula
        # candidatos não-passados e devolve top 3 como fallback.
        melhor_pool: list[tuple[float, ResultadoBusca]] = []

        for confianca, query in camadas:
            self._rate_limiter.acquire()
            brutos = self._buscar_query(self._page, query, confianca)
            relevantes = []
            for r in brutos:
                rel = relevancia(descricao, r.descricao_encontrada)
                if rel >= _RELEVANCIA_MINIMA:
                    relevantes.append(r)
                else:
                    melhor_pool.append((rel, r))
            if relevantes:
                return relevantes

        melhor_pool.sort(key=lambda x: x[0], reverse=True)
        top = []
        ja_vistos: set[tuple[str, float]] = set()
        for rel, r in melhor_pool:
            chave = (r.codigo_fornecedor, r.preco)
            if chave in ja_vistos:
                continue
            ja_vistos.add(chave)
            top.append(ResultadoBusca(
                descricao_encontrada=r.descricao_encontrada,
                marca=r.marca,
                preco=r.preco,
                codigo_fornecedor=r.codigo_fornecedor,
                confianca="variacao",
                fornecedor=r.fornecedor,
            ))
            if len(top) >= 3:
                break
        return top

    def _buscar_query(self, page, query: str, confianca: str) -> list[ResultadoBusca]:
        url = f"{_BASE_URL_DANTAS}/busca?s={quote_plus(query)}"
        page.goto(url, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(1_500)

        cards_attrs = page.evaluate("""
            () => {
                const cards = document.querySelectorAll(
                    '.product-card__info[data-codigo-produto]'
                );
                return Array.from(cards).slice(0, %d).map(el => ({
                    'data-codigo-produto': el.getAttribute('data-codigo-produto'),
                    'data-nome':           el.getAttribute('data-nome'),
                    'data-preco':          el.getAttribute('data-preco'),
                    'data-marca':          el.getAttribute('data-marca'),
                }));
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
    """
    resultados: dict[int, list[ResultadoBusca]]
    erros: list[ErroBusca] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0


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
            out.append(ResultadoBusca(
                descricao_encontrada=d["descricao_encontrada"],
                marca=d.get("marca", ""),
                preco=float(d["preco"]),
                codigo_fornecedor=d["codigo_fornecedor"],
                confianca=d.get("confianca", "exato"),
                fornecedor=d.get("fornecedor", fornecedor),
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
    max_workers: Optional[int] = None,  # aceito mas ignorado (compat. legacy)
) -> ResultadoLote:
    """Busca em batch SERIAL com cache TTL=2d e coleta de erros.

    Por que serial: Playwright sync API atrela o browser à thread que
    chamou `sync_playwright().start()`. Tentar paralelizar com
    ThreadPoolExecutor gera `greenlet.error: Cannot switch to a different
    thread` (visto em produção em 2026-05-27). Para retomar paralelismo,
    refatorar para `playwright.async_api`.

    Cache:
    - Consulta `precos_cache_descricao` antes de chamar scraper. Hit
      reaproveita; miss busca e grava. Cache persistente entre execuções.
    - Cliente típico processa o mesmo edital várias vezes (revisões) —
      a 2ª execução fica praticamente instantânea.

    Erros isolados (timeout, layout mudou) **não** quebram batch — vão
    para `ResultadoLote.erros` que a UI mostra num painel.

    Args:
        itens: lista de ItemExtraido vindos da revisão.
        fornecedores: instâncias já abertas (login feito) de Almeida/Dantas.
        progresso_callback: callback `(atual, total, msg)` a cada item.
        usar_cache: False em testes que querem chamar scraper real.
        max_workers: ignorado (mantido na assinatura para compat. com
            chamadores antigos que ainda passam o argumento).
    """
    resultados: dict[int, list[ResultadoBusca]] = {}
    erros: list[ErroBusca] = []
    cache_hits = 0
    cache_misses = 0
    total = len(itens)

    if total == 0:
        return ResultadoLote(resultados={}, erros=[])

    # Conexão SQLite na thread do orquestrador. Sem lock — serial.
    conn = None
    if usar_cache:
        import sqlite3
        from . import config as _config
        conn = sqlite3.connect(_config.DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")

    try:
        for idx, item in enumerate(itens):
            bag: list[ResultadoBusca] = []
            desc_norm = normalizar_descricao(item.descricao)

            for forn in fornecedores:
                # 1. Tenta cache
                if conn is not None and desc_norm:
                    payload = db.cache_buscar(conn, desc_norm, forn.nome)
                    if payload is not None:
                        cache_hits += 1
                        bag.extend(_payload_to_resultados(payload, forn.nome))
                        continue
                    cache_misses += 1

                # 2. Busca real
                try:
                    encontrados = forn.buscar(item.descricao)
                except Exception as exc:
                    erros.append(ErroBusca(
                        indice_item=idx,
                        fornecedor=forn.nome,
                        descricao_resumo=(item.descricao or "")[:60],
                        mensagem=f"{type(exc).__name__}: {exc}",
                    ))
                    continue

                bag.extend(encontrados)

                # 3. Grava no cache
                if conn is not None and desc_norm:
                    payload = json.dumps(
                        [asdict(r) for r in encontrados], ensure_ascii=False
                    )
                    db.cache_gravar(conn, desc_norm, forn.nome, payload)

            resultados[idx] = bag
            if progresso_callback is not None:
                progresso_callback(idx + 1, total, item.descricao[:60])

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
    )
