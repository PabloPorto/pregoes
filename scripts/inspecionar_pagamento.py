"""
Inspetor da CONDIÇÃO DE PAGAMENTO (Almeida + Dantas).

PROBLEMA: o preço dos produtos nos dois sites VARIA conforme a "Forma de
Pagamento" + "Prazo de Pagamento" selecionados no modal "Selecione suas
Preferências" (canto superior esquerdo da home). O scraper hoje NÃO seleciona
nada — pega o preço da condição default da sessão. Para precificar pregão certo,
o operador precisa fixar a condição que ele realmente usa.

Este script descobre, sem adivinhação, o que o app precisa para resolver isso:
- Quais são as opções de "Forma de Pagamento" (texto exato p/ o operador escolher)
- Quais são as opções de "Prazo de Pagamento" (idem; podem depender da Forma)
- A estrutura dos <select> e do botão "Confirmar" (p/ o scraper selecionar)
- Como a escolha PERSISTE: cookies antes/depois de Confirmar
- PROVA de que funciona: o preço de um produto ANTES vs DEPOIS de trocar a condição

Reusa o login do próprio scraper (data/credenciais.json). NÃO grava nada no banco
nem altera credenciais. Saída: relatório no stdout + JSON e screenshots em
/tmp/pagamento_inspect/.

Uso:
    .venv/bin/python scripts/inspecionar_pagamento.py
    .venv/bin/python scripts/inspecionar_pagamento.py --headed   # ver o browser
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pregoes import credenciais
from pregoes.scraper import AlmeidaDistribuidor, DantasDistribuidora

OUT = Path("/tmp/pagamento_inspect")

# JS que varre a página e devolve TODOS os <select> com seus rótulos e opções —
# é assim que descobrimos os dropdowns de Forma/Prazo sem saber o id de antemão.
_JS_DUMP_SELECTS = r"""
() => {
  const out = [];
  document.querySelectorAll('select').forEach((sel, i) => {
    // tenta achar um rótulo: <label for>, texto anterior, aria-label, name
    let rotulo = '';
    if (sel.id) {
      const lab = document.querySelector(`label[for="${sel.id}"]`);
      if (lab) rotulo = lab.innerText.trim();
    }
    if (!rotulo) rotulo = sel.getAttribute('aria-label') || '';
    if (!rotulo) {
      // texto do nó anterior (ex.: "Forma de Pagamento*:")
      let p = sel.previousElementSibling;
      while (p && !p.innerText) p = p.previousElementSibling;
      if (p) rotulo = (p.innerText || '').trim().slice(0, 60);
    }
    out.push({
      indice: i,
      id: sel.id || null,
      name: sel.getAttribute('name') || null,
      classe: sel.className || null,
      rotulo,
      selecionado: sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text.trim() : null,
      opcoes: Array.from(sel.options).map(o => ({ value: o.value, texto: o.text.trim() })),
    });
  });
  return out;
}
"""

# Procura, na home, um preço de produto qualquer para comparar antes/depois.
# Reusa o seletor dos cards (mesmo do scraper: data-preco no .product-card__info).
_JS_PRIMEIRO_PRECO = r"""
() => {
  const el = document.querySelector('.product-card__info[data-codigo-produto]');
  if (!el) return null;
  return { codigo: el.getAttribute('data-codigo-produto'),
           nome: el.getAttribute('data-nome'),
           preco: el.getAttribute('data-preco') };
}
"""


def _cookies_resumo(ctx):
    """Só nome→valor (curto) p/ comparar antes/depois sem poluir o relatório."""
    return {c["name"]: (c["value"][:24] + "…" if len(c["value"]) > 24 else c["value"])
            for c in ctx.cookies()}


def _inspecionar(cls, nome: str, base_url: str, headed: bool) -> dict:
    cred = credenciais.carregar(nome)
    if not cred:
        return {"fornecedor": nome, "erro": "sem credencial em data/credenciais.json"}

    rel: dict = {"fornecedor": nome}
    OUT.mkdir(parents=True, exist_ok=True)
    forn = cls(cred["email"], cred["senha"], headless=not headed)
    try:
        forn.__enter__()
    except Exception as exc:
        return {"fornecedor": nome, "erro": f"login falhou: {type(exc).__name__}: {exc}"}

    try:
        page = forn._page
        page.goto(base_url, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(2_500)
        page.screenshot(path=str(OUT / f"{nome}_home.png"))

        rel["cookies_antes"] = _cookies_resumo(forn._context)
        rel["selects"] = page.evaluate(_JS_DUMP_SELECTS)
        rel["preco_antes"] = page.evaluate(_JS_PRIMEIRO_PRECO)

        # Tenta abrir o modal "Selecione suas Preferências", se houver um gatilho.
        for txt in ["Preferências", "Forma de Pagamento", "BANCO DO BRASIL"]:
            try:
                alvo = page.get_by_text(txt, exact=False).first
                if alvo.count() and alvo.is_visible():
                    alvo.click(timeout=2_000)
                    page.wait_for_timeout(1_200)
                    rel["modal_aberto_por"] = txt
                    rel["selects_no_modal"] = page.evaluate(_JS_DUMP_SELECTS)
                    page.screenshot(path=str(OUT / f"{nome}_modal.png"))
                    break
            except Exception:
                continue

        # PROVA: troca a 2ª opção da 1ª Forma de Pagamento (se houver) e confirma,
        # depois relê o preço — para mostrar que a condição muda o preço.
        try:
            selects = page.locator("select")
            if selects.count() >= 1 and len(rel.get("selects", [])) >= 1:
                opcoes = rel["selects"][0]["opcoes"]
                if len(opcoes) >= 2:
                    selects.first.select_option(value=opcoes[1]["value"])
                    page.wait_for_timeout(800)
                    botao = page.get_by_role("button", name="Confirmar")
                    if not botao.count():
                        botao = page.get_by_text("Confirmar", exact=False)
                    if botao.count():
                        botao.first.click(timeout=3_000)
                        page.wait_for_timeout(2_500)
                    rel["trocou_para"] = opcoes[1]["texto"]
                    rel["cookies_depois"] = _cookies_resumo(forn._context)
                    page.goto(base_url, wait_until="networkidle", timeout=30_000)
                    page.wait_for_timeout(2_000)
                    rel["preco_depois"] = page.evaluate(_JS_PRIMEIRO_PRECO)
        except Exception as exc:
            rel["prova_troca_erro"] = f"{type(exc).__name__}: {exc}"

    finally:
        try:
            forn.__exit__(None, None, None)
        except Exception:
            pass
    return rel


def main() -> None:
    headed = "--headed" in sys.argv
    alvos = [
        (AlmeidaDistribuidor, "almeida", "https://www.almeidadistribuidor.com.br"),
        (DantasDistribuidora, "dantas", "https://www.dantasdistribuidora.com.br"),
    ]
    relatorio = [_inspecionar(cls, nome, url, headed) for cls, nome, url in alvos]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "relatorio.json").write_text(
        json.dumps(relatorio, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 70)
    print("RELATÓRIO — CONDIÇÃO DE PAGAMENTO")
    print("=" * 70)
    for r in relatorio:
        print(f"\n### {r['fornecedor'].upper()}")
        if r.get("erro"):
            print("  ERRO:", r["erro"]); continue
        for s in (r.get("selects_no_modal") or r.get("selects") or []):
            print(f"  <select> rótulo={s['rotulo']!r} name={s['name']} "
                  f"(selecionado: {s['selecionado']!r})")
            for o in s["opcoes"][:15]:
                print(f"       • {o['texto']!r}  (value={o['value']!r})")
        pa, pd = r.get("preco_antes"), r.get("preco_depois")
        if pa:
            print(f"  preço ANTES : {pa.get('nome','?')[:40]} = R$ {pa.get('preco')}")
        if r.get("trocou_para"):
            print(f"  trocou para : {r['trocou_para']!r}")
        if pd:
            print(f"  preço DEPOIS: {pd.get('nome','?')[:40]} = R$ {pd.get('preco')}")
        ca, cd = r.get("cookies_antes", {}), r.get("cookies_depois", {})
        mudou = [k for k in set(ca) | set(cd) if ca.get(k) != cd.get(k)]
        if mudou:
            print(f"  cookies que MUDARAM ao confirmar: {mudou}")
    print(f"\nJSON completo + screenshots em: {OUT}")
    print("Envie o relatorio.json (e os .png) para finalizar a implementação.")


if __name__ == "__main__":
    main()
