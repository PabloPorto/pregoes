"""Configurações (`/configuracoes`) — credenciais dos fornecedores, condição
de pagamento, chave da IA, backup e a aba Sistema (para o suporte).

A aba aberta vai na query string (`?aba=dantas`); cada ação devolve só o
painel da aba (`#painel`) com a mensagem do que aconteceu — a página não
recarrega e a mensagem fica na tela, como os avisos do Streamlit.

SEGREDOS: senha e chave salvas NUNCA voltam para o navegador. Os campos
nascem vazios; em branco = manter o que está salvo (no Streamlit a senha ia
pré-preenchida no HTML). A aba Sistema mostra o fim de `data/pregoes.log`
com qualquer senha/chave salva trocada por '•••'.

Textos e regras: inventário 5.1-5.3, 5.5, 5.6 e 6.11 (app.py L1247-1429,
L2095-2155, L3612-3722). O catálogo, que era uma aba aqui, virou a tela
"Meu catálogo" (`rotas_catalogo.py`).
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.routing import Route

from pregoes import backup, config, credenciais, rascunho
from pregoes.web import regras_historico as regras
from pregoes.web import tarefas
from pregoes.web.nucleo import corpo_json, falha, ok, pagina, pedaco

_log = logging.getLogger("pregoes.web")

ABAS = (("almeida", "Almeida"), ("dantas", "Dantas"), ("ia", "IA (Gemini)"),
        ("backup", "Backup"), ("sistema", "Sistema"))
_NOMES_ABAS = dict(ABAS)
# "da Almeida" / "da Dantas" — os dois nomes pedem o mesmo artigo.
_NOME = regras.NOME_FORNECEDOR


# --------------------------------------------------------------------------
# O que cada aba precisa para ser desenhada
# --------------------------------------------------------------------------

def _contexto_fornecedor(forn: str) -> dict:
    atual = credenciais.carregar(forn)
    opcoes = config.carregar_opcoes_pagamento().get(forn, {})
    formas = list(opcoes.get("forma", []))
    prazos = [regras.SEM_PRAZO] + list(opcoes.get("prazo", []))
    pref = credenciais.carregar_preferencia_pagamento(forn) or {}
    # Valor salvo que não existe mais nas opções cai na 1ª (app.py L2105).
    forma_sel = (regras.opcao_por_valor(formas, pref.get("forma_value"))
                 or (formas[0] if formas else {})).get("value")
    prazo_sel = (regras.opcao_por_valor(prazos, pref.get("prazo_value"))
                 or prazos[0]).get("value")
    return {
        "forn": forn, "nome": _NOME[forn],
        "email": atual["email"] if atual else "", "tem_credencial": atual is not None,
        "pode_copiar": forn == "dantas" and atual is None
                       and credenciais.carregar("almeida") is not None,
        "formas": formas, "prazos": prazos, "forma_sel": forma_sel, "prazo_sel": prazo_sel,
        "condicao_atual": regras.texto_condicao(pref.get("forma_texto"),
                                                pref.get("prazo_texto")) if pref else "",
    }


def _contexto_ia() -> dict:
    return {"chave_mascarada": regras.mascarar_chave(credenciais.carregar_chave_api("gemini"))}


def _ultimo_backup_pedido() -> Optional[dict]:
    """Desfecho do último "Fazer backup agora" ainda guardado (ou None)."""
    t = next((t for t in tarefas.do_pregao(None)
              if t.tipo == "backup" and t.estado != "rodando"), None)
    if t is None:
        return None
    r = t.resultado if isinstance(t.resultado, dict) else {}
    return {"ok": bool(r.get("ok")), "arquivo": r.get("arquivo"), "terminou_em": t.fim}


def _contexto_backup() -> dict:
    andamento = tarefas.ativa("backup", None)
    ctx = {"tarefa": andamento.retrato() if andamento else None,
           "pedido": None if andamento else _ultimo_backup_pedido()}
    if config.MODO_SERVIDOR:
        ultimo = backup.ultimo_envio_servidor()
        ctx["ultimo"] = ultimo
        # Alerta vermelho quando o último envio tem mais de 2 dias (app.py L3631).
        ctx["atrasado"] = (ultimo is not None and
                           datetime.now(ultimo.tzinfo) - ultimo > timedelta(days=2))
    else:
        ctx["pasta"] = credenciais.carregar_pasta_backup() or ""
    return ctx


def _segredos() -> list[str]:
    creds = [credenciais.carregar(f) for f in regras.FORNECEDORES]
    return [c["senha"] for c in creds if c] + [credenciais.carregar_chave_api("gemini") or ""]


def _mtime(caminho: Path) -> Optional[float]:
    try:
        return caminho.stat().st_mtime
    except OSError:
        return None


def _contexto_sistema() -> dict:
    """Fatos para o suporte — só leitura, sem nenhum segredo."""
    banco = Path(config.DB_PATH)
    copias = [m for m in (_mtime(p) for p in (banco.parent / "backups").glob("pregoes_*.db"))
              if m is not None]
    if config.MODO_SERVIDOR:
        ultimo = backup.ultimo_envio_servidor()
        fora = f"{ultimo:%d/%m/%Y às %H:%M}" if ultimo else None
        fora_onde = "enviado para fora do servidor"
    else:
        pasta = credenciais.carregar_pasta_backup()
        fora = _mtime(Path(pasta) / backup.NOME_BACKUP_EXTERNO) if pasta else None
        fora_onde = "na pasta do Google Drive" if pasta else "pasta de backup não configurada"
    try:
        tamanho = banco.stat().st_size
    except OSError:
        tamanho = None
    registro = Path(config.DATA_DIR) / "pregoes.log"
    return {
        "pasta_dados": str(config.DATA_DIR),
        "tamanho_banco": regras.tamanho_legivel(tamanho),
        "n_rascunhos": len(list(Path(rascunho.DIR).glob("*.pkl"))),
        "backup_fora": fora, "backup_fora_onde": fora_onde,
        "copia_banco": max(copias) if copias else None,
        "caminho_registro": str(registro),
        "registro": regras.ultimas_linhas_do_registro(registro, 50, _segredos()),
    }


def _contexto(aba: str) -> dict:
    if aba in regras.FORNECEDORES:
        return _contexto_fornecedor(aba)
    return {"ia": _contexto_ia, "backup": _contexto_backup,
            "sistema": _contexto_sistema}[aba]()


def configuracoes(request: Request):
    aba = request.query_params.get("aba")
    aba = aba if aba in _NOMES_ABAS else "almeida"
    return pagina(request, "config.html", secao="config", aba=aba, abas=ABAS,
                  mensagens=[], **_contexto(aba))


def _painel(request: Request, aba: str, *mensagens: tuple[str, str]):
    """Resposta das ações: redesenha o painel da aba com as mensagens
    `(tipo, html)` no topo. O HTML das mensagens é montado AQUI, com todo
    valor variável escapado."""
    return ok(html={"#painel": pedaco(request, "config_painel.html", aba=aba,
                                      mensagens=list(mensagens), **_contexto(aba))})


# --------------------------------------------------------------------------
# Almeida / Dantas — credenciais e condição de pagamento
# --------------------------------------------------------------------------

async def salvar_credenciais(request: Request):
    dados = await corpo_json(request)
    forn = dados.get("fornecedor")
    if forn not in regras.FORNECEDORES:
        return falha("Fornecedor desconhecido.")
    email = str(dados.get("email") or "").strip()
    senha = str(dados.get("senha") or "")
    if not senha:                       # em branco = manter a senha já salva
        atual = credenciais.carregar(forn)
        senha = atual["senha"] if atual else ""
    if not email or not senha:
        return falha("Preencha e-mail e senha antes de salvar.")
    credenciais.salvar(forn, email, senha)
    _log.info("CONFIG: credenciais da %s salvas", _NOME[forn])
    return _painel(request, forn, ("ok", f"Credenciais da {_NOME[forn]} salvas com sucesso."))


def copiar_da_almeida(request: Request):
    """"Usar as mesmas credenciais da Almeida" (aba Dantas, app.py L1299)."""
    origem = credenciais.carregar("almeida")
    if origem is None:
        return falha("Não há credencial da Almeida salva para copiar.")
    credenciais.salvar("dantas", origem["email"], origem["senha"])
    return _painel(request, "dantas",
                   ("ok", "Credenciais da Dantas salvas com sucesso (as mesmas da Almeida)."))


async def salvar_pagamento(request: Request):
    dados = await corpo_json(request)
    forn = dados.get("fornecedor")
    if forn not in regras.FORNECEDORES:
        return falha("Fornecedor desconhecido.")
    opcoes = config.carregar_opcoes_pagamento().get(forn, {})
    forma = regras.opcao_por_valor(opcoes.get("forma", []), dados.get("forma"))
    prazo = regras.opcao_por_valor([regras.SEM_PRAZO] + list(opcoes.get("prazo", [])),
                                   dados.get("prazo") or "")
    if forma is None or prazo is None:
        return falha("Escolha a forma de pagamento na lista.")
    # Texto do prazo gravado vazio quando "Não se aplica" (app.py L2130).
    prazo_texto = prazo["texto"] if prazo["value"] else ""
    credenciais.salvar_preferencia_pagamento(forn, forma["value"], forma["texto"],
                                             prazo["value"], prazo_texto)
    # A sessão viva foi logada com a condição ANTIGA — fecha todas para a
    # próxima busca relogar já com a nova. Só agenda (não espera a fila).
    try:
        from pregoes import scraper
        await run_in_threadpool(scraper.fechar_sessoes_vivas, esperar=False)
    except Exception:
        _log.warning("não consegui fechar as sessões dos fornecedores", exc_info=True)
    condicao = html.escape(regras.texto_condicao(forma["texto"], prazo_texto))
    return _painel(request, forn, ("ok", f"Condição salva: <b>{condicao}</b>. As próximas "
                                         "buscas usam essa condição."))


# --------------------------------------------------------------------------
# IA (Gemini)
# --------------------------------------------------------------------------

def _perguntar_ao_gemini(chave: str) -> Optional[str]:
    """A ÚNICA chamada de rede desta tela: pergunta 'OK' ao Gemini com a
    chave dada (app.py L1387-1404). Isolada para os testes trocarem por um
    dublê. Timeout de 60 s — sem ele um soluço de rede prende o botão."""
    from google import genai
    from google.genai import types

    from pregoes.extrator_llm import MODELO_DEFAULT
    cliente = genai.Client(api_key=chave, http_options=types.HttpOptions(timeout=60_000))
    resposta = cliente.models.generate_content(model=MODELO_DEFAULT,
                                               contents="Responda apenas 'OK'.")
    return resposta.text


def testar_chave_gemini(chave: str) -> tuple[str, str]:
    """`(tipo, html)` do teste da chave. O texto da exceção aparece cru, como
    no Streamlit (inventário 6.13) — menos a própria chave, se vier nele."""
    try:
        texto = _perguntar_ao_gemini(chave)
    except Exception as exc:
        _log.warning("teste da chave Gemini falhou: %s", type(exc).__name__)
        detalhe = regras.ocultar_segredos(str(exc), [chave])
        return "erro", f"Falha ao chamar Gemini: {type(exc).__name__}: {html.escape(detalhe)}"
    if texto and "OK" in texto.upper():
        return "ok", "Conexão com Gemini funcionando."
    return "alerta", f"Resposta inesperada: {html.escape(texto[:80]) if texto else '(vazio)'}"


async def salvar_gemini(request: Request):
    dados = await corpo_json(request)
    chave = str(dados.get("chave") or "").strip()
    testar = dados.get("acao") == "testar"
    salva = credenciais.carregar_chave_api("gemini")
    mensagens: list[tuple[str, str]] = []
    if chave:
        credenciais.salvar_chave_api("gemini", chave)
        if chave.startswith("AIza"):
            mensagens.append(("ok", "Chave Gemini salva com sucesso."))
        else:           # avisa MAS salva (app.py L1372)
            mensagens.append(("alerta", "Chave parece inválida (esperado começar com "
                                        "<code>AIza</code>). Salva mesmo assim, mas verifique."))
    elif not salva:
        return falha("Cole a chave antes de salvar.")
    elif not testar:
        mensagens.append(("info", "O campo ficou em branco — a chave já salva foi mantida."))
    if testar:
        mensagens.append(await run_in_threadpool(testar_chave_gemini, chave or salva))
    return _painel(request, "ia", *mensagens)


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------

def _tarefa_backup(destino_dir: Optional[str]) -> tarefas.Tarefa:
    """"Fazer backup agora" pode levar minutos (no servidor é um envio com
    limite de 10 min) — roda como tarefa, a tela só acompanha."""
    def _fazer(tarefa: tarefas.Tarefa) -> dict:
        if config.MODO_SERVIDOR:
            tarefa.atualizar(0.1, "Enviando a cópia para fora do servidor…")
            return {"ok": backup.enviar_backup_servidor()}
        tarefa.atualizar(0.1, "Copiando para o Google Drive…")
        destino = backup.fazer_backup_externo(destino_dir)
        return {"ok": destino is not None, "arquivo": destino.name if destino else None}
    return tarefas.iniciar("backup", None, "Fazendo o backup…", _fazer)


async def salvar_backup(request: Request):
    dados = await corpo_json(request)
    if config.MODO_SERVIDOR:
        _tarefa_backup(None)
        return _painel(request, "backup")
    caminho = str(dados.get("pasta") or "").strip()
    if dados.get("acao") == "agora":
        # Grava o que estiver no campo, mesmo sem "Salvar" antes (app.py L3706).
        if caminho:
            credenciais.salvar_pasta_backup(caminho)
        _tarefa_backup(caminho or None)
        return _painel(request, "backup")
    credenciais.salvar_pasta_backup(caminho)
    if not caminho:
        mensagem = ("info", "Backup automático desligado (pasta em branco).")
    elif Path(caminho).is_dir():
        mensagem = ("ok", "Pasta de backup salva. A partir de agora o sistema guarda uma "
                          "cópia lá sozinho.")
    else:
        mensagem = ("alerta", "Pasta salva, mas <b>não encontrei</b> ela no computador agora. "
                              "Confira se o Google Drive está aberto e se o caminho está certo "
                              "— o backup só grava quando a pasta existe.")
    return _painel(request, "backup", mensagem)


rotas = [
    Route("/configuracoes", configuracoes),
    Route("/configuracoes/credenciais", salvar_credenciais, methods=["POST"]),
    Route("/configuracoes/copiar-almeida", copiar_da_almeida, methods=["POST"]),
    Route("/configuracoes/pagamento", salvar_pagamento, methods=["POST"]),
    Route("/configuracoes/gemini", salvar_gemini, methods=["POST"]),
    Route("/configuracoes/backup", salvar_backup, methods=["POST"]),
]
