"""Launcher do .exe (run_app) — patches no index.html e flags de config.

Patches no index.html servido pelo Streamlit (1× no boot, idempotente):
- pt-BR + translate="no": o navegador não traduz a página (a tradução trocava
  nós do DOM e quebrava o Streamlit — NotFoundError: removeChild).
- Neutraliza os atalhos "c" (popup "Clear caches") e "r" (rerun) do Streamlit,
  que disparam com a tecla SOZINHA. Listener em <html> na fase de BOLHA, no
  keydown e no keyup, só impedindo a PROPAGAÇÃO (sem preventDefault).

Flags: o .exe não enxerga o .streamlit/config.toml (o Streamlit só o procura
na pasta atual), então o run_app o converte em flags de linha de comando.
"""
from __future__ import annotations

import pytest

import run_app

_SAMPLE = ('<!doctype html><html lang="en"><head><meta charset="utf-8"/></head>'
           '<body><div id="root"></div></body></html>')

# Guard da 1ª versão (mirava Ctrl+C) — como ficou gravado nas instalações.
_GUARD_V1 = (
    '<script id="pp-atalhos-guard">'
    "(function(){window.addEventListener('keydown',function(e){"
    "var k=(e.key||'').toLowerCase();"
    "if((e.ctrlKey||e.metaKey)&&(k==='c'||k==='r')){e.stopImmediatePropagation();}"
    "},true);})();"
    "</script>"
)


@pytest.fixture
def index_html(tmp_path, monkeypatch):
    """Cria um index.html-fake e faz streamlit.__file__ apontar para ele."""
    static = tmp_path / "static"
    static.mkdir()
    idx = static / "index.html"
    idx.write_text(_SAMPLE, encoding="utf-8")
    import streamlit
    monkeypatch.setattr(streamlit, "__file__", str(tmp_path / "__init__.py"))
    return idx


def test_patch_aplica_pt_br_e_atalho(index_html):
    run_app._ajustar_pagina_streamlit()
    out = index_html.read_text(encoding="utf-8")
    # (1) tradução: pt-BR + translate=no + meta notranslate
    assert 'lang="pt-BR"' in out and 'translate="no"' in out
    assert 'content="notranslate"' in out
    # (2) atalho: c (67) e r (82) SEM modificador, keydown E keyup, em <html>
    assert 'id="pp-atalhos-guard-v2"' in out
    assert "e.keyCode===67||e.keyCode===82" in out
    assert "if(e.ctrlKey||e.metaKey||e.altKey)return;" in out   # Ctrl+C copia
    assert "'keydown',f,false" in out and "'keyup',f,false" in out   # BOLHA
    assert "document.documentElement" in out
    assert "stopImmediatePropagation" not in out
    assert "preventDefault" not in run_app._SCRIPT_ATALHOS   # digitar continua


def test_patch_e_idempotente(index_html):
    run_app._ajustar_pagina_streamlit()
    run_app._ajustar_pagina_streamlit()
    run_app._ajustar_pagina_streamlit()
    out = index_html.read_text(encoding="utf-8")
    assert out.count('id="pp-atalhos-guard-v2"') == 1
    assert out.count('lang="pt-BR"') == 1


def test_patch_nao_quebra_se_ja_traduzido_sem_atalho(index_html):
    # Simula uma instalação que já tinha o fix de tradução (v1.0.6) mas ainda
    # não o do atalho: o script de atalho deve ser ADICIONADO mesmo assim.
    parcial = _SAMPLE.replace('<html lang="en">',
                              '<html lang="pt-BR" translate="no">')
    index_html.write_text(parcial, encoding="utf-8")
    run_app._ajustar_pagina_streamlit()
    out = index_html.read_text(encoding="utf-8")
    assert 'id="pp-atalhos-guard-v2"' in out
    assert out.count('lang="pt-BR"') == 1   # não duplicou o pt-BR


def test_patch_troca_o_guard_antigo_pelo_novo(index_html):
    # Instalação já patcheada com o guard v1 (Ctrl+C): ele sai, o v2 entra.
    antigo = (_SAMPLE.replace('<html lang="en">', '<html lang="pt-BR" translate="no">')
              .replace("</head>", _GUARD_V1 + "</head>"))
    index_html.write_text(antigo, encoding="utf-8")
    run_app._ajustar_pagina_streamlit()
    out = index_html.read_text(encoding="utf-8")
    assert 'id="pp-atalhos-guard"' not in out
    assert out.count('id="pp-atalhos-guard-v2"') == 1


def test_patch_falha_em_silencio_sem_streamlit(monkeypatch):
    # Se o index.html não existir, NÃO pode levantar (não derruba o boot).
    import streamlit
    monkeypatch.setattr(streamlit, "__file__", "/caminho/inexistente/__init__.py")
    run_app._ajustar_pagina_streamlit()   # não deve levantar


def _flags_como_dict() -> dict[str, str]:
    flags = run_app._flags_config()
    assert all(f.startswith("--") and "=" in f for f in flags)
    return dict(f[2:].split("=", 1) for f in flags)


def test_flags_trazem_o_config_toml_do_app():
    # O toml empacotado chega ao Streamlit (tema, etc.) mesmo fora da pasta.
    op = _flags_como_dict()
    assert op["theme.primaryColor"] == "#1D3F8F"
    assert op["theme.base"] == "light"
    assert op["theme.font"].startswith("'IBM Plex Sans':https://")


def test_flags_forcam_as_opcoes_de_producao():
    op = _flags_como_dict()
    assert op["server.port"] == "8501"
    assert op["server.address"] == "127.0.0.1"
    assert op["server.headless"] == "true"
    # Sem menu de desenvolvedor: é o que libera o "Clear caches" e o "Deploy".
    assert op["client.toolbarMode"] == "minimal"
    assert op["client.showErrorDetails"] == "none"
    # O toml de dev liga runOnSave/fileWatcher; no cliente ficam desligados.
    assert op["server.runOnSave"] == "false"
    assert op["server.fileWatcherType"] == "none"


def test_flags_sem_toml_ainda_forcam_producao(monkeypatch, tmp_path):
    # Pacote sem o toml (ou ilegível): o app sobe com as opções de produção.
    monkeypatch.setattr(run_app, "_resource", lambda rel: str(tmp_path / rel))
    op = _flags_como_dict()
    assert op["client.toolbarMode"] == "minimal"
    assert op["server.address"] == "127.0.0.1"
    assert "theme.primaryColor" not in op
