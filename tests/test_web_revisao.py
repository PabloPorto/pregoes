"""Interface web — Revisão dos itens e Mapeamento manual.

Cobre a paridade com o Streamlit (inventário 2.3, 2.4, 6.2, 6.12) e o que a
tela nova faz a mais: a grade grava direto em `itens_edital` (reabrir o pregão
traz edições e marcações), incluir/excluir linhas e a REGRA DOS ÍNDICES (mexer
nos itens marcados depois de buscar só com confirmação, e descarta a busca).
Sem rede: nada aqui chama fornecedor, IA nem Docling de verdade.
"""
from __future__ import annotations

import re
import threading
import time

import pytest

from pregoes import db, mapeamento_manual
from pregoes.exportador import SelecaoFornecedor
from pregoes.extrator import ItemExtraido, ResultadoExtracao, _para_numero
from pregoes.web import regras_revisao as regras
from pregoes.web import rotas_revisao, tarefas
from pregoes.web.pregao import Pregao

_RE_LINHA = re.compile(
    r'<tr class="rev-linha(?P<classe>[^"]*)" data-i="(?P<i>\d+)"(?P<attrs>[^>]*)>(?P<corpo>.*?)</tr>',
    re.S)


def _grade(html: str) -> list[dict]:
    """As linhas da grade como o operador as vê."""
    def campo(corpo: str, nome: str) -> str:
        m = re.search(rf'name="{nome}" value="([^"]*)"', corpo)
        return m.group(1) if m else ""

    linhas = []
    for m in _RE_LINHA.finditer(html):
        corpo = m.group("corpo")
        linhas.append({
            "i": int(m.group("i")),
            "duvidoso": "duvidoso" in m.group("classe"),
            "nova": "data-nova" in m.group("attrs"),
            "marcado": re.search(r'name="marcado" checked', corpo) is not None,
            "conferir": "rev-conferir" in corpo,
            "item": campo(corpo, "item_numero"),
            "descricao": re.search(r"<textarea[^>]*>(.*?)</textarea>", corpo, re.S).group(1),
            "unidade": campo(corpo, "unidade"),
            "quantidade": campo(corpo, "quantidade"),
            "referencia": campo(corpo, "valor_referencia"),
        })
    return linhas


def _salvar(web, p, **dados):
    return web.post(f"/pregao/{p.slug}/revisao/item", json=dados)


def _com_busca(p: Pregao) -> Pregao:
    """Deixa o pregão como depois de uma busca: resultados e uma escolha."""
    p.estado["resultados_busca"] = {i: [] for i in range(len(p.itens_revisados))}
    p.estado["selecoes"] = {0: SelecaoFornecedor(preco=9.9, codigo_fornecedor="A1", marca="TIGRE")}
    p.estado["itens_nao_usar"] = {1}
    p.estado["markup_overrides"] = {0: 1.3}
    p.salvar()
    return p


def _tem_busca(slug: str) -> bool:
    p = Pregao.abrir(slug)
    return p.tem_busca and bool(p.estado.get("selecoes"))


def _esperar(tipo: str, slug: str) -> None:
    for _ in range(200):
        if not tarefas.ativa(tipo, slug):
            return
        time.sleep(0.02)
    raise AssertionError(f"a tarefa {tipo} não terminou")


# --------------------------------------------------------------------------
# A tela
# --------------------------------------------------------------------------

def test_tela_mostra_todos_os_itens_e_os_rotulos(web):
    p = web.pregao(n_itens=5)
    r = web.get(f"/pregao/{p.slug}/revisao")
    assert r.status_code == 200
    linhas = _grade(r.text)
    assert [l["descricao"] for l in linhas] == [f"PRODUTO DE TESTE {n}" for n in range(1, 6)]
    assert [l["item"] for l in linhas] == ["1", "2", "3", "4", "5"]
    assert linhas[1]["quantidade"] == "20" and linhas[1]["referencia"] == "6,00"
    # o duvidoso nasce desmarcado, destacado e com o selo "conferir"
    assert linhas[0]["duvidoso"] and linhas[0]["conferir"] and not linhas[0]["marcado"]
    assert all(l["marcado"] and not l["duvidoso"] and not l["conferir"] for l in linhas[1:])
    for texto in ("Revisão dos itens extraídos",
                  "Confira o que foi lido do PDF, edite o que precisar e marque o que entra na planilha.",
                  "Órgão", "Número do pregão", "Buscar preço?", "Descrição", "Unidade", "Quant",
                  "Ref. (R$)", "Marcar todos", "Desmarcar todos", "Adicionar item",
                  "Gerar sem buscar", "Buscar preços", "Total de itens", "Duvidosos",
                  "Tabelas detectadas", "Só duvidosos", "Só desmarcados",
                  "Linhas em amarelo precisam de atenção.",
                  "<b>Alta confiança · 100%</b> — pode seguir para precificar."):
        assert texto in r.text, texto
    assert ('<b class="num">4</b> de <b class="num">5</b> itens marcados para <b>buscar '
            'preço</b> · todos os <b class="num">5</b> vão para a planilha (os demais sem '
            'preço).') in r.text
    assert f'href="/pregao/{p.slug}/planilha?sem_precos=1"' in r.text
    assert 'value="Prefeitura de Teste"' in r.text and 'value="10/2026"' in r.text
    # confiança alta: não oferece o mapeamento manual nem fala de busca feita
    assert "Mapeamento Manual" not in r.text
    assert "já tem preços buscados" not in r.text


def test_pdf_ainda_em_leitura_manda_para_a_leitura(web):
    p = Pregao.criar("Prefeitura", "1/2026", str(web.dados / "editais" / "x.pdf"))
    r = web.get(f"/pregao/{p.slug}/revisao", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"/pregao/{p.slug}/leitura")


def test_primeira_abertura_grava_as_listas_da_revisao(web):
    """Rascunho que só tem a extração (acabou de ser lido): abrir a revisão
    deriva e GRAVA itens_edital / mapa / itens_revisados."""
    p = web.pregao(n_itens=3)
    for chave in ("itens_edital", "itens_revisados", "mapa_busca_edital"):
        p.estado.pop(chave)
    p.salvar()
    assert web.get(f"/pregao/{p.slug}/revisao").status_code == 200
    p = Pregao.abrir(p.slug)
    assert len(p.itens_edital) == 3 and p.mapa_busca_edital == [1, 2]
    assert [i.item_numero for i in p.itens_revisados] == [2, 3]


