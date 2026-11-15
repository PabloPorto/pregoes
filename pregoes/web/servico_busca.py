"""A busca de preços em lote como TAREFA DO SERVIDOR (tipo "busca").

Porta de `_executar_busca` (pregoes/ui/app.py L1772-2085) sem tela: o
progresso vai para a `Tarefa` (a página só pergunta como ela está) e o
resultado é gravado no rascunho do pregão no fim, de uma vez, sob
`trava(slug)`. Fechar o navegador não interrompe nada.

O que foi mantido igual ao Streamlit (inventário 4.2 e 6.4):
- login de cada fornecedor com credencial + a busca de TODOS dentro de UMA
  operação `scraper.no_navegador` (a thread dona dos navegadores, com fila —
  quem chega depois vê "Aguardando a busca de outro operador terminar…");
- modo PARALELO (uma thread por fornecedor, só HTTP) quando há mais de uma
  sessão e todas têm `_http_cookie`; senão SEQUENCIAL, na thread dona;
- falha de um fornecedor inteiro não derruba o outro (`_registrar_falha_grave`);
- busca incremental (`indices`): o scraper recebe só o sub-lote e os índices
  voltam remapeados para a posição original antes de mesclar.
Não foram trazidos: os controles [dev] (diagnóstico XLSX, limpar cache).

CANCELAMENTO (novo). Usa o mecanismo de interrupção que o scraper já tem: o
aviso de progresso levanta `scraper.OperacaoInterrompida` (BaseException — não
é engolida pelos `except Exception` do scraper), o `no_navegador` marca a
operação como interrompida e ela aborta. Duas coisas a mais, para o cancelar
valer de verdade sem editar o scraper:
- o scraper recebe CÓPIAS dos itens (`_ItemDoLote`) que levantam a mesma
  interrupção quando o cancelamento foi pedido. Sem isso, o pool interno do
  `buscar_precos_para_itens` só devolveria o controle depois de consultar o
  lote inteiro (o `with ThreadPoolExecutor` espera as tarefas já enfileiradas)
  — horas gastando o limite de consultas dos sites à toa;
- no modo paralelo a thread dona ESPERA os fornecedores pararem antes de
  liberar a fila: eles deixam a sessão em modo "só HTTP" enquanto vivem, e uma
  busca nova não pode começar com esse modo sendo desligado por baixo dela.
Cancelada, a tarefa não grava NADA no rascunho; o que já foi consultado fica
no cache de preços do backend (2 dias), então refazer é rápido.
"""
from __future__ import annotations

import dataclasses
import logging
import queue
import threading
import time
from typing import Optional

from pregoes import credenciais, erros, scraper
from pregoes.extrator import ItemExtraido
from pregoes.web import regras_selecao as regras
from pregoes.web.pregao import Pregao, fornecedores_configurados, trava
from pregoes.web.tarefas import Tarefa

_log = logging.getLogger("pregoes.web")

# app.py L1672 (`_MSG_PAUSA_LIMITE`), sem o "não feche esta tela": aqui a
# busca roda no servidor e a página pode ser fechada.
MSG_PAUSA_LIMITE = ("O site dos fornecedores limitou as consultas. A busca "
                    "pausou e continua sozinha em alguns minutos.")
MSG_AGUARDANDO = "Aguardando a busca de outro operador terminar…"
MSG_ITENS_MUDARAM = ("A lista de itens deste pregão mudou enquanto a busca rodava. Os "
                     "resultados foram descartados para não ligar preço ao item errado "
                     "— inicie a busca de novo (o que já foi consultado responde da "
                     "memória).")


class _ItemDoLote(ItemExtraido):
    """Cópia de um item entregue ao scraper. Igual ao original, mas a leitura
    da descrição (a 1ª coisa que o scraper faz com cada item) interrompe o
    lote quando o cancelamento foi pedido — ver o docstring do módulo."""

    def __getattribute__(self, nome):
        if nome == "descricao" and object.__getattribute__(self, "_tarefa").cancelamento_pedido:
            raise scraper.OperacaoInterrompida()
        return object.__getattribute__(self, nome)


def _copia_cancelavel(item: ItemExtraido, tarefa: Tarefa) -> _ItemDoLote:
    copia = _ItemDoLote(**{c.name: getattr(item, c.name) for c in dataclasses.fields(ItemExtraido)})
    object.__setattr__(copia, "_tarefa", tarefa)
    return copia


def _tempo(segundos: float) -> str:
    """Decorrido "MM:SS"; de 1 hora em diante "{h}h {mm}min" (app.py L1830)."""
    m, s = divmod(int(segundos), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h:d}h {m:02d}min"
    return f"{m:02d}:{s:02d}"


