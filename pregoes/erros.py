"""
Tradução de exceções técnicas em mensagens acionáveis para o operador leigo
(P0.4).

O operador não deve ver "RuntimeError: cookies de sessão não estabelecidos"
nem stacktraces de Playwright/Gemini. `mensagem_amigavel` mapeia os erros
comuns para um português claro com o que fazer. O detalhe técnico vai para o
log (data/pregoes.log), não para a tela.
"""
from __future__ import annotations

_RODAPE_LOG = " (O detalhe técnico foi salvo em data/pregoes.log.)"


def mensagem_amigavel(exc: Exception, contexto: str = "") -> str:
    """Devolve uma mensagem clara em PT-BR para a exceção dada.

    `contexto` é uma ação curta para o fallback genérico (ex: "buscar preços",
    "ler o PDF"). As regras vão da mais específica para a mais genérica.
    """
    texto = str(exc).lower()
    tipo = type(exc).__name__.lower()

    # Quota / billing do Gemini
    if ("resource_exhausted" in texto or "429" in texto
            or "quota" in texto or "billing" in texto):
        return ("A IA (Gemini) atingiu o limite de uso do dia. A extração e a "
                "padronização por IA ficam indisponíveis até o limite renovar "
                "— o sistema continua funcionando com a busca normal." + _RODAPE_LOG)

    # Chave Gemini inválida
    if "api key" in texto or "api_key" in texto or "permission denied" in texto:
        return ("A chave da IA (Gemini) parece inválida. Verifique em "
                "Configurações → IA." + _RODAPE_LOG)

    # Login no fornecedor
    if ("cookies de sessão" in texto or "falha no login" in texto
            or "login" in texto):
        forn = ""
        if "almeida" in texto:
            forn = " da Almeida"
        elif "dantas" in texto:
            forn = " da Dantas"
        return (f"Não consegui entrar no site{forn}. Confira o e-mail e a senha "
                "em Configurações. Se estiverem certos, o site pode estar "
                "instável — tente de novo em alguns minutos." + _RODAPE_LOG)

    # Sem internet / rede
    if any(k in texto for k in (
            "getaddrinfo", "name or service not known", "err_internet",
            "net::", "connection refused", "connection error",
            "failed to establish", "max retries", "nameresolutionerror",
            "temporary failure in name resolution")):
        return ("Parece que está sem internet. Verifique a conexão e tente de "
                "novo." + _RODAPE_LOG)

    # Timeout (site lento, Playwright, etc.)
    if "timeout" in tipo or "timed out" in texto or "timeout" in texto:
        return ("A operação demorou demais — o site do fornecedor pode estar "
                "lento agora. Tente de novo em alguns minutos." + _RODAPE_LOG)

    # Fallback genérico
    acao = f" ao {contexto}" if contexto else ""
    return (f"Algo deu errado{acao}. Tente de novo; se persistir, envie o "
            "arquivo data/pregoes.log para o suporte." + _RODAPE_LOG)
