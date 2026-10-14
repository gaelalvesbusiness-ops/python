@echo off
rem HonestPool - instalacao para Windows 11.
rem Cria .venv, instala requirements.txt e o Chromium do Playwright e cria as pastas locais.
rem Nao inicia nenhuma extracao e nao pede nem le a chave 2Captcha.
setlocal EnableExtensions
pushd "%~dp0"
title HonestPool - Instalacao
set "PYTHONUTF8=1"
set "VENV_PY=.venv\Scripts\python.exe"
set "SETUP_MARKER=.venv\honestpool_setup_ok.txt"
set "PY_VERSION_CHECK=import sys; sys.exit(0 if sys.version_info[:2] in ((3, 11), (3, 12)) else 1)"
set "PATH_LENGTH_CHECK=import os, sys; sys.exit(1 if len(os.getcwd()) > 100 else 0)"

echo ============================================================
echo  HonestPool - instalacao para Windows 11
echo ============================================================
echo.
echo Pasta do programa: "%CD%"
echo Nenhuma extracao sera iniciada por esta instalacao.
echo.

echo "%CD%" | find /I "\AppData\Local\Temp" >nul
if not errorlevel 1 goto :zip_not_extracted
if not exist "main.py" goto :missing_files
if not exist "requirements.txt" goto :missing_files
if not exist "windows_launcher.py" goto :missing_files
echo "%CD%" | find /I "OneDrive" >nul
if not errorlevel 1 call :onedrive_warning

echo [1/6] Procurando Python 3.12 ou 3.11...
set "PYTHON_CMD="
call :find_python
if not defined PYTHON_CMD goto :no_python
echo       Encontrado: %PYTHON_CMD%
%PYTHON_CMD% --version
%PYTHON_CMD% -c "%PATH_LENGTH_CHECK%"
if errorlevel 1 call :long_path_warning
echo.

echo [2/6] Preparando o ambiente virtual .venv...
if exist "%SETUP_MARKER%" del "%SETUP_MARKER%"
if exist "%VENV_PY%" goto :venv_exists
%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto :venv_failed
goto :venv_check
:venv_exists
echo       O ambiente virtual ja existe e sera reutilizado.
:venv_check
"%VENV_PY%" -c "%PY_VERSION_CHECK%"
if errorlevel 1 goto :venv_bad
echo       Ambiente virtual pronto.
echo.

echo [3/6] Instalando as dependencias - pode levar alguns minutos...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 echo       Aviso: nao foi possivel atualizar o pip; a instalacao continua com a versao atual.
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :pip_failed
echo.

echo [4/6] Instalando o navegador Chromium do Playwright - pode levar alguns minutos...
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 goto :chromium_failed
echo.

echo [5/6] Criando as pastas data, logs e config...
if not exist "data" mkdir "data"
if not exist "logs" mkdir "logs"
if not exist "config" mkdir "config"
if not exist "data" goto :folders_failed
if not exist "logs" goto :folders_failed
if not exist "config" goto :folders_failed
echo       Pastas prontas.
echo.

echo [6/6] Verificando a instalacao...
"%VENV_PY%" -c "import playwright, bs4, httpx, pandas, openpyxl"
if errorlevel 1 goto :check_failed
"%VENV_PY%" main.py
if errorlevel 1 goto :check_failed
"%VENV_PY%" windows_launcher.py --help >nul
if errorlevel 1 goto :check_failed
> "%SETUP_MARKER%" echo Instalacao do HonestPool concluida.
if not exist "%SETUP_MARKER%" goto :check_failed

echo.
echo ============================================================
echo  INSTALACAO CONCLUIDA COM SUCESSO
echo ============================================================
echo Proximos passos:
echo   1. Clique duas vezes em configurar_2captcha.bat para salvar a sua chave 2Captcha.
echo   2. Clique duas vezes em iniciar_honestpool.bat para abrir o menu.
echo Nenhuma extracao foi iniciada.
goto :end_ok

:zip_not_extracted
echo ERRO: este arquivo foi aberto de dentro do ZIP, sem extrair.
echo Como resolver:
echo   1. Feche esta janela.
echo   2. Clique com o botao direito no arquivo ZIP e escolha "Extrair tudo...".
echo   3. Extraia para uma pasta simples, por exemplo C:\HonestPool-v3
echo   4. Abra a pasta extraida e clique duas vezes em setup_windows.bat.
goto :end_fail

