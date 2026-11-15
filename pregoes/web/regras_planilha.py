"""Regras da etapa "Planilha" (tributação, margem e geração) — funções PURAS.

Portadas de `pregoes/ui/app.py` (Streamlit), uma a uma, com a linha de origem
no docstring para conferir a paridade. Nada aqui lê disco, banco ou rede: as
rotas (`rotas_planilha.py`) juntam estas peças com o rascunho do pregão, o
exportador e o histórico.

ÍNDICES: `selecoes`, `markup_overrides` e a classificação de ST usam a posição
na lista de BUSCA (`itens_revisados`); a planilha usa a posição no EDITAL
(`itens_edital`). `remapear` é a única ponte (via `mapa_busca_edital`).

DIFERENÇA DELIBERADA do Streamlit: lá o painel de ST gravava um override
"Normal" para TODO item sem NCM a cada desenho da tela. Aqui o override só
existe quando o operador escolhe "Com Substituição Tributária" (escolher
"Normal" remove o override). O resultado da classificação é idêntico — sem
override, item sem NCM = normal (`tributacao.classificar_markups`) — e o
rascunho não muda só por abrir a tela.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Optional

from pregoes import config, tributacao
from pregoes.exportador import eh_dantas
from pregoes.normalizador import normalizar_descricao

# --- Mensagens (texto exato do Streamlit) ---------------------------------

# app.py L826
MSG_SEM_ITENS = "Nenhum item no edital — nada para exportar."
# app.py L837-841
MSG_SEM_IDENTIDADE = (
    "Informe o órgão e o número do pregão antes de gerar a planilha — "
    "eles identificam o pregão no histórico. Volte e preencha os dois campos.")
# app.py L892-895
MSG_HISTORICO_FALHOU = (
    "A planilha foi gerada — baixe abaixo. Só não consegui registrá-la "
    "no <b>histórico</b> agora; gere de novo mais tarde para completar.")
# app.py L2664-2666
LEGENDA_ST = (
    "O preço de venda é calculado na planilha (3 cenários de margem). "
    "Aqui você só confirma os itens com ST — eles saem marcados 'SIM' "
    "na coluna correspondente e a planilha ajusta o imposto sozinha.")
# app.py L2752-2754
LEGENDA_MARGEM = (
    "A planilha já sai com o PREÇO VENDA preenchido nesta margem "
    "(você pode alterar célula a célula no Excel). Fica salvo como seu "
    "padrão para os próximos pregões.")
# app.py L931-932
MSG_CONCLUIDO = ("Pregão concluído e salvo no histórico. Pode baixar a planilha "
                 "acima ou começar o próximo.")


# --- Índices: busca → edital ----------------------------------------------

def remapear(itens_busca: list, itens_edital: Optional[list], mapa: Optional[list],
             selecoes: Optional[dict], markups: Optional[dict] = None,
             itens_com_st: Optional[set] = None
             ) -> tuple[list, dict, Optional[dict], Optional[set]]:
    """Troca a lista de busca pelo EDITAL INTEIRO e leva seleções, markups e ST
    do índice de busca para o índice no edital. Porta de `_gerar_excel`
    (app.py L814-823): só remapeia se há edital e `len(mapa) == len(itens)`;
    senão (rascunho legado) devolve tudo como veio. Chave fora do mapa é
    descartada."""
    selecoes = selecoes or {}
    if itens_edital and mapa is not None and len(mapa) == len(itens_busca):
        def _dict(d: dict) -> dict:
            return {mapa[k]: v for k, v in d.items() if 0 <= k < len(mapa)}
        selecoes = _dict(selecoes)
        markups = _dict(markups) if markups else markups
        if itens_com_st:
            itens_com_st = {mapa[k] for k in itens_com_st if 0 <= k < len(mapa)}
        return itens_edital, selecoes, markups, itens_com_st
    return itens_busca, selecoes, markups, itens_com_st


# --- Substituição Tributária ----------------------------------------------

@dataclass
class Classificacao:
    """Resultado do painel de ST, em índices de BUSCA."""
    markups: dict          # {offset: markup} — só compatibilidade (exportador ignora)
    resumo: dict           # {"st": n, "normal": n, "sem_ncm": n}
    sem_ncm: list          # offsets sem NCM (o operador decide; padrão = normal)
    com_st: set            # offsets com ST — fonte da coluna "SUBST. TRIBUTÁRIA"

    @property
    def st_manual(self) -> int:
        """Quantos itens SEM NCM o operador marcou "Com ST" (o resumo do
        Streamlit não mostrava; aqui confirma que a escolha valeu)."""
        return len(self.com_st.intersection(self.sem_ncm))


def ncms_das_selecoes(selecoes: dict) -> dict:
    """{offset: ncm|None} dos itens escolhidos (app.py L2669)."""
    return {off: getattr(s, "ncm", None) for off, s in selecoes.items()}


def classificar(selecoes: dict, overrides: Optional[dict]) -> Classificacao:
    """Classifica os itens escolhidos: com NCM o Anexo 05 decide (override
    ignorado); sem NCM vale a escolha do operador, padrão Normal. Porta de
    `_painel_markup` (app.py L2668-2716). `MARKUP_ST`/`MARKUP_NORMAL` são só
    sentinelas para distinguir os dois casos — não precificam."""
    m_st, m_normal = config.MARKUP_ST, config.MARKUP_NORMAL
    ncms = ncms_das_selecoes(selecoes)
    markups, resumo, sem_ncm = tributacao.classificar_markups(
        ncms, m_st, m_normal, overrides or {})
    com_st = tributacao.com_st_de_ncms(ncms, m_st, m_normal, overrides or {})
    return Classificacao(markups, resumo, sem_ncm, com_st)


def identificaveis(selecoes: dict, sem_ncm: Iterable[int]) -> list[int]:
    """Itens sem NCM cuja opção escolhida tem `url` — os que o botão
    "Identificar NCM" consegue consultar (app.py L2692)."""
    return [off for off in sem_ncm if getattr(selecoes.get(off), "url", None)]


def marcar_st(overrides: dict, off: int, com_st: bool) -> None:
    """Escolha do operador para um item sem NCM (rádio do app.py L2706-2713).
    "Com ST" grava o sentinela `MARKUP_ST`; "Normal" REMOVE o override (ver a
    diferença deliberada no topo do módulo)."""
    if com_st:
        overrides[off] = config.MARKUP_ST
    else:
        overrides.pop(off, None)


def rotulo_item(itens_busca: list, off: int) -> str:
    """Descrição do item no painel de ST; cai em "Item N" (app.py L2704)."""
    item = itens_busca[off] if 0 <= off < len(itens_busca) else None
    return getattr(item, "descricao", None) or f"Item {off + 1}"


# --- NCM ---------------------------------------------------------------------

def fornecedor_da_url(url: Optional[str]) -> Optional[str]:
    """Fornecedor deduzido do domínio da URL do produto (app.py L2607-2608)."""
    if not url:
        return None
    if "almeidadistribuidor" in url:
        return "almeida"
    if "dantasdistribuidora" in url:
        return "dantas"
    return None


def pedidos_ncm(selecoes: dict, offsets: Iterable[int]) -> list[tuple]:
    """Pedidos `(offset, fornecedor, url, codigo)` para `scraper.consultar_ncms`.
    Porta de `_identificar_ncm` (app.py L2597-2613) SEM o cache de sessão por
    URL: o cache durável do próprio `consultar_ncms` (por fornecedor+código)
    já responde na hora o que foi consultado antes. Sem `url` ou de domínio
    desconhecido → fora."""
    pedidos = []
    for off in offsets:
        sel = selecoes.get(off)
        url = getattr(sel, "url", None)
        forn = fornecedor_da_url(url)
        if forn:
            pedidos.append((off, forn, url, getattr(sel, "codigo_fornecedor", None)))
    return pedidos


def aplicar_ncms(selecoes: dict, overrides: dict, pedidos: list[tuple],
                 ncms: dict, falhas: Iterable[int]) -> int:
    """Grava nas seleções os NCMs DEFINITIVOS e remove o override manual de
    quem ganhou NCM. Porta de app.py L2635-2646. Falha transitória não mexe em
    nada (fica re-tentável). Devolve quantos NCMs foram encontrados.

    Como a consulta roda em segundo plano, o operador pode ter trocado a opção
    do item nesse meio-tempo: só aplica se a seleção ainda é a MESMA (mesma
    `url`) — o NCM é do produto, não da posição."""
    falhas = set(falhas or ())
    achados = 0
    for off, _forn, url, _codigo in pedidos:
        if off in falhas:
            continue
        sel = selecoes.get(off)
        if sel is None or getattr(sel, "url", None) != url:
            continue
        ncm = ncms.get(off)
        selecoes[off] = replace(sel, ncm=ncm)
        if ncm:
            overrides.pop(off, None)
            achados += 1
    return achados


def aviso_falhas_ncm(n_falhas: int) -> str:
    """Aviso das consultas que falharam por motivo passageiro (app.py L2648-2653)."""
    return (f"{n_falhas} item(ns) não puderam ser consultados agora "
            "(login do fornecedor ou navegador). Os demais foram "
            "classificados. Clique em <b>Identificar NCM</b> de novo para "
            "tentar os que faltaram.")


# --- Margem do preço de venda -----------------------------------------------

def opcoes_margem() -> list[tuple[str, str]]:
    """[(coluna, "15%"), …] na ordem de exibição (app.py L2745-2746)."""
    return [(c, f"{int(round(p * 100))}%") for c, p in config.MARGENS_VENDA.items()]


def margem_efetiva(salva: Optional[str]) -> str:
    """Margem em vigor: a preferência salva → padrão de fábrica; valor
    desconhecido cai no padrão (app.py L2747-2751)."""
    atual = salva or config.MARGEM_VENDA_PADRAO
    return atual if atual in config.MARGENS_VENDA else config.MARGEM_VENDA_PADRAO


def deve_salvar_margem(escolha: Optional[str], salva: Optional[str]) -> bool:
    """Persiste SÓ quando a escolha difere do padrão efetivo (salvo ou de
    fábrica) — abrir a tela nunca grava nada (app.py L2766-2771)."""
    if escolha not in config.MARGENS_VENDA:
        return False
    return escolha != (salva or config.MARGEM_VENDA_PADRAO)


# --- Desconto Dantas e resumo ------------------------------------------------

def n_dantas(selecoes: dict) -> int:
    return sum(1 for s in selecoes.values() if eh_dantas(s))


def pct_desconto_dantas() -> int:
    return int(round(config.DESCONTO_DANTAS * 100))


def aviso_desconto_dantas(selecoes: dict) -> Optional[str]:
    """Aviso do painel (app.py L2720-2731); None se não há item da Dantas."""
    n = n_dantas(selecoes)
    if not n:
        return None
    um = n == 1
    return (f"<b>Desconto Dantas:</b> {n} "
            f"{'item escolhido é' if um else 'itens escolhidos são'} da Dantas e "
            f"{'sairá' if um else 'sairão'} com {pct_desconto_dantas()}% de desconto "
            f"no custo, já incluído na planilha.")


def linhas_de_sucesso(n_selecoes: int, n_itens: int, n_itens_dantas: int) -> list[str]:
    """Legendas sob "Planilha gerada" (app.py L913-920): só quando a planilha
    saiu com seleções."""
    if not n_selecoes:
        return []
    linhas = [f"Inclui preço/marca/código para {n_selecoes} de {n_itens} itens."]
    if n_itens_dantas:
        linhas.append(
            f"Desconto Dantas de {pct_desconto_dantas()}% aplicado no custo de "
            f"{n_itens_dantas} {'item' if n_itens_dantas == 1 else 'itens'} da Dantas.")
    return linhas


# --- Aprendizado --------------------------------------------------------------

def registros_aprendizado(itens_busca: list, selecoes: dict,
                          resultados: Optional[dict]) -> list[tuple]:
    """O que gravar em `mapeamentos_aprendidos`: uma tupla
    `(descricao_normalizada, fornecedor, codigo, marca, preco)` por item da
    busca com seleção. Porta de `_registrar_aprendizado` (app.py L3376-3419):
    descrição que normaliza para vazio é pulada; o fornecedor vem da própria
    seleção, com o re-casamento por (preço, código, marca) nas opções do item
    só para seleções antigas sem o campo; sem fornecedor → pula."""
    if not itens_busca or not selecoes:
        return []
    resultados = resultados or {}
    saida = []
    for idx, item in enumerate(itens_busca):
        sel = selecoes.get(idx)
        if sel is None:
            continue
        desc_norm = normalizar_descricao(item.descricao)
        if not desc_norm:
            continue
        fornecedor = getattr(sel, "fornecedor", None)
        if not fornecedor:
            for o in resultados.get(idx, []):
                if (o.preco == sel.preco
                        and o.codigo_fornecedor == sel.codigo_fornecedor
                        and o.marca == sel.marca):
                    fornecedor = o.fornecedor
                    break
        if not fornecedor:
            continue
        saida.append((desc_norm, fornecedor, sel.codigo_fornecedor, sel.marca, sel.preco))
    return saida


# --- Histórico ------------------------------------------------------------------

def status_do_edital(selecoes: Optional[dict]) -> str:
    """`precificado` se a planilha saiu com alguma seleção (app.py L958)."""
    return "precificado" if selecoes else "rascunho"


def prefixo_do_arquivo(orgao: Any, numero: Any) -> str:
    """Começo do nome que `exportador._nome_default` dá às planilhas deste
    pregão (`pregao_<órgão>_<número>_`). Serve para conferir que um arquivo
    pedido para download é mesmo deste pregão."""
    partes = ["pregao"]
    for texto in (orgao, numero):
        texto = (texto or "").strip()
        if texto:
            partes.append("".join(c for c in texto if c.isalnum())[:20])
    return "_".join(partes) + "_"
