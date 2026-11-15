"""Regras da revisão dos itens e do mapeamento manual — funções PURAS.

Trazidas de `pregoes/ui/app.py` (inventário seções 6.2 e 6.12), uma a uma, com
a linha de origem no docstring. Nenhuma função daqui lê ou grava disco, nem
conhece `Pregao`: recebem e devolvem listas de `ItemExtraido` — quem persiste
é `rotas_revisao.py`.

Decisões de desenho (diferenças deliberadas em relação ao Streamlit):

* A grade edita `itens_edital` DIRETO, célula a célula. No Streamlit a grade
  era um DataFrame fora do rascunho: reabrir o pregão perdia as edições e as
  marcações (inventário 3.3, último item).
* Editar um item preserva `codigo_orgao` e `pagina` do original (o Streamlit
  recriava o item e os descartava, `_linha_para_item` L750). A `confianca`
  vira "extraido" quando o operador mexe no item — foi conferido por gente.
* Números aceitam o formato brasileiro ("1.500,50") reusando
  `extrator._para_numero`, o mesmo conversor da leitura do PDF.
"""
from __future__ import annotations

import dataclasses
import unicodedata
from typing import Any, Optional

from pregoes import mapeamento_manual
from pregoes.extrator import ItemExtraido, _para_numero

# Campos de um item que a grade edita (nome do campo = nome do <input>).
CAMPOS_EDITAVEIS = ("item_numero", "descricao", "unidade", "quantidade", "valor_referencia")

_ROTULO_CAMPO = {
    "item_numero": "Número do item",
    "quantidade": "Quantidade",
    "valor_referencia": "Valor de referência",
}

# Obrigatórios do mapeamento manual (app.py L3569).
OBRIGATORIOS_MAPA = ("descricao", "quantidade")


class EntradaInvalida(ValueError):
    """O que o operador digitou não serve para o campo. A mensagem já está em
    português, pronta para a tela."""


# --------------------------------------------------------------------------
# Confiança da extração
# --------------------------------------------------------------------------

def selo_confianca(conf: float) -> tuple[str, str, str]:
    """`(rótulo, tipo, complemento)` do aviso de confiança.

    Porta de `style.confianca_para_badge` (style.py L608) + os três textos de
    `tela_revisao` (app.py L573-580). `tipo` já é a classe de `app.css`
    (success→ok, warning→alerta, danger→erro)."""
    if conf >= 0.95:
        return (f"Alta confiança · {conf:.0%}", "ok", "pode seguir para precificar.")
    if conf >= 0.80:
        return (f"Confiança média · {conf:.0%}", "alerta",
                "revise com atenção antes de gerar a planilha.")
    if conf > 0:
        return (f"Baixa confiança · {conf:.0%}", "erro",
                "recomendado usar o mapeamento manual.")
    return ("Sem itens detectados", "erro", "recomendado usar o mapeamento manual.")


def limpar_aviso(texto: Any) -> str:
    """Tira o emoji decorativo do começo de um aviso do backend ("✨ Mapa
    aprendido…", "⚠️ …") — a interface nova não usa emoji; o tipo do aviso já
    é dado pelo ícone e pela cor."""
    s = str(texto or "")
    i = 0
    while i < len(s) and (s[i].isspace() or unicodedata.category(s[i]) in ("So", "Sk", "Mn", "Cf")):
        i += 1
    return s[i:] or s


# --------------------------------------------------------------------------
# Grade de itens
# --------------------------------------------------------------------------

