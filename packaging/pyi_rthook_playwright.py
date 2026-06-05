"""Runtime hook do PyInstaller: aponta o Playwright para o Chromium EMPACOTADO.

Quando o .exe roda, se a pasta `ms-playwright` foi incluída no bundle (ver
pregoes.spec + `playwright install chromium` antes do build), o Playwright a
usa em vez de tentar baixar — o cliente leigo não precisa rodar nada.

Fica em packaging/ (não em build/) porque build/ é o diretório de trabalho do
PyInstaller e `--clean` o apaga.
"""
import os
import sys

if getattr(sys, "frozen", False):
    _base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    _browsers = os.path.join(_base, "ms-playwright")
    if os.path.isdir(_browsers):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _browsers)
