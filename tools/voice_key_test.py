# -*- coding: utf-8 -*-
"""验证：模拟按住「右 Alt」能否触发微信输入法的语音输入。

工作方式：
  1. 后台等待，直到前台窗口像是记事本（标题含 记事本 / Notepad）
  2. 连续 3 次「按住右 Alt 4 秒 → 松开 → 等 6 秒」
  3. 全程监视顶层窗口变化，若微信输入法的录音窗口/进程出现会被打印出来

用法: python voice_key_test.py [等待秒数]
"""
import ctypes
import sys
import time
from ctypes import wintypes as w

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                ("dwFlags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", w.DWORD), ("u", INPUTUNION)]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
VK_RMENU = 0xA5          # 右 Alt
VK_LMENU = 0xA4

user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = w.UINT
user32.GetForegroundWindow.restype = w.HWND
user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]

WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


def send_key(vk, up, extended=True):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = 0
    flags = KEYEVENTF_EXTENDEDKEY if extended else 0
    if up:
        flags |= KEYEVENTF_KEYUP
    inp.u.ki.dwFlags = flags
    inp.u.ki.time = 0
    inp.u.ki.dwExtraInfo = 0
    n = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    return n == 1


def win_text(hwnd, attr):
    buf = ctypes.create_unicode_buffer(512)
    if attr == "class":
        user32.GetClassNameW(hwnd, buf, 512)
    else:
        user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def foreground_info():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", "", 0
    pid = w.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return win_text(hwnd, "class"), win_text(hwnd, "title"), pid.value


def list_windows():
    out = {}

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = w.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out[hwnd] = (win_text(hwnd, "class"), win_text(hwnd, "title"), pid.value)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def proc_name(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return "?"
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = w.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return "?"
    finally:
        kernel32.CloseHandle(h)


kernel32.OpenProcess.restype = w.HANDLE
kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel32.QueryFullProcessImageNameW.restype = w.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR,
                                                ctypes.POINTER(w.DWORD)]
kernel32.CloseHandle.argtypes = [w.HANDLE]

user32.GetWindowThreadProcessId.restype = w.DWORD
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user32.IsWindowVisible.restype = w.BOOL
user32.IsWindowVisible.argtypes = [w.HWND]
user32.EnumWindows.restype = w.BOOL
user32.EnumWindows.argtypes = [WNDENUMPROC, w.LPARAM]


def main():
    wait_s = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    print("等待你把「记事本」切到前台并点进去……（最多等 %d 秒）" % wait_s)
    print("准备就绪后会自动执行 3 次「按住右 Alt 4 秒」测试。")
    sys.stdout.flush()

    base = dict(list_windows())
    deadline = time.time() + wait_s
    ready = False
    while time.time() < deadline:
        cls, title, pid = foreground_info()
        name = proc_name(pid) if pid else ""
        if "notepad" in cls.lower() or "记事本" in title or "notepad" in name.lower():
            print("检测到前台是记事本: class=%s title=%r pid=%d (%s)"
                  % (cls, title, pid, name))
            ready = True
            break
        time.sleep(0.5)

    if not ready:
        print("超时，没有等到记事本。仍会执行一次测试……")
        time.sleep(1)

    before = dict(list_windows())
    for i in range(1, 4):
        cls, title, _ = foreground_info()
        print("\n[%s] 第 %d 次：按住右 Alt（当前前台 class=%s title=%r）"
              % (time.strftime("%H:%M:%S"), i, cls, title[:40]))
        sys.stdout.flush()
        ok1 = send_key(VK_RMENU, False)
        time.sleep(4.0)
        ok2 = send_key(VK_RMENU, True)
        print("    SendInput 按下=%s 松开=%s" % (ok1, ok2))
        cur = dict(list_windows())
        new = [(h, v) for h, v in cur.items() if h not in before]
        if new:
            for h, (c, t, p) in new:
                print("    ★ 新窗口 hwnd=%s class=%s title=%r pid=%d (%s)"
                      % (h, c, t, p, proc_name(p)))
        else:
            print("    （没有新顶层窗口出现）")
        before = cur
        sys.stdout.flush()
        if i < 3:
            time.sleep(6.0)
    print("\n测试结束。请告诉我：记事本里有没有出现微信输入法的语音输入/录音界面？")
    return 0


if __name__ == "__main__":
    sys.exit(main())
