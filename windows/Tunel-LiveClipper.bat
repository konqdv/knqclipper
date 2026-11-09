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

Refresh-Path
if (-not (Has 'cloudflared')) {
    Write-Host '==> Instalando cloudflared...' -ForegroundColor Cyan
    winget install --id Cloudflare.cloudflared -e --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}
if (-not (Has 'cloudflared')) { Write-Host '  No se pudo instalar cloudflared. Instalalo desde https://github.com/cloudflare/cloudflared/releases' -ForegroundColor Red; Read-Host 'Enter para salir'; exit 1 }

# Public URL (persisted so you only type it once)
$publicUrl = Get-EnvVar $envFile 'CLIPPER_PUBLIC_BASE_URL'
if (-not $publicUrl) {
    $publicUrl = (Read-Host 'URL publica de tu subdominio (ej. https://clipper.tudominio.com)').Trim().TrimEnd('/')
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

$tunnelToken = (Read-Host 'Pega el token del tunel de Cloudflare (tras --token)').Trim()
if (-not $tunnelToken) { Write-Host '  Sin token del tunel no se puede continuar.' -ForegroundColor Red; Read-Host 'Enter para salir'; exit 1 }

# Start the app in its own window (serves on localhost:8000) and then the tunnel.
if (Test-Path $startBat) { Start-Process -FilePath $startBat }
Write-Host '==> Levantando el tunel. Deja esta ventana abierta. Ctrl+C para detener.' -ForegroundColor Cyan
& cloudflared tunnel run --token $tunnelToken
Write-Host ''
Write-Host '  Tunel detenido.'
Read-Host 'Enter para cerrar'
