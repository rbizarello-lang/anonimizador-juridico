@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" app.py --navegador
if errorlevel 1 pause