@pytest.mark.parametrize("conf", [1.0, 0.95, 0.949, 0.80, 0.5, 0.001, 0.0])
def test_selo_de_confianca_igual_ao_do_streamlit(conf):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from pregoes.ui import style
    rotulo, tipo, complemento = regras.selo_confianca(conf)
    rotulo_antigo, tipo_antigo = style.confianca_para_badge(conf)
    assert rotulo == rotulo_antigo
    assert tipo == {"success": "ok", "warning": "alerta", "danger": "erro"}[tipo_antigo]
    assert complemento == {"ok": "pode seguir para precificar.",
                           "alerta": "revise com atenção antes de gerar a planilha.",
                           "erro": "recomendado usar o mapeamento manual."}[tipo]


def test_confianca_baixa_mostra_avisos_e_oferece_mapeamento(web):
    p = web.pregao(n_itens=4)
    p.extracao.confianca_global = 0.62
    p.extracao.avisos = ["✨ Mapa aprendido reusado (tabela #1 e continuações, cabeçalho conhecido).",
                         "Linha <estranha> na página 3."]
    p.salvar()
    html = web.get(f"/pregao/{p.slug}/revisao").text
    assert "<b>Baixa confiança · 62%</b> — recomendado usar o mapeamento manual." in html
    assert "Mapa aprendido reusado (tabela #1 e continuações, cabeçalho conhecido)." in html
    assert "✨" not in html                               # sem emoji
    assert "Linha &lt;estranha&gt; na página 3." in html   # aviso do PDF sai escapado
    assert ("A confiança automática ficou abaixo de 95%. Você pode usar o <b>Mapeamento "
            "Manual</b> para indicar qual é a tabela de itens e o que é cada coluna. O sistema "
            "aprende e reaproveita o mapeamento em próximos editais com o mesmo formato.") in html
    assert f'href="/pregao/{p.slug}/mapeamento"' in html
    # 0,949 arredonda para "95%" mas ainda é confiança média e oferece o mapeamento
    p.extracao.confianca_global = 0.949
    p.salvar()
    html = web.get(f"/pregao/{p.slug}/revisao").text
    assert "<b>Confiança média · 95%</b> — revise com atenção antes de gerar a planilha." in html
    assert "Mapeamento Manual" in html
    # sem tabelas brutas não há o que mapear
    p.extracao.tabelas_brutas = []
    p.salvar()
    assert "Mapeamento Manual" not in web.get(f"/pregao/{p.slug}/revisao").text


def test_sem_itens_distingue_pdf_escaneado_de_tabela_nao_encontrada(web, monkeypatch):
    res = ResultadoExtracao(arquivo="x.pdf")
    res.confianca_global = 0.0
    p = Pregao.criar("Prefeitura", "2/2026", str(web.dados / "editais" / "x.pdf"))
    p.estado["extracao"] = res
    p.salvar()

    monkeypatch.setattr(rotas_revisao.extrator, "tem_texto_extraivel", lambda caminho: False)
    html = web.get(f"/pregao/{p.slug}/revisao").text
    assert "<b>Sem itens detectados</b>" in html
    assert ("Este PDF parece ser <b>escaneado</b> (imagem), sem texto que o sistema consiga ler. "
            "Peça ao órgão o edital em PDF com texto (ou copie a tabela de itens para um "
            "PDF/planilha). Leitura de imagem (OCR) ainda não é suportada.") in html
    assert 'id="rev-grade"' not in html and "Buscar preços" not in html   # a tela para aqui

    monkeypatch.setattr(rotas_revisao.extrator, "tem_texto_extraivel", lambda caminho: True)
    html = web.get(f"/pregao/{p.slug}/revisao").text
    assert ("Nenhum item foi extraído automaticamente. Se o PDF tem uma tabela de itens "
            "visível, use o <b>Mapeamento Manual</b> acima.") in html
    assert "escaneado" not in html

    # dá para montar a lista à mão: a 1ª linha faz a grade aparecer
    r = web.post(f"/pregao/{p.slug}/revisao/adicionar", json={}).json()
    assert r["ok"] and r["recarregar"]
    html = web.get(f"/pregao/{p.slug}/revisao").text
    assert 'id="rev-grade"' in html and len(_grade(html)) == 1


# --------------------------------------------------------------------------
# Células: cada alteração é gravada na hora
# --------------------------------------------------------------------------

def test_celula_salva_inclusive_numero_no_formato_brasileiro(web):
    p = web.pregao(n_itens=3)
    itens = list(p.itens_edital)
    itens[1].codigo_orgao, itens[1].pagina = "COD-77", 4
    p.definir_itens(itens, p.mapa_busca_edital)
    p.salvar()

    r = _salvar(web, p, i=1, n=3, quantidade="1.500,50").json()
    assert r["ok"] and r["valor"] == "1.500,5" and r["duvidoso"] is False
    assert "Salvo às" in r["html"]["#rev-contador"]
    assert _salvar(web, p, i=1, n=3, valor_referencia="R$ 12,30").json()["valor"] == "12,30"
    assert _salvar(web, p, i=1, n=3, descricao="  TUBO   PVC\n 25MM ").json()["valor"] == "TUBO PVC 25MM"
    assert _salvar(web, p, i=1, n=3, unidade=" CX ").json()["valor"] == "CX"
    assert _salvar(web, p, i=1, n=3, item_numero="7").json()["valor"] == "7"

    p = Pregao.abrir(p.slug)
    item = p.itens_edital[1]
    assert (item.item_numero, item.descricao, item.unidade, item.quantidade,
            item.valor_referencia) == (7, "TUBO PVC 25MM", "CX", 1500.5, 12.3)
    # o que o Streamlit descartava ao editar continua no item
    assert (item.codigo_orgao, item.pagina) == ("COD-77", 4)
    # a lista de busca enxerga a edição; a extração (o que foi LIDO) fica como veio
    assert p.itens_revisados[0].descricao == "TUBO PVC 25MM"
    assert p.extracao.itens[1].descricao == "PRODUTO DE TESTE 2"
    assert p.mapa_busca_edital == [1, 2]

    # ponto de milhar sem vírgula e vazio (= sem valor)
    _salvar(web, p, i=2, n=3, quantidade="15.000")
    _salvar(web, p, i=2, n=3, valor_referencia="")
    _salvar(web, p, i=2, n=3, unidade="")
    item = Pregao.abrir(p.slug).itens_edital[2]
    assert (item.quantidade, item.valor_referencia, item.unidade) == (15000.0, None, None)


