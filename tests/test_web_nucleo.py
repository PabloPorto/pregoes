"""Interface web — núcleo: início, novo pregão, leitura do PDF, tarefas e a
compatibilidade do estado com os rascunhos do Streamlit."""
from __future__ import annotations

import time

from pregoes import rascunho
from pregoes.web import tarefas
from pregoes.web.pregao import Pregao, em_andamento, slug_valido


def test_telas_basicas_abrem(web):
    for caminho in ("/", "/pregoes", "/novo", "/historico", "/catalogo", "/configuracoes"):
        r = web.get(caminho)
        assert r.status_code == 200, caminho
        assert "Pregões" in r.text


def test_endereco_inexistente_e_pregao_inexistente_dao_404(web):
    assert web.get("/nao-existe").status_code == 404
    assert web.get("/pregao/nao-existe/revisao", follow_redirects=False).status_code == 404


def test_slug_da_url_e_validado_antes_de_virar_caminho():
    assert slug_valido("20-2026-catole-9c433c81")
    for ruim in ("", "../../etc/passwd", "a/b", "A" * 3, "x" * 81, "a.b"):
        assert not slug_valido(ruim)
        assert Pregao.abrir(ruim) is None


def test_novo_pregao_exige_orgao_numero_e_pdf(web):
    r = web.post("/novo", data={"orgao": "", "numero": "1/2026"},
                 files={"pdf": ("e.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert r.status_code == 400 and "órgão" in r.json()["erro"]
    r = web.post("/novo", data={"orgao": "Prefeitura", "numero": "1/2026"},
                 files={"pdf": ("e.txt", b"nao sou pdf", "text/plain")})
    assert r.status_code == 400 and "PDF" in r.json()["erro"]
    assert em_andamento() == []


def test_novo_pregao_cria_rascunho_e_dispara_a_leitura(web, monkeypatch):
    from pregoes.extrator import ItemExtraido, ResultadoExtracao
    from pregoes.web import servicos

    def _fake(pdf, callback_progresso_llm=None):
        res = ResultadoExtracao(arquivo=str(pdf))
        res.itens = [ItemExtraido(item_numero=1, descricao="TUBO PVC 25MM", unidade="UND",
                                  quantidade=10.0, confianca="extraido"),
                     ItemExtraido(item_numero=2, descricao="LINHA ESTRANHA", confianca="duvidoso")]
        res.confianca_global = 0.97
        return res

    monkeypatch.setattr(servicos.extrator, "extrair_com_pipeline", _fake)
    r = web.post("/novo", data={"orgao": "Prefeitura de Araruna", "numero": "6/2026"},
                 files={"pdf": ("edital.pdf", b"%PDF-1.4 conteudo", "application/pdf")})
    assert r.status_code == 200
    destino = r.json()["ir"]
    slug = destino.split("/")[2]
    for _ in range(100):                       # a leitura roda em outra thread
        if not tarefas.ativa("leitura", slug):
            break
        time.sleep(0.02)
    p = Pregao.abrir(slug)
    assert p.titulo == "Pregão 6/2026 — Prefeitura de Araruna"
    assert len(p.itens_edital) == 2
    assert p.mapa_busca_edital == [0]          # o duvidoso nasce desmarcado
    assert [i.descricao for i in p.itens_revisados] == ["TUBO PVC 25MM"]
    assert p.tela_de_trabalho() == "revisao"
    # a tela de leitura, pronta, manda para a revisão
    assert web.get(destino, follow_redirects=False).headers["location"].endswith("/revisao")


def test_rascunho_do_streamlit_abre_igual(web):
    """As chaves gravadas são as mesmas do Streamlit: um rascunho de lá abre
    aqui, e o que é salvo aqui continua legível por `rascunho.listar_pregoes`."""
    p = web.pregao(n_itens=4)
    estado = rascunho.carregar_pregao(p.slug)
    assert {"orgao", "numero", "pdf_path", "extracao", "itens_edital", "itens_revisados",
            "mapa_busca_edital", "tela"} <= set(estado)
    assert estado["tela"] == "revisao"
    lista = rascunho.listar_pregoes()
    assert lista[0]["slug"] == p.slug and lista[0]["n_itens"] == 4


def test_descartar_pregao_apaga_o_rascunho(web):
    p = web.pregao()
    assert web.post(f"/pregao/{p.slug}/descartar", json={}).json()["ok"]
    assert Pregao.abrir(p.slug) is None


def test_tarefa_registra_progresso_erro_e_nao_duplica(web):
    import threading
    solta = threading.Event()

    def _lenta(t):
        t.atualizar(0.4, "no meio")
        solta.wait(5)
        return 7

    t1 = tarefas.iniciar("busca", "abc", "Busca", _lenta)
    t2 = tarefas.iniciar("busca", "abc", "Busca", _lenta)
    assert t1 is t2                               # 2 cliques = 1 tarefa
    r = web.get(f"/api/tarefas/{t1.id}").json()
    assert r["estado"] == "rodando" and r["fracao"] == 0.4 and r["detalhe"] == "no meio"
    solta.set()
    for _ in range(100):
        if t1.estado != "rodando":
            break
        time.sleep(0.02)
    assert t1.estado == "concluida" and t1.resultado == 7

    def _quebra(t):
        raise RuntimeError("explodiu")

    t3 = tarefas.iniciar("leitura", "abc", "Leitura", _quebra)
    for _ in range(100):
        if t3.estado != "rodando":
            break
        time.sleep(0.02)
    r = web.get(f"/api/tarefas/{t3.id}").json()
    assert r["estado"] == "erro" and r["erro"] and "Traceback" not in r["erro"]
    assert web.get("/api/tarefas/naoexiste").json()["estado"] == "erro"
