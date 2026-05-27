"""
Mapeamento manual de tabelas para PDFs cujo cabeçalho a heurística não
detectou (ou detectou errado).

Fluxo:
1. Operador escolhe qual das tabelas brutas detectadas no PDF é a
   "tabela de itens" real.
2. Para cada coluna dessa tabela, operador diz qual campo é: descrição,
   quantidade, unidade, valor de referência, código do órgão ou ignorar.
3. Sistema gera mapa `{campo: indice_coluna}`, aplica à tabela e devolve
   `ResultadoExtracao` no formato padrão.
4. Sistema grava (assinatura_cabeçalho → mapa) em `mapeamentos_pdf`
   (tabela já existente no schema, [db.py:80](db.py:80)). Próximo PDF
   com mesmo cabeçalho recupera o mapa automaticamente sem precisar
   refazer o mapeamento.

Assinatura: hash sha256 da concatenação canonizada (lower, sem acento,
sem espaço extra) das células da primeira linha da tabela. Robusto a
variações de espaçamento, caixa e acentuação — resolve "vários editais
do mesmo órgão usam o mesmo cabeçalho".
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from typing import Optional

from .extrator import (
    ItemExtraido,
    ResultadoExtracao,
    _THRESHOLD_LINHA_DUVIDOSA,
    _THRESHOLD_LINHA_EXTRAIDA,
    _para_int,
    _para_numero,
)
from .validador import score_linha


# Campos canônicos que o usuário pode mapear. 'ignorar' = coluna não usada.
CAMPOS = ["ignorar", "descricao", "quantidade", "unidade", "valor_referencia", "codigo_orgao"]

# Para a UI mostrar legível
ROTULOS_CAMPOS = {
    "ignorar": "— ignorar —",
    "descricao": "Descrição",
    "quantidade": "Quantidade",
    "unidade": "Unidade",
    "valor_referencia": "Valor de referência",
    "codigo_orgao": "Código (item nº)",
}


def assinatura_cabecalho(linha: list) -> str:
    """Hash determinístico de uma linha-cabeçalho. Robusto a variações
    cosméticas (caixa, acento, espaço). Usado como chave em mapeamentos_pdf."""
    partes = []
    for cel in linha or []:
        s = str(cel) if cel is not None else ""
        s = unicodedata.normalize("NFD", s)
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        s = "_".join(s.lower().split())
        partes.append(s)
    canonica = "|".join(partes)
    return hashlib.sha256(canonica.encode("utf-8")).hexdigest()[:16]


def carregar_mapa(conn: sqlite3.Connection, assinatura: str) -> Optional[dict[str, int]]:
    """Carrega mapa salvo para uma assinatura. Incrementa `usado_count`."""
    row = conn.execute(
        "SELECT mapa_colunas FROM mapeamentos_pdf WHERE assinatura = ?",
        (assinatura,),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE mapeamentos_pdf SET usado_count = usado_count + 1 "
        "WHERE assinatura = ?",
        (assinatura,),
    )
    return json.loads(row[0])


def salvar_mapa(conn: sqlite3.Connection, assinatura: str, mapa: dict[str, int]) -> None:
    """Persiste mapa para uma assinatura. UPSERT por assinatura."""
    conn.execute(
        """
        INSERT INTO mapeamentos_pdf (assinatura, mapa_colunas)
        VALUES (?, ?)
        ON CONFLICT(assinatura) DO UPDATE SET
            mapa_colunas = excluded.mapa_colunas,
            usado_count = mapeamentos_pdf.usado_count + 1
        """,
        (assinatura, json.dumps(mapa)),
    )


def aplicar_mapa(
    linhas_tabela: list[list],
    mapa: dict[str, int],
    *,
    arquivo: str = "",
    pular_cabecalho: bool = True,
) -> ResultadoExtracao:
    """Aplica um mapa a uma tabela única e devolve ResultadoExtracao.

    Usado tanto pelo fluxo manual (após o operador confirmar mapa) quanto
    pelo fluxo automático (quando assinatura bate com mapa salvo).
    """
    resultado = ResultadoExtracao(arquivo=arquivo)
    resultado.tabelas_brutas = [linhas_tabela]
    resultado.mapa_colunas_detectado = mapa

    inicio = 1 if pular_cabecalho else 0
    n_extraidos = 0
    n_duvidosos = 0

    for linha in linhas_tabela[inicio:]:
        item = _linha_para_item(linha, mapa)
        if item is None:
            continue

        sc = score_linha(item.descricao, item.quantidade, item.unidade,
                         item.valor_referencia).score
        if sc >= _THRESHOLD_LINHA_EXTRAIDA:
            item.confianca = "extraido"
            n_extraidos += 1
        elif sc >= _THRESHOLD_LINHA_DUVIDOSA:
            item.confianca = "duvidoso"
            n_duvidosos += 1
        else:
            continue
        resultado.itens.append(item)

    total = n_extraidos + n_duvidosos
    resultado.confianca_global = (n_extraidos / total) if total else 0.0
    return resultado


def _linha_para_item(linha: list, mapa: dict[str, int]) -> Optional[ItemExtraido]:
    """Converte uma linha-bruta em ItemExtraido usando mapa manual."""
    if not linha:
        return None

    def _get(campo: str) -> Optional[str]:
        idx = mapa.get(campo)
        if idx is None or idx >= len(linha):
            return None
        v = linha[idx]
        if v is None:
            return None
        return str(v).strip().replace("\n", " ")

    descricao = _get("descricao") or ""
    if not descricao or len(descricao) < 3:
        return None

    codigo = _get("codigo_orgao")
    return ItemExtraido(
        item_numero=_para_int(codigo),
        codigo_orgao=codigo,
        descricao=descricao,
        unidade=_get("unidade"),
        quantidade=_para_numero(_get("quantidade")),
        valor_referencia=_para_numero(_get("valor_referencia")),
    )
