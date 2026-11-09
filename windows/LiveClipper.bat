@echo off
rem Live Clipper - archivo unico: instala/actualiza, arranca la app y (opcional) levanta el tunel.
title Live Clipper
set "LC_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:LC_SELF); $m='#'+'#PS1#'+'#'; Invoke-Expression ($s.Substring($s.IndexOf($m)+$m.Length))"
exit /b
##PS1##

# Live Clipper - archivo unico: instala o actualiza, arranca la app y (opcional) levanta el tunel.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$Repo = 'https://github.com/konqdv/knqclipper'
$Dest = Join-Path $env:USERPROFILE 'knqclipper'
$Log  = Join-Path $env:TEMP 'knqclipper.log'

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
function Get-EnvVar([string]$file, [string]$key) {
    if (-not (Test-Path $file)) { return $null }
    foreach ($l in Get-Content $file) { if ($l -match "^\s*$([regex]::Escape($key))=(.*)$") { return $Matches[1].Trim() } }
    return $null
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

function Build-And-Install([string]$backend, [string]$venvPy) {
    Say 'Compilando la interfaz...'
    Push-Location (Join-Path $Dest 'frontend')
    try { npm install --no-audit --no-fund; Assert-Exit 'npm install'; npm run build; Assert-Exit 'La compilacion de la interfaz' } finally { Pop-Location }
    # record which commit the compiled UI was built from, so a stale dist forces a rebuild next time
    $sha = ([string](git -C $Dest rev-parse HEAD 2>$null)).Trim()
    if ($sha) { Set-Content -Path (Join-Path $Dest 'frontend\dist\.built') -Value $sha -Encoding ASCII }
    if (-not (Test-Path $venvPy)) { Say 'Creando el entorno de Python...'; & $script:Py -m venv (Join-Path $backend '.venv'); Assert-Exit 'La creacion del entorno' }
    Say 'Instalando dependencias de Python...'
    & $venvPy -m pip install --upgrade pip --disable-pip-version-check
    & $venvPy -m pip install -e $backend --disable-pip-version-check
    Assert-Exit 'La instalacion de dependencias'
    $script:Built = $true
    Ok 'Actualizado'
}

function Setup-Gpu([string]$backend, [string]$venvPy, [string]$envFile) {
    # Una sola vez: si hay GPU NVIDIA, activa CUDA + modelo grande (mejor calidad y velocidad).
    $marker = Join-Path $backend '.gpu-configured'
    if (Test-Path $marker) { return }
    $hasNvidia = $false
    try {
        $smi = Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'
        if (Test-Path $smi) { & $smi -L *> $null; if ($LASTEXITCODE -eq 0) { $hasNvidia = $true } }
    } catch { }
    if (-not $hasNvidia) {
        try { if (Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'NVIDIA' }) { $hasNvidia = $true } } catch { }
    }
    if ($hasNvidia) {
        Say 'GPU NVIDIA detectada: activando CUDA para transcribir mas rapido y con mejor calidad...'
        & $venvPy -m pip install --disable-pip-version-check nvidia-cublas-cu12 nvidia-cudnn-cu12
        if ($LASTEXITCODE -eq 0) {
            Set-EnvVar $envFile 'CLIPPER_WHISPER_DEVICE' 'cuda'
            Set-EnvVar $envFile 'CLIPPER_WHISPER_MODEL' 'large-v3-turbo'
            $script:Built = $true
            Ok 'CUDA activado (modelo large-v3-turbo). Si la GPU fallara, la app usa CPU automaticamente.'
        } else {
            Ok 'No se pudieron instalar las librerias CUDA; se seguira usando CPU.'
        }
    } else {
        Ok 'Sin GPU NVIDIA: se usa CPU (modelo small).'
    }
    'configured' | Set-Content -Path $marker -Encoding ASCII
}

function Ensure-Model([string]$backend, [string]$venvPy, [string]$envFile) {
    # Descarga por adelantado el modelo de voz configurado (evita la espera en el primer video).
    $model = Get-EnvVar $envFile 'CLIPPER_WHISPER_MODEL'
    if (-not $model) { $model = 'small' }
    $cache = Join-Path $backend 'data\models\hf\hub'
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    Say "Preparando el modelo de voz ($model)..."
    try {
        & $venvPy -c "from faster_whisper import download_model; download_model('$model', cache_dir=r'$cache')"
        if ($LASTEXITCODE -eq 0) { Ok 'Modelo listo.' } else { Ok 'El modelo se descargara al procesar el primer video.' }
    } catch { Ok 'El modelo se descargara al procesar el primer video.' }
}

