"""
Fase 4 — Inteligência tributária: Substituição Tributária (ST) por NCM.

Regra do contador do cliente: se o NCM do produto consta na lista de ST do
estado (Anexo 05 do RICMS/PB), é forte indício de ST → usa markup MENOR (o
ICMS já foi recolhido na origem); senão, markup normal.

A lista de ST é dado de REFERÊNCIA (legislação pública, muda ~1×/ano) e mora
em ``pregoes/dados/ncm_st.json`` — versionada junto do código (bundla no .exe,
não depende de banco). Para atualizar quando o contador mandar nova relação:
re-extrair o PDF e regerar o JSON (a extração do PDF é semântica, pois as
colunas vêm embaralhadas — ver histórico).

Match HIERÁRQUICO por prefixo: a lista mistura NCMs de 4, 6 e 8 dígitos — uma
"posição" de 4 dígitos (ex.: ``3917``) cobre toda a família ``3917.xx.xx``. Um
produto é ST se o NCM dele OU qualquer prefixo (8 → 6 → 4) constar na lista.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from pregoes import config

_ARQ = Path(__file__).resolve().parent / "dados" / "ncm_st.json"


@lru_cache(maxsize=1)
def _conjunto_st() -> frozenset[str]:
    """NCMs/posições com ST (só dígitos, granularidade 4/6/8 preservada).

    Degrada com segurança: se o arquivo sumir/quebrar, devolve conjunto vazio
    → nada é classificado como ST → todos usam markup normal (o operador ainda
    pode ajustar na UI). Nunca derruba a geração da planilha por causa disto.
    """
    try:
        doc = json.loads(_ARQ.read_text(encoding="utf-8"))
        return frozenset(e["ncm"] for e in doc.get("ncms", []) if e.get("ncm"))
    except Exception:
        return frozenset()


def normalizar_ncm(ncm: str | None) -> str:
    """Reduz um NCM a só dígitos. ``'3917.40.90'`` -> ``'39174090'``."""
    return re.sub(r"\D", "", ncm or "")


def eh_st(ncm: str | None) -> bool:
    """True se o NCM (ou um prefixo dele) consta na lista de ST.

    Testa o NCM completo e depois os prefixos de 6 e 4 dígitos, porque a lista
    usa posições (``'3917'`` cobre ``'3917.40.90'``). NCM com menos de 4
    dígitos (ou vazio) não classifica.
    """
    n = normalizar_ncm(ncm)
    if len(n) < 4:
        return False
    conjunto = _conjunto_st()
    return n in conjunto or n[:6] in conjunto or n[:4] in conjunto


def markup_para(
    ncm: str | None,
    *,
    markup_st: float | None = None,
    markup_normal: float | None = None,
) -> float:
    """Markup do item pela classificação tributária (ST → menor).

    Os defaults vêm do ``config`` (editáveis por pregão na UI). Sem NCM, devolve
    o normal — o sistema NÃO presume ST sem base; o operador confirma na tela.
    """
    m_st = markup_st if markup_st is not None else config.MARKUP_ST
    m_normal = markup_normal if markup_normal is not None else config.MARKUP_NORMAL
    return m_st if eh_st(ncm) else m_normal


# === Captura do NCM no scraper (a fonte é o site do fornecedor) ===

def ncm_de_card(valor: str | None) -> str | None:
    """NCM de 8 dígitos vindo de um atributo (ex.: ``data-ncm`` do card), ou None.

    Os cards de busca de Almeida/Dantas podem ou não trazer o NCM — se trouxer,
    capturamos de graça; se não, fica None e o operador decide na UI.
    """
    n = normalizar_ncm(valor)
    return n if len(n) == 8 else None


_RE_NCM_TEXTO = re.compile(r"NCM[:\s]*([\d.]{8,12})", re.IGNORECASE)


def ncm_de_texto(texto: str | None) -> str | None:
    """Extrai o NCM da PÁGINA de detalhe do produto (ex.: ``Código NCM:
    39174090``). Para o caminho em que o card não traz ``data-ncm`` — basta o
    scraper passar o texto da página. None se não houver NCM de 8 dígitos.
    """
    if not texto:
        return None
    m = _RE_NCM_TEXTO.search(texto)
    return ncm_de_card(m.group(1)) if m else None
