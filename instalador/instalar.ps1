# Instalador do Dynamic Captions para Adobe Premiere Pro (Windows).
#
# O que ele faz, tudo dentro da sua pasta de usuário:
#   painel  -> %APPDATA%\Adobe\CEP\extensions\com.dynamic.captions
#   motor   -> %LOCALAPPDATA%\DynamicCaptions\motor
#   ajustes -> %LOCALAPPDATA%\DynamicCaptions\launch.json (as variáveis que já existem são mantidas)
#
# O que ele NÃO faz: não mexe no Registro do Windows, nem em configurações de segurança,
# nem apaga projetos, legendas aplicadas ou renders. A versão anterior é copiada para
# %LOCALAPPDATA%\DynamicCaptions\backup\<data-hora> antes de qualquer troca.
#
# Uso: clique duas vezes em "Instalar Dynamic Captions.cmd".
#      -SemDependencias  pula a instalação de bibliotecas do Python (quando já estão prontas).
#      -CertThumbprint X assina os binários sem assinatura com o certificado de produção X
#                        (necessário com o Controle Inteligente de Aplicativos ligado).
#      -PythonExe C:\...\python.exe  Python 3.11/3.12 oficial a usar como base do runtime.
#      -Forcar           instala mesmo se a validação do runtime falhar (só para diagnóstico).
#      -UsoPessoal / -UsoComercial  responde à pergunta do tipo de uso (sem perguntar).
#
# Na primeira vez: se não houver Python 3.11/3.12 oficial, oferece instalar o 3.12 pelo winget (você
# confirma); depois de instalar, baixa os modelos que este computador vai usar (vários GB com placa NVIDIA).
#
# O Python do motor é sempre o runtime de produção em %LOCALAPPDATA%\DynamicCaptions\runtime
# (veja runtime-producao.ps1); um ambiente de teste apontado no launch.json não é reaproveitado.
# O runtime é validado ANTES de trocar motor, launch.json e painel: se não passar, nada disso muda.
# Código de saída: 0 instalado; 2 bloqueado pelo Windows (sem certificado de produção); 1 erro.

param([switch]$SemDependencias, [string]$CertThumbprint = "", [string]$TimestampUrl = "http://timestamp.digicert.com",
      [string]$PythonExe = "", [switch]$Forcar, [switch]$UsoPessoal, [switch]$UsoComercial,
      [switch]$SemPerguntas, [switch]$InstalarPython)
# -SemPerguntas: usado pelo "Instalar Dynamic Captions.exe" (as respostas vêm da janela); grava o registro
#   completo em %LOCALAPPDATA%\DynamicCaptions\instalacao.log. -InstalarPython: instala o Python 3.12 se faltar.

$ErrorActionPreference = "Stop"
# No PowerShell 7 um comando externo que sai com erro vira excecao: aqui os codigos de saida
# sao conferidos a mao (pip, python), entao essa conversao fica desligada.
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}
$Here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Version = (Get-Content (Join-Path $Here "painel\VERSION") -Raw).Trim()
$Base    = Join-Path $env:LOCALAPPDATA "DynamicCaptions"
$ExtRoot = Join-Path $env:APPDATA "Adobe\CEP\extensions"
$Ext     = Join-Path $ExtRoot "com.dynamic.captions"
$Motor   = Join-Path $Base "motor"
$Launch  = Join-Path $Base "launch.json"
$Backup  = Join-Path $Base ("backup\" + (Get-Date -Format "yyyyMMdd-HHmmss"))

# Roda um programa externo e devolve a saída (stdout e stderr) como texto. No Windows PowerShell 5.1,
# com ErrorAction Stop, QUALQUER linha em stderr (um simples aviso do PyTorch, por exemplo) vira erro
# fatal e derrubava o instalador; aqui os avisos são só texto e quem decide é o código de saída.
function Invoke-Native([scriptblock]$Cmd, [switch]$SemErros) {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        & $Cmd 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { if (-not $SemErros) { "$_" } } else { "$_" }
        }
    } finally { $ErrorActionPreference = $old }
}
function Step($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "   $t" -ForegroundColor Green }
function Warn($t) { Write-Host "   $t" -ForegroundColor Yellow }

