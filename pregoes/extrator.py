"""
Extração de itens de PDFs de edital.

Estratégia em duas passadas (resp 9 — schemas variáveis):

PASSADA 1 — heurística automática:
  - pdfplumber abre o PDF, varre todas as páginas, extrai todas as tabelas
  - identifica linha de cabeçalho por palavras-chave conhecidas
  - mapeia colunas para campos canônicos (descrição, quantidade, etc.)

PASSADA 2 — quando heurística falha:
  - retorna a tabela bruta + matriz de cabeçalhos detectados
  - a UI exibe e o usuário faz mapeamento manual
  - mapeamento é gravado em `mapeamentos_pdf` para o próximo edital

O cliente disse que SEMPRE tem nome do produto e quantidade — então mesmo
com schema variável, esses dois campos aparecem em alguma coluna. A
heurística identifica qual.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber


# === Vocabulário de cabeçalhos ===
# Ordem importa: a primeira palavra-chave que casa em uma célula define o campo.

PALAVRAS_CHAVE = {
    "descricao": [
        "discriminação", "discriminacao", "descrição", "descricao",
        "especificação", "especificacao", "especificações", "especificacoes",
        "produto", "objeto", "item descrição",
    ],
    "quantidade": [
        "quantidade", "quant", "qtd", "qtde",
    ],
    "unidade": [
        "unidade", "unid", "und", "u.m.", "um",
    ],
    "valor_referencia": [
        "valor unit", "valor de referência", "valor de referencia",
        "preço unit", "preco unit", "preço estimado", "preco estimado",
        "valor unitário", "valor unitario", "preço de referência",
    ],
    "codigo_orgao": [
        "código", "codigo", "item", "nº",
    ],
}


@dataclass
class ItemExtraido:
    """Uma linha de produto extraída do edital."""
    item_numero: Optional[int] = None
    codigo_orgao: Optional[str] = None
    descricao: str = ""
    unidade: Optional[str] = None
    quantidade: Optional[float] = None
    valor_referencia: Optional[float] = None
    pagina: Optional[int] = None
    confianca: str = "extraido"   # "extraido" | "duvidoso" | "lixo"


@dataclass
class ResultadoExtracao:
    """Tudo que saiu do PDF mais metadados."""
    arquivo: str
    itens: list[ItemExtraido] = field(default_factory=list)
    tabelas_brutas: list[list[list[str]]] = field(default_factory=list)
    mapa_colunas_detectado: dict[str, int] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)

    @property
    def total_itens(self) -> int:
        return len(self.itens)

    @property
    def total_duvidosos(self) -> int:
        return sum(1 for i in self.itens if i.confianca == "duvidoso")


# === Funções utilitárias ===

def _normaliza(texto: str) -> str:
    """Caixa baixa e remove acentos para comparar cabeçalhos."""
    if not texto:
        return ""
    import unicodedata
    s = unicodedata.normalize("NFD", str(texto))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def _para_numero(valor) -> Optional[float]:
    """Converte string brasileira ('1.234,56') para float."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip()
    # Remove R$, espaços
    s = re.sub(r"[R\$\s]", "", s)
    # Casos: "1.234,56" -> "1234.56"; "1234,56" -> "1234.56"; "1234.56" -> "1234.56"
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _para_int(valor) -> Optional[int]:
    n = _para_numero(valor)
    return int(n) if n is not None else None


def _identifica_cabecalho(linha: list[str]) -> dict[str, int]:
    """
    Dado uma linha (presumida cabeçalho), retorna mapa {campo: indice_coluna}.
    Retorna dict vazio se não conseguiu identificar nada útil.
    """
    mapa = {}
    for idx, celula in enumerate(linha):
        celula_norm = _normaliza(celula)
        if not celula_norm:
            continue
        for campo, palavras in PALAVRAS_CHAVE.items():
            if campo in mapa:
                continue
            for pal in palavras:
                if pal in celula_norm:
                    mapa[campo] = idx
                    break
    return mapa


def _eh_cabecalho_valido(mapa: dict[str, int]) -> bool:
    """Cabeçalho útil precisa ter pelo menos descrição + quantidade
    (mínimo garantido pelo cliente)."""
    return "descricao" in mapa and "quantidade" in mapa


def _eh_modelo_de_proposta(linha: list[str]) -> bool:
    """Detecta tabelas que são o modelo de proposta em branco do edital.

    O 'Anexo - Modelo de Proposta' costuma vir com a coluna 'MARCA/MODELO'
    (ou 'MARCA' isolada) entre descrição e unidade — diferente da tabela
    real do Termo de Referência. Se entrarmos nela, capturamos os 2-3 itens
    de exemplo (1, 2, 3 + 'Etc.') como se fossem do edital.

    TODO(extrator): heurística frágil. Casa em um único marcador textual
    ('marca/modelo' no cabeçalho) específico do edital de Araruna 06/2026.
    Outros editais podem usar 'FABRICANTE', 'PROCEDÊNCIA', 'MODELO/MARCA'
    em ordem diferente, ou anexar o modelo de proposta como tabela com
    cabeçalho idêntico ao do Termo de Referência (e aí esta checagem não
    pega). Revisitar quando aparecer o segundo edital real — provavelmente
    trocando por (a) detecção de "número de item retrocede" entre tabelas
    consecutivas, ou (b) parar de extrair após detectar a linha 'TOTAL'
    do Termo de Referência. Por enquanto serve para destravar o cliente.
    """
    for celula in linha:
        norm = _normaliza(celula)
        if "marca/modelo" in norm or norm in ("marca", "modelo"):
            return True
    return False


