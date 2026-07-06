"""Isolamento dos testes do ambiente de PRODUÇÃO (regressão 2026-07-06).

SINTOMA: o `data/pregoes.log` de campo aparecia cheio de "falhas" de condição
de pagamento (CONFIRMADA + NÃO pegou + AttributeError, várias em milissegundos)
que pareciam um bug real de produção — e não eram.

CAUSA: durante a COLETA, importar `pregoes.ui.app` (test_historico_exports,
test_run_app) executa o boot do módulo, que chama `logconf.configurar()` e
anexa um `RotatingFileHandler` ao logger raiz `pregoes` apontando para o
`data/pregoes.log` REAL. A partir daí, TODO log de QUALQUER teste (scraper,
alinhador…) — inclusive os fakes de pagamento — era gravado no log de
produção. Rodar um arquivo de teste isolado não reproduzia (nada chamava
`configurar`); só a suíte inteira poluía. Diagnóstico impossível pelo log.

CORREÇÃO: este conftest roda ANTES de qualquer módulo de teste ser importado.
Ele neutraliza `configurar()` e remove o handler de arquivo do logger
`pregoes`, garantindo que a suíte NUNCA escreva no log de produção. Testes que
checam log usam `caplog`/monkeypatch do próprio logger — não dependem do
arquivo. (Guardado por regressão em tests/test_conftest_isolamento.py.)
"""
from __future__ import annotations

import logging
import logging.handlers

from pregoes import logconf

# 1) configurar() vira no-op — nenhum import de app.py liga o handler de produção.
logconf.configurar = lambda: None
logconf._configurado = True

# 2) Remove qualquer RotatingFileHandler já anexado (a ordem de coleta pode ter
#    importado app.py antes deste conftest em execuções antigas) e silencia a
#    saída para arquivo do logger 'pregoes'.
_root = logging.getLogger("pregoes")
for _h in list(_root.handlers):
    if isinstance(_h, logging.handlers.RotatingFileHandler):
        _root.removeHandler(_h)
if not any(isinstance(h, logging.NullHandler) for h in _root.handlers):
    _root.addHandler(logging.NullHandler())
