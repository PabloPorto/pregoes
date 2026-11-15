"""Thread DONA dos navegadores (scraper.no_navegador) — multiusuário, 2026-10.

O Playwright sync é preso à thread que o criou, e no Streamlit cada clique (de
cada operador) roda numa thread nova. Todo trabalho de navegador passa por uma
única thread permanente, em fila. Os dublês aqui imitam essa regra do
Playwright: usar a "página" fora da thread que a criou levanta, como o greenlet
real ("cannot switch to a different thread").
"""
from __future__ import annotations

import threading
import time

import pytest

import pregoes.scraper as scraper


class _PaginaPresa:
    """Imita a página do Playwright: só funciona na thread que a criou."""

    def __init__(self):
        self._dona = threading.get_ident()

    def evaluate(self, _js):
        if threading.get_ident() != self._dona:
            raise RuntimeError("cannot switch to a different thread")
        return 1


class _SessaoFalsa:
    logins = 0

    def __init__(self, *_a):
        type(self).logins += 1
        self._page = _PaginaPresa()
        self.fechada = False

    def __exit__(self, *_a):
        self._page.evaluate("() => 1")   # fechar também é preso à thread
        self.fechada = True


@pytest.fixture(autouse=True)
def _sessoes_limpas(monkeypatch):
    _SessaoFalsa.logins = 0
    monkeypatch.setattr(scraper, "_abrir_sessao_viva", lambda f: _SessaoFalsa())
    scraper._sessoes_vivas.clear()
    yield
    scraper._sessoes_vivas.clear()


def _em_outra_thread(fn):
    """Roda fn numa thread nova (como um clique no Streamlit) e devolve o
    resultado ou re-levanta a exceção."""
    saida = {}

    def _alvo():
        try:
            saida["ok"] = fn()
        except BaseException as exc:   # noqa: BLE001 — repassada abaixo
            saida["erro"] = exc
    t = threading.Thread(target=_alvo)
    t.start()
    t.join(10)
    if "erro" in saida:
        raise saida["erro"]
    return saida["ok"]


def test_cliques_em_threads_diferentes_reusam_a_sessao_sem_relogar():
    """O bug de antes: o 2º clique (outra thread) falhava no ping, relogava e
    não conseguia fechar a sessão antiga (navegador órfão)."""
    s1 = _em_outra_thread(lambda: scraper.obter_sessao_viva("almeida"))
    s2 = _em_outra_thread(lambda: scraper.obter_sessao_viva("almeida"))
    assert s1 is s2
    assert _SessaoFalsa.logins == 1


def test_fechar_de_outra_thread_fecha_de_verdade():
    sessao = _em_outra_thread(lambda: scraper.obter_sessao_viva("dantas"))
    _em_outra_thread(lambda: scraper.fechar_sessao_viva("dantas"))
    assert sessao.fechada
    assert "dantas" not in scraper._sessoes_vivas


def test_todo_trabalho_roda_numa_unica_thread_permanente():
    ids = {_em_outra_thread(lambda: scraper.no_navegador(threading.get_ident))
           for _ in range(3)}
    assert len(ids) == 1
    assert threading.get_ident() not in ids


def test_progresso_volta_para_a_thread_que_chamou():
    vistos = []

    def _trabalho(progresso):
        for i in range(3):
            progresso(i)
        return "fim"

    assert scraper.no_navegador(
        _trabalho, progresso=lambda i: vistos.append((i, threading.get_ident()))
    ) == "fim"
    assert [i for i, _ in vistos] == [0, 1, 2]
    assert {t for _, t in vistos} == {threading.get_ident()}


def test_excecao_do_trabalho_chega_a_quem_chamou():
    def _falha():
        raise ValueError("login recusado")
    with pytest.raises(ValueError, match="login recusado"):
        scraper.no_navegador(_falha)


def test_tela_interrompida_aborta_o_trabalho_e_libera_a_fila():
    """Rerun do Streamlit no meio de uma busca: a exceção sai do callback na
    thread da tela; a thread dona aborta no próximo aviso, sem prender a fila."""
    class _Rerun(BaseException):
        pass

    abortou = threading.Event()

    def _busca_longa(progresso):
        try:
            for i in range(200):
                progresso(i)
                time.sleep(0.02)
        except scraper.OperacaoInterrompida:
            abortou.set()
            raise

    def _tela(i):
        if i == 2:
            raise _Rerun()

    with pytest.raises(_Rerun):
        scraper.no_navegador(_busca_longa, progresso=_tela)
    assert abortou.wait(5)
    assert scraper.no_navegador(lambda: "livre") == "livre"   # fila não travou


def test_quem_esta_na_fila_e_avisado_e_espera_a_vez():
    liberar = threading.Event()
    ocupando = threading.Event()

    def _busca_do_outro_operador():
        ocupando.set()
        liberar.wait(5)

    outro = threading.Thread(
        target=lambda: scraper.no_navegador(_busca_do_outro_operador))
    outro.start()
    assert ocupando.wait(5)

    avisos = []
    threading.Timer(0.5, liberar.set).start()
    assert scraper.no_navegador(lambda: "minha vez",
                                aguardando=lambda: avisos.append(1)) == "minha vez"
    assert avisos == [1]                 # avisou uma vez só
    outro.join(5)


def test_fechar_sessoes_sem_esperar_nao_trava_a_tela():
    """'Novo pregão' de um operador com a busca de outro em andamento: a tela
    segue na hora e o fechamento entra na fila DEPOIS da busca."""
    sessao = scraper.obter_sessao_viva("almeida")
    liberar = threading.Event()
    ocupando = threading.Event()

    def _busca_em_andamento():
        ocupando.set()
        liberar.wait(5)
        return sessao.fechada            # não pode ter sido fechada no meio

    resultado = {}
    outro = threading.Thread(target=lambda: resultado.update(
        fechada_no_meio=scraper.no_navegador(_busca_em_andamento)))
    outro.start()
    assert ocupando.wait(5)

    t0 = time.monotonic()
    scraper.fechar_sessoes_vivas(esperar=False)
    assert time.monotonic() - t0 < 0.5   # não esperou a busca do outro

    liberar.set()
    outro.join(5)
    assert resultado["fechada_no_meio"] is False
    scraper.no_navegador(lambda: None)   # fila andou: o fechamento já rodou
    assert sessao.fechada
    assert scraper._sessoes_vivas == {}
