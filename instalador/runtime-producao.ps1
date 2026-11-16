# Runtime de producao do Dynamic Captions, compativel com o Controle Inteligente de Aplicativos
# (Smart App Control) e com politicas WDAC.
#
# O que faz:
#   1. le (so leitura) o estado do Controle Inteligente de Aplicativos;
#   2. escolhe um Python 3.11/3.12 oficial com assinatura Authenticode valida (-PythonExe, py,
#      python do PATH ou pastas padrao; o alias vazio de WindowsApps e ignorado). O ambiente de
#      teste nunca e reaproveitado: o runtime e criado em %LOCALAPPDATA%\DynamicCaptions\runtime;
#   3. instala o PyTorch (CUDA 12.8) e as bibliotecas do motor;
#   4. confere a assinatura de cada .exe/.dll/.pyd do runtime, do Python base e do FFmpeg
#      configurado (launch.json > DYNAMIC_CAPTIONS_FFMPEG, ou -FFmpeg);
#   5. com -CertThumbprint, assina o que estiver sem assinatura (certificado de producao de uma
#      autoridade publica; certificado autoassinado NAO serve para o Controle Inteligente);
#   6. testa "import torch" e as demais bibliotecas nativas num processo novo, e executa o FFmpeg;
#   7. le os eventos CodeIntegrity 3077/3089/3033/3034 do teste, distinguindo "log inacessivel"
#      de "nenhum evento";
#   8. grava %LOCALAPPDATA%\DynamicCaptions\runtime-relatorio.json.
#
# Resultado (campo status e codigo de saida quando chamado direto):
#   ok                         0  tudo carregou
#   bloqueado-sem-certificado  2  ha evidencia de bloqueio (WinError 4551 no carregador ou evento
#                                 3077/3033) e nenhum certificado de producao foi usado
#   bloqueado-com-certificado  2  ainda bloqueado mesmo depois de assinar
#   falhou                     1  falhou por outro motivo (ou sem evidencia de bloqueio)
#
# Nao altera o Registro nem configuracoes de seguranca.
#
# Uso direto: powershell -NoProfile -ExecutionPolicy Bypass -File runtime-producao.ps1 -Motor <pasta>
#             [-PythonExe C:\...\python.exe] [-SemInstalar] [-CertThumbprint X] [-FFmpeg C:\...\ffmpeg.exe]
# O instalador chama com -ComoEtapa (devolve o resultado em vez de sair com codigo).

param(
    [Parameter(Mandatory = $true)][string]$Motor,
    [string]$Base = (Join-Path $env:LOCALAPPDATA "DynamicCaptions"),
    [string]$PythonExe = "",
    [string]$CertThumbprint = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [string]$FFmpeg = "",
    [switch]$SemInstalar,
    [switch]$ComoEtapa
)

$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$Runtime = Join-Path $Base "runtime"
$Report  = Join-Path $Base "runtime-relatorio.json"
$Result  = [ordered]@{
    data = (Get-Date).ToString("s"); runtime = $Runtime; python = $null; motor = $Motor
    smart_app_control = "desconhecido"; language_mode = "$($ExecutionContext.SessionState.LanguageMode)"
    python_base = $null; python_candidatos = @()
    ffmpeg = $null; ffmpeg_assinatura = $null; ffmpeg_execucao = $null
    binarios = 0; sem_assinatura = 0; sem_assinatura_lista = @(); assinados_agora = 0
    import_ok = $false; import_resultado = $null; import_saida = ""
    eventos_status = "nao lido"; eventos = @(); bloqueados_eventos = @()
    evidencia_carregador = @()
    status = "falhou"; bloqueio = $null
}

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
function Save-Report {
    New-Item -ItemType Directory -Force -Path $Base | Out-Null
    $Result | ConvertTo-Json -Depth 6 | Set-Content -Path $Report -Encoding UTF8
    Write-Host "   relatorio: $Report"
}
function Finish {
    Save-Report
    if ($Result.status -ne "ok") { Warn $Result.bloqueio }
    if ($ComoEtapa) { return $Result }
    $code = 1
    if ($Result.status -eq "ok") { $code = 0 } elseif ($Result.status -like "bloqueado*") { $code = 2 }
    exit $code
}
function Fail($why) {
    $Result.status = "falhou"; $Result.bloqueio = $why
    Save-Report; Warn $why
    if ($ComoEtapa) { throw $why }
    exit 1
}
# Mensagem do Windows quando o Controle de Aplicativo bloqueia (portugues e ingles) e o codigo.
$BlockPattern = "4551|Controle de Aplicativo|Application Control policy"

