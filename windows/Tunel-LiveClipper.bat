@echo off
rem Live Clipper - levanta el tunel de Cloudflare para publicar la app en tu subdominio.
title Live Clipper - Tunel
set "LC_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:LC_SELF); $m='#'+'#PS1#'+'#'; Invoke-Expression ($s.Substring($s.IndexOf($m)+$m.Length))"
exit /b
##PS1##
# Live Clipper - Cloudflare Tunnel launcher (run from windows/Tunel-LiveClipper.bat).
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

function Has([string]$c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Refresh-Path {
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$m;$u;" + (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links')
}

function Set-EnvVar([string]$file, [string]$key, [string]$value) {
    $lines = @(); if (Test-Path $file) { $lines = @(Get-Content $file) }
    $out = @()
    $found = $false
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

$root = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $root 'backend\.env'
$startBat = Join-Path $PSScriptRoot 'Iniciar-LiveClipper.bat'

Write-Host ''
Write-Host '  Live Clipper - Tunel de Cloudflare' -ForegroundColor Magenta

# Get cloudflared: use the system one if present, otherwise download the portable .exe (no install, no admin)
Refresh-Path
$cf = 'cloudflared'
if (-not (Has 'cloudflared')) {
    $cfDir = Join-Path $root 'bin'
    New-Item -ItemType Directory -Force -Path $cfDir | Out-Null
    $cf = Join-Path $cfDir 'cloudflared.exe'
    if (-not (Test-Path $cf)) {
        Write-Host '==> Descargando cloudflared (portable, sin instalar)...' -ForegroundColor Cyan
        $url = 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
        try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $cf }
        catch { Write-Host "  No se pudo descargar cloudflared: $($_.Exception.Message)" -ForegroundColor Red; Read-Host 'Enter para salir'; exit 1 }
    }
}

# Public URL (persisted so you only type it once)
$publicUrl = Get-EnvVar $envFile 'CLIPPER_PUBLIC_BASE_URL'
if (-not $publicUrl) {
    $publicUrl = (Read-Host 'URL publica de tu subdominio (ej. https://clipper.konq.lol)').Trim().TrimEnd('/')
}
if ($publicUrl -notmatch '^https?://') { Write-Host '  La URL debe empezar por https://' -ForegroundColor Red; Read-Host 'Enter para salir'; exit 1 }
Set-EnvVar $envFile 'CLIPPER_PUBLIC_BASE_URL' $publicUrl

# Access token so the public URL is only usable by you
$token = Get-EnvVar $envFile 'CLIPPER_API_TOKEN'
if (-not $token) {
    $token = -join ((48..57) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })
    Set-EnvVar $envFile 'CLIPPER_API_TOKEN' $token
}

Write-Host ''
Write-Host "  Subdominio: $publicUrl" -ForegroundColor Green
Write-Host "  Token de acceso (lo pedira la app una vez, en Configuracion): $token" -ForegroundColor Yellow
Write-Host ''

$tunnelToken = (Read-Host 'Pega el token del tunel de Cloudflare (la cadena eyJ... tras --token)').Trim()
if (-not $tunnelToken) { Write-Host '  Sin token del tunel no se puede continuar.' -ForegroundColor Red; Read-Host 'Enter para salir'; exit 1 }

# Start the app in its own window (serves on localhost:8000) and then the tunnel.
if (Test-Path $startBat) { Start-Process -FilePath $startBat }
Write-Host '==> Levantando el tunel. Deja esta ventana abierta. Ctrl+C para detener.' -ForegroundColor Cyan
& $cf tunnel run --token $tunnelToken
Write-Host ''
Write-Host '  Tunel detenido.'
Read-Host 'Enter para cerrar'
