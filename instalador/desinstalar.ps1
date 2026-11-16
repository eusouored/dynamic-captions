# Remove o painel e o motor do Dynamic Captions. Projetos, legendas aplicadas e renders ficam
# em %LOCALAPPDATA%\DynamicCaptions (apague a pasta à mão se quiser tudo fora).
param([switch]$SemPerguntas)  # -SemPerguntas: chamado pelo desinstalador do Windows (Configurações > Aplicativos)
$ErrorActionPreference = "Stop"
$Home_ = Join-Path $env:LOCALAPPDATA "DynamicCaptions"
$Ext = Join-Path $env:APPDATA "Adobe\CEP\extensions\com.dynamic.captions"
Write-Host "Isto remove o painel e o motor do Dynamic Captions. Seus projetos e arquivos de legenda ficam."
if (-not $SemPerguntas) { Read-Host "Pressione Enter para remover ou feche esta janela para cancelar" | Out-Null }
$EngineJson = Join-Path $Home_ "engine.json"
if (Test-Path $EngineJson) {
    try {
        $info = Get-Content $EngineJson -Raw | ConvertFrom-Json
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($info.port)/v1/engine/shutdown" -Headers @{ "X-SC-Token" = $info.token } -TimeoutSec 5 | Out-Null
        Start-Sleep -Seconds 2
    } catch {}
}
if (Test-Path $Ext) { Remove-Item $Ext -Recurse -Force; Write-Host "painel removido" }
$Motor = Join-Path $Home_ "motor"
if (Test-Path $Motor) { Remove-Item $Motor -Recurse -Force; Write-Host "motor removido" }
# Atalhos do Menu Iniciar e a entrada em Configurações > Aplicativos (criados pelo instalador padrão).
$Atalhos = Join-Path ([Environment]::GetFolderPath("Programs")) "Dynamic Captions"
if (Test-Path $Atalhos) { Remove-Item $Atalhos -Recurse -Force; Write-Host "atalhos removidos" }
Remove-Item "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\DynamicCaptions" -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Pronto. O launch.json e os projetos continuam em $Home_."
if (-not $SemPerguntas) { Read-Host "Enter para fechar" | Out-Null }
