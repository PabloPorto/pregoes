"""
Abstração de fornecedor + esqueletos para Almeida e Dantas.

ESTE ARQUIVO É ESQUELETO. As classes concretas têm o contrato definido e
levantam NotImplementedError. A implementação real (Playwright + parsing
dos sites) vem no próximo lote da Fase 2, depois deste esqueleto ser
revisado.

Decisões registradas em `CLAUDE.md` Seção 7:
- Variação de nome (Q1): scraper marca o item como 'variacao' (amarelo na
  UI). Não bloqueia.
- Sites alvo: almeidadistribuidor.com.br + dantasdistribuidora.com.br.
- Anti-bot: parceiros, sem proteção; usar Playwright headless é suficiente.

Estratégia de busca em 4 camadas, por fornecedor:
1. **Exata** — usa `descricao` como veio do edital.
2. **Limpa** — usa `normalizador.normalizar_descricao(descricao)`.
3. **Keywords** — usa `normalizador.keywords(descricao)` (2-3 tokens).
4. **Código histórico** — se o produto já foi comprado antes, busca pelo
   `codigo_almeida` / `codigo_dantas` no schema `produtos`.

A primeira camada que retornar pelo menos um resultado **para a busca**:
- Camada 1 → confianca = 'exato'
- Camadas 2-4 → confianca = 'variacao'
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ResultadoBusca:
    """
    Um resultado vindo do scraper de um fornecedor.

    Um único item de edital pode retornar múltiplos `ResultadoBusca` (várias
    marcas disponíveis). A escolha entre marcas é sempre do operador humano
    — o sistema NUNCA decide (decisão Resp final 7).
    """
    descricao_encontrada: str
    marca: str
    preco: float
    codigo_fornecedor: str
    confianca: str  # 'exato' | 'variacao'
    fornecedor: str  # 'almeida' | 'dantas'


class Fornecedor(Protocol):
    """
    Contrato que todos os scrapers de fornecedor devem cumprir.

    A implementação concreta encapsula os detalhes do site (URL, seletores,
    forma do campo de busca). O resto do sistema só conhece esta interface.
    """

    nome: str  # 'almeida' | 'dantas'

    def buscar(self, descricao: str) -> list[ResultadoBusca]:
        """
        Busca um produto no site do fornecedor.

        Implementa as 4 camadas internamente. Retorna a primeira camada que
        produzir resultado (lista pode ter múltiplas marcas/variantes do mesmo
        produto). Lista vazia significa "não achou em nenhuma das 4 camadas".

        Não lança exceção em caso de produto inexistente — apenas retorna [].
        Lança em caso de falha de rede, timeout ou mudança brusca de layout
        do site (para a UI capturar e mostrar como erro técnico, distinto de
        "não encontrado").
        """
        ...


class AlmeidaDistribuidor:
    """Scraper de almeidadistribuidor.com.br. Não implementado ainda."""

    nome = "almeida"

    def buscar(self, descricao: str) -> list[ResultadoBusca]:
        raise NotImplementedError(
            "AlmeidaDistribuidor.buscar() será implementado no próximo lote "
            "da Fase 2 (Playwright + parsing do site)."
        )


class DantasDistribuidora:
    """Scraper de dantasdistribuidora.com.br. Não implementado ainda."""

    nome = "dantas"

    def buscar(self, descricao: str) -> list[ResultadoBusca]:
        raise NotImplementedError(
            "DantasDistribuidora.buscar() será implementado no próximo lote "
            "da Fase 2 (Playwright + parsing do site)."
        )
