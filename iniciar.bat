@echo off
cd /d "%~dp0"
REM Janela sem o console preto: use o atalho da area de trabalho ou iniciar_app.vbs.
".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
