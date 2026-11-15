"""Regras da tela de Preços: ranking das opções, escolha default, o que
aparece em cada item, avisos pós-busca e a busca no catálogo.

Todas são portas PURAS de `pregoes/ui/app.py` (o Streamlit): não tocam em
sessão, banco, rede nem tela — recebem dados e devolvem dados. Cada função
traz a linha de origem para conferir a paridade (os testes em
`tests/test_web_precos.py` comparam com as originais). O que NÃO existe no
Streamlit está marcado como "novo".

Tipos que circulam (todos do backend): `scraper.ResultadoBusca` (uma opção),
`exportador.SelecaoFornecedor` (a escolha gravada), `extrator.ItemExtraido`.
Índices são sempre a posição do item em `itens_revisados`.
"""
from __future__ import annotations

import hashlib
import html
import math
import unicodedata
from typing import Any, Callable, Iterable, Optional

from pregoes import config
from pregoes.exportador import SelecaoFornecedor
from pregoes.normalizador import normalizar_descricao, relevancia
from pregoes.ranking import bonus_codigo

# --------------------------------------------------------------------------
# Ranking e escolha
# --------------------------------------------------------------------------


def chave_default_opcao(item_desc: str, o) -> tuple:
    """Chave de ordenação das opções (menor vence): escolha anterior do
    operador > já no catálogo > código forte do edital > relevância > menor
    preço. Origem: `_chave_default_opcao` (app.py L3055)."""
    return (-int(getattr(o, "veio_por_aprendizado", False)),
            -int(getattr(o, "no_catalogo", False)),
            -bonus_codigo(item_desc, o.descricao_encontrada),
            -relevancia(item_desc, o.descricao_encontrada), o.preco)


def melhor_opcao(item_desc: str, opcoes: list):
    """A opção pré-selecionada. Origem: `_melhor_opcao` (app.py L3071)."""
    return min(opcoes, key=lambda o: chave_default_opcao(item_desc, o))


def selecao_de(o) -> SelecaoFornecedor:
    """A escolha gravada no rascunho para a opção `o`. Origem:
    `_escolher_opcao` (app.py L2439)."""
    return SelecaoFornecedor(
        preco=o.preco, codigo_fornecedor=o.codigo_fornecedor, marca=o.marca,
        ncm=getattr(o, "ncm", None), url=getattr(o, "url", None),
        fornecedor=getattr(o, "fornecedor", None))


def mesma_selecao(o, s) -> bool:
    """A opção `o` é a escolha `s`? Identidade visível = preço + marca +
    código (sem fornecedor). Origem: `_mesma_selecao` (app.py L2546)."""
    return (s is not None and o.preco == s.preco and o.marca == s.marca
            and o.codigo_fornecedor == s.codigo_fornecedor)


def dedup_opcoes(opcoes: list) -> list:
    """Remove opções repetidas por (fornecedor, código, marca, preço),
    preservando a ordem. Origem: `_dedup_opcoes` (app.py L2571)."""
    vistos: set = set()
    unicas: list = []
    for o in opcoes:
        chave = (o.fornecedor, o.codigo_fornecedor, o.marca, o.preco)
        if chave not in vistos:
            vistos.add(chave)
            unicas.append(o)
    return unicas


def opcoes_visiveis(opcoes_todas: list, max_exibidas: int, sel_atual) -> list:
    """As `max_exibidas` primeiras opções, SEMPRE com a escolhida (se ficou
    fora do corte, entra no topo). Origem: `_opcoes_visiveis` (app.py L2555)."""
    opcoes = opcoes_todas[:max_exibidas]
    if sel_atual is not None and not any(mesma_selecao(o, sel_atual) for o in opcoes):
        escolhida = next((o for o in opcoes_todas if mesma_selecao(o, sel_atual)), None)
        if escolhida is not None:
            opcoes = [escolhida, *opcoes]
    return opcoes


