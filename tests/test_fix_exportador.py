"""
Testes de regressão para dois bugs do exportador (grupo "exportador.py —
injeção de fórmula + markup robusto").

Cada teste FALHARIA na versão anterior do código e passa após o fix:

(#8) Injeção de fórmula: texto livre (descrição do edital, código/marca
     raspados, órgão, número do pregão) começando com '=', '+', '-' ou '@'
     era gravado direto na célula. openpyxl o interpreta como FÓRMULA viva
     (data_type 'f'), corrompendo a planilha entregue ao pregoeiro. O fix
     prefixa apóstrofo, forçando texto.

(#9) Markup silenciosamente não aplicado: _aplicar_markup derivava o sufixo
     a substituir de config.MARKUP_DEFAULT. Editar MARKUP_DEFAULT para 1.5
     (o config convida a isso) fazia o sufixo virar '*1.5', que nunca casa
     o literal '=S5*1.45' do template — markup não aplicado, sem erro. O fix
     troca o multiplicador via regex, independente de MARKUP_DEFAULT.

Sem rede/navegador/Streamlit. Usa o template real (o fix de markup depende do
formato '=Sn*1.45' dele) e monkeypatch para o cenário de MARKUP_DEFAULT alterado.
"""
from __future__ import annotations

from openpyxl import load_workbook

from pregoes import config
from pregoes.exportador import (
    SelecaoFornecedor,
    exportar,
    _escrever_texto,
    _aplicar_markup,
)
from pregoes.extrator import ItemExtraido


def _item(descricao="PARAFUSO 1/2", **kw) -> ItemExtraido:
    base = dict(descricao=descricao, unidade="UN", quantidade=10.0,
                valor_referencia=1.0, item_numero=1)
    base.update(kw)
    return ItemExtraido(**base)


# === (#8) Injeção de fórmula em texto livre =========================

def test_descricao_iniciando_com_igual_vira_texto_nao_formula(tmp_path):
    """Descrição '=cmd|calc' deve ser TEXTO literal na célula C, não fórmula.

    Antes do fix: data_type == 'f' (openpyxl trata como fórmula).
    Depois: data_type == 's' e o valor preserva o conteúdo (com apóstrofo de
    escape, que o Excel não exibe).
    """
    destino = tmp_path / "saida.xlsx"
    exportar([_item(descricao="=cmd|calc")], destino=destino)

    wb = load_workbook(destino)
    ws = wb.active
    cel = ws["C5"]
    assert cel.data_type != "f", "descrição com '=' virou FÓRMULA (corrompido)"
    # O apóstrofo de escape garante exibição literal do '=...'.
    assert cel.value == "'=cmd|calc"


def test_descricao_com_formula_excel_nao_e_avaliada(tmp_path):
    """Uma descrição que parece fórmula Excel ('=SOMA(A1)') não pode virar
    cálculo — é só texto do edital."""
    destino = tmp_path / "saida.xlsx"
    exportar([_item(descricao="=SOMA(A1)")], destino=destino)

    ws = load_workbook(destino).active
    cel = ws["C5"]
    assert cel.data_type != "f"
    assert cel.value == "'=SOMA(A1)"


def test_neutraliza_todos_os_prefixos_perigosos(tmp_path):
    """'+', '-' e '@' também disparam interpretação de fórmula no Excel.
    Ex.: '-3/4 POL' (medida de tubo) é texto, não subtração."""
    itens = [
        _item(descricao="+1 POLEGADA", item_numero=1),
        _item(descricao="-3/4 POL", item_numero=2),
        _item(descricao="@arroba", item_numero=3),
    ]
    destino = tmp_path / "saida.xlsx"
    exportar(itens, destino=destino)

    ws = load_workbook(destino).active
    for linha, esperado in [(5, "'+1 POLEGADA"), (6, "'-3/4 POL"), (7, "'@arroba")]:
        cel = ws[f"C{linha}"]
        assert cel.data_type != "f", f"linha {linha} virou fórmula"
        assert cel.value == esperado


def test_descricao_normal_nao_ganha_apostrofo(tmp_path):
    """Texto que NÃO começa com prefixo perigoso fica intacto — sem apóstrofo
    espúrio (não regredir o caso comum)."""
    destino = tmp_path / "saida.xlsx"
    exportar([_item(descricao="ABRAÇADEIRA NYLON 10MM")], destino=destino)

    ws = load_workbook(destino).active
    cel = ws["C5"]
    assert cel.value == "ABRAÇADEIRA NYLON 10MM"
    assert cel.data_type != "f"


