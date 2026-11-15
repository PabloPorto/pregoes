"""Interface web — administração: Planilhas geradas (histórico), Meu catálogo,
Variação de preços e Configurações.

Sem rede: importador roda de verdade sobre planilhas minúsculas geradas aqui;
IA (alinhador/Gemini), envio de backup ao Drive e o scraper são dublês. Os
textos conferidos são os do inventário (docs/INVENTARIO_UI.md 2.7, 5.x, 6.8,
6.11) — `pregoes.ui.app` não é importado (o import dele mexe no banco real).
"""
from __future__ import annotations

import io
import time
from datetime import datetime, timedelta, timezone

import pytest
from openpyxl import Workbook

from pregoes import backup, config, credenciais, db
from pregoes.normalizador import normalizar_descricao as _norm
from pregoes.web import regras_historico as regras
from pregoes.web import rotas_catalogo, rotas_config, tarefas

SENHA = "SenhaSecretaDoSite9731"
CHAVE = "AIzaSyCHAVEsuperSECRETA0123456789abcdefg"


# --------------------------------------------------------------------------
# Apoio
# --------------------------------------------------------------------------

def _edital(orgao="Prefeitura de Araruna", numero="6/2026", status="precificado",
            criado_em="2026-06-09 17:30:00", planilhas=()):
    """Registra um pregão; `planilhas` = [(arquivo, n_itens, n_selecoes, criar_arquivo)]."""
    with db.get_conn() as conn:
        eid = conn.execute(
            "INSERT INTO editais (orgao, numero_pregao, arquivo_pdf, status, criado_em) "
            "VALUES (?, ?, 'x.pdf', ?, ?)", (orgao, numero, status, criado_em)).lastrowid
        conn.execute(
            "INSERT INTO edital_itens (edital_id, item_numero, descricao_original) "
            "VALUES (?, 1, 'TUBO PVC 25MM')", (eid,))
        for i, (arquivo, n_itens, n_sel, criar) in enumerate(planilhas):
            conn.execute(
                "INSERT INTO exports_log (edital_id, arquivo, n_itens, n_selecoes, criado_em) "
                "VALUES (?, ?, ?, ?, ?)",
                (eid, arquivo, n_itens, n_sel, f"2026-06-{10 + i:02d} 12:00:00"))
            if criar:
                (config.EXPORTS_DIR / arquivo).write_bytes(b"PK planilha " + arquivo.encode())
    return eid


def _ids_exports(eid):
    with db.get_conn() as conn:
        return [r["id"] for r in conn.execute(
            "SELECT id FROM exports_log WHERE edital_id = ? ORDER BY id", (eid,))]


def _produto(descricao, marca=None, almeida=None, dantas=None, ncm=None, unidade="UN"):
    with db.get_conn() as conn:
        return conn.execute(
            "INSERT INTO produtos (descricao_canonica, unidade_padrao, marca, codigo_almeida, "
            "codigo_dantas, ncm) VALUES (?, ?, ?, ?, ?, ?)",
            (descricao, unidade, marca, almeida, dantas, ncm)).lastrowid


def _cotacao(produto_id, fornecedor, preco, quando):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO precos_historico (produto_id, fornecedor, preco, consultado_em, fonte) "
            "VALUES (?, ?, ?, ?, 'teste')", (produto_id, fornecedor, preco, quando))


def _matriz(linhas) -> bytes:
    """Planilha no formato que o importador reconhece (cabeçalho por nome)."""
    wb = Workbook()
    ws = wb.active
    ws.append(["ITEM", "DESCRIÇÃO", "UNID", "MARCA", "CÓDIGO", "PREÇO"])
    for i, linha in enumerate(linhas, 1):
        ws.append([i, *linha])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _enviar_planilhas(web, *arquivos, fornecedor="almeida"):
    return web.post("/catalogo/importar", data={"fornecedor": fornecedor},
                    files=[("planilhas", (nome, conteudo, "application/octet-stream"))
                           for nome, conteudo in arquivos])


def _esperar(tipo):
    for _ in range(500):
        if not tarefas.ativa(tipo, None):
            return next(t for t in tarefas.do_pregao(None) if t.tipo == tipo)
        time.sleep(0.02)
    raise AssertionError(f"a tarefa {tipo} não terminou")


def _painel(resposta) -> str:
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["html"]["#painel"]


# --------------------------------------------------------------------------
# Planilhas geradas (histórico)
# --------------------------------------------------------------------------

def test_historico_vazio_convida_a_comecar(web):
    r = web.get("/historico")
    assert r.status_code == 200
    assert "Planilhas geradas" in r.text
    assert "Nenhum pregão processado ainda" in r.text


def test_rotulo_do_pregao_segue_o_do_streamlit():
    # 17:30 UTC = 14:30 em Brasília (o SQLite grava em UTC).
    assert regras.rotulo_pregao("Prefeitura de Araruna", "00006/2026", "precificado",
                                "2026-06-09 17:30:00") == (
        "Pregão 00006/2026 — Prefeitura de Araruna — precificado em 09/06 14:30")
    assert regras.rotulo_pregao(None, "7/2026", None, "2026-06-09 17:30:00") == (
        "Pregão 7/2026 — rascunho em 09/06 14:30")
    assert regras.rotulo_pregao(" ", None, "x", None) == (
        "Pregão sem identificação (de data desconhecida)")
    assert regras.data_amigavel("ontem") == "ontem"


