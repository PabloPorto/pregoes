"""Captura fixtures de HTML REAL dos sites para os testes de regressão do parse.

Os testes em tests/test_fixtures_html.py congelam o CONTRATO atual dos sites
(atributos data-* dos cards + <a> do produto). Quando um site mudar de layout e
o parse quebrar em produção, rode este script para recapturar e ajuste o parser
até os testes passarem de novo — sem rede no pytest.

Uso (precisa de credenciais válidas; faz 1 login por fornecedor):
    python scripts/capturar_fixtures_html.py

Segurança: o fragmento é ANONIMIZADO (nome/e-mail/documentos do cliente nunca
vão para o repositório) e o script ABORTA se sobrar indício de dado pessoal.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pregoes import credenciais, scraper  # noqa: E402

DESTINO = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# Termos com resultado garantido por fornecedor + um termo sem resultado.
BUSCAS = {"almeida": "tubo pvc esgoto", "dantas": "tubo"}
TERMO_VAZIO = "zzzxxxyyy123"


def _anonimizar(html: str) -> str:
    cred_all = " ".join(
        str(v) for f in ("almeida", "dantas")
        for v in (credenciais.carregar(f) or {}).values())
    # nome de usuário do e-mail e domínio (ex.: anselmo / centraldaconstrucao)
    tokens = set(re.findall(r"[a-z0-9._-]{4,}", cred_all.lower()))
    for t in sorted(tokens, key=len, reverse=True):
        html = re.sub(re.escape(t), "anonimo", html, flags=re.IGNORECASE)
    html = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "anonimo@example.com", html)
    html = re.sub(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", "00.000.000/0000-00", html)  # CNPJ
    html = re.sub(r'(name="[^"]*token[^"]*"\s+value=")[^"]*(")',
                  r"\1anonimo\2", html, flags=re.IGNORECASE)
    return html


def _fragmento_cards(html: str, n_cards: int = 5) -> str:
    """Fatia com N cards + os <a> de produto que os seguem (layout logado)."""
    posicoes = [m.start() for m in scraper._RE_CARD_TAG.finditer(html)]
    if len(posicoes) < n_cards + 1:
        raise SystemExit(f"esperava >= {n_cards + 1} cards, achei {len(posicoes)}")
    return html[max(0, posicoes[0] - 200):posicoes[n_cards]]


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    for forn_nome, termo in BUSCAS.items():
        forn = scraper.obter_sessao_viva(forn_nome)
        base = scraper.config.FORNECEDORES[forn_nome]
        html = scraper._http_get(
            f"{base}/busca?s={termo.replace(' ', '+')}", forn._http_cookie)
        frag = _anonimizar(_fragmento_cards(html))
        for indicio in ("@", "cnpj"):
            resto = frag.lower().count(indicio) - frag.lower().count("example.com")
            if indicio == "@" and resto > 0:
                raise SystemExit(f"ABORTADO: sobrou '@' não anonimizado em {forn_nome}")
        (DESTINO / f"{forn_nome}_busca_logada.html").write_text(frag, encoding="utf-8")
        vazio = scraper._http_get(
            f"{base}/busca?s={TERMO_VAZIO}", forn._http_cookie)
        meio = len(vazio) // 2
        (DESTINO / f"{forn_nome}_busca_vazia.html").write_text(
            _anonimizar(vazio[max(0, meio - 15000):meio + 15000]), encoding="utf-8")
        print(f"{forn_nome}: fixtures gravadas "
              f"({len(frag)//1024}KB cards + vazia)")
    scraper.fechar_sessoes_vivas()


if __name__ == "__main__":
    main()
