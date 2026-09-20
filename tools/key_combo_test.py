# -*- coding: utf-8 -*-
"""通用组合键注入测试器：验证「模拟按键」能否触发微信输入法的语音输入。

用法:
  python key_combo_test.py "ctrl+win" [等待秒数] [按住秒数]

判定方式（三重）：
  1. 监视新出现的顶层窗口，并解析其进程名（找 wetype_* 的录音悬浮窗）
  2. 监视 HKCU 麦克风占用记录，看 wetype 相关进程是否开始使用麦克风
  3. 打印记事本等前台窗口标题变化
"""
import ctypes
import sys
import time
import winreg
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

EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
                0x5B, 0x5C, 0x6F, 0xA3, 0xA5, 0x0D, 0x2F}

NAME2VK = {
    "ctrl": 0xA2, "control": 0xA2, "lctrl": 0xA2, "rctrl": 0xA3,
    "alt": 0xA4, "lalt": 0xA4, "ralt": 0xA5,
    "shift": 0xA0, "lshift": 0xA0, "rshift": 0xA1,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "meta": 0x5B,
    "space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
}

user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = w.UINT
user32.GetForegroundWindow.restype = w.HWND
user32.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.restype = w.DWORD
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user32.IsWindowVisible.restype = w.BOOL
user32.IsWindowVisible.argtypes = [w.HWND]
user32.VkKeyScanW.restype = ctypes.c_short
user32.VkKeyScanW.argtypes = [ctypes.c_wchar]

WNDENUMPROC = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
user32.EnumWindows.restype = w.BOOL
user32.EnumWindows.argtypes = [WNDENUMPROC, w.LPARAM]

kernel32.OpenProcess.restype = w.HANDLE
kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel32.QueryFullProcessImageNameW.restype = w.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR,
                                                ctypes.POINTER(w.DWORD)]
kernel32.CloseHandle.argtypes = [w.HANDLE]

MIC_KEY = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
           r"\ConsentStore\microphone\NonPackaged")


def parse_combo(spec):
    keys = []
    for tok in spec.lower().replace(" ", "").split("+"):
        if not tok:
            continue
        if tok in NAME2VK:
            keys.append(NAME2VK[tok])
        elif len(tok) == 1:
            vk = user32.VkKeyScanW(tok) & 0xFF
            if vk in (0xFF, 0):
                raise SystemExit("无法解析按键: %r" % tok)
            keys.append(vk)
        else:
            raise SystemExit("未知按键名: %r" % tok)
    return keys


def key_event(vk, up):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = 0
    flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0
    if up:
        flags |= KEYEVENTF_KEYUP
    inp.u.ki.dwFlags = flags
    inp.u.ki.time = 0
    inp.u.ki.dwExtraInfo = 0
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 1


def press_combo(keys):
    return [key_event(vk, False) for vk in keys]


def release_combo(keys):
    return [key_event(vk, True) for vk in reversed(keys)]


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
        if user32.IsWindowVisible(hwnd):
            pid = w.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out[hwnd] = (win_text(hwnd, "class"), win_text(hwnd, "title"),
                         pid.value)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


_pname_cache = {}


def proc_name(pid):
    if pid in _pname_cache:
        return _pname_cache[pid]
    h = kernel32.OpenProcess(0x1000, False, pid)
    name = "?"
    if h:
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = w.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf,
                                                   ctypes.byref(size)):
                name = buf.value.rsplit("\\", 1)[-1]
        finally:
            kernel32.CloseHandle(h)
    _pname_cache[pid] = name
    return name


def mic_users():
    """返回 {进程路径: (LastUsedTimeStart, LastUsedTimeStop)} 含 wetype 的条目"""
    out = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, MIC_KEY)
    except OSError:
        return out
    i = 0
    while True:
        try:
            name = winreg.EnumKey(root, i)
        except OSError:
            break
        i += 1
        if "wetype" not in name.lower():
            continue
        try:
            k = winreg.OpenKey(root, name)
            s, _ = winreg.QueryValueEx(k, "LastUsedTimeStart")
            e, _ = winreg.QueryValueEx(k, "LastUsedTimeStop")
            out[name] = (s, e)
        except OSError:
            out[name] = (0, 0)
    return out


def fmt_time(v):
    if not v:
        return "-"
    try:
        return time.strftime("%H:%M:%S", time.localtime(v / 10_000_000
                                                        - 11644473600))
    except Exception:
        return str(v)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    spec = sys.argv[1]
    wait_s = int(sys.argv[2]) if len(sys.argv) > 2 else 240
    hold_s = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
    keys = parse_combo(spec)
    print("组合键 %r -> VK %s" % (spec, ["0x%02X" % k for k in keys]))
    print("等待你把「记事本」切到前台并点进去……")
    sys.stdout.flush()

    deadline = time.time() + wait_s
    while time.time() < deadline:
        cls, title, pid = foreground_info()
        name = proc_name(pid) if pid else ""
        if "notepad" in cls.lower() or "记事本" in title \
                or "notepad" in name.lower():
            print("前台已是记事本: %s" % title)
            break
        time.sleep(0.5)
    else:
        print("超时未检测到记事本，仍尝试一次")

    base_mic = mic_users()
    print("初始 wetype 麦克风记录: %s" % (
        {k: (fmt_time(v[0]), fmt_time(v[1])) for k, v in base_mic.items()} or "无"))
    before = dict(list_windows())

    for i in range(1, 4):
        cls, title, _ = foreground_info()
        print("\n[%s] 第 %d 次：按住 %s 共 %.1f 秒（前台 %s / %r）"
              % (time.strftime("%H:%M:%S"), i, spec, hold_s, cls, title[:30]))
        sys.stdout.flush()
        downs = press_combo(keys)
        time.sleep(hold_s)
        ups = release_combo(keys)
        print("    SendInput 按下=%s 松开=%s" % (all(downs), all(ups)))

        new_win = [(h, v) for h, v in list_windows().items() if h not in before]
        if new_win:
            for h, (c, t, p) in new_win:
                print("    ★ 新窗口 class=%s title=%r pid=%d (%s)"
                      % (c, t, p, proc_name(p)))
        else:
            print("    （无新顶层窗口）")
        before = dict(list_windows())

        cur = mic_users()
        changed = {k: v for k, v in cur.items() if base_mic.get(k) != v}
        newmic = {k: v for k, v in cur.items() if k not in base_mic}
        if newmic:
            print("    ★★ 新出现麦克风使用者: %s" % {
                k: (fmt_time(v[0]), fmt_time(v[1])) for k, v in newmic.items()})
        if changed:
            print("    ★★ 麦克风记录变化: %s" % {
                k: (fmt_time(v[0]), fmt_time(v[1])) for k, v in changed.items()})
        if not newmic and not changed:
            print("    （麦克风记录无变化）")
        sys.stdout.flush()
        if i < 3:
            time.sleep(6.0)
    print("\n测试结束。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
