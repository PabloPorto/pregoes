"""
Testes automatizados do MVP. Roda com: pytest tests/

Cobre o caminho crítico: extrair PDF → exportar Excel preservando fórmulas.
"""
import pytest
from pathlib import Path
from openpyxl import load_workbook

from pregoes.extrator import extrair_pdf, ItemExtraido, _para_numero, _normaliza
from pregoes.exportador import exportar
from pregoes.db import init_db, get_conn


PDF_TESTE = Path(__file__).parent.parent / "data" / "editais" / "araruna_teste_sintetico.pdf"


# === Extrator: parsing numérico ===

@pytest.mark.parametrize("entrada,esperado", [
    ("1.234,56", 1234.56),
    ("1234,56", 1234.56),
    ("1234.56", 1234.56),
    ("R$ 1.234,56", 1234.56),
    ("0,99", 0.99),
    (None, None),
    ("", None),
    ("abc", None),
    (42, 42.0),
    (3.14, 3.14),
])
def test_para_numero(entrada, esperado):
    assert _para_numero(entrada) == esperado


def test_normaliza_remove_acentos():
    assert _normaliza("DESCRIÇÃO") == "descricao"
    assert _normaliza("Especificação") == "especificacao"
    assert _normaliza("  Quantidade  ") == "quantidade"
    assert _normaliza(None) == ""


# === Extrator: integração com PDF real ===

def test_extrator_pdf_sintetico():
    """Sanity check: PDF de teste extrai todos os 25 itens."""
    if not PDF_TESTE.exists():
        # Gera o PDF se não existir
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from gerar_pdf_teste import gerar
        gerar(PDF_TESTE)

    res = extrair_pdf(PDF_TESTE)
    assert res.total_itens == 25
    assert res.total_duvidosos == 0
    assert "descricao" in res.mapa_colunas_detectado
    assert "quantidade" in res.mapa_colunas_detectado


def test_extrator_detecta_dados_corretos():
    res = extrair_pdf(PDF_TESTE)
    primeiro = res.itens[0]
    assert "ABRAÇADEIRA" in primeiro.descricao
    assert primeiro.quantidade == 40.0
    assert primeiro.valor_referencia == 16.40
    assert primeiro.unidade == "PACOTE"


# === Exportador ===

def test_exportador_preserva_formulas(tmp_path):
    """Garante que o exportador NÃO destruiu as fórmulas do template."""
    res = extrair_pdf(PDF_TESTE)
    destino = tmp_path / "saida.xlsx"
    exportar(res.itens, destino=destino)

    wb = load_workbook(destino)
    ws = wb.active

    # Linha 5 (primeiro item) deve ter fórmulas em G, H, I, J, K, S
    for col in ["G", "H", "I", "J", "K", "S"]:
        v = ws[f"{col}5"].value
        assert isinstance(v, str) and v.startswith("="), f"{col}5 perdeu fórmula: {v}"

    # Total da linha 507 deve estar preservado
    assert ws["C507"].value == "=SUM(I5:I504)"
    assert ws["G507"].value == "=SUM(K5:K504)"


def test_exportador_preenche_dados_certos(tmp_path):
    res = extrair_pdf(PDF_TESTE)
    destino = tmp_path / "saida.xlsx"
    exportar(res.itens, orgao="Teste", numero_pregao="123", destino=destino)

    wb = load_workbook(destino)
    ws = wb.active

    assert ws["C1"].value == "Teste"
    assert ws["C2"].value == "123"
    assert ws["E5"].value == 40
    assert ws["F5"].value == 16.40
    assert "ABRAÇADEIRA" in ws["C5"].value


def test_exportador_aplica_markup_customizado(tmp_path):
    """Quando o usuário passa markup != default, todas as fórmulas G são
    reescritas. Outras fórmulas (H, I, etc.) referenciam G e ajustam-se
    automaticamente pelo Excel."""
    res = extrair_pdf(PDF_TESTE)
    destino = tmp_path / "saida.xlsx"
    exportar(res.itens, markup=1.30, destino=destino)

    wb = load_workbook(destino)
    ws = wb.active
    # Linha 5: =S5*1.30 (não mais *1.45)
    assert ws["G5"].value == "=S5*1.3"
    # Linha 504 (última do template): =S504*1.30
    assert ws["G504"].value == "=S504*1.3"
    # Fórmulas adjacentes preservadas
    assert ws["H5"].value == "=J5/G5"
    assert ws["S5"].value == "=L5+N5+P5+R5"


def test_exportador_markup_default_preserva_template(tmp_path):
    """Sem markup explícito, fórmulas G ficam idênticas ao template (*1.45)."""
    res = extrair_pdf(PDF_TESTE)
    destino = tmp_path / "saida.xlsx"
    exportar(res.itens, destino=destino)

    wb = load_workbook(destino)
    ws = wb.active
    assert ws["G5"].value == "=S5*1.45"
    assert ws["G504"].value == "=S504*1.45"


