"""Testes do extrator via LLM (Gemini).

Não chama a API real — usa mocks para validar:
- Parse de JSON (com/sem markdown wrapper, formatos válidos/inválidos)
- Conversão dict → ItemExtraido
- Disponibilidade (importação + key)
- credenciais.salvar_chave_api / carregar_chave_api round-trip
"""
from __future__ import annotations

import json
import pytest

from pregoes.extrator_llm import (
    _parse_json_lista,
    _dict_para_item,
)


# === _parse_json_lista ===

def test_parse_json_array_simples():
    s = '[{"descricao": "X", "quantidade": 1}]'
    assert _parse_json_lista(s) == [{"descricao": "X", "quantidade": 1}]


def test_parse_json_com_markdown_wrapper():
    s = '```json\n[{"descricao": "X"}]\n```'
    assert _parse_json_lista(s) == [{"descricao": "X"}]


def test_parse_json_com_prosa_antes_e_depois():
    """Gemini às vezes ignora 'sem prosa' — tolerar."""
    s = 'Aqui está o resultado:\n[{"descricao": "Y"}]\nEspero ter ajudado.'
    assert _parse_json_lista(s) == [{"descricao": "Y"}]


def test_parse_json_array_vazio():
    assert _parse_json_lista("[]") == []
    assert _parse_json_lista("```\n[]\n```") == []
    assert _parse_json_lista("") == []


def test_parse_json_invalido_levanta():
    with pytest.raises((ValueError, json.JSONDecodeError)):
        _parse_json_lista('{"descricao": "X"}')  # objeto, não array


def test_parse_json_sem_array_levanta():
    with pytest.raises(ValueError):
        _parse_json_lista("apenas texto, sem JSON algum")


# === _dict_para_item ===

def test_dict_para_item_caso_feliz():
    d = {
        "numero": 15,
        "descricao": "ABRAÇADEIRA DE NYLON 100MM",
        "unidade": "UN",
        "quantidade": 200,
        "valor_referencia": 1.45,
    }
    item = _dict_para_item(d, pagina=43)
    assert item is not None
    assert item.item_numero == 15
    assert item.descricao == "ABRAÇADEIRA DE NYLON 100MM"
    assert item.unidade == "UN"
    assert item.quantidade == 200.0
    assert item.valor_referencia == 1.45
    assert item.pagina == 43


def test_dict_para_item_descarta_sem_descricao():
    assert _dict_para_item({"quantidade": 1}, pagina=1) is None
    assert _dict_para_item({"descricao": "X"}, pagina=1) is None  # < 3 chars


def test_dict_para_item_aceita_numero_null():
    d = {"descricao": "ITEM SEM NÚMERO IDENTIFICADO", "quantidade": 5}
    item = _dict_para_item(d, pagina=1)
    assert item is not None
    assert item.item_numero is None


def test_dict_para_item_converte_numericos_de_string():
    """Gemini às vezes retorna numéricos como string."""
    d = {
        "descricao": "PARAFUSO",
        "quantidade": "100",
        "valor_referencia": "12,34",  # formato BR
    }
    item = _dict_para_item(d, pagina=1)
    assert item.quantidade == 100.0
    assert item.valor_referencia == 12.34


def test_dict_para_item_robusto_a_dict_lixo():
    assert _dict_para_item("não é dict", pagina=1) is None
    assert _dict_para_item(None, pagina=1) is None
    assert _dict_para_item([], pagina=1) is None


# === credenciais.salvar_chave_api / carregar_chave_api ===

def test_credenciais_chave_api_round_trip(tmp_path, monkeypatch):
    from pregoes import credenciais
    caminho = tmp_path / "creds.json"
    monkeypatch.setattr(credenciais, "CAMINHO", caminho)

    assert credenciais.carregar_chave_api("gemini") is None
    credenciais.salvar_chave_api("gemini", "AIzaSyFAKE-test-key-1234")
    assert credenciais.carregar_chave_api("gemini") == "AIzaSyFAKE-test-key-1234"


def test_credenciais_chave_api_preserva_outros_provedores(tmp_path, monkeypatch):
    """Salvar chave gemini não pode apagar credenciais Almeida/Dantas."""
    from pregoes import credenciais
    caminho = tmp_path / "creds.json"
    monkeypatch.setattr(credenciais, "CAMINHO", caminho)

    credenciais.salvar("almeida", "a@a.com", "senha_a")
    credenciais.salvar("dantas", "d@d.com", "senha_d")
    credenciais.salvar_chave_api("gemini", "AIza-chave")

    assert credenciais.carregar("almeida") == {"email": "a@a.com", "senha": "senha_a"}
    assert credenciais.carregar("dantas") == {"email": "d@d.com", "senha": "senha_d"}
    assert credenciais.carregar_chave_api("gemini") == "AIza-chave"


# === disponivel() ===

def test_disponivel_falso_sem_key(tmp_path, monkeypatch):
    from pregoes import credenciais, extrator_llm
    caminho = tmp_path / "creds.json"
    monkeypatch.setattr(credenciais, "CAMINHO", caminho)
    # google-genai está instalado no venv (Fase 2.7), mas sem key
    assert extrator_llm.disponivel() is False


def test_disponivel_verdadeiro_com_key(tmp_path, monkeypatch):
    # disponivel() exige o SDK google-genai instalado E uma chave. Sem o SDK
    # no ambiente (ex.: venv de dev enxuto), o correto é PULAR, não falhar —
    # a ausência do pacote é condição de ambiente, não regressão de código.
    pytest.importorskip("google.genai")
    from pregoes import credenciais, extrator_llm
    caminho = tmp_path / "creds.json"
    monkeypatch.setattr(credenciais, "CAMINHO", caminho)
    credenciais.salvar_chave_api("gemini", "AIzaFAKE")
    assert extrator_llm.disponivel() is True
