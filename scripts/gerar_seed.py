# -*- coding: utf-8 -*-
"""Gera data/pregoes_seed.db — o banco-semente para instalação NOVA.

O seed é o banco real MENOS o que é volátil ou específico desta máquina:

PRESERVA (o conhecimento acumulado que faz o sistema "nascer esperto"):
- catálogo: produtos, produtos_canonico
- aprendizado do operador: mapeamentos_aprendidos
- histórico de preços: precos_historico
- mapas de PDF aprendidos: mapeamentos_pdf
- caches de IA: cache_canonico, cache_llm_judge (evitam custo Gemini
  na instalação nova)

REMOVE (volátil / desta máquina):
- editais processados (CASCADE leva edital_itens e exports_log junto)
- cache de busca TTL 2d: precos_cache_descricao
- log de importações: imports_log

A cópia usa a API nativa de backup do SQLite (consistente mesmo com WAL
ativo) e o banco de origem passa pelo porteiro de integridade antes.

Uso (no Mac de dev, com o banco real em data/pregoes.db):
    python scripts/gerar_seed.py
Gera/sobrescreve data/pregoes_seed.db (gitignored — contém dados do cliente).
O passo a passo de instalação está em INSTALACAO.md.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pregoes import config                      # noqa: E402
from pregoes.db import _integridade_ok          # noqa: E402

VOLATEIS = ("precos_cache_descricao", "editais", "imports_log")
PRESERVADAS = ("produtos", "produtos_canonico", "mapeamentos_aprendidos",
               "precos_historico", "mapeamentos_pdf",
               "cache_canonico", "cache_llm_judge")


def main() -> int:
    origem_path = config.DB_PATH
    seed_path = config.DATA_DIR / "pregoes_seed.db"

    if not origem_path.exists():
        print(f"ERRO: banco de origem não existe: {origem_path}")
        return 1

    origem = sqlite3.connect(origem_path)
    try:
        if not _integridade_ok(origem):
            print("ERRO: o banco real falhou na verificação de integridade — "
                  "seed NÃO gerado. Restaure um backup íntegro antes.")
            return 2

        # Sobrescreve seed anterior (e sidecars de WAL, se sobraram).
        for p in (seed_path, seed_path.with_name(seed_path.name + "-wal"),
                  seed_path.with_name(seed_path.name + "-shm")):
            p.unlink(missing_ok=True)

        destino = sqlite3.connect(seed_path)
        try:
            origem.backup(destino)
            destino.execute("PRAGMA foreign_keys = ON;")
            for tabela in VOLATEIS:
                destino.execute(f"DELETE FROM {tabela};")
            destino.commit()
            destino.execute("VACUUM;")

            print(f"Seed gerado: {seed_path}")
            for t in PRESERVADAS:
                n = destino.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"  {t}: {n}")
            for t in ("editais", "edital_itens", "exports_log",
                      "precos_cache_descricao", "imports_log"):
                n = destino.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                assert n == 0, f"{t} deveria estar vazia, tem {n}"
            print("  (editais/itens/exports/cache de busca/imports: zerados)")
        finally:
            destino.close()
    finally:
        origem.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
