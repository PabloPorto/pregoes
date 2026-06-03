"""
Matcher fuzzy entre descrição do edital e o catálogo histórico do cliente.

Esta é a **Camada 0** do pipeline de busca em 5 camadas (Q12). Quando o
operador importa planilhas históricas via `pregoes.importador`, a tabela
`produtos` ganha registros com `codigo_almeida` / `codigo_dantas`
preenchidos. A partir daí, para cada novo item de edital, podemos:

    descrição do edital
        → matcher fuzzy (token_sort_ratio >= 90) contra `produtos`
        → produto encontrado
        → extrai códigos para Almeida/Dantas
        → scraper faz busca DIRETA POR CÓDIGO no site (determinística)

Sem ambiguidade textual, sem disputa de relevância. Quando há histórico,
esta camada resolve o item antes mesmo de cair em busca por descrição.

Threshold = 90:
- Calibrado no plano Q12 como balanço entre cobertura e ruído.
- `token_sort_ratio` é tolerante a ordem ("PVC ADAPTADOR" vs "ADAPTADOR PVC")
  e erros leves ("PARAFUZO"/"PARAFUSO"). 90 ainda exige correspondência
  semântica forte; 80 deixaria passar matches duvidosos.
- Threshold por sí só não basta — quem chama deve sempre exigir
  correspondência de ao menos um token-chave (regra do plano Q12). Aqui
  garantimos isso filtrando por sobreposição de keywords.

Função pura: recebe lista de registros e descrição, devolve melhor match.
Não toca em SQLite diretamente — `buscar_produto` é o wrapper que faz
SELECT e chama esta função pura. Mantém testes independentes do banco.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz, process

from .normalizador import keywords, normalizar_descricao


# Threshold mínimo do `token_sort_ratio` para aceitar um match.
# Plano Q12 propôs 90. Operacional: quem chama pode afrouxar passando
# `threshold` menor (ex: 75 para Camada 4 com "descrições parecidas mas
# não idênticas" reusando aprendizado).
THRESHOLD_MATCH_DEFAULT = 90


@dataclass(frozen=True)
class ProdutoCatalogo:
    """Registro do catálogo (tabela `produtos`) com o que o matcher precisa."""
    produto_id: int
    descricao_canonica: str
    codigo_almeida: Optional[str]
    codigo_dantas: Optional[str]
    marca: Optional[str]
    unidade_padrao: Optional[str]


@dataclass(frozen=True)
class MatchProduto:
    """Resultado do matcher — produto encontrado com score."""
    produto: ProdutoCatalogo
    score: float    # 0..100 (escala do rapidfuzz)

    def codigo_para(self, fornecedor: str) -> Optional[str]:
        """Devolve o código do fornecedor solicitado, ou None se não houver."""
        if fornecedor == "almeida":
            return self.produto.codigo_almeida
        if fornecedor == "dantas":
            return self.produto.codigo_dantas
        return None


def carregar_catalogo(conn: sqlite3.Connection) -> list[ProdutoCatalogo]:
    """Lê todos os produtos do banco para o matcher operar em memória.

    Volume típico esperado: ~5.000 produtos (Q2). Carregar tudo em memória
    cabe folgado — cada registro ~200 bytes ≈ 1MB total. Faz UM SELECT em
    vez de N queries no matcher.
    """
    rows = conn.execute(
        """
        SELECT id, descricao_canonica, codigo_almeida, codigo_dantas,
               marca, unidade_padrao
        FROM produtos
        WHERE descricao_canonica IS NOT NULL
          AND TRIM(descricao_canonica) != ''
        """
    ).fetchall()
    return [
        ProdutoCatalogo(
            produto_id=row["id"] if hasattr(row, "keys") else row[0],
            descricao_canonica=row["descricao_canonica"] if hasattr(row, "keys") else row[1],
            codigo_almeida=row["codigo_almeida"] if hasattr(row, "keys") else row[2],
            codigo_dantas=row["codigo_dantas"] if hasattr(row, "keys") else row[3],
            marca=row["marca"] if hasattr(row, "keys") else row[4],
            unidade_padrao=row["unidade_padrao"] if hasattr(row, "keys") else row[5],
        )
        for row in rows
    ]


def melhor_match(
    descricao: str,
    catalogo: list[ProdutoCatalogo],
    *,
    threshold: int = THRESHOLD_MATCH_DEFAULT,
) -> Optional[MatchProduto]:
    """Encontra o melhor produto do catálogo para a descrição do edital.

    Função PURA — não toca em SQLite. Recebe lista e devolve melhor match.

    Algoritmo:
    1. Normaliza a descrição do edital (acentos, frações, prefixo "NN)")
    2. Cria índice {produto_id: descricao_normalizada} do catálogo
    3. `rapidfuzz.process.extractOne` com `token_sort_ratio`
    4. Filtro extra de proteção: pelo menos UM token-chave (>=3 chars,
       não-stopword, não-unidade) precisa coincidir entre original e
       canônica. Bloqueia "falsos amigos" — produtos com tokens curtos
       ou stopwords que casam acidentalmente com score alto.

    Retorna `None` se:
    - descrição vazia
    - catálogo vazio
    - nenhum produto >= threshold
    - melhor match não compartilha keywords (filtro de proteção)
    """
    if not descricao or not catalogo:
        return None

    desc_norm = normalizar_descricao(descricao)
    if not desc_norm:
        return None

    # Índice id → descricao normalizada (para o rapidfuzz reconciliar)
    # Não usamos a descrição canônica crua porque ela tem acentos e
    # variações de caixa; normalizando, comparamos "maçãs com maçãs".
    indice: dict[int, str] = {
        p.produto_id: normalizar_descricao(p.descricao_canonica)
        for p in catalogo
    }
    # Remove entradas com descrição normalizada vazia (caso raro de catálogo sujo)
    indice = {k: v for k, v in indice.items() if v}
    if not indice:
        return None

    res = process.extractOne(
        desc_norm,
        indice,
        scorer=fuzz.token_sort_ratio,
        score_cutoff=threshold,
    )
    if res is None:
        return None

    # Em rapidfuzz 3.x com dict: retorna (value, score, key)
    _, score, produto_id = res

    # Filtro de proteção: ao menos uma keyword distintiva precisa coincidir.
    # Sem isso, "TUBO PVC 50MM" e "CANO PVC 100MM" podem casar com score alto
    # (token_sort_ratio compensa muito).
    kws_orig = set(keywords(descricao))
    kws_cand = set(keywords(_canonica_de(catalogo, produto_id)))
    if kws_orig and not (kws_orig & kws_cand):
        return None

    produto = _por_id(catalogo, produto_id)
    if produto is None:
        return None

    return MatchProduto(produto=produto, score=float(score))


def buscar_produto(
    conn: sqlite3.Connection,
    descricao: str,
    *,
    threshold: int = THRESHOLD_MATCH_DEFAULT,
) -> Optional[MatchProduto]:
    """Wrapper conveniente: carrega catálogo e chama `melhor_match`.

    Para uso em lote (vários itens do mesmo edital), prefira chamar
    `carregar_catalogo` uma vez e `melhor_match` N vezes — evita N
    SELECTs no banco.
    """
    catalogo = carregar_catalogo(conn)
    return melhor_match(descricao, catalogo, threshold=threshold)


def _canonica_de(catalogo: list[ProdutoCatalogo], produto_id: int) -> str:
    for p in catalogo:
        if p.produto_id == produto_id:
            return p.descricao_canonica
    return ""


def _por_id(catalogo: list[ProdutoCatalogo], produto_id: int) -> Optional[ProdutoCatalogo]:
    for p in catalogo:
        if p.produto_id == produto_id:
            return p
    return None
