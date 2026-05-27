"""
Reconstrução de tabelas multi-página fragmentadas pelo Docling.

Docling 2.7 fragmenta uma tabela visual única que quebra entre páginas
em N tabelas separadas. A primeira (base) costuma preservar o cabeçalho;
as continuações vêm com cabeçalho vazio ou sequência numérica
('0','1','2'...). O número de colunas pode variar entre fatias quando
Docling perde colunas vazias.

Este módulo:
1. Encontra a "tabela base" (primeira com cabeçalho casando palavras-chave).
2. Identifica fatias seguintes que parecem continuação:
   - Cabeçalho vazio ou sequência '0','1','2'..., E
   - Número de colunas dentro de uma tolerância da base (±3).
3. Alinha colunas:
   - Mesmo nº cols → identidade (caso fácil).
   - Diferente → alinhamento por perfil de conteúdo
     (monetário, numérico, texto longo) com correspondência greedy.
4. Funde linhas da fatia na tabela base.

Conservadorismo:
- Se não encontrar base com cabeçalho válido, devolve entrada inalterada.
- Se aparecer uma nova tabela com cabeçalho válido distinto, para de absorver
  (começou uma nova seção).
- Se a fatia tem nº de colunas radicalmente diferente (>3 de diferença),
  não é absorvida.
- Saída sempre contém TODAS as tabelas originais — fatias absorvidas
  somem da lista; tabelas que sobraram fora ficam intactas.

Heurísticas calibradas para os PDFs reais observados (TRE-PE, SEI/HU,
Hidráulicos). Quando aparecer formato novo que quebra a heurística,
revisitar os thresholds.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from .extrator import (
    _eh_cabecalho_valido,
    _identifica_cabecalho_em_linha,
    _identifica_cabecalho_multilinha,
    _normaliza,
)


# Padrões para detectar conteúdo de células
_MONETARIO_RE = re.compile(r"R\$|^\s*-?\d{1,3}(?:\.\d{3})*,\d{2}\s*$")
_NUMERICO_RE = re.compile(r"^\s*-?\d+(?:[.,]\d+)?\s*$")

# Tolerância de variação de colunas entre fatias e a base.
# 3 cobre os casos observados no PDF 03 (TRE-PE): base 10 cols,
# fatias com 8, 9, 10 cols. Acima de 3 já é uma tabela diferente.
_TOLERANCIA_COLS = 3


def reconstruir(tabelas: list[list[list[str]]]) -> list[list[list[str]]]:
    """Funde fatias-continuação Docling na tabela base.

    Entrada e saída são lista de tabelas. Cada tabela é lista de linhas;
    cada linha é lista de células-string. A primeira linha é o cabeçalho.

    Comportamento:
    - Se não houver tabela base com cabeçalho válido, retorna entrada
      inalterada (não inventa estrutura).
    - Fatias absorvidas desaparecem da lista de retorno; a tabela base
      ganha as linhas correspondentes.
    - Tabelas que não parecem continuação (ex: tabelas isoladas em
      seções diferentes do PDF) ficam intactas.
    """
    if not tabelas:
        return tabelas

    idx_base = _encontrar_tabela_base(tabelas)
    if idx_base is None:
        return tabelas

    base = tabelas[idx_base]
    base_n_cols = _n_cols(base)
    base_dados = base[1:] if len(base) > 1 else []

    # Perfis das colunas da base, usando linhas de dados
    perfis_base = _perfis_colunas(base_dados, base_n_cols)

    # Caminha pelas tabelas SEGUINTES tentando absorver
    saida: list[list[list[str]]] = list(tabelas[: idx_base + 1])
    linhas_extras: list[list[str]] = []

    for i in range(idx_base + 1, len(tabelas)):
        candidata = tabelas[i]
        if not candidata or _n_cols(candidata) == 0:
            saida.append(candidata)
            continue

        # Verifica se candidata começa uma NOVA seção (cabeçalho válido distinto)
        mapa_cand, _ = _identifica_cabecalho_multilinha(candidata)
        if _eh_cabecalho_valido(mapa_cand):
            # Para de absorver — outra tabela legítima começou
            saida.extend(tabelas[i:])
            break

        if not _parece_continuacao(candidata, base_n_cols, perfis_base):
            saida.append(candidata)
            continue

        alinhamento = _alinhar_colunas(candidata, perfis_base, base_n_cols)
        if alinhamento is None:
            saida.append(candidata)
            continue

        # Decide se a primeira linha da fatia é cabeçalho sentinela
        # do Docling ('0','1','2'... / vazio) ou já um dado real.
        # Ordem importa: cabeçalho fake (sequencial) tem perfil
        # 'num_curto×N' que casaria _eh_linha_de_dado por engano.
        if _eh_cabecalho_fake_docling(candidata[0]):
            linhas_para_realinhar = candidata[1:] if len(candidata) > 1 else []
        elif _eh_linha_de_dado(candidata[0]):
            linhas_para_realinhar = candidata
        else:
            linhas_para_realinhar = candidata[1:] if len(candidata) > 1 else []
        linhas_realinhadas = _realinhar(linhas_para_realinhar, alinhamento, base_n_cols)
        linhas_extras.extend(linhas_realinhadas)
        # Fatia foi absorvida — não entra em `saida`

    if linhas_extras:
        # Adiciona linhas extras à tabela base na saída
        saida[idx_base] = saida[idx_base] + linhas_extras

    return saida


def _encontrar_tabela_base(tabelas: list[list[list[str]]]) -> Optional[int]:
    """Primeira tabela com cabeçalho casando palavras-chave (descrição + qtd)."""
    for idx, t in enumerate(tabelas):
        if not t or len(t) < 2:
            continue
        mapa, _ = _identifica_cabecalho_multilinha(t)
        if _eh_cabecalho_valido(mapa):
            return idx
    return None


def _n_cols(tabela: list[list[str]]) -> int:
    """Maior número de colunas observado entre as linhas."""
    return max((len(l) for l in tabela if l), default=0)


def _parece_continuacao(
    candidata: list[list[str]],
    base_n_cols: int,
    perfis_base: Optional[list[str]] = None,
) -> bool:
    """Heurísticas combinadas para "fatia órfã sem cabeçalho próprio".

    Critérios (combinados como OR — qualquer um basta):
    A. Cabeçalho é sequência '0','1','2'... (Docling falhou em detectar header)
    B. Cabeçalho é todo vazio
    C. Primeira linha não casa palavras-chave de cabeçalho
    D. Primeira linha tem perfil de DADO compatível com a base
       (quando passamos `perfis_base`). Cobre caso em que Docling
       começa fatia direto com dados como ['15', 'Adaptador...', ...] —
       a célula 'Unidade' atrapalha critério C porque é palavra-chave,
       mas o conjunto da linha é claramente um item, não um cabeçalho.
    """
    n_cols_cand = _n_cols(candidata)
    if abs(n_cols_cand - base_n_cols) > _TOLERANCIA_COLS:
        return False

    primeira = candidata[0]
    nao_vazios = [c for c in primeira if c and str(c).strip()]

    # (A) Cabeçalho é sequência numérica '0','1','2'... — sinal forte
    #     de que Docling não detectou cabeçalho real.
    if nao_vazios and all(
        _normaliza(c).isdigit() and int(_normaliza(c)) == i
        for i, c in enumerate(primeira)
        if c is not None and str(c).strip()
    ):
        return True

    # (B) Cabeçalho todo vazio
    if not nao_vazios:
        return True

    # (C) Primeira linha não casa palavras-chave conhecidas — ou seja,
    #     não é um cabeçalho "de verdade", provavelmente é um dado.
    mapa = _identifica_cabecalho_em_linha(primeira)
    if not mapa:
        return True

    # (D) Perfil da primeira linha é compatível com perfil da base.
    #     Casos como ['15', 'Adaptador de rede', ..., 'Unidade', '3', ..., 'R$ 43,44']
    #     têm "Unidade" como palavra-chave (atrapalha critério C) mas claramente
    #     são DADOS, não cabeçalho — a maioria das colunas tem perfil numérico/
    #     monetário casando com a base.
    if perfis_base:
        perfis_primeira = [_perfil_celula(c) for c in primeira]
        # Conta colunas compatíveis (ignora 'vazio' nos dois lados)
        compativeis = 0
        total = 0
        for j, pf in enumerate(perfis_primeira):
            if j >= len(perfis_base):
                break
            if pf == "vazio" or perfis_base[j] == "vazio":
                continue
            total += 1
            if _compativel(pf, perfis_base[j]):
                compativeis += 1
        # Se ≥60% das colunas não-vazias batem perfil, primeira linha é DADO
        if total >= 3 and compativeis / total >= 0.6:
            return True

    return False


def _perfil_celula(cel: object) -> str:
    """Classifica uma célula em um dos tipos:
    'vazio', 'monetario', 'num_curto', 'num_longo', 'texto_curto', 'texto_longo'."""
    s = "" if cel is None else str(cel).strip()
    if not s:
        return "vazio"
    if _MONETARIO_RE.search(s):
        return "monetario"
    s_compacto = s.replace(" ", "")
    if _NUMERICO_RE.match(s_compacto):
        digitos = re.sub(r"[^0-9]", "", s_compacto)
        return "num_longo" if len(digitos) >= 5 else "num_curto"
    return "texto_longo" if len(s) > 40 else "texto_curto"


def _perfis_colunas(linhas_dados: list[list[str]], n_cols: int) -> list[str]:
    """Tipo dominante (excluindo 'vazio') de cada coluna nas linhas de dados."""
    perfis = []
    for c in range(n_cols):
        amostra = [
            linhas_dados[i][c] if c < len(linhas_dados[i] or []) else ""
            for i in range(len(linhas_dados))
        ]
        nao_vazios = [_perfil_celula(v) for v in amostra if (v or "").strip()]
        if not nao_vazios:
            perfis.append("vazio")
        else:
            perfis.append(Counter(nao_vazios).most_common(1)[0][0])
    return perfis


_PERFIS_COMPATIVEIS = {
    "texto_curto": {"texto_curto", "texto_longo"},
    "texto_longo": {"texto_curto", "texto_longo"},
    "num_curto": {"num_curto"},
    "num_longo": {"num_longo", "num_curto"},
    "monetario": {"monetario"},
    "vazio": {"vazio", "texto_curto", "texto_longo", "num_curto", "num_longo", "monetario"},
}


def _compativel(perfil_a: str, perfil_b: str) -> bool:
    """Dois perfis são compatíveis (podem ser a mesma coluna semântica)."""
    return perfil_b in _PERFIS_COMPATIVEIS.get(perfil_a, set())


def _alinhar_colunas(
    fatia: list[list[str]],
    perfis_base: list[str],
    base_n_cols: int,
) -> Optional[dict[int, int]]:
    """Mapa {idx_fatia → idx_base}, ou None se não foi possível alinhar.

    - Mesmo nº cols → identidade.
    - Caso médio (diferença ≤ tolerância): alinhamento greedy preservando
      ordem (idx_fatia[k] não pode mapear para idx_base[<k_anterior]).
    """
    fatia_n_cols = _n_cols(fatia)
    if fatia_n_cols == 0:
        return None
    if fatia_n_cols == base_n_cols:
        return {i: i for i in range(fatia_n_cols)}

    perfis_fatia = _perfis_colunas(fatia, fatia_n_cols)

    # Alinhamento greedy preservando ordem
    mapa: dict[int, int] = {}
    j = 0
    for i, pf in enumerate(perfis_fatia):
        if pf == "vazio":
            # Coluna toda vazia — não força alinhar, simplesmente pula
            # mas avança o índice da base proporcionalmente
            continue
        avancou = False
        while j < base_n_cols:
            if _compativel(pf, perfis_base[j]):
                mapa[i] = j
                j += 1
                avancou = True
                break
            j += 1
        if not avancou:
            # Coluna sem correspondente na base
            continue

    return mapa if mapa else None


def _eh_cabecalho_fake_docling(linha: list[str]) -> bool:
    """True para cabeçalho sentinela típico Docling — sempre descartar.

    Padrões cobertos:
    - Todo vazio: ``['', '', '']``
    - Sequência numérica monotônica: ``['0', '1', '2', '3']``
    """
    if not linha:
        return True
    nao_vazios = [c for c in linha if c and str(c).strip()]
    if not nao_vazios:
        return True
    # Sequência '0','1','2',... (Docling não detectou cabeçalho real)
    if all(
        _normaliza(c).isdigit() and int(_normaliza(c)) == i
        for i, c in enumerate(linha)
        if c is not None and str(c).strip()
    ):
        return True
    return False


def _eh_linha_de_dado(linha: list[str]) -> bool:
    """True se a linha tem perfil de dado real (item de edital).

    Cabeçalhos sentinela Docling devem ser filtrados ANTES via
    ``_eh_cabecalho_fake_docling`` — esta função aceita o cabeçalho
    '0','1','2'... como "dado" porque vê 10 numéricos.

    Critério: ≥1 texto longo OU ≥2 numéricos/monetários.
    """
    if not linha:
        return False
    perfis = [_perfil_celula(c) for c in linha]
    n_texto_longo = sum(1 for p in perfis if p == "texto_longo")
    n_num_mon = sum(1 for p in perfis if p in ("num_curto", "num_longo", "monetario"))
    return n_texto_longo >= 1 or n_num_mon >= 2


def _realinhar(
    fatia: list[list[str]],
    alinhamento: dict[int, int],
    base_n_cols: int,
) -> list[list[str]]:
    """Aplica o mapa de alinhamento, padding com strings vazias.

    Linhas inteiramente vazias após realinhamento são descartadas — são
    fragmentos visuais que Docling capturou mas não contêm item real.
    """
    out: list[list[str]] = []
    for linha in fatia:
        nova = [""] * base_n_cols
        for i_fatia, valor in enumerate(linha or []):
            j_base = alinhamento.get(i_fatia)
            if j_base is not None and 0 <= j_base < base_n_cols:
                nova[j_base] = "" if valor is None else str(valor)
        # Descarta linhas cujo conteúdo é totalmente vazio
        if any(c.strip() for c in nova):
            out.append(nova)
    return out
