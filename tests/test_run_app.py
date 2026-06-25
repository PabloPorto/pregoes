"""Launcher do .exe (run_app) — patches no index.html do Streamlit.

Dois ajustes para o operador leigo no Windows, ambos aplicados ao index.html
servido pelo Streamlit (1× no boot, idempotente):
- pt-BR + translate="no": o navegador não traduz a página (a tradução trocava
  nós do DOM e quebrava o Streamlit — NotFoundError: removeChild).
- Neutraliza o atalho de teclado do Streamlit que abre o popup "Clear cache" no
  Ctrl+C (copiar, no Windows). Captura na fase de captura e só impede a
  PROPAGAÇÃO (sem preventDefault) → copiar/colar continuam funcionando.
"""
from __future__ import annotations

import os

import pytest

import run_app

_SAMPLE = ('<!doctype html><html lang="en"><head><meta charset="utf-8"/></head>'
           '<body><div id="root"></div></body></html>')


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
    # (2) atalho: script de captura que suprime Ctrl/Cmd+C e +R
    assert 'id="pp-atalhos-guard"' in out
    assert "stopImmediatePropagation" in out
    assert "k==='c'||k==='r'" in out
    assert "},true);" in out          # fase de CAPTURA (antes do Streamlit)
    assert "preventDefault" not in run_app._SCRIPT_ATALHOS   # não quebra o copiar


def test_patch_e_idempotente(index_html):
    run_app._ajustar_pagina_streamlit()
    run_app._ajustar_pagina_streamlit()
    run_app._ajustar_pagina_streamlit()
    out = index_html.read_text(encoding="utf-8")
    assert out.count('id="pp-atalhos-guard"') == 1
    assert out.count('lang="pt-BR"') == 1


def test_patch_nao_quebra_se_ja_traduzido_sem_atalho(index_html):
    # Simula uma instalação que já tinha o fix de tradução (v1.0.6) mas ainda
    # não o do atalho: o script de atalho deve ser ADICIONADO mesmo assim.
    parcial = _SAMPLE.replace('<html lang="en">',
                              '<html lang="pt-BR" translate="no">')
    index_html.write_text(parcial, encoding="utf-8")
    run_app._ajustar_pagina_streamlit()
    out = index_html.read_text(encoding="utf-8")
    assert 'id="pp-atalhos-guard"' in out
    assert out.count('lang="pt-BR"') == 1   # não duplicou o pt-BR


def test_patch_falha_em_silencio_sem_streamlit(monkeypatch):
    # Se o index.html não existir, NÃO pode levantar (não derruba o boot).
    import streamlit
    monkeypatch.setattr(streamlit, "__file__", "/caminho/inexistente/__init__.py")
    run_app._ajustar_pagina_streamlit()   # não deve levantar
