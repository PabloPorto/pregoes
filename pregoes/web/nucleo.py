"""Peças comuns da interface web: modelos (Jinja), respostas e o contexto que
toda tela recebe.

A interface web (2026-11) substitui o Streamlit reutilizando o pacote
`pregoes` inteiro e os MESMOS dados em `data/` — nenhuma regra de negócio mora
aqui. Feita com o que já vinha instalado (Starlette, Jinja2, uvicorn são
dependências do próprio Streamlit): nenhuma biblioteca nova, nenhum passo de
build, um único processo Python.
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from markupsafe import Markup
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.templating import Jinja2Templates

from pregoes import config

PASTA = Path(__file__).parent
# Prefixo de URL quando o app é servido num subcaminho (ex.: "/novo" atrás do
# mesmo túnel da interface antiga). Vazio = na raiz.
RAIZ = os.environ.get("PREGOES_WEB_RAIZ", "").rstrip("/")

modelos = Jinja2Templates(directory=str(PASTA / "templates"))


def _brl(valor: Any) -> str:
    """1234.5 -> 'R$ 1.234,50' (vazio para None)."""
    if valor is None or valor == "":
        return ""
    try:
        s = f"{float(valor):,.2f}"
    except (TypeError, ValueError):
        return str(valor)
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _num(valor: Any, casas: int = 0) -> str:
    """Número no padrão brasileiro; inteiros sem casas decimais."""
    if valor is None or valor == "":
        return ""
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if casas == 0 and f != int(f):
        casas = 2
    s = f"{f:,.{casas}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _quando(valor: Any) -> str:
    """Data/hora no fuso do operador. Aceita datetime, epoch ou o texto UTC do
    SQLite ('2026-11-13 20:41:05')."""
    if not valor:
        return ""
    if isinstance(valor, (int, float)):
        dt = datetime.fromtimestamp(valor, tz=timezone.utc)
    elif isinstance(valor, datetime):
        dt = valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    else:
        try:
            dt = datetime.fromisoformat(str(valor).replace("Z", "")).replace(tzinfo=timezone.utc)
        except ValueError:
            return str(valor)
    return dt.astimezone(config.FUSO_HORARIO).strftime("%d/%m/%Y %H:%M")


def _sem_entidades(valor: Any) -> Any:
    """Os sites dos fornecedores mandam nomes com entidades HTML cruas
    ('TUBO 3&#8221;'); sem isto a tela mostrava o código em vez da aspa. Só
    texto comum — HTML já marcado como seguro passa intacto, e o autoescape
    do Jinja roda DEPOIS, então nada vira marcação."""
    if isinstance(valor, str) and not isinstance(valor, Markup) and "&" in valor:
        return html.unescape(valor)
    return valor


modelos.env.finalize = _sem_entidades
modelos.env.filters.update(brl=_brl, num=_num, quando=_quando)


def operador_de(request: Request) -> Optional[str]:
    """Quem está usando: o e-mail que o Cloudflare Access autenticou (no
    servidor). Local, sem o Access na frente, não há identificação."""
    email = request.headers.get("cf-access-authenticated-user-email", "").strip()
    return email or None


def pagina(request: Request, modelo: str, secao: str = "", status: int = 200, **contexto):
    """Renderiza uma tela completa."""
    base = {
        "raiz": RAIZ, "versao": config.VERSAO_APP, "secao": secao,
        "operador": operador_de(request), "servidor": config.MODO_SERVIDOR,
    }
    base.update(contexto)
    return modelos.TemplateResponse(request, modelo, base, status_code=status)


def pedaco(request: Request, modelo: str, **contexto) -> str:
    """Renderiza um pedaço de tela e devolve o HTML (para trocar na página)."""
    base = {"raiz": RAIZ, "versao": config.VERSAO_APP, "operador": operador_de(request),
            "servidor": config.MODO_SERVIDOR, "request": request}
    base.update(contexto)
    return modelos.get_template(modelo).render(base)


def ok(**campos) -> JSONResponse:
    """Resposta padrão das ações (ver `aplicar` em static/app.js)."""
    return JSONResponse({"ok": True, **campos})


def falha(mensagem: str, status: int = 400) -> JSONResponse:
    """Erro em português para o operador — nunca stacktrace."""
    return JSONResponse({"ok": False, "erro": mensagem}, status_code=status)


async def corpo_json(request: Request) -> dict:
    try:
        dados = await request.json()
    except Exception:
        return {}
    return dados if isinstance(dados, dict) else {}