def test_historico_lista_todas_as_planilhas_de_cada_pregao(web):
    eid = _edital(planilhas=[("antiga.xlsx", 437, 120, True), ("sumiu.xlsx", 437, 300, False),
                             ("nova.xlsx", 440, 401, True)])
    _edital("Câmara de Solânea", "9/2026", status="rascunho", criado_em="2026-06-01 12:00:00")
    antiga, sumiu, nova = _ids_exports(eid)
    r = web.get("/historico")
    assert "Pregão 6/2026 — Prefeitura de Araruna" in r.text
    assert "09/06/2026 14:30" in r.text                       # fuso do operador
    assert "3 planilhas · 440 itens · 401 com preço/marca" in r.text      # a mais recente
    assert f'href="/historico/planilha/{antiga}"' in r.text   # todas baixáveis…
    assert f'href="/historico/planilha/{nova}"' in r.text
    assert f'href="/historico/planilha/{sumiu}"' not in r.text            # …menos a que sumiu
    assert "Arquivo não está na pasta — gere de novo a partir do pregão." in r.text
    assert "Nenhuma planilha registrada — gere de novo a partir do pregão." in r.text
    assert "Sem planilha para baixar." in r.text
    # mais recente primeiro
    assert r.text.index("Prefeitura de Araruna") < r.text.index("Câmara de Solânea")


def test_historico_filtra_pelo_rotulo_e_avisa_quando_nao_acha(web):
    _edital("Prefeitura de Araruna", "6/2026")
    _edital("Câmara de Solânea", "9/2026", status="rascunho")
    r = web.get("/historico", params={"q": "solânea"})
    assert "Câmara de Solânea" in r.text and "Prefeitura de Araruna" not in r.text
    assert "1 de 2" in r.text
    # o rótulo inclui o status — dá para filtrar por ele
    r = web.get("/historico", params={"q": "PRECIFICADO"})
    assert "Prefeitura de Araruna" in r.text and "Câmara de Solânea" not in r.text
    r = web.get("/historico", params={"q": "inexistente"})
    assert "Nenhum pregão encontrado para “inexistente”." in r.text


def test_historico_pagina_de_30_em_30_e_leva_o_filtro_nos_links(web):
    for i in range(35):
        _edital("Prefeitura de Araruna", f"{i + 1}/2026",
                criado_em=f"2026-05-{(i % 28) + 1:02d} 12:00:{i:02d}")
    _edital("Outro órgão", "99/2026")
    r = web.get("/historico", params={"q": "araruna"})
    assert r.text.count('id="edital-') == 30
    assert "Página 1 de 2" in r.text
    assert 'href="/historico?q=araruna&amp;p=2"' in r.text
    r = web.get("/historico", params={"q": "araruna", "p": "2"})
    assert r.text.count('id="edital-') == 5 and "Página 2 de 2" in r.text
    # página que não existe mais cai na última
    assert "Página 2 de 2" in web.get("/historico", params={"q": "araruna", "p": "9"}).text


def test_historico_editar_identificacao(web):
    eid = _edital("Prefeitura de Araruna", "6/2026", planilhas=[("p.xlsx", 3, 2, True)])
    formulario = web.get(f"/historico/{eid}/editar")
    assert "Editar identificação do pregão" in formulario.text
    assert 'value="Prefeitura de Araruna"' in formulario.text
    r = web.post(f"/historico/{eid}/editar", json={"orgao": "  Prefeitura de Cacimba  ",
                                                   "numero": " "})
    corpo = r.json()
    assert corpo["ok"] and corpo["fechar_modal"]
    linha = corpo["html"][f"#edital-{eid}"]
    assert linha.startswith(f'<tr id="edital-{eid}"')
    assert "<b>Prefeitura de Cacimba</b>" in linha and "Pregão 6/2026" not in linha
    with db.get_conn() as conn:
        e = conn.execute("SELECT orgao, numero_pregao FROM editais WHERE id = ?", (eid,)).fetchone()
    assert e["orgao"] == "Prefeitura de Cacimba" and e["numero_pregao"] is None   # vazio → NULL
    assert web.post("/historico/9999/editar", json={"orgao": "x"}).status_code == 404
    assert web.get("/historico/9999/editar").status_code == 404


def test_historico_excluir_apaga_planilhas_e_registros(web):
    eid = _edital(planilhas=[("a.xlsx", 3, 2, True), ("b.xlsx", 3, 3, True)])
    outro = _edital("Câmara de Solânea", "9/2026", planilhas=[("fica.xlsx", 1, 1, True)])
    pagina = web.get("/historico").text
    assert "Excluir este pregão e suas planilhas? Não dá para desfazer." in pagina
    assert web.post(f"/historico/{eid}/excluir", json={}).json() == {"ok": True, "recarregar": True}
    assert not (config.EXPORTS_DIR / "a.xlsx").exists()
    assert not (config.EXPORTS_DIR / "b.xlsx").exists()
    assert (config.EXPORTS_DIR / "fica.xlsx").exists()
    with db.get_conn() as conn:
        assert [r["id"] for r in conn.execute("SELECT id FROM editais")] == [outro]
        assert conn.execute("SELECT COUNT(*) FROM exports_log WHERE edital_id = ?",
                            (eid,)).fetchone()[0] == 0          # CASCADE
        assert conn.execute("SELECT COUNT(*) FROM edital_itens WHERE edital_id = ?",
                            (eid,)).fetchone()[0] == 0
    assert web.post(f"/historico/{eid}/excluir", json={}).status_code == 404


def test_excluir_nunca_apaga_arquivo_fora_da_pasta_de_planilhas(web):
    credenciais.salvar("almeida", "a@b.com", SENHA)
    eid = _edital(planilhas=[("../credenciais.json", 1, 1, False)])
    assert web.post(f"/historico/{eid}/excluir", json={}).json()["ok"]
    assert credenciais.CAMINHO.exists()


def test_baixar_planilha_devolve_o_arquivo_do_disco(web):
    eid = _edital(planilhas=[("Pregao_6_2026.xlsx", 3, 2, True), ("sumiu.xlsx", 3, 2, False)])
    existe, sumiu = _ids_exports(eid)
    r = web.get(f"/historico/planilha/{existe}")
    assert r.status_code == 200
    assert r.content == b"PK planilha Pregao_6_2026.xlsx"
    assert 'filename="Pregao_6_2026.xlsx"' in r.headers["content-disposition"]
    assert "spreadsheetml" in r.headers["content-type"]
    faltando = web.get(f"/historico/planilha/{sumiu}")
    assert faltando.status_code == 404
    assert "gere de novo a partir do pregão" in faltando.text
    assert web.get("/historico/planilha/99999").status_code == 404
    assert web.get("/historico/planilha/abc").status_code == 404


