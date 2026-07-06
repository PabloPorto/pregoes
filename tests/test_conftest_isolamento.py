"""Regressão do isolamento log de PRODUÇÃO × testes (2026-07-06).

O log de campo aparecia poluído por fakes de teste (falhas de pagamento que
não eram reais), mascarando o sinal genuíno e disparando investigações falsas.
tests/conftest.py corta isso; estes testes travam a garantia.
"""
from __future__ import annotations

import logging
import logging.handlers

from pregoes import logconf


def test_logger_pregoes_sem_handler_de_arquivo():
    """Durante os testes, o logger raiz 'pregoes' NÃO pode ter um handler de
    arquivo apontando para o data/pregoes.log de produção."""
    root = logging.getLogger("pregoes")
    de_arquivo = [h for h in root.handlers
                  if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert not de_arquivo, f"handler de arquivo vazou nos testes: {de_arquivo}"


def test_configurar_e_noop_sob_conftest():
    """conftest neutraliza configurar(): chamá-la não re-anexa handler algum."""
    antes = list(logging.getLogger("pregoes").handlers)
    logconf.configurar()
    depois = list(logging.getLogger("pregoes").handlers)
    assert antes == depois


def test_warning_do_scraper_e_capturavel_mas_nao_vai_a_arquivo(caplog):
    """Warnings continuam capturáveis por caplog (o que os testes usam), sem
    depender do arquivo de produção."""
    with caplog.at_level(logging.WARNING, logger="pregoes.scraper"):
        logging.getLogger("pregoes.scraper").warning("linha de teste isolada")
    assert "linha de teste isolada" in caplog.text