:missing_files
echo ERRO: arquivos do programa nao foram encontrados nesta pasta.
echo setup_windows.bat precisa ficar na mesma pasta que main.py, requirements.txt e
echo windows_launcher.py. Extraia o ZIP completo novamente e tente de novo.
goto :end_fail

:no_python
echo ERRO: o Python 3.12 ou 3.11 nao foi encontrado neste computador.
echo Versao do Python encontrada no PATH, se houver:
python --version 2>nul
echo.
echo Como resolver:
echo   1. Abra https://www.python.org/downloads/windows/
echo   2. Baixe o "Windows installer (64-bit)" da versao 3.12 mais recente.
echo   3. Na primeira tela do instalador, marque "Add python.exe to PATH"
echo      e clique em "Install Now".
echo   4. Quando terminar, feche esta janela e clique duas vezes em setup_windows.bat de novo.
echo Versoes 3.13 ou mais novas, ou 3.10 ou mais antigas, nao sao suportadas por esta instalacao.
goto :end_fail

:venv_failed
echo ERRO: nao foi possivel criar o ambiente virtual .venv.
echo Verifique se ha espaco livre em disco e se a pasta nao e somente leitura.
echo Se o problema continuar, extraia o programa em C:\HonestPool-v3 e tente de novo.
goto :end_fail

:venv_bad
echo ERRO: a pasta .venv existente nao usa Python 3.11 ou 3.12, ou esta danificada.
echo Como resolver: apague a pasta .venv dentro da pasta do programa
echo e clique duas vezes em setup_windows.bat de novo.
goto :end_fail

:pip_failed
echo ERRO: a instalacao das dependencias falhou.
echo Causas comuns: sem internet, antivirus bloqueando, proxy da empresa ou caminho de pasta longo demais.
echo Verifique a conexao com a internet e clique duas vezes em setup_windows.bat de novo.
echo Se o erro continuar, mova a pasta do programa para C:\HonestPool-v3 e tente de novo.
goto :end_fail

:chromium_failed
echo ERRO: a instalacao do navegador Chromium do Playwright falhou.
echo Verifique a conexao com a internet e o espaco em disco, cerca de 500 MB,
echo e clique duas vezes em setup_windows.bat de novo.
goto :end_fail

:folders_failed
echo ERRO: nao foi possivel criar as pastas data, logs ou config.
echo Verifique se a pasta do programa nao e somente leitura.
goto :end_fail

:check_failed
echo ERRO: a verificacao final da instalacao falhou; veja a mensagem acima.
echo Clique duas vezes em setup_windows.bat de novo. Se o erro continuar, apague a pasta .venv
echo e execute setup_windows.bat mais uma vez.
goto :end_fail

:find_python
for %%V in (3.12 3.11) do call :try_py_launcher %%V
if defined PYTHON_CMD goto :eof
python -c "%PY_VERSION_CHECK%" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"
goto :eof

:try_py_launcher
if defined PYTHON_CMD goto :eof
py -%1 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -%1"
goto :eof

:onedrive_warning
echo AVISO: a pasta do programa esta dentro do OneDrive.
echo A sincronizacao do OneDrive pode bloquear o banco de dados durante a extracao.
echo Recomendado: feche esta janela, mova a pasta para C:\HonestPool-v3 e execute de novo.
echo Para continuar mesmo assim, pressione uma tecla.
pause >nul
echo.
goto :eof

:long_path_warning
echo AVISO: o caminho desta pasta tem mais de 100 caracteres.
echo O Windows pode recusar alguns arquivos do Chromium e da instalacao em caminhos longos.
echo Recomendado: feche esta janela, mova a pasta para C:\HonestPool-v3 e execute de novo.
echo Para continuar mesmo assim, pressione uma tecla.
pause >nul
goto :eof

:end_fail
echo.
echo ============================================================
echo  A INSTALACAO NAO FOI CONCLUIDA
echo ============================================================
echo Leia a mensagem de erro acima. Se precisar de ajuda, envie uma foto
echo ou captura desta janela. Ela nao contem a sua chave 2Captcha.
echo.
pause
exit /b 1

:end_ok
echo.
pause
exit /b 0
