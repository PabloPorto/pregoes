"""Regressão (#5a/b): timeout explícito de 60s nas chamadas ao Gemini.

Achado: as três construções de `genai.Client(...)` —
`extrator_llm.extrair_via_gemini`, `extrator_llm.julgar_candidatos` e
`alinhador._chamar_llm_em_lotes` — rodavam SEM `http_options`. O SDK
google-genai NÃO impõe timeout default; um soluço de rede travaria a thread
do Streamlit para sempre (spinner eterno, sem cancelar) — fatal durante o
pregão.

O fix passa `http_options=types.HttpOptions(timeout=60000)` (60s; o timeout
do HttpOptions é em MILISSEGUNDOS no google-genai) e garante que o except
existente degrada para o fallback local quando a chamada estoura.

Estes testes NÃO tocam rede. Como o SDK `google.genai` em geral não está
instalado no venv de dev/CI, montamos um dublê de `google.genai` em
`sys.modules` (com `Client` e `types.HttpOptions` que apenas registram o que
recebem) e exercitamos as funções reais. Há ainda um teste que roda contra o
SDK REAL quando ele estiver presente (via `importorskip`), confirmando que a
forma `types.HttpOptions(timeout=60000)` é válida na API instalada.

ANTES do fix estes testes falhariam: `Client` seria chamado sem
`http_options`, logo `http_options.timeout` não seria 60000.
"""
from __future__ import annotations

import sys
import types as _pytypes

import pytest


_TIMEOUT_ESPERADO_MS = 60_000  # 60s em milissegundos (contrato do google-genai)


class _FakeHttpOptions:
    """Dublê de `google.genai.types.HttpOptions` — só guarda os campos."""

    def __init__(self, *, timeout=None, **kwargs):
        self.timeout = timeout
        self.kwargs = kwargs


class _ClientSpy:
    """Dublê de `genai.Client` — registra os kwargs da construção e expõe
    um `models.generate_content` configurável (sucesso ou exceção)."""

    ultima_instancia: "_ClientSpy | None" = None

    def __init__(self, *args, **kwargs):
        self.kwargs_construcao = kwargs
        type(self).ultima_instancia = self

        spy = self

        class _Models:
            def generate_content(self, *a, **k):
                if spy._erro is not None:
                    raise spy._erro
                return spy._resposta

        self.models = _Models()
        self._resposta = _pytypes.SimpleNamespace(text="[]")
        self._erro = None

    def configurar_resposta(self, texto):
        self._resposta = _pytypes.SimpleNamespace(text=texto)

    def configurar_erro(self, exc):
        self._erro = exc


