# VoxTerm Windows installer
#
# Install:    iwr -useb https://raw.githubusercontent.com/dmarzzz/VoxTerm/main/install.ps1 | iex
# Specific:   $args = @('--version','v0.1.0'); iwr -useb .../install.ps1 | iex
# Uninstall:  $args = @('--uninstall'); iwr -useb .../install.ps1 | iex

[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$Uninstall,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$Repo       = "dmarzzz/VoxTerm"
$RepoUrl    = "https://github.com/$Repo"

# Resolve %LOCALAPPDATA% with fallback for environments where the env var
# is missing or empty (some service accounts / managed Windows installs).
$LocalAppData = $env:LOCALAPPDATA
if (-not $LocalAppData) {
    $LocalAppData = Join-Path $env:USERPROFILE "AppData\Local"
}

$InstallDir = Join-Path $LocalAppData "voxterm"
$BinDir     = Join-Path $InstallDir "bin"
$VenvDir    = Join-Path $InstallDir ".venv"
$VenvPy     = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"
$VersionFile = Join-Path $InstallDir ".installed-version"
$LauncherBat = Join-Path $BinDir "voxterm.bat"

function Info($msg)  { Write-Host "[*] $msg" -ForegroundColor Cyan }
function OK($msg)    { Write-Host "[ok] $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Fail($msg)  { Write-Host "[x] $msg" -ForegroundColor Red; exit 1 }

if ($Help) {
    Write-Host @"
VoxTerm Windows installer

Usage: powershell -ExecutionPolicy Bypass -File install.ps1 [OPTIONS]

Options:
  -Version VERSION   Install a specific release tag (e.g. v0.1.0)
  -Uninstall         Remove VoxTerm completely (keeps voice profile data)
  -Help              Show this help
"@
    exit 0
}

# ── Uninstall ─────────────────────────────────────────────────
if ($Uninstall) {
    Write-Host ""
    Write-Host "Uninstalling VoxTerm..." -ForegroundColor White
    $removed = $false
    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
        OK "removed $InstallDir"
        $removed = $true
    } else {
        Warn "no install directory at $InstallDir"
    }
    Write-Host ""
    if ($removed) {
        Write-Host "App data + voice profiles removed from $InstallDir." -ForegroundColor DarkGray
    }
    Write-Host "Session transcripts in Documents\voxterm were preserved." -ForegroundColor DarkGray
    Write-Host ""
    exit 0
}

# ── Header ────────────────────────────────────────────────────
Write-Host ""
Write-Host "VOXTERM" -ForegroundColor White -NoNewline
Write-Host " — local voice transcription"
Write-Host "everything runs on your machine, nothing leaves" -ForegroundColor DarkGray
Write-Host ""

# ── Resolve version ───────────────────────────────────────────
if (-not $Version) {
    Info "checking latest release..."
    try {
        $releases = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases" -ErrorAction Stop
        $vTag = $releases | Where-Object { $_.tag_name -like "v*" } | Select-Object -First 1
        if ($vTag) {
            $Version = $vTag.tag_name
            OK "latest release: $Version"
        } else {
            $Version = "main"
            Warn "no v* releases found, using main branch"
        }
    } catch {
        $Version = "main"
        Warn "failed to query releases: $_; using main branch"
    }
}

# ── Already up-to-date check ──────────────────────────────────
if (Test-Path $VersionFile) {
    $installed = (Get-Content $VersionFile -ErrorAction SilentlyContinue).Trim()
    if ($installed -eq $Version) {
        OK "already up to date ($installed)"
        Write-Host ""
        exit 0
    }
    Info "updating $installed -> $Version"
}

# ── Check Python ──────────────────────────────────────────────
Info "checking python..."
$PythonExe = $null
foreach ($cmd in @("python3.12","python3.11","python3.10","python","python3")) {
    try {
        $verOut = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $verOut) {
            $parts = $verOut.Trim().Split('.')
            $major = [int]$parts[0]; $minor = [int]$parts[1]
            if ($major -eq 3 -and $minor -ge 9) {
                $PythonExe = $cmd
                break
            }
        }
    } catch {
        # try next candidate
    }
}

if (-not $PythonExe) {
    Fail @"
Python 3.9+ required but not found.
Install it from https://www.python.org/downloads/windows/ or via winget:
  winget install Python.Python.3.12
After install, open a new PowerShell window and re-run this installer.
"@
}

$pyVer = & $PythonExe --version
OK "found $PythonExe ($pyVer)"

# ── Download release ──────────────────────────────────────────
Info "downloading voxterm $Version..."

if ($Version -eq "main") {
    $ArchiveUrl = "$RepoUrl/archive/refs/heads/main.zip"
} else {
    $ArchiveUrl = "$RepoUrl/archive/refs/tags/$Version.zip"
}