def test_baixar_planilha_nao_sai_da_pasta_de_planilhas(web):
    """O nome gravado no banco é o único dado que vira caminho — um registro
    adulterado ('../credenciais.json') não pode servir outro arquivo."""
    credenciais.salvar("almeida", "a@b.com", SENHA)
    (config.EXPORTS_DIR / "sub").mkdir()
    (config.EXPORTS_DIR / "sub" / "x.xlsx").write_bytes(b"x")
    eid = _edital(planilhas=[("../credenciais.json", 1, 1, False),
                             (str(credenciais.CAMINHO), 1, 1, False),
                             ("sub/x.xlsx", 1, 1, False), ("", 1, 1, False)])
    for export_id in _ids_exports(eid):
        r = web.get(f"/historico/planilha/{export_id}")
        assert r.status_code == 404, export_id
        assert SENHA not in r.text
    assert regras.caminho_da_planilha("../credenciais.json") is None
    # e a lista trata como arquivo ausente (sem link de download)
    assert 'href="/historico/planilha/' not in web.get("/historico").text


def test_historico_oferece_reabrir_quando_o_pregao_ainda_esta_guardado(web):
    p = web.pregao(orgao="Prefeitura de Teste", numero="10/2026")
    _edital("prefeitura de teste ", "10/2026")            # mesma identidade, outra caixa
    _edital("Câmara de Solânea", "9/2026")
    r = web.get("/historico")
    assert r.text.count("Reabrir") == 1
    assert f'href="/pregao/{p.slug}"' in r.text


# --------------------------------------------------------------------------
# Meu catálogo
# --------------------------------------------------------------------------

def test_sanitizar_nome_de_planilha_e_deterministico_e_seguro():
    s = regras.sanitizar_nome_arquivo
    assert s("../../etc/passwd") == "passwd.xlsx"
    assert s("C:\\Users\\cliente\\Pregão 06 (Araruna).xlsx") == "Preg_o_06__Araruna_.xlsx"
    assert s("...oculto") == "oculto.xlsx"
    assert s("") == "planilha.xlsx" and s("///") == "planilha.xlsx"
    assert s("MATRIZ.XLSX") == "MATRIZ.XLSX"
    assert s("a b.xlsx") == s("a b.xlsx") == "a_b.xlsx"


def test_catalogo_mostra_texto_e_as_quatro_metricas(web):
    a = _produto("TUBO PVC 25MM", "TIGRE", almeida="123", dantas="900")
    _produto("JOELHO 90 PVC", "AMANCO", almeida="456")
    _produto("CABO FLEXIVEL 2,5MM", dantas="")
    with db.get_conn() as conn:
        db.produto_canonico_gravar(conn, a, "tubo pvc 25 mm")
    r = web.get("/catalogo")
    assert "Meu catálogo" in r.text
    assert "acelera a busca de preços nos" in r.text and "código do produto" in r.text
    assert "Re-enviar a mesma planilha não duplica nada." in r.text
    assert "Fornecedor principal destas planilhas:" in r.text
    with db.get_conn() as conn:
        assert regras.metricas_catalogo(conn) == {
            "total": 3, "com_almeida": 2, "com_dantas": 1, "padronizados": 1}
    for rotulo in ("Produtos no catálogo", "Com código Almeida", "Com código Dantas",
                   "Nomes padronizados (IA)"):
        assert rotulo in r.text


def test_catalogo_vazio_explica_o_que_fazer(web):
    assert "Catálogo vazio" in web.get("/catalogo").text


def test_catalogo_lista_e_busca_produtos_com_ultimo_preco(web):
    tubo = _produto("TUBO PVC SOLDÁVEL 25MM", "TIGRE", almeida="10234", ncm="39172300")
    _produto("CONEXÃO JOELHO 90", "AMANCO", dantas="D777")
    _cotacao(tubo, "almeida", 9.5, "2026-03-01 12:00:00")
    _cotacao(tubo, "almeida", 12.34, "2026-06-09 12:00:00")
    r = web.get("/catalogo")
    assert "TUBO PVC SOLDÁVEL 25MM" in r.text and "CONEXÃO JOELHO 90" in r.text
    assert "R$ 12,34" in r.text and "09/06/2026" in r.text and "R$ 9,50" not in r.text
    assert "39172300" in r.text and "2 produtos" in r.text
    # todas as palavras, sem acento nem maiúsculas
    r = web.get("/catalogo", params={"q": "conexao joelho"})
    assert "CONEXÃO JOELHO 90" in r.text and "TUBO PVC" not in r.text
    # por código do fornecedor e por marca
    assert "TUBO PVC" in web.get("/catalogo", params={"q": "10234"}).text
    assert "CONEXÃO" in web.get("/catalogo", params={"q": "amanco"}).text
    r = web.get("/catalogo", params={"q": "parafuso"})
    assert "Nenhum produto encontrado para “parafuso”." in r.text


def test_catalogo_pagina_os_produtos(web):
    with db.get_conn() as conn:
        conn.executemany("INSERT INTO produtos (descricao_canonica) VALUES (?)",
                         [(f"PRODUTO {i:03d}",) for i in range(120)])
    r = web.get("/catalogo")
    assert "Página 1 de 3" in r.text and "PRODUTO 000" in r.text and "PRODUTO 050" not in r.text
    r = web.get("/catalogo", params={"p": "3"})
    assert "PRODUTO 119" in r.text and "PRODUTO 099" not in r.text


