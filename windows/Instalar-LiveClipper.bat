@echo off
rem Universal AI Live Clipper - instalador de un clic para Windows 10/11.
rem Este archivo contiene el instalador en PowerShell tras la marca de abajo.
title Instalador de Live Clipper
set "LC_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:LC_SELF); $m='#'+'#PS1#'+'#'; Invoke-Expression ($s.Substring($s.IndexOf($m)+$m.Length))"
exit /b
##PS1##
# Universal AI Live Clipper - instalador para Windows 10/11 (Windows PowerShell 5.1+).
# Se ejecuta desde Instalar-LiveClipper.bat (doble clic). Es seguro ejecutarlo varias veces:
# instala solo lo que falta y actualiza el código si ya estaba descargado.

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Repo   = 'https://github.com/konqdv/knqclipper'
$Branch = ''   # vacío = la rama por defecto del repositorio
if ($env:CLIPPER_BRANCH) { $Branch = $env:CLIPPER_BRANCH }
$Dest   = Join-Path $env:USERPROFILE 'knqclipper'
$Log    = Join-Path $env:TEMP 'knqclipper-install.log'

function Say([string]$Message) { Write-Host ''; Write-Host "==> $Message" -ForegroundColor Cyan }
function Ok([string]$Message) { Write-Host "    $Message" -ForegroundColor Green }
function Has([string]$Command) { return [bool](Get-Command $Command -ErrorAction SilentlyContinue) }

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $links = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'
    $env:Path = "$machine;$user;$links"
}

function Assert-Exit([string]$What) {
    if ($LASTEXITCODE -ne 0) { throw "$What falló (código $LASTEXITCODE)." }
}

function Install-WithWinget([string]$Id, [string]$Name) {
    Say "Instalando $Name (puede tardar unos minutos)..."
    winget install --id $Id -e --silent --accept-package-agreements --accept-source-agreements
    Update-SessionPath
}

function Find-Python {
    # The "python" command on a clean Windows is a Microsoft Store stub, so use the py launcher first.
    if (Has 'py') {
        foreach ($v in @('3.12', '3.13', '3.11')) {
            $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return ([string]$exe).Trim() }
        }
    }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe')
    )
    foreach ($p in $candidates) { if (Test-Path $p) { return $p } }
    return $null
}

