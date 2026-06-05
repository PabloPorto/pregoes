# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec do app Pregões (Streamlit). **BUILD NO WINDOWS** — PyInstaller
não faz cross-compile; o .exe Windows precisa ser gerado numa máquina Windows.

Modo onedir: gera uma PASTA `dist/Pregoes/` com `Pregoes.exe` dentro. Startup
rápido e os dados do cliente persistem ao lado do .exe (ver pregoes/config.py:
_raiz_gravavel). Use `build_exe.bat` para rodar. Detalhes em BUILD.md.
"""
import os
from PyInstaller.utils.hooks import (collect_all, copy_metadata,
                                     collect_submodules)

datas, binaries, hiddenimports = [], [], []

# Pacotes cujos dados/binários/submódulos o PyInstaller não detecta sozinho.
for pkg in ("streamlit", "altair", "pandas", "pyarrow", "openpyxl",
            "pdfplumber", "pdfminer", "rapidfuzz", "google", "PIL"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as exc:  # pragma: no cover
        print(f"[spec] collect_all({pkg}) falhou: {exc}")

# Metadata (Streamlit/google checam a versão via importlib.metadata).
for dist in ("streamlit", "google-genai", "pdfplumber", "openpyxl",
             "rapidfuzz", "pandas", "altair"):
    try:
        datas += copy_metadata(dist)
    except Exception as exc:  # pragma: no cover
        print(f"[spec] copy_metadata({dist}) falhou: {exc}")

# Código-fonte do app + recursos lidos como ARQUIVO pelo Streamlit/openpyxl.
datas += [
    ("pregoes", "pregoes"),
    ("data/templates/matriz_template.xlsx", "data/templates"),
    (".streamlit/config.toml", ".streamlit"),
]
hiddenimports += collect_submodules("pregoes")
hiddenimports += ["google.genai", "playwright", "playwright.sync_api",
                  "streamlit.runtime.scriptrunner.magic_funcs"]

# Chromium do Playwright (opcional, recomendado p/ cliente leigo): se a pasta
# ms-playwright existir na máquina de build (rode `playwright install chromium`
# ANTES — o build_exe.bat já faz), ela é empacotada e o rthook aponta para ela.
_pw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "ms-playwright")
if os.path.isdir(_pw):
    datas += [(_pw, "ms-playwright")]
    print(f"[spec] Chromium do Playwright empacotado de: {_pw}")
else:
    print("[spec] AVISO: ms-playwright não encontrado — o .exe NÃO terá "
          "Chromium. Rode 'playwright install chromium' antes do build.")

a = Analysis(
    ["run_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=["packaging/pyi_rthook_playwright.py"],
    excludes=["docling", "torch", "tkinter", "tensorflow"],  # peso desnecessário
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="Pregoes", console=True,  # console=True mostra erros — útil no piloto
    disable_windowed_traceback=False, icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Pregoes")
