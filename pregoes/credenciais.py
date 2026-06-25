"""
Leitura e escrita de credenciais dos sites de fornecedores.

Decisão Q8 (2026-05-15):
- Arquivo único `data/credenciais.json`, gitignored.
- Estrutura: { "almeida": { "email": "...", "senha": "..." }, "dantas": { ... } }
- A UI Streamlit ("Configurações") é o único ponto que escreve neste arquivo.
- O scraper só lê.

Segurança: senha fica em texto puro no disco do cliente. É equivalente a
um cookie de navegador salvo — mesma confiança. Máquina local, single-user
(ou dupla — os 2 operadores compartilham, Resp 3+12).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, TypedDict

from . import config


CAMINHO = config.DATA_DIR / "credenciais.json"


class Credencial(TypedDict):
    email: str
    senha: str


def carregar(fornecedor: str) -> Optional[Credencial]:
    """
    Retorna `{email, senha}` se houver credencial salva para o fornecedor.
    Retorna None se o arquivo não existe ou não tem entrada para esse nome.
    """
    if not CAMINHO.exists():
        return None
    try:
        dados = json.loads(CAMINHO.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        # Arquivo corrompido: JSON inválido OU bytes não-UTF-8 (cliente editou/
        # salvou em outra codificação — visto no Windows) → trata como sem
        # credencial em vez de derrubar o app.
        return None
    cred = dados.get(fornecedor)
    if not cred or not cred.get("email") or not cred.get("senha"):
        return None
    return {"email": cred["email"], "senha": cred["senha"]}


def salvar(fornecedor: str, email: str, senha: str) -> None:
    """
    Persiste credencial. Cria o arquivo se não existir. Sobrescreve a
    entrada do fornecedor se já existir; preserva outras entradas.
    """
    if CAMINHO.exists():
        try:
            dados = json.loads(CAMINHO.read_text(encoding="utf-8"))
            if not isinstance(dados, dict):
                dados = {}
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            dados = {}
    else:
        dados = {}

    dados[fornecedor] = {"email": email, "senha": senha}
    CAMINHO.parent.mkdir(parents=True, exist_ok=True)
    CAMINHO.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")


def remover(fornecedor: str) -> None:
    """Apaga a credencial de um fornecedor. Útil em testes e troca de conta."""
    if not CAMINHO.exists():
        return
    try:
        dados = json.loads(CAMINHO.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return
    dados.pop(fornecedor, None)
    CAMINHO.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")


# === Chaves de API (Gemini, futuros provedores) ===
# Formato no mesmo arquivo: { "gemini": { "api_key": "..." }, ... }
# Estrutura distinta de email/senha para evitar confusão semântica;
# pequeno preço para clareza de intenção em vez de reciclar Credencial.


def carregar_chave_api(provider: str) -> Optional[str]:
    """Retorna a string da chave API de um provedor, ou None se não houver."""
    if not CAMINHO.exists():
        return None
    try:
        dados = json.loads(CAMINHO.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    cred = dados.get(provider)
    if not cred:
        return None
    chave = cred.get("api_key")
    return chave if chave else None


# === Condição de pagamento por fornecedor (Forma + Prazo) ===
# O preço nos sites varia conforme a condição de pagamento. Guardamos a escolha
# do operador (value para o scraper selecionar + texto para exibir) por fornecedor,
# numa seção própria do mesmo arquivo. Estrutura:
#   { "preferencias_pagamento": { "almeida": {forma_value, forma_texto,
#                                              prazo_value, prazo_texto}, ... } }
_CHAVE_PREF = "preferencias_pagamento"


def carregar_preferencia_pagamento(fornecedor: str) -> Optional[dict]:
    """Retorna `{forma_value, forma_texto, prazo_value, prazo_texto}` da condição
    de pagamento configurada para o fornecedor, ou None se não houver."""
    if not CAMINHO.exists():
        return None
    try:
        dados = json.loads(CAMINHO.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    pref = (dados.get(_CHAVE_PREF) or {}).get(fornecedor)
    if not pref or not pref.get("forma_value") or not pref.get("prazo_value"):
        return None
    return pref


def salvar_preferencia_pagamento(
    fornecedor: str, forma_value: str, forma_texto: str,
    prazo_value: str, prazo_texto: str,
) -> None:
    """Persiste a condição de pagamento escolhida. Preserva as outras entradas
    (credenciais, chaves de API, o outro fornecedor)."""
    if CAMINHO.exists():
        try:
            dados = json.loads(CAMINHO.read_text(encoding="utf-8"))
            if not isinstance(dados, dict):
                dados = {}
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            dados = {}
    else:
        dados = {}

    dados.setdefault(_CHAVE_PREF, {})[fornecedor] = {
        "forma_value": forma_value, "forma_texto": forma_texto,
        "prazo_value": prazo_value, "prazo_texto": prazo_texto,
    }
    CAMINHO.parent.mkdir(parents=True, exist_ok=True)
    CAMINHO.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")


def salvar_chave_api(provider: str, api_key: str) -> None:
    """Persiste uma chave API. Cria o arquivo se não existir, sobrescreve a
    entrada do provider, preserva outras entradas."""
    if CAMINHO.exists():
        try:
            dados = json.loads(CAMINHO.read_text(encoding="utf-8"))
            if not isinstance(dados, dict):
                dados = {}
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            dados = {}
    else:
        dados = {}

    dados[provider] = {"api_key": api_key}
    CAMINHO.parent.mkdir(parents=True, exist_ok=True)
    CAMINHO.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