# 1. Estado do Controle Inteligente de Aplicativos (0 desligado, 1 ligado, 2 avaliacao).
Step "Controle Inteligente de Aplicativos"
try {
    $sac = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy" -Name VerifiedAndReputablePolicyState -ErrorAction Stop).VerifiedAndReputablePolicyState
    $names = @{ 0 = "desligado"; 1 = "ligado"; 2 = "avaliacao" }
    $Result.smart_app_control = if ($names.ContainsKey([int]$sac)) { $names[[int]$sac] } else { "valor $sac" }
} catch { $Result.smart_app_control = "nao encontrado" }
Ok $Result.smart_app_control
if ($Result.language_mode -ne "FullLanguage") {
    Warn "PowerShell em modo $($Result.language_mode): a politica exige scripts assinados; este instalador tambem precisa ser assinado."
}

# 2. Python base: versao 3.11/3.12 e assinatura valida do executavel real (nao do alias).
Step "Python base"
function Test-PythonCandidate($exe, $preArgs, $label) {
    $entry = [ordered]@{ candidato = $label; ok = $false; motivo = $null; base = $null; versao = $null; assinatura = $null }
    try {
        if ($exe -like "*\WindowsApps\*" -and (Test-Path $exe) -and (Get-Item $exe).Length -eq 0) {
            $entry.motivo = "alias de WindowsApps (sem Python instalado de verdade)"; return $entry
        }
        $info = (Invoke-Native { & $exe @preArgs -c "import sys, json; print(json.dumps({'v': list(sys.version_info[:3]), 'base': getattr(sys, '_base_executable', '') or sys.executable}))" } -SemErros) -join ""
        if ($LASTEXITCODE -ne 0 -or -not $info) { $entry.motivo = "nao executou (codigo $LASTEXITCODE)"; return $entry }
        $j = $info | ConvertFrom-Json
        $entry.versao = ($j.v -join ".")
        $entry.base = [string]$j.base
        if (-not ($j.v[0] -eq 3 -and ($j.v[1] -eq 11 -or $j.v[1] -eq 12))) { $entry.motivo = "versao $($entry.versao) (precisa 3.11 ou 3.12)"; return $entry }
        if (-not (Test-Path $entry.base) -or (Get-Item $entry.base).Length -eq 0) { $entry.motivo = "executavel base nao encontrado: $($entry.base)"; return $entry }
        $sig = Get-AuthenticodeSignature -FilePath $entry.base
        $entry.assinatura = "$($sig.Status) $($sig.SignerCertificate.Subject)"
        if ($sig.Status -ne "Valid") { $entry.motivo = "assinatura $($sig.Status)"; return $entry }
        $entry.ok = $true
    } catch { $entry.motivo = "$_" }
    return $entry
}
$candidates = @()
if ($PythonExe) {
    $candidates += , @($PythonExe, @(), "-PythonExe $PythonExe")
} else {
    foreach ($v in @("3.12", "3.11")) { $candidates += , @("py", @("-$v"), "py -$v") }
    foreach ($c in @(Get-Command python.exe -All -ErrorAction SilentlyContinue)) { $candidates += , @($c.Source, @(), $c.Source) }
    foreach ($d in @("$env:LOCALAPPDATA\Programs\Python\Python312", "$env:LOCALAPPDATA\Programs\Python\Python311",
                     "$env:ProgramFiles\Python312", "$env:ProgramFiles\Python311")) {
        $p = Join-Path $d "python.exe"
        if (Test-Path $p) { $candidates += , @($p, @(), $p) }
    }
}
$basePy = $null
foreach ($c in $candidates) {
    if ($c[0] -eq "py" -and -not (Get-Command py -ErrorAction SilentlyContinue)) { continue }
    $r = Test-PythonCandidate $c[0] $c[1] $c[2]
    $Result.python_candidatos += [pscustomobject]$r
    if ($r.ok) { $basePy = $r; break } else { Warn "$($c[2]): $($r.motivo)" }
}
if (-not $basePy) {
    if ($PythonExe) { Fail "o Python indicado em -PythonExe nao serve: $($Result.python_candidatos[-1].motivo)" }
    Fail "nenhum Python 3.11/3.12 oficial com assinatura valida encontrado. Instale pelo python.org ou indique -PythonExe <caminho do python.exe>."
}
$baseExe = $basePy.base
$Result.python_base = "$baseExe ($($basePy.versao); $($basePy.assinatura))"
Ok $Result.python_base

