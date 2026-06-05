"""
Logging em arquivo (P0.3) — observabilidade para suporte remoto.

O sistema roda na máquina do cliente, sem o desenvolvedor por perto. Quando
algo falha, o stderr do Streamlit some ao fechar a janela. Este módulo
direciona os logs para `data/pregoes.log` (com rotação), para o cliente
poder enviar o arquivo e o desenvolvedor diagnosticar.

Uso:
    from pregoes import logconf
    logconf.configurar()                 # 1× no boot da UI
    log = logconf.get_logger("scraper")  # nos módulos
    log.exception("falha ao ...")        # captura stacktrace no arquivo
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from . import config


_configurado = False

# 5 MB por arquivo, 3 backups → ~20 MB no máximo. Suficiente para semanas.
_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 3


def configurar() -> None:
    """Idempotente. Liga o RotatingFileHandler no logger raiz 'pregoes'."""
    global _configurado
    if _configurado:
        return
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        config.DATA_DIR / "pregoes.log",
        maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    root = logging.getLogger("pregoes")
    root.setLevel(logging.INFO)
    # Evita handlers duplicados se reconfigurar
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)
    _configurado = True


def get_logger(nome: str) -> logging.Logger:
    """Logger de um módulo: get_logger('scraper') → 'pregoes.scraper'."""
    return logging.getLogger(f"pregoes.{nome}")
