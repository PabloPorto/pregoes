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


# Threshold mínimo do `token_sort_ratio` para aceitar um match no modo CRU
# (descrições normalizadas localmente). Plano Q12 propôs 90.
THRESHOLD_MATCH_DEFAULT = 90

# Threshold do modo CANÔNICO (Fase B): usa `token_set_ratio` (tolerante a
# palavras extras de um lado só — a normalização LLM é levemente
# inconsistente: "arame farpado galvanizado 1.65mm" vs "arame farpado
# 1.65mm x 250m"). Mais baixo que o cru porque o set_ratio é mais
# conservador em score, e a validação de dimensão + filtro de keyword
# protegem contra falso positivo.
THRESHOLD_CANONICO = 85


@dataclass(frozen=True)
class ProdutoCatalogo:
    """Registro do catálogo (tabela `produtos`) com o que o matcher precisa.

    `canonico` é a forma canônica alinhada por LLM (Fase B / `produtos_canonico`),
    quando disponível — vocabulário padronizado que casa com o canônico do
    edital. None quando o catálogo ainda não foi normalizado.
    """
    produto_id: int
    descricao_canonica: str
    codigo_almeida: Optional[str]
    codigo_dantas: Optional[str]
    marca: Optional[str]
    unidade_padrao: Optional[str]
    canonico: Optional[str] = None


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
        SELECT p.id, p.descricao_canonica, p.codigo_almeida, p.codigo_dantas,
               p.marca, p.unidade_padrao, pc.canonico
        FROM produtos p
        LEFT JOIN produtos_canonico pc ON pc.produto_id = p.id
        WHERE p.descricao_canonica IS NOT NULL
          AND TRIM(p.descricao_canonica) != ''
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
            canonico=row["canonico"] if hasattr(row, "keys") else row[6],
        )
        for row in rows
    ]


def indice_por_codigo(
    catalogo: list[ProdutoCatalogo],
) -> dict[tuple[str, str], ProdutoCatalogo]:
    """Índice reverso `(fornecedor, codigo) -> produto` do catálogo.

    Usado para reconhecer, entre os candidatos que a busca devolve, quais já
    estão no catálogo do cliente PELO CÓDIGO do fornecedor (feedback do cliente:
    "queremos saber se já temos aquele produto"). É o inverso da Camada 0 (que
    vai descrição→catálogo→código): aqui vamos resultado→código→catálogo.

    Construído uma vez por lote a partir do catálogo já carregado em memória.
    """
    idx: dict[tuple[str, str], ProdutoCatalogo] = {}
    for p in catalogo:
        if p.codigo_almeida and p.codigo_almeida.strip():
            idx[("almeida", p.codigo_almeida.strip())] = p
        if p.codigo_dantas and p.codigo_dantas.strip():
            idx[("dantas", p.codigo_dantas.strip())] = p
    return idx


