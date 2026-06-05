# Empacotamento do `.exe` (P1.1)

Transforma o app num **executável Windows** que o cliente roda com dois cliques,
sem instalar Python. Gera uma pasta portável `dist\Pregoes\` com `Pregoes.exe`.

> ⚠️ **PyInstaller não faz cross-compile.** O `.exe` Windows **tem que ser
> gerado numa máquina Windows** — não dá para buildar no Mac/Linux. Toda a
> preparação (launcher, spec, caminhos frozen-aware) foi feita e validada; o
> build em si roda no seu Windows secundário.

## O que cada peça faz

| Arquivo | Papel |
|---|---|
| `run_app.py` | Ponto de entrada: sobe o Streamlit em `127.0.0.1:8501` e abre o navegador. Validado no Mac. |
| `pregoes.spec` | Receita do PyInstaller (onedir): empacota o app, o Streamlit, o template e — se presente — o Chromium do Playwright. |
| `packaging/pyi_rthook_playwright.py` | Runtime hook: faz o `.exe` achar o Chromium empacotado. |
| `build_exe.bat` | Roda tudo no Windows (venv → deps → `playwright install` → PyInstaller). |
| `pregoes/config.py` | Já **frozen-aware**: no `.exe`, os dados (db, backups, credenciais, exports) ficam em `data\` **ao lado do .exe** (graváveis e persistentes); o template vem do bundle e é semeado na 1ª execução. |

## Como buildar (no Windows)

```bat
cd pregoes_app
build_exe.bat
```

Ao final: **`dist\Pregoes\Pregoes.exe`**. Dois cliques abrem o navegador na UI.
Os dados do cliente ficam em `dist\Pregoes\data\` — para atualizar o app, troque
os arquivos do programa preservando essa pasta `data\`.

## Plano de teste no Windows secundário (o que conferir)

1. **Sobe?** Dois cliques em `Pregoes.exe` → navegador abre a UI em 8501.
2. **Grava dados?** Importe uma planilha / processe um PDF → confirme que
   `dist\Pregoes\data\pregoes.db` e `data\backups\` aparecem (não dentro de
   pasta temporária).
3. **Extração de PDF** funciona (pdfplumber empacotado).
4. **Busca ao vivo** (Playwright/Chromium): faça uma busca → confirme que
   abre o site do fornecedor. Se falhar com "Executable doesn't exist",
   o Chromium não foi empacotado — ver "Playwright" abaixo.
5. **Gemini** (extração/canonização): configure a chave em Configurações e
   teste — a chamada deve funcionar (SDK `google-genai` empacotado).
6. **Exporta o Excel** preservando as fórmulas do MATRIZ.

## Playwright / Chromium (ponto que mais costuma precisar de ajuste)

O `build_exe.bat` roda `playwright install chromium` antes do build, e o
`pregoes.spec` empacota a pasta `ms-playwright` se ela existir. O runtime hook
aponta o `.exe` para ela. **Confirme no teste 4 acima.** Se não funcionar:

- Verifique no console do build a linha `[spec] Chromium do Playwright
  empacotado de: ...`. Se aparecer o AVISO em vez disso, o `playwright install`
  não rodou ou foi para outro caminho — rode-o manualmente e rebuilde.
- Alternativa (sem empacotar): deixar o Chromium fora e instalá-lo uma vez na
  máquina do cliente — mas isso exige rodar um comando, o que foge do "leigo".
  Empacotar é o caminho recomendado.

## Gotchas conhecidos (PyInstaller + Streamlit)

- **`server.port does not work when global.developmentMode is true`** (já
  RESOLVIDO no `run_app.py`): dentro do `.exe`, o Streamlit fica em
  `_internal\streamlit\` — sem `site-packages` no caminho, ele se acha em modo
  de desenvolvimento e proíbe `--server.port`, derrubando o app no boot. O
  `run_app.py` força `STREAMLIT_GLOBAL_DEVELOPMENT_MODE=false` antes de o
  Streamlit carregar. Se o erro voltar, confirme que essa linha está lá.
- **`console=True`** no spec deixa uma janela preta junto do app — útil no
  piloto para ver erros. Para a versão final, trocar para `console=False`
  (mais limpo) **depois** de confirmar que está estável.
- Se faltar algum módulo no runtime (`ModuleNotFoundError`), adicione-o em
  `hiddenimports` no `pregoes.spec` e rebuilde.
- Se o Streamlit reclamar de **metadata/versão**, adicione o pacote em
  `copy_metadata(...)` no spec.
- **Antivírus** às vezes marca `.exe` do PyInstaller — onedir (esta config)
  costuma incomodar menos que onefile.
- Tamanho: a pasta sai grande (Streamlit + pandas + pyarrow + Chromium ≈
  algumas centenas de MB). Normal.

## Atualização do app no cliente

Troque os arquivos de `dist\Pregoes\` (exceto a pasta `data\`, que tem o
catálogo/histórico do cliente) pela nova versão. A migration de schema
(`pregoes/migrations.py`) cuida de evoluções do banco automaticamente no boot.
