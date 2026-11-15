"""Interface web — etapa "Planilha": Substituição Tributária/NCM, margem e
geração da planilha matriz (porta de `_gerar_excel`, `_painel_markup`,
`_identificar_ncm`, `_seletor_margem_venda`, `_registrar_aprendizado` e
`_persistir_edital` do Streamlit).

Sem rede: `scraper.consultar_ncms` e `backup.backup_externo_agora` são
trocados por dublês. O exportador e o template MATRIZ são os reais.

Desenho do pregão de teste (`web.pregao(n_itens=4)`): o item 1 do edital nasce
"duvidoso" (desmarcado), então a lista de BUSCA é [edital 1, 2, 3] — a posição
de busca `i` cai na linha `3 + i + 1` da planilha. É isso que prova o
remapeamento busca → edital.
"""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from openpyxl import load_workbook

from pregoes import backup, config, credenciais, rascunho, scraper
from pregoes.exportador import TOKEN_ST, SelecaoFornecedor
from pregoes.normalizador import normalizar_descricao
from pregoes.scraper import ResultadoBusca
from pregoes.web import regras_planilha as regras
from pregoes.web import tarefas
from pregoes.web.pregao import Pregao, em_andamento, trava

URL_ALMEIDA = "https://www.almeidadistribuidor.com.br/produto/"
URL_DANTAS = "https://www.dantasdistribuidora.com.br/produto/"
NCM_ST = "39174090"        # consta no Anexo 05 (posição 3917)
NCM_NORMAL = "99999999"


@pytest.fixture(autouse=True)
def backup_falso(monkeypatch):
    """Nenhum teste dispara o backup externo de verdade."""
    chamado = threading.Event()
    monkeypatch.setattr(backup, "backup_externo_agora", lambda: chamado.set() or True)
    return chamado


def _sel(preco, codigo, marca, fornecedor="almeida", ncm=None, url="auto"):
    if url == "auto":
        url = (URL_DANTAS if fornecedor == "dantas" else URL_ALMEIDA) + codigo
    return SelecaoFornecedor(preco=preco, codigo_fornecedor=codigo, marca=marca,
                             ncm=ncm, url=url, fornecedor=fornecedor)


def _com_precos(web, selecoes=None, **kw):
    """Pregão de 4 itens já buscado: busca = edital[1:], escolhas nas posições
    de busca 0 (Almeida) e 2 (Dantas); a posição 1 ficou sem resultado."""
    p = web.pregao(n_itens=4, **kw)
    assert p.mapa_busca_edital == [1, 2, 3]
    if selecoes is None:
        selecoes = {0: _sel(10.0, "A1", "TIGRE"),
                    2: _sel(20.0, "D3", "KRONA", fornecedor="dantas")}
    p.estado["resultados_busca"] = {
        0: [ResultadoBusca("TUBO", "TIGRE", 10.0, "A1", "exato", "almeida")],
        1: [],
        2: [ResultadoBusca("JOELHO", "KRONA", 20.0, "D3", "exato", "dantas")],
    }
    p.estado["selecoes"] = dict(selecoes)
    p.salvar()
    return p


def _gerar(web, p, **dados):
    return web.post(f"/pregao/{p.slug}/planilha/gerar", json=dados)


def _planilhas(web):
    return sorted((web.dados / "exports").glob("*.xlsx"))


def _aba(web):
    arquivos = _planilhas(web)
    assert len(arquivos) == 1, arquivos
    return load_workbook(arquivos[0]).active


def _banco(sql, *args):
    conn = sqlite3.connect(config.DB_PATH)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _esperar_tarefa(tipo, slug):
    for _ in range(250):
        if not tarefas.ativa(tipo, slug):
            return
        time.sleep(0.02)
    raise AssertionError("a tarefa não terminou")


# --------------------------------------------------------------------------
# Tela
# --------------------------------------------------------------------------