def test_editar_item_duvidoso_tira_a_duvida_sem_marcar(web):
    p = web.pregao(n_itens=3)
    r = _salvar(web, p, i=0, n=3, descricao="TUBO CORRIGIDO").json()
    assert r["duvidoso"] is False
    assert "#rev-resumo" in r["html"]          # as métricas mudam (Duvidosos 1 → 0)
    p = Pregao.abrir(p.slug)
    assert p.itens_edital[0].confianca == "extraido"
    assert p.mapa_busca_edital == [1, 2]       # editar não marca o item para busca
    linha = _grade(web.get(f"/pregao/{p.slug}/revisao").text)[0]
    assert not linha["duvidoso"] and not linha["conferir"] and not linha["marcado"]


def test_celula_com_valor_invalido_e_recusada_sem_alterar(web):
    p = web.pregao(n_itens=3)
    for dados, trecho in (({"quantidade": "abc"}, "Quantidade"),
                          ({"valor_referencia": "dez reais"}, "Valor de referência"),
                          ({"quantidade": "-5"}, "negativo"),
                          ({"quantidade": "10 20"}, "Quantidade"),
                          ({"item_numero": "1,5"}, "inteiro"),
                          ({"item_numero": "doze"}, "Número do item")):
        r = _salvar(web, p, i=1, n=3, **dados)
        assert r.status_code == 400, dados
        assert trecho in r.json()["erro"] and "Traceback" not in r.json()["erro"]
    assert _salvar(web, p, i=1, n=3, confianca="extraido").status_code == 400   # campo que não é da grade
    assert _salvar(web, p, i=1, n=3, pagina=9).status_code == 400
    item = Pregao.abrir(p.slug).itens_edital[1]
    assert (item.item_numero, item.quantidade, item.valor_referencia, item.pagina) == (2, 20.0, 6.0, None)


def test_celula_de_pagina_desatualizada_nao_grava_na_linha_errada(web):
    p = web.pregao(n_itens=3)
    for dados in ({"i": 3, "n": 3}, {"i": -1, "n": 3}, {"i": "1", "n": 3}, {"n": 3},
                  {"i": 1, "n": 4}):          # a página achava que eram 4 linhas
        r = _salvar(web, p, descricao="X", **dados)
        assert r.status_code == 409 and "Recarregue a página" in r.json()["erro"]
    assert [i.descricao for i in Pregao.abrir(p.slug).itens_edital] == [
        "PRODUTO DE TESTE 1", "PRODUTO DE TESTE 2", "PRODUTO DE TESTE 3"]
    assert _salvar(web, Pregao("nao-existe", {}), i=0, descricao="X").status_code == 404


def test_marcar_e_desmarcar_um_item(web):
    p = web.pregao(n_itens=4)
    r = _salvar(web, p, i=2, n=4, marcado=False).json()
    assert '<b class="num">2</b> de <b class="num">4</b>' in r["html"]["#rev-contador"]
    p = Pregao.abrir(p.slug)
    assert p.mapa_busca_edital == [1, 3]
    assert [i.item_numero for i in p.itens_revisados] == [2, 4]
    assert len(p.itens_edital) == 4            # desmarcado continua no edital (vai para a planilha)

    # marcar o duvidoso = o operador conferiu: entra na busca e perde o destaque
    r = _salvar(web, p, i=0, n=4, marcado=True).json()
    assert r["duvidoso"] is False
    p = Pregao.abrir(p.slug)
    assert p.mapa_busca_edital == [0, 1, 3] and p.itens_edital[0].confianca == "extraido"


def test_marcar_todos_e_desmarcar_todos_preservam_as_edicoes(web):
    p = web.pregao(n_itens=4)
    _salvar(web, p, i=3, n=4, descricao="EDITADO ANTES")
    r = web.post(f"/pregao/{p.slug}/revisao/marcar-todos", json={"valor": False}).json()
    assert r["ok"] and set(r["html"]) == {"#rev-corpo", "#rev-contador", "#rev-resumo"}
    assert not any(l["marcado"] for l in _grade(r["html"]["#rev-corpo"]))
    p = Pregao.abrir(p.slug)
    assert p.mapa_busca_edital == [] and p.itens_revisados == [] and len(p.itens_edital) == 4

    r = web.post(f"/pregao/{p.slug}/revisao/marcar-todos", json={"valor": True}).json()
    linhas = _grade(r["html"]["#rev-corpo"])
    assert all(l["marcado"] for l in linhas) and linhas[3]["descricao"] == "EDITADO ANTES"
    assert linhas[0]["duvidoso"]               # marcar em massa não "confere" ninguém
    p = Pregao.abrir(p.slug)
    assert p.mapa_busca_edital == [0, 1, 2, 3]
    assert p.itens_edital[3].descricao == "EDITADO ANTES"


