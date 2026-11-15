"""Sobe a interface web do Pregões (a nova, que substitui o Streamlit).

    python run_web.py            → http://127.0.0.1:8502

Variáveis: PREGOES_WEB_PORTA (padrão 8502), PREGOES_WEB_RAIZ (subcaminho, ex.
"/painel"), PREGOES_SERVIDOR=1 (modo servidor), PREGOES_RAIZ (outra pasta de
dados — para ensaiar com uma cópia). Escuta só em 127.0.0.1: no servidor quem
publica é o túnel, com o login do Cloudflare Access na frente.
"""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("pregoes.web.app:app", host="127.0.0.1",
                port=int(os.environ.get("PREGOES_WEB_PORTA", "8502")),
                workers=1, log_level="warning")
