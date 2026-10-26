@echo off
rem Windows: stop the platform, including Ollama in a container and the Ollama
rem that start.bat launched (marker .ollama-started). Data and models stay.
cd /d "%~dp0"
docker compose --profile ollama --profile ollama-gpu --profile integration down
if exist .ollama-started (
    powershell -NoProfile -Command "Stop-Process -Name 'ollama app','ollama' -ErrorAction SilentlyContinue"
    del .ollama-started
    echo Ollama stopped.
)
pause
