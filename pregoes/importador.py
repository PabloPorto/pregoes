"""
Importador do catálogo histórico — Camada 0 da Fase 3 (Q12).

Lê planilhas no formato MATRIZ que o cliente já preencheu em pregões
anteriores (Fase 0 — template sagrado) e popula a tabela `produtos`
com a descrição canônica, marca, unidade e códigos dos fornecedores
(Almeida e/ou Dantas). Também grava preços em `precos_historico` quando
a planilha tem a coluna L (preço base) preenchida.

Sem este insumo, o pipeline da Fase 3 cai direto na busca por texto.
Com 10–20 planilhas importadas, espera-se cobrir 60-70% dos itens
recorrentes em editais por busca por código (determinística), bypassando
a disputa de relevância textual.

CLI (modo planejado):
    python -m pregoes.importador --fornecedor=almeida data/historico/*.xlsx
    python -m pregoes.importador --fornecedor=dantas  data/historico/*.xlsx

`--fornecedor` indica para QUAL fornecedor a coluna T da planilha aponta.
Sem ele, descrição/marca/unidade são importadas (úteis para o matcher
fuzzy puro) mas os códigos não — o cliente precisa saber em qual fornecedor
ele consultou aquela planilha.

Idempotência:
- `produtos` tem UNIQUE(descricao_canonica, marca) → UPSERT atualiza
  códigos sem duplicar.
- `precos_historico` é dedupado por (produto_id, fornecedor, fonte): a
  fonte é "import:<basename>" e cada planilha só insere uma vez.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from openpyxl import load_workbook

from . import config, db
from .normalizador import normalizar_descricao


# Linha onde os dados começam (igual ao template). Linhas 1-3 são cabeçalho
# de identificação (órgão, pregão), linha 4 é cabeçalho das colunas.
LINHA_INICIO = config.LINHA_DADOS_INICIO       # 5
# Limite superior: o template padrão vai até 504. Planilhas expandidas podem
# ter mais — varremos até a max_row real do worksheet, mas não confiamos em
# linhas sem descrição (paramos lá).
LINHA_FIM_PADRAO = 504

# Letras das colunas conforme `config.COLUNAS`
COL_ITEM = "B"
COL_DESCRICAO = "C"
COL_UNIDADE = "D"
COL_QUANTIDADE = "E"
COL_VALOR_REF = "F"
COL_PRECO_BASE = "L"
COL_CODIGO_FORN = "T"
COL_MARCA = "U"


@dataclass
class StatsImport:
    """Telemetria da importação — devolvida pela CLI ao operador.

    Total de produtos lidos / criados / atualizados informa quanta cobertura
    a importação trouxe ao matcher. Preços gravados/pulados ajuda a entender
    se o histórico de valores está sendo capturado.
    """
    arquivos_processados: int = 0
    linhas_lidas: int = 0
    produtos_criados: int = 0
    produtos_atualizados: int = 0
    precos_gravados: int = 0
    precos_pulados: int = 0  # dedupe por fonte
    avisos: list[str] = field(default_factory=list)

    def resumo(self) -> str:
        partes = [
            f"{self.arquivos_processados} arquivo(s) processado(s)",
            f"{self.linhas_lidas} linha(s) lida(s)",
            f"{self.produtos_criados} produto(s) novo(s)",
            f"{self.produtos_atualizados} produto(s) atualizado(s)",
            f"{self.precos_gravados} preço(s) histórico(s) gravado(s)",
        ]
        if self.precos_pulados:
            partes.append(f"{self.precos_pulados} preço(s) já estavam no histórico")
        return " · ".join(partes)


@dataclass(frozen=True)
class LinhaImportada:
    """Uma linha lida de uma planilha histórica. Pré-validada."""
    descricao: str
    unidade: Optional[str]
    marca: Optional[str]
    codigo: Optional[str]
    preco: Optional[float]


def importar(
    arquivos: Iterable[Path | str],
    *,
    fornecedor: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> StatsImport:
    """Importa N planilhas históricas para o catálogo e o histórico de preços.

    Args:
        arquivos: caminhos das planilhas .xlsx (formato MATRIZ).
        fornecedor: "almeida" ou "dantas" — em qual coluna T a planilha
            cadastrou o código. Pode ser None: nesse caso só descrição/marca/
            unidade entram (sem códigos), o que ainda alimenta o matcher
            mas não a busca por código.
        conn: conexão SQLite aberta. Se None, usa o banco padrão de config.

    Retorna `StatsImport` com a telemetria. Não levanta exceção em linhas
    inválidas — registra em `stats.avisos` e segue. Levanta se o arquivo
    não puder ser aberto (caller deve tratar).
    """
    if fornecedor is not None and fornecedor not in ("almeida", "dantas"):
        raise ValueError(
            f"fornecedor deve ser 'almeida', 'dantas' ou None — recebi {fornecedor!r}"
        )

    stats = StatsImport()
    deve_fechar = False
    if conn is None:
        db.init_db()
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        deve_fechar = True

    try:
        for path in arquivos:
            path = Path(path)
            if not path.exists():
                stats.avisos.append(f"Arquivo não encontrado: {path}")
                continue
            try:
                _importar_arquivo(path, fornecedor=fornecedor, conn=conn, stats=stats)
                stats.arquivos_processados += 1
            except Exception as exc:
                # Não derruba o batch — registra e segue. Caller decide se
                # quer interromper baseado nos avisos.
                stats.avisos.append(
                    f"Falha ao processar {path.name}: {type(exc).__name__}: {exc}"
                )
        conn.commit()
    finally:
        if deve_fechar:
            conn.close()

    return stats


def _importar_arquivo(
    path: Path,
    *,
    fornecedor: Optional[str],
    conn: sqlite3.Connection,
    stats: StatsImport,
) -> None:
    """Processa uma planilha. `data_only=True` para ler valores calculados
    das fórmulas (o cliente preenche L manualmente, mas células como E5
    podem ter sido digitadas como fórmula em editais antigos)."""
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb.active

    fonte = f"import:{path.name}"
    max_row = min(ws.max_row or LINHA_FIM_PADRAO, max(LINHA_FIM_PADRAO, ws.max_row or 0))

    for linha_num in range(LINHA_INICIO, max_row + 1):
        linha = _ler_linha(ws, linha_num)
        if linha is None:
            continue
        stats.linhas_lidas += 1

        produto_id, criado = _upsert_produto(
            conn,
            descricao=linha.descricao,
            marca=linha.marca,
            unidade=linha.unidade,
            codigo=linha.codigo,
            fornecedor=fornecedor,
        )
        if criado:
            stats.produtos_criados += 1
        else:
            stats.produtos_atualizados += 1

        # Grava preço histórico se houver valor + fornecedor identificado.
        # Sem fornecedor, não há onde alocar o preço (nem inferir).
        if linha.preco is not None and fornecedor is not None:
            gravou = _gravar_preco_idempotente(
                conn,
                produto_id=produto_id,
                fornecedor=fornecedor,
                preco=linha.preco,
                unidade=linha.unidade,
                fonte=fonte,
            )
            if gravou:
                stats.precos_gravados += 1
            else:
                stats.precos_pulados += 1

    wb.close()


def _ler_linha(ws, linha_num: int) -> Optional[LinhaImportada]:
    """Extrai uma linha da planilha em formato MATRIZ. None se for inválida
    (descrição vazia, lixo, etc.) — caller pula sem registrar."""
    descricao_raw = _cell_str(ws[f"{COL_DESCRICAO}{linha_num}"].value)
    if not descricao_raw or len(descricao_raw) < 3:
        return None

    # Linha sem item E sem quantidade quase certamente é continuação ou
    # totalizador. Descartamos pra não poluir o catálogo.
    item_val = ws[f"{COL_ITEM}{linha_num}"].value
    quant_val = ws[f"{COL_QUANTIDADE}{linha_num}"].value
    if item_val is None and quant_val is None:
        return None

    unidade = _cell_str(ws[f"{COL_UNIDADE}{linha_num}"].value)
    marca = _cell_str(ws[f"{COL_MARCA}{linha_num}"].value)
    codigo = _cell_str(ws[f"{COL_CODIGO_FORN}{linha_num}"].value)
    preco = _cell_num(ws[f"{COL_PRECO_BASE}{linha_num}"].value)

    return LinhaImportada(
        descricao=descricao_raw,
        unidade=unidade or None,
        marca=marca or None,
        codigo=codigo or None,
        preco=preco,
    )


def _cell_str(valor) -> Optional[str]:
    """Normaliza uma célula em string limpa, ou None se vazia/branca."""
    if valor is None:
        return None
    s = str(valor).strip()
    return s if s else None


def _cell_num(valor) -> Optional[float]:
    """Tenta converter para float — aceita número direto, string '12,34' ou
    '12.34'. Retorna None se for vazio, fórmula ou texto inconversível."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor) if valor > 0 else None
    s = str(valor).strip().replace(",", ".")
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def _upsert_produto(
    conn: sqlite3.Connection,
    *,
    descricao: str,
    marca: Optional[str],
    unidade: Optional[str],
    codigo: Optional[str],
    fornecedor: Optional[str],
) -> tuple[int, bool]:
    """Insere ou atualiza um produto. Retorna (produto_id, criado?).

    Chave UNIQUE em (descricao_canonica, marca) — descrição canônica aqui
    é a versão normalizada. Marca pode ser NULL (planilha pode ter rodado
    sem coluna U preenchida); UNIQUE com NULL no SQLite trata cada NULL
    como distinto, então usamos "" (string vazia) para marca ausente.
    """
    desc_canonica = normalizar_descricao(descricao)
    if not desc_canonica:
        # Improvável (já filtrado em _ler_linha), mas mantém invariante
        raise ValueError(f"descrição não normalizável: {descricao!r}")

    marca_chave = (marca or "").strip().upper()  # normaliza casing pra evitar duplicatas FOXLUX/Foxlux

    # Existe?
    coluna_codigo = "codigo_almeida" if fornecedor == "almeida" else (
        "codigo_dantas" if fornecedor == "dantas" else None
    )

    row = conn.execute(
        "SELECT id, codigo_almeida, codigo_dantas, unidade_padrao "
        "FROM produtos WHERE descricao_canonica = ? AND COALESCE(marca, '') = ?",
        (desc_canonica, marca_chave),
    ).fetchone()

    if row is not None:
        # Atualiza só o que faz sentido — não sobrescreve códigos existentes
        # de outro fornecedor; só preenche o que estiver vazio.
        produto_id = row["id"]
        novos = {}
        if coluna_codigo and codigo and not row[coluna_codigo]:
            novos[coluna_codigo] = codigo
        if unidade and not row["unidade_padrao"]:
            novos["unidade_padrao"] = unidade
        if novos:
            sets = ", ".join(f"{k} = ?" for k in novos) + ", atualizado_em = CURRENT_TIMESTAMP"
            conn.execute(
                f"UPDATE produtos SET {sets} WHERE id = ?",
                (*novos.values(), produto_id),
            )
        return produto_id, False

    # Insere novo
    valores = {
        "descricao_canonica": desc_canonica,
        "marca": marca_chave or None,
        "unidade_padrao": unidade,
        "codigo_almeida": codigo if fornecedor == "almeida" else None,
        "codigo_dantas": codigo if fornecedor == "dantas" else None,
    }
    cursor = conn.execute(
        """
        INSERT INTO produtos
            (descricao_canonica, marca, unidade_padrao,
             codigo_almeida, codigo_dantas)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            valores["descricao_canonica"],
            valores["marca"],
            valores["unidade_padrao"],
            valores["codigo_almeida"],
            valores["codigo_dantas"],
        ),
    )
    return cursor.lastrowid, True


def _gravar_preco_idempotente(
    conn: sqlite3.Connection,
    *,
    produto_id: int,
    fornecedor: str,
    preco: float,
    unidade: Optional[str],
    fonte: str,
) -> bool:
    """Insere em precos_historico se ainda não há registro com a mesma fonte.

    Retorna True se gravou, False se pulou (dedupe). Mecanismo:
    `fonte = "import:<basename>"` é único por planilha — re-importar a
    mesma planilha não duplica preços históricos.
    """
    ja_existe = conn.execute(
        """
        SELECT 1 FROM precos_historico
        WHERE produto_id = ? AND fornecedor = ? AND fonte = ?
        LIMIT 1
        """,
        (produto_id, fornecedor, fonte),
    ).fetchone()
    if ja_existe:
        return False
    db.gravar_preco(
        conn, produto_id=produto_id, fornecedor=fornecedor,
        preco=preco, unidade=unidade, fonte=fonte,
    )
    return True


# === CLI ===

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pregoes.importador",
        description=(
            "Importa planilhas históricas (formato MATRIZ) para o catálogo "
            "de produtos. Alimenta a Camada 0 da busca (Q12)."
        ),
    )
    parser.add_argument(
        "arquivos",
        nargs="+",
        help="Caminho(s) das planilhas .xlsx a importar.",
    )
    parser.add_argument(
        "--fornecedor",
        choices=("almeida", "dantas"),
        default=None,
        help=(
            "Em qual fornecedor a coluna T (código) foi cadastrada. "
            "Omitir só importa descrição/marca/unidade (sem códigos)."
        ),
    )
    args = parser.parse_args(argv)

    arquivos = [Path(a) for a in args.arquivos]
    stats = importar(arquivos, fornecedor=args.fornecedor)
    print(stats.resumo())
    if stats.avisos:
        print("\nAvisos:")
        for av in stats.avisos:
            print(f"  - {av}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
