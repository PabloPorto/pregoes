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

import unicodedata

from . import config, db
from .normalizador import normalizar_descricao


# Quantas linhas no topo procurar pelo cabeçalho (órgão/pregão ocupam 1-3).
_MAX_LINHAS_CABECALHO = 12


def _norm_cab(valor) -> str:
    """Normaliza um rótulo de cabeçalho para comparação (sem acento, minúsculo)."""
    if valor is None:
        return ""
    s = unicodedata.normalize("NFD", str(valor))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.strip().lower()


def _classificar_coluna(nome: str) -> Optional[str]:
    """Classifica um rótulo de cabeçalho em um campo do catálogo, ou None.

    Ordem importa: regras mais específicas primeiro. Casos exatos
    (`== "preco"`, `== "item"`) evitam confundir "PREÇO VENDA" (fórmula) com
    "PREÇO" (custo base) e "LUCRO ITEM" com "ITEM".
    """
    if not nome:
        return None
    if "descri" in nome or "discrimina" in nome or "especificacao" in nome:
        return "descricao"
    if "codigo" in nome or nome == "cod":
        return "codigo"
    if nome == "marca":
        return "marca"
    if "unid" in nome:
        return "unidade"
    if "referencia" in nome:           # "valor/preço de referência"
        return "valor_ref"
    if "venda" in nome:                # "preço venda" — fórmula, ignorar
        return None
    if "preco" in nome:                # "preço", "preço base", "preço custo"
        return "preco"
    if nome.startswith("quant") or nome == "qtd":
        return "quantidade"
    if nome == "item":                 # exato — não "lucro item"
        return "item"
    return None


def detectar_colunas(ws) -> tuple[Optional[int], dict[str, int]]:
    """Localiza a linha de cabeçalho e mapeia campo→índice de coluna (0-based).

    Resolve os layouts variáveis das planilhas reais do cliente (descrição
    pode estar em B ou C, código em S ou T, etc). A linha de cabeçalho é a
    primeira (entre as primeiras `_MAX_LINHAS_CABECALHO`) que contém uma
    coluna de descrição. A primeira coluna que classifica para um campo
    vence (evita que fórmulas à direita sobrescrevam).

    Retorna `(linha_cabecalho_0based, mapa)` ou `(None, {})` se não achar.
    """
    for i, row in enumerate(ws.iter_rows(values_only=True, max_row=_MAX_LINHAS_CABECALHO)):
        nomes = [_norm_cab(c) for c in row]
        if not any(("descri" in n or "discrimina" in n) for n in nomes):
            continue
        mapa: dict[str, int] = {}
        for j, n in enumerate(nomes):
            campo = _classificar_coluna(n)
            if campo and campo not in mapa:   # primeira ocorrência vence
                mapa[campo] = j
        if "descricao" in mapa:
            return i, mapa
    return None, {}


