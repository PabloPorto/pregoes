"""Tarefas demoradas da interface web — rodam no SERVIDOR, não na aba.

Decisão de desenho (2026-11): extração com IA leva até 90 s e a busca de
preços de um edital grande leva horas (o limite dos fornecedores manda — Q20).
No Streamlit a tarefa vivia na sessão da aba: recarregar ou fechar a página
abandonava o lote. Aqui cada tarefa é uma thread do servidor com um registro
de progresso; a página só PERGUNTA como ela está (`/api/tarefas/<id>`). O
operador pode fechar o navegador, voltar depois — ou o colega abrir o mesmo
pregão — e encontrar a tarefa andando ou pronta.

Simples de propósito: um dicionário em memória protegido por lock. Reiniciar o
serviço perde as tarefas em andamento (o que elas já gravaram no rascunho do
pregão fica), como já acontecia.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

_log = logging.getLogger(__name__)

# Quanto tempo uma tarefa terminada continua consultável (a página que a
# acompanhava precisa ler o desfecho; depois disso ela some do registro).
_GUARDA_TERMINADA_S = 6 * 3600


@dataclass
class Tarefa:
    id: str
    tipo: str                      # 'extracao' | 'busca' | 'ncm' | ...
    pregao: Optional[str]          # slug do pregão a que pertence (ou None)
    titulo: str
    estado: str = "rodando"        # 'rodando' | 'concluida' | 'erro' | 'cancelada'
    fracao: float = 0.0
    detalhe: str = ""
    extra: dict = field(default_factory=dict)
    resultado: Any = None
    erro: Optional[str] = None
    inicio: float = field(default_factory=time.time)
    fim: Optional[float] = None
    _cancelar: threading.Event = field(default_factory=threading.Event, repr=False)
    _amostras: list = field(default_factory=list, repr=False)

    def atualizar(self, fracao: Optional[float] = None, detalhe: Optional[str] = None,
                  **extra) -> None:
        """Chamado de dentro da tarefa. Atribuições simples — seguras entre
        threads para quem só lê o retrato."""
        if fracao is not None:
            self.fracao = max(0.0, min(1.0, float(fracao)))
            agora = time.time()
            if self.fracao > 0:
                self._amostras.append((agora, self.fracao))
                while self._amostras and agora - self._amostras[0][0] > 600:
                    self._amostras.pop(0)
        if detalhe is not None:
            self.detalhe = detalhe
        if extra:
            self.extra.update(extra)

    @property
    def cancelamento_pedido(self) -> bool:
        return self._cancelar.is_set()

    def restante_s(self) -> Optional[float]:
        """Estimativa pelo ritmo dos últimos 5 min (o lote começa rápido e
        depois é dosado pelo limite dos sites — a média desde o início
        prometeria minutos quando falta mais de hora). None = sem base."""
        if self.estado != "rodando" or not self._amostras or self.fracao >= 1:
            return None
        agora = self._amostras[-1][0]
        t0, f0 = next((a for a in self._amostras if agora - a[0] <= 300), self._amostras[-1])
        if agora - t0 < 120 or self.fracao <= f0:
            return None
        return (1.0 - self.fracao) * (agora - t0) / (self.fracao - f0)

    def retrato(self) -> dict:
        """O que a página recebe (JSON)."""
        agora = time.time()
        return {
            "id": self.id, "tipo": self.tipo, "pregao": self.pregao,
            "titulo": self.titulo, "estado": self.estado,
            "fracao": round(self.fracao, 4), "detalhe": self.detalhe,
            "extra": self.extra, "erro": self.erro,
            "decorrido_s": int((self.fim or agora) - self.inicio),
            "restante_s": (lambda r: None if r is None else int(r))(self.restante_s()),
        }


_tarefas: dict[str, Tarefa] = {}
_lock = threading.Lock()


def _limpar_antigas() -> None:
    agora = time.time()
    for tid in [t.id for t in _tarefas.values()
                if t.fim and agora - t.fim > _GUARDA_TERMINADA_S]:
        _tarefas.pop(tid, None)


def ativa(tipo: str, pregao: Optional[str]) -> Optional[Tarefa]:
    """A tarefa `tipo` em andamento para este pregão, se houver."""
    with _lock:
        return next((t for t in _tarefas.values()
                     if t.tipo == tipo and t.pregao == pregao and t.estado == "rodando"), None)


def do_pregao(pregao: Optional[str]) -> list[Tarefa]:
    """Tarefas (rodando ou recém-terminadas) deste pregão, mais nova primeiro."""
    with _lock:
        return sorted((t for t in _tarefas.values() if t.pregao == pregao),
                      key=lambda t: t.inicio, reverse=True)


def rodando() -> list[Tarefa]:
    with _lock:
        return [t for t in _tarefas.values() if t.estado == "rodando"]


def obter(tid: str) -> Optional[Tarefa]:
    with _lock:
        return _tarefas.get(tid)


def iniciar(tipo: str, pregao: Optional[str], titulo: str,
            fn: Callable[[Tarefa], Any]) -> Tarefa:
    """Dispara `fn(tarefa)` numa thread. Se já há uma tarefa do mesmo tipo
    rodando para o pregão, devolve ESSA (dois cliques, ou os dois operadores,
    não disparam duas buscas do mesmo edital)."""
    with _lock:
        _limpar_antigas()
        existente = next((t for t in _tarefas.values()
                          if t.tipo == tipo and t.pregao == pregao and t.estado == "rodando"), None)
        if existente is not None:
            return existente
        tarefa = Tarefa(id=secrets.token_hex(8), tipo=tipo, pregao=pregao, titulo=titulo)
        _tarefas[tarefa.id] = tarefa

    def _corpo() -> None:
        try:
            tarefa.resultado = fn(tarefa)
            tarefa.estado = "cancelada" if tarefa.cancelamento_pedido else "concluida"
            if tarefa.estado == "concluida":
                tarefa.fracao = 1.0
        except Exception as exc:   # noqa: BLE001 — a tarefa nunca derruba o servidor
            from pregoes import erros
            _log.exception("tarefa %s (%s) falhou", tarefa.tipo, tarefa.pregao)
            tarefa.erro = erros.mensagem_amigavel(exc)
            tarefa.estado = "erro"
        finally:
            tarefa.fim = time.time()

    threading.Thread(target=_corpo, name=f"tarefa-{tipo}-{tarefa.id}", daemon=True).start()
    return tarefa


def cancelar(tid: str) -> bool:
    """Pede o cancelamento. A tarefa decide quando parar (entre um item e o
    seguinte) — nada é interrompido no meio de uma escrita."""
    t = obter(tid)
    if t is None or t.estado != "rodando":
        return False
    t._cancelar.set()
    return True