def test_exportador_preenche_LTU_quando_ha_selecao(tmp_path):
    """Quando o operador escolhe marca/preço, exportador preenche L (preço),
    T (código fornecedor), U (marca) na linha correspondente."""
    from pregoes.exportador import SelecaoFornecedor
    res = extrair_pdf(PDF_TESTE)
    destino = tmp_path / "saida.xlsx"
    selecoes = {
        0: SelecaoFornecedor(preco=12.34, codigo_fornecedor="ABC123", marca="FOXLUX"),
        2: SelecaoFornecedor(preco=99.99, codigo_fornecedor="XYZ", marca="THOMPSON"),
    }
    exportar(res.itens, selecoes=selecoes, destino=destino)

    wb = load_workbook(destino)
    ws = wb.active
    # Linha 5 (item 0) preenchida
    assert ws["L5"].value == 12.34
    assert ws["T5"].value == "ABC123"
    assert ws["U5"].value == "FOXLUX"
    # Linha 6 (item 1) sem seleção: L/T/U vazios
    assert ws["L6"].value is None
    assert ws["T6"].value is None
    assert ws["U6"].value is None
    # Linha 7 (item 2) preenchida
    assert ws["L7"].value == 99.99
    assert ws["U7"].value == "THOMPSON"


def test_exportador_sem_selecoes_nao_toca_LTU(tmp_path):
    """Comportamento pré-Fase 2 deve seguir intacto quando selecoes não é passado."""
    res = extrair_pdf(PDF_TESTE)
    destino = tmp_path / "saida.xlsx"
    exportar(res.itens, destino=destino)
    wb = load_workbook(destino)
    ws = wb.active
    for linha in range(5, 10):
        assert ws[f"L{linha}"].value is None
        assert ws[f"T{linha}"].value is None
        assert ws[f"U{linha}"].value is None


def test_exportador_lista_vazia_falha(tmp_path):
    with pytest.raises(ValueError, match="vazia"):
        exportar([], destino=tmp_path / "vazio.xlsx")


def test_exportador_template_inexistente(tmp_path):
    with pytest.raises(FileNotFoundError):
        exportar(
            [ItemExtraido(descricao="x", quantidade=1)],
            destino=tmp_path / "saida.xlsx",
            template=tmp_path / "nao_existe.xlsx",
        )


# === DB ===

def test_init_db(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    assert db.exists()

    with get_conn(db) as conn:
        tabelas = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"produtos", "precos_historico", "editais",
            "edital_itens", "mapeamentos_pdf"}.issubset(tabelas)


def test_db_idempotente(tmp_path):
    """init_db pode ser chamado várias vezes sem quebrar."""
    db = tmp_path / "test.db"
    init_db(db)
    init_db(db)
    init_db(db)
    assert db.exists()


# === Validador semântico ===

from pregoes.validador import score_linha, score_tabela


@pytest.mark.parametrize("desc,qtd,unid,vref,score_min", [
    # Caso claramente válido: descrição longa, qtd, unidade, valor
    ("ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND", 40, "PACOTE", 16.40, 0.95),
    # Sem valor de referência (vimos em vários editais reais): ainda alto
    ("VERGALHÃO ACO CA-50 12,5 MM", 100, "BARRA", None, 0.80),
    # Sem unidade explícita: cai mas continua aceitável
    ("CIMENTO PORTLAND CP-II SACO 50KG", 30, None, 28.90, 0.70),
])
def test_score_linha_itens_validos_sao_aceitos(desc, qtd, unid, vref, score_min):
    s = score_linha(desc, qtd, unid, vref)
    assert s.score >= score_min, f"Esperado >= {score_min}, veio {s.score:.2f} ({s.motivos})"


@pytest.mark.parametrize("desc,qtd,unid,vref", [
    # Linhas tipicamente capturadas pelo extrator antigo como itens
    ("Tatiana Araújo Monteiro da Cruz", None, None, None),    # nome de pessoa (TRE-PE)
    ("XXXXXXXXXXXXXXXXXXXXXXXXXXXX", None, None, None),      # placeholder modelo de proposta
    ("Total geral", None, None, 1234.56),                     # linha de total
    ("GRUPO 01 - FERRAMENTAS R$ 10.184,60", None, None, None),  # cabeçalho de grupo
    ("3.3.90.30", None, None, None),                           # rubrica orçamentária
    ("Área Requisitante", None, None, None),                  # rótulo de formulário
    ("", 100, "UND", 10.0),                                    # descrição vazia
])
def test_score_linha_lixo_e_descartado(desc, qtd, unid, vref):
    s = score_linha(desc, qtd, unid, vref)
    assert s.score < 0.4, f"Esperado < 0.4 (descartável), veio {s.score:.2f} ({s.motivos})"


def test_score_tabela_alta_qualidade():
    """Tabela com 10 linhas todas válidas → score alto."""
    scores = [score_linha(f"ABRAÇADEIRA DE NYLON {i} MM", 100, "PCT", 5.0) for i in range(10)]
    sc = score_tabela(scores)
    assert sc.score >= 0.8
    assert sc.n_linhas_validas == 10


