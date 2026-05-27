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


def aplicar_mapa_em_lote(
    tabelas_brutas: list[list[list]],
    idx_base: int,
    mapa: dict[str, int],
    *,
    arquivo: str = "",
) -> ResultadoExtracao:
    """Aplica o mapa à tabela escolhida + às fatias-continuação adjacentes.

    Cenário concreto observado em produção (PDF "EDITAL E ANEXOS",
    93 págs, 2026-05-27): a tabela de itens da MATRIZ é fragmentada em
    múltiplas tabelas (uma por página) pelo pdfplumber. Quando o
    operador mapeia só uma delas, perde os itens das outras páginas.

    Esta função detecta as fatias da mesma tabela visual e aplica o
    mapeamento a todas, concatenando os itens.

    Heurísticas para "fatia continuação" (qualquer uma serve):
    - Mesma assinatura de cabeçalho da tabela base (cabeçalho repetido
      em cada página, padrão típico de tabelas no Word/Excel/InDesign).
    - Cabeçalho fake do Docling ('0','1','2'...) ou todo vazio.
    - Primeira linha tem perfil de DADO (não cabeçalho) e nº de colunas
      compatível com a base — Docling/pdfplumber às vezes "perdem" o
      cabeçalho na fatia seguinte.

    Args:
        tabelas_brutas: todas as tabelas detectadas no PDF.
        idx_base: índice da tabela que o operador escolheu no dropdown.
        mapa: mapeamento {campo: idx_coluna} da tabela base.
        arquivo: caminho do PDF (para `ResultadoExtracao.arquivo`).
    """
    if not tabelas_brutas or not 0 <= idx_base < len(tabelas_brutas):
        return aplicar_mapa([], mapa, arquivo=arquivo)

    base = tabelas_brutas[idx_base]
    if not base or not base[0]:
        return aplicar_mapa(base or [], mapa, arquivo=arquivo)

    base_assinatura = assinatura_cabecalho(base[0])
    base_n_cols = max((len(l) for l in base if l), default=0)

    # Identifica as tabelas que pertencem ao mesmo grupo da base
    indices_irmas = _identificar_fatias_irmas(
        tabelas_brutas, idx_base, base_assinatura, base_n_cols
    )

    # Concatena: linhas da base (sem cabeçalho) + linhas das irmãs (lidando
    # com cabeçalho repetido VS continuação direta).
    linhas_unificadas: list[list] = list(base[1:])   # pula cabeçalho da base
    for idx_irma in indices_irmas:
        fatia = tabelas_brutas[idx_irma]
        if not fatia:
            continue
        if _primeira_linha_eh_cabecalho_repetido(fatia[0], base_assinatura):
            # Cabeçalho repetido — pula
            linhas_unificadas.extend(fatia[1:])
        elif _eh_cabecalho_fake_ou_vazio(fatia[0]):
            # Cabeçalho fake/vazio — pula
            linhas_unificadas.extend(fatia[1:])
        else:
            # Primeira linha já é dado — preserva tudo
            linhas_unificadas.extend(fatia)

    # Constrói tabela "virtual" com cabeçalho da base + todas linhas
    tabela_unificada = [base[0]] + linhas_unificadas
    resultado = aplicar_mapa(
        tabela_unificada, mapa, arquivo=arquivo, pular_cabecalho=True,
    )

    # Preserva tabelas brutas originais (UI usa pra mostrar opções)
    resultado.tabelas_brutas = tabelas_brutas
    n_irmas = len(indices_irmas)
    if n_irmas > 0:
        resultado.avisos.insert(
            0,
            f"Mapeamento aplicado à tabela #{idx_base + 1} + {n_irmas} "
            f"fatia(s) continuação (mesma tabela multi-página).",
        )
    return resultado


def _identificar_fatias_irmas(
    tabelas: list[list[list]],
    idx_base: int,
    base_assinatura: str,
    base_n_cols: int,
) -> list[int]:
    """Retorna índices das tabelas que parecem ser fatias da mesma
    tabela visual da `tabelas[idx_base]`.

    Procura tanto ANTES quanto DEPOIS da base — alguns PDFs têm a
    primeira fatia fragmentada (cabeçalho na pág 1, mas tabela "boa"
    começa pág 2 e operador escolhe a pág 2 sem ver a pág 1).

    Para de buscar ao encontrar uma tabela claramente diferente
    (cabeçalho novo válido com palavras-chave).
    """
    irmas: list[int] = []

    # Procura DEPOIS da base
    for i in range(idx_base + 1, len(tabelas)):
        if _eh_irma(tabelas[i], base_assinatura, base_n_cols):
            irmas.append(i)
        else:
            break   # sequência quebrou — fim da tabela visual

    # Procura ANTES da base (em ordem reversa, depois inverte)
    irmas_antes: list[int] = []
    for i in range(idx_base - 1, -1, -1):
        if _eh_irma(tabelas[i], base_assinatura, base_n_cols):
            irmas_antes.append(i)
        else:
            break
    irmas = list(reversed(irmas_antes)) + irmas

    return irmas


def _eh_irma(fatia: list[list], base_assinatura: str, base_n_cols: int) -> bool:
    """True se `fatia` parece ser continuação da mesma tabela visual."""
    if not fatia or not fatia[0]:
        return False

    n_cols_fatia = max((len(l) for l in fatia if l), default=0)
    # Tolerância de ±3 colunas: alguns extratores perdem colunas vazias
    if abs(n_cols_fatia - base_n_cols) > 3:
        return False

    # (A) Mesma assinatura de cabeçalho — cabeçalho repetido em cada página
    if assinatura_cabecalho(fatia[0]) == base_assinatura:
        return True

    # (B) Cabeçalho fake do Docling ou vazio
    if _eh_cabecalho_fake_ou_vazio(fatia[0]):
        return True

    # (C) Primeira linha tem perfil de DADO real (não cabeçalho).
    #     Heurística simples: contém pelo menos 1 célula com texto longo
    #     OU pelo menos 2 numéricas. Sinal de que essa fatia começa
    #     direto com dados (extrator perdeu cabeçalho).
    return _primeira_linha_parece_dado(fatia[0])


def _primeira_linha_eh_cabecalho_repetido(linha: list, base_assinatura: str) -> bool:
    return bool(linha) and assinatura_cabecalho(linha) == base_assinatura


def _eh_cabecalho_fake_ou_vazio(linha: list) -> bool:
    """True para '0','1','2'... ou todo vazio."""
    if not linha:
        return True
    nao_vazios = [c for c in linha if c and str(c).strip()]
    if not nao_vazios:
        return True
    # Sequência numérica monotônica
    try:
        return all(
            str(c).strip().isdigit() and int(str(c).strip()) == i
            for i, c in enumerate(linha)
            if c is not None and str(c).strip()
        )
    except Exception:
        return False


def _primeira_linha_parece_dado(linha: list) -> bool:
    """≥1 célula com texto longo OU ≥2 numéricas/monetárias."""
    n_texto_longo = 0
    n_num_mon = 0
    for c in linha:
        s = str(c or "").strip()
        if not s:
            continue
        if len(s) > 40:
            n_texto_longo += 1
        # Numérica/monetária: contém dígito e não é só texto
        elif any(ch.isdigit() for ch in s) and len(s) <= 20:
            n_num_mon += 1
    return n_texto_longo >= 1 or n_num_mon >= 2


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
