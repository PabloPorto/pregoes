<#
.SYNOPSIS
    Instala/atualiza o Pregões a partir de um .zip, PRESERVANDO os dados e as
    credenciais do cliente.

.DESCRIPTION
    Automatiza a "Parte C — Atualizar uma máquina que já roda o sistema" do
    GUIA_INSTALACAO_WINDOWS.md, que hoje é manual e tem um passo perigoso:
    "apague tudo dentro de C:\Pregoes EXCETO a pasta data\". Um deslize ali é
    perda irreversível — a pasta `data\` guarda o cérebro acumulado do sistema.

    O que é preservado (tudo vive em `data\`):
      - credenciais.json .... e-mail/senha da Almeida e da Dantas, chave da API
                              do Gemini, condição de pagamento por fornecedor,
                              margem de venda e a PASTA DE BACKUP do Google
                              Drive. É o arquivo que concentra TODAS as
                              credenciais — não há OAuth do Google neste app.
      - pregoes.db .......... catálogo, histórico de preços e o aprendizado
      - backups\ ............ backups diários e pre-migration
      - historico\, exports\, editais\, diagnostico\

    Regras de segurança embutidas:
      1. Recusa rodar com o app aberto (arquivo travado = instalação corrompida).
      2. Faz backup da `data\` ANTES de tocar em qualquer coisa.
      3. NUNCA copia um `data\` que venha dentro do .zip por cima do do cliente.
      4. Confere no fim que as credenciais continuam legíveis.

.PARAMETER Zip
    Caminho do .zip com o programa novo (a pasta `dist\Pregoes\` empacotada).

.PARAMETER Destino
    Pasta de instalação. Padrão: C:\Pregoes. O script detecta sozinho se o
    Pregoes.exe está na raiz ou num nível aninhado (ex.: instalações antigas
    ficaram em C:\Pregoes\dist\Pregoes\).

.PARAMETER PastaBackup
    Onde guardar o backup da `data\`. Padrão: a área de trabalho do usuário.

.EXAMPLE
    .\instalar_atualizacao.ps1 -Zip D:\Pregoes_v1.1.9.zip

.EXAMPLE
    .\instalar_atualizacao.ps1 -Zip D:\Pregoes_v1.1.9.zip -Destino C:\Pregoes
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Zip,

    [string]$Destino = "C:\Pregoes",

    [string]$PastaBackup = [Environment]::GetFolderPath("Desktop")
)

$ErrorActionPreference = "Stop"

function Titulo($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Ok($t)     { Write-Host "  [ok] $t" -ForegroundColor Green }
function Aviso($t)  { Write-Host "  [!]  $t" -ForegroundColor Yellow }
function Erro($t)   { Write-Host "`n[ERRO] $t`n" -ForegroundColor Red; exit 1 }

# Carimbo de tempo estável para nomear backup e pasta temporária.
$carimbo = Get-Date -Format "yyyyMMdd_HHmmss"

Titulo "1/7  Conferindo o pacote"

if (-not (Test-Path -LiteralPath $Zip -PathType Leaf)) {
    Erro "Não achei o arquivo .zip em: $Zip"
}
if ([IO.Path]::GetExtension($Zip) -ne ".zip") {
    Erro "O arquivo informado não é um .zip: $Zip"
}
$zipMb = [math]::Round((Get-Item -LiteralPath $Zip).Length / 1MB, 1)
Ok "Pacote: $Zip ($zipMb MB)"

# --- 2. Onde o programa está instalado -------------------------------------
# O guia manda copiar o CONTEÚDO de dist\Pregoes\ para C:\Pregoes, mas há
# instalações no campo com um nível a mais (C:\Pregoes\dist\Pregoes\) — o
# traceback do cliente em 2026-08-31 veio desse layout. Detectamos os dois em
# vez de assumir, senão o script atualizaria uma pasta e o cliente continuaria
# abrindo a outra (bug silencioso: "atualizei e não mudou nada").
Titulo "2/7  Localizando a instalação"

$raizApp = $null
if (Test-Path -LiteralPath $Destino) {
    $exes = @(Get-ChildItem -LiteralPath $Destino -Filter "Pregoes.exe" -Recurse -File -ErrorAction SilentlyContinue)

    # Máquinas onde alguém rodou o build_exe.bat no lugar têm DOIS Pregoes.exe:
    # o de verdade em dist\Pregoes\ e um artefato intermediário do PyInstaller em
    # build\pregoes\. Só o primeiro é executável — o modo onedir põe as
    # dependências numa pasta `_internal` ao lado do .exe, e o intermediário não
    # tem essa pasta. Filtrar por `_internal` distingue pela ESTRUTURA, sem
    # depender do nome da pasta (mais robusto que ignorar "build\" no caminho).
    $reais = @($exes | Where-Object { Test-Path -LiteralPath (Join-Path $_.Directory.FullName "_internal") })
    if ($reais.Count -gt 0 -and $reais.Count -lt $exes.Count) {
        Aviso "Ignorando $($exes.Count - $reais.Count) Pregoes.exe sem pasta _internal (sobra de build, não é instalação)."
        $exes = $reais
    }

    if ($exes.Count -eq 1) {
        $raizApp = $exes[0].Directory.FullName
    } elseif ($exes.Count -gt 1) {
        Aviso "Mais de um Pregoes.exe instalável em $Destino :"
        $exes | ForEach-Object { Write-Host "       $($_.FullName)" }
        Erro "Ambíguo — rode de novo apontando -Destino para a pasta certa."
    }
}

$instalacaoNova = $false
if ($null -eq $raizApp) {
    $instalacaoNova = $true
    $raizApp = $Destino
    Aviso "Nenhum Pregoes.exe encontrado — tratando como INSTALAÇÃO NOVA."
    Aviso "Se esta máquina JÁ tinha o sistema, pare agora (Ctrl+C) e confira o -Destino."
} else {
    Ok "Instalação encontrada em: $raizApp"
}

$pastaDados = Join-Path $raizApp "data"
$arqCred    = Join-Path $pastaDados "credenciais.json"

# --- 3. O app não pode estar aberto ----------------------------------------
# Copiar por cima de um .exe/.dll em uso falha no meio e deixa a instalação
# pela metade — pior que não ter atualizado.
Titulo "3/7  Conferindo se o app está fechado"

# Só interessa o Pregões que roda DESTA pasta. Bloquear por qualquer processo
# de mesmo nome impediria atualizar uma instalação por causa de outra (e trava
# o teste automatizado do próprio script). Processos de outras pastas viram
# aviso, não erro.
$todos = @(Get-Process -Name "Pregoes" -ErrorAction SilentlyContinue)
$daPasta = @($todos | Where-Object {
    $p = $null
    try { $p = $_.Path } catch { }   # processo de outro usuário nega o Path
    $p -and $p.StartsWith($raizApp, [StringComparison]::OrdinalIgnoreCase)
})

if ($daPasta.Count -gt 0) {
    Erro @"
O Pregões desta pasta está ABERTO (PID $($daPasta.Id -join ', ')).
  $($daPasta[0].Path)
Feche a janela do navegador E o programa, depois rode de novo.
Atualizar com o app aberto deixa a instalação pela metade.
"@
}
if ($todos.Count -gt 0) {
    Aviso "Há Pregões rodando de OUTRA pasta ($($todos.Count) processo(s)) — não impede esta atualização."
}
Ok "Nenhum Pregoes.exe rodando a partir de $raizApp"

# --- 4. Backup da data\ ANTES de qualquer alteração ------------------------
Titulo "4/7  Backup dos dados do cliente"

$versaoAntes = "(desconhecida)"
$temDados = (-not $instalacaoNova) -and (Test-Path -LiteralPath $pastaDados)

if ($temDados) {
    # Inventário do que existe, para conferir depois da troca.
    $credAntes = $null
    if (Test-Path -LiteralPath $arqCred) {
        try {
            $credAntes = Get-Content -LiteralPath $arqCred -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            Aviso "credenciais.json existe mas não é JSON válido — será preservado como está."
        }
    }

    $destBackup = Join-Path $PastaBackup "Pregoes_backup_data_$carimbo.zip"
    Write-Host "  Compactando $pastaDados ..."
    # -Force para sobrescrever um backup do mesmo segundo; o nome tem carimbo,
    # então na prática nunca colide.
    Compress-Archive -Path (Join-Path $pastaDados "*") -DestinationPath $destBackup -Force
    $bkMb = [math]::Round((Get-Item -LiteralPath $destBackup).Length / 1MB, 1)
    Ok "Backup: $destBackup ($bkMb MB)"
    Write-Host "       Guarde este arquivo até confirmar que a nova versão funciona." -ForegroundColor DarkGray
} else {
    Ok "Sem data\ anterior (instalação nova) — nada a preservar."
}

# --- 5. Extrair o pacote novo ----------------------------------------------
Titulo "5/7  Extraindo o pacote"

$tmp = Join-Path $env:TEMP "pregoes_update_$carimbo"
if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
New-Item -ItemType Directory -Path $tmp -Force | Out-Null

Expand-Archive -LiteralPath $Zip -DestinationPath $tmp -Force

# O zip pode ter sido feito de jeitos diferentes (conteúdo na raiz, dentro de
# Pregoes\, ou dentro de dist\Pregoes\). Achamos a pasta que REALMENTE contém
# o Pregoes.exe em vez de exigir um formato específico de quem empacotou.
$exeNovo = Get-ChildItem -LiteralPath $tmp -Filter "Pregoes.exe" -Recurse -File -ErrorAction SilentlyContinue |
           Select-Object -First 1
if ($null -eq $exeNovo) {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Erro "O .zip não contém Pregoes.exe. Este script instala o pacote BUILDADO (dist\Pregoes\), não o código-fonte."
}
$origem = $exeNovo.Directory.FullName
Ok "Programa novo em: $origem"

# --- 6. Trocar o programa, preservando data\ -------------------------------
Titulo "6/7  Trocando o programa (preservando data\)"

if (-not $instalacaoNova) {
    # Apaga só o PROGRAMA. `data\` fica intocada — é a regra de ouro do guia.
    Get-ChildItem -LiteralPath $raizApp -Force |
        Where-Object { $_.Name -ne "data" } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
    Ok "Arquivos antigos do programa removidos (data\ preservada)"
} else {
    New-Item -ItemType Directory -Path $raizApp -Force | Out-Null
}

# Copia tudo MENOS um eventual data\ vindo no zip. Um data\ do build carrega o
# banco de TESTE do desenvolvedor; sobrescrever o do cliente apagaria catálogo,
# histórico e aprendizado — exatamente o que o guia marca como irreversível.
Get-ChildItem -LiteralPath $origem -Force |
    Where-Object { $_.Name -ne "data" } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $raizApp -Recurse -Force }
Ok "Programa novo instalado em: $raizApp"

$dataNoZip = Join-Path $origem "data"
if (Test-Path -LiteralPath $dataNoZip) {
    if ($instalacaoNova) {
        # Numa instalação nova não há o que proteger: o data\ do pacote traz o
        # template da planilha e serve de ponto de partida.
        Copy-Item -LiteralPath $dataNoZip -Destination $raizApp -Recurse -Force
        Ok "data\ do pacote copiada (instalação nova)"
    } else {
        Aviso "O .zip trazia uma pasta data\ — IGNORADA de propósito (a do cliente vale mais)."
    }
}

Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue

# --- 7. Conferência final ---------------------------------------------------
Titulo "7/7  Conferindo o resultado"

if (-not (Test-Path -LiteralPath (Join-Path $raizApp "Pregoes.exe"))) {
    Erro "Pregoes.exe não está em $raizApp após a cópia. Restaure o backup e tente de novo."
}
Ok "Pregoes.exe no lugar"

if ($temDados) {
    if (-not (Test-Path -LiteralPath $pastaDados)) {
        Erro "A pasta data\ SUMIU. Restaure já o backup gerado no passo 4."
    }
    $banco = Join-Path $pastaDados "pregoes.db"
    if (Test-Path -LiteralPath $banco) {
        $dbMb = [math]::Round((Get-Item -LiteralPath $banco).Length / 1MB, 1)
        Ok "Banco preservado: pregoes.db ($dbMb MB)"
    } else {
        Aviso "pregoes.db não encontrado em data\ — confira antes de usar."
    }

    if (Test-Path -LiteralPath $arqCred) {
        try {
            $cred = Get-Content -LiteralPath $arqCred -Raw -Encoding UTF8 | ConvertFrom-Json
            $achados = @()
            foreach ($f in @("almeida", "dantas")) {
                if ($cred.$f -and $cred.$f.email) { $achados += "$f ($($cred.$f.email))" }
            }
            if ($cred.gemini -and $cred.gemini.api_key) { $achados += "chave do Gemini" }
            if ($cred.pasta_backup)                     { $achados += "pasta de backup: $($cred.pasta_backup)" }
            if ($cred.preferencias_pagamento)           { $achados += "condição de pagamento" }
            if ($cred.margem_venda)                     { $achados += "margem de venda ($($cred.margem_venda))" }

            Ok "Credenciais preservadas:"
            $achados | ForEach-Object { Write-Host "         - $_" -ForegroundColor Green }

            if ($cred.pasta_backup -and -not (Test-Path -LiteralPath $cred.pasta_backup)) {
                Aviso "A pasta de backup configurada não existe nesta máquina: $($cred.pasta_backup)"
                Aviso "Se o Google Drive ainda não sincronizou, o backup off-site fica parado até ela aparecer."
            }
        } catch {
            Aviso "credenciais.json preservado, mas não consegui lê-lo como JSON. Confira em Configurações."
        }
    } else {
        Aviso "credenciais.json NÃO existe — o operador vai precisar preencher tudo em Configurações."
    }
}

Titulo "PRONTO"
Write-Host @"
  Abra o Pregões e confirme na barra lateral que a VERSÃO subiu.
  Depois faça uma busca de teste em um item para validar o login nos
  fornecedores.

  Se algo der errado, o backup dos dados está em:
    $PastaBackup
"@ -ForegroundColor White
