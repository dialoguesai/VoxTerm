@echo off
set DIR=%~dp0
if "%1"=="--dictate" (
    shift
    goto :dictate
)
if "%1"=="-D" (
    shift
    goto :dictate
)
"%DIR%.venv\Scripts\python.exe" -m tui.app %*
goto :eof

:dictate
"%DIR%.venv\Scripts\python.exe" -m dictation %*
goto :eof