def test_adicionar_e_excluir_linha(web):
    p = web.pregao(n_itens=3)
    r = web.post(f"/pregao/{p.slug}/revisao/adicionar", json={}).json()
    linhas = _grade(r["html"]["#rev-corpo"])
    assert len(linhas) == 4 and 'data-n="4"' in r["html"]["#rev-corpo"]
    assert linhas[3]["nova"] and linhas[3]["marcado"] and linhas[3]["descricao"] == ""
    assert linhas[3]["item"] == "4"            # número sugerido: o seguinte ao maior
    assert not any(l["nova"] for l in linhas[:3])
    _salvar(web, p, i=3, n=4, descricao="LUVA PVC 25MM")
    p = Pregao.abrir(p.slug)
    assert p.mapa_busca_edital == [1, 2, 3] and p.itens_edital[3].descricao == "LUVA PVC 25MM"

    # excluir a linha 1 (marcada): as marcadas que vinham depois recuam uma posição
    r = web.post(f"/pregao/{p.slug}/revisao/excluir", json={"i": 1, "n": 4}).json()
    assert r["ok"] and r["aviso"] == "Linha excluída."
    assert [l["descricao"] for l in _grade(r["html"]["#rev-corpo"])] == [
        "PRODUTO DE TESTE 1", "PRODUTO DE TESTE 3", "LUVA PVC 25MM"]
    p = Pregao.abrir(p.slug)
    assert p.mapa_busca_edital == [1, 2]
    assert [i.descricao for i in p.itens_revisados] == ["PRODUTO DE TESTE 3", "LUVA PVC 25MM"]

    # excluir uma desmarcada (a duvidosa, linha 0) também reposiciona as demais
    web.post(f"/pregao/{p.slug}/revisao/excluir", json={"i": 0, "n": 3})
    p = Pregao.abrir(p.slug)
    assert p.mapa_busca_edital == [0, 1] and len(p.itens_edital) == 2
    # índice inexistente / página velha: recusa
    assert web.post(f"/pregao/{p.slug}/revisao/excluir", json={"i": 5, "n": 2}).status_code == 409
    assert web.post(f"/pregao/{p.slug}/revisao/excluir", json={"i": 0, "n": 3}).status_code == 409
    assert len(Pregao.abrir(p.slug).itens_edital) == 2
    # excluir a última linha troca o estado da tela (recarrega para o "sem itens")
    web.post(f"/pregao/{p.slug}/revisao/excluir", json={"i": 0, "n": 2})
    r = web.post(f"/pregao/{p.slug}/revisao/excluir", json={"i": 0, "n": 1}).json()
    assert r["recarregar"] and Pregao.abrir(p.slug).itens_edital == []
    html = web.get(f"/pregao/{p.slug}/revisao").text
    assert "Nenhum item na lista" in html and 'id="rev-grade"' not in html


# --------------------------------------------------------------------------
# Regra dos índices: pregão que já tem busca
# --------------------------------------------------------------------------

def test_tela_avisa_quando_o_pregao_ja_tem_precos(web):
    p = _com_busca(web.pregao(n_itens=4))
    html = web.get(f"/pregao/{p.slug}/revisao").text
    assert ("Este pregão já tem preços buscados. Alterar quais itens estão marcados (ou "
            "incluir/excluir linhas) descarta a busca feita — os preços encontrados continuam "
            "na memória por 2 dias, então buscar de novo é rápido.") in html
    assert "data-confirmar-busca=" in html and "&#34;confirmar&#34;: true" not in html
    assert html.count('"confirmar": true') == 3        # marcar todos, desmarcar todos, adicionar


@pytest.mark.parametrize("caminho, dados", [
    ("revisao/item", {"i": 1, "n": 4, "marcado": False}),
    ("revisao/item", {"i": 0, "n": 4, "marcado": True}),
    ("revisao/marcar-todos", {"valor": False}),
    ("revisao/marcar-todos", {"valor": True}),
    ("revisao/adicionar", {}),
    ("revisao/excluir", {"i": 2, "n": 4}),
])
def test_mudar_posicoes_com_busca_feita_exige_confirmacao(web, caminho, dados):
    p = _com_busca(web.pregao(n_itens=4))
    antes = (list(p.mapa_busca_edital), len(p.itens_edital))

    r = web.post(f"/pregao/{p.slug}/{caminho}", json=dados)
    assert r.status_code == 409 and "já tem preços buscados" in r.json()["erro"]
    p = Pregao.abrir(p.slug)
    assert _tem_busca(p.slug) and (list(p.mapa_busca_edital), len(p.itens_edital)) == antes
    assert p.estado["itens_nao_usar"] == {1} and p.estado["markup_overrides"] == {0: 1.3}

    # "confirmar" que não seja o booleano true não vale
    assert web.post(f"/pregao/{p.slug}/{caminho}",
                    json={**dados, "confirmar": "sim"}).status_code == 409

    r = web.post(f"/pregao/{p.slug}/{caminho}", json={**dados, "confirmar": True})
    assert r.status_code == 200 and r.json()["recarregar"]   # aviso e etapas mudam: recarrega
    p = Pregao.abrir(p.slug)
    assert not p.tem_busca
    for chave in ("resultados_busca", "selecoes", "itens_nao_usar", "markup_overrides"):
        assert not p.estado.get(chave), chave
    assert (list(p.mapa_busca_edital), len(p.itens_edital)) != antes
    assert p.tela_de_trabalho() == "revisao"
    assert "já tem preços buscados" not in web.get(f"/pregao/{p.slug}/revisao").text


def test_corrigir_celulas_com_busca_feita_nao_descarta_nada(web):
    p = _com_busca(web.pregao(n_itens=4))
    for dados in ({"descricao": "TUBO PVC 25MM"}, {"unidade": "CX"}, {"quantidade": "1.500,50"},
                  {"valor_referencia": "3,20"}, {"item_numero": "9"}):
        r = _salvar(web, p, i=1, n=4, **dados)
        assert r.status_code == 200 and "recarregar" not in r.json(), dados
    p = Pregao.abrir(p.slug)
    assert _tem_busca(p.slug) and p.mapa_busca_edital == [1, 2, 3]
    assert p.estado["selecoes"][0].marca == "TIGRE" and p.estado["itens_nao_usar"] == {1}
    # a posição 0 da busca continua sendo o MESMO item, agora corrigido
    assert p.itens_revisados[0] is p.itens_edital[1]
    assert (p.itens_revisados[0].descricao, p.itens_revisados[0].quantidade) == ("TUBO PVC 25MM", 1500.5)


def test_operacao_que_nao_muda_os_marcados_nao_pede_confirmacao(web):
    """"Marcar todos" com tudo já marcado, ou reenviar a caixa no estado em
    que já está: não desloca posição nenhuma — a busca fica."""
    p = web.pregao(n_itens=3)
    web.post(f"/pregao/{p.slug}/revisao/marcar-todos", json={"valor": True})
    p = _com_busca(Pregao.abrir(p.slug))
    assert web.post(f"/pregao/{p.slug}/revisao/marcar-todos", json={"valor": True}).status_code == 200
    assert _salvar(web, p, i=1, n=3, marcado=True).status_code == 200
    assert _tem_busca(p.slug)


