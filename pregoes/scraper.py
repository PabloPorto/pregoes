"""
Scraper de fornecedores: Almeida (implementado) + Dantas (esqueleto).

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

Uso típico:
    cred = credenciais.carregar("almeida")
    with AlmeidaDistribuidor(cred["email"], cred["senha"]) as alm:
        resultados = alm.buscar("ABRAÇADEIRA DE NYLON 100 X 2.5 PCT")
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol
from urllib.parse import quote_plus

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


class AlmeidaDistribuidor:
    """
    Scraper de almeidadistribuidor.com.br.

    Gerenciado como context manager: o `__enter__` faz o login uma vez,
    e o `__exit__` fecha o browser. Múltiplas chamadas a `.buscar()` no
    meio reaproveitam a sessão (cookies PHPSESSID + token).
    """

    nome = "almeida"

    def __init__(self, email: str, senha: str, *, headless: bool = True):
        self._email = email
        self._senha = senha
        self._headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def __enter__(self) -> "AlmeidaDistribuidor":
        # Import local para não pagar o custo de carregar Playwright em
        # quem só importa o módulo (ex: testes da função pura).
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(viewport={"width": 1280, "height": 900})
        self._page = self._context.new_page()
        self._login()
        return self

    def __exit__(self, *_args) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()

    def _login(self) -> None:
        page = self._page
        assert page is not None
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
        """
        Tenta as camadas de busca em ordem. Para na primeira que produzir
        resultados RELEVANTES (filtrados por `normalizador.relevancia`).

        Cada resultado bruto é confrontado com a descrição original do
        edital. Resultados com menos de `_RELEVANCIA_MINIMA` palavras-chave
        em comum são descartados — o site faz busca por tokens isolados e
        devolve qualquer produto que tenha qualquer palavra, mesmo sem
        relação real.

        Não lança em produto inexistente — retorna lista vazia. Lança
        apenas em falha técnica (timeout, rede caída, layout mudou).
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

        for confianca, query in camadas:
            brutos = self._buscar_query(query, confianca)
            relevantes = [
                r for r in brutos
                if relevancia(descricao, r.descricao_encontrada) >= _RELEVANCIA_MINIMA
            ]
            if relevantes:
                return relevantes

        return []

    def _buscar_query(self, query: str, confianca: str) -> list[ResultadoBusca]:
        """Faz uma busca crua e retorna até N resultados parseados (sem
        filtro de relevância — quem chama é responsável por filtrar)."""
        page = self._page
        assert page is not None
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


# === Dantas (esqueleto) ===

class DantasDistribuidora:
    """Scraper de dantasdistribuidora.com.br. Não implementado ainda."""

    nome = "dantas"

    def buscar(self, descricao: str) -> list[ResultadoBusca]:
        raise NotImplementedError(
            "DantasDistribuidora.buscar() será implementado no próximo lote "
            "da Fase 2 (após validar Almeida em produção)."
        )


# === Orquestrador em lote ===

ProgressoCallback = Callable[[int, int, str], None]


def buscar_precos_para_itens(
    itens: list[ItemExtraido],
    fornecedores: list[Fornecedor],
    *,
    progresso_callback: Optional[ProgressoCallback] = None,
) -> dict[int, list[ResultadoBusca]]:
    """
    Para cada item, busca em todos os fornecedores e concatena os resultados.

    Retorna `{indice_do_item: [ResultadoBusca, ...]}`. Itens sem nenhum
    resultado em nenhum fornecedor mapeiam para lista vazia (UI mostra
    vermelho).

    Falhas isoladas de um fornecedor (timeout, layout mudou) **não** quebram
    o batch — o item é registrado com lista vazia e a busca continua.

    Args:
        itens: lista de ItemExtraido vindos da revisão.
        fornecedores: instâncias já abertas (login feito) de Almeida/Dantas.
            O orquestrador NÃO gerencia ciclo de vida — quem chama abre/fecha
            via context manager.
        progresso_callback(atual, total, msg): chamado após cada item, para
            a UI atualizar barra de progresso. `atual` começa em 1.

    TODO(cache): integrar com `db.preco_em_cache()` quando `produto_id` estiver
    disponível em ItemExtraido (depende da Fase 3 — matcher fuzzy).
    Hoje, cada chamada é uma busca real.
    """
    resultados: dict[int, list[ResultadoBusca]] = {}
    total = len(itens)
    for idx, item in enumerate(itens):
        bag: list[ResultadoBusca] = []
        for forn in fornecedores:
            try:
                bag.extend(forn.buscar(item.descricao))
            except Exception:
                # Fornecedor falhou neste item; segue o batch.
                # A UI mostra vermelho para itens com bag vazio no fim.
                continue
        resultados[idx] = bag
        if progresso_callback is not None:
            progresso_callback(idx + 1, total, item.descricao[:60])
    return resultados
