"""Rascunhos de pregões em andamento — autosave + recuperação, VÁRIOS por vez.

O trabalho sobre um edital (itens extraídos, BUSCA DE PREÇOS, seleções, markup)
vive em `st.session_state` (memória). Um apagão / fechar o app ANTES de gerar a
planilha perderia tudo — e o mais caro de perder é a busca de preços (minutos de
scraping + login limitado). Aqui gravamos um snapshot em disco a cada mudança e
a UI oferece retomá-lo.

MULTI-PREGÃO (2026-07-06, pedido do cliente): o operador trabalha em VÁRIOS
editais ao mesmo tempo (ex.: um de 1000 itens em curso quando chega outro).
Cada pregão tem SEU arquivo em `data/rascunhos/<slug>.pkl` (slug derivado de
órgão+número); trocar de pregão GUARDA o atual em vez de descartá-lo. O formato
antigo (slot único `data/rascunho.pkl`) é migrado automaticamente no 1º boot.

Decisões de robustez:
- Escrita ATÔMICA (arquivo temporário + os.replace): um apagão NO MEIO da
  gravação nunca deixa o rascunho corrompido — ou está o antigo inteiro, ou o
  novo inteiro.
- Carregamento BLINDADO: rascunho ausente, corrompido ou de um formato
  incompatível vira None — o boot NUNCA trava por causa dele (mesma filosofia do
  banco corrompido em app._abrir_banco). `listar_pregoes` pula arquivos ruins.
- `_VERSAO` invalida snapshots de formatos antigos (ex.: dataclass mudou) sem
  quebrar nada — o operador só perde aquele rascunho, não o app.
"""
from __future__ import annotations

import os
import pickle
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Optional

from pregoes import config

CAMINHO = config.DATA_DIR / "rascunho.pkl"      # formato ANTIGO (slot único)
DIR = config.DATA_DIR / "rascunhos"             # multi-pregão: 1 arquivo/pregão
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


# === Multi-pregão: um rascunho por (órgão, número) ==========================

def slug_de(orgao, numero) -> str:
    """Nome de arquivo estável e seguro derivado da identidade do pregão.
    Sem identificação ainda (PDF recém-subido) → slot 'sem-identificacao'
    (um por vez — vira o slug real assim que órgão/número aparecem)."""
    base = f"{numero or ''} {orgao or ''}".strip().lower()
    base = unicodedata.normalize("NFD", base)
    base = "".join(ch for ch in base if unicodedata.category(ch) != "Mn")
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base[:60] or "sem-identificacao"


def _dir(diretorio: Optional[Path]) -> Path:
    return Path(diretorio) if diretorio else DIR


def salvar_pregao(estado: dict, diretorio: Optional[Path] = None) -> str:
    """Grava o snapshot no arquivo do PREGÃO (slug por órgão+número do próprio
    estado) e devolve o slug. Atômico e sem levantar (como `salvar`)."""
    slug = slug_de(estado.get("orgao"), estado.get("numero"))
    salvar(estado, _dir(diretorio) / f"{slug}.pkl")
    return slug


def carregar_pregao(slug: str, diretorio: Optional[Path] = None) -> Optional[dict]:
    return carregar(_dir(diretorio) / f"{slug}.pkl")


def remover_pregao(slug: str, diretorio: Optional[Path] = None) -> None:
    limpar(_dir(diretorio) / f"{slug}.pkl")


def _migrar_legado(diretorio: Path) -> None:
    """Formato antigo (slot único rascunho.pkl) → arquivo por pregão. Roda no
    1º `listar_pregoes` após a atualização; nunca levanta."""
    try:
        estado = carregar(CAMINHO)
        if estado:
            salvar(estado, diretorio / f"{slug_de(estado.get('orgao'), estado.get('numero'))}.pkl")
        limpar(CAMINHO)
    except Exception:
        pass


def listar_pregoes(diretorio: Optional[Path] = None) -> list[dict]:
    """Rascunhos recuperáveis, mais recente primeiro. Cada entrada:
    ``{slug, orgao, numero, n_itens, tem_busca, mtime}``. Arquivos corrompidos
    ou de versão incompatível são PULADOS (nunca travam a listagem)."""
    d = _dir(diretorio)
    if CAMINHO.exists():
        _migrar_legado(d)
    if not d.is_dir():
        return []
    out: list[dict] = []
    for arq in d.glob("*.pkl"):
        estado = carregar(arq)
        if not estado:
            continue
        itens = estado.get("itens_edital") or estado.get("itens_revisados") or []
        out.append({
            "slug": arq.stem,
            "orgao": estado.get("orgao"),
            "numero": estado.get("numero"),
            "n_itens": len(itens),
            "tem_busca": bool(estado.get("resultados_busca")),
            "mtime": arq.stat().st_mtime,
        })
    out.sort(key=lambda p: p["mtime"], reverse=True)
    return out