function Install-LiveClipper {
    Write-Host ''
    Write-Host '  Universal AI Live Clipper - instalador' -ForegroundColor Magenta
    Write-Host "  Carpeta de instalación: $Dest"

    # --- 1. herramientas ---------------------------------------------------------------
    Update-SessionPath
    if (-not (Has 'winget')) {
        Start-Process 'ms-windows-store://pdp/?productid=9NBLGGH4NNS1'
        throw 'Falta "Instalador de aplicación" (winget). Se abrió Microsoft Store: instálalo y vuelve a ejecutar este instalador.'
    }
    if (-not (Has 'git')) { Install-WithWinget 'Git.Git' 'Git' }
    if (-not (Has 'git')) { throw 'No se pudo instalar Git.' }
    Ok 'Git listo'

    $py = Find-Python
    if (-not $py) { Install-WithWinget 'Python.Python.3.12' 'Python 3.12'; $py = Find-Python }
    if (-not $py) { throw 'No se pudo instalar Python 3.12. Instálalo desde python.org y vuelve a intentarlo.' }
    Ok "Python listo ($py)"

    if (-not (Has 'npm')) { Install-WithWinget 'OpenJS.NodeJS.LTS' 'Node.js' }
    if (-not (Has 'npm')) { throw 'No se pudo instalar Node.js.' }
    Ok 'Node.js listo'

    if (-not (Has 'ffmpeg')) { Install-WithWinget 'Gyan.FFmpeg' 'FFmpeg' }
    if (-not (Has 'ffmpeg')) { throw 'No se pudo instalar FFmpeg.' }
    Ok 'FFmpeg listo'

    # --- 2. código ------------------------------------------------------------------------
    if (Test-Path (Join-Path $Dest '.git')) {
        Say 'Actualizando el código...'
        git -C $Dest pull --ff-only
        Assert-Exit 'La actualización del código'
    } elseif (Test-Path $Dest) {
        throw "Ya existe la carpeta $Dest pero no es una descarga de Live Clipper. Renómbrala y vuelve a ejecutar el instalador."
    } else {
        Say 'Descargando el código...'
        if ($Branch) { git clone --branch $Branch $Repo $Dest } else { git clone $Repo $Dest }
        Assert-Exit 'La descarga del código'
    }

    # --- 3. interfaz ----------------------------------------------------------------------
    Say 'Compilando la interfaz...'
    Push-Location (Join-Path $Dest 'frontend')
    try {
        npm install --no-audit --no-fund
        Assert-Exit 'npm install'
        npm run build
        Assert-Exit 'La compilación de la interfaz'
    } finally { Pop-Location }
    if (-not (Test-Path (Join-Path $Dest 'frontend\dist\index.html'))) { throw 'La interfaz no se generó (falta frontend\dist).' }
    Ok 'Interfaz compilada'

    # --- 4. backend -------------------------------------------------------------------------
    $backend = Join-Path $Dest 'backend'
    $venvPy = Join-Path $backend '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPy)) {
        Say 'Creando el entorno de Python...'
        & $py -m venv (Join-Path $backend '.venv')
        Assert-Exit 'La creación del entorno de Python'
    }
    Say 'Instalando dependencias de Python (Whisper, OpenCV, FastAPI...)'
    & $venvPy -m pip install --upgrade pip --disable-pip-version-check
    & $venvPy -m pip install -e $backend --disable-pip-version-check
    Assert-Exit 'La instalación de dependencias de Python'
    Ok 'Backend instalado'

    $envFile = Join-Path $backend '.env'
    if (-not (Test-Path $envFile)) {
        @(
            '# Configuración de Live Clipper (ver .env.example para todas las opciones)',
            'CLIPPER_DATA_DIR=./data',
            'CLIPPER_WHISPER_MODEL=small',
            'CLIPPER_WHISPER_DEVICE=cpu'
        ) | Set-Content -Path $envFile -Encoding ASCII
    }

    # --- 5. modelo de voz (para que el primer uso sea inmediato) -------------------------------
    Say 'Descargando el modelo de voz Whisper (unos 500 MB, solo la primera vez)...'
    $cache = Join-Path $backend 'data\models\hf\hub'
    $env:LC_CACHE = $cache
    & $venvPy -c "import os; from faster_whisper import download_model; download_model('small', cache_dir=os.environ['LC_CACHE'])"
    if ($LASTEXITCODE -ne 0) { Write-Host '    No se pudo descargar ahora; se descargará al procesar el primer vídeo.' -ForegroundColor Yellow }
    else { Ok 'Modelo listo' }

    # --- 6. acceso directo ----------------------------------------------------------------------
    $start = Join-Path $Dest 'windows\Iniciar-LiveClipper.bat'
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut((Join-Path $desktop 'Live Clipper.lnk'))
    $lnk.TargetPath = $start
    $lnk.WorkingDirectory = $backend
    $lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,115"
    $lnk.Description = 'Iniciar Universal AI Live Clipper'
    $lnk.Save()
    Ok 'Acceso directo "Live Clipper" creado en el escritorio'

    Write-Host ''
    Write-Host '  Instalación completada.' -ForegroundColor Green
    Write-Host '  Abriendo Live Clipper... (las próximas veces usa el acceso directo del escritorio)'
    Start-Process -FilePath $start
}

Start-Transcript -Path $Log -Force | Out-Null
$failed = $false
try {
    Install-LiveClipper
} catch {
    $failed = $true
    Write-Host ''
    Write-Host "  ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Registro completo: $Log"
    Write-Host '  Si no sabes cómo resolverlo, comparte ese archivo o el mensaje de arriba.'
} finally {
    Stop-Transcript | Out-Null
}
Write-Host ''
if ($failed) { Read-Host 'Pulsa Enter para cerrar' | Out-Null } else { Start-Sleep -Seconds 5 }
