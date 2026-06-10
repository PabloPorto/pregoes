# -*- coding: utf-8 -*-
"""Gera o PDF de apresentação do sistema (capacidades) para o cliente.

Documento client-facing, em português, fiel ao que está implementado.
Rodar: python scripts/gerar_apresentacao.py
"""
from __future__ import annotations

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    ListFlowable, ListItem, HRFlowable, KeepTogether,
)

SAIDA = "Apresentacao_Sistema_Pregoes.pdf"

# Paleta (mesma do app)
AZUL = colors.HexColor("#1E40AF")
AZUL_CLARO = colors.HexColor("#DBEAFE")
CINZA = colors.HexColor("#475569")
CINZA_CLARO = colors.HexColor("#F1F5F9")
VERDE = colors.HexColor("#15803D")
TEXTO = colors.HexColor("#0F172A")

styles = getSampleStyleSheet()
S = {
    "h1": ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                         fontSize=17, textColor=AZUL, spaceBefore=14, spaceAfter=8,
                         leading=21),
    "h2": ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                         fontSize=12.5, textColor=TEXTO, spaceBefore=10, spaceAfter=4,
                         leading=16),
    "body": ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica",
                           fontSize=10.3, textColor=TEXTO, alignment=TA_JUSTIFY,
                           leading=15, spaceAfter=6),
    "bullet": ParagraphStyle("bullet", parent=styles["BodyText"], fontName="Helvetica",
                             fontSize=10.3, textColor=TEXTO, leading=14.5, spaceAfter=3),
    "caption": ParagraphStyle("caption", parent=styles["BodyText"], fontName="Helvetica-Oblique",
                              fontSize=9, textColor=CINZA, leading=12, spaceAfter=6),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=9.3, textColor=TEXTO, leading=12.5),
    "cellb": ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=9.3, textColor=TEXTO, leading=12.5),
    "cellh": ParagraphStyle("cellh", fontName="Helvetica-Bold", fontSize=9.3, textColor=colors.white, leading=12.5),
}


def P(txt, st="body"):
    return Paragraph(txt, S[st])


def bullets(items, st="bullet"):
    return ListFlowable(
        [ListItem(Paragraph(t, S[st]), leftIndent=10, value="•") for t in items],
        bulletType="bullet", start="•", leftIndent=12, bulletColor=AZUL,
        spaceBefore=2, spaceAfter=6,
    )


def tabela(linhas, larguras, cabecalho=True):
    dados = []
    for i, lin in enumerate(linhas):
        est = "cellh" if (cabecalho and i == 0) else None
        dados.append([Paragraph(c, S[est] if est else S["cellb" if c2 == 0 and not (cabecalho and i == 0) else "cell"])
                      for c2, c in enumerate(lin)])
    t = Table(dados, colWidths=larguras, repeatRows=1 if cabecalho else 0)
    estilo = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
        ("ROWBACKGROUNDS", (0, 1 if cabecalho else 0), (-1, -1), [colors.white, CINZA_CLARO]),
    ]
    if cabecalho:
        estilo += [("BACKGROUND", (0, 0), (-1, 0), AZUL),
                   ("LINEBELOW", (0, 0), (-1, 0), 0, colors.white)]
    t.setStyle(TableStyle(estilo))
    return t


