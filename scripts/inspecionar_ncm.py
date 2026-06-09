"""Inspeção ao vivo: onde está o NCM nos sites Almeida/Dantas?
Descobre se o NCM está no card de busca ou só na página de detalhe, e o
padrão do link do produto — para decidir como capturar.

Uso: python -m scripts.inspecionar_ncm
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pregoes import credenciais
from pregoes.scraper import AlmeidaDistribuidor, DantasDistribuidora

ALVOS = [("almeida", AlmeidaDistribuidor, "https://www.almeidadistribuidor.com.br",
          "tubo pvc soldavel 75"),
         ("dantas", DantasDistribuidora, "https://www.dantasdistribuidora.com.br",
          "cabo flexivel 2,5mm")]


def inspecionar(nome, cls, base, query):
    print(f"\n{'='*60}\n### {nome.upper()} — busca '{query}'")
    cred = credenciais.carregar(nome)
    if not cred:
        print("  sem credencial"); return
    try:
        with cls(cred["email"], cred["senha"]) as forn:
            page = forn._page
            from urllib.parse import quote_plus
            page.goto(f"{base}/busca?s={quote_plus(query)}",
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1500)
            # 1. NCM aparece em algum lugar da página de busca?
            tem_ncm_busca = page.evaluate(
                "() => document.body.innerText.toUpperCase().includes('NCM')")
            print(f"  página de BUSCA tem texto 'NCM'? {tem_ncm_busca}")
            # 2. HTML do 1º card (procurar ncm em atributo/elemento)
            card = page.evaluate("""() => {
                const c = document.querySelector('.product-card__info[data-codigo-produto]');
                if (!c) return null;
                const card = c.closest('.product-card') || c.parentElement;
                return {
                    info_html: c.outerHTML.slice(0, 600),
                    tem_data_ncm: c.hasAttribute('data-ncm'),
                    link: (card && card.querySelector('a')) ? card.querySelector('a').href : null,
                    codigo: c.getAttribute('data-codigo-produto'),
                };
            }""")
            if not card:
                print("  NENHUM card encontrado"); return
            print(f"  card tem data-ncm? {card['tem_data_ncm']}")
            print(f"  código do card: {card['codigo']}")
            print(f"  link do produto: {card['link']}")
            print(f"  info_html (trecho): {card['info_html'][:300]}")
            # 3. Visitar a página de DETALHE e achar o NCM
            if card["link"]:
                page.goto(card["link"], wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(1500)
                txt = page.evaluate("() => document.body.innerText")
                m = re.search(r'NCM[:\s]*([\d.]{8,12})', txt, re.IGNORECASE)
                print(f"  >>> NCM na página de DETALHE: {m.group(0) if m else 'NÃO ACHOU'}")
    except Exception as exc:
        print(f"  FALHOU: {type(exc).__name__}: {exc}")


for nome, cls, base, query in ALVOS:
    inspecionar(nome, cls, base, query)