# 3. Runtime proprio (nunca o ambiente de teste).
Step "Runtime em $Runtime"
$py  = Join-Path $Runtime "Scripts\python.exe"
$pyw = Join-Path $Runtime "Scripts\pythonw.exe"
# Um runtime de uma instalação anterior só serve se ainda abrir: se o Python base foi desinstalado
# ou trocado de versão, o venv fica quebrado e o pip falharia. Aí ele é apagado e recriado (as
# bibliotecas voltam do cache do pip, sem baixar de novo).
if (Test-Path $py) {
    $rtInfo = (Invoke-Native { & $py -c "import sys; print('%d.%d' % sys.version_info[:2])" } -SemErros) -join ""
    $baseVer = ($basePy.versao -split "\.")[0..1] -join "."
    if ($LASTEXITCODE -ne 0 -or $rtInfo.Trim() -ne $baseVer) {
        Warn "o runtime existente nao abre com o Python $baseVer (resposta: '$rtInfo'); recriando"
        Remove-Item $Runtime -Recurse -Force
    }
}
if (-not (Test-Path $py)) {
    & $baseExe -m venv $Runtime | Out-Host
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $py)) { Fail "nao foi possivel criar o runtime em $Runtime" }
    Ok "criado"
} else { Ok "ja existe" }
$Result.python = $pyw

if (-not $SemInstalar) {
    Step "Bibliotecas (PyTorch com CUDA: alguns GB na primeira vez)"
    & $py -m pip install --upgrade pip | Out-Host
    Write-Host "   PyTorch com CUDA: ~2,8 GB na primeira vez (o pip so escreve quando termina cada arquivo; nas proximas vem do cache)"
    & $py -m pip install "torch>=2.8,<2.12" "torchaudio>=2.8,<2.12" --index-url https://download.pytorch.org/whl/cu128 | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "falha ao instalar o PyTorch" }
    & $py -m pip install ("{0}[preciso,ffmpeg]" -f $Motor) | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "falha ao instalar as bibliotecas do motor" }
    Ok "bibliotecas prontas"
}

# FFmpeg configurado (o mesmo que o motor vai usar).
if (-not $FFmpeg) {
    $launch = Join-Path $Base "launch.json"
    if (Test-Path $launch) {
        try { $cfg = Get-Content $launch -Raw | ConvertFrom-Json; if ($cfg.env.DYNAMIC_CAPTIONS_FFMPEG) { $FFmpeg = [string]$cfg.env.DYNAMIC_CAPTIONS_FFMPEG } } catch { }
    }
}
$Result.ffmpeg = if ($FFmpeg) { $FFmpeg } else { "(nao configurado: o motor usa o do imageio-ffmpeg dentro do runtime)" }

# 4. Assinaturas de todos os binarios que o motor carrega.
function Get-Binaries {
    $list = @(Get-ChildItem -Path $Runtime -Recurse -File -Include *.exe, *.dll, *.pyd -ErrorAction SilentlyContinue)
    $baseDir = Split-Path -Parent $baseExe
    foreach ($d in @($baseDir, (Join-Path $baseDir "DLLs"))) {
        if (Test-Path $d) { $list += @(Get-ChildItem -Path (Join-Path $d "*") -File -Include *.dll, *.pyd -ErrorAction SilentlyContinue) }
    }
    if ($FFmpeg -and (Test-Path $FFmpeg)) { $list += Get-Item $FFmpeg }
    return $list
}
function Test-Signatures {
    $bins = Get-Binaries
    $bad = @()
    foreach ($f in $bins) {
        $s = Get-AuthenticodeSignature -FilePath $f.FullName
        if ($s.Status -ne "Valid") { $bad += [pscustomobject]@{ arquivo = $f.FullName; status = "$($s.Status)" } }
    }
    return @{ total = $bins.Count; bad = $bad }
}
Step "Conferindo assinaturas"
$chk = Test-Signatures
$Result.binarios = $chk.total; $Result.sem_assinatura = $chk.bad.Count
$Result.sem_assinatura_lista = @($chk.bad | Select-Object -First 400)
if ($FFmpeg -and (Test-Path $FFmpeg)) { $Result.ffmpeg_assinatura = "$((Get-AuthenticodeSignature -FilePath $FFmpeg).Status)" }
Ok "$($chk.total) binarios; $($chk.bad.Count) sem assinatura valida"
if ($Result.ffmpeg_assinatura) { Ok "FFmpeg configurado: $($Result.ffmpeg_assinatura)" }

