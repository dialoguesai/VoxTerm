@echo off
set DIR=%~dp0
if "%1"=="--dictate" (
    shift
    "%DIR%.venv\Scripts\python.exe" -m dictation %*
    goto :eof
)
if "%1"=="-D" (
    shift
    "%DIR%.venv\Scripts\python.exe" -m dictation %*
    goto :eof
)
"%DIR%.venv\Scripts\python.exe" -m tui.app %*
