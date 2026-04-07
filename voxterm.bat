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
set PYTHONWARNINGS=ignore::UserWarning
"%VENV_PY%" -m tui.app %*
exit /b %ERRORLEVEL%
