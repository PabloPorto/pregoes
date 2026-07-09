"""
Testes de regressão do exportador — migrados para o MODELO DO CONTADOR (2026-07).

Dois grupos de invariantes:

(#8) INJEÇÃO DE FÓRMULA em texto livre. Texto do edital, código/marca raspados
     do site — qualquer string começando com '=', '+', '-' ou '@' — era gravado
     direto na célula. openpyxl o interpreta como FÓRMULA viva (data_type 'f'),
     corrompendo a planilha entregue ao pregoeiro. O fix prefixa apóstrofo,
     forçando texto. Este contrato NÃO mudou com o novo template; apenas as
     linhas de dados agora começam na LINHA 3 (antes 5).

(#9) PREÇO / TRIBUTAÇÃO. No modelo antigo o exportador escrevia o PREÇO VENDA em
     G como fórmula '=S*markup' e o efeito da Substituição Tributária vinha de um
     markup MENOR nessa fórmula. No modelo do contador isso mudou:
       - G (PREÇO VENDA) é MANUAL — o exportador NUNCA escreve G (fica None);
       - os parâmetros markup/markups continuam na assinatura por
         compatibilidade, mas são IGNORADOS (não afetam a planilha);
       - a Substituição Tributária virou uma FLAG NATIVA: o app grava o texto
         'SIM' na coluna AC dos itens com ST e deixa AC vazio nos demais; a
         própria planilha lê IF(AC="SIM",...) na fórmula de gross-up (Z).
     Os antigos testes de "markup em G" foram RETARGETADOS para estes invariantes
     novos (G manual + ST via AC), preservando a intenção de cobertura.

Sem rede/navegador/Streamlit. Usa o template real do contador.
"""
from __future__ import annotations

from openpyxl import load_workbook

from pregoes import config, tributacao
from pregoes.exportador import (
    SelecaoFornecedor,
    exportar,
    _escrever_texto,
)
from pregoes.extrator import ItemExtraido


def _item(descricao="PARAFUSO 1/2", **kw) -> ItemExtraido:
    base = dict(descricao=descricao, unidade="UN", quantidade=10.0,
                valor_referencia=1.0, item_numero=1)
    base.update(kw)
    return ItemExtraido(**base)


# === (#8) Injeção de fórmula em texto livre =========================
# Modelo do contador: dados começam na LINHA 3 (config.LINHA_DADOS_INICIO).

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
    cel = ws["C3"]
    assert cel.data_type != "f", "descrição com '=' virou FÓRMULA (corrompido)"
    # O apóstrofo de escape garante exibição literal do '=...'.
    assert cel.value == "'=cmd|calc"


def test_descricao_com_formula_excel_nao_e_avaliada(tmp_path):
    """Uma descrição que parece fórmula Excel ('=SOMA(A1)') não pode virar
    cálculo — é só texto do edital."""
    destino = tmp_path / "saida.xlsx"
    exportar([_item(descricao="=SOMA(A1)")], destino=destino)

    ws = load_workbook(destino).active
    cel = ws["C3"]
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
    # itens 1,2,3 → linhas 3,4,5 (LINHA_DADOS_INICIO=3)
    for linha, esperado in [(3, "'+1 POLEGADA"), (4, "'-3/4 POL"), (5, "'@arroba")]:
        cel = ws[f"C{linha}"]
        assert cel.data_type != "f", f"linha {linha} virou fórmula"
        assert cel.value == esperado


def test_descricao_normal_nao_ganha_apostrofo(tmp_path):
    """Texto que NÃO começa com prefixo perigoso fica intacto — sem apóstrofo
    espúrio (não regredir o caso comum)."""
    destino = tmp_path / "saida.xlsx"
    exportar([_item(descricao="ABRAÇADEIRA NYLON 10MM")], destino=destino)

    ws = load_workbook(destino).active
    cel = ws["C3"]
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
    assert ws["T3"].data_type != "f"
    assert ws["T3"].value == "'=EVIL()"
    assert ws["U3"].data_type != "f"
    assert ws["U3"].value == "'-MARCA"
    # Preço (coluna L) permanece numérico — neutralização não vaza para números.
    assert ws["L3"].value == 12.34
    assert ws["L3"].data_type == "n"


def test_orgao_e_numero_pregao_neutralizados_no_nome_do_arquivo(tmp_path, monkeypatch):
    """RETARGET: no modelo antigo órgão/pregão eram escritos em C1/C2 (texto
    livre) e precisavam de neutralização de fórmula. O modelo do contador NÃO
    tem essas células — órgão/número passaram a compor apenas o NOME do arquivo
    gerado. A mesma intenção ('texto livre do usuário não pode virar conteúdo
    perigoso') foi retargetada para o nome do arquivo, que sanitiza mantendo só
    alfanuméricos: '=ORGAO'/'=123' viram 'ORGAO'/'123', sem '=' nem separadores
    de caminho."""
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path)
    caminho = exportar([_item()], orgao="=ORGAO", numero_pregao="=123")

    nome = caminho.name
    for perigoso in ("=", "+", "@", "/", "\\"):
        assert perigoso not in nome, f"caractere perigoso '{perigoso}' vazou no nome do arquivo"
    assert "ORGAO" in nome
    assert "123" in nome


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


