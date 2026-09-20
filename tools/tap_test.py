# -*- coding: utf-8 -*-
"""自动测试：微信输入法的语音输入是「必须一直按住」还是「点一下切换」。

流程（全自动，会临时弹出记事本）：
  1. 启动/定位记事本并强制置前台
  2. 极短地"点"一下 Ctrl+Win（80ms）
  3. 之后 8 秒内持续观察麦克风占用记录与 wetype 窗口
     - 若录音持续存在 → 说明是"点一下切换"，可以不用一直按住键
     - 若立刻停止       → 说明必须一直按住
  4. 收尾：再点一下停止录音，关闭临时记事本
"""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes as w

import key_combo_test as kct

user32 = kct.user32
kernel32 = kct.kernel32

user32.AllowSetForegroundWindow.argtypes = [w.DWORD]
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.SetForegroundWindow.restype = w.BOOL
user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]


def find_notepad():
    for hwnd, (cls, title, pid) in kct.list_windows().items():
        if cls == "Notepad" or "notepad" in kct.proc_name(pid).lower():
            return hwnd, pid, title
    return None, None, None


def focus(hwnd):
    try:
        user32.AllowSetForegroundWindow(0xFFFFFFFF)   # ASFW_ANY
    except Exception:
        pass
    user32.ShowWindow(hwnd, 9)                        # SW_RESTORE
    ok = user32.SetForegroundWindow(hwnd)
    time.sleep(0.6)
    cls, title, pid = kct.foreground_info()
    print("   置前台结果=%s 当前前台: class=%s title=%r (%s)"
          % (ok, cls, title[:30], kct.proc_name(pid) if pid else "?"))
    return "notepad" in cls.lower() or "notepad" in kct.proc_name(pid).lower()


def mic_snapshot():
    return {k: (v[0], v[1]) for k, v in kct.mic_users().items()}


def show_mic(tag):
    snap = mic_snapshot()
    for k, v in snap.items():
        print("   %s 麦克风记录 %s: Start=%s Stop=%s"
              % (tag, k.split("#")[-1], kct.fmt_time(v[0]), kct.fmt_time(v[1])))
    return snap


def main():
    combo = sys.argv[1] if len(sys.argv) > 1 else "ctrl+win"
    tap_ms = float(sys.argv[2]) if len(sys.argv) > 2 else 80
    keys = kct.parse_combo(combo)
    print("组合键 %r -> VK %s" % (combo, ["0x%02X" % k for k in keys]))

    hwnd, pid, title = find_notepad()
    started = None
    if not hwnd:
        print("没有找到记事本，临时启动一个……")
        p = subprocess.Popen(["notepad.exe"])
        started = p
        for _ in range(20):
            time.sleep(0.5)
            hwnd, pid, title = find_notepad()
            if hwnd:
                break
    if not hwnd:
        print("无法获得记事本窗口，测试中止")
        return 1
    print("记事本窗口 hwnd=%s pid=%s title=%r" % (hwnd, pid, title))
    focus(hwnd)

    print("\n基线:")
    base = show_mic("基线")
    base_win = set(kct.list_windows().keys())

    print("\n>>> 单击一下 %s（%d ms）" % (combo, tap_ms))
    kct.press_combo(keys)
    time.sleep(tap_ms / 1000.0)
    kct.release_combo(keys)

    print("\n观察 8 秒：")
    stop_seen_at = None
    for i in range(16):
        time.sleep(0.5)
        cur = mic_snapshot()
        for k, v in cur.items():
            old = base.get(k)
            if old != v:
                print("   [%4.1fs] %s 变化: Start=%s Stop=%s"
                      % ((i + 1) * 0.5, k.split("#")[-1],
                         kct.fmt_time(v[0]), kct.fmt_time(v[1])))
                base[k] = v
                if v[1] and v[1] > v[0]:
                    stop_seen_at = (i + 1) * 0.5
        new_win = set(kct.list_windows().keys()) - base_win
        if new_win:
            for h in new_win:
                cls, t, p = kct.list_windows()[h]
                print("   [%4.1fs] ★ wetype/其他新窗口 class=%s title=%r pid=%s (%s)"
                      % ((i + 1) * 0.5, cls, t, p, kct.proc_name(p)))
            base_win = set(kct.list_windows().keys())

    print("\n结论参考：")
    if stop_seen_at is None:
        print("  8 秒内没有看到录音停止 → 单击似乎能让录音【持续】，即支持切换式。")
        print("  收尾：再单击一次停止录音。")
        kct.press_combo(keys)
        time.sleep(tap_ms / 1000.0)
        kct.release_combo(keys)
        time.sleep(1.0)
    else:
        print("  约 %.1f 秒后录音就停止了 → 是【必须按住】的长按模式。" % stop_seen_at)

    if started:
        print("\n关闭临时记事本 (pid=%s)" % started.pid)
        subprocess.run(["taskkill", "/PID", str(started.pid), "/F"],
                       capture_output=True)
    print("测试结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
