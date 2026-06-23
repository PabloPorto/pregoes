"""
Testes da Fase 3 — busca robusta em 5 camadas (Q12).

Cobre:
- matcher.py (Camada 0)
- importador.py (alimenta o catálogo)
- scraper.buscar_por_codigo (Almeida e Dantas)
- buscar_precos_para_itens com Camadas 0+3+4 integradas
- normalizador.relevancia híbrida (Camada 2)
- extrator_llm.julgar_candidatos (Camada 3)
- mapeamentos_aprendidos (Camada 4)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from openpyxl import Workbook

from pregoes import config, db
from pregoes.db import init_db, get_conn
from pregoes.extrator import ItemExtraido
from pregoes.matcher import (
    MatchProduto,
    ProdutoCatalogo,
    buscar_produto,
    carregar_catalogo,
    melhor_match,
)
from pregoes.normalizador import relevancia
from pregoes.scraper import (
    ResultadoBusca,
    ResultadoLote,
    buscar_precos_para_itens,
)


# === Helpers ===

def _produto(produto_id: int, descricao: str, **kw) -> ProdutoCatalogo:
    return ProdutoCatalogo(
        produto_id=produto_id,
        descricao_canonica=descricao,
        codigo_almeida=kw.get("codigo_almeida"),
        codigo_dantas=kw.get("codigo_dantas"),
        marca=kw.get("marca"),
        unidade_padrao=kw.get("unidade_padrao"),
    )


def _r(descricao, marca="M", preco=10.0, codigo="X",
       confianca="exato", fornecedor="fake"):
    return ResultadoBusca(descricao, marca, preco, codigo, confianca, fornecedor)


# === Camada 0: matcher.py ===

class TestMatcher:
    def test_melhor_match_vazio_retorna_none(self):
        assert melhor_match("ABRACADEIRA", []) is None
        assert melhor_match("", [_produto(1, "X" * 10)]) is None
        assert melhor_match(None, [_produto(1, "X" * 10)]) is None

    def test_melhor_match_acima_threshold_retorna_produto(self):
        catalogo = [
            _produto(1, "ABRACADEIRA NYLON 100", codigo_almeida="A100",
                     marca="FOXLUX"),
            _produto(2, "VERGALHAO CA50", codigo_almeida="V50"),
        ]
        match = melhor_match("ABRACADEIRA NYLON 100", catalogo)
        assert match is not None
        assert match.produto.produto_id == 1
        assert match.score >= 90
        assert match.codigo_para("almeida") == "A100"
        assert match.codigo_para("dantas") is None

    def test_melhor_match_abaixo_threshold_retorna_none(self):
        catalogo = [_produto(1, "ABRACADEIRA NYLON")]
        # 'PARAFUSO' não casa com 'ABRACADEIRA' nem com score moderado
        assert melhor_match("PARAFUSO INOX 5MM", catalogo) is None

    def test_melhor_match_protege_contra_falso_positivo_sem_keyword(self):
        """Mesmo que token_sort_ratio dê score alto, sem coincidência de
        keyword distintiva NÃO casa (filtro de proteção do plano Q12)."""
        # Catálogo só tem 'CANO PVC' — buscar 'TUBO PVC' poderia dar score
        # alto por causa de 'pvc', mas keywords resultam apenas 'pvc' em
        # ambos lados, então casa OK (pvc é distintivo). Pra testar falso
        # positivo, uso descrições totalmente diferentes que por coincidência
        # têm score fuzz alto.
        catalogo = [_produto(1, "AAAA BBBB")]
        # Mesma estrutura mas tokens completamente diferentes
        match = melhor_match("XXXX YYYY", catalogo)
        assert match is None  # nada em comum

    def test_melhor_match_ignora_descricao_canonica_vazia(self):
        catalogo = [
            _produto(1, ""),
            _produto(2, "ABRACADEIRA NYLON"),
        ]
        match = melhor_match("ABRACADEIRA NYLON", catalogo)
        assert match is not None
        assert match.produto.produto_id == 2

    def test_melhor_match_resolve_ordem_trocada(self):
        catalogo = [_produto(1, "PVC ADAPTADOR SOLDAVEL 50MM",
                             codigo_almeida="X")]
        match = melhor_match("ADAPTADOR PVC SOLDAVEL 50MM", catalogo)
        assert match is not None
        assert match.score >= 90  # token_sort_ratio resolve

    def test_codigo_para_fornecedor_desconhecido(self):
        p = _produto(1, "X", codigo_almeida="A", codigo_dantas="D")
        m = MatchProduto(produto=p, score=95.0)
        assert m.codigo_para("almeida") == "A"
        assert m.codigo_para("dantas") == "D"
        assert m.codigo_para("inexistente") is None

    def test_carregar_catalogo_do_banco(self, tmp_path, monkeypatch):
        db_path = tmp_path / "cat.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)
        with get_conn(db_path) as conn:
            conn.execute("""
                INSERT INTO produtos
                    (descricao_canonica, marca, codigo_almeida, codigo_dantas,
                     unidade_padrao)
                VALUES (?, ?, ?, ?, ?)
            """, ("ABRACADEIRA NYLON 100", "FOXLUX", "A100", "D100", "PCT"))
            conn.execute("""
                INSERT INTO produtos (descricao_canonica)
                VALUES (?)
            """, ("",))  # entrada com descrição vazia deve ser filtrada
            conn.commit()
            catalogo = carregar_catalogo(conn)
        assert len(catalogo) == 1
        assert catalogo[0].codigo_almeida == "A100"
        assert catalogo[0].marca == "FOXLUX"

    def test_buscar_produto_integra_carregar_e_matcher(self, tmp_path, monkeypatch):
        db_path = tmp_path / "bp.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)
        with get_conn(db_path) as conn:
            conn.execute("""
                INSERT INTO produtos
                    (descricao_canonica, codigo_almeida)
                VALUES (?, ?)
            """, ("PARAFUSO INOX 5MM", "P5"))
            conn.commit()
            match = buscar_produto(conn, "PARAFUSO INOX 5MM")
        assert match is not None
        assert match.produto.codigo_almeida == "P5"


# === Importador ===

def _criar_planilha_matriz(path: Path, linhas: list[dict]) -> None:
    """Gera uma planilha sintética com a estrutura da MATRIZ.

    Linhas começam em 5. Cada dict pode ter: item, descricao, unidade,
    quantidade, valor_ref, preco, codigo, marca.
    """
    wb = Workbook()
    ws = wb.active
    # Cabeçalhos visuais (não importam pro importador, só pra realismo)
    ws["B4"] = "Item"
    ws["C4"] = "Descricao"
    ws["D4"] = "Unidade"
    ws["E4"] = "Quant"
    ws["F4"] = "Valor Ref"
    ws["L4"] = "Preco Base"
    ws["T4"] = "Codigo"
    ws["U4"] = "Marca"

    for offset, linha in enumerate(linhas):
        l = 5 + offset
        ws[f"B{l}"] = linha.get("item")
        ws[f"C{l}"] = linha.get("descricao")
        ws[f"D{l}"] = linha.get("unidade")
        ws[f"E{l}"] = linha.get("quantidade")
        ws[f"F{l}"] = linha.get("valor_ref")
        ws[f"L{l}"] = linha.get("preco")
        ws[f"T{l}"] = linha.get("codigo")
        ws[f"U{l}"] = linha.get("marca")
    wb.save(path)


class TestMatcherCanonico:
    """Fase B — matching sobre formas canônicas alinhadas pelo LLM."""

    def _cat(self, canonico, **kw):
        return ProdutoCatalogo(
            produto_id=kw.get("id", 1),
            descricao_canonica=kw.get("desc", "x"),
            codigo_almeida=kw.get("cod_a", "A1"),
            codigo_dantas=None, marca=kw.get("marca"), unidade_padrao=None,
            canonico=canonico,
        )

    def test_canonico_casa_vocabulario_diferente(self):
        """'cano' (edital) casa com 'tubo' (catálogo) via canônico — o que o
        fuzzy cru não conseguia."""
        catalogo = [self._cat("tubo pvc soldavel 100mm", id=1, cod_a="T100")]
        # modo cru: "CANO PVC SOLDAVEL 100" não casa "tubo pvc..."
        assert melhor_match("CANO PVC SOLDAVEL 100", catalogo) is None
        # modo canônico: ambos reduzidos a "tubo pvc soldavel 100mm"
        m = melhor_match("CANO PVC SOLDAVEL 100", catalogo,
                         canonico_edital="tubo pvc soldavel 100mm")
        assert m is not None
        assert m.produto.codigo_almeida == "T100"

    def test_dimensao_mm_distingue_bitola_hidraulica(self):
        """Regressão de edital real: joelho 60mm NÃO pode casar joelho 20mm.
        mm é o diâmetro nominal — a dimensão crítica em conexões PVC."""
        from pregoes.normalizador import extrair_dimensoes
        assert ("mm", "60") in extrair_dimensoes("joelho pvc soldavel 60mm")
        assert ("mm", "20") in extrair_dimensoes("joelho pvc soldavel 20mm")
        catalogo = [self._cat("joelho pvc soldavel 20mm", id=1,
                              desc="joelho pvc soldavel 20mm")]
        # 60mm (edital) não casa 20mm (catálogo) — dimensão diverge
        assert melhor_match("JOELHO PVC SOLDAVEL 60MM", catalogo,
                            canonico_edital="joelho pvc soldavel 60mm") is None

    def test_canonico_valida_dimensao_divergente(self):
        """Mesmo com vocabulário igual, dimensão diferente (5/16 vs 3/8) não
        casa — é subproduto distinto."""
        catalogo = [self._cat("parafuso sextavado 3/8 x 2", id=1)]
        m = melhor_match("PARAFUSO SEXTAVADO 5/16 X 4", catalogo,
                         canonico_edital="parafuso sextavado 5/16 x 4")
        assert m is None  # dimensão diverge

    def test_degradacao_sem_canonico_usa_modo_cru(self):
        """Sem canônico (catálogo não normalizado / sem quota), cai no modo
        cru — não quebra."""
        catalogo = [self._cat(None, id=1, desc="abracadeira nylon 100")]
        m = melhor_match("ABRACADEIRA NYLON 100", catalogo)
        assert m is not None  # modo cru ainda funciona

    def test_orquestrador_sem_chave_nao_canoniza(self, tmp_path, monkeypatch):
        """Degradação: sem chave Gemini, o orquestrador NÃO chama o alinhador
        (não gasta quota) mesmo com catálogo canonizado — usa modo cru."""
        from pregoes import credenciais
        import pregoes.alinhador as alinhador

        db_path = tmp_path / "deg.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)
        with get_conn(db_path) as conn:
            cur = conn.execute(
                "INSERT INTO produtos (descricao_canonica, codigo_almeida) "
                "VALUES (?, ?)", ("produto especial unico", "C123"))
            conn.execute(
                "INSERT INTO produtos_canonico (produto_id, canonico) "
                "VALUES (?, ?)", (cur.lastrowid, "produto especial unico"))
            conn.commit()

        monkeypatch.setattr(credenciais, "carregar_chave_api", lambda _: None)
        monkeypatch.setattr(alinhador, "normalizar_canonico",
                            lambda *a, **k: pytest.fail("não devia canonizar sem chave"))

        forn = _FornecedorComCodigo(); forn.nome = "almeida"
        itens = [ItemExtraido(descricao="PRODUTO ESPECIAL UNICO")]
        # Não levanta (alinhador não é chamado) e ainda casa por código via cru
        lote = buscar_precos_para_itens(
            itens, [forn], usar_cache=False, usar_llm_judge=False)
        assert lote.codigo_hits == 1


class TestImportador:
    def test_importa_planilha_cria_produtos(self, tmp_path, monkeypatch):
        from pregoes.importador import importar

        db_path = tmp_path / "imp.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        planilha = tmp_path / "hist.xlsx"
        _criar_planilha_matriz(planilha, [
            {"item": 1, "descricao": "ABRAÇADEIRA DE NYLON 100",
             "unidade": "PCT", "quantidade": 10, "preco": 3.50,
             "codigo": "A100", "marca": "FOXLUX"},
            {"item": 2, "descricao": "VERGALHÃO CA-50 12,5MM",
             "unidade": "M", "quantidade": 100, "preco": 5.20,
             "codigo": "V50", "marca": "GERDAU"},
        ])

        stats = importar([planilha], fornecedor="almeida")
        assert stats.arquivos_processados == 1
        assert stats.linhas_lidas == 2
        assert stats.produtos_criados == 2
        assert stats.precos_gravados == 2

        with get_conn(db_path) as conn:
            rows = conn.execute(
                "SELECT descricao_canonica, codigo_almeida, marca, unidade_padrao "
                "FROM produtos ORDER BY id"
            ).fetchall()
        assert len(rows) == 2
        assert rows[0]["codigo_almeida"] == "A100"
        assert rows[0]["marca"] == "FOXLUX"
        assert rows[1]["codigo_almeida"] == "V50"

    def test_importa_idempotente_nao_duplica(self, tmp_path, monkeypatch):
        from pregoes.importador import importar

        db_path = tmp_path / "imp2.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        planilha = tmp_path / "hist.xlsx"
        _criar_planilha_matriz(planilha, [
            {"item": 1, "descricao": "ABRAÇADEIRA NYLON 100",
             "unidade": "PCT", "quantidade": 10, "preco": 3.50,
             "codigo": "A100", "marca": "FOXLUX"},
        ])

        importar([planilha], fornecedor="almeida")
        stats2 = importar([planilha], fornecedor="almeida")
        # Segunda passada: produto já existe, preço já existe (mesmo fonte)
        assert stats2.produtos_criados == 0
        assert stats2.produtos_atualizados == 1
        assert stats2.precos_gravados == 0
        assert stats2.precos_pulados == 1

        with get_conn(db_path) as conn:
            n_produtos = conn.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
            n_precos = conn.execute("SELECT COUNT(*) FROM precos_historico").fetchone()[0]
        assert n_produtos == 1
        assert n_precos == 1  # não duplicou

    def test_importa_sem_fornecedor_so_descricao(self, tmp_path, monkeypatch):
        from pregoes.importador import importar

        db_path = tmp_path / "imp3.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        planilha = tmp_path / "hist.xlsx"
        _criar_planilha_matriz(planilha, [
            {"item": 1, "descricao": "TUBO PVC 50MM",
             "unidade": "M", "quantidade": 5, "preco": 8.0,
             "codigo": "T50", "marca": "TIGRE"},
        ])

        stats = importar([planilha], fornecedor=None)
        assert stats.produtos_criados == 1
        assert stats.precos_gravados == 0  # sem fornecedor, sem preço histórico

        with get_conn(db_path) as conn:
            row = conn.execute(
                "SELECT codigo_almeida, codigo_dantas FROM produtos"
            ).fetchone()
        assert row["codigo_almeida"] is None
        assert row["codigo_dantas"] is None

    def test_linha_sem_descricao_e_pulada(self, tmp_path, monkeypatch):
        from pregoes.importador import importar

        db_path = tmp_path / "imp4.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        planilha = tmp_path / "hist.xlsx"
        _criar_planilha_matriz(planilha, [
            {"item": 1, "descricao": "PRODUTO REAL",
             "unidade": "UN", "quantidade": 1, "preco": 1.0,
             "codigo": "P1", "marca": "X"},
            # Linha lixo — sem descrição mas com algum valor (tipo total)
            {"item": None, "descricao": None, "unidade": None,
             "quantidade": None, "preco": 999.99},
            # Linha com descrição mas sem item nem quant — totalizador
            {"item": None, "descricao": "TOTAL", "unidade": None,
             "quantidade": None, "preco": 100.0},
        ])

        stats = importar([planilha], fornecedor="almeida")
        assert stats.produtos_criados == 1
        # 1 produto criado, 1 preço gravado (do produto real)
        assert stats.precos_gravados == 1

    def test_detecta_layout_descricao_em_B(self, tmp_path):
        """Layout alternativo das planilhas reais: descrição em B, código em S
        (deslocado 1 coluna vs o template). Deve ser detectado por cabeçalho."""
        from openpyxl import Workbook
        from pregoes.importador import detectar_colunas
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws["A4"] = "ITEM"; ws["B4"] = "DESCRIÇÃO"; ws["C4"] = "UNID"
        ws["D4"] = "QUANT"; ws["K4"] = "PREÇO"; ws["S4"] = "CODIGO INTERNO"
        ws["T4"] = "MARCA"
        ws["G4"] = "PREÇO VENDA"  # fórmula — não pode virar 'preco'
        lcab, mapa = detectar_colunas(ws)
        letras = {k: get_column_letter(v + 1) for k, v in mapa.items()}
        assert letras["descricao"] == "B"
        assert letras["codigo"] == "S"
        assert letras["marca"] == "T"
        assert letras["preco"] == "K"   # PREÇO, não PREÇO VENDA (G)

    def test_parse_codigo_sufixo_fornecedor(self):
        from pregoes.importador import _parse_codigo
        assert _parse_codigo("43026D", "almeida") == ("43026", "dantas")
        assert _parse_codigo("12345A", "dantas") == ("12345", "almeida")
        assert _parse_codigo("52763", "almeida") == ("52763", "almeida")
        assert _parse_codigo("52763.0", "dantas") == ("52763", "dantas")
        assert _parse_codigo("ABC", "dantas") == ("ABC", "dantas")  # não numérico
        assert _parse_codigo(None, "almeida") == (None, "almeida")

    def test_sufixo_codigo_define_fornecedor_na_importacao(self, tmp_path, monkeypatch):
        """Planilha importada com default 'almeida', mas código com sufixo D
        vai para o codigo_dantas (sufixo sobrepõe o default)."""
        from pregoes.importador import importar

        db_path = tmp_path / "suf.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        planilha = tmp_path / "hist.xlsx"
        _criar_planilha_matriz(planilha, [
            {"item": 1, "descricao": "ABRAÇADEIRA NYLON 100", "quantidade": 10,
             "preco": 3.5, "codigo": "43026D", "marca": "WAVES"},  # sufixo D
            {"item": 2, "descricao": "TUBO PVC 100", "quantidade": 5,
             "preco": 8.0, "codigo": "52763", "marca": "TIGRE"},   # sem sufixo
        ])

        importar([planilha], fornecedor="almeida")  # default = almeida
        with get_conn(db_path) as conn:
            rows = conn.execute(
                "SELECT descricao_canonica, codigo_almeida, codigo_dantas "
                "FROM produtos ORDER BY id"
            ).fetchall()
        # Item 1: sufixo D → dantas (não almeida)
        assert rows[0]["codigo_dantas"] == "43026"
        assert rows[0]["codigo_almeida"] is None
        # Item 2: sem sufixo → default almeida
        assert rows[1]["codigo_almeida"] == "52763"
        assert rows[1]["codigo_dantas"] is None

    def test_marca_normalizada_evita_duplicatas(self, tmp_path, monkeypatch):
        """FOXLUX e Foxlux devem virar o mesmo produto (case-insensitive)."""
        from pregoes.importador import importar

        db_path = tmp_path / "imp5.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        planilha1 = tmp_path / "hist1.xlsx"
        planilha2 = tmp_path / "hist2.xlsx"
        _criar_planilha_matriz(planilha1, [
            {"item": 1, "descricao": "ABRACADEIRA",
             "quantidade": 1, "marca": "FOXLUX", "codigo": "A1"},
        ])
        _criar_planilha_matriz(planilha2, [
            {"item": 1, "descricao": "ABRACADEIRA",
             "quantidade": 1, "marca": "Foxlux", "codigo": "A1"},
        ])

        importar([planilha1, planilha2], fornecedor="almeida")
        with get_conn(db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM produtos").fetchone()[0]
        assert n == 1  # Marca case-folded


# === Orquestrador com Camadas 0 + 3 + 4 ===

class _FornecedorComCodigo:
    """Fornecedor fake que implementa `buscar_por_codigo`."""
    nome = "fakeforn"

    def __init__(self):
        self.chamadas_buscar = []
        self.chamadas_codigo = []
        # mapa codigo → resultado
        self._codigos = {
            "C123": _r("PRODUTO EXATO", "MX", 99.0,
                       codigo="C123", fornecedor="fakeforn"),
        }

    def buscar(self, descricao):
        self.chamadas_buscar.append(descricao)
        # Resultados textuais com confiança 'variacao' (simulando fallback)
        return [_r(f"VARIANTE {i}", "Mv", 100.0 + i,
                   codigo=f"V{i}", confianca="variacao", fornecedor="fakeforn")
                for i in range(3)]

    def buscar_por_codigo(self, codigo):
        self.chamadas_codigo.append(codigo)
        r = self._codigos.get(codigo)
        return [r] if r else []


class TestCamada0Integrada:
    def test_buscar_chama_buscar_por_codigo_quando_catalogo_tem_match(
        self, tmp_path, monkeypatch
    ):
        from pregoes import db as _db

        db_path = tmp_path / "cam0.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)
        # Popula catálogo
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT INTO produtos (descricao_canonica, codigo_almeida) "
                "VALUES (?, ?)",
                ("PRODUTO ESPECIAL UNICO", "C123"),
            )
            conn.commit()

        forn = _FornecedorComCodigo()
        forn.nome = "almeida"  # fingir que é Almeida pra match.codigo_para
        itens = [ItemExtraido(descricao="PRODUTO ESPECIAL UNICO")]

        lote = buscar_precos_para_itens(
            itens, [forn], usar_cache=False, usar_llm_judge=False,
        )

        # Chamou buscar_por_codigo, NÃO chamou buscar textual
        assert forn.chamadas_codigo == ["C123"]
        assert forn.chamadas_buscar == []
        assert lote.codigo_hits == 1
        assert len(lote.resultados[0]) == 1
        assert lote.resultados[0][0].codigo_fornecedor == "C123"

    def test_fallback_textual_quando_catalogo_vazio(self, tmp_path, monkeypatch):
        db_path = tmp_path / "cam0_v.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        forn = _FornecedorComCodigo()
        forn.nome = "almeida"
        itens = [ItemExtraido(descricao="NUNCA VISTO ANTES")]

        lote = buscar_precos_para_itens(
            itens, [forn], usar_cache=False, usar_llm_judge=False,
        )

        # Não chamou buscar_por_codigo (sem match no catálogo)
        # Chamou buscar textual normalmente
        assert forn.chamadas_codigo == []
        assert forn.chamadas_buscar == ["NUNCA VISTO ANTES"]
        assert lote.codigo_hits == 0

    def test_salvaguarda_descarta_codigo_que_traz_produto_sem_relacao(
        self, tmp_path, monkeypatch
    ):
        """Se a busca por código traz um produto sem afinidade com o que o
        catálogo esperava (código/fornecedor errado), a salvaguarda descarta
        e cai na busca textual."""
        db_path = tmp_path / "salv.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT INTO produtos (descricao_canonica, codigo_almeida) "
                "VALUES (?, ?)",
                ("martelo unha cabo madeira", "C999"),
            )
            conn.commit()

        class FornCodigoErrado:
            nome = "almeida"
            def __init__(self):
                self.chamou_buscar = []
            def buscar_por_codigo(self, codigo):
                # Código traz produto SEM relação (fornecedor trocado)
                return [ResultadoBusca("LAMPADA LED 9W BULBO", "M", 5.0,
                                       codigo, "exato", "almeida")]
            def buscar(self, descricao):
                self.chamou_buscar.append(descricao)
                return [_r("MARTELO UNHA TRAMONTINA", "TRAMONTINA", 12.0,
                           "T1", "exato", "almeida")]

        forn = FornCodigoErrado()
        itens = [ItemExtraido(descricao="MARTELO UNHA CABO MADEIRA")]
        lote = buscar_precos_para_itens(
            itens, [forn], usar_cache=False, usar_llm_judge=False)

        # Salvaguarda descartou o código (lâmpada) → caiu na busca textual
        assert forn.chamou_buscar == ["MARTELO UNHA CABO MADEIRA"]
        assert lote.codigo_hits == 0
        # Resultado final é o do textual (martelo), não a lâmpada
        marcas = {r.marca for r in lote.resultados[0]}
        assert "M" not in marcas  # lâmpada não entrou

    def test_usar_catalogo_false_desabilita_camada_0(self, tmp_path, monkeypatch):
        db_path = tmp_path / "cam0_d.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT INTO produtos (descricao_canonica, codigo_almeida) "
                "VALUES (?, ?)",
                ("PRODUTO ESPECIAL UNICO", "C123"),
            )
            conn.commit()

        forn = _FornecedorComCodigo()
        forn.nome = "almeida"
        itens = [ItemExtraido(descricao="PRODUTO ESPECIAL UNICO")]

        lote = buscar_precos_para_itens(
            itens, [forn], usar_cache=False, usar_catalogo=False,
            usar_llm_judge=False,
        )

        # Mesmo com match no catálogo, não chama buscar_por_codigo
        assert forn.chamadas_codigo == []
        assert forn.chamadas_buscar == ["PRODUTO ESPECIAL UNICO"]


# === Camada 2: relevância híbrida ===

class TestRelevanciaHibrida:
    def test_resolve_typos_leves(self):
        # PARAFUSO vs PARAFUZO — letras trocadas. Resolvido pelo boost
        # de typo (token_sort_ratio >= 90).
        score = relevancia("PARAFUSO 10MM", "PARAFUZO 10MM")
        assert score >= 0.6, f"esperado >= 0.6 (typo), recebi {score}"

    def test_resolve_ordem_trocada(self):
        score = relevancia("PVC ADAPTADOR", "ADAPTADOR PVC")
        assert score >= 0.7, f"esperado >= 0.7 (ordem), recebi {score}"

    def test_nao_cria_falso_positivo_tubo_vs_cano(self):
        """Tubo PVC 50MM e Cano PVC 100MM compartilham 'pvc' mas são
        produtos diferentes — categorias 'tubo' e '50mm' não batem em
        'cano pvc 100mm'."""
        score = relevancia("TUBO PVC 50MM", "CANO PVC 100MM")
        assert score < 0.35, f"esperado < 0.35 (categoria diferente), recebi {score}"

    def test_keyword_match_alto_mantido(self):
        """Quando todas as keywords-categoria casam, score sobe alto."""
        score = relevancia(
            "ABRAÇADEIRA DE NYLON 100 X 2.5",
            "ABRAÇADEIRA DE NYLON FOXLUX BRANCA 2.5X10CM C/100",
        )
        assert score >= 0.7


class TestRelevanciaCasoReal:
    """Travas de regressão baseadas no diagnóstico do TRE-PE
    (2026-05-27). Esses casos foram a evidência usada para calibrar
    a variante F + threshold 0.35."""

    def test_filtro_de_linha_match_verdadeiro_passa(self):
        edital = ("Filtro de linha com 5 (CINCO) tomadas elétricas de "
                  "10 Amperes, espaçadas de forma que possamos ligar 5 "
                  "plugues machos 90° simultaneamente")
        cand = "FILTRO DE LINHA MULTICRAFT 2P+T PRETO 10A 05 TOMADAS 2200V"
        assert relevancia(edital, cand) >= 0.35

    def test_filtro_de_linha_falso_positivo_luminaria_descartado(self):
        edital = ("Filtro de linha com 5 (CINCO) tomadas elétricas de "
                  "10 Amperes")
        cand = "LUMINARIA AVANT LED LINHA POP PLASTICO ABS 30W EMBUTIR"
        assert relevancia(edital, cand) < 0.35

    def test_lona_lonax_match_verdadeiro_passa(self):
        edital = ("LONA PLÁSTICA, ESPESSURA:200 MICRA, LARGURA:8 M, "
                  "COR:PRETA, COM CANTOS REFORÇADOS E ILHOSES METÁLICOS.")
        cand = "LONA PRETA E BRANCA LONAX 08X100 80KG 100 MICRA"
        assert relevancia(edital, cand) >= 0.35

    def test_lona_falso_positivo_filme_pvc_descartado(self):
        edital = ("LONA PLÁSTICA, ESPESSURA:200 MICRA, LARGURA:8 M, "
                  "COR:PRETA")
        cand = "FILME PVC ESTICAVEL RJM ESPESSURA 9 MICRAS / LARGURA 38CM"
        assert relevancia(edital, cand) < 0.35

    def test_chave_philips_passa_chave_fenda_nao(self):
        edital = ('Chave com ponta Philips (3/16" x 3") cabo isolante')
        philips = "CHAVE PHILIPS MAX 1/8”X3”"
        fenda = "CHAVE FENDA THOMPSON PROFISSIONAL CABO VERDE 1/4X4 EMBALAGEM"
        # PHILIPS é o match desejado; FENDA é categoria parecida mas
        # produto diferente.
        s_philips = relevancia(edital, philips)
        s_fenda = relevancia(edital, fenda)
        assert s_philips > s_fenda, f"philips={s_philips} fenda={s_fenda}"

    def test_removedor_etiquetas_nao_tem_no_catalogo(self):
        """Item real onde o catálogo não tem o produto — quaisquer
        candidatos retornados são falsos positivos. Score deve ficar
        abaixo do threshold pra UI mostrar 'Não encontrado'."""
        edital = ("Removedor de etiquetas e colas, a base de solvente, "
                  "utilizado para dissolver sujeiras, manchas, graxas")
        falsos = [
            "RESINA ACRÍLICA DE BASE SOLVENTE EUCATEX GALÃO 5L",
            "BASE PARA RELE FOTOCÉLULA SUPORTE DE FERRO DECORLUX 10A",
            "TORNEIRA SORTE METAIS GOURMET BASE ABS TUBO METAL",
            "ETIQUETA NORDESTE VOLTE SEMPRE MX5500 21X12MM C/8 METROS",
        ]
        for cand in falsos:
            score = relevancia(edital, cand)
            assert score < 0.35, f"esperado < 0.35 para {cand[:40]!r}, recebi {score}"

    def test_bateria_automotiva_nao_tem_no_catalogo(self):
        edital = ("Bateria automotiva 12V x 100 Ah Não selada/Com "
                  "manutenção. Dimensões: 330x172x241 mm")
        falsos = [
            "CABO PARA RECARGA DE BATERIA COM GARRA JACARÉ COMPOLUX 300A",
            "CHAVE DE IMPACTO TRAMONTINA 1/2” 20V 850NM - C/2 BATERIAS",
            "PARAFUSADEIRA/FURADEIRA DE IMPACTO 1/2 13MM 20V 96 N.M",
        ]
        for cand in falsos:
            score = relevancia(edital, cand)
            assert score < 0.35, f"esperado < 0.35 para {cand[:40]!r}, recebi {score}"

    def test_substring_pilha_em_empilhadeira_nao_casa(self):
        """Bug visto em produção: 'PILHA BRASFORT BATERIA LITIO CR2032'
        casava com 'Bateria automotiva... empilhadeira...' porque 'pilha'
        é substring de 'empilhadeira'. Fix: substring só conta como prefixo
        ou sufixo, não no meio."""
        edital = ("Bateria automotiva 12V x 100 Ah Não selada/Com "
                  "manutenção. Compatível com empilhadeira elétrica.")
        cand = "PILHA BRASFORT BATERIA LITIO (CR2032) 3V"
        score = relevancia(edital, cand)
        assert score < 0.35, f"esperado < 0.35 (pilha não é empilhadeira), recebi {score}"

    def test_prefix_sufix_continuam_funcionando(self):
        """Sanity check: o fix do PILHA/EMPILHADEIRA não pode quebrar
        casos legítimos de match por prefix/sufix."""
        # 'sold.' como prefixo de 'soldavel' (abreviação edital)
        assert relevancia(
            'ADAPTADOR PVC SOLD. CURTO DE 50 X 1.1/2"',
            'ADAPTADOR SOLDÁVEL CURTO AMANCO 50X1.1/2',
        ) >= 0.7
        # 'c/100' como prefixo de 'c/100und'
        assert relevancia(
            "ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND",
            "ABRAÇADEIRA DE NYLON FOXLUX BRANCA 2.5X10CM C/100",
        ) >= 0.5


class TestSemFallback:
    """Quando nenhum candidato passa o threshold, o scraper retorna
    lista vazia (não devolve 'top 3 variação' lixo). Pedido do operador
    em 2026-05-27: 'se não da match, n retornar'."""

    def test_scraper_almeida_retorna_vazio_sem_match(self):
        """Não há como rodar Playwright real aqui. Validamos via
        comportamento documentado: lista de candidatos brutos vai zero
        quando o site não tem nada relevante. O algoritmo retorna [].
        Isso é o que muda o comportamento da UI (de 'amarelo top 3
        lixo' para 'vazio — não encontrado')."""
        # Smoke test: o método existe e retorna lista vazia para input vazio
        from pregoes.scraper import AlmeidaDistribuidor
        alm = AlmeidaDistribuidor(email="x", senha="y")
        assert alm.buscar("") == []
        assert alm.buscar(None) == []

    @pytest.mark.parametrize("classe_nome", ["AlmeidaDistribuidor", "DantasDistribuidora"])
    def test_buscar_percorre_loop_completo_sem_match(self, classe_nome, monkeypatch):
        """Regressão do NameError 'melhor_pool' (2026-06): quando NENHUM
        candidato passa o threshold, o loop percorre TODAS as camadas até o
        fallback vazio. Mocka `_buscar_query` para devolver sempre lixo
        (rel < 0.35) e confirma retorno [] sem exceção. Exercita o caminho
        que só aparecia em produção (scraper real não tem teste)."""
        import pregoes.scraper as scraper_mod
        classe = getattr(scraper_mod, classe_nome)
        forn = classe(email="x", senha="y")
        nome = "almeida" if classe_nome == "AlmeidaDistribuidor" else "dantas"

        # Candidato sem nenhuma relação com a query → relevância < 0.35
        def fake_query(page, query, conf):
            return [scraper_mod.ResultadoBusca(
                "PRODUTO COMPLETAMENTE DIFERENTE ZZZ", "M", 1.0, "C", conf, nome)]

        monkeypatch.setattr(forn, "_buscar_query", fake_query)
        monkeypatch.setattr(forn._rate_limiter, "acquire", lambda: None)

        # TORNEIRA não casa com PRODUTO ZZZ → percorre tudo → fallback vazio
        assert forn.buscar("TORNEIRA MATERIAL CORPO METAL TIPO BICA MOVEL") == []


class TestTopNCandidatos:
    """Limite de candidatos por fornecedor — Q12 / pedido do operador
    em 2026-05-27: 'retornar menos resultados, 47 variações não é bom'."""

    def test_top_n_ordena_por_relevancia_desc_preco_asc(self):
        from pregoes.scraper import ResultadoBusca, _top_n_por_relevancia

        # candidatos com relevâncias diferentes contra "FILTRO DE LINHA"
        candidatos = [
            ResultadoBusca("LUMINARIA LED LINHA POP", "GAYA", 5.0, "A",
                           "variacao", "almeida"),
            ResultadoBusca("FILTRO DE LINHA 10A", "TECHNA", 20.0, "B",
                           "exato", "almeida"),
            ResultadoBusca("FILTRO DE LINHA 4 TOM", "ROMAZI", 15.0, "C",
                           "exato", "almeida"),
        ]
        top = _top_n_por_relevancia("FILTRO DE LINHA", candidatos, n=5)
        # Os 2 FILTROs vêm primeiro (rel alta), LUMINARIA por último
        assert top[0].marca in {"TECHNA", "ROMAZI"}
        assert top[-1].marca == "GAYA"

    def test_top_n_corta_quando_excede_limite(self):
        from pregoes.scraper import ResultadoBusca, _top_n_por_relevancia

        candidatos = [
            ResultadoBusca(f"FILTRO DE LINHA {i}", f"M{i}", 10.0 + i,
                           f"C{i}", "exato", "almeida")
            for i in range(20)
        ]
        top = _top_n_por_relevancia("FILTRO DE LINHA", candidatos, n=5)
        assert len(top) == 5


class TestQueryDistintiva:
    """Tier 1 (2026-06): a query primária passou a ser keywords distintivas
    curtas em vez da descrição completa do edital."""

    def test_keywords_distintivas_remove_texto_contratual(self):
        from pregoes.normalizador import keywords_distintivas
        q = keywords_distintivas(
            "Filtro de linha com 5 (CINCO) tomadas elétricas de 10 Amperes, "
            "conforme ABNT NBR 14136, garantia mínima de 03 meses", 4)
        assert q == ["filtro", "linha", "tomadas", "eletricas"]

    def test_keywords_distintivas_preserva_marca(self):
        from pregoes.normalizador import keywords_distintivas
        q = keywords_distintivas('Chave de fenda com ponta Philips (3/16" x 3")', 4)
        assert "philips" in q

    def test_keywords_distintivas_deduplica(self):
        from pregoes.normalizador import keywords_distintivas
        q = keywords_distintivas("removedor de etiquetas; removedor de colas", 4)
        assert q.count("removedor") == 1

    def test_montar_camadas_query_distintiva_primeiro(self):
        from pregoes.scraper import _montar_camadas
        camadas = _montar_camadas(
            "Filtro de linha com 5 (CINCO) tomadas elétricas de 10 Amperes "
            "conforme ABNT NBR 14136")
        # 1ª camada = query distintiva curta (não a descrição completa)
        assert camadas[0][1] == "filtro linha tomadas eletricas"
        # Descrição completa continua como fallback textual (contém "conforme")
        assert any("conforme" in q for _conf, q in camadas)
        # Onda 1 (2026-06-23): a rede de 1 keyword de CATEGORIA é o novo último
        # recurso — só dispara quando todas as anteriores zeram no site.
        assert camadas[-1][1] == "filtro"


class TestDimensao:
    """Tier 2 (2026-06): distinção por dimensão/medida."""

    def test_extrai_polegada(self):
        from pregoes.normalizador import extrair_dimensoes
        assert ("pol", "3/16") in extrair_dimensoes('Chave Philips 3/16" x 3"')

    def test_extrai_tomadas_e_ampere_com_numeral_extenso(self):
        from pregoes.normalizador import extrair_dimensoes
        dims = extrair_dimensoes("Filtro de linha 5 (CINCO) tomadas 10 Amperes")
        assert ("tom", "5") in dims
        assert ("amp", "10") in dims

    def test_dimensao_divergente_penaliza(self):
        # Chave 3/16 vs candidato 1/8 — mesmo eixo (polegada), valor diferente
        s_igual = relevancia("Chave fenda philips 3/16", "CHAVE FENDA PHILIPS 3/16X6")
        s_dif = relevancia("Chave fenda philips 3/16", "CHAVE FENDA PHILIPS 1/8X6")
        assert s_igual > s_dif

    def test_sem_dimensao_e_neutro(self):
        # Abraçadeira não tem dimensão reconhecível — não deve penalizar
        score = relevancia(
            "ABRAÇADEIRA DE NYLON 100 X 2.5",
            "ABRAÇADEIRA DE NYLON FOXLUX BRANCA 2.5X10CM C/100")
        assert score >= 0.7


class TestDiscriminador:
    """Tier 3 (2026-06): keyword discriminadora (marca/tipo)."""

    def test_discriminadora_pega_marca(self):
        from pregoes.normalizador import _keyword_discriminadora
        assert _keyword_discriminadora("Chave de fenda ponta Philips 3/16") == "philips"

    def test_discriminadora_ignora_medida_e_caracteristica(self):
        from pregoes.normalizador import _keyword_discriminadora
        # Lona: só medidas e características construtivas → sem discriminador
        assert _keyword_discriminadora(
            "LONA PLÁSTICA ESPESSURA 200 MICRA LARGURA 8M COR PRETA "
            "COM CANTOS REFORÇADOS E ILHOSES") is None

    def test_philips_vence_chave_comum_e_chave_teste(self):
        """O caso central do Tier 3: distinguir subtipos de chave de fenda."""
        edital = 'Chave de fenda com ponta Philips (3/16" x 3") cabo isolante'
        s_philips = relevancia(edital, "CHAVE FENDA PHILIPS MAX 3/16X6")
        s_comum = relevancia(edital, "CHAVE FENDA FAMASTIL CROMO VANADIO 3/16X3")
        s_teste = relevancia(edital, "CHAVE FENDA TESTE DECORLUX 100V/500V")
        # Philips com dimensão certa vence por larga margem
        assert s_philips >= 0.7
        assert s_philips > s_comum
        assert s_philips > s_teste
        # Subtipos errados ficam abaixo do threshold (filtrados)
        assert s_comum < 0.35
        assert s_teste < 0.35


class TestAlinhadorLLM:
    """Fase B — normalização canônica via Gemini. Testes SEM rede (cache,
    fallback, dedup). O teste com chamada real fica fora da suíte."""

    def test_fallback_local_sem_chave(self, tmp_path, monkeypatch):
        """Sem chave Gemini, devolve a normalização LOCAL — nunca quebra."""
        from pregoes import alinhador, credenciais
        db_path = tmp_path / "al.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)
        monkeypatch.setattr(credenciais, "carregar_chave_api", lambda _: None)

        res = alinhador.normalizar_canonico(
            ["PARAFUSO SXT 5/16", "Cano PVC 100"])
        # Fallback = normalizar_descricao (acentos/caixa), não levanta
        assert res["PARAFUSO SXT 5/16"] == "parafuso sxt 5/16"
        assert res["Cano PVC 100"] == "cano pvc 100"

    def test_usa_cache_sem_chamar_llm(self, tmp_path, monkeypatch):
        """Com a forma canônica já no cache, não chama o LLM."""
        from pregoes import alinhador, credenciais, db as _db
        db_path = tmp_path / "al2.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        # Pré-grava no cache a forma canônica de "parafuso sxt 5/16"
        norm = "parafuso sxt 5/16"
        h = alinhador._hash(norm)
        with get_conn(db_path) as conn:
            _db.canonico_gravar_muitos(conn, [(h, norm, "parafuso sextavado 5/16")])

        # Se tentar chamar o LLM, falha o teste (chave presente mas sem rede)
        chamou = {"llm": False}
        def _nao_chamar(*a, **k):
            chamou["llm"] = True
            return {}
        monkeypatch.setattr(alinhador, "_chamar_llm_em_lotes", _nao_chamar)
        monkeypatch.setattr(credenciais, "carregar_chave_api", lambda _: "FAKE")

        res = alinhador.normalizar_canonico(["PARAFUSO SXT 5/16"])
        assert res["PARAFUSO SXT 5/16"] == "parafuso sextavado 5/16"
        assert chamou["llm"] is False  # veio do cache

    def test_dedup_normaliza_iguais(self, tmp_path, monkeypatch):
        """Descrições que normalizam igual compartilham 1 entrada de LLM."""
        from pregoes import alinhador, credenciais
        db_path = tmp_path / "al3.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        recebidas = {}
        def _fake_llm(normas, key, modelo):
            recebidas["n"] = list(normas)
            return {n: f"CANON({n})" for n in normas}
        monkeypatch.setattr(alinhador, "_chamar_llm_em_lotes", _fake_llm)
        monkeypatch.setattr(credenciais, "carregar_chave_api", lambda _: "FAKE")

        # "Cano PVC" e "CANO PVC" normalizam para o mesmo → 1 só no LLM
        res = alinhador.normalizar_canonico(["Cano PVC", "CANO PVC", "Tubo X"])
        assert len(recebidas["n"]) == 2   # 'cano pvc' e 'tubo x'
        assert res["Cano PVC"] == res["CANO PVC"]


class TestCacheVersionado:
    """Quando a versão da busca muda, cache antigo é apagado
    automaticamente — operador não vê resultados velhos."""

    def test_invalidar_cache_quando_versao_muda(self, tmp_path, monkeypatch):
        """Salva versão antiga no arquivo, popula cache, chama
        invalidação. Cache deve sumir e arquivo atualiza para nova versão."""
        from pregoes import db as _db, config as _config, scraper
        import sqlite3

        # Aponta data_dir e db pra tmp
        monkeypatch.setattr(_config, "DATA_DIR", tmp_path)
        monkeypatch.setattr(_config, "DB_PATH", tmp_path / "t.db")
        _db.init_db(tmp_path / "t.db")

        # Popula cache com 1 entrada
        with _db.get_conn(tmp_path / "t.db") as conn:
            _db.cache_gravar(conn, "abracadeira nylon", "almeida", '[]')

        # Salva versão "antiga" no arquivo
        (tmp_path / ".busca_versao").write_text("VERSAO_ANTIGA")

        # Chama invalidador (a versão hardcoded no código difere)
        with _db.get_conn(tmp_path / "t.db") as conn:
            scraper._invalidar_cache_se_versao_mudou(conn)

        # Cache vazio + arquivo atualizado
        with _db.get_conn(tmp_path / "t.db") as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM precos_cache_descricao"
            ).fetchone()[0]
        assert n == 0
        assert (tmp_path / ".busca_versao").read_text().strip() == scraper.BUSCA_VERSAO