def test_importar_planilhas_sem_ia_grava_o_catalogo_e_guarda_o_resumo(web):
    plan = _matriz([("PARAFUSO SEXTAVADO 1/2", "UN", "CISER", "123", "1.234,56"),
                    ("CABO FLEXIVEL 2,5MM", "M", "SIL", "456D", "sob consulta")])
    r = _enviar_planilhas(web, ("Pregão 06 (Araruna).xlsx", plan))
    assert r.status_code == 200 and r.json()["recarregar"] and r.json()["tarefa"]
    t = _esperar("importar")
    assert t.estado == "concluida"
    # nome determinístico em data/historico (chave para não duplicar)
    assert (config.HISTORICO_DIR / "Preg_o_06__Araruna_.xlsx").read_bytes() == plan
    with db.get_conn() as conn:
        produtos = {r["descricao_canonica"]: dict(r) for r in conn.execute("SELECT * FROM produtos")}
    assert produtos[_norm("PARAFUSO SEXTAVADO 1/2")]["codigo_almeida"] == "123"
    assert produtos[_norm("CABO FLEXIVEL 2,5MM")]["codigo_dantas"] == "456"   # sufixo D → Dantas
    # o resumo sobrevive a recarregar a página (fica na tarefa terminada)
    for _ in range(2):
        pagina = web.get("/catalogo").text
        assert "Importação concluída e salva." in pagina and "Etapa 1 de 2" not in pagina
        assert "1 arquivo(s) processado(s)" in pagina and "2 produto(s) novo(s)" in pagina
        assert "Avisos (1)" in pagina and "sob consulta" in pagina
        assert "R$ 1.234,56" in pagina
    # reenviar a MESMA planilha não duplica preços
    _enviar_planilhas(web, ("Pregão 06 (Araruna).xlsx", plan))
    _esperar("importar")
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM produtos").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM precos_historico").fetchone()[0] == 1
    assert "já estavam no histórico" in web.get("/catalogo").text
    assert "Últimas planilhas importadas" in web.get("/catalogo").text


def test_importar_com_chave_da_ia_roda_a_etapa_2_com_progresso(web, monkeypatch):
    credenciais.salvar_chave_api("gemini", CHAVE)
    visto = {}

    def _fake_padronizar(conn=None, *, apenas_faltantes=True, callback=None):
        callback(50, 100)
        visto["tarefa"] = tarefas.ativa("importar", None).retrato()
        return {"total": 2, "normalizados": 0, "falhos": 2, "ja_tinham": 0}

    monkeypatch.setattr(rotas_catalogo.alinhador, "normalizar_catalogo", _fake_padronizar)
    _enviar_planilhas(web, ("a.xlsx", _matriz([("TUBO PVC 25MM", "UN", "TIGRE", "1", 5)])),
                      ("b.xlsx", _matriz([("JOELHO 90", "UN", "TIGRE", "2", 3)])),
                      fornecedor="dantas")
    assert _esperar("importar").estado == "concluida"
    assert visto["tarefa"]["detalhe"] == "Etapa 2 de 2 — preparando o catálogo… 50/100"
    assert visto["tarefa"]["fracao"] == pytest.approx(0.7)
    assert visto["tarefa"]["extra"]["etapa1"] == "Etapa 1 de 2 — importação concluída e salva."
    assert "2 arquivo(s) processado(s)" in visto["tarefa"]["extra"]["resumo"]
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM produtos WHERE codigo_dantas IN ('1', '2')"
                            ).fetchone()[0] == 2
    pagina = web.get("/catalogo").text
    assert "Etapa 1 de 2 — importação concluída e salva." in pagina
    assert "Etapa 2 de 2 concluída — catálogo pronto para a busca." in pagina
    assert "2 produto(s) não foram padronizados (limite da IA ou internet)." in pagina
    assert CHAVE not in pagina


def test_importar_avisa_quando_a_padronizacao_falha_mas_mantem_a_importacao(web, monkeypatch):
    credenciais.salvar_chave_api("gemini", CHAVE)

    def _quebra(conn=None, *, apenas_faltantes=True, callback=None):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota")

    monkeypatch.setattr(rotas_catalogo.alinhador, "normalizar_catalogo", _quebra)
    _enviar_planilhas(web, ("a.xlsx", _matriz([("TUBO PVC 25MM", "UN", "TIGRE", "1", 5)])))
    assert _esperar("importar").estado == "concluida"
    pagina = web.get("/catalogo").text
    assert "A importação está salva, mas não consegui padronizar os nomes agora (" in pagina
    assert "limite de uso do dia" in pagina and "Traceback" not in pagina
    assert _norm("TUBO PVC 25MM") in pagina


def test_importar_valida_envio_e_trata_falha_do_importador(web, monkeypatch):
    r = web.post("/catalogo/importar", data={"fornecedor": "almeida"})
    assert r.status_code == 400 and "Escolha ao menos uma planilha" in r.json()["erro"]
    r = _enviar_planilhas(web, ("notas.pdf", b"%PDF"))
    assert r.status_code == 400 and "notas.pdf" in r.json()["erro"]
    r = _enviar_planilhas(web, ("a.xlsx", b"x"), fornecedor="outro")
    assert r.status_code == 400
    assert list(config.HISTORICO_DIR.iterdir()) == []         # nada gravado nas recusas

    def _quebra(caminhos, fornecedor=None):
        raise OSError("disco cheio")

    monkeypatch.setattr(rotas_catalogo.importador, "importar", _quebra)
    _enviar_planilhas(web, ("a.xlsx", _matriz([("TUBO", "UN", "", "1", 5)])))
    _esperar("importar")
    pagina = web.get("/catalogo").text
    assert "Algo deu errado ao importar as planilhas." in pagina
    assert "disco cheio" not in pagina and "Traceback" not in pagina


def test_botao_de_padronizar_so_existe_em_modo_dev(web, monkeypatch):
    _produto("TUBO PVC 25MM")
    assert "[dev]" not in web.get("/catalogo").text
    assert web.post("/catalogo/padronizar", json={}).status_code == 404
    chamadas = []
    monkeypatch.setattr(rotas_catalogo, "_DEBUG", True)
    monkeypatch.setattr(rotas_catalogo.alinhador, "normalizar_catalogo",
                        lambda **k: chamadas.append(k) or {"falhos": 0})
    assert "[dev] Padronizar 1 nomes (IA)" in web.get("/catalogo").text
    assert web.post("/catalogo/padronizar", json={}).json()["ok"]
    _esperar("padronizar")
    assert len(chamadas) == 1


