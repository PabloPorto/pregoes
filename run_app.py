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


def main() -> None:
    # Empacotado pelo PyInstaller, o Streamlit fica em _internal\streamlit\ —
    # sem "site-packages" no caminho, ele se acha em modo de DESENVOLVIMENTO
    # (streamlit/config.py:_global_development_mode) e aí bloqueia
    # --server.port com "server.port does not work when global.developmentMode
    # is true". Forçamos o modo de produção via env var ANTES de o Streamlit
    # ler a config — tem precedência sobre a autodetecção. Ver BUILD.md.
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"
    app_path = _resource(os.path.join("pregoes", "ui", "app.py"))
    threading.Thread(target=_abrir_navegador, daemon=True).start()
    sys.argv = [
        "streamlit", "run", app_path,
        "--server.port=8501",
        "--server.address=127.0.0.1",   # só loopback (não expõe na rede)
        "--server.headless=true",        # nós abrimos o navegador (acima)
        "--browser.gatherUsageStats=false",
    ]
    from streamlit.web import cli as stcli
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