def selecoes_default(itens: list, resultados: dict, selecoes: Optional[dict] = None,
                     nao_usar: Iterable[int] = ()) -> dict:
    """Escolha default (melhor opção) para todo item com opções — o operador
    pode gerar a planilha sem abrir nenhum item. Origem: `_mostrar_selecao`
    (app.py L3096-3108), que fazia isso na 1ª exibição; aqui a tarefa de busca
    chama ao terminar. Não troca escolha existente nem toca em "não usar"."""
    selecoes = {} if selecoes is None else selecoes
    nao_usar = set(nao_usar)
    for idx, opcoes in resultados.items():
        if opcoes and idx not in selecoes and idx not in nao_usar and 0 <= idx < len(itens):
            selecoes[idx] = selecao_de(melhor_opcao(itens[idx].descricao, opcoes))
    return selecoes


def mesclar(itens: list, indices: list[int], novos: dict, resultados: dict,
            selecoes: dict, nao_usar: Iterable[int]) -> None:
    """Aplica uma re-busca PARCIAL: troca só as entradas re-buscadas; item que
    agora tem opções, sem escolha e fora de "não usar", ganha a default.
    Nenhuma escolha existente é tocada. Altera `resultados` e `selecoes` no
    lugar. Origem: `_mesclar_busca_incremental` (app.py L1742)."""
    resultados.update(novos)
    nao_usar = set(nao_usar)
    for idx in indices:
        bag = resultados.get(idx) or []
        if bag and idx not in selecoes and idx not in nao_usar:
            selecoes[idx] = selecao_de(melhor_opcao(itens[idx].descricao, bag))


def faltantes(n_itens: int, resultados: dict, nao_usar: Iterable[int]) -> list[int]:
    """Itens sem nenhuma opção e fora de "não usar" — o alvo da re-busca
    incremental. Origem: app.py L1502."""
    nao_usar = set(nao_usar)
    return [i for i in range(n_itens) if not resultados.get(i) and i not in nao_usar]


def metricas(total: int, resultados: dict, selecoes: dict) -> dict:
    """Os números do topo da lista. Origem: `_mostrar_selecao` (app.py
    L3111-3128)."""
    com_resultado = sum(1 for r in resultados.values() if r)
    com_variacao = sum(1 for r in resultados.values()
                       if r and any(x.confianca == "variacao" for x in r))
    return {"total": total, "com_preco": com_resultado - com_variacao,
            "por_variacao": com_variacao, "sem_resultado": total - com_resultado,
            "selecionados": len(selecoes)}


# --------------------------------------------------------------------------
# Apresentação de um item
# --------------------------------------------------------------------------

def numero_item_edital(item, idx: int) -> int:
    """Número do item NO EDITAL (o mesmo da coluna ITEM da planilha); sem
    número, a posição + 1. Um número 0 é válido. Origem:
    `_numero_item_edital` (app.py L2450)."""
    n = getattr(item, "item_numero", None)
    return n if n is not None else idx + 1


def titulo_item(descricao: str) -> str:
    """Descrição do edital como título legível: palavra TODA maiúscula vira
    Capitalizada, menos os tokens técnicos (têm dígito, "/" ou até 3 letras);
    corta em 95 caracteres. Origem: `_titulo_item` (app.py L2463)."""
    texto = (descricao or "").strip().rstrip(" .;,-")
    palavras = []
    for p in texto.split():
        tecnico = any(c.isdigit() for c in p) or "/" in p or len(p) <= 3
        palavras.append(p.capitalize() if (p.isupper() and not tecnico) else p)
    titulo = " ".join(palavras)
    if len(titulo) > 95:
        titulo = titulo[:95].rstrip() + "…"
    return (titulo[:1].upper() + titulo[1:]) if titulo else titulo


