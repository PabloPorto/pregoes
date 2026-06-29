@echo off
REM ============================================================
REM  Build do executavel Pregoes (RODAR NO WINDOWS).
REM  Gera:  dist\Pregoes\Pregoes.exe  (pasta portavel)
REM  Pre-requisito: Python 3.11+ instalado (ver BUILD.md).
REM ============================================================
setlocal

echo === Pregoes - build do executavel ===

REM Detecta o Python. Prefere o launcher "py" (instalado por padrao com o
REM Python e NAO depende do PATH de "python"); cai para "python" se preciso.
set "PYEXE="
py -3 --version 1>nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE ( python --version 1>nul 2>nul && set "PYEXE=python" )
if not defined PYEXE (
    echo.
    echo [ERRO] Python 3.11+ nao encontrado nesta maquina.
    echo.
    echo  1. Baixe em: https://www.python.org/downloads/windows/
    echo  2. No instalador, NA PRIMEIRA TELA, marque "Add python.exe to PATH".
    echo  3. Conclua a instalacao, abra um NOVO Prompt de Comando e rode de novo.
    echo.
    pause & exit /b 1
)
echo Python encontrado:
%PYEXE% --version

REM 1. Ambiente isolado de build
if not exist .build_env (
    %PYEXE% -m venv .build_env || (echo [ERRO] criacao do venv falhou. & pause & exit /b 1)
)
call .build_env\Scripts\activate.bat

REM 2. Dependencias de producao + PyInstaller
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || (echo [ERRO] pip install. & pause & exit /b 1)
python -m pip install pyinstaller

REM 3. Chromium do Playwright (sera empacotado no .exe pelo pregoes.spec).
REM    SEM ele a busca de precos do .exe nasce morta e o operador leigo nao
REM    tem como consertar — por isso o build ABORTA se o download falhar
REM    (auditoria 2026-06).
echo Baixando o Chromium do Playwright (uma vez)...
python -m playwright install chromium || (
    echo.
    echo [ERRO] Download do Chromium falhou — sem ele a busca de precos
    echo        nao funciona no .exe. Verifique a internet/proxy e rode
    echo        este build de novo. NADA foi gerado.
    pause & exit /b 1
)

REM 3b. Garante o .streamlit\config.toml. A pasta comeca com ponto e o Finder
REM      do Mac a ESCONDE, entao costuma NAO ir junto ao copiar o pen drive e o
REM      PyInstaller aborta por nao achar o arquivo (campo 2026-06). Recria aqui
REM      se faltar — mesmo conteudo do repo (tema + loopback).
REM      (Sem if(...) aninhado: cmd quebra o bloco com o ')' interno. Usamos
REM       'goto' para pular quando o arquivo ja existe.)
if exist ".streamlit\config.toml" goto :config_ok
if not exist ".streamlit" mkdir ".streamlit"
(
    echo [theme]
    echo base = "light"
    echo primaryColor = "#1E40AF"
    echo backgroundColor = "#F8FAFC"
    echo secondaryBackgroundColor = "#FFFFFF"
    echo textColor = "#0F172A"
    echo font = "sans serif"
    echo [server]
    echo address = "127.0.0.1"
    echo headless = true
    echo runOnSave = true
    echo fileWatcherType = "auto"
    echo [browser]
    echo gatherUsageStats = false
    echo [client]
    echo showErrorDetails = "none"
    echo toolbarMode = "minimal"
) > ".streamlit\config.toml"
echo [build] .streamlit\config.toml criado automaticamente.
:config_ok

REM 4. Build
pyinstaller pregoes.spec --noconfirm --clean || (echo [ERRO] PyInstaller falhou. & pause & exit /b 1)

echo.
echo === PRONTO ===
echo Aplicativo: dist\Pregoes\Pregoes.exe
echo Teste com dois cliques nele. Os dados ficam em dist\Pregoes\data\
pause
endlocal