def test_score_tabela_lixo():
    """Tabela com placeholders/lixo → score baixo."""
    scores = [score_linha("XXXXXX", None, None, None) for _ in range(5)]
    sc = score_tabela(scores)
    assert sc.score < 0.4


def test_score_tabela_poucas_linhas_validas_e_penalizado():
    """Mesmo com 2 linhas perfeitas, tabela com só 2 itens válidos é
    penalizada (não vale a pena lançar como tabela de itens)."""
    scores_bons = [score_linha("ABRAÇADEIRA DE NYLON 100 X 2.5", 40, "PCT", 16.40)
                   for _ in range(2)]
    sc = score_tabela(scores_bons)
    # fator_volume = 2/3 ≈ 0.66, então score < 0.7
    assert sc.score < 0.7


def test_extrator_expoe_confianca_global():
    """Garante que o pipeline novo expõe confiança no resultado."""
    res = extrair_pdf(PDF_TESTE)
    assert 0.0 <= res.confianca_global <= 1.0
    # PDF sintético é controlado: confiança deve ser alta
    assert res.confianca_global >= 0.9


# === Mapeamento manual ===

from pregoes import mapeamento_manual as mm


def test_assinatura_cabecalho_estavel():
    """Mesma linha (varia caixa/acento/espaço) → mesma assinatura."""
    a = mm.assinatura_cabecalho(["Item", "Descrição", "Quant.", "Unidade", "Valor"])
    b = mm.assinatura_cabecalho(["ITEM", "DESCRICAO", "Quant.", "unidade", "VALOR"])
    c = mm.assinatura_cabecalho(["  item  ", "descrição ", " quant.", "unidade", "Valor"])
    assert a == b == c


def test_assinatura_cabecalho_diferente_para_layouts_diferentes():
    a = mm.assinatura_cabecalho(["Item", "Descrição", "Quant.", "Unidade"])
    b = mm.assinatura_cabecalho(["Item", "Descrição", "Quant.", "Unidade", "Valor"])
    assert a != b


def test_mapeamento_salvar_e_carregar(tmp_path):
    """Round-trip: salva mapa, lê de volta, valida que veio igual."""
    db = tmp_path / "test.db"
    init_db(db)
    mapa_original = {"descricao": 2, "quantidade": 4, "unidade": 3,
                     "valor_referencia": 5, "codigo_orgao": 0}
    sig = "abcd1234"

    with get_conn(db) as conn:
        mm.salvar_mapa(conn, sig, mapa_original)

    with get_conn(db) as conn:
        recuperado = mm.carregar_mapa(conn, sig)

    assert recuperado == mapa_original


def test_mapeamento_carregar_inexistente_retorna_none(tmp_path):
    db = tmp_path / "test.db"
    init_db(db)
    with get_conn(db) as conn:
        assert mm.carregar_mapa(conn, "qualquer-coisa-inexistente") is None


def test_aplicar_mapa_extrai_itens_corretos():
    """Tabela bruta + mapa → ItemExtraido. Lixo é filtrado por score."""
    tabela = [
        ["Item", "Descrição", "Qtd", "Un", "Valor"],     # cabeçalho
        ["1", "ABRAÇADEIRA DE NYLON 100 X 2.5", "40", "PCT", "16.40"],
        ["2", "VERGALHÃO ACO CA-50 12,5 MM", "100", "BARRA", "85.50"],
        ["3", "Total Geral", "—", "—", "1234.56"],       # linha-lixo
    ]
    mapa = {"codigo_orgao": 0, "descricao": 1, "quantidade": 2,
            "unidade": 3, "valor_referencia": 4}

    res = mm.aplicar_mapa(tabela, mapa, arquivo="test.pdf")
    assert res.total_itens == 2   # linha 'Total Geral' descartada pelo score
    assert res.itens[0].quantidade == 40
    assert res.itens[0].valor_referencia == 16.40
    assert res.itens[1].quantidade == 100
    assert res.confianca_global == 1.0


# === Pipeline multi-camada (extrair_com_pipeline) ===

from pregoes.extrator import extrair_com_pipeline
from pregoes import extrator_docling


def test_pipeline_pdf_sintetico_resolve_na_camada_1():
    """PDF sintético é simples — pdfplumber resolve. Não precisa Docling."""
    res = extrair_com_pipeline(PDF_TESTE)
    assert res.total_itens == 25
    assert res.confianca_global >= 0.9
    # Primeiro aviso deve indicar qual camada resolveu
    assert any("pdfplumber" in a.lower() or "rápida" in a.lower()
               for a in res.avisos)


def test_disponivel_docling_responde_sem_quebrar():
    """A função `disponivel()` checa importlib sem importar de verdade —
    funciona mesmo quando Docling não está instalado."""
    out = extrator_docling.disponivel()
    assert isinstance(out, bool)
