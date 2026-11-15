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

Fase atual — **as duas lado a lado**: a antiga em `/` (o que os operadores
usam), a nova em `/novo` (`deploy/pregoes-web.service`, porta 8502), na mesma
hostname e atrás do mesmo login.

Trocar de vez (a nova passa a responder em `/`):

```bash
ssh -i ~/.ssh/pregoes_vps root@IP '
  sed -i "s/^Environment=PREGOES_WEB_PORTA=8502/Environment=PREGOES_WEB_PORTA=8501/; /PREGOES_WEB_RAIZ/d" /etc/systemd/system/pregoes-web.service
  systemctl daemon-reload
  systemctl disable --now pregoes && systemctl restart pregoes-web'
```

(e tirar a regra `/novo` de `/home/pregoes/.cloudflared/config.yml` +
`systemctl restart pregoes-tunel`). Voltar atrás: `systemctl disable --now
pregoes-web && systemctl enable --now pregoes`. Os dados não mudam de lugar
nem de formato em nenhum dos sentidos.

A velocidade da busca em lote (rápida × segura) se escolhe em Configurações →
Sistema, só na interface nova — mas vale para as duas, porque é do motor de
busca. Só a interface nova continua a busca com a página fechada e a retoma
sozinha se o serviço reiniciar; na antiga a aba precisa ficar aberta.

Cuidado enquanto as duas estão no ar: cada uma conta sozinha o limite de
consultas dos fornecedores (Q20). Dois lotes grandes ao mesmo tempo, um em cada
interface, podem encostar no limite — nada se perde (o lote pausa e retoma),
só demora mais.
