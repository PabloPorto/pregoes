"""O pregão em andamento — estado guardado NO SERVIDOR, um arquivo por pregão.

No Streamlit o pregão vivia na memória da aba (`st.session_state`) e o
rascunho em disco era só a rede de segurança. Aqui é o contrário: o arquivo do
rascunho (`data/rascunhos/<slug>.pkl`, módulo `pregoes.rascunho`) é A fonte da
verdade, e a URL diz qual pregão está aberto (`/pregao/<slug>/…`). Ganhos:
recarregar a página, fechar o navegador ou o colega abrir o mesmo pregão não
perdem nada, e cada tela tem endereço próprio.

COMPATIBILIDADE: as chaves e os tipos gravados são EXATAMENTE os do Streamlit
(`_CHAVES_RASCUNHO` de `pregoes/ui/app.py`) — os rascunhos que o cliente já tem
abrem aqui sem conversão, e o que for salvo aqui abre lá. Não renomear chaves
nem trocar os tipos (dataclasses de `pregoes.extrator`, `pregoes.scraper`,
`pregoes.exportador`).

ÍNDICES (regra central, herdada): `resultados_busca`, `selecoes`,
`itens_nao_usar` e `markup_overrides` usam a posição em `itens_revisados` (a
lista de BUSCA, só os itens marcados). A planilha usa a posição em
`itens_edital` (o edital inteiro). A ponte é `mapa_busca_edital[i_busca] =
i_edital`. Mudar quais itens estão marcados depois de buscar invalida a busca.
"""
from __future__ import annotations

import hashlib
import re
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from pregoes import config, db, rascunho
from pregoes.extrator import ItemExtraido

# Validado ANTES de virar caminho de arquivo (o slug vem da URL).
_RE_SLUG = re.compile(r"[a-z0-9-]{1,80}")

_travas: dict[str, threading.RLock] = {}
_travas_lock = threading.Lock()


def slug_valido(slug: str) -> bool:
    return bool(slug) and _RE_SLUG.fullmatch(slug) is not None


@contextmanager
def trava(slug: str) -> Iterator[None]:
    """Serializa ler-alterar-gravar do MESMO pregão (requisições e tarefas
    rodam em threads; dois operadores podem estar no mesmo pregão)."""
    with _travas_lock:
        t = _travas.setdefault(slug, threading.RLock())
    with t:
        yield