def rodape(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#E2E8F0"))
    canvas.line(20 * mm, 15 * mm, 190 * mm, 15 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(CINZA)
    canvas.drawString(20 * mm, 10 * mm,
                      "Sistema de Automação de Pregões Eletrônicos — Documento de Capacidades")
    canvas.drawRightString(190 * mm, 10 * mm, "Página %d" % doc.page)
    canvas.restoreState()


def capa(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(AZUL)
    canvas.rect(0, 247 * mm, 210 * mm, 50 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(20 * mm, 270 * mm, "AUTOMAÇÃO DE LICITAÇÕES")
    canvas.setFillColor(AZUL)
    canvas.setFont("Helvetica-Bold", 25)
    canvas.drawString(20 * mm, 210 * mm, "Sistema de Automação de")
    canvas.drawString(20 * mm, 198 * mm, "Pregões Eletrônicos")
    canvas.setFillColor(CINZA)
    canvas.setFont("Helvetica", 13)
    canvas.drawString(20 * mm, 185 * mm, "Documento de Capacidades — Apresentação")
    canvas.setStrokeColor(AZUL)
    canvas.setLineWidth(2)
    canvas.line(20 * mm, 178 * mm, 70 * mm, 178 * mm)
    canvas.setFillColor(TEXTO)
    canvas.setFont("Helvetica-Oblique", 12)
    canvas.drawString(20 * mm, 165 * mm, "Do edital em PDF à planilha de preços pronta —")
    canvas.drawString(20 * mm, 157 * mm, "com inteligência que aprende com o seu uso.")
    canvas.setFillColor(CINZA)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(20 * mm, 30 * mm, "Versão do documento: junho/2026")
    canvas.drawString(20 * mm, 24 * mm, "Operação 100% local — material de construção, hidráulico e elétrico")
    canvas.restoreState()


def build():
    doc = SimpleDocTemplate(
        SAIDA, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=22 * mm, bottomMargin=22 * mm,
        title="Sistema de Automação de Pregões — Capacidades",
        author="Equipe de desenvolvimento",
    )
    L = 170 * mm
    story = []

    # ---- CAPA (página 1, desenhada em capa()) ----
    story.append(Spacer(1, 220 * mm))
    story.append(PageBreak())

    # ---- 1. VISÃO GERAL ----
    story.append(P("1. Visão geral", "h1"))
    story.append(P(
        "Este sistema automatiza a preparação de pregões eletrônicos públicos, da leitura "
        "do edital em PDF até a planilha de formação de preços pronta para o pregão. "
        "Ele foi construído sobre três princípios: <b>simplicidade</b> (poucos cliques), "
        "<b>operação 100% local</b> (roda na máquina, sem depender de servidores) e "
        "<b>o operador sempre decide</b> — o sistema sugere, organiza e acelera, mas nunca "
        "escolhe a marca por você."))
    story.append(P("A dor que ele resolve", "h2"))
    story.append(P(
        "Preparar cada pregão era um trabalho manual e demorado: transcrever os mais de 500 itens "
        "do edital para o Excel, pesquisar o preço de cada item nos sites dos distribuidores, estimar "
        "impostos (muitas vezes errando para mais) e só então aplicar a margem — tudo sob a pressão do "
        "pregão, em que o pregoeiro pede planilhas em minutos. O sistema comprime esse processo de "
        "horas para uma fração do tempo, mantendo a sua planilha de cálculo (a MATRIZ) intacta."))
    story.append(HRFlowable(width="100%", color=AZUL_CLARO, thickness=1, spaceBefore=4, spaceAfter=8))

    # ---- 2. FLUXO ----
    story.append(P("2. Como funciona — o fluxo de ponta a ponta", "h1"))
    story.append(P(
        "Cada pregão percorre cinco etapas. O sistema faz o trabalho pesado em cada uma; "
        "você revisa e decide nos pontos que importam."))
    story.append(tabela([
        ["Etapa", "O que o sistema faz", "O seu papel"],
        ["1. Leitura do edital", "Lê o PDF e transcreve os itens automaticamente para a planilha.",
         "Confere e corrige o que precisar."],
        ["2. Revisão", "Mostra os itens lidos com um semáforo de confiança.",
         "Ajusta descrições e marca o que entra."],
        ["3. Busca de preços", "Procura cada item nos dois distribuidores parceiros.",
         "Escolhe a marca e o preço entre as opções."],
        ["4. Impostos e margem", "Aplica o markup correto por item (com ou sem Substituição Tributária).",
         "Define o markup do pregão."],
        ["5. Planilha pronta", "Preenche a sua planilha MATRIZ preservando todas as fórmulas.",
         "Baixa e entrega."],
    ], [38 * mm, 78 * mm, 54 * mm]))
    story.append(P(
        "A planilha MATRIZ é tratada como sagrada: o sistema apenas <b>preenche</b> as colunas de "
        "entrada (descrição, unidade, quantidade, preço, código, marca) e mantém intactas as 16 "
        "colunas de fórmulas que você já usa.", "caption"))

    # ---- 3. CAPACIDADES ----
    story.append(PageBreak())
    story.append(P("3. Capacidades detalhadas", "h1"))

    story.append(P("3.1 Leitura automática do edital", "h2"))
    story.append(P(
        "O sistema lê o PDF do edital e extrai a tabela de itens. Quando o edital tem um layout "
        "simples, a leitura é instantânea e offline. Quando o layout é complexo (colunas "
        "embaralhadas, tabelas que mudam de página), ele aciona a <b>inteligência artificial</b> "
        "para entender o documento — e, se ainda assim restar dúvida, oferece um mapeamento manual "
        "guiado, no qual você indica qual é a tabela e o que é cada coluna (e o sistema aprende esse "
        "formato para os próximos editais iguais)."))

    story.append(P("3.2 Revisão assistida", "h2"))
    story.append(P(
        "Cada item lido recebe um indicador de confiança (verde, amarelo, vermelho). Você revisa "
        "numa tela de edição, corrige o que for preciso e marca quais itens entram na planilha. "
        "Nada é gerado sem a sua conferência."))

    story.append(P("3.3 Busca de preços nos fornecedores", "h2"))
    story.append(P(
        "Com um clique, o sistema procura cada item nos dois distribuidores parceiros e traz, por "
        "item, as opções de marca e preço encontradas. Ele <b>nunca decide a marca</b>: apresenta as "
        "opções (com destaque para o melhor casamento e para o mais barato) e você escolhe. O motor "
        "de busca é descrito em detalhe na Seção 4."))

    story.append(P("3.4 Reconhecimento de produtos que você já tem", "h2"))
    story.append(P(
        "O sistema mantém um catálogo dos produtos que você já comprou (importado das suas planilhas "
        "históricas). Quando um resultado da busca tem o <b>mesmo código</b> de um produto que você já "
        "tem, ele marca com um selo <b>“no seu catálogo”</b>, sobe essa opção para o topo e a "
        "pré-seleciona — garantindo que a planilha saia com o mesmo código das suas tabelas internas."))

    story.append(P("3.5 Busca manual no seu catálogo", "h2"))
    story.append(P(
        "Quando a busca automática não satisfaz, você abre a busca no seu próprio catálogo e procura "
        "por <b>palavras</b> (ex.: “tubo pvc 75”) ou pelo <b>código</b> do fornecedor. O sistema acha o "
        "produto que você já comprou, mostra a última cotação conhecida e o adiciona como opção — uma "
        "rede de segurança que não depende de acertar a busca automática."))

    story.append(P("3.6 Precificação tributária (markup por Substituição Tributária)", "h2"))
    story.append(P(
        "O imposto deixa de ser estimativa. O sistema verifica o NCM de cada produto: se ele consta na "
        "lista de Substituição Tributária do estado (Anexo 05 do RICMS/PB), aplica um markup menor "
        "(padrão 1,30, porque o imposto já foi pago na origem); caso contrário, aplica o markup normal "
        "(padrão 1,45). O markup é <b>por item</b> e editável a cada pregão — precificação mais "
        "agressiva onde dá, e segura onde precisa."))

    story.append(P("3.7 Geração da planilha MATRIZ", "h2"))
    story.append(P(
        "O sistema preenche a sua planilha MATRIZ preservando todas as fórmulas, e entrega o arquivo "
        "Excel pronto para download. A escrita é feita de forma segura (o arquivo final só aparece "
        "quando está completo)."))

    story.append(P("3.8 Histórico e segunda via", "h2"))
    story.append(P(
        "Toda planilha gerada fica registrada num histórico de pregões. Você pode voltar a qualquer "
        "pregão processado e <b>baixar de novo</b> a planilha — com o mesmo conteúdo validado. Reenviar "
        "o mesmo pregão atualiza o registro, sem duplicar."))

    # ---- 4. O MOTOR DE BUSCA ----
    story.append(HRFlowable(width="100%", color=AZUL_CLARO, thickness=1, spaceBefore=12, spaceAfter=2))
    story.append(P("4. Como são feitas as buscas — o motor inteligente", "h1"))
    story.append(P(
        "A busca de preços não é uma simples pesquisa por texto. Ela é uma cascata de cinco camadas, "
        "da mais barata e precisa à mais ampla. O sistema <b>para na primeira camada que resolve</b> — "
        "economizando tempo e custo."))
    story.append(tabela([
        ["Camada", "Como funciona", "Para que serve"],
        ["Memória (cache)", "Reusa preços buscados nos últimos 2 dias.", "Resposta instantânea no que já foi visto."],
        ["Aprendizado", "Reusa a sua escolha anterior para descrições iguais OU parecidas.", "O sistema fica mais certeiro a cada uso (Seção 5)."],
        ["Catálogo por código", "Casa a descrição com o seu histórico e busca pelo código exato.", "Produto certo, sem disputa de texto."],
        ["Busca textual", "Monta uma consulta com as palavras distintivas e ordena por relevância.", "Encontra o produto pelo nome quando não há código."],
        ["Juiz de IA", "A IA confere os candidatos ambíguos e descarta os falsos.", "Resolve sinônimos e nomes sutis (ex.: fita-rotulador ≠ fita-crepe)."],
    ], [34 * mm, 80 * mm, 56 * mm]))

    story.append(P("Inteligência de medida", "h2"))
    story.append(P(
        "O motor entende as medidas que distinguem um produto de outro: <b>diâmetro</b> (75mm, "
        "“DN 75”, “diâmetro 75”), <b>polegada</b> (7/8, 1.1/2), bitola de cabo, tensão, amperagem, etc. "
        "Assim, um pedido de “tubo diâmetro 75” não casa com um tubo de 25mm, e o diâmetro pedido entra "
        "na própria consulta ao site para trazer o produto certo."))

    story.append(P("Mais opções, decisão sempre sua", "h2"))
    story.append(P(
        "O motor remove resultados repetidos para você ver mais marcas distintas, destaca o melhor "
        "casamento e o mais barato, mas a escolha final é sempre do operador. <b>O sistema nunca "
        "decide a marca</b> — ele apresenta as opções organizadas."))

    # ---- 5. RETROALIMENTAÇÃO ----
    story.append(HRFlowable(width="100%", color=AZUL_CLARO, thickness=1, spaceBefore=12, spaceAfter=2))
    story.append(P("5. Como o sistema se retroalimenta (aprende com você)", "h1"))
    story.append(P(
        "Esta é uma das características mais valiosas do sistema: ele fica mais inteligente a cada uso, "
        "sem nenhuma programação adicional."))
    story.append(P(
        "<b>O ciclo é simples:</b> toda vez que você escolhe um produto para um item e gera a planilha, "
        "o sistema guarda essa decisão — qual produto (marca e código) você escolheu para aquela "
        "descrição de edital. Esse aprendizado fica salvo na máquina."))
    story.append(P(
        "Na próxima vez que um item igual <b>ou parecido</b> aparecer, o sistema reconhece e vai direto "
        "no produto que você já havia escolhido, pulando a busca. Para nomes parecidos, ele ainda "
        "confere se a <b>medida bate</b> — assim nunca reusa uma “válvula 7/8” no lugar de uma “válvula "
        "1.1/4”."))
    story.append(KeepTogether([
        Spacer(1, 2),
        tabela([
            ["Você escolhe", "O sistema guarda", "Da próxima vez"],
            ["Marca e preço de um item.",
             "“Para esta descrição, esta foi a escolha.”",
             "Reusa direto em nomes iguais ou parecidos (com a mesma medida)."],
        ], [54 * mm, 56 * mm, 60 * mm]),
    ]))
    story.append(P(
        "Resultado prático: quanto mais você usa, menos buscas o sistema precisa fazer e mais certeiras "
        "ficam as sugestões. É um ativo que cresce com o seu trabalho — e que se transfere com você ao "
        "instalar o sistema em outra máquina.", "body"))

    # ---- 6. TECNOLOGIA ----
    story.append(HRFlowable(width="100%", color=AZUL_CLARO, thickness=1, spaceBefore=12, spaceAfter=2))
    story.append(P("6. Tecnologia e uso consciente de recursos", "h1"))
    story.append(P(
        "O sistema foi desenhado para ser robusto e barato de operar. Em linguagem acessível:"))
    story.append(bullets([
        "<b>Roda na sua máquina (local).</b> Não depende de servidor, internet permanente ou "
        "mensalidade de nuvem. Você abre como um programa comum (instalador de dois cliques).",
        "<b>Banco de dados em um único arquivo.</b> Todo o conhecimento do sistema — catálogo, "
        "aprendizado, histórico de preços — vive num arquivo só, fácil de levar e de fazer backup "
        "(o backup é automático).",
        "<b>Interface no navegador, local.</b> A tela abre no seu navegador, mas tudo roda na própria "
        "máquina — nada é publicado na internet.",
        "<b>Busca nos sites automatizada.</b> O sistema navega nos sites dos distribuidores parceiros "
        "como um usuário faria, respeitando o ritmo dos sites.",
        "<b>Inteligência artificial usada de forma reduzida e pontual.</b> A IA (do Google) só é "
        "acionada quando realmente agrega — em editais complexos, na padronização do catálogo e para "
        "desempatar buscas ambíguas. O resultado é mantido em memória para não repetir custo. Por isso "
        "o gasto fica em torno de poucos reais por mês, mesmo no volume alvo.",
    ]))
    story.append(P(
        "Essa escolha — IA pontual, processamento local, banco em arquivo — mantém o custo de operação "
        "mínimo e a sua autonomia máxima.", "caption"))

    # ---- 7. SEGURANÇA E OPERAÇÃO ----
    story.append(P("7. Segurança, dados e operação", "h1"))
    story.append(bullets([
        "<b>Seus dados não saem da máquina</b> sem autorização. O sistema é de operação local por "
        "princípio de confiança.",
        "<b>Backup automático</b> do banco de dados, com cópias preservadas antes de qualquer "
        "atualização sensível.",
        "<b>Suas credenciais</b> dos distribuidores e a chave da IA ficam guardadas localmente e são "
        "preenchidas por você na tela de Configurações.",
        "<b>Dois operadores</b> compartilham o mesmo acesso e dividem o volume, sem hierarquia.",
        "<b>Atualizações simples:</b> recebe-se uma versão nova do programa; o conhecimento "
        "acumulado (catálogo, aprendizado, histórico) é preservado.",
    ]))

    # ---- 8. CONFIABILIDADE ----
    story.append(P("8. Confiabilidade", "h1"))
    story.append(P(
        "Em edital real da especialidade (ferragens/hidráulico/elétrico), a busca resolveu cerca de "
        "<b>9 em cada 10 itens</b> sem intervenção, com índice mínimo de erro. Um princípio de "
        "segurança guia o sistema: <b>quando não encontra o produto certo, ele diz “não encontrado” "
        "em vez de inventar</b> — e oferece a busca no seu catálogo como alternativa. A precificação "
        "tributária por Substituição também é conservadora: aplica o desconto de imposto apenas onde a "
        "lista oficial do estado confirma."))

    # ---- 9. RESUMO ----
    story.append(HRFlowable(width="100%", color=AZUL_CLARO, thickness=1, spaceBefore=6, spaceAfter=8))
    story.append(P("Em resumo — o que o sistema entrega", "h1"))
    story.append(bullets([
        "<b>Tempo:</b> de horas de trabalho manual para minutos por pregão.",
        "<b>Consistência:</b> a sua planilha MATRIZ preenchida sem erro de digitação, com fórmulas "
        "intactas e os códigos batendo com as suas tabelas internas.",
        "<b>Precificação melhor:</b> impostos por Substituição Tributária aplicados por item, sem "
        "estimativa para mais.",
        "<b>Inteligência que cresce:</b> o sistema aprende com cada escolha sua e fica mais certeiro a "
        "cada uso.",
        "<b>Autonomia e custo baixo:</b> roda local, sem mensalidade de nuvem, com IA usada só onde "
        "agrega.",
    ]))
    story.append(Spacer(1, 6))
    story.append(P(
        "Construído em fases, com testes a cada passo, para substituir com segurança o trabalho manual "
        "de preparação de pregões — sempre simples, reutilizável e sem suposições.", "caption"))

    doc.build(story, onFirstPage=capa, onLaterPages=rodape)
    print("PDF gerado:", SAIDA)


if __name__ == "__main__":
    build()
