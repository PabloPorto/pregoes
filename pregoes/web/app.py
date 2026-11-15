"""Aplicação web do Pregões (Starlette). Monta as rotas de cada tela.

Subir em desenvolvimento:  python run_web.py   (http://127.0.0.1:8502)

Mapa de endereços:
    /                         Início
    /novo                     Novo pregão (envio do PDF)
    /pregoes                  Pregões em andamento (todos)
    /pregao/<slug>            → redireciona para a etapa em que o pregão está
    /pregao/<slug>/leitura    Lendo o PDF (tarefa)
    /pregao/<slug>/revisao    Revisão dos itens          (rotas_revisao)
    /pregao/<slug>/mapeamento Mapeamento manual          (rotas_revisao)
    /pregao/<slug>/precos     Busca e escolha de preços  (rotas_precos)
    /pregao/<slug>/planilha   Tributação, margem e geração (rotas_planilha)
    /historico                Planilhas geradas          (rotas_historico)
    /catalogo                 Meu catálogo               (rotas_catalogo)
    /configuracoes            Configurações              (rotas_config)
    /api/tarefas/<id>         Progresso de tarefa demorada
"""
from __future__ import annotations

import contextlib
import logging
import threading

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from pregoes import config, credenciais, db, erros, logconf
from pregoes.web import nucleo, servicos, tarefas
from pregoes.web.nucleo import RAIZ, falha, ok, pagina
from pregoes.web.pregao import Pregao, em_andamento, trava

_log = logging.getLogger("pregoes.web")


# --------------------------------------------------------------------------
# Início e listas
# --------------------------------------------------------------------------

def _contagens() -> dict:
    with db.get_conn() as conn:
        linha = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN status='precificado' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN status='rascunho' THEN 1 ELSE 0 END) FROM editais").fetchone()
        planilhas = conn.execute("SELECT COUNT(*) FROM exports_log").fetchone()[0]
        produtos = conn.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
        aprendidos = conn.execute("SELECT COUNT(*) FROM mapeamentos_aprendidos").fetchone()[0]
    return {"registrados": linha[0] or 0, "precificados": linha[1] or 0,
            "sem_preco": linha[2] or 0, "planilhas": planilhas,
            "produtos": produtos, "aprendidos": aprendidos}


def _situacao_sistema() -> dict:
    return {
        "almeida": credenciais.carregar("almeida") is not None,
        "dantas": credenciais.carregar("dantas") is not None,
        "ia": bool(credenciais.carregar_chave_api("gemini")),
    }


def inicio(request: Request):
    pendentes = em_andamento()
    return pagina(request, "inicio.html", secao="inicio",
                  pendentes=pendentes[:6], total_pendentes=len(pendentes),
                  contagens=_contagens(), situacao=_situacao_sistema(),
                  em_curso=[t.retrato() for t in tarefas.rodando()])


def pregoes_em_andamento(request: Request):
    return pagina(request, "pregoes.html", secao="pregoes", pendentes=em_andamento())


# --------------------------------------------------------------------------
# Novo pregão → leitura do PDF
# --------------------------------------------------------------------------

def novo(request: Request):
    return pagina(request, "novo.html", secao="novo", situacao=_situacao_sistema())


async def novo_enviar(request: Request):
    form = await request.form()
    orgao = (form.get("orgao") or "").strip()
    numero = (form.get("numero") or "").strip()
    arquivo = form.get("pdf")
    if not orgao or not numero:
        return falha("Informe o órgão e o número do pregão para continuar.")
    if arquivo is None or not getattr(arquivo, "filename", ""):
        return falha("Escolha o PDF do edital.")
    conteudo = await arquivo.read()
    if not conteudo[:5].startswith(b"%PDF"):
        return falha("O arquivo enviado não é um PDF. Envie o edital em PDF.")

    def _criar() -> Pregao:
        caminho = servicos.guardar_pdf(conteudo)
        return Pregao.criar(orgao, numero, str(caminho))

    pregao = await run_in_threadpool(_criar)
    _iniciar_leitura(pregao)
    return ok(ir=f"/pregao/{pregao.slug}/leitura")


def _iniciar_leitura(pregao: Pregao) -> tarefas.Tarefa:
    slug = pregao.slug
    return tarefas.iniciar("leitura", slug, f"Lendo o edital — {pregao.titulo}",
                           lambda t: servicos.ler_edital(slug, t))


def _abrir_ou_404(request: Request) -> Pregao | None:
    return Pregao.abrir(request.path_params["slug"])


def _nao_encontrado(request: Request):
    return pagina(request, "erro.html", status=404, titulo="Pregão não encontrado",
                  mensagem="Este pregão não está mais na lista — pode ter sido descartado "
                           "ou já virou planilha (veja em Planilhas geradas).")


def pregao_raiz(request: Request):
    pregao = _abrir_ou_404(request)
    if pregao is None:
        return _nao_encontrado(request)
    return RedirectResponse(f"{RAIZ}/pregao/{pregao.slug}/{pregao.caminho_de_trabalho()}", 303)


def leitura(request: Request):
    pregao = _abrir_ou_404(request)
    if pregao is None:
        return _nao_encontrado(request)
    if pregao.extracao is not None:
        return RedirectResponse(f"{RAIZ}/pregao/{pregao.slug}/revisao", 303)
    tarefa = tarefas.ativa("leitura", pregao.slug)
    ultima = next((t for t in tarefas.do_pregao(pregao.slug) if t.tipo == "leitura"), None)
    return pagina(request, "leitura.html", secao="novo", pregao=pregao,
                  tarefa=tarefa.retrato() if tarefa else None,
                  falhou=ultima.retrato() if (ultima and ultima.estado == "erro") else None)


