"""
Ponto de entrada do executável (PyInstaller). Sobe a UI Streamlit em
localhost:8501 e abre o navegador — para o cliente leigo é "1 clique".

Por que um launcher em vez de chamar `streamlit run` direto: o .exe não tem o
comando `streamlit` no PATH; aqui invocamos o CLI do Streamlit em-processo,
apontando para o app.py empacotado (em sys._MEIPASS quando congelado).

Em desenvolvimento também funciona: `python run_app.py`.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
import urllib.request
import webbrowser

URL = "http://127.0.0.1:8501"


def _resource(rel: str) -> str:
    """Caminho de um recurso empacotado: sys._MEIPASS (congelado) ou a pasta
    deste arquivo (dev)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


# Script injetado no index.html para NEUTRALIZAR os atalhos de teclado do
# Streamlit "c" (abre o popup em inglês "Clear caches") e "r" (reexecuta a
# página, interrompendo uma busca em andamento). Eles disparam com a tecla
# SOZINHA — sem Ctrl — sempre que o foco está fora de um campo de texto: o
# operador clica num botão e já sai digitando o próximo termo (relato de campo
# 2026-10). A 1ª versão deste guard bloqueava só Ctrl+C/Ctrl+R, que o Streamlit
# já ignora — não pegava o gatilho real.
#
# Como funciona: o Streamlit escuta pela lib hotkeys-js, em `document`, na fase
# de BOLHA, e reage no keydown E no keyup. Um listener em <html> (também bolha)
# roda DEPOIS dos campos, do React e da tabela de revisão (data_editor, que usa
# as mesmas teclas para começar a editar uma célula) e ANTES do document — então
# só o atalho do Streamlit deixa de ver a tecla. Sem preventDefault: digitar,
# copiar (Ctrl+C) e recarregar (Ctrl+R) seguem normais.
# (Editar o bundle JS não serve: ele é servido com cache "immutable" de 1 ano e
# o navegador do cliente continuaria usando a cópia antiga.)
_SCRIPT_ATALHOS = (
    '<script id="pp-atalhos-guard-v2">'
    "(function(){function f(e){"
    "if(e.ctrlKey||e.metaKey||e.altKey)return;"
    "if(e.keyCode===67||e.keyCode===82){e.stopPropagation();}}"
    "var h=document.documentElement;"
    "h.addEventListener('keydown',f,false);h.addEventListener('keyup',f,false);"
    "})();"
    "</script>"
)
# Guard da versão anterior (mirava Ctrl+C) — removido de instalações já patcheadas.
_RE_GUARD_ANTIGO = re.compile(r'<script id="pp-atalhos-guard">.*?</script>', re.S)


def _ajustar_pagina_streamlit() -> None:
    """Patches no index.html servido pelo Streamlit, para o operador leigo:

    (1) Declara a página pt-BR + ``translate="no"``: o Streamlit serve
        ``<html lang="en">`` e o navegador em português TRADUZ a página, trocando
        nós do DOM (ex.: "Gemini" → "Gêmeos") — aí o Streamlit não acha o nó e
        quebra com ``NotFoundError: removeChild`` (visto no Windows).
    (2) Neutraliza os atalhos "c" e "r" do Streamlit (ver ``_SCRIPT_ATALHOS``).

    O index.html é servido com ``no-cache``: a correção chega ao navegador do
    cliente no próximo carregamento. Idempotente; falha em silêncio (nunca
    impede o app de subir).
    """
    try:
        import streamlit
        idx = os.path.join(os.path.dirname(streamlit.__file__),
                           "static", "index.html")
        with open(idx, encoding="utf-8") as f:
            html = f.read()
        novo = _patch_index(html)
        if novo != html:
            with open(idx, "w", encoding="utf-8") as f:
                f.write(novo)
    except Exception:
        pass   # ajustes de UX; nunca bloquear o boot por causa disto


def _patch_index(html: str) -> str:
    if 'lang="pt-BR"' not in html:
        html = html.replace('<html lang="en">',
                            '<html lang="pt-BR" translate="no">')
        html = html.replace(
            "<head>", '<head><meta name="google" content="notranslate"/>', 1)
    html = _RE_GUARD_ANTIGO.sub("", html)
    if 'id="pp-atalhos-guard-v2"' not in html:
        html = html.replace("</head>", _SCRIPT_ATALHOS + "</head>", 1)
    return html


