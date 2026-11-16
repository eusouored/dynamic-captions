@echo off
rem Revalida o runtime instalado (inclui o FFmpeg configurado no launch.json). Codigo: 0 ok, 2 bloqueado, 1 erro.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime-producao.ps1" -Motor "%LOCALAPPDATA%\DynamicCaptions\motor" -SemInstalar %*
set RC=%ERRORLEVEL%
echo.
echo Resultado: %RC% (0 ok, 2 bloqueado pelo Windows, 1 erro)
pause
exit /b %RC%