# 5. Assinatura com o certificado de producao (opcional).
if ($CertThumbprint -and $chk.bad.Count -gt 0) {
    Step "Assinando com o certificado $CertThumbprint"
    $tp = $CertThumbprint.Replace(" ", "").ToUpper()
    $cert = @(Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -CodeSigningCert -ErrorAction SilentlyContinue |
              Where-Object { $_.Thumbprint -eq $tp }) | Select-Object -First 1
    if (-not $cert) { Fail "certificado $CertThumbprint nao encontrado (precisa da chave privada e do uso 'assinatura de codigo')" }
    if ($cert.Subject -eq $cert.Issuer -or -not $cert.Verify()) {
        Fail "o certificado e autoassinado ou nao tem cadeia confiavel: o Controle Inteligente de Aplicativos nao o aceita. Use um certificado de assinatura de codigo de uma autoridade publica (OV/EV ou Azure Trusted Signing)."
    }
    foreach ($b in $chk.bad) {
        $r = Set-AuthenticodeSignature -FilePath $b.arquivo -Certificate $cert -HashAlgorithm SHA256 -TimestampServer $TimestampUrl
        if ($r.Status -eq "Valid") { $Result.assinados_agora++ } else { Warn "nao assinou $($b.arquivo): $($r.StatusMessage)" }
    }
    $chk = Test-Signatures
    $Result.sem_assinatura = $chk.bad.Count; $Result.sem_assinatura_lista = @($chk.bad | Select-Object -First 400)
    Ok "$($Result.assinados_agora) assinados; restam $($chk.bad.Count) sem assinatura"
}

# 6. Teste real num processo novo: bibliotecas nativas do motor + FFmpeg.
Step "Testando o carregamento no runtime"
$t0 = Get-Date
$probe = @"
import json, sys
sys.path.insert(0, r'$Motor')
out = {}
for name in ['torch', 'torchaudio', 'numpy', 'soundfile', 'silero_vad', 'sherpa_onnx', 'PIL.Image', 'fontTools.ttLib', 'pydantic_core', 'tokenizers', 'safetensors', 'pyannote.audio', 'qwen_asr', 'dynamic_captions.api.app']:
    try:
        m = __import__(name, fromlist=['x'])
        out[name] = getattr(m, '__version__', 'ok')
    except BaseException as e:
        out[name] = 'ERRO ' + type(e).__name__ + ': ' + str(e)[:400]
try:
    import torch
    out['cuda'] = bool(torch.cuda.is_available())
    from silero_vad import load_silero_vad
    load_silero_vad(); out['silero_vad_modelo'] = 'ok'
except BaseException as e:
    out['cuda_e_vad'] = 'ERRO ' + type(e).__name__ + ': ' + str(e)[:400]
print(json.dumps(out, ensure_ascii=True))
sys.exit(1 if any(str(v).startswith('ERRO') for v in out.values()) else 0)
"@
$probeFile = Join-Path $Base "runtime-teste.py"
Set-Content -Path $probeFile -Value $probe -Encoding ASCII
$out = (Invoke-Native { & $py $probeFile }) -join "`n"
$Result.import_ok = ($LASTEXITCODE -eq 0)
$Result.import_saida = $out
$line = ($out -split "`n" | Where-Object { $_.Trim().StartsWith("{") } | Select-Object -Last 1)
if ($line) {
    try {
        $Result.import_resultado = $line | ConvertFrom-Json
        foreach ($p in $Result.import_resultado.PSObject.Properties) {
            if ("$($p.Value)" -match $BlockPattern) { $Result.evidencia_carregador += "$($p.Name): $($p.Value)" }
        }
    } catch { }
} elseif ($out -match $BlockPattern) {
    $Result.evidencia_carregador += "python: $out"  # o proprio python.exe do runtime foi bloqueado
}
if ($Result.import_ok) { Ok "todas as bibliotecas carregaram" } else { Warn "falhou:"; Warn $out }

