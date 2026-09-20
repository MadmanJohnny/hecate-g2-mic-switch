@echo off
cd /d "%~dp0.."
set PY=pythonw.exe
if exist ".venv\Scripts\pythonw.exe" set PY=.venv\Scripts\pythonw.exe
if exist "C:\Python314\pythonw.exe" if /i "%PY%"=="pythonw.exe" set PY=C:\Python314\pythonw.exe
start "" "%PY%" "src\g2_voice_bridge.py"
exit /b
