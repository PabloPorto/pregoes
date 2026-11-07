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


# ---------------------------------------------------------------------------
# Busca da UI: cada fornecedor roda numa thread que NÃO é a dona do navegador,
# com _http_only ligado por quem chama. Campo 2026-11-06 (Pregão 20/2026): o
# site respondeu 405 a algumas consultas e a exceção da 2ª passada derrubava o
# fornecedor inteiro — 0 de 654 itens, descartando o que já tinha sido achado.
# ---------------------------------------------------------------------------

def _sem_pausa(monkeypatch):
    monkeypatch.setattr(scraper, "_PAUSA_APOS_RECUSA_S", 0.0)


def test_recusa_do_site_sem_navegador_nao_descarta_o_que_foi_achado(monkeypatch):
    _sem_pausa(monkeypatch)
    forn = _FornParalelo(expira_em={"B", "C"})
    forn._http_only = True                      # como a UI chama
    lote = _rodar(forn, ["A", "B", "C", "D"], workers=4)
    assert [r.codigo_fornecedor for r in lote.resultados[0]] == ["C-A"]
    assert [r.codigo_fornecedor for r in lote.resultados[3]] == ["C-D"]
    assert lote.resultados[1] == [] and lote.resultados[2] == []
    assert sorted(e.indice_item for e in lote.erros) == [1, 2]
    assert all(e.mensagem.startswith(scraper._MENSAGEM_RECUSA) for e in lote.erros)
    assert forn._http_only is True, "flag de quem chamou preservada"


def test_recusa_passageira_e_recuperada_na_segunda_passada(monkeypatch):
    """O site volta a responder depois da pausa: o item sai com preço."""
    _sem_pausa(monkeypatch)
    forn = _FornParalelo()
    forn._http_only = True
    ja_recusou: set = set()
    buscar_original = forn.buscar

    def buscar_recusa_uma_vez(descricao):
        if descricao == "B" and descricao not in ja_recusou:
            ja_recusou.add(descricao)
            raise scraper.SessaoHTTPExpirou("405", status=405)
        return buscar_original(descricao)

    forn.buscar = buscar_recusa_uma_vez
    lote = _rodar(forn, ["A", "B", "C"], workers=2)
    assert [r.codigo_fornecedor for r in lote.resultados[1]] == ["C-B"]
    assert lote.erros == []


def test_recusas_em_serie_param_de_consultar_o_site(monkeypatch):
    """Site recusando tudo: depois de poucas falhas o lote para de disparar
    consultas (insistir prolonga o bloqueio) e o resto vira erro do item."""
    _sem_pausa(monkeypatch)
    descrs = [f"I{i}" for i in range(40)]
    forn = _FornParalelo(expira_em=set(descrs))
    forn._http_only = True
    chamadas = []
    buscar_original = forn.buscar
    forn.buscar = lambda d: (chamadas.append(d), buscar_original(d))[1]
    lote = _rodar(forn, descrs, workers=4)
    assert len(lote.erros) == 40 and all(lote.resultados[i] == [] for i in range(40))
    limite = (scraper._MAX_FALHAS_HTTP_NO_POOL + 4      # em voo no pool
              + scraper._MAX_RECUSAS_SEGUIDAS)
    assert len(chamadas) <= limite, f"{len(chamadas)} consultas a um site que recusa tudo"


def test_ritmo_padrao_fica_abaixo_do_limite_medido_dos_fornecedores():
    """Q20: os sites bloqueiam (405) acima de ~350 consultas em 10 min por
    fornecedor; 0,5/s rodou 25 min limpo e 0,65/s bloqueou. Subir o ritmo sem
    novo teste de carga volta a quebrar o lote."""
    from pregoes import config
    assert config.SCRAPER_MAX_REQ_POR_SEGUNDO * 600 <= 320
