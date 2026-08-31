<#
.SYNOPSIS
    Teste automatizado do instalar_atualizacao.ps1, num cenário FALSO.

.DESCRIPTION
    O instalador apaga arquivos na pasta do cliente. Um erro ali é perda
    irreversível (catálogo, aprendizado, credenciais), então ele não deve ser
    alterado sem rodar este teste.

    Monta em pasta temporária uma "instalação do cliente" no layout aninhado
    (C:\Pregoes\dist\Pregoes\ — o mesmo do traceback de campo 2026-08-31), com
    banco e credenciais preenchidas, e um .zip da versão nova contendo um
    `data\` ISCA. Roda o instalador e confere que:

      1. o programa foi de fato trocado;
      2. o banco do cliente sobreviveu;
      3. o `data\` isca do .zip NÃO sobrescreveu o do cliente;
      4. cada credencial continua legível (fornecedores, Gemini, pasta de
         backup do Drive, condição de pagamento, margem);
      5. sobras da versão antiga foram removidas;
      6. o backup de segurança foi gerado.

.EXAMPLE
    .\testar_instalar_atualizacao.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$instalador = Join-Path $PSScriptRoot "instalar_atualizacao.ps1"
if (-not (Test-Path -LiteralPath $instalador)) {
    Write-Host "[ERRO] não achei o instalador em $instalador" -ForegroundColor Red
    exit 1
}

$base = Join-Path ([IO.Path]::GetTempPath()) "pregoes_teste_instalador"
Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $base -Force | Out-Null

# --- instalação "do cliente": layout aninhado, com dados reais simulados ---
$cliente = Join-Path $base "C_Pregoes"
$app = Join-Path $cliente "dist\Pregoes"
New-Item -ItemType Directory -Path (Join-Path $app "_internal\pregoes") -Force | Out-Null

# Sobra do build_exe.bat rodado no lugar (campo 2026-08-31): um Pregoes.exe
# intermediário do PyInstaller em build\pregoes\, SEM a pasta _internal. O
# instalador tem que ignorá-lo e não travar por ambiguidade.
$iscaBuild = Join-Path $cliente "build\pregoes"
New-Item -ItemType Directory -Path $iscaBuild -Force | Out-Null
Set-Content -Path (Join-Path $iscaBuild "Pregoes.exe") -Value "ARTEFATO-DE-BUILD" -Encoding utf8
New-Item -ItemType Directory -Path (Join-Path $app "data\backups") -Force | Out-Null
Set-Content -Path (Join-Path $app "Pregoes.exe") -Value "EXE-VERSAO-ANTIGA" -Encoding utf8
Set-Content -Path (Join-Path $app "lixo_versao_antiga.dll") -Value "sobra" -Encoding utf8
Set-Content -Path (Join-Path $app "data\pregoes.db") -Value "BANCO-REAL-DO-CLIENTE" -Encoding utf8
Set-Content -Path (Join-Path $app "data\backups\bk1.db") -Value "backup antigo" -Encoding utf8
@'
{
  "almeida": { "email": "cliente@empresa.com.br", "senha": "senhaAlmeida1!" },
  "dantas":  { "email": "cliente@empresa.com.br", "senha": "senhaDantas2!" },
  "gemini":  { "api_key": "AIzaSyFAKE-chave" },
  "pasta_backup": "C:\\Drive\\BackupPregoes",
  "margem_venda": "AB",
  "preferencias_pagamento": {
    "almeida": { "forma_value": "3", "forma_texto": "PIX", "prazo_value": "", "prazo_texto": "" }
  }
}
'@ | Set-Content -Path (Join-Path $app "data\credenciais.json") -Encoding utf8

# --- pacote novo, com data\ ISCA que não pode vazar para o cliente ---
$stage = Join-Path $base "stage\Pregoes"
New-Item -ItemType Directory -Path (Join-Path $stage "_internal\pregoes") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stage "data\templates") -Force | Out-Null
Set-Content -Path (Join-Path $stage "Pregoes.exe") -Value "EXE-VERSAO-NOVA" -Encoding utf8
Set-Content -Path (Join-Path $stage "data\pregoes.db") -Value "BANCO-DE-TESTE-DO-DEV" -Encoding utf8
Set-Content -Path (Join-Path $stage "data\templates\matriz_template.xlsx") -Value "template" -Encoding utf8

$zip = Join-Path $base "Pregoes_novo.zip"
Compress-Archive -Path $stage -DestinationPath $zip -Force

$bkDir = Join-Path $base "backups_gerados"
New-Item -ItemType Directory -Path $bkDir -Force | Out-Null

Write-Host "######## saída do instalador ########" -ForegroundColor DarkGray
& $instalador -Zip $zip -Destino $cliente -PastaBackup $bkDir
Write-Host "######## verificação ########" -ForegroundColor DarkGray

$falhas = @()
$exe  = Get-Content -LiteralPath (Join-Path $app "Pregoes.exe") -Raw
$db   = Get-Content -LiteralPath (Join-Path $app "data\pregoes.db") -Raw
$cred = Get-Content -LiteralPath (Join-Path $app "data\credenciais.json") -Raw | ConvertFrom-Json

if ($exe -notmatch "VERSAO-NOVA")           { $falhas += "o programa NÃO foi trocado" }
if ($db  -notmatch "BANCO-REAL-DO-CLIENTE") { $falhas += "o banco do cliente foi perdido/sobrescrito" }
if ($db  -match  "BANCO-DE-TESTE-DO-DEV")   { $falhas += "o data\ do .zip sobrescreveu o do cliente" }
if ($cred.almeida.senha  -ne "senhaAlmeida1!")    { $falhas += "senha da Almeida perdida" }
if ($cred.dantas.senha   -ne "senhaDantas2!")     { $falhas += "senha da Dantas perdida" }
if ($cred.gemini.api_key -notmatch "AIzaSyFAKE")  { $falhas += "chave do Gemini perdida" }
if ($cred.pasta_backup   -notmatch "BackupPregoes") { $falhas += "pasta de backup do Drive perdida" }
if ($cred.margem_venda   -ne "AB")                  { $falhas += "margem de venda perdida" }
if (-not $cred.preferencias_pagamento)              { $falhas += "condição de pagamento perdida" }
if (-not (Test-Path (Join-Path $app "data\backups\bk1.db")))    { $falhas += "backups antigos perdidos" }
if (Test-Path (Join-Path $app "lixo_versao_antiga.dll"))        { $falhas += "sobra da versão antiga não foi removida" }
# a isca de build\ não pode ter sido escolhida como alvo nem sido tocada
$isca = Join-Path $cliente "build\pregoes\Pregoes.exe"
if (-not (Test-Path $isca))                                       { $falhas += "o instalador mexeu na pasta build\ (não deveria)" }
elseif ((Get-Content $isca -Raw) -notmatch "ARTEFATO-DE-BUILD")    { $falhas += "o instalador instalou por cima do artefato de build" }
if (-not (Get-ChildItem $bkDir -Filter *.zip))                  { $falhas += "backup de segurança não foi gerado" }

Write-Host ""
if ($falhas.Count -eq 0) {
    Write-Host "TESTE PASSOU — programa trocado, dados e credenciais intactos." -ForegroundColor Green
    Remove-Item -LiteralPath $base -Recurse -Force -ErrorAction SilentlyContinue
    exit 0
} else {
    Write-Host "TESTE FALHOU:" -ForegroundColor Red
    $falhas | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host "Cenário preservado para inspeção em: $base" -ForegroundColor Yellow
    exit 1
}