def test_com_a_busca_rodando_nem_com_confirmacao(web):
    p = web.pregao(n_itens=3)
    solta = threading.Event()
    tarefas.iniciar("busca", p.slug, "Busca", lambda t: solta.wait(10))
    try:
        for caminho, dados in (("revisao/item", {"i": 1, "n": 3, "marcado": False}),
                               ("revisao/adicionar", {}), ("revisao/excluir", {"i": 1, "n": 3}),
                               ("revisao/marcar-todos", {"valor": False})):
            r = web.post(f"/pregao/{p.slug}/{caminho}", json={**dados, "confirmar": True})
            assert r.status_code == 409 and "em andamento" in r.json()["erro"], caminho
        assert _salvar(web, p, i=1, n=3, descricao="PODE CORRIGIR").status_code == 200
        assert "está <b>em andamento</b>" in web.get(f"/pregao/{p.slug}/revisao").text
    finally:
        solta.set()
        _esperar("busca", p.slug)
    p = Pregao.abrir(p.slug)
    assert p.mapa_busca_edital == [1, 2] and len(p.itens_edital) == 3


# --------------------------------------------------------------------------
# Identificação, "Buscar preços", reabrir
# --------------------------------------------------------------------------

def test_orgao_e_numero_editaveis(web):
    p = web.pregao()
    base = f"/pregao/{p.slug}/revisao/identificacao"
    assert web.post(base, json={"orgao": "  Prefeitura de   Araruna "}).json()["ok"]
    assert web.post(base, json={"numero": "00006/2026"}).json()["ok"]
    p = Pregao.abrir(p.slug)
    assert (p.orgao, p.numero) == ("Prefeitura de Araruna", "00006/2026")
    assert p.titulo == "Pregão 00006/2026 — Prefeitura de Araruna"
    html = web.get(f"/pregao/{p.slug}/revisao").text
    assert 'value="Prefeitura de Araruna"' in html and 'value="00006/2026"' in html
    # em branco é aceito, como no Streamlit (a geração da planilha é que barra), mas avisa
    r = web.post(base, json={"numero": ""}).json()
    assert r["ok"] and "em branco" in r["aviso"]
    assert Pregao.abrir(p.slug).numero == ""
    assert web.post(base, json={"outro": "x"}).status_code == 400
    assert web.post("/pregao/nao-existe/revisao/identificacao", json={"orgao": "x"}).status_code == 404


def test_buscar_precos_valida_marcados_e_fornecedores(web, monkeypatch):
    p = web.pregao(n_itens=3)
    url = f"/pregao/{p.slug}/revisao/buscar"

    # nenhum fornecedor configurado (pasta de dados vazia)
    r = web.post(url, json={})
    assert r.status_code == 400
    assert r.json()["erro"] == ("Nenhum fornecedor configurado. Acesse Configurações para "
                                "cadastrar Almeida e/ou Dantas.")
    assert "Nenhum fornecedor configurado." in web.get(f"/pregao/{p.slug}/revisao").text

    monkeypatch.setattr(rotas_revisao, "fornecedores_configurados", lambda: ["dantas"])
    assert web.post(url, json={}).json() == {"ok": True, "ir": f"/pregao/{p.slug}/precos"}
    assert "Nenhum fornecedor configurado." not in web.get(f"/pregao/{p.slug}/revisao").text

    # item marcado sem descrição: não há o que procurar
    web.post(f"/pregao/{p.slug}/revisao/adicionar", json={})
    r = web.post(url, json={})
    assert r.status_code == 400 and "linha 4" in r.json()["erro"] and "sem descrição" in r.json()["erro"]
    _salvar(web, p, i=3, n=4, marcado=False)
    assert web.post(url, json={}).json()["ok"]

    # nenhum item marcado (vem antes da checagem de fornecedor, como no Streamlit)
    web.post(f"/pregao/{p.slug}/revisao/marcar-todos", json={"valor": False})
    monkeypatch.setattr(rotas_revisao, "fornecedores_configurados", lambda: [])
    r = web.post(url, json={})
    assert r.status_code == 400
    assert r.json()["erro"] == "Nenhum item marcado para buscar preço — marque ao menos um."


def test_reabrir_o_pregao_mostra_edicoes_e_marcacoes_salvas(web):
    """O defeito do Streamlit (inventário 3.3): a grade era refeita da
    extração e as edições/marcações sumiam ao retomar o pregão."""
    p = web.pregao(n_itens=4)
    _salvar(web, p, i=1, n=4, descricao="CABO FLEXIVEL 2,5MM <AZUL>")
    _salvar(web, p, i=1, n=4, quantidade="1.500,50")
    _salvar(web, p, i=2, n=4, marcado=False)
    _salvar(web, p, i=0, n=4, marcado=True)
    web.post(f"/pregao/{p.slug}/revisao/adicionar", json={})
    _salvar(web, p, i=4, n=5, descricao="ITEM INCLUIDO A MAO")

    # "reabrir": entra pelo endereço do pregão, como quem clica em Continuar
    r = web.get(f"/pregao/{p.slug}")
    assert r.status_code == 200 and r.url.path.endswith("/revisao")
    linhas = _grade(r.text)
    assert [l["marcado"] for l in linhas] == [True, True, False, True, True]
    assert linhas[1]["descricao"] == "CABO FLEXIVEL 2,5MM &lt;AZUL&gt;"   # escapado na tela
    assert linhas[1]["quantidade"] == "1.500,5"
    assert linhas[4]["descricao"] == "ITEM INCLUIDO A MAO" and not linhas[4]["nova"]
    assert not any(l["duvidoso"] for l in linhas)
    assert '<b class="num">4</b> de <b class="num">5</b>' in r.text
    p = Pregao.abrir(p.slug)
    assert p.itens_edital[1].descricao == "CABO FLEXIVEL 2,5MM <AZUL>"
    assert p.mapa_busca_edital == [0, 1, 3, 4]
    assert [i is p.itens_edital[j] for i, j in zip(p.itens_revisados, p.mapa_busca_edital)] == [True] * 4