def leitura_refazer(request: Request):
    """A leitura falhou ou o serviço reiniciou no meio: lê de novo o mesmo PDF."""
    pregao = _abrir_ou_404(request)
    if pregao is None:
        return falha("Pregão não encontrado.", 404)
    _iniciar_leitura(pregao)
    return ok(recarregar=True)


def pregao_descartar(request: Request):
    slug = request.path_params["slug"]
    with trava(slug):
        pregao = Pregao.abrir(slug)
        if pregao is not None:
            for t in tarefas.do_pregao(slug):
                tarefas.cancelar(t.id)
            pregao.descartar()
    return ok(aviso="Pregão descartado.", ir="/")


# --------------------------------------------------------------------------
# Tarefas demoradas
# --------------------------------------------------------------------------

def tarefa_estado(request: Request):
    t = tarefas.obter(request.path_params["tid"])
    if t is None:
        # O serviço reiniciou (as tarefas vivem na memória): a página recarrega
        # e mostra o que ficou gravado.
        return JSONResponse({"id": request.path_params["tid"], "estado": "erro",
                             "erro": "A tarefa não está mais em andamento (o sistema foi "
                                     "reiniciado). O que já tinha sido salvo continua aqui.",
                             "fracao": 0, "titulo": "", "detalhe": "", "extra": {},
                             "decorrido_s": 0, "restante_s": None})
    if t.tipo == "busca" and t.estado == "rodando":
        from pregoes.scraper import espera_da_janela, pausa_por_limite
        t.extra["pausa_s"] = int(pausa_por_limite())
        t.extra["espera_s"] = int(espera_da_janela())
    return JSONResponse(t.retrato())


def tarefa_cancelar(request: Request):
    if tarefas.cancelar(request.path_params["tid"]):
        return ok(aviso="Cancelamento pedido — a tarefa para no próximo item.")
    return falha("Esta tarefa já terminou.")


def saude(request: Request):
    return JSONResponse({"ok": True, "versao": config.VERSAO_APP})


# --------------------------------------------------------------------------
# Montagem
# --------------------------------------------------------------------------

async def _erro_inesperado(request: Request, exc: Exception):
    _log.exception("erro inesperado em %s", request.url.path)
    mensagem = erros.mensagem_amigavel(exc, "atender este pedido")
    if request.headers.get("x-requested-with") == "pregoes":
        return falha(mensagem, 500)
    return pagina(request, "erro.html", status=500, titulo="Algo deu errado",
                  mensagem=f"{mensagem} Seu trabalho está salvo — volte ao Início e "
                           "continue o pregão de onde parou.")


async def _nao_existe(request: Request, exc):
    if request.headers.get("x-requested-with") == "pregoes":
        return falha("Endereço não encontrado.", 404)
    return pagina(request, "erro.html", status=404, titulo="Página não encontrada",
                  mensagem="Este endereço não existe. Volte ao Início.")


def _ao_subir() -> None:
    logconf.configurar()
    db.init_db()
    try:
        db.backup("diario")
    except Exception:
        _log.exception("backup diário do banco falhou ao subir")
    if not config.MODO_SERVIDOR:
        from pregoes import backup
        threading.Thread(target=backup.fazer_backup_externo, daemon=True).start()
    _log.info("Pregões web v%s iniciado (raiz=%r)", config.VERSAO_APP, RAIZ)


def _rotas_das_telas() -> list:
    """Cada grupo de telas mora no seu módulo e expõe `rotas`."""
    from pregoes.web import (rotas_catalogo, rotas_config, rotas_historico,
                             rotas_planilha, rotas_precos, rotas_revisao)
    saida = []
    for modulo in (rotas_revisao, rotas_precos, rotas_planilha, rotas_historico,
                   rotas_catalogo, rotas_config):
        saida.extend(modulo.rotas)
    return saida


@contextlib.asynccontextmanager
async def _vida(app):
    _ao_subir()
    yield


def criar_app() -> Starlette:
    rotas = [
        Route("/", inicio),
        Route("/pregoes", pregoes_em_andamento),
        Route("/novo", novo),
        Route("/novo", novo_enviar, methods=["POST"]),
        Route("/pregao/{slug}", pregao_raiz),
        Route("/pregao/{slug}/leitura", leitura),
        Route("/pregao/{slug}/leitura", leitura_refazer, methods=["POST"]),
        Route("/pregao/{slug}/descartar", pregao_descartar, methods=["POST"]),
        Route("/api/tarefas/{tid}", tarefa_estado),
        Route("/api/tarefas/{tid}/cancelar", tarefa_cancelar, methods=["POST"]),
        Route("/saude", saude),
        *_rotas_das_telas(),
        Mount("/static", StaticFiles(directory=str(nucleo.PASTA / "static")), name="static"),
    ]
    tratadores = {Exception: _erro_inesperado, 404: _nao_existe}
    if not RAIZ:
        return Starlette(routes=rotas, lifespan=_vida, exception_handlers=tratadores)
    # Servido num subcaminho (ex.: /painel atrás do mesmo túnel).
    interno = Starlette(routes=rotas, exception_handlers=tratadores)
    return Starlette(routes=[Mount(RAIZ, app=interno)], lifespan=_vida)


app = criar_app()
