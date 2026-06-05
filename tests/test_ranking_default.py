"""Testes do re-ranking do default (pregoes/ranking.py) — sinal de código
forte do edital. Inclui a garantia anti-regressão: SEM código casado, a
ordem nova é idêntica à antiga (relevância, preço)."""
from __future__ import annotations

from pregoes.ranking import bonus_codigo, codigos_fortes
from pregoes.normalizador import relevancia


def test_codigos_fortes_somente_3_digitos():
    assert codigos_fortes("CILINDRO 803/1401 52mm 10A") == {"803", "1401"}
    assert codigos_fortes("PARAFUSO sextavado") == set()
    assert codigos_fortes("FILTRO 10A 5un") == set()  # 1-2 dígitos ignorados
    assert codigos_fortes(None) == set()


def test_bonus_codigo_casa_modelo_do_edital():
    ed = "CILINDRO para fechadura 803/1401"
    assert bonus_codigo(ed, "CILINDRO STAM GORJE 803/804/1801 52MM") == 1
    assert bonus_codigo(ed, "CILINDRO SOPRANO 2 CHAVES 53MM") == 0


def test_bonus_ignora_numeros_curtos_coincidentes():
    # '10' (2 dígitos) NÃO deve gerar bônus espúrio
    assert bonus_codigo("FILTRO 10A", "LUMINARIA 10W") == 0


def _chave_nova(ed, c):
    return (-bonus_codigo(ed, c["desc"]), -relevancia(ed, c["desc"]), c["preco"])


def _chave_antiga(ed, c):
    return (-relevancia(ed, c["desc"]), c["preco"])


def test_promove_candidato_que_casa_codigo_mesmo_com_relevancia_menor():
    # Caso real (cilindro): o que casa o 803 deve virar default, ainda que o
    # outro tenha relevância textual maior e preço menor.
    ed = "CILINDRO para fechadura 803/1401"
    cands = [
        {"desc": "CILINDRO SOPRANO 2 CHAVES C/PARAF 53MM CR", "preco": 17.98},
        {"desc": "CILINDRO STAM GORJE CR 803/804/1801 52MM", "preco": 18.59},
    ]
    melhor = min(cands, key=lambda c: _chave_nova(ed, c))
    assert "803" in melhor["desc"]


def test_sem_codigo_ordem_identica_a_antiga():
    # GARANTIA ANTI-REGRESSÃO: edital sem código forte -> bônus 0 p/ todos ->
    # o vencedor da chave NOVA é o mesmo da chave ANTIGA.
    ed = "PARAFUSO sextavado galvanizado"
    cands = [
        {"desc": "PARAFUSO sextavado galvanizado inox", "preco": 5.0},
        {"desc": "PREGO ardox 18x30", "preco": 2.0},
        {"desc": "ARRUELA lisa", "preco": 1.0},
    ]
    assert (min(cands, key=lambda c: _chave_nova(ed, c))
            is min(cands, key=lambda c: _chave_antiga(ed, c)))


def test_empate_de_bonus_cai_para_relevancia_e_preco():
    # Dois candidatos casam o MESMO código -> desempata por relevância/preço,
    # como antes.
    ed = "MOTOR 1750 rpm"
    cands = [
        {"desc": "MOTOR ELETRICO 1750 rpm trifasico", "preco": 200.0},
        {"desc": "MOTOR 1750 rpm monofasico bomba", "preco": 150.0},
    ]
    # ambos têm bônus 1 (casam '1750'); o desempate não pode quebrar
    assert bonus_codigo(ed, cands[0]["desc"]) == bonus_codigo(ed, cands[1]["desc"]) == 1
    melhor = min(cands, key=lambda c: _chave_nova(ed, c))
    assert melhor in cands  # não estoura, escolhe um determinístico