def test_tela_com_precos_mostra_resumo_st_margem_e_um_botao_principal(web):
    p = _com_precos(web)
    antes = rascunho.carregar_pregao(p.slug)
    r = web.get(f"/pregao/{p.slug}/planilha")
    assert r.status_code == 200
    t = r.text
    assert "Gerar planilha matriz com preços" in t
    assert "Gerar planilha sem preços" not in t
    assert "Substituição Tributária deste pregão" in t
    assert regras.LEGENDA_ST.replace("'", "&#39;") in t
    assert "0 normais" in t and "0 com Substituição Tributária" in t and "2 sem NCM" in t
    assert "Identificar NCM e classificar (2 itens)" in t
    assert "Margem do preço de venda" in t and "Margem aplicada ao preço de venda" in t
    for rotulo in ("15%", "12%", "7%"):
        assert f"<span>{rotulo}</span>" in t
    assert 'value="AB" form="form-gerar"' in t and "checked" in t   # padrão de fábrica: 7%
    assert ("<b>Desconto Dantas:</b> 1 item escolhido é da Dantas e sairá com 17% de "
            "desconto no custo, já incluído na planilha.") in t
    assert t.count("btn g primario") + t.count("btn primario g") == 1
    # Abrir a tela NÃO grava nada (o Streamlit gravava um override por item).
    depois = rascunho.carregar_pregao(p.slug)
    assert "markup_overrides" not in depois and depois.keys() == antes.keys()
    assert credenciais.carregar_margem_venda() is None


def test_tela_sem_precos_so_resumo_e_botao(web):
    p = _com_precos(web)
    t = web.get(f"/pregao/{p.slug}/planilha?sem_precos=1").text
    assert "Gerar planilha sem preços" in t
    assert "Gerar planilha matriz com preços" not in t
    assert "Substituição Tributária deste pregão" not in t
    assert "Margem do preço de venda" not in t
    assert 'name="sem_precos" value="1"' in t
    # Pregão sem nenhuma escolha de preço cai no mesmo modo, sem precisar da URL.
    q = web.pregao(n_itens=2, numero="11/2026")
    t = web.get(f"/pregao/{q.slug}/planilha").text
    assert "Gerar planilha sem preços" in t and "Margem do preço de venda" not in t


def test_pregao_inexistente_da_404(web):
    assert web.get("/pregao/nao-existe/planilha").status_code == 404
    for acao in ("gerar", "st", "ncm", "margem"):
        dados = {"margem": "Z"} if acao == "margem" else {"i": 0, "st": "st"}
        r = web.post(f"/pregao/nao-existe/planilha/{acao}", json=dados)
        assert r.status_code in (200, 404), acao
        if acao != "margem":                      # margem é preferência global
            assert r.status_code == 404 and r.json()["ok"] is False


# --------------------------------------------------------------------------
# Geração
# --------------------------------------------------------------------------

def test_gerar_sem_precos_exporta_o_edital_inteiro_e_registra_no_historico(web, backup_falso):
    p = _com_precos(web)                 # tem escolhas — e elas NÃO entram
    r = _gerar(web, p, sem_precos="1")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "ir": f"/pregao/{p.slug}/planilha?sem_precos=1"}

    ws = _aba(web)
    for offset in range(4):              # TODOS os itens do edital, na ordem
        linha = 3 + offset
        assert ws[f"B{linha}"].value == offset + 1
        assert ws[f"C{linha}"].value == f"PRODUTO DE TESTE {offset + 1}"
        assert ws[f"L{linha}"].value is None and ws[f"T{linha}"].value is None
        assert ws[f"U{linha}"].value is None and ws[f"G{linha}"].value is None
    assert ws["C7"].value is None
    assert str(ws["H3"].value).startswith("=")          # fórmulas do template intactas

    editais = _banco("SELECT id, orgao, numero_pregao, status FROM editais")
    assert [e[1:] for e in editais] == [("Prefeitura de Teste", "10/2026", "rascunho")]
    assert _banco("SELECT COUNT(*) FROM edital_itens WHERE preco_base IS NULL") == [(4,)]
    log = _banco("SELECT id, arquivo, n_itens, n_selecoes, markup_global FROM exports_log")
    assert len(log) == 1 and log[0][1:] == (_planilhas(web)[0].name, 4, 0, config.MARKUP_DEFAULT)
    # "Gerar sem buscar" não registra aprendizado.
    assert _banco("SELECT COUNT(*) FROM mapeamentos_aprendidos") == [(0,)]
    assert backup_falso.wait(2)

    # O rascunho FICA, com o hash; o pregão some de "em andamento".
    q = Pregao.abrir(p.slug)
    assert q is not None and q.gerado_e_intocado and q.passo() == 3
    assert em_andamento() == []

    t = web.get(f"/pregao/{p.slug}/planilha?sem_precos=1").text
    assert "Planilha gerada:" in t and log[0][1] in t
    assert f'href="/historico/planilha/{log[0][0]}"' in t and "Baixar planilha matriz" in t
    assert 'href="/novo"' in t and "Começar um novo pregão" in t
    assert 'href="/historico"' in t and "Ver em Planilhas geradas" in t
    assert regras.MSG_CONCLUIDO in t
    assert "Inclui preço/marca/código" not in t


