@echo off
rem Liga (ou desliga) o PlayerDebugMode do Adobe CEP para o SEU usuario (HKCU), versoes 9 a 12.
rem E a chave que faz o Premiere aceitar extensoes assinadas com certificado proprio, como o Dynamic Captions.
rem Nao precisa de administrador e nao mexe em mais nada do Windows. So rode se o painel nao aparecer.
setlocal
echo.
echo  Dynamic Captions - ativar o painel no Premiere
echo  ==========================================
echo.
echo  O Dynamic Captions e assinado com um certificado proprio (nao comprado de uma autoridade).
echo  Se ele nao aparece em Janela ^> Extensoes, o Premiere precisa do "PlayerDebugMode" ligado.
echo.
echo  O que muda: um valor no Registro do SEU usuario, para as extensoes do Adobe (CEP 9 a 12):
echo    HKEY_CURRENT_USER\Software\Adobe\CSXS.N\PlayerDebugMode = 1
echo  Efeito: o Premiere passa a carregar extensoes que nao vieram da loja da Adobe.
echo  Instale extensoes so de quem voce confia. Para desfazer, rode este arquivo e escolha D.
echo.
choice /C ADS /N /M "  [A] ativar   [D] desfazer   [S] sair sem mudar nada: "
if errorlevel 3 goto :fim
if errorlevel 2 goto :desfazer
for %%V in (9 10 11 12) do reg add "HKCU\Software\Adobe\CSXS.%%V" /v PlayerDebugMode /t REG_SZ /d 1 /f >nul
echo.
echo  Pronto. Feche e abra o Premiere: Janela ^> Extensoes ^> Dynamic Captions.
goto :fim
:desfazer
for %%V in (9 10 11 12) do reg delete "HKCU\Software\Adobe\CSXS.%%V" /v PlayerDebugMode /f >nul 2>&1
echo.
echo  Desfeito. Feche e abra o Premiere.
:fim
echo.
pause
endlocal