def melhor_match(
    descricao: str,
    catalogo: list[ProdutoCatalogo],
    *,
    threshold: int = THRESHOLD_MATCH_DEFAULT,
    canonico_edital: Optional[str] = None,
) -> Optional[MatchProduto]:
    """Encontra o melhor produto do catálogo para a descrição do edital.

    Função PURA — não toca em SQLite. Recebe lista e devolve melhor match.

    Dois modos:
    - **Canônico (Fase B)**: se `canonico_edital` é dado E o catálogo tem
      formas canônicas (`produto.canonico`), casa as FORMAS CANÔNICAS — ambos
      reduzidos pelo LLM ao mesmo vocabulário ("tubo pvc 100mm"). Resolve
      sinônimo/abreviação que o fuzzy cru não pega (cano≠tubo, sxt≠sextavado).
    - **Cru (degradação)**: senão, casa as descrições normalizadas localmente
      (comportamento anterior). Usado quando o catálogo não foi normalizado
      ou não há quota.

    Em ambos os modos:
    - filtro de proteção por keyword (≥1 token distintivo em comum);
    - validação de DIMENSÃO (Tier 2): se ambos têm dimensão no mesmo eixo
      mas valores divergentes (5/16 vs 3/8), rejeita — é subproduto diferente.

    Retorna `None` se nada casa com segurança.
    """
    if not descricao or not catalogo:
        return None

    usar_canonico = (
        bool(canonico_edital) and any(p.canonico for p in catalogo)
    )
    if usar_canonico:
        consulta = canonico_edital
        indice = {p.produto_id: p.canonico for p in catalogo if p.canonico}
        # token_set_ratio: tolera palavras que aparecem só de um lado (a
        # normalização LLM é levemente inconsistente). Threshold próprio.
        scorer = fuzz.token_set_ratio
        cutoff = THRESHOLD_CANONICO if threshold == THRESHOLD_MATCH_DEFAULT else threshold
    else:
        consulta = normalizar_descricao(descricao)
        indice = {
            p.produto_id: normalizar_descricao(p.descricao_canonica)
            for p in catalogo
        }
        scorer = fuzz.token_sort_ratio
        cutoff = threshold
    consulta = (consulta or "").strip()
    indice = {k: v for k, v in indice.items() if v}
    if not consulta or not indice:
        return None

    res = process.extractOne(
        consulta, indice, scorer=scorer, score_cutoff=cutoff,
    )
    if res is None:
        return None
    _, score, produto_id = res

    produto = _por_id(catalogo, produto_id)
    if produto is None:
        return None

    # Texto do lado do edital e do produto para os filtros abaixo —
    # usa o canônico quando esse foi o modo de casamento.
    texto_edital = consulta if usar_canonico else descricao
    texto_produto = (produto.canonico if usar_canonico else
                     produto.descricao_canonica) or ""

    # Filtro de proteção: ao menos uma keyword distintiva em comum.
    kws_e = set(keywords(texto_edital))
    kws_p = set(keywords(texto_produto))
    if kws_e and not (kws_e & kws_p):
        return None

    # Validação de dimensão: mesmo eixo com valor divergente → subproduto
    # diferente (parafuso 5/16 não é parafuso 3/8).
    if _dimensao_diverge(texto_edital, texto_produto):
        return None

    return MatchProduto(produto=produto, score=float(score))


def _dimensao_diverge(texto_a: str, texto_b: str) -> bool:
    """True se A e B têm dimensão no MESMO eixo mas nenhum valor em comum."""
    from .normalizador import extrair_dimensoes
    da, dbb = extrair_dimensoes(texto_a), extrair_dimensoes(texto_b)
    if not da or not dbb:
        return False
    eixos_comuns = {e for e, _ in da} & {e for e, _ in dbb}
    for eixo in eixos_comuns:
        va = {v for e, v in da if e == eixo}
        vb = {v for e, v in dbb if e == eixo}
        if not (va & vb):
            return True
    return False


# Reuso FUZZY do aprendizado bypassa a busca textual inteira (vai direto no
# código), então o threshold é mais ALTO que o catálogo (90): só reusa quando a
# descrição é bem parecida. O gate de dimensão é a 2ª salvaguarda.
THRESHOLD_APRENDIDO_FUZZY = 92


def melhor_aprendido_fuzzy(
    descricao_edital: str,
    fornecedor: str,
    aprendidos: list,
    threshold: int = THRESHOLD_APRENDIDO_FUZZY,
):
    """Camada 4 plena: reusa um mapeamento aprendido cuja descrição é PARECIDA
    com a do edital (token_sort_ratio >= threshold) no MESMO fornecedor — desde
    que as DIMENSÕES não divirjam (não reusa 'válvula 7/8' para 'válvula 1.1/4').

    `aprendidos` são linhas de `db.aprendidos_carregar` (acesso por chave:
    descricao_normalizada, fornecedor, codigo_fornecedor, marca, preco). Retorna
    o registro aprendido casado, ou None.
    """
    if not descricao_edital or not aprendidos:
        return None
    alvo = normalizar_descricao(descricao_edital)
    if not alvo:
        return None
    cands = [a for a in aprendidos if a["fornecedor"] == fornecedor]
    if not cands:
        return None
    escolhas = {i: a["descricao_normalizada"] for i, a in enumerate(cands)}
    achou = process.extractOne(
        alvo, escolhas, scorer=fuzz.token_sort_ratio, score_cutoff=threshold)
    if achou is None:
        return None
    _desc, _score, idx = achou
    cand = cands[idx]
    if _dimensao_diverge(descricao_edital, cand["descricao_normalizada"]):
        return None   # nome parecido mas medida diferente → não reusa
    return cand


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