def pilula_de(pos: int, o, preco_min: float) -> Optional[tuple[str, str]]:
    """A pílula de uma opção, por prioridade: escolha anterior > catálogo >
    1ª do ranking > menor preço entre as visíveis. Devolve (texto, classe) ou
    None. Origem: `_badge_de` (app.py L3257)."""
    if getattr(o, "veio_por_aprendizado", False):
        return ("sua escolha anterior", "aprendido")
    if getattr(o, "no_catalogo", False):
        return ("no seu catálogo", "catalogo")
    if pos == 0:
        return ("Melhor match", "match")
    if o.preco == preco_min:
        return ("Mais barato", "barato")
    return None


# Situação do item — chave (para filtro e cor), na precedência do Streamlit.
SITUACOES = (("todos", "Todos"), ("com_preco", "Com preço"), ("conferir", "A conferir"),
             ("sem_resultado", "Sem resultado"), ("sem_preco", "Sem preço (você marcou)"))


def status_item(visiveis: list, nao_usar: bool) -> tuple[str, str, str]:
    """(chave, rótulo, cor) do item. Precedência: não usar > sem opções >
    alguma opção visível por variação > ok. Origem: app.py L3184-3195."""
    n = len(visiveis)
    if nao_usar:
        return ("sem_preco", "sem preço (você marcou)", "")
    if not visiveis:
        return ("sem_resultado", "Não encontrado", "erro")
    if any(o.confianca == "variacao" for o in visiveis):
        return ("conferir", f"{n} a conferir", "alerta")
    return ("com_preco", f"{n} {'opção' if n == 1 else 'opções'}", "ok")


def _ordenadas(item_desc: str, opcoes: list) -> list:
    return sorted(opcoes, key=lambda o: chave_default_opcao(item_desc, o))


def situacao_de(item, opcoes: list, sel_atual, nao_usar: bool,
                max_exibidas: Optional[int] = None) -> str:
    """Só a chave da situação, sem montar o cartão (novo — para contar e
    filtrar centenas de itens sem ordenar tudo). Dá o MESMO resultado de
    `visao_item(...)["status"]`: o ranking só é calculado quando decide algo
    (há opções exatas E por variação e nem todas cabem no corte)."""
    if nao_usar:
        return "sem_preco"
    unicas = dedup_opcoes(opcoes or [])
    if not unicas:
        return "sem_resultado"
    max_exibidas = config.MAX_OPCOES_EXIBIDAS if max_exibidas is None else max_exibidas
    variacoes = sum(1 for o in unicas if o.confianca == "variacao")
    if variacoes == 0:
        return "com_preco"
    if variacoes == len(unicas) or len(unicas) <= max_exibidas:
        return "conferir"
    visiveis = opcoes_visiveis(_ordenadas(item.descricao, unicas), max_exibidas, sel_atual)
    return status_item(visiveis, False)[0]


def token_opcao(o) -> str:
    """Identificador curto de uma opção dentro do item (novo): a mesma
    identidade do dedup — é o que o botão "escolher" manda de volta."""
    bruto = repr((o.fornecedor, o.codigo_fornecedor, o.marca, o.preco))
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()[:12]


def opcao_por_token(opcoes: list, token: str):
    return next((o for o in dedup_opcoes(opcoes or []) if token_opcao(o) == token), None)


def _e_a_escolhida(o, s) -> bool:
    """Qual cartão leva a marca "escolhido" (novo). `mesma_selecao` não olha o
    fornecedor; quando Almeida e Dantas trazem o mesmo código, marca e preço,
    os DOIS cartões apareciam escolhidos. A escolha gravada sabe de quem é
    (`fornecedor`) — usa isso para marcar um só; escolha antiga, sem
    fornecedor, cai na regra de sempre."""
    if not mesma_selecao(o, s):
        return False
    return not getattr(s, "fornecedor", None) or o.fornecedor == s.fornecedor


