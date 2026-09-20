@echo off
cd /d "%~dp0.."
echo === 调试模式：会显示实时日志，关闭窗口或按 Ctrl+C 即停止 ===
echo.
python "src\g2_voice_bridge.py"
echo.
echo 程序已退出。
pause
