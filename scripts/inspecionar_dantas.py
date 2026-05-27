"""
Inspetor autônomo do site dantasdistribuidora.com.br.

Tenta descobrir, sem intervenção humana:
- URL e estrutura do formulário de login
- Cookies que indicam sessão estabelecida
- Estrutura HTML dos cards de produto na página de busca
- Atributos disponíveis para extrair preço, marca, código, descrição

Usa as credenciais salvas em data/credenciais.json (operador confirmou
que mesma conta serve para Almeida e Dantas).

Saída: screenshots em /tmp/dantas_inspect/ + relatório textual no stdout.
Não modifica credenciais.json nem grava nada no banco de dados.

Uso:
    .venv/bin/python scripts/inspecionar_dantas.py
    .venv/bin/python scripts/inspecionar_dantas.py --headless=false  # ver browser
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pregoes import credenciais


DANTAS_URL = "https://www.dantasdistribuidora.com.br"
OUT_DIR = Path("/tmp/dantas_inspect")
QUERY_TESTE = "PARAFUSO"


def _shot(page, nome: str) -> None:
    """Tira screenshot nomeado em OUT_DIR."""
    caminho = OUT_DIR / f"{nome}.png"
    page.screenshot(path=str(caminho))
    print(f"  📸 {caminho}", flush=True)


def _dump(page, nome: str, html: str) -> None:
    """Salva snippet HTML em arquivo."""
    caminho = OUT_DIR / f"{nome}.html"
    caminho.write_text(html, encoding="utf-8")
    print(f"  📄 {caminho} ({len(html)} bytes)", flush=True)


def main(headless: bool = True) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    cred = credenciais.carregar("almeida")
    if cred is None:
        print("❌ Credencial 'almeida' não encontrada — abortando.")
        return 1
    print(f"Credencial em uso: {cred['email']}", flush=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        # ── 1. Página inicial ─────────────────────────────────────────
        print("\n[1] Navegando para a home Dantas…", flush=True)
        page.goto(DANTAS_URL, wait_until="networkidle", timeout=30_000)
        _shot(page, "01_home")
        print(f"  URL final: {page.url}", flush=True)
        print(f"  Title: {page.title()}", flush=True)

        # Procura formulário de login na própria home (alguns sites têm na home)
        # ou link/botão para a página de login
        form_info = page.evaluate("""
            () => {
                // Procura inputs do tipo password (sinal de form de login)
                const pwd = document.querySelectorAll('input[type="password"]');
                const formHomeTemLogin = pwd.length > 0;

                // Procura links/botões que mencionem "entrar", "login", "minha conta"
                const links = Array.from(document.querySelectorAll('a, button'));
                const candidatosLogin = links
                    .filter(el => {
                        const t = (el.textContent || '').toLowerCase().trim();
                        return /entrar|login|acessar|minha\\s+conta|sign\\s*in/i.test(t);
                    })
                    .slice(0, 10)
                    .map(el => ({
                        tag: el.tagName.toLowerCase(),
                        text: (el.textContent || '').trim().slice(0, 60),
                        href: el.href || null,
                    }));

                return { formHomeTemLogin, totalInputsPassword: pwd.length, candidatosLogin };
            }
        """)
        print(f"  Form com password na home? {form_info['formHomeTemLogin']}", flush=True)
        print(f"  Links candidatos para login:", flush=True)
        for c in form_info["candidatosLogin"]:
            print(f"    {c['tag']} '{c['text']}' → {c['href']}", flush=True)

        # ── 2. Tentar navegar para a página de login ──────────────────
        login_url = None
        if form_info["formHomeTemLogin"]:
            print("[2] Login fica na própria home.", flush=True)
            login_url = page.url
        else:
            # Pega o primeiro candidato com href
            for c in form_info["candidatosLogin"]:
                if c.get("href") and "dantas" in c["href"]:
                    login_url = c["href"]
                    break

            if login_url is None:
                # Tenta URLs comuns
                for tentativa in ("/login", "/entrar", "/acessar", "/cliente/login"):
                    test_url = DANTAS_URL.rstrip("/") + tentativa
                    print(f"  Tentando {test_url}…", flush=True)
                    try:
                        resp = page.goto(test_url, wait_until="networkidle", timeout=20_000)
                        if resp and resp.ok:
                            tem_pwd = page.evaluate(
                                "() => document.querySelectorAll('input[type=\"password\"]').length > 0"
                            )
                            if tem_pwd:
                                login_url = test_url
                                print(f"  ✓ Encontrei form de login em {test_url}", flush=True)
                                break
                    except Exception as e:
                        print(f"  ✗ {test_url}: {e}", flush=True)

            if login_url is None:
                print("❌ Não encontrei página de login. Abortando.", flush=True)
                _shot(page, "02_nao_achou_login")
                browser.close()
                return 2

            print(f"[2] Navegando para login: {login_url}", flush=True)
            page.goto(login_url, wait_until="networkidle", timeout=30_000)
            _shot(page, "02_login_pagina")

        # ── 3. Inspecionar formulário de login ────────────────────────
        print("[3] Inspecionando inputs do formulário de login…", flush=True)
        form = page.evaluate("""
            () => {
                const inputs = Array.from(document.querySelectorAll('form input'));
                return inputs.slice(0, 20).map(i => ({
                    name: i.name,
                    id: i.id,
                    type: i.type,
                    placeholder: i.placeholder,
                    required: i.required,
                }));
            }
        """)
        for i in form:
            print(f"  input name='{i['name']}' id='{i['id']}' type='{i['type']}' "
                  f"placeholder='{i['placeholder']}'", flush=True)

        # ── 4. Tentar login ────────────────────────────────────────────
        print("[4] Preenchendo credenciais…", flush=True)
        # Heurísticas para localizar campos
        email_input = None
        senha_input = None
        for sel in ("input[name='email']", "input[type='email']",
                    "input[name='login']", "input[name='usuario']",
                    "input[name='user']"):
            loc = page.locator(sel)
            if loc.count() > 0:
                email_input = loc.first
                print(f"  ✓ Campo email via '{sel}'", flush=True)
                break
        for sel in ("input[name='senha']", "input[name='password']",
                    "input[type='password']"):
            loc = page.locator(sel)
            if loc.count() > 0:
                senha_input = loc.first
                print(f"  ✓ Campo senha via '{sel}'", flush=True)
                break

        if not email_input or not senha_input:
            print("❌ Não localizei email/senha. Abortando.", flush=True)
            browser.close()
            return 3

        email_input.fill(cred["email"])
        senha_input.fill(cred["senha"])
        _shot(page, "03_preenchido")

        # Submete (testa Enter primeiro, fallback no botão)
        cookies_antes = {c["name"] for c in context.cookies()}
        try:
            senha_input.press("Enter")
        except Exception:
            pass
        page.wait_for_timeout(4_000)

        cookies_depois = {c["name"]: c["value"][:8] + "..."
                          for c in context.cookies()}
        cookies_novos = set(cookies_depois) - cookies_antes
        _shot(page, "04_pos_login")

        print(f"[4] Cookies após login: {list(cookies_depois.keys())}", flush=True)
        print(f"    Novos pós-login: {cookies_novos}", flush=True)
        print(f"    URL após login: {page.url}", flush=True)

        # Heurística "loguei?": URL mudou ou apareceu cookie de sessão
        logou = (
            "login" not in page.url.lower() and
            "entrar" not in page.url.lower() and
            len(cookies_depois) > len(cookies_antes)
        )
        # Outra heurística: presença de elemento típico de área logada
        tem_logout = page.locator("text=/sair|logout|deslogar/i").count() > 0
        print(f"    Heurística 'parece logado': {logou or tem_logout}", flush=True)

        # ── 5. Tentar busca ────────────────────────────────────────────
        print(f"[5] Tentando busca por '{QUERY_TESTE}'…", flush=True)
        # Heurísticas de URL de busca
        for url_busca in (
            f"{DANTAS_URL}/busca?s={QUERY_TESTE}",
            f"{DANTAS_URL}/busca?q={QUERY_TESTE}",
            f"{DANTAS_URL}/buscar?q={QUERY_TESTE}",
            f"{DANTAS_URL}/search?q={QUERY_TESTE}",
            f"{DANTAS_URL}/produtos?busca={QUERY_TESTE}",
        ):
            try:
                page.goto(url_busca, wait_until="networkidle", timeout=20_000)
                page.wait_for_timeout(1_500)
                _shot(page, f"05_busca_{url_busca.split('?')[0].rsplit('/',1)[1]}")
                # Verifica se há resultados na página
                conta = page.evaluate("""
                    () => {
                        const candidatos = document.querySelectorAll(
                            '.product-card__info, .product-card, [class*="produto"], [class*="product"]'
                        );
                        return candidatos.length;
                    }
                """)
                print(f"  {url_busca} → {conta} elementos candidatos a card", flush=True)
                if conta > 0:
                    break
            except Exception as e:
                print(f"  ✗ {url_busca}: {e}", flush=True)

        # ── 6. Inspecionar estrutura dos cards ────────────────────────
        print("[6] Inspecionando estrutura dos cards de produto…", flush=True)
        cards_info = page.evaluate("""
            () => {
                // Estratégia ampla: pega tudo que parece um card de produto
                const seletores = [
                    '.product-card', '.product-card__info',
                    '[class*="produto"]', '[class*="product"]',
                    '[data-product]', '[data-produto]',
                    '.card-product', '.item-produto',
                ];
                const vistos = new Set();
                const out = [];
                for (const s of seletores) {
                    document.querySelectorAll(s).forEach((el, i) => {
                        if (vistos.has(el) || out.length >= 5) return;
                        vistos.add(el);
                        // Pega todos os atributos data-*
                        const datas = {};
                        for (const a of el.attributes) {
                            if (a.name.startsWith('data-')) datas[a.name] = a.value;
                        }
                        out.push({
                            seletor_match: s,
                            tag: el.tagName.toLowerCase(),
                            classes: el.className,
                            data_attrs: datas,
                            html_snippet: el.outerHTML.slice(0, 500),
                            text_snippet: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 200),
                        });
                    });
                }
                return out;
            }
        """)

        for i, c in enumerate(cards_info):
            print(f"\n  --- Card #{i+1} (via {c['seletor_match']}) ---", flush=True)
            print(f"  tag: {c['tag']}, classes: {c['classes'][:80]}", flush=True)
            print(f"  data-*: {c['data_attrs']}", flush=True)
            print(f"  texto: {c['text_snippet']}", flush=True)
            _dump(page, f"06_card_{i+1}", c["html_snippet"])

        # ── 7. Dump do HTML completo da página de busca ───────────────
        print("\n[7] Salvando HTML completo da página de busca…", flush=True)
        html_busca = page.content()
        _dump(page, "07_busca_completa", html_busca)

        browser.close()
        print("\n✓ Inspeção concluída. Arquivos em:", OUT_DIR, flush=True)
        return 0


if __name__ == "__main__":
    headless = "--headless=false" not in sys.argv
    sys.exit(main(headless=headless))