def _cartao_opcao(o, pos: Optional[int], preco_min: Optional[float], sel_atual) -> dict:
    """Os textos de um cartão de opção. Origem: `_card_opcao` (app.py L2484):
    marca vazia → "Sem marca"; descrição encontrada cortada em 72."""
    desc = (o.descricao_encontrada or "").strip()
    if len(desc) > 72:
        desc = desc[:72].rstrip() + "…"
    if pos is None:      # além do corte: só as pílulas que não dependem da posição
        pilula = pilula_de(-1, o, float("nan"))
    else:
        pilula = pilula_de(pos, o, preco_min)
    return {"o": o, "k": token_opcao(o), "escolhida": _e_a_escolhida(o, sel_atual),
            "pilula": pilula, "marca": (o.marca or "Sem marca").strip() or "Sem marca",
            "desc": desc, "fornecedor": (o.fornecedor or "").capitalize(),
            "fornecedor_chave": (o.fornecedor or "").lower(),
            "codigo": (o.codigo_fornecedor or "").strip()}


def visao_item(item, idx: int, opcoes: list, sel_atual, nao_usar: bool,
               max_exibidas: Optional[int] = None) -> dict:
    """Tudo o que o cartão de um item mostra. Origem: `_mostrar_selecao`
    (app.py L3171-3300): dedup → ordena pela chave default → corta em
    `config.MAX_OPCOES_EXIBIDAS` (a escolhida sempre aparece) → status →
    destaque (a escolhida; sem escolha, a 1ª do ranking) → pílulas.

    Diferença de apresentação (novo): as opções além do corte vêm em
    `ocultas` para o operador poder abri-las — no Streamlit só havia a
    contagem "+N menos relevantes"."""
    max_exibidas = config.MAX_OPCOES_EXIBIDAS if max_exibidas is None else max_exibidas
    descricao = item.descricao or ""
    unicas = dedup_opcoes(opcoes or [])
    # A chave do ranking é calculada UMA vez por opção (a relevância é a parte
    # cara) e serve às duas ordenações abaixo, como no Streamlit.
    chaves = {id(o): chave_default_opcao(descricao, o) for o in unicas}
    todas = sorted(unicas, key=lambda o: chaves[id(o)])
    visiveis = opcoes_visiveis(todas, max_exibidas, sel_atual)
    chave, rotulo, cor = status_item(visiveis, nao_usar)

    cartoes: list[dict] = []
    if visiveis:
        ordenadas = sorted(visiveis, key=lambda o: chaves[id(o)])
        preco_min = min(o.preco for o in ordenadas)
        cartoes = [_cartao_opcao(o, p, preco_min, sel_atual)
                   for p, o in enumerate(ordenadas)]
        if sel_atual is not None and not any(c["escolhida"] for c in cartoes):
            # A opção do fornecedor escolhido não está entre as visíveis (caso
            # de borda): vale a identidade de sempre, para a escolha aparecer.
            for c in cartoes:
                c["escolhida"] = mesma_selecao(c["o"], sel_atual)
        destaque = next((p for p, c in enumerate(cartoes) if c["escolhida"]), 0)
        cartoes.insert(0, cartoes.pop(destaque))
        cartoes[0]["destaque"] = True
    dentro = {id(o) for o in visiveis}
    ocultas = [_cartao_opcao(o, None, None, sel_atual) for o in todas if id(o) not in dentro]

    titulo = titulo_item(descricao)
    literal = descricao.strip()
    return {
        "i": idx, "numero": numero_item_edital(item, idx), "titulo": titulo,
        "descricao": descricao,
        # O título encurta ou reescreve a descrição? Então vale oferecer a literal.
        "descricao_longa": len(literal) > 95 or "\n" in literal,
        "unidade": getattr(item, "unidade", None),
        "quantidade": getattr(item, "quantidade", None),
        "nao_usar": nao_usar, "status": chave, "rotulo": rotulo, "cor": cor,
        "selecao": None if nao_usar else sel_atual,
        "opcoes": cartoes, "ocultas": ocultas, "n_total": len(todas),
    }


