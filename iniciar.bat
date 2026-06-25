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

REM Sobe pelo run_app.py (o MESMO entrypoint do .exe). Ele prende a UI ao
REM loopback (127.0.0.1 -> nunca exposta na rede Wi-Fi local), abre o navegador
REM sozinho, E aplica os ajustes do index.html: pagina pt-BR (navegador nao
REM traduz/quebra) + neutraliza o popup "Clear cache" no Ctrl+C. Rodar o
REM streamlit direto pulava esses ajustes.
python run_app.py

echo.
echo Sistema encerrado.
pause
