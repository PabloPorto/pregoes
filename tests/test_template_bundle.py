"""
O template da planilha viaja DENTRO do pacote em `pregoes/dados/matriz_template.xlsx`
(além da cópia gravável em `data/templates/`).

Motivo (2026-07-11): o cliente colocou a pasta descompactada num Google Drive e
baixou no computador; o Drive deixou o `data/templates/matriz_template.xlsx` no
Drive mas NÃO baixou o arquivo, e o `build_exe.bat`/PyInstaller abortou por não
achar o template. A pasta `pregoes/` desce sempre junto com o código, então a
cópia dentro do pacote é a fonte confiável para o build (build_exe.bat se
auto-recupera dela) e para o seeding em runtime (config._TEMPLATE_BUNDLE).

Estes testes travam: (1) a cópia do pacote existe e é BYTE-IDÊNTICA à canônica
(sem drift silencioso entre as duas); (2) o config semeia a partir do pacote.
"""
import hashlib
from pathlib import Path

from pregoes import config


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def _template_do_pacote() -> Path:
    return Path(config.__file__).resolve().parent / "dados" / "matriz_template.xlsx"


def test_copia_do_pacote_existe():
    pkg = _template_do_pacote()
    assert pkg.exists(), (
        "pregoes/dados/matriz_template.xlsx sumiu — é a fonte que o build_exe.bat "
        "usa quando a pasta data/ não vem completa (Google Drive)")


def test_copia_do_pacote_identica_a_data_templates():
    """Sem drift: as duas cópias precisam ser o MESMO arquivo."""
    pkg = _template_do_pacote()
    canon = config.TEMPLATE_MATRIZ          # data/templates/matriz_template.xlsx
    assert canon.exists()
    assert _md5(pkg) == _md5(canon), (
        "pregoes/dados/matriz_template.xlsx divergiu de data/templates/ — "
        "recopie: cp data/templates/matriz_template.xlsx pregoes/dados/")


def test_seed_do_template_aponta_para_o_pacote():
    """O seed que o config usa para recriar data/templates/ deve vir de
    pregoes/dados/ (viaja com o código), NÃO da pasta data/ (que o Drive pode
    não baixar). Assim o boot recria o template mesmo se data/ veio incompleta."""
    assert config._TEMPLATE_BUNDLE.resolve() == _template_do_pacote().resolve(), (
        "config._TEMPLATE_BUNDLE deveria apontar para pregoes/dados/matriz_template.xlsx")
