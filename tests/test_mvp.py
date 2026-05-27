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