if ($FFmpeg -and (Test-Path $FFmpeg)) {
    try {
        $fv = (Invoke-Native { & $FFmpeg -hide_banner -version } | Select-Object -First 1) -join ""
        $Result.ffmpeg_execucao = if ($LASTEXITCODE -eq 0) { "ok: $fv" } else { "codigo $LASTEXITCODE`: $fv" }
    } catch { $Result.ffmpeg_execucao = "ERRO: $_" }
    if ("$($Result.ffmpeg_execucao)" -match $BlockPattern) { $Result.evidencia_carregador += "ffmpeg: $($Result.ffmpeg_execucao)" }
    Ok "FFmpeg: $($Result.ffmpeg_execucao)"
}

# 7. Eventos CodeIntegrity do teste (bloqueio 3077, assinatura 3089, auditoria 3033/3034).
Step "Eventos CodeIntegrity desde $($t0.ToString('HH:mm:ss'))"
$ev = @()
try {
    $ev = @(Get-WinEvent -FilterHashtable @{ LogName = "Microsoft-Windows-CodeIntegrity/Operational"; Id = 3077, 3089, 3033, 3034; StartTime = $t0.AddSeconds(-2) } -ErrorAction Stop)
    $Result.eventos_status = "lidos: $($ev.Count)"
} catch {
    if ($_.FullyQualifiedErrorId -like "NoMatchingEventsFound*") { $Result.eventos_status = "nenhum evento no periodo" }
    else { $Result.eventos_status = "inacessivel: $($_.Exception.Message)" }
}
foreach ($e in $ev) {
    $Result.eventos += [pscustomobject]@{ id = $e.Id; hora = $e.TimeCreated.ToString("s"); mensagem = (($e.Message -split "`n")[0]).Trim() }
    if ($e.Id -eq 3077 -or $e.Id -eq 3033) {
        $m = [regex]::Match($e.Message, "(?i)[a-z]:\\[^\r\n""]+?\.(dll|pyd|exe)")
        if ($m.Success -and $Result.bloqueados_eventos -notcontains $m.Value) { $Result.bloqueados_eventos += $m.Value }
    }
}
if ($Result.eventos_status -like "inacessivel*") { Warn "log CodeIntegrity $($Result.eventos_status) (rode como administrador para ler os eventos)" }
else { Ok "$($Result.eventos_status); $($Result.bloqueados_eventos.Count) arquivos bloqueados" }
foreach ($b in ($Result.bloqueados_eventos | Select-Object -First 15)) { Warn "bloqueado: $b" }

# 8. Veredito: bloqueio so com evidencia (carregador ou eventos), nao por suposicao.
$evidence = ($Result.evidencia_carregador.Count -gt 0) -or ($Result.bloqueados_eventos.Count -gt 0)
if ($Result.import_ok -and -not ($Result.ffmpeg_execucao -and "$($Result.ffmpeg_execucao)" -notlike "ok:*")) {
    $Result.status = "ok"
} elseif ($evidence -and $CertThumbprint) {
    $Result.status = "bloqueado-com-certificado"
    $Result.bloqueio = "BLOQUEIO: mesmo depois de assinar, o Windows ainda bloqueia arquivos do runtime. Confira se o certificado e de uma autoridade publica e se a assinatura tem carimbo de tempo."
} elseif ($evidence) {
    $Result.status = "bloqueado-sem-certificado"
    $Result.bloqueio = "BLOQUEIO: o Windows bloqueou arquivos do runtime ($($Result.evidencia_carregador.Count) evidencias no carregador, $($Result.bloqueados_eventos.Count) nos eventos; $($Result.sem_assinatura) binarios sem assinatura; Controle Inteligente: $($Result.smart_app_control)). Sem um certificado de assinatura de codigo de producao, de uma autoridade publica, o runtime nao carrega com a protecao ativa."
} else {
    $Result.status = "falhou"
    $Result.bloqueio = "o teste de carregamento falhou sem evidencia de bloqueio do Windows; veja import_saida no relatorio"
}
Finish
