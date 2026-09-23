@echo off
cd /d "%~dp0"
echo === 打包 HECATE G2 语音开关 ===
if not exist ".venv\Scripts\python.exe" (
  echo 首次运行：创建虚拟环境...
  python -m venv .venv || goto :err
  echo 安装 PyInstaller...
  .venv\Scripts\python.exe -m pip install pyinstaller || goto :err
)
set ICON=
if exist "%~dp0assets\app.ico" set ICON=--icon "%~dp0assets\app.ico"
.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed --noupx ^
  --paths src --name "HECATE-G2-VoiceSwitch" %ICON% ^
  --distpath dist --workpath build --specpath build ^
  src\bridge_gui.py || goto :err
echo.
echo 打包完成： %~dp0dist\HECATE-G2-VoiceSwitch.exe
pause
exit /b 0
:err
echo.
echo 打包失败，请检查上面的输出。
pause
exit /b 1