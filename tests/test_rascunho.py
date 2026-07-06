"""Rascunho anti-apagão: autosave do pregão em andamento + recuperação.

O que protege: itens, BUSCA DE PREÇOS, seleções e markup — tudo que vive só na
memória até gerar a planilha. Estes testes travam a persistência (round-trip
idêntico, escrita atômica, blindagem contra corrupção)."""
from __future__ import annotations

from pregoes import rascunho


def test_salvar_carregar_round_trip(tmp_path):
    p = tmp_path / "r.pkl"
    estado = {"itens": [1, 2, 3], "selecoes": {0: "x"}, "orgao": "Prefeitura"}
    rascunho.salvar(estado, p)
    assert rascunho.carregar(p) == estado


def test_carregar_inexistente_e_none(tmp_path):
    assert rascunho.carregar(tmp_path / "nao_existe.pkl") is None


def test_carregar_corrompido_e_none(tmp_path):
    """Apagão no meio da gravação / arquivo lixo → None, SEM levantar (o boot
    nunca pode travar por causa do rascunho)."""
    p = tmp_path / "r.pkl"
    p.write_bytes(b"isso nao e um pickle \x00\x01\x02")
    assert rascunho.carregar(p) is None


def test_versao_incompativel_e_none(tmp_path, monkeypatch):
    """Snapshot de um formato antigo (dataclass mudou) é descartado, não quebra."""
    p = tmp_path / "r.pkl"
    rascunho.salvar({"a": 1}, p)
    monkeypatch.setattr(rascunho, "_VERSAO", 999)
    assert rascunho.carregar(p) is None


def test_estado_vazio_nao_conta_como_rascunho(tmp_path):
    p = tmp_path / "r.pkl"
    rascunho.salvar({}, p)
    assert rascunho.carregar(p) is None
    assert rascunho.existe(p) is False


def test_existe(tmp_path):
    p = tmp_path / "r.pkl"
    assert rascunho.existe(p) is False
    rascunho.salvar({"x": 1}, p)
    assert rascunho.existe(p) is True


def test_limpar_idempotente(tmp_path):
    p = tmp_path / "r.pkl"
    rascunho.salvar({"x": 1}, p)
    rascunho.limpar(p)
    assert not p.exists()
    rascunho.limpar(p)   # de novo: não levanta


def test_escrita_atomica_nao_deixa_temp_orfao(tmp_path):
    p = tmp_path / "r.pkl"
    rascunho.salvar({"x": 1}, p)
    assert not list(tmp_path.glob("*.tmp")), "temp órfão ficou na pasta"


def test_round_trip_objetos_reais_do_pregao(tmp_path):
    """O que de fato vai no snapshot: itens, resultados da busca e seleções —
    restaurados IDÊNTICOS (é o que torna o motor 'igual à primeira busca')."""
    from pregoes.exportador import SelecaoFornecedor
    from pregoes.extrator import ItemExtraido
    from pregoes.scraper import ResultadoBusca
    p = tmp_path / "r.pkl"
    estado = {
        "itens_edital": [ItemExtraido(item_numero=1, descricao="LUVA", quantidade=2.0)],
        "resultados_busca": {0: [ResultadoBusca(
            descricao_encontrada="LUVA 50", marca="M", preco=8.32,
            codigo_fornecedor="9185", confianca="exato", fornecedor="dantas")]},
        "selecoes": {0: SelecaoFornecedor(preco=8.32, codigo_fornecedor="9185",
                                          marca="M", fornecedor="dantas")},
        "itens_nao_usar": {2}, "markup_overrides": {1: 1.6},
    }
    rascunho.salvar(estado, p)
    v = rascunho.carregar(p)
    assert v["itens_edital"][0].descricao == "LUVA"
    assert v["resultados_busca"][0][0].preco == 8.32
    assert v["resultados_busca"][0][0].fornecedor == "dantas"
    assert v["selecoes"][0].codigo_fornecedor == "9185"
    assert v["itens_nao_usar"] == {2}
    assert v["markup_overrides"] == {1: 1.6}


def test_salvar_sobrescreve_e_continua_valido(tmp_path):
    """Autosave grava várias vezes — a última vale e nunca corrompe."""
    p = tmp_path / "r.pkl"
    rascunho.salvar({"v": 1}, p)
    rascunho.salvar({"v": 2}, p)
    rascunho.salvar({"v": 3}, p)
    assert rascunho.carregar(p) == {"v": 3}


# === Multi-pregão: um rascunho por (órgão, número) ===========================

def test_slug_normaliza_identidade():
    assert rascunho.slug_de("Prefeitura de Araruna", "00012/2026") == \
        "00012-2026-prefeitura-de-araruna"
    assert rascunho.slug_de("SÃO JOSÉ", "7") == "7-sao-jose"     # sem acento
    assert rascunho.slug_de(None, None) == "sem-identificacao"
    assert rascunho.slug_de("", "  ") == "sem-identificacao"


def test_salvar_e_listar_varios_pregoes(tmp_path):
    d = tmp_path / "rascunhos"
    s1 = rascunho.salvar_pregao(
        {"orgao": "A", "numero": "1", "itens_edital": [1, 2, 3],
         "resultados_busca": {0: ["x"]}}, d)
    s2 = rascunho.salvar_pregao(
        {"orgao": "B", "numero": "2", "itens_revisados": [1]}, d)
    assert s1 != s2
    lista = rascunho.listar_pregoes(d)
    por_num = {p["numero"]: p for p in lista}
    assert set(por_num) == {"1", "2"}
    assert por_num["1"]["n_itens"] == 3 and por_num["1"]["tem_busca"] is True
    assert por_num["2"]["n_itens"] == 1 and por_num["2"]["tem_busca"] is False
    assert rascunho.carregar_pregao(s1, d)["orgao"] == "A"


def test_remover_pregao_nao_toca_nos_outros(tmp_path):
    d = tmp_path / "rascunhos"
    s1 = rascunho.salvar_pregao({"orgao": "A", "numero": "1", "itens_edital": [1]}, d)
    rascunho.salvar_pregao({"orgao": "B", "numero": "2", "itens_edital": [1]}, d)
    rascunho.remover_pregao(s1, d)
    assert [p["numero"] for p in rascunho.listar_pregoes(d)] == ["2"]
    rascunho.remover_pregao(s1, d)   # idempotente


def test_listar_pula_arquivo_corrompido(tmp_path):
    d = tmp_path / "rascunhos"
    rascunho.salvar_pregao({"orgao": "A", "numero": "1", "itens_edital": [1]}, d)
    (d / "podre.pkl").write_bytes(b"\x00nao-e-pickle")
    lista = rascunho.listar_pregoes(d)   # não levanta
    assert [p["numero"] for p in lista] == ["1"]


def test_listar_migra_formato_legado(tmp_path, monkeypatch):
    """Slot único antigo (data/rascunho.pkl) vira arquivo por pregão no 1º
    boot após a atualização — o apagão da versão anterior não se perde."""
    legado = tmp_path / "rascunho.pkl"
    monkeypatch.setattr(rascunho, "CAMINHO", legado)
    rascunho.salvar({"orgao": "Velha", "numero": "9", "itens_edital": [1, 2]},
                    legado)
    d = tmp_path / "rascunhos"
    lista = rascunho.listar_pregoes(d)
    assert [p["numero"] for p in lista] == ["9"]      # migrou
    assert not legado.exists()                        # legado consumido
    assert rascunho.listar_pregoes(d)[0]["n_itens"] == 2   # 2ª chamada estável