def test_gerar_com_precos_remapeia_busca_para_edital_e_registra_aprendizado(web, backup_falso):
    p = _com_precos(web)
    r = _gerar(web, p, margem="AB")
    assert r.status_code == 200 and r.json() == {"ok": True, "ir": f"/pregao/{p.slug}/planilha"}

    ws = _aba(web)
    # posição de busca 0 → edital 1 → linha 4 (Almeida, sem desconto)
    assert (ws["C4"].value, ws["L4"].value, ws["T4"].value, ws["U4"].value) == \
        ("PRODUTO DE TESTE 2", 10.0, "A1", "TIGRE")
    assert ws["G4"].value == "=AB4"
    # posição de busca 2 → edital 3 → linha 6 (Dantas: 17% de desconto e sufixo D)
    assert (ws["C6"].value, ws["L6"].value, ws["T6"].value, ws["U6"].value) == \
        ("PRODUTO DE TESTE 4", 16.6, "D3D", "KRONA")
    # edital 0 (desmarcado) e edital 2 (sem resultado) vão em branco
    for linha in (3, 5):
        assert ws[f"C{linha}"].value.startswith("PRODUTO DE TESTE")
        assert ws[f"L{linha}"].value is None and ws[f"T{linha}"].value is None
        assert ws[f"G{linha}"].value is None

    # Aprendizado: uma linha por escolha, com o fornecedor da seleção.
    aprendido = _banco("SELECT descricao_normalizada, fornecedor, codigo_fornecedor, marca, preco "
                       "FROM mapeamentos_aprendidos ORDER BY fornecedor")
    assert aprendido == [
        (normalizar_descricao("PRODUTO DE TESTE 2"), "almeida", "A1", "TIGRE", 10.0),
        (normalizar_descricao("PRODUTO DE TESTE 4"), "dantas", "D3", "KRONA", 20.0),
    ]
    # Histórico: preço do site (sem desconto) no item certo do edital.
    assert _banco("SELECT status FROM editais") == [("precificado",)]
    assert _banco("SELECT item_numero, preco_base, confianca_match FROM edital_itens "
                  "ORDER BY item_numero") == [(1, None, None), (2, 10.0, "manual"),
                                              (3, None, None), (4, 20.0, "manual")]
    log = _banco("SELECT id, n_itens, n_selecoes FROM exports_log")
    assert [l[1:] for l in log] == [(4, 2)]
    assert backup_falso.wait(2)

    q = Pregao.abrir(p.slug)
    assert q is not None                                  # o rascunho NÃO é apagado
    assert q["pregao_gerado_hash"] == q.hash() and q.gerado_e_intocado
    assert q.selecoes == p.selecoes                       # índices de busca preservados
    assert em_andamento() == []

    t = web.get(f"/pregao/{p.slug}/planilha").text
    assert "Planilha gerada:" in t and f'href="/historico/planilha/{log[0][0]}"' in t
    assert "Inclui preço/marca/código para 2 de 4 itens." in t
    assert "Desconto Dantas de 17% aplicado no custo de 1 item da Dantas." in t
    assert "Ajustar e gerar de novo" in t
    assert t.count("btn primario g") == 1                 # o principal agora é baixar


