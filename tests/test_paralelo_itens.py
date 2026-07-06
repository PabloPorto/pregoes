"""Paralelismo POR ITEM no orquestrador (Fase 2 de perf, 2026-07-06).

Quando todos os fornecedores têm transporte HTTP, os itens rodam num pool de
threads. Estes testes travam os cenários de risco:
- equivalência: paralelo produz os MESMOS resultados que o serial;
- progresso: callback SEMPRE na thread de quem chamou (Streamlit) e monotônico;
- isolamento: erro em um item não derruba os outros;
- sessão HTTP expirada no meio: item volta na 2ª passada serial (navegador);
- diagnóstico ligado / fornecedor sem HTTP: força serial (comportamento
  histórico intocado);
- rate limiter com lock: pool NÃO aumenta a cadência no site.
"""
from __future__ import annotations

import threading
import time

import pregoes.scraper as scraper
from pregoes.extrator import ItemExtraido
from pregoes.scraper import ResultadoBusca, buscar_precos_para_itens


def _itens(descrs):
    return [ItemExtraido(item_numero=i + 1, descricao=d, quantidade=1.0)
            for i, d in enumerate(descrs)]


class _FornParalelo:
    """Fake com transporte HTTP 'ativo' (_http_cookie) — liga o pool."""
    nome = "almeida"

    def __init__(self, falha_em=None, expira_em=None):
        self._http_cookie = "PHPSESSID=x; token=y"
        self._http_only = False
        self._falha_em = falha_em or set()
        self._expira_em = expira_em or set()
        self.threads_vistos: set = set()

    def buscar(self, descricao):
        self.threads_vistos.add(threading.get_ident())
        if descricao in self._falha_em:
            raise ValueError("item podre")
        if descricao in self._expira_em and self._http_only:
            raise scraper.SessaoHTTPExpirou("cookie caiu")
        return [ResultadoBusca(f"{descricao} ACHADO", "M", 10.0,
                               f"C-{descricao}", "exato", self.nome)]


def _rodar(forn, descrs, workers, **kw):
    return buscar_precos_para_itens(
        _itens(descrs), [forn], usar_cache=False, usar_catalogo=False,
        max_workers=workers, **kw)


def test_paralelo_equivale_ao_serial():
    descrs = [f"ITEM {i}" for i in range(8)]
    serial = _rodar(_FornParalelo(), descrs, workers=1)
    paralelo = _rodar(_FornParalelo(), descrs, workers=4)
    chave = lambda lote: {i: [r.codigo_fornecedor for r in bag]  # noqa: E731
                          for i, bag in lote.resultados.items()}
    assert chave(paralelo) == chave(serial)
    assert set(paralelo.resultados) == set(range(8))
    assert paralelo.erros == [] and serial.erros == []


def test_paralelo_usa_mais_de_uma_thread_e_progresso_na_thread_principal():
    forn = _FornParalelo()
    eventos = []
    principal = threading.get_ident()
    # Barreira nas 2 primeiras buscas: EXIGE 2 threads simultâneas (com itens
    # instantâneos, 1 worker às vezes engolia a fila inteira antes dos outros
    # subirem → flake). Se o pool regredir para serial, a barreira estoura o
    # timeout e o teste falha alto — determinístico nos dois sentidos.
    barreira = threading.Barrier(2, timeout=5)
    buscar_original = forn.buscar

    def buscar_bloqueante(descricao):
        if descricao in ("I0", "I1"):
            barreira.wait()
        return buscar_original(descricao)

    forn.buscar = buscar_bloqueante

    def cb(atual, total, msg):
        eventos.append((threading.get_ident(), atual, total))

    _rodar(forn, [f"I{i}" for i in range(8)], workers=4,
           progresso_callback=cb)
    assert len(forn.threads_vistos) > 1, "pool não paralelizou"
    assert all(t == principal for t, _, _ in eventos), \
        "callback rodou fora da thread principal (quebraria a UI)"
    assert [a for _, a, _ in eventos] == list(range(1, 9)), "progresso monotônico"
    assert all(tot == 8 for _, _, tot in eventos)


def test_erro_num_item_nao_derruba_os_outros_no_pool():
    forn = _FornParalelo(falha_em={"B"})
    lote = _rodar(forn, ["A", "B", "C", "D"], workers=4)
    assert [r.codigo_fornecedor for r in lote.resultados[0]] == ["C-A"]
    assert lote.resultados[1] == []                     # o podre fica vazio
    assert [r.codigo_fornecedor for r in lote.resultados[3]] == ["C-D"]
    assert len(lote.erros) == 1 and lote.erros[0].indice_item == 1


def test_sessao_http_expirada_volta_pela_segunda_passada_serial():
    """Cookie morre no meio do pool: o item NÃO vira erro — reprocessa em
    serial (onde o fallback de navegador é permitido) e completa."""
    forn = _FornParalelo(expira_em={"B", "C"})
    eventos = []
    lote = _rodar(forn, ["A", "B", "C", "D"], workers=4,
                  progresso_callback=lambda a, t, m: eventos.append(a))
    assert set(lote.resultados) == {0, 1, 2, 3}
    assert [r.codigo_fornecedor for r in lote.resultados[1]] == ["C-B"]
    assert [r.codigo_fornecedor for r in lote.resultados[2]] == ["C-C"]
    assert lote.erros == []
    assert eventos == [1, 2, 3, 4], "retry conta no progresso uma única vez"
    assert forn._http_only is False, "flag restaurada após o pool"


def test_diagnostico_forca_serial():
    from pregoes.diagnostico import LogDiagnostico
    forn = _FornParalelo()
    _rodar(forn, [f"I{i}" for i in range(6)], workers=4,
           diagnostico=LogDiagnostico(arquivo_edital=None,
                                      fornecedores=["almeida"]))
    assert len(forn.threads_vistos) == 1, "diagnóstico compartilhado exige serial"


def test_fornecedor_sem_http_forca_serial():
    forn = _FornParalelo()
    forn._http_cookie = None      # sem transporte HTTP → navegador → serial
    _rodar(forn, [f"I{i}" for i in range(6)], workers=4)
    assert len(forn.threads_vistos) == 1


def test_rate_limiter_com_lock_mantem_cadencia_global():
    rl = scraper._RateLimiter(max_req_por_segundo=100)   # 10ms de intervalo
    t0 = time.perf_counter()
    threads = [threading.Thread(target=rl.acquire) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 6 acquires concorrentes ≥ 5 intervalos — o pool não fura a cadência.
    assert time.perf_counter() - t0 >= 0.045
