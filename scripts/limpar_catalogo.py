"""Limpa SÓ o catálogo de produtos (Camada 0) — preservando TODO o resto.

Uso: trocar um catálogo com códigos errados pelo validado (catalogo_limpo.xlsx),
SEM perder o trabalho acumulado do operador. O que é preservado, explicitamente:

- mapeamentos_aprendidos  → as escolhas que o operador já fez (a inteligência
                            central do sistema — NUNCA pode ser perdida)
- editais / edital_itens  → histórico de pregões processados
- exports_log             → histórico de planilhas geradas
- caches (busca/judge)    → aceleração
- credenciais.json        → nem é tocado (arquivo separado)

O que é APAGADO (só o catálogo): produtos, precos_historico e
produtos_canonico (nomes padronizados — derivados do catálogo).

Segurança: faz um backup do banco ANTES (data/backups/, com verificação de
integridade — db.backup recusa banco corrompido) e roda tudo numa transação.

Uso:
    python scripts/limpar_catalogo.py                     # banco da instalação
    python scripts/limpar_catalogo.py --sim               # sem pergunta
    python scripts/limpar_catalogo.py C:\\backup\\pregoes.db --sim
        (limpa um banco COPIADO — p/ máquina do cliente sem Python: traga o
         pregoes.db no pen drive, limpe aqui, devolva)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pregoes import config, db


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--sim"]
    caminho = Path(args[0]) if args else config.DB_PATH
    if not caminho.exists():
        print(f"Banco não encontrado em {caminho} — nada a limpar.")
        return 0
    # Redireciona o módulo inteiro para o banco alvo (backup incluso).
    config.DB_PATH = caminho

    with db.conectar(config.DB_PATH) as c:
        n_prod = c.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
        n_hist = c.execute("SELECT COUNT(*) FROM precos_historico").fetchone()[0]
        n_apr = c.execute("SELECT COUNT(*) FROM mapeamentos_aprendidos").fetchone()[0]
        n_ed = c.execute("SELECT COUNT(*) FROM editais").fetchone()[0]

    print(f"Catálogo atual: {n_prod} produtos · {n_hist} preços históricos")
    print(f"Será PRESERVADO: {n_apr} aprendizados do operador · {n_ed} pregões no histórico")
    if "--sim" not in sys.argv:
        resp = input("Apagar o catálogo (produtos + preços históricos)? [s/N] ")
        if resp.strip().lower() not in ("s", "sim"):
            print("Cancelado — nada foi alterado.")
            return 1

    destino = db.backup("pre-limpeza-catalogo")
    print(f"Backup criado: {destino}")

    with db.get_conn(config.DB_PATH) as conn:
        # Ordem respeita as FKs: primeiro quem REFERENCIA produtos.
        conn.execute("DELETE FROM precos_historico")
        conn.execute("DELETE FROM produtos_canonico")
        # Itens de editais antigos apontavam para o catálogo velho — solta o
        # vínculo (o item continua no histórico, só sem produto associado).
        conn.execute("UPDATE edital_itens SET produto_id = NULL "
                     "WHERE produto_id IS NOT NULL")
        conn.execute("DELETE FROM produtos")

    with db.conectar(config.DB_PATH) as c:
        n_prod2 = c.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
        n_apr2 = c.execute("SELECT COUNT(*) FROM mapeamentos_aprendidos").fetchone()[0]
        n_ed2 = c.execute("SELECT COUNT(*) FROM editais").fetchone()[0]
    print(f"Pronto: produtos={n_prod2} · aprendizados preservados={n_apr2} · "
          f"pregões preservados={n_ed2}")
    print("Agora importe o catálogo validado em Configurações → Catálogo "
          "histórico (arquivo catalogo_limpo.xlsx).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