def _extras_dos_stats(stats: list) -> dict:
    """O placar por fornecedor concluído — (nome, ok, variação, vazio) — como
    a tarefa publica: a lista crua e um texto pronto por fornecedor."""
    extra: dict = {"stats": [list(s) for s in stats]}
    for nome, ok, var, vazio in stats:
        extra[f"resumo_{nome}"] = (f"{ok} encontrados · {var} por variação · "
                                   f"{vazio} sem resultado")
    return extra


class _Lote:
    """Um lote em andamento: o que a thread dona acumula e como o progresso
    chega à tarefa."""

    def __init__(self, itens: list, alvo_indices: list[int], fornecedores: list[str],
                 tarefa: Tarefa):
        self.tarefa = tarefa
        self.fornecedores = fornecedores
        self.alvo_indices = alvo_indices
        self.itens_alvo = [_copia_cancelavel(itens[i], tarefa) for i in alvo_indices]
        self.total = len(alvo_indices)
        # Sempre pela posição ORIGINAL do item (o scraper devolve pela posição
        # no sub-lote; `_absorver` remapeia).
        self.resultados: dict[int, list] = {i: [] for i in alvo_indices}
        self.erros: list = []
        self.cache_hits = 0
        self.cache_misses = 0
        self.stats: list[tuple[str, int, int, int]] = []   # (nome, ok, variação, vazio)

    # --- progresso: roda na thread da TAREFA (o no_navegador devolve) --------
    def progresso(self, frac: float, detalhe: str, feitos: int, total: int,
                  por_fornecedor: Optional[dict] = None, stats: Optional[list] = None,
                  rotulo: Optional[str] = None) -> None:
        if self.tarefa.cancelamento_pedido:
            raise scraper.OperacaoInterrompida()
        extra = {"feitos": feitos, "total": total,
                 "rotulo": rotulo or ("Aguardando início" if feitos <= 0
                                      else f"Item {feitos} de {total}")}
        if por_fornecedor:
            extra["por_fornecedor"] = por_fornecedor
            # Textos prontos para a página (`data-extra` só troca texto).
            for nome, (a, t) in por_fornecedor.items():
                extra[f"prog_{nome}"] = f"{a} de {t}"
        extra.update(_extras_dos_stats(stats or []))
        self.tarefa.atualizar(frac, detalhe, **extra)

    def aguardando(self) -> None:
        self.tarefa.atualizar(0.0, MSG_AGUARDANDO, feitos=0, total=self.total,
                              rotulo="Aguardando início")

    # --- acumuladores (thread dona) ------------------------------------------
    def _absorver(self, nome: str, lote) -> None:
        """Sumário + mesclagem de um `ResultadoLote` (app.py `_absorver_lote`,
        L1890): ok exclui os por variação; posição no sub-lote → índice
        original, também nos erros."""
        n_ok = sum(1 for bag in lote.resultados.values() if bag)
        n_var = sum(1 for bag in lote.resultados.values()
                    if bag and any(r.confianca == "variacao" for r in bag))
        self.stats.append((nome, n_ok - n_var, n_var, self.total - n_ok))
        for sub_idx, bag in lote.resultados.items():
            self.resultados[self.alvo_indices[sub_idx]].extend(bag)
        for e in lote.erros:
            if 0 <= e.indice_item < len(self.alvo_indices):
                e = dataclasses.replace(e, indice_item=self.alvo_indices[e.indice_item])
            self.erros.append(e)
        self.cache_hits += lote.cache_hits
        self.cache_misses += lote.cache_misses

    def _registrar_falha_grave(self, nome: str, exc: Exception) -> None:
        """Falha do fornecedor inteiro (login/layout): descarta a sessão do
        cache (a próxima busca reloga) e registra o erro global (app.py
        L1911). O outro fornecedor segue."""
        _log.error("falha grave na busca em %s", nome, exc_info=exc)
        try:
            scraper.fechar_sessao_viva(nome)
        except Exception:
            pass
        self.erros.append(scraper.ErroBusca(
            indice_item=-1, fornecedor=nome, descricao_resumo="(login/inicialização)",
            mensagem=erros.mensagem_amigavel(exc, f"buscar na {nome.capitalize()}")))
        self.stats.append((nome, 0, 0, self.total))

    # --- a operação inteira: roda na thread DONA dos navegadores --------------
    def buscar_na_thread_dona(self, progresso) -> dict:
        n_forn = len(self.fornecedores)
        sessoes: dict = {}
        for forn_idx, nome in enumerate(self.fornecedores):
            if credenciais.carregar(nome) is None:
                continue
            progresso(frac=forn_idx / max(n_forn, 1) * 0.05,
                      detalhe=f"Fazendo login em {nome.capitalize()}…",
                      feitos=0, total=self.total, stats=list(self.stats))
            try:
                sessoes[nome] = scraper.obter_sessao_viva(nome)
            except Exception as exc:
                self._registrar_falha_grave(nome, exc)

        paralelo = (len(sessoes) > 1
                    and all(getattr(f, "_http_cookie", None) for f in sessoes.values()))
        if paralelo:
            self._buscar_em_paralelo(sessoes, progresso)
        else:
            self._buscar_em_sequencia(sessoes, progresso)
        return regras.avisos_pagamento_de(sessoes)

    def _buscar_em_sequencia(self, sessoes: dict, progresso) -> None:
        """Um fornecedor por vez, na thread dona (app.py L1983-2008)."""
        n_forn = len(self.fornecedores)
        prog: dict = {}
        for forn_idx, nome in enumerate(self.fornecedores):
            forn = sessoes.get(nome)
            if forn is None:
                continue      # sem credencial, ou o login já falhou (registrado)

            def cb(atual, total, msg, _forn_idx=forn_idx, _nome=nome):
                prog[_nome] = (atual, total)
                progresso(frac=(_forn_idx + atual / max(total, 1)) / max(n_forn, 1),
                          detalhe=f"{_nome.capitalize()} · fornecedor {_forn_idx + 1} "
                                  f"de {n_forn} — {msg}",
                          feitos=atual, total=total, por_fornecedor=dict(prog),
                          stats=list(self.stats))

            try:
                lote = scraper.buscar_precos_para_itens(
                    self.itens_alvo, [forn], progresso_callback=cb)
            except Exception as exc:
                self._registrar_falha_grave(nome, exc)
                continue
            self._absorver(nome, lote)

    def _buscar_em_paralelo(self, sessoes: dict, progresso) -> None:
        """Uma thread por fornecedor, só HTTP — os workers nunca tocam o
        navegador (app.py `_buscar_fornecedores_paralelo`, L1679, e o trecho
        L1959-1982). Os workers só enfileiram; quem avisa o progresso é esta
        thread (a dona)."""
        fila: "queue.Queue" = queue.Queue()
        saida: dict = {}
        tarefa = self.tarefa

        def _avisar(nome, atual, total, msg) -> None:
            if tarefa.cancelamento_pedido:      # para ESTE fornecedor já
                raise scraper.OperacaoInterrompida()
            fila.put((nome, atual, total, msg))

        def _worker(nome: str, forn) -> None:
            try:
                forn._http_only = True
                saida[nome] = scraper.buscar_precos_para_itens(
                    self.itens_alvo, [forn],
                    progresso_callback=lambda a, t, m, _n=nome: _avisar(_n, a, t, m))
            except scraper.OperacaoInterrompida:
                pass                             # cancelada: sem resultado
            except Exception as exc:   # noqa: BLE001 — worker nunca propaga cru
                saida[nome] = exc
            finally:
                forn._http_only = False
                fila.put((nome, None, None, None))   # sinal de término

        prog = {n: (0, self.total) for n in sessoes}

        def _pintar(detalhe: str) -> None:
            frac = sum(a / max(t, 1) for a, t in prog.values()) / max(len(prog), 1)
            progresso(frac=frac, detalhe=detalhe,
                      feitos=sum(a for a, _ in prog.values()),
                      total=sum(t for _, t in prog.values()),
                      por_fornecedor=dict(prog), stats=list(self.stats))

        def _placar() -> str:
            return " · ".join(f"{n.capitalize()} {a}/{t}" for n, (a, t) in prog.items())

        threads = [threading.Thread(target=_worker, args=(n, f), daemon=True,
                                    name=f"busca-{n}")
                   for n, f in sessoes.items()]
        for t in threads:
            t.start()
        try:
            vivos, calado_s, ultimo = len(threads), 0, _placar()
            while vivos:
                try:
                    nome, atual, total, msg = fila.get(timeout=1)
                except queue.Empty:
                    # Sem item novo. De segundo em segundo repete o último
                    # aviso (é assim que o cancelamento é percebido rápido); a
                    # cada 5 s, se o site limitou as consultas, diz que o lote
                    # está em pausa em vez de parecer travado.
                    calado_s += 1
                    em_pausa = calado_s % 5 == 0 and scraper.pausa_por_limite() > 0
                    _pintar(MSG_PAUSA_LIMITE if em_pausa else ultimo)
                    continue
                calado_s = 0
                if atual is None:
                    vivos -= 1
                    continue
                prog[nome] = (atual, total)
                ultimo = f"{_placar()} — {msg}" if msg else _placar()
                _pintar(ultimo)
        finally:
            # Também na interrupção: ninguém usa as sessões enquanto um worker
            # ainda pode desligar o modo "só HTTP" delas.
            for t in threads:
                t.join()
        if tarefa.cancelamento_pedido:
            # Os workers pararam por causa do cancelamento, não por falha: sem
            # isto o laço abaixo os trataria como "fornecedor sem resultado" e
            # derrubaria as sessões logadas.
            raise scraper.OperacaoInterrompida()
        for nome in sessoes:
            r = saida.get(nome)
            if r is None or isinstance(r, Exception):
                self._registrar_falha_grave(
                    nome, r if isinstance(r, Exception)
                    else RuntimeError("fornecedor terminou sem resultado"))
            else:
                self._absorver(nome, r)