# --------------------------------------------------------------------------
# Variação de preços
# --------------------------------------------------------------------------

def test_variacao_percentual():
    assert regras.variacao_pct(10, 12.5) == pytest.approx(25.0)
    assert regras.variacao_pct(8, 6) == pytest.approx(-25.0)
    assert regras.variacao_pct(5, 5) == 0
    assert regras.variacao_pct(0, 5) is None and regras.variacao_pct(None, 5) is None


def test_minigrafico_cabe_na_caixa():
    pontos = [tuple(map(float, p.split(","))) for p in
              regras.pontos_do_minigrafico([10, 20, 15]).split()]
    assert [x for x, _ in pontos] == [3.0, 48.0, 93.0]
    assert pontos[0][1] == 23.0 and pontos[1][1] == 3.0          # menor embaixo, maior em cima
    assert all(0 <= y <= 26 for _, y in pontos)
    assert regras.pontos_do_minigrafico([7, 7]) == "3.0,13.0 93.0,13.0"
    assert regras.pontos_do_minigrafico([7]) == ""


def _historico_de_precos():
    tubo = _produto("TUBO PVC 25MM", "TIGRE")
    cabo = _produto("CABO FLEXÍVEL 2,5MM", "SIL")
    lampada = _produto("LÂMPADA LED 9W", "OUROLUX")
    unico = _produto("PRODUTO DE UMA COTAÇÃO SÓ")
    _cotacao(tubo, "almeida", 10.0, "2026-01-10 12:00:00")
    _cotacao(tubo, "almeida", 11.0, "2026-03-10 12:00:00")
    _cotacao(tubo, "almeida", 12.5, "2026-06-01 12:00:00")       # +25%
    _cotacao(tubo, "dantas", 9.0, "2026-06-01 12:00:00")          # 1 só na Dantas: fora
    _cotacao(cabo, "dantas", 8.0, "2026-05-20 12:00:00")
    _cotacao(cabo, "dantas", 6.0, "2026-06-05 12:00:00")          # −25%
    _cotacao(lampada, "almeida", 4.0, "2026-05-25 12:00:00")
    _cotacao(lampada, "almeida", 4.0, "2026-06-06 12:00:00")      # estável
    _cotacao(unico, "almeida", 3.0, "2026-06-06 12:00:00")
    return tubo, cabo, lampada


def test_variacoes_agregam_primeiro_e_ultimo_preco_por_fornecedor(web):
    tubo, cabo, lampada = _historico_de_precos()
    agora = datetime(2026, 6, 10, tzinfo=timezone.utc)
    with db.get_conn() as conn:
        todas = regras.variacoes_de_preco(conn, agora=agora)
        assert [(v["produto_id"], v["fornecedor"]) for v in todas] == [
            (tubo, "almeida"), (lampada, "almeida"), (cabo, "dantas")]     # maior alta primeiro
        v = todas[0]
        assert (v["primeiro"], v["ultimo"], v["n"]) == (10.0, 12.5, 3)
        assert v["variacao"] == pytest.approx(25.0)
        assert (v["primeira_data"], v["ultima_data"]) == ("2026-01-10 12:00:00",
                                                           "2026-06-01 12:00:00")
        assert regras.resumo_variacoes(todas) == {"total": 3, "subiram": 1, "cairam": 1,
                                                  "estaveis": 1}
        quedas = regras.variacoes_de_preco(conn, ordem="queda", agora=agora)
        assert quedas[0]["produto_id"] == cabo and quedas[0]["variacao"] == pytest.approx(-25.0)
        so_dantas = regras.variacoes_de_preco(conn, fornecedor="dantas", agora=agora)
        assert [v["produto_id"] for v in so_dantas] == [cabo]
        # 30 dias: do tubo só sobra a cotação de junho (1 só → sai da lista)
        recentes = regras.variacoes_de_preco(conn, periodo="30", agora=agora)
        assert {v["produto_id"] for v in recentes} == {cabo, lampada}
        # 365 dias com o tubo: a variação é medida DENTRO do período
        ano = regras.variacoes_de_preco(conn, periodo="365", consulta="tubo tigre", agora=agora)
        assert [v["variacao"] for v in ano] == [pytest.approx(25.0)]
        assert regras.variacoes_de_preco(conn, consulta="lampada", agora=agora)[0][
            "produto_id"] == lampada                                         # sem acento
        regras.anexar_series(conn, todas)
        assert len(todas[0]["pontos"].split()) == 3


def test_tela_de_variacao_de_precos(web):
    _historico_de_precos()
    r = web.get("/precos")
    assert r.status_code == 200 and "Variação de preços" in r.text
    assert "+25,0%" in r.text and "−25,0%" in r.text and "+0,0%" in r.text
    assert "R$ 10,00" in r.text and "R$ 12,50" in r.text and "10/01/2026" in r.text
    assert r.text.count("<polyline") == 3
    assert r.text.index("TUBO PVC 25MM") < r.text.index("CABO FLEXÍVEL")
    assert "PRODUTO DE UMA COTAÇÃO SÓ" not in r.text
    r = web.get("/precos", params={"ordem": "queda", "fornecedor": "dantas"})
    assert "CABO FLEXÍVEL" in r.text and "TUBO PVC 25MM" not in r.text
    assert 'name="fornecedor" value="dantas"' in r.text       # a busca mantém os filtros
    r = web.get("/precos", params={"q": "nada disso"})
    assert "Nenhum produto com variação para estes filtros" in r.text


def test_variacao_sem_historico_explica_de_onde_vem(web):
    r = web.get("/precos")
    assert "Ainda não há histórico de preços" in r.text and "/catalogo" in r.text
    p = _produto("TUBO PVC 25MM")
    _cotacao(p, "almeida", 10.0, "2026-06-01 12:00:00")
    r = web.get("/precos")
    assert "Ainda não há produto com duas cotações do mesmo fornecedor" in r.text