function Ensure-Installed {
    $script:Built = $false
    $backend = Join-Path $Dest 'backend'
    $venvPy  = Join-Path $backend '.venv\Scripts\python.exe'
    $distIdx = Join-Path $Dest 'frontend\dist\index.html'

    if (Test-Path (Join-Path $Dest '.git')) {
        Ensure-Tools
        Say 'Buscando actualizaciones...'
        git -C $Dest fetch --prune origin 2>$null
        $target = ''
        git -C $Dest remote set-head origin --auto *> $null
        $ref = git -C $Dest symbolic-ref --short refs/remotes/origin/HEAD 2>$null
        if ($ref) { $target = ([string]$ref).Trim() -replace '^origin/', '' }
        if (-not $target) { $target = 'AI-Live-Clipper' }
        $local  = ([string](git -C $Dest rev-parse HEAD 2>$null)).Trim()
        $remote = ([string](git -C $Dest rev-parse "origin/$target" 2>$null)).Trim()
        $builtFile = Join-Path $Dest 'frontend\dist\.built'
        $built = ''
        if (Test-Path $builtFile) { $built = ([string](Get-Content $builtFile -Raw)).Trim() }
        if ($local -eq $remote -and (Test-Path $distIdx) -and (Test-Path $venvPy) -and $built -eq $remote) {
            Ok 'Ya tienes la ultima version.'
        } else {
            $cur = ([string](git -C $Dest rev-parse --abbrev-ref HEAD)).Trim()
            if ($target -and $cur -ne $target) { Say "Cambiando a la rama $target..."; git -C $Dest checkout $target; Assert-Exit "checkout $target" }
            git -C $Dest pull --ff-only; Assert-Exit 'La actualizacion del codigo'
            # self-update the .bat the user runs (header is identical across versions, safe to overwrite)
            try {
                $repoBat = Join-Path $Dest 'windows\LiveClipper.bat'
                if ((Test-Path $repoBat) -and $env:LC_SELF -and (Test-Path $env:LC_SELF) -and ((Get-FileHash $repoBat).Hash -ne (Get-FileHash $env:LC_SELF).Hash)) {
                    Copy-Item $repoBat $env:LC_SELF -Force
                    Ok 'Instalador actualizado: tu .bat se actualizo solo (el proximo arranque usa la version nueva).'
                }
            } catch { }
            Build-And-Install $backend $venvPy
        }
    } elseif (Test-Path $Dest) {
        throw "Ya existe $Dest pero no es una instalacion de Live Clipper. Renombrala y reintenta."
    } else {
        Ensure-Tools
        Say 'Descargando Live Clipper...'
        git clone $Repo $Dest; Assert-Exit 'La descarga del codigo'
        Build-And-Install $backend $venvPy
    }

    $envFile = Join-Path $backend '.env'
    if (-not (Test-Path $envFile)) { @('CLIPPER_DATA_DIR=./data', 'CLIPPER_WHISPER_MODEL=small', 'CLIPPER_WHISPER_DEVICE=auto') | Set-Content -Path $envFile -Encoding ASCII }

    Setup-Gpu $backend $venvPy $envFile
    if ($script:Built) { Ensure-Model $backend $venvPy $envFile }

    # Desktop shortcut to THIS single file
    try {
        $self = $env:LC_SELF
        $sh = New-Object -ComObject WScript.Shell
        $lnk = $sh.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Live Clipper.lnk'))
        $lnk.TargetPath = $self; $lnk.WorkingDirectory = $backend
        $lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,115"; $lnk.Save()
    } catch { }

    return @{ backend = $backend; venvPy = $venvPy; envFile = $envFile }
}

