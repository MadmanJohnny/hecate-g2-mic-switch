# -*- coding: utf-8 -*-
"""应急：强制松开桥接程序按住的所有按键。

桥接程序如果被"结束进程"式强杀（任务管理器 / 断电），正在按住的 Ctrl+Win
会残留在按下状态，表现为键盘像是被 Win 键卡住。
双击「紧急松开按键.cmd」或运行本脚本即可清除。
（另一个等效办法：手动按一下左 Ctrl 和左 Win 键，各按一次。）
"""
import sys

import g2_voice_bridge as gb

cfg = gb.load_config()
hk = gb.Hotkey(cfg["hotkey"])
ok = hk.release()
print("已强制松开快捷键: %s  ->  %s" % (cfg["hotkey"], "成功" if ok else "失败"))
sys.exit(0 if ok else 1)