# === Camada 3: LLM judge ===

class TestLLMJudge:
    def test_parse_indices_valido(self):
        from pregoes.extrator_llm import _parse_indices_judge
        assert _parse_indices_judge("[0, 2, 1]", total_candidatos=3) == [0, 2, 1]

    def test_parse_indices_filtra_fora_range(self):
        from pregoes.extrator_llm import _parse_indices_judge
        # Índices fora do range são silenciosamente descartados
        assert _parse_indices_judge("[0, 5, 1]", total_candidatos=3) == [0, 1]

    def test_parse_indices_vazio(self):
        from pregoes.extrator_llm import _parse_indices_judge
        assert _parse_indices_judge("[]", total_candidatos=3) == []

    def test_parse_indices_markdown_wrapper(self):
        from pregoes.extrator_llm import _parse_indices_judge
        resp = "```json\n[2, 0]\n```"
        assert _parse_indices_judge(resp, total_candidatos=3) == [2, 0]

    def test_parse_indices_invalido_retorna_vazio(self):
        from pregoes.extrator_llm import _parse_indices_judge
        assert _parse_indices_judge("não é JSON", total_candidatos=3) == []
        assert _parse_indices_judge("", total_candidatos=3) == []
        assert _parse_indices_judge("{}", total_candidatos=3) == []

    def test_chave_judge_estavel_independente_de_ordem(self):
        """Hash precisa ser estável quando o scraper devolve candidatos
        em ordens diferentes — o cache só funciona se a chave for a mesma."""
        from pregoes.extrator_llm import _chave_judge
        c1 = [_r("X", codigo="A1"), _r("Y", codigo="A2")]
        c2 = [_r("Y", codigo="A2"), _r("X", codigo="A1")]
        assert _chave_judge("DESC", c1) == _chave_judge("DESC", c2)

    def test_chave_judge_muda_com_descricao(self):
        from pregoes.extrator_llm import _chave_judge
        candidatos = [_r("X", codigo="A1")]
        assert _chave_judge("DESC1", candidatos) != _chave_judge("DESC2", candidatos)

    def test_julgar_candidatos_usa_cache(self, tmp_path, monkeypatch):
        from pregoes.extrator_llm import julgar_candidatos
        import json

        db_path = tmp_path / "j.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        candidatos = [_r("PROD A", codigo="A1"), _r("PROD B", codigo="A2")]

        with get_conn(db_path) as conn:
            # Pré-popula cache
            from pregoes.extrator_llm import _chave_judge
            chave = _chave_judge("MEU ITEM", candidatos)
            db.llm_judge_gravar(conn, chave, "MEU ITEM", json.dumps([1, 0]))

            # Chama judge — deve usar cache (sem rede)
            res = julgar_candidatos("MEU ITEM", candidatos, conn=conn,
                                     api_key="fake-mas-nao-vai-usar")
        assert res == [1, 0]

    def test_julgar_candidatos_sem_chave_retorna_vazio(self, monkeypatch):
        from pregoes import credenciais
        from pregoes.extrator_llm import julgar_candidatos
        # Força ausência de chave no ambiente (o dev pode ter uma chave
        # configurada localmente — não queremos chamar Gemini real no CI).
        monkeypatch.setattr(credenciais, "carregar_chave_api", lambda _: None)
        candidatos = [_r("X", codigo="A1")]
        res = julgar_candidatos("X", candidatos, conn=None, api_key=None)
        assert res == []

    def test_julgar_candidatos_lista_vazia(self):
        from pregoes.extrator_llm import julgar_candidatos
        assert julgar_candidatos("X", [], conn=None) == []
        assert julgar_candidatos("", [_r("X")], conn=None) == []