def executar_busca(slug: str, indices: Optional[list[int]], tarefa: Tarefa) -> Optional[dict]:
    """Tarefa "busca": busca os preços dos itens do pregão `slug` e grava no
    rascunho. `indices=None` = busca completa (substitui os resultados);
    lista = incremental (só esses itens; mescla, mantendo as escolhas).

    Devolve `{"tipo": "ok"|"alerta", "texto": …}` para a tela mostrar ao
    terminar, ou None quando foi cancelada / o pregão sumiu."""
    pregao = Pregao.abrir(slug)
    if pregao is None:
        raise RuntimeError("pregão não encontrado")
    itens = list(pregao.itens_revisados)
    fornecedores = fornecedores_configurados()
    if indices is None:
        alvo_indices = list(range(len(itens)))
    else:
        alvo_indices = sorted({i for i in indices if 0 <= i < len(itens)})
    if not alvo_indices or not fornecedores:
        return None

    inicio = time.monotonic()
    lote = _Lote(itens, alvo_indices, fornecedores, tarefa)
    tarefa.atualizar(0.0, "Preparando ambiente…", feitos=0, total=lote.total,
                     rotulo="Aguardando início")
    try:
        avisos_pagamento = scraper.no_navegador(
            lote.buscar_na_thread_dona, progresso=lote.progresso,
            aguardando=lote.aguardando)
    except scraper.OperacaoInterrompida:
        avisos_pagamento = None
    if avisos_pagamento is None or tarefa.cancelamento_pedido:
        _log.info("LOTE cancelado: pregão %s — %d itens, depois de %s (nada gravado; "
                  "o que foi consultado fica no cache de preços)",
                  slug, lote.total, _tempo(time.monotonic() - inicio))
        return None

    with trava(slug):
        pregao = Pregao.abrir(slug)
        if pregao is None:              # descartado enquanto buscava
            return None
        if list(pregao.itens_revisados) != itens:
            # A revisão foi alterada no meio: as posições não valem mais.
            _log.warning("LOTE descartado: os itens do pregão %s mudaram durante a busca", slug)
            return {"tipo": "alerta", "texto": MSG_ITENS_MUDARAM}
        estado = pregao.estado
        if indices is None:
            estado["resultados_busca"] = lote.resultados
            regras.selecoes_default(itens, lote.resultados, pregao.selecoes,
                                    pregao.itens_nao_usar)
        else:
            regras.mesclar(itens, alvo_indices, lote.resultados,
                           estado.setdefault("resultados_busca", {}),
                           pregao.selecoes, pregao.itens_nao_usar)
        estado["erros_busca"] = lote.erros
        estado["avisos_pagamento"] = avisos_pagamento
        estado["avisos_lote_vazio"] = (regras.avisos_lote_vazio(lote.stats, lote.total)
                                       + regras.avisos_recusa(lote.erros))
        pregao.salvar()

    tarefa.atualizar(1.0, "", feitos=lote.total, total=lote.total, rotulo="Busca concluída",
                     **_extras_dos_stats(lote.stats))
    # Uma linha por lote no log (app.py L2046): sem ela, só dava para saber
    # como a busca foi abrindo o rascunho do pregão.
    _log.info(
        "LOTE: %d itens em %s | com candidato %d | erros %d | por fornecedor "
        "(ok, variação, vazio) %s",
        lote.total, _tempo(time.monotonic() - inicio),
        sum(1 for v in lote.resultados.values() if v), len(lote.erros),
        {n: (a, b, c) for n, a, b, c in lote.stats})
    return {"tipo": "ok",
            "texto": regras.resumo_do_lote(len(lote.resultados), lote.cache_hits,
                                           lote.cache_misses, len(lote.erros))}