# === Extração principal ===

def extrair_pdf(caminho_pdf: str | Path) -> ResultadoExtracao:
    """
    Extrai itens de um PDF de edital.

    Retorna `ResultadoExtracao` mesmo quando falha — a UI vai mostrar
    avisos e oferecer mapeamento manual.
    """
    caminho_pdf = Path(caminho_pdf)
    resultado = ResultadoExtracao(arquivo=str(caminho_pdf))

    with pdfplumber.open(caminho_pdf) as pdf:
        mapa_colunas: dict[str, int] = {}

        for n_pagina, page in enumerate(pdf.pages, start=1):
            tabelas = page.extract_tables() or []
            for tabela in tabelas:
                if not tabela or len(tabela) < 1:
                    continue
                resultado.tabelas_brutas.append(tabela)

                # Procura o cabeçalho nas primeiras 3 linhas (preâmbulos comuns)
                cabecalho_idx = None
                for i in range(min(3, len(tabela))):
                    if _eh_modelo_de_proposta(tabela[i]):
                        # Sai do for e marca a tabela inteira para ser ignorada
                        cabecalho_idx = "ignorar"
                        break
                    candidato = _identifica_cabecalho(tabela[i])
                    if _eh_cabecalho_valido(candidato):
                        mapa_colunas = candidato
                        cabecalho_idx = i
                        break

                if cabecalho_idx == "ignorar":
                    continue

                # Se a tabela atual não tem cabeçalho próprio, mas já vimos um
                # cabeçalho válido em página anterior, assumimos que esta tabela
                # é continuação da anterior (caso típico de tabelas que quebram
                # entre páginas sem repetir o header).
                if cabecalho_idx is None:
                    if mapa_colunas:
                        linha_inicial = 0
                    else:
                        continue
                else:
                    linha_inicial = cabecalho_idx + 1

                if not resultado.mapa_colunas_detectado:
                    resultado.mapa_colunas_detectado = mapa_colunas

                # Extrai linhas de dados
                for linha in tabela[linha_inicial:]:
                    item = _extrai_item(linha, mapa_colunas, n_pagina)
                    if item is not None:
                        resultado.itens.append(item)

    if not resultado.itens:
        if resultado.tabelas_brutas:
            resultado.avisos.append(
                "Tabelas encontradas, mas cabeçalho não identificado. "
                "Mapeamento manual necessário."
            )
        else:
            resultado.avisos.append(
                "Nenhuma tabela detectada no PDF. "
                "Pode ser PDF escaneado (precisa OCR) ou layout incomum."
            )

    return resultado


def _extrai_item(linha: list, mapa: dict[str, int], pagina: int) -> Optional[ItemExtraido]:
    """Extrai uma linha de tabela em ItemExtraido. Retorna None se for lixo."""
    if not linha or all(_normaliza(c) == "" for c in linha):
        return None

    def _get(campo: str) -> Optional[str]:
        idx = mapa.get(campo)
        if idx is None or idx >= len(linha):
            return None
        v = linha[idx]
        if v is None:
            return None
        return str(v).strip().replace("\n", " ")

    descricao = _get("descricao") or ""
    quantidade = _para_numero(_get("quantidade"))
    valor_ref = _para_numero(_get("valor_referencia"))
    codigo = _get("codigo_orgao")

    # Filtra linhas obviamente lixo (totais, subtotais, vazias)
    if not descricao or len(descricao) < 3:
        return None

    descr_norm = _normaliza(descricao)
    if any(p in descr_norm for p in ("total", "subtotal", "valor global", "etc.")):
        return None

    # Fragmentos órfãos: linhas em que o pdfplumber quebrou uma descrição
    # longa (ex: "KG.", "URBANA)") chegam sem nenhum campo numérico e com
    # descrição curta. Não é item; é continuação visual da linha anterior.
    if (
        len(descricao) < 12
        and quantidade is None
        and valor_ref is None
        and not codigo
    ):
        return None

    item = ItemExtraido(
        item_numero=_para_int(codigo),
        codigo_orgao=codigo,
        descricao=descricao,
        unidade=_get("unidade"),
        quantidade=quantidade,
        valor_referencia=valor_ref,
        pagina=pagina,
    )

    # Heurísticas de qualidade
    if item.quantidade is None or item.quantidade <= 0:
        item.confianca = "duvidoso"

    return item