# --------------------------------------------------------------------------
# Regras puras
# --------------------------------------------------------------------------

@pytest.mark.parametrize("valor", [0.0, 0.125, 1.5, 10.0, 12.3, 999.99, 1500.5, 15000.0, 1500000.0, 1234567.8912])
def test_numero_mostrado_no_campo_volta_igual(valor):
    """O que a grade mostra é o que `_para_numero` lê de volta — reabrir e
    salvar de novo nunca muda um número."""
    assert _para_numero(regras.numero_para_campo(valor)) == valor
    assert _para_numero(regras.numero_para_campo(valor, casas_min=2)) == valor
    assert regras.numero_para_campo(None) == ""


def test_regras_puras_da_grade_e_do_mapeamento():
    assert regras.numero_para_campo(1500.5) == "1.500,5"
    assert regras.numero_para_campo(5.0, casas_min=2) == "5,00"
    assert regras.limpar_aviso("✨ Mapa aprendido reusado.") == "Mapa aprendido reusado."
    assert regras.limpar_aviso("⚠️ Atenção") == "Atenção"
    assert regras.limpar_aviso("3 linhas sem quantidade") == "3 linhas sem quantidade"
    assert regras.marcados_apos_excluir([0, 2, 3, 5], 2) == [0, 2, 4]
    original = ItemExtraido(item_numero=1, codigo_orgao="C1", descricao="A", pagina=2,
                            confianca="duvidoso")
    novo = regras.editar_item(original, "quantidade", "2,5")
    assert novo is not original and original.quantidade is None and original.confianca == "duvidoso"
    assert (novo.quantidade, novo.codigo_orgao, novo.pagina, novo.confianca) == (2.5, "C1", 2, "extraido")
    assert regras.item_em_branco([]).item_numero is None
    assert regras.item_em_branco([original, ItemExtraido(item_numero=41)]).item_numero == 42

    # rótulo das tabelas e mapa: iguais aos do Streamlit (app.py L3485-3495, L3559-3563)
    tabela = [["ITEM", "DESCRIÇÃO", "UNID", "QUANT"], ["1", "PRODUTO DE TESTE 1", "UND", "10"]]
    assert regras.rotulo_tabela(0, tabela) == (
        "Tabela #1 · 4 colunas · 2 linhas · ITEM | DESCRIÇÃO | UNID | QUANT")
    assert regras.rotulo_tabela(2, []) == "Tabela #3 (vazia)"
    assert regras.rotulo_tabela(0, [[]]) == "Tabela #1 (vazia)"     # lá isto estourava
    assert regras.previa_tabela([["a", None], ["b"], ["c", "d", "e"]]) == [
        ["a", "", ""], ["b", "", ""], ["c", "d", "e"]]
    assert regras.rotulos_colunas([["ITEM", "X" * 40], ["1", "2", "3"]]) == ["ITEM", "X" * 30, "Col 3"]
    mapa, repetidas = regras.montar_mapa(["descricao", "ignorar", "descricao", "quantidade"])
    assert mapa == {"descricao": 2, "quantidade": 3} and repetidas == [2]   # a última vence
    assert regras.obrigatorios_faltando({"unidade": 1}) == ["Descrição", "Quantidade"]
    assert regras.obrigatorios_faltando(mapa) == []
    assert regras.campos_iniciais(3, {"descricao": 1, "quantidade": 2}) == [
        "ignorar", "descricao", "quantidade"]
    assert regras.campos_iniciais(2, None) == ["ignorar", "ignorar"]


# --------------------------------------------------------------------------
# Mapeamento manual
# --------------------------------------------------------------------------

_TABELA = [["ITEM", "DESCRIÇÃO DO MATERIAL", "UNID", "QUANT", "VALOR"],
           ["1", "TUBO PVC SOLDAVEL 25MM BARRA 6M", "UND", "10", "5,50"],
           ["2", "JOELHO 90 GRAUS PVC 32MM", "UND", "1.500", "3,25"],
           ["3", "CIMENTO CP II 50KG", "SC", "40", "38,90"]]
_ESCOLHAS = {"tabela": "1", "col_0": "codigo_orgao", "col_1": "descricao", "col_2": "unidade",
             "col_3": "quantidade", "col_4": "valor_referencia"}


def _pregao_para_mapear(web, **kw) -> Pregao:
    p = web.pregao(n_itens=2, **kw)
    p.extracao.confianca_global = 0.5
    p.extracao.tabelas_brutas = [[["LOTE", "OBJETO"], ["1", "MATERIAL HIDRAULICO"]],
                                 [list(l) for l in _TABELA], []]
    p.salvar()
    return p


def test_tela_de_mapeamento_mostra_os_tres_passos(web):
    p = _pregao_para_mapear(web)
    r = web.get(f"/pregao/{p.slug}/mapeamento?tabela=1")
    assert r.status_code == 200
    for texto in ("Mapeamento manual da tabela de itens",
                  "Quando a extração automática não acerta, você indica qual é a tabela certa e o "
                  "que é cada coluna. O sistema aprende para os próximos editais.",
                  "Voltar para revisão",
                  "Passo 1 — Qual das tabelas é a de itens?", "Tabelas detectadas no PDF",
                  "Tabela #1 · 2 colunas · 2 linhas · LOTE | OBJETO",
                  "Tabela #2 · 5 colunas · 4 linhas · ITEM | DESCRIÇÃO DO MATERIAL | UNID | QUANT | VALOR",
                  "Tabela #3 (vazia)",
                  "<b>Pré-visualização da Tabela #2</b> (primeiras 5 linhas):",
                  "<th>Col 1</th>", "<th>Col 5</th>", "JOELHO 90 GRAUS PVC 32MM",
                  "Passo 2 — O que é cada coluna?",
                  "A primeira linha do PDF acima é o cabeçalho — não vai virar item. Para cada "
                  "coluna, escolha o campo correspondente.",
                  "Coluna 2 — <code>DESCRIÇÃO DO MATERIAL</code>",
                  "— ignorar —", "Valor de referência", "Código (item nº)",
                  "Passo 3 — Validar e aplicar",
                  "Mapeamento incompleto — faltam: <b>Descrição, Quantidade</b>.",
                  "Salvar este mapeamento para reuso automático em próximos editais com o mesmo formato",
                  "Aplicar mapeamento"):
        assert texto in r.text, texto
    assert '<option value="1" selected>' in r.text and 'name="tabela" value="1"' in r.text
    assert "Encontrei um mapa salvo" not in r.text
    assert '<button class="btn primario" type="submit" disabled>' in r.text   # falta mapear

    # sem ?tabela, abre na primeira tabela com conteúdo; a vazia avisa e para
    assert '<option value="0" selected>' in web.get(f"/pregao/{p.slug}/mapeamento").text
    html = web.get(f"/pregao/{p.slug}/mapeamento?tabela=2").text
    assert "Tabela vazia — escolha outra." in html and "Passo 2" not in html
    # índice fora da lista cai no padrão em vez de quebrar
    assert web.get(f"/pregao/{p.slug}/mapeamento?tabela=99").status_code == 200
    assert web.get(f"/pregao/{p.slug}/mapeamento?tabela=abc").status_code == 200
    assert web.get("/pregao/nao-existe/mapeamento").status_code == 404