def numero_para_campo(valor: Optional[float], casas_min: int = 0) -> str:
    """Número no padrão brasileiro para um campo EDITÁVEL: 1500.5 → "1.500,5".

    Diferente do filtro `num` (que arredonda para 2 casas): aqui nada pode ser
    arredondado na ida — o que aparece no campo é o que volta por
    `_para_numero` se o operador salvar de novo. Até 4 casas, sem zeros à
    direita além de `casas_min`."""
    if valor is None:
        return ""
    inteiro, _, decimais = f"{float(valor):,.4f}".partition(".")
    decimais = decimais.rstrip("0")
    if len(decimais) < casas_min:
        decimais = decimais.ljust(casas_min, "0")
    inteiro = inteiro.replace(",", ".")
    return f"{inteiro},{decimais}" if decimais else inteiro


def valor_do_campo(item: ItemExtraido, campo: str) -> str:
    """O texto que a grade mostra para `campo` de `item`."""
    if campo == "item_numero":
        return "" if item.item_numero is None else str(item.item_numero)
    if campo == "quantidade":
        return numero_para_campo(item.quantidade)
    if campo == "valor_referencia":
        return numero_para_campo(item.valor_referencia, casas_min=2)
    return str(getattr(item, campo) or "")


def _numero(campo: str, valor: Any) -> Optional[float]:
    texto = "" if valor is None else str(valor).strip()
    if not texto:
        return None
    n = _para_numero(texto)
    if n is None:
        raise EntradaInvalida(
            f"{_ROTULO_CAMPO[campo]}: “{texto[:30]}” não é um número. "
            "Use só algarismos, como 1.500,50.")
    if n < 0:
        raise EntradaInvalida(f"{_ROTULO_CAMPO[campo]} não pode ser negativo.")
    return n


def editar_item(item: ItemExtraido, campo: str, valor: Any) -> ItemExtraido:
    """Devolve uma CÓPIA de `item` com `campo` trocado pelo que o operador
    digitou. Levanta `EntradaInvalida` (mensagem pronta) se não servir.

    Regra de `_linha_para_item` (app.py L750): Item → int, Quantidade e Valor
    de referência → float, vazio → None (descrição vazia → ""). Aqui o resto
    do item (`codigo_orgao`, `pagina`) é preservado e a confiança vira
    "extraido". Cópia, não alteração no lugar: o objeto original também está
    em `extracao.itens`, que é o retrato do que foi LIDO do PDF."""
    if campo not in CAMPOS_EDITAVEIS:
        raise EntradaInvalida("Este campo não pode ser alterado.")
    if campo == "descricao":
        novo: Any = " ".join(str(valor or "").split())
    elif campo == "unidade":
        novo = " ".join(str(valor or "").split()) or None
    elif campo == "item_numero":
        n = _numero(campo, valor)
        if n is not None and n != int(n):
            raise EntradaInvalida("Número do item: use um número inteiro, como 12.")
        novo = None if n is None else int(n)
    else:
        novo = _numero(campo, valor)
    return dataclasses.replace(item, **{campo: novo, "confianca": "extraido"})


def conferido(item: ItemExtraido) -> ItemExtraido:
    """Item duvidoso que o operador marcou para buscar: foi conferido."""
    if item.confianca == "extraido":
        return item
    return dataclasses.replace(item, confianca="extraido")


def item_em_branco(itens: list[ItemExtraido]) -> ItemExtraido:
    """Linha nova para o fim da grade. O número já vem sugerido (o seguinte
    ao maior do edital) — o operador troca se quiser."""
    numeros = [i.item_numero for i in itens if i.item_numero is not None]
    return ItemExtraido(item_numero=(max(numeros) + 1) if numeros else None,
                        descricao="", confianca="extraido")


def marcados_apos_excluir(marcados: list[int], excluido: int) -> list[int]:
    """Posições marcadas depois de tirar a linha `excluido` do edital: a
    própria sai da lista e as que vinham depois recuam uma posição."""
    return [j - 1 if j > excluido else j for j in marcados if j != excluido]


