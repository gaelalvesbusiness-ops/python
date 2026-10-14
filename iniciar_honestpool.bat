@echo off
rem HonestPool - menu principal: extracao supervisionada, retomar, exportar CSV/Excel e progresso.
rem Nenhuma extracao comeca sem que a pessoa escolha a opcao e confirme com S.
setlocal EnableExtensions
pushd "%~dp0"
title HonestPool - Menu
set "PYTHONUTF8=1"
set "VENV_PY=.venv\Scripts\python.exe"
set "SETUP_MARKER=.venv\honestpool_setup_ok.txt"

if not exist "windows_launcher.py" goto :missing_files
if not exist "%VENV_PY%" goto :not_installed
if not exist "%SETUP_MARKER%" goto :setup_incomplete

"%VENV_PY%" windows_launcher.py menu
if errorlevel 1 goto :failed
goto :end_ok

:missing_files
echo ERRO: windows_launcher.py nao foi encontrado nesta pasta. Extraia o ZIP completo novamente.
goto :end_fail

:not_installed
echo ERRO: o programa ainda nao foi instalado neste computador.
echo Clique duas vezes em setup_windows.bat primeiro.
goto :end_fail

:setup_incomplete
echo ERRO: a instalacao nao foi concluida.
echo Clique duas vezes em setup_windows.bat e espere a mensagem INSTALACAO CONCLUIDA COM SUCESSO.
goto :end_fail

:failed
echo.
echo O menu foi encerrado por causa de um erro. Leia a mensagem acima.
goto :end_fail

:end_fail
echo.
pause
exit /b 1

:end_ok
echo.
echo Menu encerrado.
pause
exit /b 0
