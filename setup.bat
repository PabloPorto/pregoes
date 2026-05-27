@echo off
REM ============================================================
REM Instalacao inicial do sistema "Pregoes"
REM Roda apenas uma vez na maquina nova.
REM ============================================================
title Pregoes - Instalacao
chcp 65001 > nul
echo.
echo  ================================================
echo   Pregoes - Instalacao do sistema
echo  ================================================
echo.

REM Verifica Python
python --version > nul 2>&1
if errorlevel 1 (
    echo  [ERRO] Python nao encontrado.
    echo.
    echo  Instale o Python 3.11 ou 3.12 antes de continuar:
    echo    https://www.python.org/downloads/
    echo.
    echo  Importante: ao instalar, marque a opcao
    echo    "Add python.exe to PATH"
    echo.
    pause
    exit /b 1
)

echo  Instalando dependencias (pode demorar 5-10 minutos).
echo  Inclui PyTorch CPU para extracao de PDFs complexos.
echo.
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 (
    echo.
    echo  [ERRO] Falha ao instalar dependencias.
    pause
    exit /b 1
)

echo.
echo  Instalando navegador interno para busca de precos...
echo  (Baixa o Chromium uma unica vez, pode demorar 1-2 minutos)
echo.
python -m playwright install chromium
if errorlevel 1 (
    echo.
    echo  [ERRO] Falha ao instalar o navegador (Chromium).
    echo  A busca automatica de precos nao vai funcionar ate corrigir.
    pause
    exit /b 1
)

echo.
echo  ================================================
echo   Instalacao concluida com sucesso!
echo  ================================================
echo.
echo  Para usar o sistema, clique duas vezes em:
echo    iniciar.bat
echo.
pause
