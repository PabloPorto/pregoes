"""
Sinais de re-ranking para a SELEÇÃO DO DEFAULT (qual candidato fica
pré-selecionado na tela de busca). Puro e testável — sem Streamlit nem DB.

Por que existe: o default era ordenado só por relevância textual + preço, e
ignorava o código/modelo que o edital pede. Ex.: "CILINDRO ... 803/1401"
defaultava no SOPRANO (relevância alta, mas sem o 803) em vez do candidato que
carrega o 803. Este módulo dá o sinal "casou o código forte do edital", usado
como 1º critério de ordenação. É deliberadamente CONSERVADOR: só dispara com
casamento literal de número >= 3 dígitos; sem isso, o bônus é 0 para todos os
candidatos e a ordenação cai exatamente no comportamento anterior.
"""
from __future__ import annotations

import re

_RE_NUM = re.compile(r"\d+")


def codigos_fortes(texto: str | None) -> set[str]:
    """Tokens numéricos DISTINTIVOS (>= 3 dígitos) — códigos/modelos do edital
    ('803', '1401', '750'). Ignora 1-2 dígitos (quantidade, '10A', '52mm'),
    que casariam por acaso e gerariam ruído de ordenação."""
    return {n for n in _RE_NUM.findall(texto or "") if len(n) >= 3}


def bonus_codigo(item_desc: str | None, cand_desc: str | None) -> int:
    """Quantos códigos fortes do edital aparecem no candidato.

    Sinal forte e RARO: só é > 0 quando há casamento literal de modelo/código
    (>= 3 dígitos) entre edital e candidato. Quando NENHUM candidato casa, o
    bônus é 0 para todos e a ordenação do default fica idêntica à anterior
    (relevância, depois preço) — mudança nula fora desses casos.
    """
    return len(codigos_fortes(item_desc) & codigos_fortes(cand_desc))
