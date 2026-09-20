@echo off
cd /d "%~dp0.."
echo === 强制松开桥接程序可能卡住的按键 ===
python "src\release_keys.py"
echo.
echo 如果执行后键盘还是像被 Win 键卡住，手动按一下左 Ctrl 和左 Win 键各一次即可。
pause
