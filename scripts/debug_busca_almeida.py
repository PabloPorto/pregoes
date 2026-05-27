"""
Debug do scraper Almeida: por que retorna 0 resultados quando o site
manual mostra 9588 produtos para a mesma query.

Faz login real, busca uma descrição-exemplo do edital, e dumpa:
- HTML dos primeiros 5 cards
- Atributos data-* disponíveis em cada card
- Resultado do _parse_card_attributes
- Filtro de relevância: quais sobreviveriam

Uso:
    .venv/bin/python scripts/debug_busca_almeida.py
    .venv/bin/python scripts/debug_busca_almeida.py "QUERY CUSTOM"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pregoes import credenciais
from pregoes.normalizador import keywords, normalizar_descricao, relevancia
from pregoes.scraper import _parse_card_attributes, _RELEVANCIA_MINIMA


DESCRICAO_DEFAULT = 'ADAPTADOR PVC SOLD. CURTO DE 50 X 1.1/2"'
OUT_DIR = Path("/tmp/dantas_inspect")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main(descricao: str) -> int:
    cred = credenciais.carregar("almeida")
    if cred is None:
        print("Credencial Almeida nao configurada. Abortando.")
        return 1
    print(f"Credencial: {cred['email']}", flush=True)
    print(f"Descricao original: {descricao!r}", flush=True)

    norm = normalizar_descricao(descricao)
    kws = keywords(descricao)
    print(f"Normalizada: {norm!r}", flush=True)
    print(f"Keywords: {kws}", flush=True)
    print(f"Threshold de relevancia: {_RELEVANCIA_MINIMA}", flush=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        print("\n[1] Login...", flush=True)
        page.goto("https://www.almeidadistribuidor.com.br/entrar",
                  wait_until="networkidle", timeout=30_000)
        page.locator("input[name='email']").first.fill(cred["email"])
        senha_input = page.locator("input[name='senha']").first
        senha_input.fill(cred["senha"])
        senha_input.press("Enter")
        page.wait_for_timeout(4_000)

        cookies = {c["name"] for c in context.cookies()}
        print(f"  Cookies pos-login: {sorted(cookies)}", flush=True)
        ok_login = "PHPSESSID" in cookies and "token" in cookies
        print(f"  Logado: {ok_login}", flush=True)
        page.screenshot(path=str(OUT_DIR / "alm_01_pos_login.png"))

        from urllib.parse import quote_plus
        url = f"https://www.almeidadistribuidor.com.br/busca?s={quote_plus(norm)}"
        print(f"\n[2] URL de busca: {url}", flush=True)
        page.goto(url, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(2_000)
        page.screenshot(path=str(OUT_DIR / "alm_02_busca.png"))
        print(f"  Title: {page.title()!r}", flush=True)
        print(f"  URL final: {page.url}", flush=True)

        contagem = page.evaluate("""
            () => ({
                'product-card__info[data-codigo-produto]': document.querySelectorAll('.product-card__info[data-codigo-produto]').length,
                'product-card__info': document.querySelectorAll('.product-card__info').length,
                'product-card': document.querySelectorAll('.product-card').length,
                'data-codigo-produto': document.querySelectorAll('[data-codigo-produto]').length,
                'data-preco': document.querySelectorAll('[data-preco]').length,
                'data-id-produto': document.querySelectorAll('[data-id-produto]').length,
                'class-product': document.querySelectorAll('[class*="product"]').length,
                'class-produto': document.querySelectorAll('[class*="produto"]').length,
            })
        """)
        print(f"\n[3] Contagem de seletores:", flush=True)
        for sel, n in contagem.items():
            print(f"    {sel}: {n}", flush=True)

        amostra = page.evaluate("""
            () => {
                const candidatos = document.querySelectorAll(
                    '.product-card, .product-card__info, [class*="product-card"]'
                );
                const vistos = new Set();
                const out = [];
                for (const el of candidatos) {
                    if (vistos.has(el) || out.length >= 5) continue;
                    vistos.add(el);
                    const datas = {};
                    for (const a of el.attributes) {
                        if (a.name.startsWith('data-')) datas[a.name] = a.value;
                    }
                    out.push({
                        tag: el.tagName.toLowerCase(),
                        classes: el.className,
                        data_attrs: datas,
                        text: (el.textContent || '').replace(/\\s+/g,' ').trim().slice(0, 200),
                        html: el.outerHTML.slice(0, 1000),
                    });
                }
                return out;
            }
        """)

        print(f"\n[4] Amostra de {len(amostra)} cards:", flush=True)
        for i, c in enumerate(amostra):
            print(f"\n  --- Card #{i+1} ---", flush=True)
            print(f"  tag: {c['tag']}", flush=True)
            print(f"  classes: {c['classes'][:100]}", flush=True)
            print(f"  data-*: {c['data_attrs']}", flush=True)
            print(f"  texto: {c['text']}", flush=True)
            print(f"  html (trunc):\n{c['html']}", flush=True)

        print(f"\n[5] Aplicando _parse_card_attributes:", flush=True)
        for i, c in enumerate(amostra):
            r = _parse_card_attributes(c["data_attrs"], confianca="exato")
            if r is None:
                d = c["data_attrs"]
                motivos = []
                if not (d.get("data-codigo-produto") or "").strip():
                    motivos.append("sem data-codigo-produto")
                if not (d.get("data-nome") or "").strip():
                    motivos.append("sem data-nome")
                preco_str = (d.get("data-preco") or "").strip()
                if not preco_str:
                    motivos.append("sem data-preco")
                else:
                    try:
                        if float(preco_str.replace(",", ".")) <= 0:
                            motivos.append(f"preco<=0 ({preco_str!r})")
                    except ValueError:
                        motivos.append(f"preco invalido ({preco_str!r})")
                print(f"  Card #{i+1}: REJEITADO - {', '.join(motivos)}", flush=True)
            else:
                rel = relevancia(descricao, r.descricao_encontrada)
                passa = rel >= _RELEVANCIA_MINIMA
                print(f"  Card #{i+1}: {r.marca} | R${r.preco:.2f} | "
                      f"cod {r.codigo_fornecedor} | {r.descricao_encontrada[:50]}",
                      flush=True)
                print(f"           Relev vs original: {rel:.2f} "
                      f"({'PASSA' if passa else 'CORTA'} >= {_RELEVANCIA_MINIMA})",
                      flush=True)

        html_path = OUT_DIR / "alm_busca_completa.html"
        html_path.write_text(page.content(), encoding="utf-8")
        print(f"\n[6] HTML completo: {html_path}", flush=True)

        browser.close()
    return 0


if __name__ == "__main__":
    desc = sys.argv[1] if len(sys.argv) > 1 else DESCRICAO_DEFAULT
    sys.exit(main(desc))