def test_variacao_nasce_de_duas_importacoes_do_mesmo_produto(web):
    """Ponta a ponta: o mesmo produto em duas planilhas vira uma variação."""
    _enviar_planilhas(web, ("jan.xlsx", _matriz([("TUBO PVC 25MM", "UN", "TIGRE", "77", 10)])))
    _esperar("importar")
    _enviar_planilhas(web, ("jun.xlsx", _matriz([("TUBO PVC 25MM", "UN", "TIGRE", "77", 13)])))
    _esperar("importar")
    r = web.get("/precos")
    assert _norm("TUBO PVC 25MM") in r.text and "+30,0%" in r.text


# --------------------------------------------------------------------------
# Configurações
# --------------------------------------------------------------------------

def test_configuracoes_abas_e_subtitulo_conforme_o_modo(web, monkeypatch):
    r = web.get("/configuracoes")
    for aba in ("Almeida", "Dantas", "IA (Gemini)", "Backup", "Sistema"):
        assert f">{aba}</a>" in r.text
    assert "Tudo fica salvo apenas no seu computador, sem envio para servidores externos." in r.text
    assert "Nenhuma credencial salva. A busca de preços não funciona até configurar." in r.text
    assert "Nenhuma credencial salva. A busca não inclui Dantas até configurar." in web.get(
        "/configuracoes?aba=dantas").text
    assert 'data-aba="almeida"' in web.get("/configuracoes?aba=nao-existe").text
    monkeypatch.setattr(config, "MODO_SERVIDOR", True)
    assert "Ficam guardadas no servidor do sistema, com backup diário." in web.get(
        "/configuracoes").text


def test_salvar_credenciais_e_senha_em_branco_mantem_a_salva(web):
    r = web.post("/configuracoes/credenciais", json={"fornecedor": "almeida", "email": "",
                                                     "senha": "x"})
    assert r.status_code == 400 and r.json()["erro"] == "Preencha e-mail e senha antes de salvar."
    r = web.post("/configuracoes/credenciais", json={"fornecedor": "almeida",
                                                     "email": "op@loja.com", "senha": ""})
    assert r.status_code == 400                               # sem senha salva, em branco não vale
    painel = _painel(web.post("/configuracoes/credenciais", json={
        "fornecedor": "almeida", "email": "  op@loja.com ", "senha": SENHA}))
    assert "Credenciais da Almeida salvas com sucesso." in painel
    assert "Credencial ativa: <code>op@loja.com</code> (senha oculta)." in painel
    assert SENHA not in painel
    assert credenciais.carregar("almeida") == {"email": "op@loja.com", "senha": SENHA}
    # trocar só o e-mail: senha em branco mantém a salva
    painel = _painel(web.post("/configuracoes/credenciais", json={
        "fornecedor": "almeida", "email": "novo@loja.com", "senha": ""}))
    assert credenciais.carregar("almeida") == {"email": "novo@loja.com", "senha": SENHA}
    pagina = web.get("/configuracoes?aba=almeida").text
    assert "(senha salva — digite para trocar)" in pagina and 'name="senha" value=""' in pagina
    assert SENHA not in pagina and SENHA not in painel
    assert web.post("/configuracoes/credenciais", json={"fornecedor": "x", "email": "a",
                                                        "senha": "b"}).status_code == 400


def test_dantas_pode_usar_as_mesmas_credenciais_da_almeida(web):
    assert "Usar as mesmas credenciais da Almeida" not in web.get("/configuracoes?aba=dantas").text
    assert web.post("/configuracoes/copiar-almeida", json={}).status_code == 400
    credenciais.salvar("almeida", "op@loja.com", SENHA)
    assert "Usar as mesmas credenciais da Almeida" in web.get("/configuracoes?aba=dantas").text
    assert "Usar as mesmas credenciais" not in web.get("/configuracoes?aba=almeida").text
    painel = _painel(web.post("/configuracoes/copiar-almeida", json={}))
    assert credenciais.carregar("dantas") == {"email": "op@loja.com", "senha": SENHA}
    assert "Credencial ativa: <code>op@loja.com</code>" in painel
    assert "Usar as mesmas credenciais" not in painel and SENHA not in painel


def test_condicao_de_pagamento_salva_e_fecha_as_sessoes(web, monkeypatch):
    import pregoes.scraper as scraper
    fechamentos = []
    monkeypatch.setattr(scraper, "fechar_sessoes_vivas",
                        lambda esperar=True: fechamentos.append(esperar))
    opcoes = config.carregar_opcoes_pagamento()["almeida"]
    forma, prazo = opcoes["forma"][1], opcoes["prazo"][2]
    pagina = web.get("/configuracoes?aba=almeida").text
    assert "PIX e pagamento antecipado não têm prazo" in pagina
    assert "— Não se aplica (PIX, à vista, antecipado) —" in pagina
    assert "Prazo de Pagamento (só para boleto/cartão)" in pagina
    assert "Ainda não configurado — o sistema usa a condição <b>default</b> do site" in pagina

    painel = _painel(web.post("/configuracoes/pagamento", json={
        "fornecedor": "almeida", "forma": forma["value"], "prazo": prazo["value"]}))
    assert fechamentos == [False]                             # só agenda, não espera
    assert credenciais.carregar_preferencia_pagamento("almeida") == {
        "forma_value": forma["value"], "forma_texto": forma["texto"],
        "prazo_value": prazo["value"], "prazo_texto": prazo["texto"]}
    assert "Condição salva: <b>" in painel and "As próximas buscas usam essa condição." in painel
    assert f"Condição atual: {forma['texto']} · " in painel.replace("&#39;", "'")
    assert f'<option value="{forma["value"]}" selected>' in painel

    # sem prazo: o texto do prazo é gravado vazio
    painel = _painel(web.post("/configuracoes/pagamento", json={
        "fornecedor": "almeida", "forma": forma["value"], "prazo": ""}))
    pref = credenciais.carregar_preferencia_pagamento("almeida")
    assert (pref["prazo_value"], pref["prazo_texto"]) == ("", "")
    assert " · " not in painel.split("Condição atual:")[1].split("</span>")[0]
    assert fechamentos == [False, False]
    # valor que não está na lista é recusado e nada muda
    r = web.post("/configuracoes/pagamento", json={"fornecedor": "almeida", "forma": "inventada"})
    assert r.status_code == 400 and fechamentos == [False, False]
    # a Dantas tem as opções dela
    assert "Forma de Pagamento" in web.get("/configuracoes?aba=dantas").text