def test_mapeamento_sem_tabelas_nao_tem_o_que_mapear(web):
    p = web.pregao()
    p.extracao.tabelas_brutas = []
    p.salvar()
    html = web.get(f"/pregao/{p.slug}/mapeamento").text
    assert "Nada para mapear — volte e envie um PDF primeiro." in html
    assert "Voltar para o início" in html and "Passo 1" not in html
    r = web.post(f"/pregao/{p.slug}/mapeamento/aplicar", json=_ESCOLHAS)
    assert r.status_code == 400 and "Nada para mapear" in r.json()["erro"]


def test_aplicar_mapeamento_troca_a_extracao_e_volta_para_a_revisao(web):
    p = _com_busca(_pregao_para_mapear(web))
    _salvar(web, p, i=1, n=2, descricao="EDICAO QUE O MAPEAMENTO SUBSTITUI")
    url = f"/pregao/{p.slug}/mapeamento/aplicar"

    # incompleto (falta Quantidade) e escolhas inválidas: recusa sem mexer em nada
    r = web.post(url, json={**_ESCOLHAS, "col_3": "ignorar", "salvar": True})
    assert r.status_code == 400 and r.json()["erro"] == "Mapeamento incompleto — faltam: Quantidade."
    assert web.post(url, json={"tabela": "1"}).json()["erro"] == (
        "Mapeamento incompleto — faltam: Descrição, Quantidade.")
    assert web.post(url, json={**_ESCOLHAS, "col_1": "preco"}).status_code == 400
    assert web.post(url, json={**_ESCOLHAS, "tabela": "9"}).status_code == 400
    assert web.post(url, json={**_ESCOLHAS, "tabela": "2"}).json()["erro"] == "Tabela vazia — escolha outra."
    # mapeamento que não lê item nenhum não apaga o que já existe
    r = web.post(url, json={"tabela": "0", "col_0": "descricao", "col_1": "quantidade"})
    assert r.status_code == 400 and "nenhum item foi lido" in r.json()["erro"]
    p = Pregao.abrir(p.slug)
    assert len(p.itens_edital) == 2 and _tem_busca(p.slug)
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mapeamentos_pdf").fetchone()[0] == 0

    r = web.post(url, json={**_ESCOLHAS, "salvar": True})
    assert r.json() == {"ok": True, "ir": f"/pregao/{p.slug}/revisao?mapeado=salvo"}
    p = Pregao.abrir(p.slug)
    assert [(i.item_numero, i.descricao, i.unidade, i.quantidade, i.valor_referencia)
            for i in p.itens_edital] == [
        (1, "TUBO PVC SOLDAVEL 25MM BARRA 6M", "UND", 10.0, 5.5),
        (2, "JOELHO 90 GRAUS PVC 32MM", "UND", 1500.0, 3.25),
        (3, "CIMENTO CP II 50KG", "SC", 40.0, 38.9)]
    assert p.extracao.total_itens == 3 and p.extracao.confianca_global == 1.0
    assert len(p.extracao.tabelas_brutas) == 3            # as tabelas brutas continuam lá
    assert p.mapa_busca_edital == [0, 1, 2] and len(p.itens_revisados) == 3
    # a busca antiga (posições de outra lista) foi descartada
    assert not p.tem_busca and not p.estado.get("selecoes") and not p.estado.get("itens_nao_usar")

    html = web.get(f"/pregao/{p.slug}/revisao?mapeado=salvo").text
    assert ("Mapeamento salvo. Próximos PDFs com este cabeçalho usam automaticamente. Itens "
            "extraídos: <b>3</b>, confiança: <b>100%</b>.") in html
    assert [l["descricao"] for l in _grade(html)] == [
        "TUBO PVC SOLDAVEL 25MM BARRA 6M", "JOELHO 90 GRAUS PVC 32MM", "CIMENTO CP II 50KG"]
    assert "Mapeamento salvo." not in web.get(f"/pregao/{p.slug}/revisao").text


def test_mapa_salvo_volta_preenchido_no_proximo_edital(web):
    p = _pregao_para_mapear(web)
    web.post(f"/pregao/{p.slug}/mapeamento/aplicar", json={**_ESCOLHAS, "salvar": True})
    assinatura = mapeamento_manual.assinatura_cabecalho(_TABELA[0])
    with db.get_conn() as conn:
        assert mapeamento_manual.carregar_mapa(conn, assinatura) == {
            "codigo_orgao": 0, "descricao": 1, "unidade": 2, "quantidade": 3, "valor_referencia": 4}

    # OUTRO pregão, mesmo cabeçalho (caixa e espaços diferentes): os campos já vêm escolhidos
    outro = _pregao_para_mapear(web, orgao="Câmara de Teste", numero="11/2026")
    outro.extracao.tabelas_brutas[1][0] = ["item", "Descrição  do Material", "UNID", "Quant", "valor"]
    outro.salvar()
    html = web.get(f"/pregao/{outro.slug}/mapeamento?tabela=1").text
    assert ("Encontrei um mapa salvo desta assinatura de cabeçalho — os campos abaixo já vieram "
            "preenchidos. Ajuste se precisar.") in html
    escolhidos = re.findall(r'<select name="(col_\d)" data-map-coluna>.*?<option value="(\w+)" selected>',
                            html, re.S)
    assert dict(escolhidos) == {k: v for k, v in _ESCOLHAS.items() if k != "tabela"}
    assert 'class="aviso erro oculto" id="map-faltando"' in html      # nada faltando
    assert '<button class="btn primario" type="submit">' in html      # já pode aplicar
    # a outra tabela (cabeçalho diferente) não herda o mapa
    assert "Encontrei um mapa salvo" not in web.get(f"/pregao/{outro.slug}/mapeamento?tabela=0").text