class Pregao:
    """Um pregão aberto: `slug` + o dicionário de estado do rascunho."""

    def __init__(self, slug: str, estado: dict):
        self.slug = slug
        self.estado = estado

    # --- abrir / criar / gravar -------------------------------------------
    @classmethod
    def abrir(cls, slug: str) -> Optional["Pregao"]:
        if not slug_valido(slug):
            return None
        estado = rascunho.carregar_pregao(slug)
        return cls(slug, estado) if estado is not None else None

    @classmethod
    def criar(cls, orgao: str, numero: str, pdf_path: str) -> "Pregao":
        """Rascunho novo, ainda sem extração (a tarefa de leitura preenche)."""
        slug = rascunho.novo_slug(orgao, numero)
        p = cls(slug, {"orgao": orgao, "numero": numero, "pdf_path": pdf_path,
                       "extracao": None, "tela": "upload"})
        p.salvar()
        return p

    def salvar(self) -> None:
        self.estado["tela"] = self.tela_de_trabalho()
        rascunho.salvar(self.estado, rascunho.DIR / f"{self.slug}.pkl")

    def descartar(self) -> None:
        rascunho.remover_pregao(self.slug)

    # --- leitura cômoda ----------------------------------------------------
    def __getitem__(self, chave: str) -> Any:
        return self.estado.get(chave)

    @property
    def orgao(self) -> str:
        return (self.estado.get("orgao") or "").strip()

    @property
    def numero(self) -> str:
        return (self.estado.get("numero") or "").strip()

    @property
    def titulo(self) -> str:
        return titulo_de(self.orgao, self.numero)

    @property
    def extracao(self):
        return self.estado.get("extracao")

    @property
    def itens_edital(self) -> list[ItemExtraido]:
        return self.estado.get("itens_edital") or []

    @property
    def itens_revisados(self) -> list[ItemExtraido]:
        return self.estado.get("itens_revisados") or []

    @property
    def mapa_busca_edital(self) -> list[int]:
        return self.estado.get("mapa_busca_edital") or []

    @property
    def tem_busca(self) -> bool:
        """A PRESENÇA da chave decide pré-busca × pós-busca (lista vazia por
        item = buscou e não achou)."""
        return "resultados_busca" in self.estado

    @property
    def resultados_busca(self) -> dict:
        return self.estado.get("resultados_busca") or {}

    @property
    def selecoes(self) -> dict:
        return self.estado.setdefault("selecoes", {})

    @property
    def itens_nao_usar(self) -> set:
        return self.estado.setdefault("itens_nao_usar", set())

    @property
    def markup_overrides(self) -> dict:
        return self.estado.setdefault("markup_overrides", {})

    # --- revisão: a grade é derivada do que está gravado ---------------------
    def garantir_itens(self) -> None:
        """Na 1ª abertura depois da extração, deriva as três listas da
        extração (item duvidoso nasce DESMARCADO). Depois disso a revisão
        edita `itens_edital` direto — reabrir o pregão traz as edições e as
        marcações de volta (no Streamlit elas se perdiam)."""
        if self.estado.get("itens_edital") is not None or self.extracao is None:
            return
        itens = list(self.extracao.itens)
        marcados = [i for i, it in enumerate(itens) if it.confianca == "extraido"]
        self.definir_itens(itens, marcados)

    def definir_itens(self, itens_edital: list[ItemExtraido], marcados: list[int]) -> None:
        """Grava o edital inteiro e quais posições vão para a busca."""
        marcados = sorted({i for i in marcados if 0 <= i < len(itens_edital)})
        self.estado["itens_edital"] = itens_edital
        self.estado["mapa_busca_edital"] = marcados
        self.estado["itens_revisados"] = [itens_edital[i] for i in marcados]

    def marcados(self) -> set[int]:
        return set(self.mapa_busca_edital)

    def descartar_busca(self) -> None:
        """Volta à pré-busca. Como no Streamlit, NÃO limpa `itens_nao_usar`
        nem `markup_overrides`... aqui limpa: os dois são indexados pela
        posição do item na busca e ficariam apontando para o item errado
        quando a lista de marcados muda (defeito anotado no inventário)."""
        for chave in ("resultados_busca", "selecoes", "pagina_busca", "erros_busca",
                      "avisos_lote_vazio", "avisos_pagamento", "itens_nao_usar",
                      "markup_overrides", "pregao_gerado_hash"):
            self.estado.pop(chave, None)

    # --- ciclo de vida -------------------------------------------------------
    def hash(self) -> str:
        """Impressão digital do que vai para a planilha — mesma fórmula do
        Streamlit (`_hash_pregao`), para um rascunho gerado lá continuar
        reconhecido como 'gerado e intocado' aqui."""
        itens = self.estado.get("itens_edital") or self.estado.get("itens_revisados") or []
        t_itens = tuple((i.item_numero, i.descricao, i.unidade, i.quantidade,
                         i.valor_referencia) for i in itens)
        sel = self.estado.get("selecoes") or {}
        t_sel = tuple(sorted((off, s.preco, s.codigo_fornecedor, s.marca, s.ncm)
                             for off, s in sel.items()))
        ov = tuple(sorted((self.estado.get("markup_overrides") or {}).items()))
        bruto = repr((t_itens, t_sel, None, None, None, ov))
        return hashlib.sha256(bruto.encode("utf-8")).hexdigest()

    @property
    def gerado_e_intocado(self) -> bool:
        h = self.estado.get("pregao_gerado_hash")
        return h is not None and h == self.hash()

    def tela_de_trabalho(self) -> str:
        """Onde o operador continua: 'upload' (lendo o PDF), 'revisao',
        'busca_precos'. Mesmos nomes do Streamlit (gravados no rascunho)."""
        if self.tem_busca or self.estado.get("selecoes"):
            return "busca_precos"
        if self.extracao is not None or self.estado.get("itens_revisados"):
            return "revisao"
        return "upload"

    def caminho_de_trabalho(self) -> str:
        return {"busca_precos": "precos", "revisao": "revisao", "upload": "leitura"}[
            self.tela_de_trabalho()]

    def passo(self) -> int:
        """Etapa atual (0 Edital, 1 Itens, 2 Preços, 3 Planilha)."""
        if self.gerado_e_intocado:
            return 3
        return {"upload": 0, "revisao": 1, "busca_precos": 2}[self.tela_de_trabalho()]


def titulo_de(orgao: Optional[str], numero: Optional[str]) -> str:
    orgao, numero = (orgao or "").strip(), (numero or "").strip()
    if numero and orgao:
        return f"Pregão {numero} — {orgao}"
    if numero:
        return f"Pregão {numero}"
    if orgao:
        return f"Pregão — {orgao}"
    return "Pregão sem identificação"


def em_andamento() -> list[dict]:
    """Pregões guardados que ainda não viraram planilha, mais recente
    primeiro. Quem já tem planilha registrada some da lista SEM apagar o
    arquivo (regra do Streamlit, `_filtrar_rascunhos_finalizados`); erro de
    banco devolve a lista inteira."""
    todos = rascunho.listar_pregoes()
    try:
        with db.get_conn() as conn:
            pendentes = [r for r in todos
                         if not db.pregao_finalizado(conn, r.get("orgao"), r.get("numero"))]
    except Exception:
        pendentes = todos
    for r in pendentes:
        r["titulo"] = titulo_de(r.get("orgao"), r.get("numero"))
    return pendentes


def fornecedores_configurados() -> list[str]:
    from pregoes import credenciais
    return [n for n in ("almeida", "dantas") if credenciais.carregar(n) is not None]