def _parse_codigo(codigo, default_forn: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Extrai `(codigo_busca, fornecedor)` de um código possivelmente sufixado.

    Convenção do cliente: a inicial do fornecedor pode vir colada ao FIM do
    código — `A` = Almeida, `D` = Dantas. O sufixo NÃO entra na busca
    (`/busca?s={codigo_busca}`). A última letra só é tratada como sufixo se
    for `A`/`D` E todo o trecho que a precede for numérico — assim "43026D"
    vira ("43026","dantas"), mas um código alfanumérico genuíno (ex.: "ABC12D")
    não é mutilado e segue inteiro com o `default_forn`.

    Sem sufixo → usa `default_forn` (o fornecedor que o operador indicou para
    aquela planilha).

    >>> _parse_codigo("43026D", "almeida")
    ('43026', 'dantas')
    >>> _parse_codigo("12345A", "dantas")
    ('12345', 'almeida')
    >>> _parse_codigo("52763", "almeida")
    ('52763', 'almeida')
    >>> _parse_codigo("52763.0", "dantas")
    ('52763', 'dantas')
    >>> _parse_codigo(None, "almeida")
    (None, 'almeida')
    """
    if codigo is None:
        return None, default_forn
    c = str(codigo).strip()
    # Código veio como float do Excel ("43026.0")
    if c.endswith(".0") and c[:-2].isdigit():
        c = c[:-2]
    if not c:
        return None, default_forn
    ult = c[-1].upper()
    resto = c[:-1]
    if ult in ("A", "D") and resto.isdigit():
        return resto, ("almeida" if ult == "A" else "dantas")
    return c, default_forn


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


def _log_import_seguro(conn, arquivo, n_produtos, n_precos, status, erro=None):
    """Registra em imports_log SEM nunca derrubar a importação.

    Rastreabilidade é best-effort: se gravar o log falhar (ex.: um caller
    injetou um conn de banco sem a tabela), o resultado real do import — já
    commitado ou já revertido — não pode ser afetado. Por isso engolimos o erro.
    """
    try:
        db.import_log_gravar(conn, arquivo, n_produtos, n_precos, status, erro)
    except Exception:
        pass


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
        conn = db.conectar()
        deve_fechar = True

    try:
        for path in arquivos:
            path = Path(path)
            if not path.exists():
                stats.avisos.append(f"Arquivo não encontrado: {path}")
                _log_import_seguro(conn, path.name, 0, 0, "nao_encontrado",
                                     "arquivo inexistente")
                conn.commit()
                continue
            # Snapshot dos contadores: se o arquivo falhar no meio, fazemos
            # ROLLBACK das linhas parciais no banco E revertemos os contadores
            # em memória (que _importar_arquivo já incrementou) — senão a
            # telemetria contaria linhas que foram desfeitas.
            snap = (stats.linhas_lidas, stats.produtos_criados,
                    stats.produtos_atualizados, stats.precos_gravados,
                    stats.precos_pulados)
            try:
                _importar_arquivo(path, fornecedor=fornecedor, conn=conn, stats=stats)
                n_prod = ((stats.produtos_criados + stats.produtos_atualizados)
                          - (snap[1] + snap[2]))
                n_preco = stats.precos_gravados - snap[3]
                _log_import_seguro(conn, path.name, n_prod, n_preco, "ok")
                # Commit POR ARQUIVO: persiste só este arquivo (linhas + log
                # juntos). Um crash depois daqui preserva os arquivos já
                # importados; e uma falha no PRÓXIMO não afeta este.
                conn.commit()
                stats.arquivos_processados += 1
            except Exception as exc:
                # Desfaz QUALQUER linha parcial deste arquivo (atomicidade por
                # arquivo) e reverte os contadores; não derruba o batch.
                conn.rollback()
                (stats.linhas_lidas, stats.produtos_criados,
                 stats.produtos_atualizados, stats.precos_gravados,
                 stats.precos_pulados) = snap
                stats.avisos.append(
                    f"Falha ao processar {path.name}: {type(exc).__name__}: {exc}"
                )
                _log_import_seguro(conn, path.name, 0, 0, "falha",
                                     f"{type(exc).__name__}: {exc}")
                conn.commit()  # persiste só o registro de falha (pós-rollback)
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
    """Processa uma planilha. `data_only=True` lê valores calculados (não
    fórmulas). Sem `read_only` porque detectamos colunas e depois iteramos —
    duas passadas; planilhas têm centenas de linhas, memória é irrelevante.

    O layout é detectado por cabeçalho (`detectar_colunas`), resolvendo as
    variações reais do cliente. O fornecedor de cada linha vem do sufixo do
    código (A/D) quando presente; senão, do `fornecedor` default da planilha.
    """
    wb = load_workbook(path, data_only=True)
    ws = wb.active

    lcab, mapa = detectar_colunas(ws)
    if lcab is None or "descricao" not in mapa:
        stats.avisos.append(
            f"{path.name}: cabeçalho (coluna DESCRIÇÃO) não reconhecido — planilha pulada."
        )
        wb.close()
        return
    if "codigo" not in mapa:
        stats.avisos.append(
            f"{path.name}: sem coluna de CÓDIGO — produtos importados sem código "
            f"(servem só para busca textual)."
        )

    fonte = f"import:{path.name}"
    for row in ws.iter_rows(min_row=lcab + 2, values_only=True):
        linha, aviso = _ler_linha_row(row, mapa)
        if aviso is not None:
            stats.avisos.append(f"{path.name}: {aviso}")
        if linha is None:
            continue
        stats.linhas_lidas += 1

        # O sufixo do código (A/D) define o fornecedor da linha; sem sufixo
        # cai no default da planilha.
        codigo_busca, forn = _parse_codigo(linha.codigo, fornecedor)

        produto_id, criado = _upsert_produto(
            conn,
            descricao=linha.descricao,
            marca=linha.marca,
            unidade=linha.unidade,
            codigo=codigo_busca,
            fornecedor=forn,
        )
        if criado:
            stats.produtos_criados += 1
        else:
            stats.produtos_atualizados += 1

        # Grava preço histórico se houver valor + fornecedor identificado.
        if linha.preco is not None and forn is not None:
            gravou = _gravar_preco_idempotente(
                conn,
                produto_id=produto_id,
                fornecedor=forn,
                preco=linha.preco,
                unidade=linha.unidade,
                fonte=fonte,
            )
            if gravou:
                stats.precos_gravados += 1
            else:
                stats.precos_pulados += 1

    wb.close()


def _ler_linha_row(
    row: tuple, mapa: dict[str, int]
) -> tuple[Optional[LinhaImportada], Optional[str]]:
    """Extrai uma linha (tupla de valores) usando o mapa de colunas detectado.

    Retorna `(linha, aviso)`:
    - `linha` é None se inválida (descrição vazia / continuação / totalizador).
    - `aviso` é uma mensagem em português quando a célula de PREÇO tinha
      conteúdo mas não pôde ser convertida em número (preço descartado).
      O caller registra esse aviso em `stats.avisos` — para que o operador
      saiba que um preço foi perdido, em vez de sumir em silêncio.
    """
    def get(campo: str):
        idx = mapa.get(campo)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    descricao_raw = _cell_str(get("descricao"))
    if not descricao_raw or len(descricao_raw) < 3:
        return None, None

    # Linha sem item E sem quantidade quase certamente é continuação ou
    # totalizador. Só aplica o filtro se ambas as colunas existem no layout.
    if "item" in mapa and "quantidade" in mapa:
        if get("item") is None and get("quantidade") is None:
            return None, None

    preco, preco_erro = _cell_num(get("preco"))
    aviso = None
    if preco_erro is not None:
        # Encurta a descrição na mensagem para não poluir o resumo.
        rotulo = descricao_raw if len(descricao_raw) <= 60 else descricao_raw[:57] + "..."
        aviso = f"Preço ignorado em '{rotulo}': {preco_erro}"

    linha = LinhaImportada(
        descricao=descricao_raw,
        unidade=_cell_str(get("unidade")) or None,
        marca=_cell_str(get("marca")) or None,
        codigo=_cell_str(get("codigo")) or None,
        preco=preco,
    )
    return linha, aviso


def _cell_str(valor) -> Optional[str]:
    """Normaliza uma célula em string limpa, ou None se vazia/branca."""
    if valor is None:
        return None
    s = str(valor).strip()
    return s if s else None


def _cell_num(valor) -> tuple[Optional[float], Optional[str]]:
    """Converte uma célula de preço em float. Retorna `(valor, erro)`.

    `valor` é o float (>0) ou None. `erro` é None em sucesso ou quando a
    célula está legitimamente vazia; é uma mensagem curta em português quando
    a célula TINHA conteúdo mas não pôde ser convertida — para o caller avisar
    o operador em vez de descartar o preço em silêncio.

    Aceita o formato monetário pt-BR que as planilhas reais do cliente usam:
    `1.234,56` (ponto de milhar + vírgula decimal), `R$ 5,00`, `10`. O caminho
    de célula numérica (`data_only=True` devolve int/float) é mantido intacto.
    """
    if valor is None:
        return None, None
    # Célula numérica (data_only) — caminho rápido, sem parsing de string.
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return (float(valor) if valor > 0 else None), None

    s = str(valor).strip()
    if not s:
        return None, None  # célula em branco — não é erro

    numero = _texto_para_float_br(s)
    if numero is None:
        # Conteúdo presente mas inconversível: sinaliza para o caller avisar.
        return None, f"valor não numérico {s!r}"
    return (numero if numero > 0 else None), None


def _texto_para_float_br(s: str) -> Optional[float]:
    """Interpreta um texto monetário pt-BR como float, ou None se inconversível.

    Regra: se há vírgula, ela é o separador decimal e os pontos são milhar
    (`1.234,56` → 1234.56). Sem vírgula, um ponto é tratado como decimal
    (`12.34` → 12.34). Remove `R$`, espaços (inclusive não-quebrável) e o
    sinal de milhar antes de delegar ao `float()`.
    """
    limpo = s.replace("R$", "").replace("\xa0", "").strip()
    limpo = limpo.replace(" ", "")
    if not limpo:
        return None
    if "," in limpo:
        # Vírgula decimal: ponto é separador de milhar, removê-lo.
        limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return float(limpo)
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