def _sem_acento(texto: str) -> str:
    bruto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in bruto if not unicodedata.combining(c)).casefold()


def casa_texto(consulta: str, item, idx: int, sel_atual) -> bool:
    """Filtro de texto da lista (novo): todas as palavras da consulta
    aparecem no número, na descrição, na marca ou no código escolhidos —
    sem diferenciar maiúsculas nem acentos."""
    palavras = _sem_acento(consulta).split()
    if not palavras:
        return True
    alvo = _sem_acento(" ".join(str(p) for p in (
        numero_item_edital(item, idx), item.descricao or "",
        getattr(sel_atual, "marca", "") or "",
        getattr(sel_atual, "codigo_fornecedor", "") or "")))
    return all(p in alvo for p in palavras)


# --------------------------------------------------------------------------
# Avisos da busca
# --------------------------------------------------------------------------

def condicoes_pagamento(fornecedores: list[str],
                        carregar_preferencia: Callable[[str], Optional[dict]]
                        ) -> tuple[list[str], list[str]]:
    """(condições configuradas "Fornecedor: forma[ · prazo]", fornecedores sem
    condição). Origem: `_mostrar_condicao_pagamento` (app.py L2158)."""
    configuradas, falta = [], []
    for nome in fornecedores:
        pref = carregar_preferencia(nome)
        if pref:
            cond = pref.get("forma_texto") or ""
            if pref.get("prazo_texto"):      # PIX/antecipado não têm prazo
                cond += " · " + pref["prazo_texto"]
            configuradas.append(f"{nome.capitalize()}: {cond}")
        else:
            falta.append(nome.capitalize())
    return configuradas, falta


def avisos_pagamento_de(sessoes: dict) -> dict:
    """Alertas (HTML) quando a condição de pagamento de um fornecedor NÃO
    ficou confirmada na sessão — os preços podem estar na condição errada.
    Origem: `_avisos_pagamento_de` (app.py L1582). A forma vem escapada (lá
    entrava crua no HTML)."""
    avisos: dict = {}
    for nome, forn in sessoes.items():
        status, forma = (getattr(forn, "_pagamento_status", None) or ("sem_config", None))
        rotulo = nome.capitalize()
        forma_txt = f" (configurada: {html.escape(str(forma), quote=False)})" if forma else ""
        if status == "nao_pegou":
            avisos[nome] = (
                f"<b>Atenção — {rotulo}:</b> a condição de pagamento NÃO foi "
                f"aplicada{forma_txt}. Os preços podem estar na condição errada "
                "do site. Confira um item conhecido antes de gerar a planilha.")
        elif status in ("nao_conferida", "falhou"):
            avisos[nome] = (
                f"<b>Atenção — {rotulo}:</b> não consegui confirmar a condição "
                f"de pagamento{forma_txt} agora — normalmente é instabilidade "
                "momentânea do site do fornecedor, não erro da condição. Tente "
                "buscar de novo em alguns minutos; se persistir, confira um item "
                "conhecido antes de gerar a planilha.")
    return avisos


def avisos_lote_vazio(stats_concluidos: list, total_itens: int) -> list[str]:
    """Fornecedor que voltou ZERO em TODOS os itens de um lote de 5 ou mais =
    site instável, não "nada existe". `stats_concluidos`: tuplas (nome, ok,
    variação, não achou). Origem: `_avisos_lote_vazio` (app.py L1607)."""
    avisos: list[str] = []
    if total_itens < 5:
        return avisos
    for nome, ok, var, _nao in stats_concluidos:
        if ok + var == 0:
            avisos.append(
                f"<b>Atenção — {nome.capitalize()}:</b> nenhum resultado em "
                f"NENHUM dos {total_itens} itens. Isso normalmente é o site do "
                "fornecedor instável ou fora do ar, não falta de produto. "
                "Aguarde alguns minutos e use "
                "<b>Buscar preços dos itens sem resultado</b> antes de confiar "
                "nos itens marcados como não encontrados.")
    return avisos