def contagem(itens: list[ItemExtraido], marcados: set[int]) -> dict:
    """Números da tela: métricas e a linha "N de M itens marcados…".

    No Streamlit as métricas vinham da extração (`res.total_itens`, L584);
    aqui vêm da grade atual, que nasce igual à extração e acompanha o que o
    operador corrige (o "Duvidosos" diminui conforme ele confere)."""
    duvidosos = sum(1 for i in itens if i.confianca == "duvidoso")
    return {"total": len(itens), "marcados": len(marcados), "duvidosos": duvidosos,
            "ok": len(itens) - duvidosos}


def marcado_sem_descricao(itens: list[ItemExtraido], marcados: list[int]) -> Optional[int]:
    """Posição (no edital) do primeiro item marcado para busca que está sem
    descrição — não há o que procurar no fornecedor. None se todos têm."""
    return next((i for i in marcados if not (itens[i].descricao or "").strip()), None)


# --------------------------------------------------------------------------
# Mapeamento manual
# --------------------------------------------------------------------------

def n_colunas(tabela: list) -> int:
    """Colunas de uma tabela bruta = a maior linha (app.py L3490 / L3508)."""
    return max((len(linha) for linha in tabela or [] if linha), default=0)


def rotulo_tabela(idx: int, tabela: list) -> str:
    """Texto de cada opção do Passo 1 (app.py L3485-3495): colunas, linhas e
    a 1ª linha não vazia como amostra (6 células × 25 caracteres, até 120)."""
    if not tabela or not n_colunas(tabela):
        return f"Tabela #{idx + 1} (vazia)"
    primeira = next((r for r in tabela if r and any(c for c in r)), [])
    amostra = " | ".join(str(c)[:25] for c in primeira[:6] if c)[:120]
    return (f"Tabela #{idx + 1} · {n_colunas(tabela)} colunas · "
            f"{len(tabela)} linhas · {amostra}")


def previa_tabela(tabela: list, linhas: int = 5) -> list[list[str]]:
    """Primeiras linhas, completadas/cortadas para o nº de colunas da tabela
    (app.py L3513-3516)."""
    n = n_colunas(tabela)
    return [[("" if c is None else str(c)) for c in (list(linha or []) + [""] * n)[:n]]
            for linha in tabela[:linhas]]


def rotulos_colunas(tabela: list) -> list[str]:
    """Nome de cada coluna no Passo 2: a célula do cabeçalho (30 caracteres)
    ou "Col N" quando o cabeçalho é mais curto que a tabela (app.py L3541)."""
    cabecalho = tabela[0] if tabela else []
    return [(str(cabecalho[c])[:30] if c < len(cabecalho) and cabecalho[c] is not None
             else f"Col {c + 1}") for c in range(n_colunas(tabela))]


def campos_iniciais(n: int, mapa_salvo: Optional[dict]) -> list[str]:
    """Campo que cada coluna já traz escolhido: o do mapa salvo para este
    cabeçalho, senão "ignorar" (app.py L3544-3549)."""
    saida = []
    for c in range(n):
        campo = next((k for k, idx in (mapa_salvo or {}).items()
                      if idx == c and k in mapeamento_manual.CAMPOS), "ignorar")
        saida.append(campo)
    return saida


def montar_mapa(escolhas: list[str]) -> tuple[dict[str, int], list[int]]:
    """`(mapa {campo: coluna}, colunas que repetem um campo já usado)`.

    Campo repetido avisa e a ÚLTIMA coluna vence (app.py L3559-3563)."""
    mapa: dict[str, int] = {}
    repetidas: list[int] = []
    for c, campo in enumerate(escolhas):
        if campo == "ignorar":
            continue
        if campo in mapa:
            repetidas.append(c)
        mapa[campo] = c
    return mapa, repetidas


def obrigatorios_faltando(mapa: dict[str, int]) -> list[str]:
    """Rótulos dos campos obrigatórios que nenhuma coluna recebeu (L3569)."""
    return [mapeamento_manual.ROTULOS_CAMPOS[c] for c in OBRIGATORIOS_MAPA if c not in mapa]