def _abrir_navegador() -> None:
    """Espera o servidor responder e abre o navegador. Polling em vez de sleep
    fixo para não abrir antes da UI estar pronta nem tarde demais."""
    for _ in range(120):  # ~60s no máximo
        try:
            urllib.request.urlopen(URL, timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    try:
        webbrowser.open(URL)
    except Exception:
        pass  # sem navegador (raro); o operador abre o endereço manualmente


def _garantir_saida_padrao() -> None:
    """No .exe SEM console (console=False no .spec — sem a "tela preta"), o
    Python zera ``sys.stdout``/``sys.stderr`` e QUALQUER ``print`` do Streamlit
    ou do Playwright quebraria com AttributeError, derrubando o boot em silêncio.
    Redireciona para ``data/console.log`` (ao lado do app) — não trava e ainda
    dá diagnóstico. No dev (com console) é no-op."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
                else os.path.dirname(os.path.abspath(__file__)))
        pasta = os.path.join(base, "data")
        os.makedirs(pasta, exist_ok=True)
        destino = open(os.path.join(pasta, "console.log"), "a",
                       encoding="utf-8", buffering=1)
    except Exception:
        import io
        destino = io.StringIO()   # último recurso: descarta, mas nunca quebra
    if sys.stdout is None:
        sys.stdout = destino
    if sys.stderr is None:
        sys.stderr = destino


# Opções que valem SEMPRE no app do cliente, por cima do config.toml. Ficam
# aqui (e não só no toml) para valerem mesmo se o toml faltar no pacote.
_OPCOES_PRODUCAO = {
    "server.port": "8501",
    "server.address": "127.0.0.1",        # só loopback (não expõe na rede)
    "server.headless": "true",            # nós abrimos o navegador
    "browser.gatherUsageStats": "false",
    # Sem o menu de desenvolvedor: é ele que libera o popup "Clear caches" e o
    # botão "Deploy", ambos em inglês e sem sentido para o operador.
    "client.toolbarMode": "minimal",
    "client.showErrorDetails": "none",    # nunca stacktrace cru na tela
    # Recarregar ao salvar o código é conforto de dev; no cliente o código
    # nunca muda e o observador de arquivos só gasta recurso.
    "server.runOnSave": "false",
    "server.fileWatcherType": "none",
}


def _flags_config() -> list[str]:
    """Flags ``--secao.opcao=valor`` com o ``.streamlit/config.toml`` do app +
    ``_OPCOES_PRODUCAO`` por cima.

    Por que flags: o Streamlit só lê o config.toml da PASTA ATUAL e de
    ``~/.streamlit``. No .exe o toml viaja dentro de ``_internal\\`` e a pasta
    atual é a do atalho — ele NUNCA era lido (achado de campo 2026-10: o cliente
    via o popup "Clear caches" e o botão "Deploy" porque o toolbarMode=minimal
    não valia). Flags de linha de comando têm precedência e independem da pasta.
    """
    opcoes: dict[str, str] = {}
    try:
        import tomllib
        with open(_resource(os.path.join(".streamlit", "config.toml")), "rb") as f:
            toml = tomllib.load(f)
        for secao, valores in toml.items():
            for chave, valor in (valores.items() if isinstance(valores, dict) else ()):
                if isinstance(valor, bool):
                    valor = "true" if valor else "false"
                opcoes[f"{secao}.{chave}"] = str(valor)
    except Exception:
        pass   # sem toml legível: ainda valem as opções de produção abaixo
    opcoes.update(_OPCOES_PRODUCAO)
    return [f"--{chave}={valor}" for chave, valor in opcoes.items()]


def main() -> None:
    # SEM console (console=False): garante stdout/err válidos ANTES de tudo,
    # senão o 1º print do Streamlit/Playwright derruba o boot.
    _garantir_saida_padrao()
    # Empacotado pelo PyInstaller, o Streamlit fica em _internal\streamlit\ —
    # sem "site-packages" no caminho, ele se acha em modo de DESENVOLVIMENTO
    # (streamlit/config.py:_global_development_mode) e aí bloqueia
    # --server.port com "server.port does not work when global.developmentMode
    # is true". Forçamos o modo de produção via env var ANTES de o Streamlit
    # ler a config — tem precedência sobre a autodetecção. Ver BUILD.md.
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"
    # pt-BR (navegador não traduz/quebra) + neutraliza os atalhos "c"/"r"
    _ajustar_pagina_streamlit()
    app_path = _resource(os.path.join("pregoes", "ui", "app.py"))
    # No servidor (deploy/pregoes.service) não há tela: os operadores acessam
    # de outros computadores, então não abrimos navegador local.
    if not os.environ.get("PREGOES_SERVIDOR"):
        threading.Thread(target=_abrir_navegador, daemon=True).start()
    sys.argv = ["streamlit", "run", app_path, *_flags_config()]
    from streamlit.web import cli as stcli
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
