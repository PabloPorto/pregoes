"""Operações da interface web que não são tela: o que as rotas e as tarefas
chamam. Regras trazidas do Streamlit (`pregoes/ui/app.py`) ficam aqui ou em
`regras_*.py`, com a linha de origem no comentário, para conferir a paridade.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from pregoes import config, db, extrator, mapeamento_manual
from pregoes.web.pregao import Pregao, trava
from pregoes.web.tarefas import Tarefa

_log = logging.getLogger("pregoes.web")

# Abaixo disso a extração tenta os mapas aprendidos e a revisão oferece o
# mapeamento manual (app.py L523 / L598).
CONFIANCA_BOA = 0.95


def guardar_pdf(conteudo: bytes) -> Path:
    """Grava o PDF enviado em `data/editais` com nome aleatório (como no
    Streamlit, app.py L506 — o nome original não é usado como caminho)."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf",
                                     dir=str(config.EDITAIS_DIR)) as f:
        f.write(conteudo)
        return Path(f.name)


def tentar_mapa_aprendido(resultado, conn, arquivo: str):
    """Melhora uma extração fraca com um mapa de colunas já ensinado pelo
    operador (cabeçalho conhecido). Em LOTE: a tabela casada + as
    continuações das outras páginas. Porta de app.py `_tentar_mapa_aprendido`
    (L443) — devolve o original se nenhum mapa ganhar."""
    for idx, tab in enumerate(resultado.tabelas_brutas):
        if not tab or not tab[0]:
            continue
        sig = mapeamento_manual.assinatura_cabecalho(tab[0])
        mapa_salvo = mapeamento_manual.carregar_mapa(conn, sig)
        if not mapa_salvo:
            continue
        novo = mapeamento_manual.aplicar_mapa_em_lote(
            resultado.tabelas_brutas, idx, mapa_salvo, arquivo=arquivo)
        if novo.confianca_global > resultado.confianca_global:
            novo.tabelas_brutas = resultado.tabelas_brutas
            novo.avisos.insert(
                0, f"✨ Mapa aprendido reusado (tabela #{idx + 1} e "
                   f"continuações, cabeçalho conhecido).")
            return novo
    return resultado


def ler_edital(slug: str, tarefa: Tarefa) -> None:
    """Tarefa 'leitura': extrai os itens do PDF do pregão `slug` e grava a
    extração no rascunho. Mostra o progresso da IA (o Streamlit só tinha um
    spinner — o backend já oferecia o callback)."""
    pregao = Pregao.abrir(slug)
    if pregao is None:
        raise RuntimeError("pregão não encontrado")
    pdf = pregao["pdf_path"]
    tarefa.atualizar(0.05, "Lendo o PDF e detectando as tabelas…")

    def _progresso_ia(atual, total, msg):
        # A leitura rápida já passou; a IA ocupa de 30% a 95% da barra.
        tarefa.atualizar(0.30 + 0.65 * (atual / max(total, 1)),
                         f"Leitura com IA — {msg}", com_ia=True)

    resultado = extrator.extrair_com_pipeline(pdf, callback_progresso_llm=_progresso_ia)
    if resultado.confianca_global < CONFIANCA_BOA and resultado.tabelas_brutas:
        tarefa.atualizar(0.96, "Conferindo formatos de edital já ensinados…")
        with db.get_conn() as conn:
            resultado = tentar_mapa_aprendido(resultado, conn, str(pdf))
    with trava(slug):
        pregao = Pregao.abrir(slug)
        if pregao is None:      # descartado enquanto lia
            return
        pregao.estado["extracao"] = resultado
        pregao.estado.pop("itens_edital", None)
        pregao.garantir_itens()
        pregao.salvar()
    _log.info("LEITURA: pregão %s — %d itens, confiança %.0f%%", slug,
              resultado.total_itens, 100 * resultado.confianca_global)