$TmpDir = Join-Path $env:TEMP "voxterm-install-$([guid]::NewGuid().Guid.Substring(0,8))"
$TmpZip = Join-Path $TmpDir "voxterm.zip"
$TmpExtract = Join-Path $TmpDir "extract"
New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null
New-Item -ItemType Directory -Force -Path $TmpExtract | Out-Null

try {
    Invoke-WebRequest -Uri $ArchiveUrl -OutFile $TmpZip -UseBasicParsing
    Expand-Archive -Path $TmpZip -DestinationPath $TmpExtract -Force

    # GitHub zips contain a single top-level directory like VoxTerm-main/
    $topDir = Get-ChildItem $TmpExtract -Directory | Select-Object -First 1
    if (-not $topDir) {
        Fail "downloaded archive was empty"
    }

    # Preserve venv across upgrades to avoid re-downloading deps
    $preservedVenv = $null
    if (Test-Path $VenvDir) {
        $preservedVenv = Join-Path $TmpDir ".venv-preserved"
        Move-Item $VenvDir $preservedVenv
    }

    # Wipe old source tree but PRESERVE user data + cached venv. We list
    # everything we want to keep so a corrupt source tree doesn't take
    # speakers.db or crash logs down with it.
    $preserve = @(
        "crashes", "bin", ".venv", ".backups",
        ".speakers.db", ".speakers.db-wal", ".speakers.db-shm",
        ".keyfile", "state.json", ".installed-version"
    )
    Get-ChildItem $InstallDir -Force -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -notin $preserve
    } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    if (-not (Test-Path $InstallDir)) {
        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    }

    # Copy fresh source over
    Copy-Item -Path (Join-Path $topDir.FullName "*") -Destination $InstallDir -Recurse -Force

    # Restore preserved venv
    if ($preservedVenv) {
        Move-Item $preservedVenv $VenvDir
    }

    OK "downloaded"
} finally {
    Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
}

# ── Create venv & install deps ────────────────────────────────
Info "installing dependencies..."
Write-Host "  this may take a minute on first install" -ForegroundColor DarkGray

if (-not (Test-Path $VenvPy)) {
    & $PythonExe -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Fail "failed to create venv" }
}

& $VenvPy -m pip install --quiet --upgrade pip 2>&1 | Out-Null
& $VenvPip install --quiet -r (Join-Path $InstallDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "pip install failed — see output above" }

OK "dependencies installed"

# ── Record version ────────────────────────────────────────────
Set-Content -Path $VersionFile -Value $Version

# ── Create launcher ───────────────────────────────────────────
Info "creating voxterm command..."

if (-not (Test-Path $BinDir)) {
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
}

$batContent = @"
@echo off
REM VOXTERM launcher (auto-generated by install.ps1)
set VOXTERM_DIR=$InstallDir
set VENV_PY=%VOXTERM_DIR%\.venv\Scripts\python.exe
if not exist "%VENV_PY%" (
    echo VoxTerm venv missing at %VENV_PY%
    echo Re-run install.ps1 to repair the install.
    exit /b 1
)
pushd "%VOXTERM_DIR%"
set PYTHONWARNINGS=ignore::UserWarning
"%VENV_PY%" -m tui.app %*
set RC=%ERRORLEVEL%
popd
exit /b %RC%
"@

Set-Content -Path $LauncherBat -Value $batContent -Encoding ASCII
OK "installed launcher to $LauncherBat"

# ── PATH check ────────────────────────────────────────────────
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($null -eq $userPath) { $userPath = "" }
if ($userPath -notlike "*$BinDir*") {
    Write-Host ""
    Info "adding $BinDir to your User PATH"
    if ($userPath -eq "") {
        $newPath = $BinDir
    } else {
        $newPath = "$userPath;$BinDir"
    }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    OK "PATH updated — open a new terminal to pick up the change"
}

# ── Done ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "voxterm $Version installed!" -ForegroundColor Green
Write-Host ""
Write-Host "  run it:    voxterm" -ForegroundColor White
Write-Host "  update:    iwr -useb $RepoUrl/raw/main/install.ps1 | iex" -ForegroundColor DarkGray
Write-Host "  uninstall: powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall" -ForegroundColor DarkGray
Write-Host ""
Write-Host "First-run notes:" -ForegroundColor DarkGray
Write-Host "  - Mic capture works out of the box." -ForegroundColor DarkGray
Write-Host "  - System audio uses WASAPI loopback. If unavailable on your driver," -ForegroundColor DarkGray
Write-Host "    enable 'Stereo Mix' in Settings -> Sound -> More sound settings." -ForegroundColor DarkGray
Write-Host ""