def test_sem_orgao_ou_numero_nao_gera_e_o_aprendizado_ja_foi_gravado(web):
    p = _com_precos(web)
    with trava(p.slug):
        q = Pregao.abrir(p.slug)
        q.estado["numero"] = "   "
        q.salvar()
    t = web.get(f"/pregao/{p.slug}/planilha").text
    assert regras.MSG_SEM_IDENTIDADE in t
    r = _gerar(web, p)
    assert r.status_code == 400
    assert r.json() == {"ok": False, "erro": (
        "Informe o órgão e o número do pregão antes de gerar a planilha — "
        "eles identificam o pregão no histórico. Volte e preencha os dois campos.")}
    assert _planilhas(web) == [] and _banco("SELECT COUNT(*) FROM editais") == [(0,)]
    assert Pregao.abrir(p.slug)["pregao_gerado_hash"] is None
    # Ordem do Streamlit: o aprendizado é gravado ANTES das validações.
    assert _banco("SELECT COUNT(*) FROM mapeamentos_aprendidos") == [(2,)]
    # No botão "sem preços" a mesma validação vale e nada é aprendido.
    assert _gerar(web, p, sem_precos=True).json()["erro"] == regras.MSG_SEM_IDENTIDADE


def test_gerar_de_novo_depois_de_alterar_atualiza_o_mesmo_registro(web):
    p = _com_precos(web)
    assert _gerar(web, p).json()["ok"]
    with trava(p.slug):                                   # o operador troca uma escolha
        q = Pregao.abrir(p.slug)
        q.selecoes[1] = _sel(7.5, "A9", "AMANCO")
        q.salvar()
    q = Pregao.abrir(p.slug)
    assert q["pregao_gerado_hash"] and not q.gerado_e_intocado
    assert em_andamento() == []                           # já tem planilha: segue fora da lista

    t = web.get(f"/pregao/{p.slug}/planilha").text
    assert "A planilha registrada está desatualizada." in t
    assert "Gerar de novo" in t and "Planilha gerada:" not in t
    assert t.count("btn g primario") == 1

    assert _gerar(web, p).json()["ok"]
    assert _banco("SELECT COUNT(*) FROM editais") == [(1,)]            # UPSERT, não duplica
    assert _banco("SELECT COUNT(*) FROM edital_itens") == [(4,)]
    log = _banco("SELECT id, n_selecoes FROM exports_log ORDER BY id")
    assert [l[1] for l in log] == [2, 3]
    assert Pregao.abrir(p.slug).gerado_e_intocado
    t = web.get(f"/pregao/{p.slug}/planilha").text
    assert "Planilha gerada:" in t and f'href="/historico/planilha/{log[-1][0]}"' in t
    assert "Inclui preço/marca/código para 3 de 4 itens." in t
    ws = load_workbook(_planilhas(web)[-1]).active
    assert (ws["L5"].value, ws["T5"].value, ws["U5"].value) == (7.5, "A9", "AMANCO")


def test_registro_casa_orgao_e_numero_sem_diferenciar_caixa_e_espacos(web):
    p = _com_precos(web)
    assert _gerar(web, p).json()["ok"]
    outro = _com_precos(web, orgao="  prefeitura de teste ", numero="10/2026 ")
    assert _gerar(web, outro).json()["ok"]
    assert _banco("SELECT COUNT(*) FROM editais") == [(1,)]
    assert _banco("SELECT COUNT(*) FROM exports_log") == [(2,)]