def avisos_recusa(erros: list) -> list[str]:
    """Itens que ficaram sem consulta porque o site RECUSOU (limite de
    acessos): um aviso por fornecedor. Origem: `_avisos_recusa` (app.py
    L1635)."""
    from pregoes.scraper import _MENSAGEM_RECUSA
    por_forn: dict = {}
    for e in erros or []:
        if (getattr(e, "mensagem", "") or "").startswith(_MENSAGEM_RECUSA):
            for nome in (e.fornecedor or "").split(","):
                por_forn.setdefault(nome, set()).add(e.indice_item)
    return [
        f"<b>Atenção — {nome.capitalize()}:</b> o site recusou consultas "
        f"(limite de acessos) e <b>{len(idx)} itens</b> ficaram sem preço deste "
        "fornecedor. O que já foi encontrado está mantido. Aguarde uns "
        "10 minutos e use <b>Buscar preços dos itens sem resultado</b>."
        for nome, idx in por_forn.items() if nome
    ]


def resumo_do_lote(n_itens: int, cache_hits: int, cache_misses: int, n_erros: int) -> str:
    """A frase de fim de busca. Origem: `_executar_busca` (app.py L2058-2068)."""
    extras = []
    if cache_hits or cache_misses:
        extras.append(f"{cache_hits} do cache, {cache_misses} buscas reais")
    if n_erros:
        extras.append(f"{n_erros} erro(s) técnico(s)")
    sub = f" ({' · '.join(extras)})" if extras else ""
    return f"Busca concluída — {n_itens} itens processados{sub}."


# --------------------------------------------------------------------------
# Estimativa de tempo (novo)
# --------------------------------------------------------------------------

# Consultas por item em cada fornecedor: a busca tenta mais de um termo quando
# o 1º não acha. Medido em itens reais (2026-11): ~1,6 — e 86 itens nos dois
# fornecedores levaram 32 min, o que a conta abaixo reproduz (30 min + rede).
CONSULTAS_POR_ITEM = 1.6


def estimativa_busca(n_itens: int, n_fornecedores: int, ritmo: str = "seguro") -> dict:
    """Quanto um lote deve levar, pelo limite dos sites (Q20) e pelo ritmo
    escolhido (`scraper.ritmo_da_busca`):

    - "seguro": `config.SCRAPER_MAX_CONSULTAS_POR_JANELA` consultas a cada
      `SCRAPER_JANELA_SEGUNDOS`, somando os dois fornecedores. O lote anda em
      LEVAS: a 1ª sai na hora e cada leva seguinte espera a janela da anterior.
    - "rapido": consulta sem dosar até o site bloquear (a cada
      `SCRAPER_CONSULTAS_ATE_BLOQUEIO`) e espera o bloqueio passar
      (`SCRAPER_BLOQUEIO_SEGUNDOS`). Entre as pausas, quem manda é o teto da
      rajada (`SCRAPER_MAX_REQ_POR_SEGUNDO` por fornecedor).

    É o pior caso — item consultado nos últimos dias responde da memória, sem
    gastar consulta."""
    consultas = math.ceil(n_itens * max(n_fornecedores, 1) * CONSULTAS_POR_ITEM)
    if ritmo == "rapido":
        maximo = config.SCRAPER_CONSULTAS_ATE_BLOQUEIO
        pausa_s = config.SCRAPER_BLOQUEIO_SEGUNDOS
        levas = max(1, math.ceil(consultas / maximo))
        por_segundo = config.SCRAPER_MAX_REQ_POR_SEGUNDO * max(n_fornecedores, 1)
        segundos = consultas / por_segundo + (levas - 1) * pausa_s
    else:
        ritmo = "seguro"
        maximo = config.SCRAPER_MAX_CONSULTAS_POR_JANELA
        pausa_s = config.SCRAPER_JANELA_SEGUNDOS
        levas = max(1, math.ceil(consultas / maximo))
        segundos = (levas - 1) * pausa_s
    return {"consultas": consultas, "levas": levas, "segundos": int(segundos),
            "texto": _duracao(segundos), "limite": maximo, "ritmo": ritmo,
            "janela_min": int(round(pausa_s / 60))}