# === (#9) Preço manual (G) + Substituição Tributária via flag AC =====
# RETARGET do antigo bloco "markup em G". No modelo do contador o preço não é
# mais '=S*markup': G é MANUAL (nunca escrito) e o efeito do ST migrou para a
# flag 'SIM' na coluna AC. Os parâmetros markup/markups são aceitos mas IGNORADOS.

def test_markup_ignorado_independente_de_markup_default(tmp_path, monkeypatch):
    """RETARGET do bug de acoplamento com MARKUP_DEFAULT: antes, editar
    config.MARKUP_DEFAULT quebrava a aplicação do markup em G. Agora G é MANUAL
    e o parâmetro markup é IGNORADO — logo NÃO há mais acoplamento nenhum: G3
    fica vazio (None) qualquer que seja o MARKUP_DEFAULT ou o markup passado."""
    monkeypatch.setattr(config, "MARKUP_DEFAULT", 1.5)  # default "envenenado"
    destino = tmp_path / "saida.xlsx"
    exportar([_item()], markup=1.6, destino=destino)

    ws = load_workbook(destino).active
    assert ws["G3"].value is None, "G (PREÇO VENDA) é manual — não pode ser escrito"


def test_markup_customizado_nao_escreve_g(tmp_path):
    """RETARGET do 'markup customizado no template padrão': passar markup=1.6 não
    pode escrever G (preço é manual). O total de LUCRO do rodapé (linha 604)
    permanece a fórmula nativa do template."""
    destino = tmp_path / "saida.xlsx"
    exportar([_item()], markup=1.6, destino=destino)

    ws = load_workbook(destino).active
    assert ws["G3"].value is None
    # Rodapé preservado: G604 = LUCRO total = SUM(K3:K600), intocado pelo markup.
    assert ws["G604"].value == "=SUM(K3:K600)"


def test_exportar_preserva_formula_de_preco_do_template(tmp_path):
    """RETARGET do unitário '_aplicar_markup não toca referência da linha': aquele
    teste garantia que a manipulação de fórmula não corrompia a referência Sn. A
    função _aplicar_markup foi REMOVIDA (o exportador não cria mais fórmula de
    preço). A intenção equivalente hoje é: o exportador PRESERVA intacta a fórmula
    de preço nativa do template — o gross-up da coluna Z, que lê a flag AC. Se ela
    saísse corrompida, o preço sugerido ao operador estaria errado."""
    wb_tpl = load_workbook(config.TEMPLATE_MATRIZ)
    z3_template = wb_tpl.active["Z3"].value
    wb_tpl.close()
    # Pré-condição do template: fórmula de gross-up que referencia AC.
    assert isinstance(z3_template, str) and z3_template.startswith("=") and "AC" in z3_template

    destino = tmp_path / "saida.xlsx"
    exportar([_item()], destino=destino)

    ws = load_workbook(destino).active
    assert ws["Z3"].value == z3_template, "fórmula de preço (Z) foi corrompida na exportação"


def test_sem_markup_g_permanece_manual(tmp_path):
    """RETARGET do 'markup default preserva fórmula original': sem markup
    explícito, G continua idêntico ao template — que no modelo do contador é
    VAZIO (preço manual). Ou seja, o exportador não inventa preço em G."""
    destino = tmp_path / "saida.xlsx"
    exportar([_item()], destino=destino)

    ws = load_workbook(destino).active
    assert ws["G3"].value is None


def test_st_migrou_de_g_para_flag_ac(tmp_path):
    """RETARGET do teste de 'item ST tinha markup menor em G'. O efeito da
    Substituição Tributária migrou do markup (fórmula em G) para a FLAG NATIVA
    da coluna AC: item com ST recebe o texto 'SIM' (que a planilha compara em
    IF(AC="SIM",...)); item sem ST fica com AC VAZIO.

    Cobre os dois caminhos de detecção de ST do exportador:
    - por índice explícito (itens_com_st);
    - por NCM da seleção (tributacao.eh_st).
    """
    itens = [
        _item(descricao="ITEM ST POR INDICE", item_numero=1),
        _item(descricao="ITEM ST POR NCM", item_numero=2),
        _item(descricao="ITEM SEM ST", item_numero=3),
    ]
    # NCM 11010010 consta no Anexo 05 (ST); confirmado via tributacao.eh_st.
    assert tributacao.eh_st("11010010") is True
    selecoes = {
        1: SelecaoFornecedor(preco=5.0, codigo_fornecedor="X1", marca="M", ncm="11010010"),
    }
    destino = tmp_path / "saida.xlsx"
    exportar(itens, itens_com_st={0}, selecoes=selecoes, destino=destino)

    ws = load_workbook(destino).active
    # AC é a coluna 29. Linhas 3,4,5 = itens 1,2,3.
    assert ws["AC3"].value == "SIM", "item ST por índice não recebeu a flag em AC"
    assert ws["AC4"].value == "SIM", "item ST por NCM não recebeu a flag em AC"
    assert ws["AC5"].value is None, "item SEM ST não pode ter flag em AC"
    # G continua manual em todos — o ST não escreve preço, só a flag.
    for linha in (3, 4, 5):
        assert ws[f"G{linha}"].value is None
