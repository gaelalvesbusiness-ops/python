@echo off
rem HonestPool - salva a chave 2Captcha cifrada em config\2captcha.cfg (Windows DPAPI).
rem A chave e digitada sem aparecer na tela, dentro do Python; este arquivo nunca a recebe.
setlocal EnableExtensions
pushd "%~dp0"
title HonestPool - Configurar 2Captcha
set "PYTHONUTF8=1"
set "VENV_PY=.venv\Scripts\python.exe"
set "SETUP_MARKER=.venv\honestpool_setup_ok.txt"

if not exist "windows_launcher.py" goto :missing_files
if not exist "%VENV_PY%" goto :not_installed
if not exist "%SETUP_MARKER%" goto :setup_incomplete

"%VENV_PY%" windows_launcher.py configurar
if errorlevel 1 goto :failed
echo.
echo Pronto. Agora clique duas vezes em iniciar_honestpool.bat para abrir o menu.
goto :end_ok

:missing_files
echo ERRO: windows_launcher.py nao foi encontrado nesta pasta. Extraia o ZIP completo novamente.
goto :end_fail

:not_installed
echo ERRO: o programa ainda nao foi instalado neste computador.
echo Clique duas vezes em setup_windows.bat primeiro e depois execute este arquivo de novo.
goto :end_fail

:setup_incomplete
echo ERRO: a instalacao nao foi concluida.
echo Clique duas vezes em setup_windows.bat e espere a mensagem INSTALACAO CONCLUIDA COM SUCESSO.
goto :end_fail

:failed
echo.
echo A configuracao NAO foi concluida. Leia a mensagem acima e execute este arquivo de novo.
goto :end_fail

:end_fail
echo.
pause
exit /b 1

:end_ok
echo.
pause
exit /b 0
