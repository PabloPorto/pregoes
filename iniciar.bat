@echo off
REM ============================================================
REM Inicia o sistema "Pregoes"
REM O cliente clica neste arquivo e o navegador abre sozinho.
REM ============================================================
title Pregoes - Sistema rodando (NAO FECHE esta janela)
chcp 65001 > nul

echo.
echo  ================================================
echo   Pregoes - Sistema em execucao
echo  ================================================
echo.
echo  O sistema vai abrir no seu navegador em alguns segundos.
echo.
echo  IMPORTANTE: Mantenha esta janela aberta enquanto usa.
echo  Para fechar o sistema, basta fechar esta janela.
echo.

REM Roda o Streamlit. Ele abre o navegador automaticamente.
python -m streamlit run pregoes\ui\app.py --server.port=8501 --browser.gatherUsageStats=false --server.headless=false

echo.
echo Sistema encerrado.
pause