# === Camada 4: mapeamentos aprendidos ===

class TestCamada4Aprendido:
    def test_aprendido_round_trip(self, tmp_path, monkeypatch):
        db_path = tmp_path / "ap.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        with get_conn(db_path) as conn:
            assert db.aprendido_buscar(conn, "abracadeira nylon", "almeida") is None
            db.aprendido_gravar(
                conn, "abracadeira nylon", "almeida",
                "A100", "FOXLUX", 3.50,
            )
            row = db.aprendido_buscar(conn, "abracadeira nylon", "almeida")
        assert row is not None
        assert row["codigo_fornecedor"] == "A100"
        assert row["marca"] == "FOXLUX"
        assert row["preco"] == 3.50

    def test_aprendido_upsert_substitui(self, tmp_path, monkeypatch):
        db_path = tmp_path / "ap2.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        with get_conn(db_path) as conn:
            db.aprendido_gravar(conn, "X", "almeida", "OLD", "OLDM", 1.0)
            db.aprendido_gravar(conn, "X", "almeida", "NEW", "NEWM", 2.0)
            row = db.aprendido_buscar(conn, "X", "almeida")
            n = conn.execute(
                "SELECT COUNT(*) FROM mapeamentos_aprendidos"
            ).fetchone()[0]
        assert row["codigo_fornecedor"] == "NEW"
        assert n == 1   # UPSERT, não duplicou

    def test_aprendido_separa_por_fornecedor(self, tmp_path, monkeypatch):
        db_path = tmp_path / "ap3.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        with get_conn(db_path) as conn:
            db.aprendido_gravar(conn, "X", "almeida", "A1", "MA")
            db.aprendido_gravar(conn, "X", "dantas", "D1", "MD")
            row_a = db.aprendido_buscar(conn, "X", "almeida")
            row_d = db.aprendido_buscar(conn, "X", "dantas")
        assert row_a["codigo_fornecedor"] == "A1"
        assert row_d["codigo_fornecedor"] == "D1"

    def test_buscar_usa_aprendido_antes_de_catalogo(self, tmp_path, monkeypatch):
        """Quando há mapeamento aprendido para a descrição+fornecedor,
        usa-o em vez de re-calcular pelo matcher."""
        db_path = tmp_path / "ap4.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        with get_conn(db_path) as conn:
            # Operador no passado escolheu "C123" para essa descrição
            db.aprendido_gravar(
                conn, "produto especial unico", "almeida",
                "C123", "MX", 99.0,
            )
            conn.commit()

        forn = _FornecedorComCodigo()
        forn.nome = "almeida"
        itens = [ItemExtraido(descricao="PRODUTO ESPECIAL UNICO")]

        lote = buscar_precos_para_itens(
            itens, [forn], usar_cache=False, usar_llm_judge=False,
        )

        # Foi pela busca por código (que existe no fake como C123)
        assert forn.chamadas_codigo == ["C123"]
        assert forn.chamadas_buscar == []
        assert lote.codigo_hits == 1