def test_chave_gemini_validacoes_e_mascara(web):
    assert "Sem chave configurada. A IA fica desligada" in web.get("/configuracoes?aba=ia").text
    r = web.post("/configuracoes/gemini", json={"chave": "  ", "acao": "salvar"})
    assert r.status_code == 400 and r.json()["erro"] == "Cole a chave antes de salvar."
    painel = _painel(web.post("/configuracoes/gemini", json={"chave": "chave-estranha-000000000",
                                                             "acao": "salvar"}))
    assert "Chave parece inválida (esperado começar com <code>AIza</code>). Salva mesmo assim" in painel
    assert credenciais.carregar_chave_api("gemini") == "chave-estranha-000000000"     # salvou
    painel = _painel(web.post("/configuracoes/gemini", json={"chave": f" {CHAVE} ",
                                                             "acao": "salvar"}))
    assert "Chave Gemini salva com sucesso." in painel
    assert credenciais.carregar_chave_api("gemini") == CHAVE
    mascara = f"{CHAVE[:8]}…{CHAVE[-4:]}"
    assert f"Chave configurada: <code>{mascara}</code>" in painel and CHAVE not in painel
    pagina = web.get("/configuracoes?aba=ia").text
    assert mascara in pagina and CHAVE not in pagina and 'name="chave" value=""' in pagina
    assert "Como obter a chave gratuita (5min)" in pagina and "Get API key" in pagina
    # em branco com chave salva: mantém
    painel = _painel(web.post("/configuracoes/gemini", json={"chave": "", "acao": "salvar"}))
    assert "a chave já salva foi mantida" in painel
    assert credenciais.carregar_chave_api("gemini") == CHAVE
    assert regras.mascarar_chave("curta") == "cu…" and regras.mascarar_chave(None) == ""


def test_salvar_e_testar_a_chave_gemini(web, monkeypatch):
    usadas = []
    respostas = iter(["OK", "ok.", "<b>não sei</b>" + "x" * 200, None])

    def _fake(chave):
        usadas.append(chave)
        return next(respostas)

    monkeypatch.setattr(rotas_config, "_perguntar_ao_gemini", _fake)
    testar = lambda chave: _painel(web.post("/configuracoes/gemini",       # noqa: E731
                                            json={"chave": chave, "acao": "testar"}))
    painel = testar(CHAVE)
    assert "Chave Gemini salva com sucesso." in painel
    assert "Conexão com Gemini funcionando." in painel
    assert "Conexão com Gemini funcionando." in testar("")    # em branco testa a chave salva
    assert usadas == [CHAVE, CHAVE]
    painel = testar(CHAVE)
    assert "Resposta inesperada: &lt;b&gt;não sei&lt;/b&gt;" in painel and "x" * 80 not in painel
    assert "Resposta inesperada: (vazio)" in testar(CHAVE)

    def _quebra(chave):
        raise PermissionError(f"API key not valid: {chave}")

    monkeypatch.setattr(rotas_config, "_perguntar_ao_gemini", _quebra)
    painel = testar(CHAVE)
    assert "Falha ao chamar Gemini: PermissionError: API key not valid:" in painel
    assert CHAVE not in painel


def test_backup_no_servidor_estado_e_fazer_agora(web, monkeypatch):
    monkeypatch.setattr(config, "MODO_SERVIDOR", True)
    estado = {"ultimo": None, "envios": 0, "ok": False}
    monkeypatch.setattr(backup, "ultimo_envio_servidor", lambda: estado["ultimo"])

    def _enviar():
        estado["envios"] += 1
        return estado["ok"]

    monkeypatch.setattr(backup, "enviar_backup_servidor", _enviar)
    pagina = web.get("/configuracoes?aba=backup").text
    assert "O sistema faz backup <b>sozinho</b>" in pagina and "últimos 30 dias" in pagina
    assert "Ainda não houve nenhum backup. O primeiro acontece esta madrugada" in pagina
    assert "Pasta de backup (Google Drive)" not in pagina

    fuso = timezone(timedelta(hours=-3))
    estado["ultimo"] = datetime(2026, 1, 5, 3, 0, tzinfo=fuso)
    pagina = web.get("/configuracoes?aba=backup").text
    assert "O último backup é de <b>05/01/2026 às 03:00</b> — há mais de 2 dias." in pagina
    recente = datetime.now(fuso) - timedelta(hours=5)
    estado["ultimo"] = recente
    pagina = web.get("/configuracoes?aba=backup").text
    assert f"Último backup: <b>{recente:%d/%m/%Y às %H:%M}</b>." in pagina

    # "Fazer backup agora" roda como tarefa; a falha aparece ao voltar à aba
    r = web.post("/configuracoes/backup", json={"acao": "agora"})
    assert r.status_code == 200 and "#painel" in r.json()["html"]
    assert _esperar("backup").resultado == {"ok": False}
    assert estado["envios"] == 1
    pagina = web.get("/configuracoes?aba=backup").text
    assert "Não consegui fazer o backup agora. Tente de novo em alguns minutos" in pagina
    estado["ok"] = True
    web.post("/configuracoes/backup", json={"acao": "agora"})
    assert _esperar("backup").resultado == {"ok": True}
    assert "Não consegui fazer o backup agora" not in web.get("/configuracoes?aba=backup").text