function Open-App([string]$url) {
    # Abre en una ventana dedicada (Chrome/Edge --app): se ve como app de escritorio, sin barra del navegador.
    $exes = @()
    foreach ($n in @('chrome.exe', 'msedge.exe')) {
        foreach ($h in @('HKLM:', 'HKCU:')) {
            try { $p = (Get-ItemProperty "$h\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$n" -ErrorAction Stop).'(default)'; if ($p -and (Test-Path $p)) { $exes += $p } } catch { }
        }
    }
    if ($env:ProgramFiles) { $exes += (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe') }
    $px86 = ${env:ProgramFiles(x86)}
    if ($px86) { $exes += (Join-Path $px86 'Google\Chrome\Application\chrome.exe') }
    if ($env:LOCALAPPDATA) { $exes += (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe') }
    if ($px86) { $exes += (Join-Path $px86 'Microsoft\Edge\Application\msedge.exe') }
    if ($env:ProgramFiles) { $exes += (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe') }
    foreach ($exe in $exes) {
        if (Test-Path $exe) {
            try { Start-Process -FilePath $exe -ArgumentList "--app=$url", '--window-size=1360,900'; return } catch { }
        }
    }
    Start-Process $url  # alternativa: pestaña del navegador por defecto
}

function Start-App([hashtable]$p) {
    $clipper = Join-Path $p.backend '.venv\Scripts\clipper.exe'
    if (-not (Test-Path $clipper)) { throw 'No se encontro clipper.exe tras la instalacion.' }
    # already running?
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8000/api/system/status' | Out-Null; Ok 'La app ya estaba abierta.'; Open-App 'http://localhost:8000'; return } catch { }
    Say 'Arrancando Live Clipper...'
    Start-Process -FilePath $clipper -ArgumentList 'serve' -WorkingDirectory $p.backend -WindowStyle Minimized
    for ($i = 0; $i -lt 120; $i++) {
        try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8000/api/system/status' | Out-Null; break } catch { Start-Sleep -Seconds 1 }
    }
    Open-App 'http://localhost:8000'
    Ok 'Abierto en http://localhost:8000'
}

function Get-Cloudflared {
    if (Has 'cloudflared') { return 'cloudflared' }
    $dir = Join-Path $Dest 'bin'; New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $cf = Join-Path $dir 'cloudflared.exe'
    if (-not (Test-Path $cf)) {
        Say 'Descargando cloudflared (portable, sin instalar)...'
        try { Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile $cf }
        catch { throw "No se pudo descargar cloudflared: $($_.Exception.Message)" }
    }
    return $cf
}

function Run-Tunnel([hashtable]$p) {
    $cf = Get-Cloudflared
    $publicUrl = Get-EnvVar $p.envFile 'CLIPPER_PUBLIC_BASE_URL'
    if (-not $publicUrl) { $publicUrl = (Read-Host 'URL publica de tu subdominio (ej. https://clipper.konq.lol)').Trim().TrimEnd('/') }
    if ($publicUrl -notmatch '^https?://') { throw 'La URL debe empezar por https://' }
    Set-EnvVar $p.envFile 'CLIPPER_PUBLIC_BASE_URL' $publicUrl
    $token = Get-EnvVar $p.envFile 'CLIPPER_API_TOKEN'
    if (-not $token) { $token = -join ((48..57) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ }); Set-EnvVar $p.envFile 'CLIPPER_API_TOKEN' $token }
    Write-Host ''; Write-Host "  Subdominio: $publicUrl" -ForegroundColor Green
    Write-Host "  Token de acceso (la app lo pide una vez, en Configuracion): $token" -ForegroundColor Yellow
    $tt = (Read-Host 'Pega el token del tunel de Cloudflare (cadena eyJ...)').Trim()
    if (-not $tt) { throw 'Sin token del tunel no se puede continuar.' }
    Say 'Levantando el tunel. Deja esta ventana abierta. Ctrl+C para detener.'
    & $cf tunnel run --token $tt
}

Start-Transcript -Path $Log -Force | Out-Null
try {
    Write-Host ''; Write-Host '  Live Clipper' -ForegroundColor Magenta
    $p = Ensure-Installed | Where-Object { $_ -is [hashtable] } | Select-Object -Last 1
    Start-App $p
    Write-Host ''
    $ans = Read-Host '¿Levantar tambien el tunel de Cloudflare para tu subdominio? (s/N)'
    if ($ans -match '^[sSyY]') { Run-Tunnel $p } else { Write-Host '  Listo. La app corre en su propia ventana; cierrala para apagarla.' -ForegroundColor Green; Start-Sleep -Seconds 4 }
} catch {
    Write-Host ''; Write-Host "  ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Registro: $Log  (comparte ese archivo si no sabes resolverlo)"
    Stop-Transcript | Out-Null
    Read-Host 'Enter para cerrar' | Out-Null
    exit 1
}
Stop-Transcript | Out-Null
