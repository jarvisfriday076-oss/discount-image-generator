@echo off
REM Double-click this file to start the Flask app + Cloudflare tunnel in two windows.
REM Close either window to stop sharing.

cd /d "%~dp0"

start "Bottela Flask" cmd /k ".venv\Scripts\activate && python app.py"

REM Wait 3s so Flask is up before the tunnel connects
timeout /t 3 /nobreak >nul

start "Cloudflare Tunnel" cmd /k "\"C:\Program Files (x86)\cloudflared\cloudflared.exe\" tunnel --url http://localhost:5000"

exit
