# -*- coding: utf-8 -*-
"""Captura screenshots das telas (para o guia do usuário no PDF).

Sobe o streamlit de demonstração (scripts/_demo_tela.py) por tela, espera
renderizar e tira um screenshot full-page com Playwright. Uso interno.
Rodar: python scripts/_capturar.py [tela1 tela2 ...]
"""
import os
import sys
import time
import socket
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

PORT = 8502
SAIDA = Path("/tmp/guia")
SAIDA.mkdir(exist_ok=True)

# (tela, expandir_primeiro_expander?)
TELAS = {
    "upload": False,
    "revisao": False,
    "busca_precos": True,   # expande o 1º item p/ mostrar os cards de escolha
    "configuracoes": False,
}


def _porta_ativa(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def capturar(tela, expandir):
    env = {**os.environ, "DEMO_TELA": tela,
           "STREAMLIT_GLOBAL_DEVELOPMENT_MODE": "false",
           "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "scripts/_demo_tela.py",
         "--server.port", str(PORT), "--server.headless", "true"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            if _porta_ativa(PORT):
                break
            time.sleep(0.5)
        time.sleep(2)
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1200, "height": 1500},
                              device_scale_factor=2)
            page.goto(f"http://localhost:{PORT}", wait_until="networkidle")
            page.wait_for_timeout(5000)
            if expandir:
                try:
                    page.get_by_text("Item 1", exact=False).first.click()
                    page.wait_for_timeout(1500)
                except Exception as e:
                    print("  (não expandiu:", e, ")")
            out = SAIDA / f"{tela}.png"
            page.screenshot(path=str(out), full_page=True)
            b.close()
        print("  ✓", out)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        time.sleep(1)


if __name__ == "__main__":
    alvos = sys.argv[1:] or list(TELAS)
    for t in alvos:
        print("capturando:", t)
        capturar(t, TELAS.get(t, False))
