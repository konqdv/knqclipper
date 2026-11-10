@echo off
rem Clipper2 - fork multiplataforma (Stream-Clipper-Factory + conectores Twitch/Kick/YouTube).
title Clipper2
set "LC_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:LC_SELF); $m='#'+'#PS1#'+'#'; Invoke-Expression ($s.Substring($s.IndexOf($m)+$m.Length))"
exit /b
##PS1##
# Clipper2 - lanzador de un clic: instala/actualiza y arranca el fork (Stream-Clipper-Factory + conectores).
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$Repo = 'https://github.com/konqdv/knqclipper'
$Dest = Join-Path $env:USERPROFILE 'knqclipper'
$App  = Join-Path $Dest 'clipper2'
$Log  = Join-Path $env:TEMP 'clipper2.log'

function Say([string]$m) { Write-Host ''; Write-Host "==> $m" -ForegroundColor Cyan }
function Ok([string]$m)  { Write-Host "    $m" -ForegroundColor Green }
function Has([string]$c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Assert-Exit([string]$w) { if ($LASTEXITCODE -ne 0) { throw "$w fallo (codigo $LASTEXITCODE)." } }

function Update-SessionPath {
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$m;$u;" + (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links')
}
function Install-WithWinget([string]$Id, [string]$Name) {
    Say "Instalando $Name (puede tardar)..."
    winget install --id $Id -e --silent --accept-package-agreements --accept-source-agreements
    Update-SessionPath
}
function Find-Python {
    if (Has 'py') {
        foreach ($v in @('3.12', '3.13', '3.11')) {
            $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return ([string]$exe).Trim() }
        }
    }
    foreach ($p in @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
                     (Join-Path $env:ProgramFiles 'Python312\python.exe'),
                     (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
                     (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'))) {
        if (Test-Path $p) { return $p }
    }
    return $null
}
function Set-EnvVar([string]$file, [string]$key, [string]$value) {
    $lines = @(); if (Test-Path $file) { $lines = @(Get-Content $file) }
    $out = @(); $found = $false
    foreach ($l in $lines) {
        if ($l -match "^\s*$([regex]::Escape($key))=") { $out += "$key=$value"; $found = $true } else { $out += $l }
    }
    if (-not $found) { $out += "$key=$value" }
    Set-Content -Path $file -Value $out -Encoding ASCII
}

function Ensure-Tools {
    Update-SessionPath
    if (-not (Has 'winget')) { Start-Process 'ms-windows-store://pdp/?productid=9NBLGGH4NNS1'; throw 'Falta "Instalador de aplicacion" (winget). Instalalo desde Microsoft Store y reintenta.' }
    if (-not (Has 'git'))  { Install-WithWinget 'Git.Git' 'Git' }
    $script:Py = Find-Python
    if (-not $script:Py) { Install-WithWinget 'Python.Python.3.12' 'Python 3.12'; $script:Py = Find-Python }
    if (-not (Has 'npm'))  { Install-WithWinget 'OpenJS.NodeJS.LTS' 'Node.js' }
    if (-not (Has 'ffmpeg')) { Install-WithWinget 'Gyan.FFmpeg' 'FFmpeg' }
    if (-not ((Has 'git') -and $script:Py -and (Has 'npm') -and (Has 'ffmpeg'))) { throw 'No se pudieron instalar todas las herramientas (Git, Python, Node, FFmpeg).' }
}

function Ensure-Env {
    $envFile = Join-Path $App '.env'
    if (-not (Test-Path $envFile)) {
        $example = Join-Path $App '.env.example'
        if (Test-Path $example) { Copy-Item $example $envFile -Force } else { New-Item -ItemType File -Path $envFile | Out-Null }
    }
    # CPU fiable por defecto (el modelo es pequeno -> rapido). La GPU se puede activar luego.
    Set-EnvVar $envFile 'WHISPER_MODEL' 'small'
    Set-EnvVar $envFile 'WHISPER_DEVICE' 'cpu'
    Set-EnvVar $envFile 'API_MODE' 'lite'
}

function Ensure-Installed {
    if (Test-Path (Join-Path $Dest '.git')) {
        Ensure-Tools
        Say 'Buscando actualizaciones...'
        git -C $Dest fetch --prune origin 2>$null
        git -C $Dest remote set-head origin --auto *> $null
        $ref = git -C $Dest symbolic-ref --short refs/remotes/origin/HEAD 2>$null
        $target = ''
        if ($ref) { $target = ([string]$ref).Trim() -replace '^origin/', '' }
        if (-not $target) { $target = 'AI-Live-Clipper' }
        $cur = ([string](git -C $Dest rev-parse --abbrev-ref HEAD)).Trim()
        if ($target -and $cur -ne $target) { git -C $Dest checkout $target 2>$null }
        git -C $Dest pull --ff-only 2>$null
        try {
            $repoBat = Join-Path $Dest 'windows\Clipper2.bat'
            if ((Test-Path $repoBat) -and $env:LC_SELF -and (Test-Path $env:LC_SELF) -and ((Get-FileHash $repoBat).Hash -ne (Get-FileHash $env:LC_SELF).Hash)) {
                Copy-Item $repoBat $env:LC_SELF -Force
                Ok 'Lanzador actualizado solo (el proximo arranque usa la version nueva).'
            }
        } catch { }
    } elseif (Test-Path $Dest) {
        throw "Ya existe $Dest pero no es una instalacion valida. Renombrala y reintenta."
    } else {
        Ensure-Tools
        Say 'Descargando Clipper2...'
        git clone $Repo $Dest; Assert-Exit 'La descarga del codigo'
    }
    if (-not (Test-Path $App)) { throw "No se encontro la carpeta clipper2 en $Dest." }

    $venvPy = Join-Path $App '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPy)) { Say 'Creando el entorno de Python...'; & $script:Py -m venv (Join-Path $App '.venv'); Assert-Exit 'La creacion del entorno' }
    Say 'Instalando dependencias de Python (la 1a vez tarda)...'
    & $venvPy -m pip install --upgrade pip --disable-pip-version-check | Out-Host
    & $venvPy -m pip install -r (Join-Path $App 'requirements.txt') --disable-pip-version-check | Out-Host
    Assert-Exit 'La instalacion de dependencias'
    Say 'Instalando la interfaz...'
    Push-Location (Join-Path $App 'frontend')
    try { npm install --no-audit --no-fund | Out-Host; Assert-Exit 'npm install' } finally { Pop-Location }
    Ensure-Env

    try {
        $sh = New-Object -ComObject WScript.Shell
        $lnk = $sh.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Clipper2.lnk'))
        $lnk.TargetPath = $env:LC_SELF; $lnk.WorkingDirectory = $App
        $lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,115"; $lnk.Save()
    } catch { }
    return $venvPy
}

Start-Transcript -Path $Log -Force | Out-Null
try {
    Write-Host ''; Write-Host '  Clipper2' -ForegroundColor Magenta
    $venvPy = Ensure-Installed | Where-Object { $_ -is [string] } | Select-Object -Last 1
    Say 'Arrancando Clipper2... se abrira el navegador en http://127.0.0.1:5173'
    Ok 'Deja esta ventana abierta. Cierrala (o Ctrl+C) para apagar la app.'
    Set-Location $App
    & $venvPy (Join-Path $App 'app.py')
} catch {
    Write-Host ''; Write-Host "  ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Registro: $Log  (comparte ese archivo si no sabes resolverlo)"
    Stop-Transcript | Out-Null
    Read-Host 'Enter para cerrar' | Out-Null
    exit 1
}
Stop-Transcript | Out-Null
