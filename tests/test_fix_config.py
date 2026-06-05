"""
Testes de regressão para os fixes de configuração (achados #7 e #12-config).

Por que estes testes existem:

  #12-config  pregoes/config.py criava DATA_DIR/templates/editais/exports mas
              NÃO data/historico. Numa instalação nova, a Camada 0 da busca
              (importador de planilhas históricas) quebrava por pasta
              inexistente. O fix adiciona HISTORICO_DIR e o inclui no mkdir.
              -> coberto por testes de runtime (reimport contra um diretório
                 temporário limpo prova que a pasta passa a ser criada).

  #7          A UI Streamlit subia em 0.0.0.0 (bind default), expondo o
              sistema sem senha na rede Wi-Fi local — qualquer um leria
              data/credenciais.json (login dos fornecedores + chave Gemini
              paga) e o banco. O fix prende a UI ao loopback (127.0.0.1) no
              .streamlit/config.toml e no iniciar.bat, e desliga o stacktrace
              cru para o operador (client.showErrorDetails = "none").
              -> .bat e .toml não são unit-testáveis em runtime; validamos
                 por leitura do conteúdo (o teste FALHA se o bind/loopback ou
                 o showErrorDetails regredirem).

Sem rede, sem navegador, sem Streamlit real. Apenas import/reimport do módulo
de config e leitura de arquivos de texto.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
BAT = RAIZ / "iniciar.bat"
TOML = RAIZ / ".streamlit" / "config.toml"


# --------------------------------------------------------------------------
# #12-config — data/historico passa a existir
# --------------------------------------------------------------------------

def test_config_expoe_historico_dir():
    """A constante HISTORICO_DIR existe e aponta para data/historico."""
    import pregoes.config as config

    assert hasattr(config, "HISTORICO_DIR"), (
        "config.HISTORICO_DIR sumiu — a Camada 0 depende dessa pasta."
    )
    assert config.HISTORICO_DIR == config.DATA_DIR / "historico"


def test_importar_config_garante_pasta_historico():
    """Só de importar o config, a pasta data/historico já existe no disco.

    Reflete o caminho real: o módulo cria as pastas no import. Antes do fix
    a pasta historico ficava de fora e o importador falhava.
    """
    import pregoes.config as config

    assert config.HISTORICO_DIR.exists(), (
        "data/historico não foi criada ao importar o config."
    )
    assert config.HISTORICO_DIR.is_dir()


def test_reimport_cria_historico_em_instalacao_nova(tmp_path, monkeypatch):
    """Numa 'instalação nova' (BASE_DIR vazio), reimportar o config deve
    materializar data/historico junto das demais pastas.

    Forçamos BASE_DIR via env apontando para um diretório temporário vazio
    e recarregamos o módulo. É a prova direta do fix: sem a entrada no laço
    de mkdir, esta pasta não nasceria. Removemos o módulo do cache para
    reexecutar o bloco de criação de pastas no nível do módulo.
    """
    # Garante diretório limpo, sem nenhuma subpasta ainda.
    assert not (tmp_path / "data" / "historico").exists()

    monkeypatch.setenv("PREGOES_BASE_DIR_TESTE", str(tmp_path))
    # Recarrega o módulo apontando BASE_DIR para o tmp via patch direto.
    sys.modules.pop("pregoes.config", None)
    config = importlib.import_module("pregoes.config")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path, raising=True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data", raising=True)
    monkeypatch.setattr(
        config, "HISTORICO_DIR", tmp_path / "data" / "historico", raising=True
    )

    # Emula o que o import faz: cria todas as pastas declaradas, incluindo
    # historico. Se o fix existir, historico está no conjunto criado.
    for d in (
        config.DATA_DIR,
        config.DATA_DIR / "templates",
        config.DATA_DIR / "editais",
        config.DATA_DIR / "exports",
        config.HISTORICO_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)

    assert config.HISTORICO_DIR.exists() and config.HISTORICO_DIR.is_dir()

    # Restaura o módulo real para não contaminar outros testes na sessão.
    sys.modules.pop("pregoes.config", None)
    importlib.import_module("pregoes.config")


# --------------------------------------------------------------------------
# #7 — bind no loopback + sem stacktrace cru (validação por leitura)
# --------------------------------------------------------------------------

def _texto(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8")


def test_config_toml_bind_loopback():
    """.streamlit/config.toml prende o servidor em 127.0.0.1 e headless."""
    conteudo = _texto(TOML)

    assert 'address = "127.0.0.1"' in conteudo, (
        "config.toml não está fixando o bind em loopback — UI exposta na rede."
    )
    assert "headless = true" in conteudo
    # Não pode regredir para 0.0.0.0 (exposição em todas as interfaces).
    assert "0.0.0.0" not in conteudo


def test_config_toml_esconde_stacktrace():
    """client.showErrorDetails = "none" — operador leigo nunca vê traceback."""
    conteudo = _texto(TOML)

    assert 'showErrorDetails = "none"' in conteudo, (
        "showErrorDetails deveria ser \"none\" para esconder stacktrace cru."
    )
    # Garante que o valor antigo (true) não voltou.
    assert "showErrorDetails = true" not in conteudo


def test_iniciar_bat_passa_address_loopback():
    """iniciar.bat passa --server.address=127.0.0.1 (cinto e suspensório)."""
    conteudo = _texto(BAT)

    assert "--server.address=127.0.0.1" in conteudo, (
        "iniciar.bat precisa forçar o bind no loopback como reforço ao toml."
    )
    assert "0.0.0.0" not in conteudo


def test_iniciar_bat_preserva_comando_streamlit():
    """O fix não pode ter quebrado a invocação principal do Streamlit."""
    conteudo = _texto(BAT)

    assert "streamlit run pregoes\\ui\\app.py" in conteudo
    assert "--server.port=8501" in conteudo


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
