# -*- coding: utf-8 -*-
"""Launcher de DEMONSTRAÇÃO para capturar screenshots das telas (guia do
usuário). Pré-carrega o session_state com dados de exemplo conforme a variável
de ambiente DEMO_TELA e então importa o app (que renderiza a tela pedida).

NÃO é usado em produção — é só para gerar as imagens do PDF de apresentação.
Rodar: DEMO_TELA=busca_precos streamlit run scripts/_demo_tela.py --server.port 8502
"""
import os
import streamlit as st

from pregoes.extrator import ItemExtraido, ResultadoExtracao
from pregoes.scraper import ResultadoBusca
from pregoes.exportador import SelecaoFornecedor
from pregoes import credenciais

tela = os.environ.get("DEMO_TELA", "home")

# Na tela de Configurações, NUNCA exibir a credencial real do cliente no PDF
# de apresentação. Substituímos a leitura por um e-mail fictício só na
# demonstração — o arquivo data/credenciais.json não é tocado.
if tela == "configuracoes":
    _DEMO_CRED = {"email": "seu-login@distribuidora.com.br", "senha": "********"}
    credenciais.carregar = lambda fornecedor: dict(_DEMO_CRED)
    credenciais.carregar_chave_api = lambda provider: "AIzaSy-demo-xxxxxxxxxxxxxxxxxxxx"
st.session_state.tela = tela
st.session_state.orgao = "Prefeitura Municipal de Araruna"
st.session_state.numero = "00027/2026"


def _item(n, d, u, q, v, c="extraido"):
    return ItemExtraido(item_numero=n, descricao=d, unidade=u,
                        quantidade=q, valor_referencia=v, confianca=c)


_ITENS = [
    _item(1, "ABRAÇADEIRA DE NYLON 100 X 2,5MM, PACOTE C/100 UNIDADES", "PCT", 50, 5.20),
    _item(2, "TUBO HIDRÁULICO, MATERIAL PVC RÍGIDO, DIÂMETRO 100, SOLDÁVEL, COMPRIMENTO 6M", "UN", 30, 92.00),
    _item(3, "VÁLVULA DE ESCOAMENTO, MATERIAL PVC, DIÂMETRO 7/8, APLICAÇÃO LAVATÓRIO", "UN", 120, 4.50, "duvidoso"),
]

if tela == "revisao":
    st.session_state.extracao = ResultadoExtracao(
        arquivo="Edital_PE_00027-2026.pdf",
        itens=_ITENS,
        tabelas_brutas=[[["x"]]],
        mapa_colunas_detectado={},
        avisos=[],
        confianca_global=0.97,
    )

if tela == "busca_precos":
    st.session_state.itens_revisados = _ITENS

    def R(d, m, p, c, forn="almeida", **kw):
        return ResultadoBusca(d, m, p, c, "exato", forn, **kw)

    st.session_state.resultados_busca = {
        0: [R("ABRAÇADEIRA DE NYLON FERTAK PRETO 2.5X20", "FERTAK", 5.20, "52763",
              no_catalogo=True, produto_catalogo_id=1, descricao_catalogo="abracadeira nylon 100"),
            R("ABRAÇADEIRA DE NYLON FOXLUX BRANCA 2.5X20", "FOXLUX", 6.20, "12608"),
            R("ABRAÇADEIRA DE NYLON THOMPSON BRANCA 2.5X20", "THOMPSON", 5.80, "18838")],
        1: [R("TUBO PVC SOLDÁVEL 100MM 6M", "PLASTUBOS", 92.00, "8811", forn="dantas"),
            R("TUBO PVC SOLDÁVEL 100MM BRANCO 6M", "AMANCO", 98.50, "1530")],
        2: [R("VÁLVULA P/ LAVATÓRIO 1603 SEM LADRÃO L.R.METAIS", "L.R.METAIS", 14.10, "19236"),
            R("VÁLVULA P/ LAVATÓRIO ESCOAMENTO GHELPLUS", "GHEL PLUS", 15.87, "17726"),
            R("VÁLVULA P/ LAVATÓRIO PLÁSTICO REBOUÇAS 7/8", "REBOUÇAS", 3.90, "46439")],
    }
    st.session_state.selecoes = {
        0: SelecaoFornecedor(preco=5.20, codigo_fornecedor="52763", marca="FERTAK"),
        1: SelecaoFornecedor(preco=92.00, codigo_fornecedor="8811", marca="PLASTUBOS"),
        2: SelecaoFornecedor(preco=14.10, codigo_fornecedor="19236", marca="L.R.METAIS"),
    }

import pregoes.ui.app  # noqa: E402,F401 — roda o router com o estado pré-carregado
