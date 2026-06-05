"""
Saúde do catálogo — o instrumento da "alavanca 1" (cobertura de dado).

Mede o que liga a busca por código (Camada 0): quantos produtos têm código
Almeida/Dantas, quantos têm preço, e onde estão os buracos por categoria.
Rode depois de cada importação de planilha histórica do cliente para ver a
cobertura subir e saber EXATAMENTE o que ainda pedir.

Uso: python -m scripts.catalogo_saude
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pregoes import db


def _scalar(conn, sql):
    return conn.execute(sql).fetchone()[0]


def main():
    conn = db.conectar()
    total = _scalar(conn, "SELECT COUNT(*) FROM produtos")
    if not total:
        print("Catálogo vazio. Importe planilhas históricas primeiro "
              "(python -m pregoes.importador <arquivos.xlsx>).")
        return

    tem_alm = "codigo_almeida IS NOT NULL AND codigo_almeida!=''"
    tem_dan = "codigo_dantas IS NOT NULL AND codigo_dantas!=''"
    alm = _scalar(conn, f"SELECT COUNT(*) FROM produtos WHERE {tem_alm}")
    dan = _scalar(conn, f"SELECT COUNT(*) FROM produtos WHERE {tem_dan}")
    ambos = _scalar(conn, f"SELECT COUNT(*) FROM produtos WHERE ({tem_alm}) AND ({tem_dan})")
    nenhum = _scalar(conn, f"SELECT COUNT(*) FROM produtos WHERE NOT ({tem_alm}) AND NOT ({tem_dan})")
    com_preco = _scalar(conn, "SELECT COUNT(*) FROM produtos p WHERE EXISTS "
                              "(SELECT 1 FROM precos_historico h WHERE h.produto_id=p.id)")

    def pct(x):
        return f"{100*x//total:>3}%"

    print(f"\n=== SAÚDE DO CATÁLOGO — {total} produtos ===\n")
    print(f"  Código Almeida ......... {alm:>5}  {pct(alm)}   (liga busca por código na Almeida)")
    print(f"  Código Dantas .......... {dan:>5}  {pct(dan)}   (liga busca por código na Dantas)")
    print(f"  Ambos os códigos ....... {ambos:>5}  {pct(ambos)}")
    print(f"  SEM código nenhum ...... {nenhum:>5}  {pct(nenhum)}   (cai na busca textual)")
    print(f"  Com histórico de preço . {com_preco:>5}  {pct(com_preco)}")

    cat_sql = ("CASE WHEN instr(descricao_canonica,' ')>0 "
               "THEN substr(descricao_canonica,1,instr(descricao_canonica,' ')-1) "
               "ELSE descricao_canonica END")
    print("\n  --- Cobertura de código por categoria (top 15) ---")
    print(f"  {'categoria':<14}{'produtos':>9}{'c/ código':>11}{'cobertura':>11}")
    for cat, n, c in conn.execute(
        f"SELECT {cat_sql} cat, COUNT(*) n, "
        f"SUM(CASE WHEN ({tem_alm}) OR ({tem_dan}) THEN 1 ELSE 0 END) c "
        f"FROM produtos GROUP BY cat ORDER BY n DESC LIMIT 15"
    ).fetchall():
        print(f"  {str(cat):<14}{n:>9}{c:>11}{(str(100*c//n)+'%'):>11}")

    print("\n  --- Categorias com mais produtos SEM código (onde pedir prioridade) ---")
    for cat, n in conn.execute(
        f"SELECT {cat_sql} cat, COUNT(*) n FROM produtos "
        f"WHERE NOT ({tem_alm}) AND NOT ({tem_dan}) GROUP BY cat ORDER BY n DESC LIMIT 10"
    ).fetchall():
        print(f"  {str(cat):<14}{n:>5} sem código")

    print("\n=== O QUE PEDIR AO CLIENTE (derivado dos números acima) ===")
    if dan < total * 0.4:
        print(f"  1. CÓDIGOS DANTAS — só {pct(dan).strip()} dos produtos têm. É o maior buraco: "
              "a busca por código na Dantas quase não funciona. Peça planilhas com\n"
              "     o código Dantas dos itens (sufixo 'D' ou coluna própria).")
    if nenhum > total * 0.15:
        print(f"  2. CÓDIGOS dos {nenhum} produtos sem código nenhum — concentrados em "
              "staples (tubo, registro, aço, chapa...). Itens centrais sem código\n"
              "     caem na busca textual (mais fraca).")
    print("  3. MAIS HISTÓRICOS — cada planilha nova amplia categorias e ensina "
          "variações de nome. Formato MATRIZ; pode reenviar (importação é idempotente).")
    conn.close()


if __name__ == "__main__":
    main()
