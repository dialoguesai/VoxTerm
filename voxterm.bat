@echo off
REM VOXTERM launcher — uses the bundled venv automatically (parallel of `voxterm` bash script)
setlocal
set DIR=%~dp0
set VENV_PY=%DIR%.venv\Scripts\python.exe
if not exist "%VENV_PY%" (
    echo VoxTerm venv missing at %VENV_PY%
    echo Run install.ps1 first, or create a venv and pip install -r requirements.txt
    exit /b 1
)
REM cd into the install dir so relative imports of `tui.app` work, mirroring
REM the bash launcher's `cd "$(dirname "$0")"` behavior.
pushd "%DIR%"
set PYTHONWARNINGS=ignore::UserWarning
"%VENV_PY%" -m tui.app %*
set RC=%ERRORLEVEL%
popd
exit /b %RC%
