"""
Gera um PDF textual de teste replicando a estrutura do edital de Araruna
que vimos no anexo. Usado apenas para testes do extrator enquanto não
temos um PDF textual real do cliente.
"""
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

# Amostra real do edital Araruna 06/2026 (subset dos ~485 itens originais)
ITENS = [
    (1, "ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND", "PACOTE", 40, 16.40),
    (2, "ABRAÇADEIRA DE NYLON 300 X 4.8 C/100UND", "PACOTE", 40, 9.91),
    (3, "ABRAÇADEIRA DE NYLON 400 X 4.8 C/100UND", "PACOTE", 40, 17.94),
    (4, "ABRACADEIRA METÁLICA 1", "UNIDADE", 25, 1.43),
    (5, "ABRAÇADEIRA PVC 20MM", "UNIDADE", 25, 1.99),
    (6, "ABRAÇADEIRA PVC 25MM", "UNIDADE", 25, 1.56),
    (7, "ABRAÇADEIRA SEM FIM 1/2 X 3/4", "UNIDADE", 20, 8.61),
    (8, "ÁCIDO MURIÁTICO", "UNIDADE", 25, 4.46),
    (9, "ACO CA-50, 10,0 MM, VERGALHAO C/ 12M SUPERFÍCIE NERVURADA", "UNIDADE", 100, 52.00),
    (10, "ACO CA-50, 12,5 MM, VERGALHAO C/ 12M SUPERFÍCIE NERVURADA", "UNIDADE", 35, 82.50),
    (11, "ACO CA-50, 6,3 MM, VERGALHAO C/ 12M SUPERFÍCIE NERVURADA", "UNIDADE", 50, 19.99),
    (12, "ACO CA-50, 8,0 MM, VERGALHAO C/ 12M SUPERFÍCIE NERVURADA", "UNIDADE", 100, 38.00),
    (13, "ACO CA-60, 5,0 MM, VERGALHAO C/ 12M SUPERFÍCIE NERVURADA", "UNIDADE", 80, 14.40),
    (14, "ADAPTADOR FLANGE 20 MM", "UNIDADE", 25, 7.71),
    (15, "ADAPTADOR FLANGE 25 MM", "UNIDADE", 25, 11.84),
    (16, "ADAPTADOR FLANGE 32 MM", "UNIDADE", 30, 14.90),
    (17, "ADESIVO PVA COLA BRANCA 1KG", "UNIDADE", 25, 13.10),
    (18, "ADESIVO PVC 175G", "UNIDADE", 40, 12.22),
    (19, "ALICATE 8\".", "UNIDADE", 10, 20.51),
    (20, "ARAME GALVANIZADO 14 BWG", "QUILOGRAMA", 20, 24.00),
    (21, "ARAME GALVANIZADO 16 BWG", "QUILOGRAMA", 20, 31.55),
    (22, "AREIA FINA", "METROS", 125, 59.09),
    (23, "CABO MULTIPLEX 10MM", "METROS", 500, 8.50),
    (24, "CIMENTO COM 50KG", "SACOS", 750, 40.70),
    (25, "PORTA DE MADEIRA, FOLHA LEVE (PRENSADA) (NBR 15930) DE 80 X 210 CM", "UNIDADE", 30, 130.00),
]


def gerar(destino: Path):
    doc = SimpleDocTemplate(str(destino), pagesize=A4,
                            topMargin=40, bottomMargin=40,
                            leftMargin=30, rightMargin=30)
    styles = getSampleStyleSheet()
    h_style = ParagraphStyle('h', parent=styles['Normal'],
                             fontName='Helvetica-Bold', fontSize=10,
                             alignment=1, spaceAfter=10)

    story = []
    story.append(Paragraph("ESTADO DA PARAÍBA — PREFEITURA MUNICIPAL DE ARARUNA", h_style))
    story.append(Paragraph("ANEXO I - PREGÃO ELETRÔNICO Nº 00006/2026 (TESTE SINTÉTICO)", h_style))
    story.append(Paragraph("TERMO DE REFERÊNCIA - ESPECIFICAÇÕES", h_style))
    story.append(Spacer(1, 12))

    cabecalho = ["CÓDIGO", "DISCRIMINAÇÃO", "UNIDADE", "QUANTIDADE", "PREÇO UNIT.", "PREÇO TOTAL"]
    rows = [cabecalho]
    for cod, disc, un, q, vu in ITENS:
        rows.append([str(cod), disc, un, str(q), f"{vu:.2f}", f"{q*vu:.2f}"])

    t = Table(rows, colWidths=[40, 250, 60, 55, 55, 65], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
        ("ALIGN", (3, 0), (5, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t)
    doc.build(story)


if __name__ == "__main__":
    destino = Path(__file__).parent.parent / "data" / "editais" / "araruna_teste_sintetico.pdf"
    gerar(destino)
    print(f"Gerado: {destino}")
