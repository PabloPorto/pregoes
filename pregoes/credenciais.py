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
    except json.JSONDecodeError:
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
        except json.JSONDecodeError:
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
    except json.JSONDecodeError:
        return
    dados.pop(fornecedor, None)
    CAMINHO.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
