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
