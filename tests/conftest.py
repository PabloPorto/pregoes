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


# 3) A janela de consultas (80 a cada 10 min — limite real dos sites) e o freio
#    são globais do módulo: sem isto, a suíte dormiria minutos ao passar de 80
#    consultas simuladas, ou herdaria a pausa de um teste no seguinte.
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _sem_espera_de_consultas(monkeypatch):
    import pregoes.scraper as scraper
    monkeypatch.setattr(scraper, "_janela",
                        scraper._JanelaDeConsultas(10**9, 300))
    monkeypatch.setattr(scraper, "_freio", scraper._FreioDeConsultas())


# 4) Interface web (pregoes/web): cada teste ganha uma pasta de dados VAZIA e
#    um cliente HTTP do app. Uso:  def test_x(web): r = web.get("/")
#    `web.dados` é a pasta; `web.pregao(n_itens=3)` cria um pregão já lido.
@pytest.fixture
def web(tmp_path, monkeypatch):
    import warnings

    from pregoes import config, credenciais, db, rascunho
    dados = tmp_path / "data"
    for sub in ("rascunhos", "exports", "editais", "historico"):
        (dados / sub).mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", dados)
    monkeypatch.setattr(config, "DB_PATH", dados / "pregoes.db")
    monkeypatch.setattr(config, "EXPORTS_DIR", dados / "exports")
    monkeypatch.setattr(config, "EDITAIS_DIR", dados / "editais")
    monkeypatch.setattr(config, "HISTORICO_DIR", dados / "historico")
    monkeypatch.setattr(rascunho, "DIR", dados / "rascunhos")
    monkeypatch.setattr(rascunho, "CAMINHO", dados / "rascunho.pkl")
    monkeypatch.setattr(credenciais, "CAMINHO", dados / "credenciais.json")
    db.init_db(config.DB_PATH)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from starlette.testclient import TestClient

        from pregoes.web import tarefas
        from pregoes.web.app import criar_app
        monkeypatch.setattr(tarefas, "_tarefas", {})
        with TestClient(criar_app(), headers={"X-Requested-With": "pregoes"}) as cliente:
            cliente.dados = dados

            def _pregao(n_itens: int = 3, orgao: str = "Prefeitura de Teste",
                        numero: str = "10/2026"):
                """Pregão já lido, com `n_itens` itens (o 1º fica 'duvidoso')."""
                from pregoes.extrator import ItemExtraido, ResultadoExtracao
                from pregoes.web.pregao import Pregao
                itens = [ItemExtraido(item_numero=i + 1, descricao=f"PRODUTO DE TESTE {i + 1}",
                                      unidade="UND", quantidade=10.0 * (i + 1),
                                      valor_referencia=5.0 + i,
                                      confianca="duvidoso" if i == 0 and n_itens > 1 else "extraido")
                         for i in range(n_itens)]
                res = ResultadoExtracao(arquivo="x.pdf")
                res.itens = itens
                res.confianca_global = 1.0
                res.tabelas_brutas = [[["ITEM", "DESCRIÇÃO", "UNID", "QUANT"],
                                       ["1", "PRODUTO DE TESTE 1", "UND", "10"]]]
                p = Pregao.criar(orgao, numero, str(dados / "editais" / "x.pdf"))
                p.estado["extracao"] = res
                p.garantir_itens()
                p.salvar()
                return p

            cliente.pregao = _pregao
            yield cliente