def test_codigo_e_marca_do_fornecedor_neutralizados(tmp_path):
    """Colunas T (código) e U (marca) recebem texto raspado do site — também
    precisam de neutralização contra prefixo de fórmula."""
    destino = tmp_path / "saida.xlsx"
    selecoes = {
        0: SelecaoFornecedor(preco=12.34, codigo_fornecedor="=EVIL()", marca="-MARCA")
    }
    exportar([_item()], selecoes=selecoes, destino=destino)

    ws = load_workbook(destino).active
    assert ws["T5"].data_type != "f"
    assert ws["T5"].value == "'=EVIL()"
    assert ws["U5"].data_type != "f"
    assert ws["U5"].value == "'-MARCA"
    # Preço (coluna L) permanece numérico — neutralização não vaza para números.
    assert ws["L5"].value == 12.34
    assert ws["L5"].data_type == "n"


def test_orgao_e_numero_pregao_neutralizados(tmp_path):
    """Cabeçalhos C1 (órgão) e C2 (pregão) também são texto livre."""
    destino = tmp_path / "saida.xlsx"
    exportar([_item()], orgao="=ORGAO", numero_pregao="=123", destino=destino)

    ws = load_workbook(destino).active
    assert ws["C1"].data_type != "f"
    assert ws["C1"].value == "'=ORGAO"
    assert ws["C2"].data_type != "f"
    assert ws["C2"].value == "'=123"


def test_escrever_texto_helper_unitario():
    """Teste unitário direto do helper, sem abrir workbook completo."""
    from openpyxl import Workbook
    ws = Workbook().active
    _escrever_texto(ws["A1"], "=perigo")
    _escrever_texto(ws["A2"], "seguro")
    _escrever_texto(ws["A3"], "")        # vazio não tem prefixo perigoso
    assert ws["A1"].value == "'=perigo"
    assert ws["A2"].value == "seguro"
    assert ws["A3"].value in ("", None)  # openpyxl pode normalizar '' para None


# === (#9) Markup robusto (independente de MARKUP_DEFAULT) ============

def test_markup_aplica_independente_de_markup_default(tmp_path, monkeypatch):
    """REGRESSÃO do bug central: se alguém edita config.MARKUP_DEFAULT, o
    markup customizado ainda deve casar o literal '=S5*1.45' do template.

    Antes do fix: com MARKUP_DEFAULT=1.5, o sufixo procurado era '*1.5', que
    NÃO casa '=S5*1.45' → markup 1.6 não era aplicado (G5 ficava *1.45).
    Depois: regex troca o multiplicador qualquer que seja o default.
    """
    monkeypatch.setattr(config, "MARKUP_DEFAULT", 1.5)  # default "envenenado"
    destino = tmp_path / "saida.xlsx"
    # markup 1.6 != MARKUP_DEFAULT(=1.5) → _aplicar_markup é chamado
    exportar([_item()], markup=1.6, destino=destino)

    ws = load_workbook(destino).active
    assert ws["G5"].value == "=S5*1.6", (
        "markup não foi aplicado — bug de derivar sufixo de MARKUP_DEFAULT"
    )
    assert ws["G504"].value == "=S504*1.6"


def test_markup_customizado_no_template_padrao(tmp_path):
    """Caminho feliz: template '=S5*1.45', markup 1.6 → '=S5*1.6'."""
    destino = tmp_path / "saida.xlsx"
    exportar([_item()], markup=1.6, destino=destino)

    ws = load_workbook(destino).active
    assert ws["G5"].value == "=S5*1.6"
    assert ws["G504"].value == "=S504*1.6"
    # Referência Sn intacta (regex não pode mexer no número da linha "504").
    assert ws["G504"].value.startswith("=S504*")


def test_aplicar_markup_nao_toca_referencia_da_linha():
    """Unitário de _aplicar_markup: o '504' de 'S504' não pode ser alterado;
    só o multiplicador depois do '*'."""
    from openpyxl import load_workbook as _lw
    wb = _lw(config.TEMPLATE_MATRIZ)
    ws = wb.active
    assert ws["G504"].value == "=S504*1.45"  # pré-condição do template

    _aplicar_markup(ws, 2.0)
    assert ws["G504"].value == "=S504*2", "regex corrompeu a referência S504"
    assert ws["G5"].value == "=S5*2"


def test_markup_default_preserva_formula_original(tmp_path):
    """Sem markup explícito (usa default), G permanece idêntico ao template —
    _aplicar_markup nem é chamado."""
    destino = tmp_path / "saida.xlsx"
    exportar([_item()], destino=destino)

    ws = load_workbook(destino).active
    assert ws["G5"].value == "=S5*1.45"


def test_markup_formatado_sem_zeros_superfluos(tmp_path):
    """1.30 deve virar '=S5*1.3' (não '*1.30'); 2.0 vira '*2'."""
    destino = tmp_path / "saida.xlsx"
    exportar([_item()], markup=1.30, destino=destino)
    ws = load_workbook(destino).active
    assert ws["G5"].value == "=S5*1.3"

    destino2 = tmp_path / "saida2.xlsx"
    exportar([_item()], markup=2.0, destino=destino2)
    ws2 = load_workbook(destino2).active
    assert ws2["G5"].value == "=S5*2"
