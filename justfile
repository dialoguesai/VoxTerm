set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

root := justfile_directory()
venv := root + "/.venv"
python := venv + "/bin/python"
pip := venv + "/bin/pip"

default:
    @just --list

# Create .venv (Python 3.12+) and install voxterm in editable mode.
setup:
    cd "{{root}}"
    [ -d .venv ] || python3 -m venv .venv
    "{{pip}}" install -e .

# Run the transcription TUI. Bootstraps .venv on first use; press R to record.
run *ARGS:
    cd "{{root}}"
    if [ ! -x "{{python}}" ] || ! "{{python}}" -c "import tui.app" 2>/dev/null; then just setup; fi
    "{{python}}" -m tui.app {{ARGS}}

# Run unit tests (pytest).
test: setup
    cd "{{root}}"
    "{{python}}" -m pytest tests -q