def espera_em_minutos(segundos: float) -> int:
    """"a próxima leva sai em ~N min" (mínimo 1)."""
    return max(1, round(segundos / 60))


def _duracao(segundos: float) -> str:
    minutos = segundos / 60
    if minutos < 5:
        return "poucos minutos"
    if minutos < 90:
        return f"cerca de {int(5 * round(minutos / 5))} minutos"
    horas = round(minutos / 30) / 2          # de meia em meia hora
    texto = f"{horas:.1f}".replace(".", ",").replace(",0", "")
    return f"cerca de {texto} {'hora' if horas == 1 else 'horas'}"


# --------------------------------------------------------------------------
# Busca manual no catálogo
# --------------------------------------------------------------------------

def buscar_no_catalogo(catalogo: list, consulta: str, limite: int = 6) -> list:
    """Procura no catálogo do cliente (`matcher.ProdutoCatalogo`), em 3 modos:
    (1) consulta só com dígitos (2 ou mais) → código Almeida/Dantas igual ou
    com esse prefixo; (2) palavras → descrições que contêm TODAS, ordenadas
    por proximidade e tamanho; (3) nenhuma contém todas → fuzzy tolerante
    (corte 60). Origem: `_buscar_no_catalogo` (app.py L2787)."""
    from rapidfuzz import fuzz, process
    q = (consulta or "").strip()
    if not q or not catalogo:
        return []
    so_digitos = q.replace(" ", "")
    if so_digitos.isdigit() and len(so_digitos) >= 2:
        return [
            p for p in catalogo
            if any((c or "").strip() == so_digitos
                   or (c or "").strip().startswith(so_digitos)
                   for c in (p.codigo_almeida, p.codigo_dantas))
        ][:limite]
    alvo = normalizar_descricao(q)
    palavras = [w for w in alvo.split() if len(w) >= 2]
    escolhas = {p.produto_id: (p.descricao_canonica or "") for p in catalogo}
    por_id = {p.produto_id: p for p in catalogo}
    if palavras:
        contendo = [(pid, d) for pid, d in escolhas.items()
                    if all(w in d for w in palavras)]
        if contendo:
            contendo.sort(key=lambda x: (-fuzz.token_set_ratio(alvo, x[1]), len(x[1])))
            return [por_id[pid] for pid, _ in contendo[:limite]]
    r = process.extract(alvo, escolhas, scorer=fuzz.token_set_ratio,
                        limit=limite, score_cutoff=60)
    return [por_id[pid] for _, _, pid in r]


def linhas_do_catalogo(achados: list, ultima_cotacao: Callable[[int, str], Any]) -> list[dict]:
    """Uma linha por (produto × fornecedor que tem código), com a referência
    da última compra — só informativa: o preço usado é sempre o atual do
    site. `ultima_cotacao(produto_id, fornecedor)` devolve a linha de
    `db.ultima_cotacao` ou None. Origem: `_busca_catalogo_item` (app.py
    L2873-2902)."""
    linhas: list[dict] = []
    for p in achados:
        for forn, codigo in (("almeida", p.codigo_almeida), ("dantas", p.codigo_dantas)):
            if not (codigo and str(codigo).strip()):
                continue
            cot = ultima_cotacao(p.produto_id, forn)
            linhas.append({
                "produto_id": p.produto_id, "fornecedor": forn,
                "codigo": str(codigo).strip(), "marca": p.marca or "Sem marca",
                "descricao": (p.descricao_canonica or "")[:58],
                "ref_preco": float(cot["preco"]) if cot else None,
                "ref_data": (cot["consultado_em"] or "")[:10] if cot else None,
            })
    return linhas