@pytest.fixture
def sdk_falso(monkeypatch):
    """Instala um pacote `google.genai` falso em sys.modules e devolve a
    classe espiã `_ClientSpy` (para inspecionar a construção do Client).

    Reusável pelos três call sites — todos fazem `from google import genai`
    e `from google.genai import types`.
    """
    _ClientSpy.ultima_instancia = None

    mod_google = _pytypes.ModuleType("google")
    mod_genai = _pytypes.ModuleType("google.genai")
    mod_types = _pytypes.ModuleType("google.genai.types")

    mod_genai.Client = _ClientSpy
    mod_genai.types = mod_types
    mod_types.HttpOptions = _FakeHttpOptions
    # Liga o subpacote para que `from google import genai` resolva o atributo.
    mod_google.genai = mod_genai

    monkeypatch.setitem(sys.modules, "google", mod_google)
    monkeypatch.setitem(sys.modules, "google.genai", mod_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", mod_types)

    return _ClientSpy


def _assert_timeout_60s(spy_cls):
    """Confere que o último Client foi construído com http_options.timeout=60000."""
    inst = spy_cls.ultima_instancia
    assert inst is not None, "genai.Client não foi construído"
    http_options = inst.kwargs_construcao.get("http_options")
    assert http_options is not None, (
        "Client construído SEM http_options — sem timeout, a thread trava "
        "para sempre num soluço de rede."
    )
    assert http_options.timeout == _TIMEOUT_ESPERADO_MS, (
        f"timeout deveria ser {_TIMEOUT_ESPERADO_MS}ms (60s); "
        f"recebido {http_options.timeout!r}"
    )


# === extrator_llm.julgar_candidatos (Camada 3 da busca) ===

def _resultado_busca_falso(i):
    return _pytypes.SimpleNamespace(
        fornecedor="almeida",
        codigo_fornecedor=f"COD{i}",
        descricao_encontrada=f"PRODUTO {i}",
    )


def test_judge_constroi_client_com_timeout(sdk_falso, monkeypatch):
    from pregoes import extrator_llm

    # API key direta (evita tocar credenciais.json / disco).
    spy = sdk_falso
    # Garante que o Client devolva uma resposta válida do judge.
    candidatos = [_resultado_busca_falso(0), _resultado_busca_falso(1)]

    # A resposta é configurada na instância criada DENTRO da função; como o
    # default do spy já é "[]", o judge devolve [] sem erro.
    indices = extrator_llm.julgar_candidatos(
        "PARAFUSO SEXTAVADO 5/16",
        candidatos,
        conn=None,
        api_key="chave-de-teste",
    )

    _assert_timeout_60s(spy)
    assert indices == []  # resposta "[]" → nenhum candidato


def test_judge_timeout_degrada_para_lista_vazia(sdk_falso, monkeypatch):
    """Um timeout na chamada deve ser capturado e devolver [] (fallback:
    operador escolhe manualmente entre os 'top 3 variação' do scraper)."""
    from pregoes import extrator_llm

    class _Timeout(Exception):
        pass

    # Faz o Client estourar timeout em generate_content. Precisamos configurar
    # a instância DEPOIS que ela é criada; usamos uma subclasse que já nasce
    # configurada para erro.
    class _ClientTimeout(_ClientSpy):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.configurar_erro(_Timeout("Deadline of 60.0s exceeded"))

    monkeypatch.setattr(sys.modules["google.genai"], "Client", _ClientTimeout)
    _ClientTimeout.ultima_instancia = None

    candidatos = [_resultado_busca_falso(0)]
    indices = extrator_llm.julgar_candidatos(
        "TUBO PVC 100MM", candidatos, conn=None, api_key="chave-de-teste"
    )

    # Construiu com timeout E degradou sem levantar.
    _assert_timeout_60s(_ClientTimeout)
    assert indices == []


# === alinhador._chamar_llm_em_lotes (Fase B / canonização) ===

def test_alinhador_constroi_client_com_timeout(sdk_falso):
    from pregoes import alinhador

    # Resposta válida não é necessária para checar a construção; usamos o
    # default "[]" do spy. _chamar_llm_em_lotes devolve {} (nada casou), mas
    # o Client JÁ foi construído com o timeout.
    resultado = alinhador._chamar_llm_em_lotes(
        ["parafuso sextavado 5/16"], "chave-de-teste", alinhador.MODELO_DEFAULT
    )

    _assert_timeout_60s(sdk_falso)
    assert isinstance(resultado, dict)


def test_alinhador_timeout_degrada_sem_levantar(sdk_falso, monkeypatch):
    """Timeout em um lote vira fallback local (lote ignorado), sem levantar."""
    from pregoes import alinhador

    class _Timeout(Exception):
        pass

    class _ClientTimeout(_ClientSpy):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.configurar_erro(_Timeout("Deadline exceeded"))

    monkeypatch.setattr(sys.modules["google.genai"], "Client", _ClientTimeout)
    _ClientTimeout.ultima_instancia = None

    # Não deve levantar; lote falho não casa nada → dict vazio.
    resultado = alinhador._chamar_llm_em_lotes(
        ["cano pvc 50"], "chave-de-teste", alinhador.MODELO_DEFAULT
    )

    _assert_timeout_60s(_ClientTimeout)
    assert resultado == {}


# === extrator_llm.extrair_via_gemini (Camada 2 da extração) ===

def test_extrair_via_gemini_constroi_client_com_timeout(sdk_falso, monkeypatch, tmp_path):
    """A construção do Client com timeout acontece antes de abrir o PDF.
    Mockamos pdfplumber para um PDF de 1 página com texto, garantindo que a
    função chega à chamada do Gemini."""
    from pregoes import extrator_llm

    # Dublê mínimo de pdfplumber.open(...) como context manager.
    class _Page:
        def extract_text(self):
            return "ITEM DESCRICAO UNIDADE QTD\n1 PARAFUSO UN 10\n" * 3

    class _PDF:
        pages = [_Page()]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(extrator_llm.pdfplumber, "open", lambda _p: _PDF())

    pdf_falso = tmp_path / "edital.pdf"
    pdf_falso.write_bytes(b"%PDF-1.4 fake")

    resultado = extrator_llm.extrair_via_gemini(
        pdf_falso, api_key="chave-de-teste"
    )

    _assert_timeout_60s(sdk_falso)
    # Resposta default "[]" → zero itens, mas sem erro de timeout.
    assert resultado.itens == []


# === Smoke test contra o SDK REAL, se instalado ===

def test_httpoptions_real_aceita_timeout_em_ms():
    """Confirma na API REAL do google-genai que HttpOptions(timeout=60000)
    é válido e guarda o valor em ms. Pula se o SDK não estiver instalado."""
    pytest.importorskip("google.genai")
    from google.genai import types as real_types

    opts = real_types.HttpOptions(timeout=_TIMEOUT_ESPERADO_MS)
    assert opts.timeout == _TIMEOUT_ESPERADO_MS
