@echo off
rem Inicia Universal AI Live Clipper y abre el navegador cuando el servidor responde.
title Live Clipper
cd /d "%~dp0..\backend"
if not exist ".venv\Scripts\clipper.exe" (
  echo No se encontro la instalacion. Ejecuta primero Instalar-LiveClipper.bat
  pause
  exit /b 1
)
echo.
echo   Live Clipper se esta iniciando. El navegador se abrira en http://localhost:8000
echo   Para apagarlo, cierra esta ventana.
echo.
start "" powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 180;$i++){try{$null=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8000/api/system/status'; Start-Process 'http://localhost:8000'; break}catch{Start-Sleep -Seconds 1}}"
".venv\Scripts\clipper.exe" serve
echo.
echo   Live Clipper se ha detenido.
pause