def test_aplicar_sem_salvar_nao_guarda_o_mapa(web):
    p = _pregao_para_mapear(web)
    r = web.post(f"/pregao/{p.slug}/mapeamento/aplicar", json={**_ESCOLHAS, "salvar": False})
    assert r.json()["ir"] == f"/pregao/{p.slug}/revisao?mapeado=aplicado"
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mapeamentos_pdf").fetchone()[0] == 0
    assert len(Pregao.abrir(p.slug).itens_edital) == 3
    html = web.get(f"/pregao/{p.slug}/revisao?mapeado=aplicado").text
    assert "Mapeamento aplicado. Itens extraídos: <b>3</b>, confiança: <b>100%</b>." in html


def test_mapeamento_campo_repetido_a_ultima_coluna_vence(web):
    p = _pregao_para_mapear(web)
    # Descrição escolhida nas colunas 1 e 2: vale a 2 (UNID vira a descrição)
    r = web.post(f"/pregao/{p.slug}/mapeamento/aplicar",
                 json={"tabela": "1", "col_1": "descricao", "col_2": "descricao",
                       "col_3": "quantidade", "salvar": False})
    assert r.json()["ok"]
    assert [i.descricao for i in Pregao.abrir(p.slug).itens_edital] == ["UND", "UND"]


def test_docling_roda_em_tarefa_e_troca_as_tabelas(web, monkeypatch):
    p = _pregao_para_mapear(web)
    url = f"/pregao/{p.slug}/mapeamento"

    # sem o Docling instalado: nem aparece, e o endereço recusa
    monkeypatch.setattr(rotas_revisao.extrator_docling, "disponivel", lambda: False)
    assert "Docling" not in web.get(url).text
    assert web.post(f"{url}/docling", json={}).status_code == 400

    monkeypatch.setattr(rotas_revisao.extrator_docling, "disponivel", lambda: True)
    html = web.get(url).text
    assert "Não encontrou a tabela certa? Carregar com extração profunda (Docling)" in html
    assert ("Docling usa um modelo de visão para detectar tabelas que o pdfplumber não viu. "
            "Funciona melhor em PDFs com layout complexo, mas demora <b>5-10 minutos</b> por "
            "edital. Use só quando a tabela certa não está na lista abaixo.") in html
    assert "Carregar tabelas com Docling" in html

    solta, chamadas = threading.Event(), []

    def _docling_falso(pdf):
        chamadas.append(pdf)
        solta.wait(10)
        return [[["0", "1"], ["TUBO PVC 25MM", "10"]], [list(l) for l in _TABELA]]

    monkeypatch.setattr(rotas_revisao.extrator_docling, "coletar_tabelas_brutas_docling", _docling_falso)
    r = web.post(f"{url}/docling", json={}).json()
    assert r["ok"] and r["tarefa"] and r["recarregar"]
    try:
        html = web.get(url).text            # enquanto roda: só o cartão de progresso
        assert f'data-tarefa="{r["tarefa"]}"' in html and "Passo 1" not in html
        assert f'data-ao-terminar="/pregao/{p.slug}/mapeamento?docling=1"' in html
        assert web.post(f"{url}/docling", json={}).json()["tarefa"] == r["tarefa"]   # 2 cliques = 1 tarefa
    finally:
        solta.set()
        _esperar("docling", p.slug)
    assert chamadas == [p["pdf_path"]]
    p = Pregao.abrir(p.slug)
    assert len(p.extracao.tabelas_brutas) == 2 and p["usando_tabelas_docling"] is True
    assert len(p.itens_edital) == 2          # a lista da revisão só muda ao APLICAR um mapa

    html = web.get(f"{url}?docling=1").text
    assert "Docling detectou 2 tabelas. Escolha abaixo." in html
    assert "Mostrando tabelas detectadas pelo Docling (extração profunda)." in html
    assert "Carregar tabelas com Docling" not in html        # já está usando as do Docling
    assert "Tabela #2 · 5 colunas · 4 linhas" in html
    assert "Docling detectou" not in web.get(url).text       # o aviso é só na volta da tarefa


def test_docling_que_falha_ou_nao_acha_nada_mantem_as_tabelas(web, monkeypatch):
    p = _pregao_para_mapear(web)
    url = f"/pregao/{p.slug}/mapeamento"
    monkeypatch.setattr(rotas_revisao.extrator_docling, "disponivel", lambda: True)

    monkeypatch.setattr(rotas_revisao.extrator_docling, "coletar_tabelas_brutas_docling", lambda pdf: [])
    web.post(f"{url}/docling", json={})
    _esperar("docling", p.slug)
    html = web.get(f"{url}?docling=1").text
    assert "não encontrou nenhuma tabela" in html and "Passo 1" in html
    assert len(Pregao.abrir(p.slug).extracao.tabelas_brutas) == 3
    assert not Pregao.abrir(p.slug)["usando_tabelas_docling"]

    def _quebra(pdf):
        raise RuntimeError("modelo de visão indisponível")

    monkeypatch.setattr(rotas_revisao.extrator_docling, "coletar_tabelas_brutas_docling", _quebra)
    web.post(f"{url}/docling", json={})
    _esperar("docling", p.slug)
    html = web.get(url).text
    assert "Docling falhou: " in html and "Traceback" not in html and "Passo 1" in html
    assert len(Pregao.abrir(p.slug).extracao.tabelas_brutas) == 3
