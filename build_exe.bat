@echo off
REM ============================================================
REM  Build do executavel Pregoes (RODAR NO WINDOWS).
REM  Gera:  dist\Pregoes\Pregoes.exe  (pasta portavel)
REM  Pre-requisito: Python 3.11+ instalado e no PATH.
REM ============================================================
setlocal

echo === Pregoes - build do executavel ===
python --version 1>nul 2>nul || (echo [ERRO] Python nao encontrado no PATH. & pause & exit /b 1)

REM 1. Ambiente isolado de build
if not exist .build_env (
    python -m venv .build_env || (echo [ERRO] venv falhou. & pause & exit /b 1)
)
call .build_env\Scripts\activate.bat

REM 2. Dependencias de producao + PyInstaller
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || (echo [ERRO] pip install. & pause & exit /b 1)
python -m pip install pyinstaller

REM 3. Chromium do Playwright (sera empacotado no .exe pelo pregoes.spec)
echo Baixando o Chromium do Playwright (uma vez)...
python -m playwright install chromium

REM 4. Build
pyinstaller pregoes.spec --noconfirm --clean || (echo [ERRO] PyInstaller falhou. & pause & exit /b 1)

echo.
echo === PRONTO ===
echo Aplicativo: dist\Pregoes\Pregoes.exe
echo Teste com dois cliques nele. Os dados ficam em dist\Pregoes\data\
pause
endlocal
