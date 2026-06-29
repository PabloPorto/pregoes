"""Rascunho do pregão em andamento — autosave + recuperação após queda.

O trabalho sobre um edital (itens extraídos, BUSCA DE PREÇOS, seleções, markup)
vive em `st.session_state` (memória). Um apagão / fechar o app ANTES de gerar a
planilha perderia tudo — e o mais caro de perder é a busca de preços (minutos de
scraping + login limitado). Aqui gravamos um snapshot em disco a cada mudança e a
UI oferece restaurá-lo no boot.

Decisões de robustez:
- Escrita ATÔMICA (arquivo temporário + os.replace): um apagão NO MEIO da
  gravação nunca deixa o rascunho corrompido — ou está o antigo inteiro, ou o
  novo inteiro.
- Carregamento BLINDADO: rascunho ausente, corrompido ou de um formato
  incompatível vira None — o boot NUNCA trava por causa dele (mesma filosofia do
  banco corrompido em app._abrir_banco).
- `_VERSAO` invalida snapshots de formatos antigos (ex.: dataclass mudou) sem
  quebrar nada — o operador só perde o rascunho daquela sessão, não o app.
"""
from __future__ import annotations

import os
import pickle
import tempfile
from pathlib import Path
from typing import Optional

from pregoes import config

CAMINHO = config.DATA_DIR / "rascunho.pkl"
_VERSAO = 1


def salvar(estado: dict, caminho: Optional[Path] = None) -> None:
    """Grava o snapshot de forma ATÔMICA. NUNCA levanta (autosave não pode
    derrubar a UI nem corromper nada)."""
    caminho = Path(caminho) if caminho else CAMINHO
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(caminho.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                pickle.dump({"versao": _VERSAO, "estado": estado}, f,
                            protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, caminho)   # troca atômica
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception:
        pass


def carregar(caminho: Optional[Path] = None) -> Optional[dict]:
    """Devolve o `estado` salvo, ou None se não há rascunho / está corrompido /
    é de um formato incompatível. Nunca levanta."""
    caminho = Path(caminho) if caminho else CAMINHO
    try:
        if not caminho.exists():
            return None
        with open(caminho, "rb") as f:
            payload = pickle.load(f)
        if not isinstance(payload, dict) or payload.get("versao") != _VERSAO:
            return None
        estado = payload.get("estado")
        return estado if isinstance(estado, dict) and estado else None
    except Exception:
        return None


def existe(caminho: Optional[Path] = None) -> bool:
    """True só se há um rascunho VÁLIDO e recuperável (carrega de verdade)."""
    return carregar(caminho) is not None


def limpar(caminho: Optional[Path] = None) -> None:
    """Remove o rascunho. Nunca levanta."""
    caminho = Path(caminho) if caminho else CAMINHO
    try:
        caminho.unlink(missing_ok=True)
    except Exception:
        pass
