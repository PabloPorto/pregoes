# Interface web nova (2026-11)

Substitui o Streamlit. Mesmo backend (`pregoes/`), mesmos dados (`data/`),
mesmos rascunhos — um pregão começado numa interface abre na outra.
Arquitetura: `docs/ARQUITETURA_WEB.md`. Inventário de funções da interface
antiga (lista de paridade): `docs/INVENTARIO_UI.md`.

## O que muda para o operador

- **Cada pregão tem endereço próprio** e o trabalho fica salvo no servidor a
  cada clique: fechar o navegador, recarregar a página ou o colega abrir o
  mesmo pregão não perde nada.
- **Leitura do PDF e busca de preços rodam no servidor.** Um lote de horas não
  depende mais da aba aberta; dá para acompanhar, sair, voltar, cancelar.
- Tela de preços: filtros por situação (com preço / a conferir / sem resultado
  / sem preço), busca por texto, opções lado a lado, escolha com 1 clique.
- Revisão: edição direto na tabela com gravação automática, filtro, itens
  duvidosos destacados, incluir/excluir linha. Reabrir o pregão traz as
  edições de volta (na antiga elas se perdiam).
- Etapa "Planilha" própria (tributação, margem, gerar, baixar). O pregão não
  some depois de gerar: dá para reabrir pelo histórico, ajustar e gerar de novo.
- Planilhas geradas: todas as versões de cada pregão para baixar.
- Novas telas: **Meu catálogo** (produtos, códigos, último preço) e **Variação
  de preços**. Em Configurações, aba **Sistema** (versão, backup, últimas
  linhas do log) para o suporte. Tema claro/escuro.
- As senhas dos fornecedores e a chave da IA nunca voltam para a tela.

## Rodar

```bash
python run_web.py            # http://127.0.0.1:8502
PREGOES_RAIZ=/outra/pasta python run_web.py   # com uma CÓPIA dos dados
```

Sem dependência nova: Starlette, Jinja2 e uvicorn já vêm com o Streamlit.
Quando o Streamlit sair do `requirements.txt`, fixar essas três + `python-multipart`.

## No servidor

**Desde 2026-11-17 esta é a interface de produção** (`deploy/pregoes-web.service`,
porta 8501, na raiz do endereço, atrás do mesmo login). O Streamlit
(`pregoes.service`) está desligado e desabilitado; o código dele continua no
repositório como plano de volta. Entre 15 e 17/11 as duas conviveram (a nova
em `/novo`): endereços daquela fase (`/novo/...`) redirecionam para o lugar
certo.

Voltar ao Streamlit (as duas usam a porta 8501 — nunca as duas ligadas):

```bash
ssh -i ~/.ssh/pregoes_vps root@IP 'systemctl disable --now pregoes-web && systemctl enable --now pregoes'
```

Os dados não mudam de lugar nem de formato em nenhum dos sentidos. O que se
perde voltando: a busca com a página fechada, a retomada após reinício e a
escolha do ritmo pela tela (o ritmo salvo continua valendo).

A velocidade da busca em lote (rápida × segura) se escolhe em Configurações →
Sistema (Q20). A busca continua com a página fechada e é retomada sozinha se
o serviço reiniciar.