# === buscar_por_codigo dos scrapers ===

class TestBuscarPorCodigoEstrutura:
    """Garante que Almeida e Dantas expõem o método. Não roda Playwright."""

    def test_almeida_tem_buscar_por_codigo(self):
        from pregoes.scraper import AlmeidaDistribuidor
        alm = AlmeidaDistribuidor(email="x", senha="y")
        assert hasattr(alm, "buscar_por_codigo")
        assert callable(alm.buscar_por_codigo)

    def test_dantas_tem_buscar_por_codigo(self):
        from pregoes.scraper import DantasDistribuidora
        dan = DantasDistribuidora(email="x", senha="y")
        assert hasattr(dan, "buscar_por_codigo")
        assert callable(dan.buscar_por_codigo)

    def test_buscar_por_codigo_vazio_retorna_lista_vazia(self):
        from pregoes.scraper import AlmeidaDistribuidor
        alm = AlmeidaDistribuidor(email="x", senha="y")
        # Sem inicializar Playwright — apenas testa o guard de entrada
        assert alm.buscar_por_codigo("") == []
        assert alm.buscar_por_codigo(None) == []
        assert alm.buscar_por_codigo("   ") == []


# === Diagnóstico instrumentado ===

class TestDiagnostico:
    def test_log_preenche_quando_diagnostico_fornecido(self, tmp_path, monkeypatch):
        from pregoes import db as _db
        from pregoes.diagnostico import LogDiagnostico

        db_path = tmp_path / "diag.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        forn = _FornecedorComCodigo()
        forn.nome = "almeida"
        itens = [ItemExtraido(descricao="ITEM TESTE")]
        log = LogDiagnostico(fornecedores=["almeida"])

        buscar_precos_para_itens(
            itens, [forn], usar_cache=False, usar_llm_judge=False,
            diagnostico=log,
        )

        assert len(log.itens) == 1
        item = log.itens[0]
        assert item.descricao_original == "ITEM TESTE"
        assert len(item.por_fornecedor) == 1
        # FornecedorComCodigo.buscar não aceita `_log`, então as queries
        # internas ficam vazias (graceful fallback) mas o resto preenche.
        assert item.por_fornecedor[0].fornecedor == "almeida"
        # candidatos finais foram registrados pelo orquestrador
        assert len(item.por_fornecedor[0].candidatos_finais) > 0

    def test_log_nao_quebra_pipeline_sem_diagnostico(self, tmp_path, monkeypatch):
        """Sem `diagnostico=`, pipeline funciona idêntico (regressão)."""
        db_path = tmp_path / "diag2.db"
        monkeypatch.setattr(config, "DB_PATH", db_path)
        init_db(db_path)

        forn = _FornecedorComCodigo()
        forn.nome = "almeida"
        itens = [ItemExtraido(descricao="ITEM")]
        # Sem diagnostico=
        lote = buscar_precos_para_itens(
            itens, [forn], usar_cache=False, usar_llm_judge=False,
        )
        assert len(lote.resultados[0]) > 0

    def test_exportar_xlsx_gera_arquivo(self, tmp_path):
        from pregoes.diagnostico import (
            LogDiagnostico, LogItemFornecedor, LogCandidatoFinal,
            exportar_xlsx,
        )
        log = LogDiagnostico(fornecedores=["almeida"])
        it = log.get_item(0, "X", "x", ["x"])
        forn = LogItemFornecedor(fornecedor="almeida")
        forn.candidatos_finais.append(LogCandidatoFinal(
            descricao_encontrada="Y", marca="M", preco=1.0,
            codigo_fornecedor="C", fornecedor="almeida",
            confianca="exato", relevancia_vs_original=0.9,
            veio_por_codigo=False, aprovado_judge=False,
        ))
        it.por_fornecedor.append(forn)

        destino = tmp_path / "teste.xlsx"
        out = exportar_xlsx(log, destino)
        assert out.exists()
        assert out.stat().st_size > 1000

        # Verifica dropdown da coluna Avaliação
        from openpyxl import load_workbook
        wb = load_workbook(out)
        ws = wb["Resumo"]
        assert len(ws.data_validations.dataValidation) >= 1
        dv = ws.data_validations.dataValidation[0]
        assert "correto" in dv.formula1
        assert "errado" in dv.formula1
        assert "aceitavel" in dv.formula1
