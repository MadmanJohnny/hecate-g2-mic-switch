# -*- coding: utf-8 -*-
"""权限诊断：当前前台程序是不是「以管理员身份运行」，本工具能不能把按键送进去。

背景——Windows 的 UIPI（用户界面特权隔离）规定：低完整性进程用 SendInput 注入的
合成按键**不能投递给高完整性进程的窗口**，会被系统静默丢弃（SendInput 照样返回
成功）。症状就是"手动按快捷键有效、拨开关没反应"，而且只在以管理员身份运行的
程序里出现。

用法：
    python tools/privilege_test.py            报告一次当前状态
    python tools/privilege_test.py --watch    每秒刷新一次（边拨开关边看）
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes as w

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
import bridge_core as core  # noqa: E402

user32 = core.user32
user32.GetWindowTextW.argtypes = [w.HANDLE, w.LPWSTR, ctypes.c_int]
user32.GetForegroundWindow.restype = w.HANDLE
user32.GetWindowThreadProcessId.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]


def window_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def report():
    me = core.current_is_elevated()
    hwnd = user32.GetForegroundWindow()
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = core.process_exe_name(pid.value) if pid.value else "?"
    elev = core.process_is_elevated(pid.value) if pid.value else None
    title = window_title(hwnd)
    blocked, _ = core.uipi_blocks_injection()

    print("本工具权限   : %s" % ("管理员 ✓" if me else "普通"))
    print("前台窗口     : %s" % (title[:60] or "(无标题)"))
    print("前台进程     : %s (PID %d)" % (name, pid.value))
    if elev is True:
        print("前台进程权限 : 管理员")
    elif elev is False:
        print("前台进程权限 : 普通")
    else:
        print("前台进程权限 : 查不到令牌 —— 说明它比本工具权限高（同级是查得到的）")
    print("能否注入按键 : %s" % ("✗ 会被 UIPI 丢弃，拨开关不会有效果"
                                if blocked else "✓ 可以"))
    if blocked:
        print()
        print("解决办法（二选一）：")
        print("  1. 让本工具也以管理员身份运行：界面「设置 → 权限 → 以管理员身份重启」")
        print("     （提权是超集，之后普通窗口和提权窗口都能控制）")
        print("  2. 别用管理员身份运行那个程序")
    return blocked


if __name__ == "__main__":
    if "--watch" in sys.argv:
        print("每秒刷新一次；请把目标程序切到前台，然后拨一下耳机的麦克风开关。")
        print("（Ctrl+C 退出）\n")
        last = None
        try:
            while True:
                cur = core.foreground_process()
                if cur != last:
                    last = cur
                    print("---- %s ----" % time.strftime("%H:%M:%S"))
                    report()
                    print()
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
    else:
        report()