def test_falha_no_historico_nao_bloqueia_o_download(web, monkeypatch):
    from pregoes import db

    def _quebra(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    p = _com_precos(web)
    original = db.export_log_gravar
    monkeypatch.setattr(db, "export_log_gravar", _quebra)
    r = _gerar(web, p)
    assert r.status_code == 200 and r.json()["ok"]
    arquivo = _planilhas(web)[0].name
    destino = r.json()["ir"]
    assert destino == f"/pregao/{p.slug}/planilha?arquivo={arquivo}"
    assert _banco("SELECT COUNT(*) FROM editais") == [(0,)]            # mesma transação
    assert Pregao.abrir(p.slug).gerado_e_intocado
    assert len(em_andamento()) == 1                       # sem registro, segue na lista

    t = web.get(destino).text
    assert regras.MSG_HISTORICO_FALHOU in t
    assert "Planilha gerada:" in t and "Baixar planilha matriz" in t
    link = f"/pregao/{p.slug}/planilha/arquivo/{arquivo}"
    assert f'href="{link}"' in t
    baixado = web.get(link)
    assert baixado.status_code == 200
    assert baixado.content == _planilhas(web)[0].read_bytes()
    assert "spreadsheetml" in baixado.headers["content-type"]
    # O download daqui só serve planilha DESTE pregão que exista em exports.
    (web.dados / "exports" / "pregao_Outro_1_x.xlsx").write_bytes(b"x")
    for nome in ("pregao_Outro_1_x.xlsx", "nao_existe.xlsx", "..%2F..%2Fpregoes.db"):
        assert web.get(f"/pregao/{p.slug}/planilha/arquivo/{nome}").status_code == 404
    # Sem o nome na URL, a tela avisa e pede para gerar de novo.
    t = web.get(f"/pregao/{p.slug}/planilha").text
    assert "não ficou registrada" in t and "Planilha gerada:" not in t
    # Gerar de novo com o histórico funcionando completa o registro e limpa a URL.
    monkeypatch.setattr(db, "export_log_gravar", original)
    assert _gerar(web, p).json()["ir"] == f"/pregao/{p.slug}/planilha"
    assert _banco("SELECT COUNT(*) FROM exports_log") == [(1,)] and em_andamento() == []
    assert regras.MSG_HISTORICO_FALHOU not in web.get(f"/pregao/{p.slug}/planilha").text


def test_falha_do_exportador_vira_mensagem_em_portugues(web, monkeypatch):
    from pregoes.web import rotas_planilha

    def _quebra(*a, **k):
        raise RuntimeError("Traceback interno xyz")

    p = _com_precos(web)
    monkeypatch.setattr(rotas_planilha.exportador, "exportar", _quebra)
    r = _gerar(web, p)
    assert r.status_code == 500 and r.json()["ok"] is False
    assert "xyz" not in r.json()["erro"] and "gerar a planilha" in r.json()["erro"]
    assert Pregao.abrir(p.slug)["pregao_gerado_hash"] is None


# --------------------------------------------------------------------------
# Substituição Tributária
# --------------------------------------------------------------------------

def test_sem_override_item_sem_ncm_e_normal_igual_ao_padrao_do_streamlit():
    sel = {0: _sel(1.0, "A", "X"), 1: _sel(2.0, "B", "Y", ncm=NCM_ST),
           2: _sel(3.0, "C", "Z", ncm=NCM_NORMAL)}
    aqui = regras.classificar(sel, {})
    # O Streamlit gravava "Normal" para todo item sem NCM a cada desenho da tela.
    la = regras.classificar(sel, {0: config.MARKUP_NORMAL})
    assert (aqui.markups, aqui.resumo, aqui.sem_ncm, aqui.com_st) == \
        (la.markups, la.resumo, la.sem_ncm, la.com_st)
    assert aqui.resumo == {"st": 1, "normal": 1, "sem_ncm": 1} and aqui.com_st == {1}
    assert aqui.markups[0] == config.MARKUP_NORMAL


def test_escolha_de_st_salva_sozinha_e_muda_a_classificacao(web):
    p = _com_precos(web)
    r = web.post(f"/pregao/{p.slug}/planilha/st", json={"i": 0, "st": "st"})
    assert r.status_code == 200
    html = r.json()["html"]["#st-resumo"]
    assert 'id="st-resumo"' in html and "2 sem NCM" in html
    assert "1 deles marcado por você como Com Substituição Tributária" in html
    q = Pregao.abrir(p.slug)
    assert q.markup_overrides == {0: config.MARKUP_ST}
    assert regras.classificar(q.selecoes, q.markup_overrides).com_st == {0}
    assert 'value="st" data-salvar' in web.get(f"/pregao/{p.slug}/planilha").text

    assert _gerar(web, p).json()["ok"]
    ws = _aba(web)
    assert ws["AC4"].value == TOKEN_ST          # busca 0 → edital 1 → linha 4
    assert ws["AC3"].value is None and ws["AC6"].value is None

    # Voltar para "Normal" remove o override — e a planilha gerada fica desatualizada.
    antes = Pregao.abrir(p.slug)
    assert antes.gerado_e_intocado
    r = web.post(f"/pregao/{p.slug}/planilha/st", json={"i": 0, "st": "normal"})
    assert r.json() == {"ok": True, "recarregar": True}
    q = Pregao.abrir(p.slug)
    assert q.markup_overrides == {} and not q.gerado_e_intocado

    for ruim in ({"i": 1, "st": "st"},          # posição sem escolha de preço
                 {"i": "x", "st": "st"}, {"i": 0, "st": "talvez"}, {}):
        assert web.post(f"/pregao/{p.slug}/planilha/st", json=ruim).status_code == 400


def test_item_com_ncm_e_classificado_sozinho_e_recusa_override(web):
    p = _com_precos(web, selecoes={0: _sel(10.0, "A1", "TIGRE", ncm=NCM_ST),
                                   1: _sel(5.0, "A2", "AMANCO", ncm=NCM_NORMAL),
                                   2: _sel(20.0, "D3", "KRONA", fornecedor="dantas")})
    t = web.get(f"/pregao/{p.slug}/planilha").text
    assert "1 normal<" in t and "1 com Substituição Tributária" in t and "1 sem NCM" in t
    assert "Identificar NCM e classificar (1 item)" in t
    assert "1 item sem NCM" in t and 'id="st-item-2"' in t and 'id="st-item-0"' not in t
    assert web.post(f"/pregao/{p.slug}/planilha/st",
                    json={"i": 0, "st": "normal"}).status_code == 400
    assert _gerar(web, p).json()["ok"]
    ws = _aba(web)
    assert [ws[f"AC{linha}"].value for linha in (3, 4, 5, 6)] == [None, TOKEN_ST, None, None]


# --------------------------------------------------------------------------
# NCM (tarefa em segundo plano)
# --------------------------------------------------------------------------

def test_tarefa_ncm_aplica_so_os_definitivos_e_remove_o_override(web, monkeypatch):
    p = _com_precos(web, selecoes={
        0: _sel(10.0, "A1", "TIGRE"),                              # NCM encontrado
        1: _sel(5.0, "D2", "AMANCO", fornecedor="dantas"),         # definitivo: sem NCM
        2: _sel(20.0, "A3", "KRONA"),                              # falha passageira
    })
    with trava(p.slug):
        q = Pregao.abrir(p.slug)
        q.selecoes[3] = _sel(1.0, "Z9", "X", url="https://outrosite.com/z9")   # domínio estranho
        q.selecoes[4] = _sel(1.0, "Z8", "X", url=None)                        # sem URL (catálogo)
        q.markup_overrides.update({0: config.MARKUP_ST, 1: config.MARKUP_ST,
                                   2: config.MARKUP_ST})
        q.salvar()
    recebidos = []

    def _falso(pedidos, *, progresso=None):
        recebidos.extend(pedidos)
        for feitos in range(1, len(pedidos) + 1):
            progresso(feitos, len(pedidos))
        return {0: NCM_ST, 1: None}, {2}

    monkeypatch.setattr(scraper, "consultar_ncms", _falso)
    t = web.get(f"/pregao/{p.slug}/planilha").text
    assert "Identificar NCM e classificar (4 itens)" in t      # os 4 com URL

    r = web.post(f"/pregao/{p.slug}/planilha/ncm", json={})
    assert r.status_code == 200 and r.json()["tarefa"] and r.json()["recarregar"]
    _esperar_tarefa("ncm", p.slug)

    # fornecedor deduzido da URL; o código vai junto (cache durável); o
    # domínio desconhecido e o item sem URL ficam de fora.
    assert sorted(recebidos) == [(0, "almeida", URL_ALMEIDA + "A1", "A1"),
                                 (1, "dantas", URL_DANTAS + "D2", "D2"),
                                 (2, "almeida", URL_ALMEIDA + "A3", "A3")]
    q = Pregao.abrir(p.slug)
    assert q.selecoes[0].ncm == NCM_ST and q.selecoes[0].marca == "TIGRE"
    assert q.selecoes[1].ncm is None and q.selecoes[2].ncm is None
    assert q.markup_overrides == {1: config.MARKUP_ST, 2: config.MARKUP_ST}

    t = web.get(f"/pregao/{p.slug}/planilha").text
    assert ("1 item(ns) não puderam ser consultados agora (login do fornecedor ou "
            "navegador). Os demais foram classificados. Clique em <b>Identificar NCM</b> "
            "de novo para tentar os que faltaram.") in t
    assert "1 com Substituição Tributária" in t and "4 sem NCM" in t
    assert 'id="st-item-0"' not in t                           # ganhou NCM: sai da lista


def test_tarefa_ncm_nao_aplica_em_item_cuja_escolha_mudou(web, monkeypatch):
    p = _com_precos(web, selecoes={0: _sel(10.0, "A1", "TIGRE")})

    def _falso(pedidos, *, progresso=None):
        with trava(p.slug):                    # o operador troca a opção no meio da consulta
            q = Pregao.abrir(p.slug)
            q.selecoes[0] = _sel(11.0, "A7", "FORTLEV")
            q.salvar()
        return {0: NCM_ST}, set()

    monkeypatch.setattr(scraper, "consultar_ncms", _falso)
    assert web.post(f"/pregao/{p.slug}/planilha/ncm", json={}).json()["ok"]
    _esperar_tarefa("ncm", p.slug)
    q = Pregao.abrir(p.slug)
    assert q.selecoes[0].codigo_fornecedor == "A7" and q.selecoes[0].ncm is None


def test_erro_geral_na_consulta_de_ncm_vira_aviso_e_nao_mexe_no_pregao(web, monkeypatch):
    p = _com_precos(web)

    def _quebra(pedidos, *, progresso=None):
        raise RuntimeError("navegador caiu")

    monkeypatch.setattr(scraper, "consultar_ncms", _quebra)
    assert web.post(f"/pregao/{p.slug}/planilha/ncm", json={}).json()["ok"]
    _esperar_tarefa("ncm", p.slug)
    t = next(t for t in tarefas.do_pregao(p.slug) if t.tipo == "ncm")
    assert t.estado == "concluida" and "consultar o NCM" in t.extra["aviso"]
    assert Pregao.abrir(p.slug).selecoes == p.selecoes
    assert "consultar o NCM" in web.get(f"/pregao/{p.slug}/planilha").text


def test_sem_item_identificavel_nao_dispara_tarefa(web):
    p = _com_precos(web, selecoes={0: _sel(10.0, "A1", "TIGRE", ncm=NCM_ST),
                                   1: _sel(5.0, "C1", "X", url=None)})
    assert "Identificar NCM e classificar" not in web.get(f"/pregao/{p.slug}/planilha").text
    r = web.post(f"/pregao/{p.slug}/planilha/ncm", json={})
    assert r.status_code == 400 and tarefas.do_pregao(p.slug) == []


# --------------------------------------------------------------------------
# Margem
# --------------------------------------------------------------------------

def test_margem_so_e_gravada_quando_difere_do_padrao_efetivo(web):
    p = _com_precos(web)
    url = f"/pregao/{p.slug}/planilha/margem"
    assert regras.margem_efetiva(None) == config.MARGEM_VENDA_PADRAO == "AB"
    # escolher o próprio padrão de fábrica não grava nada
    assert web.post(url, json={"margem": "AB"}).json() == {"ok": True}
    assert credenciais.carregar_margem_venda() is None
    r = web.post(url, json={"margem": "Z"}).json()
    assert r["ok"] and "15%" in r["aviso"]
    assert credenciais.carregar_margem_venda() == "Z"
    assert 'value="Z" form="form-gerar" data-salvar="' + url + '" checked' in \
        web.get(f"/pregao/{p.slug}/planilha").text
    # agora o padrão efetivo é Z: voltar para AB difere → grava
    assert web.post(url, json={"margem": "AB"}).json()["ok"]
    assert credenciais.carregar_margem_venda() == "AB"
    assert web.post(url, json={"margem": "AC"}).status_code == 400
    assert credenciais.carregar_margem_venda() == "AB"
    # preferência salva inválida cai no padrão de fábrica
    assert regras.margem_efetiva("XX") == "AB"
    assert not regras.deve_salvar_margem("AB", None) and regras.deve_salvar_margem("AA", None)
    assert not regras.deve_salvar_margem("Z", "Z") and not regras.deve_salvar_margem("??", "Z")


def test_planilha_sai_na_margem_escolhida_na_tela(web):
    p = _com_precos(web)
    assert _gerar(web, p, margem="AA").json()["ok"]
    ws = _aba(web)
    assert ws["G4"].value == "=AA4" and ws["G6"].value == "=AA6"
    assert credenciais.carregar_margem_venda() == "AA"     # virou o padrão


def test_sem_margem_no_pedido_usa_a_salva(web):
    credenciais.salvar_margem_venda("Z")
    p = _com_precos(web)
    assert _gerar(web, p).json()["ok"]
    assert _aba(web)["G4"].value == "=Z4"


# --------------------------------------------------------------------------
# Regras puras
# --------------------------------------------------------------------------

def test_remapear_so_troca_pelo_edital_quando_o_mapa_bate():
    busca, edital = ["b0", "b1"], ["e0", "e1", "e2"]
    sel = {0: "s0", 1: "s1", 5: "fora"}
    itens, s, mk, st = regras.remapear(busca, edital, [2, 0], sel, {0: 1.3, 1: 1.45}, {1, 9})
    assert itens is edital and s == {2: "s0", 0: "s1"}
    assert mk == {2: 1.3, 0: 1.45} and st == {0}
    # rascunho legado (sem edital, sem mapa ou mapa de outro tamanho): como veio
    for ed, mapa in ((None, [0, 1]), (edital, None), (edital, [0])):
        itens, s, mk, st = regras.remapear(busca, ed, mapa, sel, None, {1})
        assert itens is busca and s == sel and mk is None and st == {1}


def test_aprendizado_usa_o_fornecedor_da_selecao_ou_recasa_nas_opcoes():
    from pregoes.extrator import ItemExtraido
    itens = [ItemExtraido(descricao="TUBO PVC 25MM"), ItemExtraido(descricao="JOELHO 90"),
             ItemExtraido(descricao="   "), ItemExtraido(descricao="REGISTRO"),
             ItemExtraido(descricao="SEM ESCOLHA")]
    sel = {0: _sel(10.0, "A1", "TIGRE"),
           1: SelecaoFornecedor(preco=4.0, codigo_fornecedor="D7", marca="KRONA"),   # legado
           2: _sel(1.0, "A2", "X"),                                                 # descrição vazia
           3: SelecaoFornecedor(preco=9.0, codigo_fornecedor="Q1", marca="DECA")}   # sem origem
    resultados = {1: [ResultadoBusca("JOELHO", "KRONA", 4.0, "D7", "exato", "dantas")],
                  3: [ResultadoBusca("REGISTRO", "DOCOL", 9.0, "Q1", "exato", "almeida")]}
    assert regras.registros_aprendizado(itens, sel, resultados) == [
        (normalizar_descricao("TUBO PVC 25MM"), "almeida", "A1", "TIGRE", 10.0),
        (normalizar_descricao("JOELHO 90"), "dantas", "D7", "KRONA", 4.0),
    ]
    assert regras.registros_aprendizado(itens, {}, resultados) == []
    assert regras.registros_aprendizado([], sel, resultados) == []


def test_textos_do_desconto_dantas_e_do_sucesso():
    um = {0: _sel(1.0, "D1", "X", fornecedor="dantas"), 1: _sel(1.0, "A1", "X")}
    dois = {**um, 2: _sel(2.0, "D2", "Y", fornecedor="Dantas ")}
    assert regras.aviso_desconto_dantas({1: um[1]}) is None
    assert regras.aviso_desconto_dantas(um) == (
        "<b>Desconto Dantas:</b> 1 item escolhido é da Dantas e sairá com 17% de "
        "desconto no custo, já incluído na planilha.")
    assert regras.aviso_desconto_dantas(dois) == (
        "<b>Desconto Dantas:</b> 2 itens escolhidos são da Dantas e sairão com 17% de "
        "desconto no custo, já incluído na planilha.")
    assert regras.linhas_de_sucesso(0, 10, 0) == []
    assert regras.linhas_de_sucesso(3, 10, 0) == ["Inclui preço/marca/código para 3 de 10 itens."]
    assert regras.linhas_de_sucesso(3, 10, 2)[1] == \
        "Desconto Dantas de 17% aplicado no custo de 2 itens da Dantas."
    assert regras.fornecedor_da_url(None) is None
    assert regras.prefixo_do_arquivo(" Prefeitura de Teste ", "10/2026") == \
        "pregao_PrefeituradeTeste_102026_"
