@echo off
rem Windows: stop the platform, including Ollama in a container. Data and models stay.
cd /d "%~dp0"
docker compose --profile ollama --profile ollama-gpu --profile integration down
pause
