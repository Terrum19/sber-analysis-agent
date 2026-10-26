@echo off
rem Windows: double click starts the platform (logic in scripts\start\start.ps1, #167).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start\start.ps1"