New-Item -ItemType Directory -Force -Path $Base | Out-Null
if ($SemPerguntas) {
    try { Start-Transcript -Path (Join-Path $Base "instalacao.log") -Force | Out-Null } catch { }
}
Write-Host "Dynamic Captions $Version - instalador" -ForegroundColor White
if (-not $SemPerguntas) {
    Write-Host "Feche o painel Dynamic Captions no Premiere antes de continuar (o Premiere pode ficar aberto)."
    Read-Host "Enter para instalar, ou feche esta janela para cancelar" | Out-Null
}
$cfg = $null
if (Test-Path $Launch) { try { $cfg = Get-Content $Launch -Raw | ConvertFrom-Json } catch { $cfg = $null } }
$EngineJson = Join-Path $Base "engine.json"
function Stop-Engine {
    Step "Encerrando o motor em uso, se estiver aberto"
    if (-not (Test-Path $EngineJson)) { Ok "nenhum motor em execução"; return }
    try {
        $info = Get-Content $EngineJson -Raw | ConvertFrom-Json
        Invoke-RestMethod -Method Post -TimeoutSec 5 -Headers @{ "X-SC-Token" = $info.token } `
            -Uri ("http://127.0.0.1:{0}/v1/engine/shutdown" -f $info.port) | Out-Null
        Start-Sleep -Seconds 2
        Ok "motor encerrado"
    } catch { Ok "nenhum motor respondendo" }
}
function Exit-With($code) { if (-not $SemPerguntas) { Read-Host "Enter para fechar" | Out-Null }; exit $code }

# 0a. Python oficial: sem ele não há motor. Oferece o 3.12 do python.org pelo winget (só para este usuário).
function Find-OfficialPython {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @("3.12", "3.11")) {
            # No Windows PowerShell 5.1, stderr redirecionado com ErrorAction Stop vira excecao: try/catch.
            $p = (Invoke-Native { & py "-$v" -c "import sys; print(sys.executable)" } -SemErros)
            if ($LASTEXITCODE -eq 0 -and $p) { return ([string]$p).Trim() }
        }
    }
    foreach ($d in @("$env:LOCALAPPDATA\Programs\Python\Python312", "$env:LOCALAPPDATA\Programs\Python\Python311",
                     "$env:ProgramFiles\Python312", "$env:ProgramFiles\Python311")) {
        $p = Join-Path $d "python.exe"
        if (Test-Path $p) { return $p }
    }
    return $null
}
if (-not $PythonExe -and -not (Find-OfficialPython)) {
    Step "Python"
    Warn "Nenhum Python 3.11/3.12 oficial encontrado neste computador (o motor precisa de um)."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        if ($SemPerguntas) { $a = $(if ($InstalarPython) { "s" } else { "n" }) }
        else { $a = Read-Host "Instalar agora o Python 3.12 oficial (python.org) para o seu usuario, pelo winget? [S/n]" }
        if ($a -notmatch '^[nN]') {
            # Na janela do instalador a pessoa já aceitou (caixa marcada); no console o winget pergunta.
            $accept = $(if ($SemPerguntas) { @("--accept-package-agreements", "--accept-source-agreements") } else { @() })
            & winget install -e --id Python.Python.3.12 --scope user --source winget @accept
            $cand = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
            if (Test-Path $cand) { $PythonExe = $cand; Ok "Python 3.12 instalado em $cand" }
            else { Warn "O winget terminou, mas o Python nao apareceu em $cand. Instale pelo python.org e rode de novo." }
        }
    } else {
        Warn "Instale o Python 3.12 pelo python.org (marque 'Add python.exe to PATH') e rode este instalador de novo."
    }
}

# 0b. Tipo de uso: decide o separador de vozes (licença). Na reinstalação, o padrão é a escolha anterior.
$prevPaid = $false
if ($cfg -and $cfg.env -and $cfg.env.SMARTCAPTION_USO_COMERCIAL) { $prevPaid = ([string]$cfg.env.SMARTCAPTION_USO_COMERCIAL) -eq "1" }
if ($UsoComercial) { $paid = $true } elseif ($UsoPessoal) { $paid = $false } else {
    Write-Host ""
    Write-Host "Como o Dynamic Captions vai ser usado neste computador?"
    Write-Host "  1 = uso pessoal, sem cobrar por isso   (separacao de vozes SUPlime: a mais precisa; licenca nao comercial)"
    Write-Host "  2 = trabalho pago / clientes           (separacao de vozes com licenca comercial; nao precisa de conta em site nenhum)"
    $default = if ($prevPaid) { "2" } else { "1" }
    $u = $(if ($SemPerguntas) { $default } else { Read-Host "Escolha 1 ou 2 [$default]" })
    if (-not $u) { $u = $default }
    $paid = ($u.Trim() -eq "2")
}
Ok ("uso: " + $(if ($paid) { "trabalho pago" } else { "pessoal" }))

# 1. Se o motor atual já roda no runtime de produção, ele segura os arquivos: encerra antes de atualizar.
$Runtime = Join-Path $Base "runtime"
$engineStopped = $false
if ($cfg -and $cfg.python -and ([string]$cfg.python).StartsWith($Runtime, [System.StringComparison]::OrdinalIgnoreCase)) {
    Stop-Engine; $engineStopped = $true
}

# 2. Runtime de produção, validado com o motor DESTE instalador antes de trocar qualquer coisa:
#    Python oficial assinado, bibliotecas, assinaturas, import real, FFmpeg configurado e eventos
#    CodeIntegrity (relatório em runtime-relatorio.json).
$ffmpegEnv = ""
if ($cfg -and $cfg.env -and $cfg.env.SMARTCAPTION_FFMPEG) { $ffmpegEnv = [string]$cfg.env.SMARTCAPTION_FFMPEG }
try {
    $rt = & (Join-Path $Here "runtime-producao.ps1") -Motor (Join-Path $Here "motor") -Base $Base -PythonExe $PythonExe `
            -CertThumbprint $CertThumbprint -TimestampUrl $TimestampUrl -FFmpeg $ffmpegEnv -SemInstalar:$SemDependencias -ComoEtapa
    $rt = @($rt)[-1]
} catch {
    Write-Host ""; Write-Host "Nada foi trocado: $_" -ForegroundColor Red
    Write-Host "Relatorio: $(Join-Path $Base 'runtime-relatorio.json')"
    Exit-With 1
}
if ($rt.status -ne "ok" -and -not $Forcar) {
    Write-Host ""
    Write-Host "Nada foi trocado (motor, launch.json e painel continuam como estavam)." -ForegroundColor Red
    Write-Host "  $($rt.bloqueio)" -ForegroundColor Red
    Write-Host "  Relatorio: $(Join-Path $Base 'runtime-relatorio.json')"
    if ($rt.status -like "bloqueado*") { Exit-With 2 } else { Exit-With 1 }
}
if ($rt.status -ne "ok") { Warn "-Forcar: instalando mesmo com o runtime em '$($rt.status)'" }
$py = [string]$rt.python
$pyCli = $py -replace "pythonw\.exe$", "python.exe"

# 3. Agora sim: encerra o motor antigo, guarda a versão atual e troca.
if (-not $engineStopped) { Stop-Engine }
Step "Guardando a versão atual em $Backup"
New-Item -ItemType Directory -Force -Path $Backup | Out-Null
if (Test-Path $Ext)    { Copy-Item $Ext    (Join-Path $Backup "painel") -Recurse -Force; Ok "painel" }
if (Test-Path $Motor)  { Copy-Item $Motor  (Join-Path $Backup "motor")  -Recurse -Force; Ok "motor" }
if (Test-Path $Launch) { Copy-Item $Launch (Join-Path $Backup "launch.json") -Force;     Ok "launch.json" }

Step "Instalando o motor em $Motor"
if (Test-Path $Motor) { Remove-Item $Motor -Recurse -Force }
Copy-Item (Join-Path $Here "motor") $Motor -Recurse -Force
Ok "motor $Version copiado"

# 6. launch.json: mantem as variaveis existentes (modelos, cache, FFmpeg) e aponta para o motor novo.
Step "Atualizando $Launch"
$envMap = [ordered]@{}
if ($cfg -and $cfg.env) { $cfg.env.PSObject.Properties | ForEach-Object { $envMap[$_.Name] = [string]$_.Value } }
$envMap["PYTHONPATH"] = $Motor
if (-not $envMap.Contains("SMARTCAPTION_HOME")) { $envMap["SMARTCAPTION_HOME"] = $Base }
$envMap["SMARTCAPTION_USO_COMERCIAL"] = $(if ($paid) { "1" } else { "0" })
$json = ([ordered]@{ python = $py; cwd = $Base; env = $envMap } | ConvertTo-Json -Depth 5)
[System.IO.File]::WriteAllText($Launch, $json, (New-Object System.Text.UTF8Encoding($false)))
Ok "PYTHONPATH -> $Motor"
if ($envMap.Contains("SMARTCAPTION_MODELS")) { Ok "modelos: $($envMap['SMARTCAPTION_MODELS'])" }

# 7. Versão do motor no runtime.
Step "Conferindo o motor"
# try/catch: no Windows PowerShell 5.1, qualquer linha em stderr com ErrorAction Stop vira excecao.
$got = (Invoke-Native { & $pyCli -c "import sys; sys.path.insert(0, r'$Motor'); import smartcaption; print(smartcaption.__version__)" }) -join " "
if ($LASTEXITCODE -eq 0 -and $got -match [regex]::Escape($Version)) { Ok "motor $Version" } else { Warn "o motor nao carregou: $got" }

# 7a. Conta da Hugging Face para o SUPlime (opcional). O token chega por variável de ambiente
#     (SC_HF_TOKEN), nunca pela linha de comando, e não é escrito no registro da instalação.
$hfTok = $env:SC_HF_TOKEN
if ($hfTok -and -not $paid) {
    Step "Conta da Hugging Face (SUPlime)"
    $hfProbe = @'
import os, sys
from huggingface_hub import hf_hub_download, login
tok = os.environ.get("SC_HF_TOKEN", "").strip()
def rede(e):  # sem internet / proxy / tempo esgotado: o token pode estar certo
    n = " ".join(x.__name__ for x in type(e).__mro__)
    return any(k in n for k in ("Connection", "Proxy", "Timeout", "Transport", "OfflineMode"))
try:
    login(token=tok, add_to_git_credential=False)
except Exception as e:
    print("token", type(e).__name__); sys.exit(5 if rede(e) else 3)
try:
    hf_hub_download("rewayai/suplime", "config.yaml", token=tok)
except Exception as e:
    print("acesso", type(e).__name__); sys.exit(5 if rede(e) else 4)
print("liberado")
'@
    $hfFile = Join-Path $Base "hf-login.py"
    Set-Content -Path $hfFile -Value $hfProbe -Encoding ASCII
    $hfOut = (Invoke-Native { & $pyCli $hfFile }) -join " "
    $hfCode = $LASTEXITCODE
    Remove-Item $hfFile -Force -ErrorAction SilentlyContinue
    Remove-Item Env:SC_HF_TOKEN -ErrorAction SilentlyContinue
    if ($hfCode -eq 0) { Ok "token aceito e SUPlime liberado" }
    elseif ($hfCode -eq 5) { Warn "Nao deu para falar com a Hugging Face agora (internet?). Rode o instalador de novo depois; ate la, o separador alternativo e usado." }
    elseif ($hfCode -eq 4) { Warn "Token aceito, mas falta aceitar os termos em huggingface.co/rewayai/suplime. Ate la, o separador alternativo e usado." }
    else { Warn "A Hugging Face recusou o token (confira se copiou inteiro). Ate rodar o instalador de novo, o separador alternativo e usado." }
}

# 7b. Modelos que este computador vai usar (antes, o primeiro uso do painel esperava o download).
Step "Modelos de transcricao e de vozes (ate ~7 GB na primeira vez, com o progresso a cada 5 s; nas proximas, segundos)"
foreach ($k in $envMap.Keys) { Set-Item -Path ("Env:" + $k) -Value $envMap[$k] }
$env:PYTHONUNBUFFERED = "1"  # cada linha de progresso aparece na hora (com a saida num pipe o Python guardaria em bloco)
Invoke-Native { & $pyCli -m smartcaption.modelos baixar tudo } | ForEach-Object { Write-Host "   $_" }
if ($LASTEXITCODE -eq 0) { Ok "modelos prontos" }
else { Warn "Parte dos modelos ficou para depois: o motor baixa quando precisar (com internet), ou rode o instalador de novo." }

# 8. Painel (extensao assinada).
Step "Instalando o painel em $Ext"
New-Item -ItemType Directory -Force -Path $ExtRoot | Out-Null
if (Test-Path $Ext) { Remove-Item $Ext -Recurse -Force }
Copy-Item (Join-Path $Here "painel") $Ext -Recurse -Force
Ok "painel $Version copiado"

# 9. Aviso sobre o PlayerDebugMode (so leitura; o instalador nunca altera o Registro).
$debug = $false
foreach ($v in @("10", "11", "12")) {
    try {
        $k = Get-ItemProperty -Path "HKCU:\Software\Adobe\CSXS.$v" -Name PlayerDebugMode -ErrorAction Stop
        if ("$($k.PlayerDebugMode)" -eq "1") { $debug = $true }
    } catch { }
}
if (-not $debug) {
    Warn "O PlayerDebugMode esta desligado. Se o Dynamic Captions nao aparecer em Janela > Extensoes,"
    Warn "rode 'Ativar painel no Premiere.cmd' (explica o que muda e pede confirmacao). Este instalador nao mexe no Registro."
}

Write-Host ""
if ($rt.status -eq "ok") {
    Write-Host "Pronto. No Premiere: Janela > Extensoes > Dynamic Captions." -ForegroundColor Green
} else {
    Write-Host "Instalado com -Forcar, mas o motor NAO vai carregar neste Windows:" -ForegroundColor Red
    Write-Host "  $($rt.bloqueio)" -ForegroundColor Red
}
Write-Host "Se precisar voltar atras, a versao anterior esta em:"
Write-Host "  $Backup"
if (-not $SemPerguntas) { Read-Host "Enter para fechar" | Out-Null }
exit 0