def test_backup_mostra_a_tarefa_em_andamento(web, monkeypatch):
    import threading
    monkeypatch.setattr(config, "MODO_SERVIDOR", True)
    monkeypatch.setattr(backup, "ultimo_envio_servidor", lambda: None)
    solta = threading.Event()
    monkeypatch.setattr(backup, "enviar_backup_servidor", lambda: solta.wait(5))
    painel = _painel(web.post("/configuracoes/backup", json={"acao": "agora"}))
    try:
        tid = tarefas.ativa("backup", None).id
        assert f'data-tarefa="{tid}"' in painel and "Fazendo o backup…" in painel
        assert "Fazer backup agora</button>" not in painel   # não dá para pedir dois
        assert f'data-tarefa="{tid}"' in web.get("/configuracoes?aba=backup").text
    finally:
        solta.set()
    _esperar("backup")


def test_backup_local_pasta_e_fazer_agora(web, tmp_path):
    pagina = web.get("/configuracoes?aba=backup").text
    assert "fica só neste computador" in pagina and "Como preparar (uma vez)" in pagina
    assert "Pasta de backup (Google Drive)" in pagina and "Meu Drive" in pagina
    salvar = lambda pasta, acao="salvar": web.post(                        # noqa: E731
        "/configuracoes/backup", json={"pasta": pasta, "acao": acao})
    destino = tmp_path / "drive"
    assert "Pasta salva, mas <b>não encontrei</b> ela no computador agora." in _painel(
        salvar(str(destino)))
    assert credenciais.carregar_pasta_backup() == str(destino)
    destino.mkdir()
    assert "Pasta de backup salva. A partir de agora o sistema guarda uma cópia lá sozinho." \
        in _painel(salvar(f"  {destino} "))
    assert "Backup automático desligado (pasta em branco)." in _painel(salvar(""))
    assert credenciais.carregar_pasta_backup() is None

    # "Fazer backup agora" grava o caminho do campo antes de copiar
    _painel(salvar(str(destino), "agora"))
    assert _esperar("backup").resultado == {"ok": True, "arquivo": backup.NOME_BACKUP_EXTERNO}
    assert credenciais.carregar_pasta_backup() == str(destino)
    assert (destino / backup.NOME_BACKUP_EXTERNO).is_file()
    pagina = web.get("/configuracoes?aba=backup").text
    assert f"Backup feito: <code>{backup.NOME_BACKUP_EXTERNO}</code> na pasta indicada." in pagina
    # pasta que não existe: falha clara
    _painel(salvar(str(tmp_path / "nao-existe"), "agora"))
    assert _esperar("backup").resultado == {"ok": False, "arquivo": None}
    assert "Não consegui gravar o backup. Verifique se o <b>Google Drive está aberto</b>" \
        in web.get("/configuracoes?aba=backup").text


def test_aba_sistema_mostra_fatos_e_o_fim_do_registro(web):
    web.pregao()
    credenciais.salvar("almeida", "op@loja.com", SENHA)
    credenciais.salvar_chave_api("gemini", CHAVE)
    linhas = [f"2026-11-15 10:00:{i:02d},000 [INFO] pregoes.web: linha {i}" for i in range(60)]
    linhas += ["2026-11-15 10:02:00,000 [WARNING] pregoes.scraper: site lento",
               "2026-11-15 10:03:00,000 [ERROR] pregoes.scraper: falha no login",
               "Traceback (most recent call last):",
               f"  senha usada: {SENHA} chave {CHAVE}",
               "2026-11-15 10:04:00,000 [INFO] pregoes.web: voltou ao normal"]
    (config.DATA_DIR / "pregoes.log").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    r = web.get("/configuracoes?aba=sistema")
    assert r.status_code == 200
    assert config.VERSAO_APP in r.text and str(config.DATA_DIR) in r.text
    assert "Pregões em andamento guardados" in r.text and "Banco de dados" in r.text
    assert "pasta de backup não configurada" in r.text
    assert "últimas 50 linhas" in r.text
    assert "linha 59" in r.text and "linha 14" not in r.text          # só as 50 últimas
    assert '<div class="l alerta">2026-11-15 10:02:00,000 [WARNING]' in r.text
    assert '<div class="l erro">2026-11-15 10:03:00,000 [ERROR]' in r.text
    assert '<div class="l erro">Traceback' in r.text                  # continuação herda o nível
    assert '<div class="l ">2026-11-15 10:04:00,000 [INFO]' in r.text
    assert "senha usada: ••• chave •••" in r.text
    assert SENHA not in r.text and CHAVE not in r.text


def test_registro_inexistente_e_tamanhos(web):
    assert "Nenhum registro ainda" in web.get("/configuracoes?aba=sistema").text
    assert regras.tamanho_legivel(None) == "—"
    assert regras.tamanho_legivel(512) == "512 bytes"
    assert regras.tamanho_legivel(1536) == "1,5 KB"
    assert regras.tamanho_legivel(5 * 1024 * 1024) == "5,0 MB"


def test_nenhuma_tela_devolve_senha_ou_chave_salva(web, monkeypatch):
    """Segredo salvo nunca volta ao navegador — em nenhuma tela, nos dois modos."""
    credenciais.salvar("almeida", "almeida@loja.com", SENHA)
    credenciais.salvar("dantas", "dantas@loja.com", SENHA + "Dantas")
    credenciais.salvar_chave_api("gemini", CHAVE)
    (config.DATA_DIR / "pregoes.log").write_text(
        f"2026-11-15 10:00:00,000 [ERROR] pregoes.scraper: login {SENHA}Dantas / {CHAVE}\n",
        encoding="utf-8")
    eid = _edital(planilhas=[("p.xlsx", 3, 2, True)])
    _produto("TUBO PVC 25MM")
    enderecos = ["/historico", f"/historico/{eid}/editar", "/catalogo", "/precos",
                 "/configuracoes"] + [f"/configuracoes?aba={a}" for a, _ in rotas_config.ABAS]
    for servidor in (False, True):
        monkeypatch.setattr(config, "MODO_SERVIDOR", servidor)
        monkeypatch.setattr(backup, "ultimo_envio_servidor", lambda: None)
        for endereco in enderecos:
            r = web.get(endereco)
            assert r.status_code == 200, endereco
            assert SENHA not in r.text and CHAVE not in r.text, endereco
    assert "almeida@loja.com" in web.get("/configuracoes?aba=almeida").text   # o e-mail pode
