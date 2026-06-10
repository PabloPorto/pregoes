"""
Testes do primeiro lote da Fase 2 (normalizador, cache de preços, contrato
do scraper). Os scrapers reais ainda não existem — os testes aqui cobrem
só o que já roda sem rede.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from pregoes.db import init_db, get_conn, preco_em_cache, gravar_preco
from pregoes.normalizador import normalizar_descricao, keywords, relevancia
from pregoes.scraper import (
    AlmeidaDistribuidor,
    DantasDistribuidora,
    Fornecedor,
    ResultadoBusca,
    _parse_card_attributes,
    buscar_precos_para_itens,
)
from pregoes import credenciais
from pregoes.extrator import ItemExtraido


# === Normalizador ===

@pytest.mark.parametrize("entrada,esperado", [
    ("1) ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND",
     "abracadeira de nylon 100 x 2.5 pct c/100und"),
    # Vírgula decimal entre dígitos vira ponto; vírgulas separadoras viram espaço
    ("14) ACO CA–50, ½, VERGALHAO C/ 12M SUPERFÍCIE LISA",
     "aco ca-50 1/2 vergalhao c/ 12m superficie lisa"),
    ("10) ACO CA-50, 12,5 MM, VERGALHAO",
     "aco ca-50 12.5mm vergalhao"),   # nº+unidade colados (preventivo do espaço)
    ("VÁLVULA  DE  RETENÇÃO(SUCÇÃO)\n32MM",
     "valvula de retencao(succao) 32mm"),
    ("  ÁCIDO   MURIÁTICO  ", "acido muriatico"),
    ("", ""),
    (None, ""),
])
def test_normalizar_descricao(entrada, esperado):
    assert normalizar_descricao(entrada) == esperado


def test_keywords_descarta_ruido():
    """Stopwords pt-BR ('de', 'para'), unidades sozinhas ('mm', 'cm'),
    números puros ('100', '12.5', '2,5') e tokens curtos saem.
    Sobram os tokens distintivos de busca."""
    kws = keywords("1) ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND")
    assert "abracadeira" in kws
    assert "nylon" in kws
    assert "de" not in kws
    assert "100" not in kws
    assert "2.5" not in kws

    # Caso vergalhão: stopwords + unidades soltas + decimais saem
    kws2 = keywords("10) ACO CA-50, 12,5 MM, VERGALHAO C/ 12M SUPERFÍCIE NERVURADA")
    assert "aco" in kws2
    assert "ca-50" in kws2
    assert "vergalhao" in kws2
    assert "nervurada" in kws2
    assert "mm" not in kws2  # unidade solta
    assert "12.5" not in kws2  # decimal puro


def test_keywords_vazio():
    assert keywords("") == []
    assert keywords(None) == []


def test_unidade_separada_do_numero_e_colada():
    """Preventivo do espaço número-unidade: '500 L' (separado) deve normalizar
    IGUAL a '500L' (colado), preservando a medida como token distintivo. Sem
    isto, a unidade solta é descartada e leva o número junto → query genérica
    ('caixa agua') que casa errado/alucina."""
    assert normalizar_descricao("CAIXA D AGUA 500 L") == \
           normalizar_descricao("CAIXA D AGUA 500L") == "caixa d agua 500l"
    # A medida sobrevive como keyword distintiva nos dois casos.
    assert keywords("CAIXA D AGUA 500 L") == keywords("CAIXA D AGUA 500L")
    assert "500l" in keywords("CAIXA D AGUA 500 L")
    # Outras unidades comuns no domínio elétrico/hidráulico.
    assert normalizar_descricao("CABO 2,5 MM 750 V") == "cabo 2.5mm 750v"
    assert normalizar_descricao("LAMPADA LED 9 W") == "lampada led 9w"
    # NÃO cola unidade de embalagem (o número antes é a bitola, não a contagem).
    assert normalizar_descricao("NYLON 100 X 2.5 PCT") == "nylon 100 x 2.5 pct"


# === Relevância ===

def test_relevancia_match_alto_quando_palavras_chave_coincidem():
    """Caso real: ABRAÇADEIRA NYLON do edital casa com card da Almeida."""
    score = relevancia(
        "1) ABRAÇADEIRA DE NYLON 100 X 2.5 PCT C/100UND",
        "ABRAÇADEIRA DE NYLON FOXLUX BRANCA 2.5X10CM C/100",
    )
    assert score >= 0.5  # 'abracadeira', 'nylon' coincidem


def test_relevancia_zero_quando_categorias_diferentes():
    """Caso visto na UI: vergalhão do edital vs corrente soldada do site.
    Nada em comum nas keywords — score baixo, deve ser descartado."""
    score = relevancia(
        "10) ACO CA-50, 12,5 MM, VERGALHAO C/ 12M SUPERFÍCIE NERVURADA",
        "CORRENTE SOLDADA SAO RAPHAEL 6.5MM-1/4 SACO C/12,5KG",
    )
    assert score < 0.5  # nenhuma das kws distintivas (aco, ca-50, vergalhao, nervurada) bate


def test_relevancia_lida_com_vazio():
    assert relevancia("", "qualquer coisa") == 0.0
    assert relevancia("qualquer coisa", "") == 0.0
    assert relevancia(None, None) == 0.0


# === Cache de preços ===

def _setup_produto(conn) -> int:
    """Cria um produto fake e retorna o id."""
    cursor = conn.execute(
        "INSERT INTO produtos (descricao_canonica, marca) VALUES (?, ?)",
        ("ABRACADEIRA NYLON 100", "Tramontina"),
    )
    return cursor.lastrowid


def test_cache_miss_quando_vazio(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    with get_conn(db) as conn:
        produto_id = _setup_produto(conn)
        assert preco_em_cache(conn, produto_id, "almeida") is None


def test_cache_hit_dentro_do_ttl(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    with get_conn(db) as conn:
        produto_id = _setup_produto(conn)
        gravar_preco(conn, produto_id, "almeida", preco=12.34, unidade="un")
        row = preco_em_cache(conn, produto_id, "almeida")
        assert row is not None
        assert row["preco"] == 12.34
        assert row["fornecedor"] == "almeida"


def test_cache_miss_apos_ttl_expirar(tmp_path):
    """Insere um registro com consultado_em forçado para 3 dias atrás e
    confirma que preco_em_cache não o retorna (TTL padrão = 2 dias)."""
    db = tmp_path / "t.db"
    init_db(db)
    tres_dias_atras = (datetime.utcnow() - timedelta(days=3)).isoformat(sep=" ")
    with get_conn(db) as conn:
        produto_id = _setup_produto(conn)
        conn.execute(
            """INSERT INTO precos_historico
               (produto_id, fornecedor, preco, consultado_em)
               VALUES (?, ?, ?, ?)""",
            (produto_id, "almeida", 99.99, tres_dias_atras),
        )
        assert preco_em_cache(conn, produto_id, "almeida") is None


def test_cache_separado_por_fornecedor(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    with get_conn(db) as conn:
        produto_id = _setup_produto(conn)
        gravar_preco(conn, produto_id, "almeida", preco=10.0)
        # Mesmo produto, fornecedor diferente: deve ser cache miss
        assert preco_em_cache(conn, produto_id, "dantas") is None
        assert preco_em_cache(conn, produto_id, "almeida") is not None


# === Contrato do scraper ===

def test_classes_concretas_cumprem_protocolo():
    """Garantir que Almeida e Dantas têm `nome` e `buscar` — se isso quebrar
    o resto do pipeline já quebra junto."""
    almeida = AlmeidaDistribuidor(email="x@y", senha="z")
    dantas = DantasDistribuidora(email="x@y", senha="z")
    assert almeida.nome == "almeida"
    assert dantas.nome == "dantas"
    # Tipo estrutural: ambas devem satisfazer o Protocol em runtime,
    # via duck typing (Python não checa Protocol em runtime sem @runtime_checkable).
    assert hasattr(almeida, "buscar")
    assert hasattr(dantas, "buscar")
    # Pool size default — mesmo nas duas
    assert almeida._pool_size == dantas._pool_size


def test_parse_card_dantas_caso_real():
    """Estrutura real observada na inspeção autônoma de 2026-05-27.
    Dantas usa exatamente o mesmo padrão data-* do Almeida no
    `.product-card__info[data-codigo-produto]`."""
    from pregoes.scraper import _parse_card_dantas

    attrs = {
        "data-codigo-produto": "41921",
        "data-nome": "PARAFUSO NOVA FIXACOES P/TELHA FERRO ZINCADO BRANCO 85MM",
        "data-preco": "63.18",
        "data-marca": "NOVA FIXACOES",
    }
    r = _parse_card_dantas(attrs, confianca="exato")
    assert r is not None
    assert r.fornecedor == "dantas"
    assert r.codigo_fornecedor == "41921"
    assert r.preco == 63.18
    assert r.marca == "NOVA FIXACOES"


def test_parse_card_dantas_descarta_invalido():
    """Mesma lógica conservadora do Almeida: faltou campo crítico → None."""
    from pregoes.scraper import _parse_card_dantas

    assert _parse_card_dantas({}, confianca="exato") is None
    assert _parse_card_dantas(
        {"data-codigo-produto": "1", "data-nome": "X", "data-preco": "0"},
        confianca="exato",
    ) is None  # preço zero


def test_fornecedor_mockado_funciona_via_protocol():
    """Um fornecedor falso que cumpre o Protocol deve passar pelo type system
    estrutural — é como vamos mockar nos testes da implementação real."""

    class FornecedorFake:
        nome = "fake"

        def buscar(self, descricao: str) -> list[ResultadoBusca]:
            return [
                ResultadoBusca(
                    descricao_encontrada=descricao,
                    marca="MARCA X",
                    preco=42.0,
                    codigo_fornecedor="ABC123",
                    confianca="exato",
                    fornecedor="fake",
                )
            ]

    fake: Fornecedor = FornecedorFake()  # checagem estática vale para mypy
    resultados = fake.buscar("ABRAÇADEIRA")
    assert len(resultados) == 1
    assert resultados[0].marca == "MARCA X"
    assert resultados[0].confianca == "exato"


# === Parse de card data-* (Almeida) — função pura ===

def test_parse_card_attributes_caso_real():
    """Atributos vistos na inspeção 2026-05-15 (.product-card__info)."""
    attrs = {
        "data-codigo-produto": "11656",
        "data-nome": "ABRAÇADEIRA DE NYLON FOXLUX BRANCA 2.5X10CM C/100 ",
        "data-preco": "3.18",
        "data-marca": "FOXLUX",
    }
    r = _parse_card_attributes(attrs, confianca="exato")
    assert r is not None
    assert r.codigo_fornecedor == "11656"
    assert r.descricao_encontrada == "ABRAÇADEIRA DE NYLON FOXLUX BRANCA 2.5X10CM C/100"
    assert r.marca == "FOXLUX"
    assert r.preco == 3.18
    assert r.confianca == "exato"
    assert r.fornecedor == "almeida"


def test_parse_card_attributes_preco_com_virgula():
    """Algum card pode vir com vírgula brasileira em vez de ponto — o parser aceita."""
    r = _parse_card_attributes(
        {"data-codigo-produto": "1", "data-nome": "X",
         "data-preco": "12,34", "data-marca": "Y"},
        confianca="variacao",
    )
    assert r is not None and r.preco == 12.34


@pytest.mark.parametrize("attrs_faltando", [
    {"data-nome": "X", "data-preco": "1.0", "data-marca": "Y"},               # sem código
    {"data-codigo-produto": "1", "data-preco": "1.0", "data-marca": "Y"},     # sem nome
    {"data-codigo-produto": "1", "data-nome": "X", "data-marca": "Y"},        # sem preço
    {"data-codigo-produto": "1", "data-nome": "X", "data-preco": "abc"},      # preço inválido
    {"data-codigo-produto": "1", "data-nome": "X", "data-preco": "0"},        # preço zero
    {"data-codigo-produto": "1", "data-nome": "X", "data-preco": "-5"},       # preço negativo
    {},                                                                       # tudo vazio
])
def test_parse_card_attributes_descarta_invalidos(attrs_faltando):
    assert _parse_card_attributes(attrs_faltando, "exato") is None


def test_parse_card_attributes_marca_vazia_ok():
    """Marca em branco não é motivo para descartar — o operador escolhe na UI."""
    r = _parse_card_attributes(
        {"data-codigo-produto": "9", "data-nome": "X",
         "data-preco": "5.0", "data-marca": ""},
        confianca="exato",
    )
    assert r is not None
    assert r.marca == ""


# === Módulo credenciais ===

def test_credenciais_round_trip(tmp_path, monkeypatch):
    """Salvar + carregar volta o que foi salvo."""
    fake_path = tmp_path / "credenciais.json"
    monkeypatch.setattr(credenciais, "CAMINHO", fake_path)

    assert credenciais.carregar("almeida") is None  # vazio antes
    credenciais.salvar("almeida", "user@x.com", "senha123")
    cred = credenciais.carregar("almeida")
    assert cred == {"email": "user@x.com", "senha": "senha123"}


def test_credenciais_preserva_outros_fornecedores(tmp_path, monkeypatch):
    """Salvar Dantas não pode apagar Almeida (e vice-versa)."""
    fake_path = tmp_path / "credenciais.json"
    monkeypatch.setattr(credenciais, "CAMINHO", fake_path)

    credenciais.salvar("almeida", "a@x.com", "s1")
    credenciais.salvar("dantas", "d@y.com", "s2")
    assert credenciais.carregar("almeida")["email"] == "a@x.com"
    assert credenciais.carregar("dantas")["email"] == "d@y.com"


def test_credenciais_remover(tmp_path, monkeypatch):
    fake_path = tmp_path / "credenciais.json"
    monkeypatch.setattr(credenciais, "CAMINHO", fake_path)
    credenciais.salvar("almeida", "a@x.com", "s1")
    credenciais.remover("almeida")
    assert credenciais.carregar("almeida") is None


def test_credenciais_arquivo_corrompido_retorna_none(tmp_path, monkeypatch):
    """Se o JSON for inválido (cliente editou na mão e estragou), o sistema
    deve tratar como 'sem credencial' em vez de explodir."""
    fake_path = tmp_path / "credenciais.json"
    fake_path.write_text("isso não é json {{{")
    monkeypatch.setattr(credenciais, "CAMINHO", fake_path)
    assert credenciais.carregar("almeida") is None


# === Orquestrador buscar_precos_para_itens ===

class _FornecedorFake:
    """Fornecedor mockado: retorna resultados conforme dict pré-configurado."""
    def __init__(self, nome: str, mapa: dict[str, list[ResultadoBusca]],
                 raise_para: set[str] | None = None):
        self.nome = nome
        self._mapa = mapa
        self._raise_para = raise_para or set()
        self.chamadas = []

    def buscar(self, descricao: str) -> list[ResultadoBusca]:
        self.chamadas.append(descricao)
        if descricao in self._raise_para:
            raise RuntimeError(f"falha simulada em {descricao!r}")
        return self._mapa.get(descricao, [])


def _r(descricao, marca, preco, codigo="X", confianca="exato", fornecedor="fake"):
    return ResultadoBusca(descricao, marca, preco, codigo, confianca, fornecedor)


def test_buscar_precos_chama_callback_em_cada_item():
    itens = [ItemExtraido(descricao=d) for d in ("AAA", "BBB", "CCC")]
    forn = _FornecedorFake("fake", mapa={"AAA": [_r("AAA", "MX", 1.0)]})
    chamadas = []

    def cb(atual, total, msg):
        chamadas.append((atual, total, msg))

    lote = buscar_precos_para_itens(itens, [forn], progresso_callback=cb,
                                     usar_cache=False)
    assert chamadas == [(1, 3, "AAA"), (2, 3, "BBB"), (3, 3, "CCC")]
    assert len(lote.resultados) == 3
    assert len(lote.resultados[0]) == 1 and lote.resultados[0][0].marca == "MX"
    assert lote.resultados[1] == [] and lote.resultados[2] == []
    assert lote.erros == []


def test_buscar_precos_concatena_de_multiplos_fornecedores():
    itens = [ItemExtraido(descricao="ABC")]
    f1 = _FornecedorFake("almeida", {"ABC": [_r("ABC", "M1", 10.0)]})
    f2 = _FornecedorFake("dantas", {"ABC": [_r("ABC", "M2", 11.0)]})
    lote = buscar_precos_para_itens(itens, [f1, f2], usar_cache=False)
    assert len(lote.resultados[0]) == 2
    marcas = {r.marca for r in lote.resultados[0]}
    assert marcas == {"M1", "M2"}


def test_buscar_precos_segue_apos_falha_isolada_e_registra_erro():
    """Se um fornecedor falha em UM item, o batch continua, o item fica
    vazio em `resultados` E o erro entra em `erros`."""
    itens = [ItemExtraido(descricao=d) for d in ("OK1", "FAIL", "OK2")]
    forn = _FornecedorFake("fake",
                           mapa={"OK1": [_r("OK1", "M", 1.0)],
                                 "OK2": [_r("OK2", "M", 2.0)]},
                           raise_para={"FAIL"})
    lote = buscar_precos_para_itens(itens, [forn], usar_cache=False)
    assert len(lote.resultados[0]) == 1
    assert lote.resultados[1] == []  # falha não derrubou o batch
    assert len(lote.resultados[2]) == 1
    assert forn.chamadas == ["OK1", "FAIL", "OK2"]
    # Erro foi registrado com contexto suficiente para a UI mostrar
    assert len(lote.erros) == 1
    assert lote.erros[0].indice_item == 1
    assert lote.erros[0].fornecedor == "fake"
    assert "FAIL" in lote.erros[0].descricao_resumo
    assert "RuntimeError" in lote.erros[0].mensagem


def test_buscar_precos_lista_vazia_de_itens():
    forn = _FornecedorFake("fake", {})
    lote = buscar_precos_para_itens([], [forn], usar_cache=False)
    assert lote.resultados == {}
    assert lote.erros == []
    assert forn.chamadas == []


# === Cache integrado (Fase 2.4) ===

def test_buscar_precos_usa_cache_no_segundo_chamado(tmp_path, monkeypatch):
    """Primeiro batch chama o scraper; segundo batch com a mesma descrição
    deve servir do cache e NÃO chamar o scraper de novo."""
    from pregoes import db, config
    db_path = tmp_path / "cache_test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db(db_path)

    forn = _FornecedorFake("fakefornecedor",
                           {"PARAFUSO 10MM": [_r("PARAFUSO 10MM", "M", 5.0)]})
    itens = [ItemExtraido(descricao="PARAFUSO 10MM")]

    # 1º batch: cache miss → chama scraper
    lote1 = buscar_precos_para_itens(itens, [forn])
    assert lote1.cache_misses == 1
    assert lote1.cache_hits == 0
    assert len(lote1.resultados[0]) == 1
    assert forn.chamadas == ["PARAFUSO 10MM"]

    # 2º batch: cache hit → NÃO chama scraper
    lote2 = buscar_precos_para_itens(itens, [forn])
    assert lote2.cache_hits == 1
    assert lote2.cache_misses == 0
    assert len(lote2.resultados[0]) == 1
    assert lote2.resultados[0][0].marca == "M"
    # Scraper não foi chamado de novo
    assert forn.chamadas == ["PARAFUSO 10MM"]


def test_cache_separado_por_fornecedor(tmp_path, monkeypatch):
    """Cache de Almeida não vaza para Dantas."""
    from pregoes import db, config
    db_path = tmp_path / "cache_sep.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db(db_path)

    f_alm = _FornecedorFake("almeida", {"X": [_r("X", "MA", 1.0, fornecedor="almeida")]})
    f_dan = _FornecedorFake("dantas",  {"X": [_r("X", "MD", 2.0, fornecedor="dantas")]})
    itens = [ItemExtraido(descricao="X")]

    # Primeira passada com só Almeida — popula cache só para Almeida
    buscar_precos_para_itens(itens, [f_alm])
    # Segunda passada com ambos — Almeida vem do cache, Dantas é miss
    lote = buscar_precos_para_itens(itens, [f_alm, f_dan])
    assert lote.cache_hits == 1   # almeida
    assert lote.cache_misses == 1  # dantas
    assert f_dan.chamadas == ["X"]  # dantas chamou scraper


def test_usar_cache_false_sempre_chama_scraper(tmp_path, monkeypatch):
    from pregoes import db, config
    db_path = tmp_path / "no_cache.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db(db_path)

    forn = _FornecedorFake("fake", {"Y": [_r("Y", "M", 1.0)]})
    itens = [ItemExtraido(descricao="Y")]

    buscar_precos_para_itens(itens, [forn], usar_cache=False)
    buscar_precos_para_itens(itens, [forn], usar_cache=False)
    # Scraper foi chamado 2 vezes pois cache está desligado
    assert forn.chamadas == ["Y", "Y"]


def test_cache_db_buscar_gravar_round_trip(tmp_path, monkeypatch):
    """Teste de baixo nível: db.cache_buscar/gravar funcionam diretamente."""
    from pregoes import db, config
    db_path = tmp_path / "cache_low.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db(db_path)
    with db.get_conn() as conn:
        assert db.cache_buscar(conn, "abracadeira", "almeida") is None
        db.cache_gravar(conn, "abracadeira", "almeida", '[{"a":1}]')
        assert db.cache_buscar(conn, "abracadeira", "almeida") == '[{"a":1}]'
        # Mesma chave → upsert (substitui, não duplica)
        db.cache_gravar(conn, "abracadeira", "almeida", '[{"a":2}]')
        assert db.cache_buscar(conn, "abracadeira", "almeida") == '[{"a":2}]'


# === Paralelismo (Fase 2.5) ===

def test_rate_limiter_respeita_intervalo_minimo():
    """RateLimiter bloqueia até a janela abrir. 5 req com 5 req/s deve
    levar ~0.8s (intervalo 0.2s × 4 esperas)."""
    import time
    from pregoes.scraper import _RateLimiter

    rl = _RateLimiter(max_req_por_segundo=5)
    t0 = time.monotonic()
    for _ in range(5):
        rl.acquire()
    elapsed = time.monotonic() - t0
    # 5 acquires com intervalo mínimo 0.2s entre eles = ~0.8s (4 esperas)
    # Tolerância ampla pra robustez em CI lenta.
    assert 0.7 <= elapsed <= 1.5, f"RateLimiter levou {elapsed:.2f}s (esperado ~0.8s)"


def test_buscar_ordem_serial_garantida(tmp_path, monkeypatch):
    """Modelo serial agora (revertido em commit ____ por incompatibilidade
    do Playwright sync API com threads): chamadas vêm na ordem dos itens
    e o retorno tem todos. Quando paralelizarmos via asyncio, este teste
    documenta o contrato anterior — atualizar para o novo aqui."""
    from pregoes import db, config

    db_path = tmp_path / "par.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db(db_path)

    chamadas: list[str] = []

    class ForncRastreado:
        nome = "rastreado"
        _pool_size = 4   # ignorado no modelo serial atual

        def buscar(self, descricao):
            chamadas.append(descricao)
            return [_r(descricao, "M", 1.0, fornecedor=self.nome)]

    itens = [ItemExtraido(descricao=f"I{i}") for i in range(8)]
    lote = buscar_precos_para_itens(itens, [ForncRastreado()], usar_cache=False)
    assert len(lote.resultados) == 8
    assert all(len(lote.resultados[i]) == 1 for i in range(8))
    assert chamadas == [f"I{i}" for i in range(8)]


def test_buscar_paralelo_serial_quando_mock_sem_pool(tmp_path, monkeypatch):
    """Fornecedor mock sem _pool_size cai em max_workers=1 (serial).
    Garante que callback de progresso vem em ordem determinística."""
    from pregoes import db, config

    db_path = tmp_path / "serial.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db(db_path)

    forn = _FornecedorFake("fake", {f"I{i}": [_r(f"I{i}", "M", 1.0)]
                                     for i in range(5)})
    itens = [ItemExtraido(descricao=f"I{i}") for i in range(5)]
    progresso = []

    def cb(atual, total, msg):
        progresso.append((atual, msg))

    buscar_precos_para_itens(itens, [forn], progresso_callback=cb,
                              usar_cache=False)
    # Como pool não definido → serial → ordem garantida
    assert progresso == [(1, "I0"), (2, "I1"), (3, "I2"), (4, "I3"), (5, "I4")]


# === Commit incremental do cache (sprint P1 / A1) ===

def test_buscar_precos_commit_incremental_preserva_itens_antes_de_interrupcao(
        tmp_path, monkeypatch):
    """Regressão (A1): se o lote é interrompido no meio (operador fecha a
    janela, queda de rede — aqui simulado por um callback que levanta), as
    buscas já concluídas DEVEM estar commitadas no cache. Sem o commit
    incremental, o commit só aconteceria no fim (nunca alcançado) e o
    `finally: conn.close()` descartaria a transação inteira — a rodada toda
    de buscas se perderia e a re-busca recomeçaria do zero."""
    from pregoes import db, config

    db_path = tmp_path / "incremental.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db(db_path)

    itens = [ItemExtraido(descricao=d) for d in ("ITEM A", "ITEM B", "ITEM C")]
    forn = _FornecedorFake(
        "fake", {it.descricao: [_r(it.descricao, "M", 1.0)] for it in itens})

    def cb_que_trava(atual, total, msg):
        # Levanta DEPOIS de concluir o 2º item (atual=2): A e B já passaram
        # pelo commit incremental; C nem chega a ser buscado.
        if atual == 2:
            raise KeyboardInterrupt("janela fechada no meio da busca")

    with pytest.raises(KeyboardInterrupt):
        buscar_precos_para_itens(itens, [forn], progresso_callback=cb_que_trava,
                                  usar_cache=True, usar_catalogo=False)

    # Conexão NOVA (a do lote foi fechada no finally). A e B persistem; C não.
    conn = db.conectar()
    try:
        assert db.cache_buscar(conn, normalizar_descricao("ITEM A"), "fake") is not None
        assert db.cache_buscar(conn, normalizar_descricao("ITEM B"), "fake") is not None
        assert db.cache_buscar(conn, normalizar_descricao("ITEM C"), "fake") is None
    finally:
        conn.close()


# === Dedup de candidatos antes do corte top-N (busca cross-brand) ===

def test_top_n_dedup_remove_repetidos_e_surface_distintos():
    """O site repete cards (mesmo produto/fornecedor) — visto ao vivo:
    L.R.METAIS×2, LUCONI×2 no mesmo resultado. _top_n_por_relevancia deve
    DEDUPLICAR antes do corte, para as repetições não gastarem slots do top-N
    e o operador ver N produtos DISTINTOS (mais opções cross-brand)."""
    from pregoes.scraper import _top_n_por_relevancia
    desc = "VALVULA PARA LAVATORIO SEM LADRAO"
    cands = [
        _r("VALVULA P/ LAVATORIO 1603 SEM LADRAO", "L.R.METAIS", 10.0, codigo="1"),
        _r("VALVULA P/ LAVATORIO 1603 SEM LADRAO", "L.R.METAIS", 10.0, codigo="1"),  # dup
        _r("VALVULA P/ LAVATORIO SEM LADRAO BRANCO", "LUCONI", 9.0, codigo="2"),
        _r("VALVULA P/ LAVATORIO SEM LADRAO BRANCO", "LUCONI", 9.0, codigo="2"),  # dup
        _r("VALVULA P/ LAVATORIO SEM LADRAO PRETO", "ASTRA", 8.0, codigo="3"),
        _r("VALVULA P/ LAVATORIO SOCEL SEM UNHA", "SOCELPLAST", 7.0, codigo="4"),
    ]
    # ANTES (sem dedup), o top-5 traria L.R×2, LUCONI×2, ASTRA → 3 distintos.
    # AGORA o top-5 traz 4 produtos DISTINTOS (SOCELPLAST deixa de ser cortado).
    res = _top_n_por_relevancia(desc, cands, 5)
    marcas = [r.marca for r in res]
    assert len(res) == 4, marcas                 # 6 cards, 2 repetidos → 4 distintos
    assert marcas.count("L.R.METAIS") == 1       # repetição colapsada
    assert marcas.count("LUCONI") == 1
    assert "ASTRA" in marcas and "SOCELPLAST" in marcas  # distinto antes cortado, agora aparece
    # Ordenação por relevância preservada: o 1º é um match de relevância máxima
    # (sem regressão no topo — o dedup só remove repetição, não reordena por mérito).
    assert relevancia(desc, res[0].descricao_encontrada) == max(
        relevancia(desc, r.descricao_encontrada) for r in res)


# === Reconhecimento por código (produto já no catálogo do cliente) ===

def test_indice_por_codigo_mapeia_fornecedor_e_codigo():
    from pregoes.matcher import ProdutoCatalogo, indice_por_codigo
    cat = [
        ProdutoCatalogo(produto_id=1, descricao_canonica="abracadeira nylon 30cm",
                        codigo_almeida="52468", codigo_dantas=None, marca="X", unidade_padrao="pct"),
        ProdutoCatalogo(produto_id=2, descricao_canonica="cabo flexivel 2.5mm",
                        codigo_almeida=None, codigo_dantas="5004", marca="Y", unidade_padrao="m"),
    ]
    idx = indice_por_codigo(cat)
    assert idx[("almeida", "52468")].produto_id == 1
    assert idx[("dantas", "5004")].produto_id == 2
    assert ("dantas", "52468") not in idx   # código é POR fornecedor


def test_top_n_prioriza_e_marca_conhecido_por_codigo():
    """Candidato cujo código já está no catálogo do cliente é MARCADO
    (no_catalogo) e vai para o TOPO mesmo com relevância textual baixa — sinal
    forte (o cliente já compra esse produto). Resp 7: continua sendo opção, não
    decisão automática de marca."""
    from pregoes.scraper import _top_n_por_relevancia
    from pregoes.matcher import ProdutoCatalogo, indice_por_codigo
    desc = "VALVULA PARA LAVATORIO SEM LADRAO"
    cands = [
        _r("VALVULA P/ LAVATORIO SEM LADRAO BRANCO", "ASTRA", 9.0, codigo="111", fornecedor="almeida"),
        # REBOUÇAS: relevância textual baixa (sem 'ladrao', plástico) MAS já no catálogo.
        _r("VALVULA P/ LAVATORIO REBOUCAS PLASTICO PRETO", "REBOUCAS", 7.0, codigo="999", fornecedor="almeida"),
    ]
    cat = [ProdutoCatalogo(produto_id=42, descricao_canonica="valvula lavatorio reboucas",
                           codigo_almeida="999", codigo_dantas=None, marca="REBOUCAS", unidade_padrao="un")]
    res = _top_n_por_relevancia(desc, cands, 5, idx_codigo=indice_por_codigo(cat))
    assert res[0].marca == "REBOUCAS"               # conhecido em 1º
    assert res[0].no_catalogo is True
    assert res[0].produto_catalogo_id == 42
    assert res[0].descricao_catalogo == "valvula lavatorio reboucas"
    assert any(r.marca == "ASTRA" and not r.no_catalogo for r in res)  # não-conhecido fica


def test_top_n_sem_idx_codigo_nao_marca_nada():
    """Sem catálogo (idx vazio), comportamento idêntico ao anterior: ninguém
    marcado, ordenação por relevância normal."""
    from pregoes.scraper import _top_n_por_relevancia
    desc = "VALVULA PARA LAVATORIO SEM LADRAO"
    cands = [_r("VALVULA P/ LAVATORIO SEM LADRAO", "ASTRA", 9.0, codigo="111", fornecedor="almeida")]
    res = _top_n_por_relevancia(desc, cands, 5)
    assert res[0].no_catalogo is False


def test_ultima_cotacao_ignora_ttl(tmp_path):
    """ultima_cotacao retorna o último preço conhecido SEM TTL (para a busca
    manual no catálogo), ao contrário de preco_em_cache (janela de 2 dias)."""
    from pregoes import db as _db
    from pregoes.db import init_db, get_conn, ultima_cotacao, preco_em_cache
    dbp = tmp_path / "t.db"
    init_db(dbp)
    velho = (datetime.utcnow() - timedelta(days=30)).isoformat(sep=" ")
    with get_conn(dbp) as conn:
        pid = _setup_produto(conn)
        conn.execute(
            "INSERT INTO precos_historico (produto_id, fornecedor, preco, consultado_em) "
            "VALUES (?,?,?,?)", (pid, "almeida", 12.34, velho))
        conn.commit()
        assert preco_em_cache(conn, pid, "almeida") is None       # velho → fora do TTL
        row = ultima_cotacao(conn, pid, "almeida")                # sem TTL → retorna
        assert row is not None and row["preco"] == 12.34


def test_cache_roundtrip_preserva_todos_os_campos():
    """O cache grava via asdict (11 campos); _payload_to_resultados deve
    reconstruir TODOS — senão o reconhecimento por código (no_catalogo),
    produto_catalogo_id, descricao_catalogo, ncm e url somem em cache hit."""
    from dataclasses import asdict
    import json as _json
    from pregoes.scraper import ResultadoBusca, _payload_to_resultados
    r = ResultadoBusca(
        descricao_encontrada="TUBO PVC 75", marca="PLASTUBOS", preco=51.44,
        codigo_fornecedor="8811", confianca="exato", fornecedor="dantas",
        ncm="39172300", url="http://x/p", no_catalogo=True,
        produto_catalogo_id=42, descricao_catalogo="tubo pvc 75mm")
    payload = _json.dumps([asdict(r)], ensure_ascii=False)
    back = _payload_to_resultados(payload, "dantas")
    assert len(back) == 1 and back[0] == r   # round-trip completo (frozen __eq__)
