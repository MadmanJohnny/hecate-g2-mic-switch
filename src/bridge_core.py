# -*- coding: utf-8 -*-
"""HECATE G2 麦克风开关 → 微信输入法语音输入：核心逻辑

被 bridge_gui.py（图形界面）和 g2_voice_bridge.py（命令行）共用。

设计要点
--------
* HID 报告只当"触发器"：每次拨动都产生同样的一帧 00 08 00 00 00，
  两个方向没有区别，所以**不能**用它判断开关位置。
* 开关位置靠**实测麦克风电平**判断：
      静音      → 耳机在硬件上切断话筒 → 峰值 ≈ 1
      麦克风开  → 有稳定底噪         → 峰值 ≥ 200
  因而拔插、漏事件、重复事件都不会造成"开/关反了"。
"""
import ctypes
import json
import os
import sys
import threading
import time
import winreg
from ctypes import wintypes as w

APP_NAME = "HECATE G2 语音输入桥接"
HID_GUID = "{4d1e55b2-f16f-11cf-88cb-001111000030}"
DEFAULT_VID_PID = "VID_2D99&PID_0026"
ENV_DATA_DIR = "G2VB_DATA_DIR"

DEFAULT_CONFIG = {
    "vid_pid": DEFAULT_VID_PID,
    "hid_path": "",                  # 指定具体 HID 接口（留空=按 vid_pid 自动找）
    "capture_device": "",            # 录音设备名关键字（留空=系统默认设备）
    "hotkey": "ctrl+win",
    "debounce_ms": 400,
    "mute_peak_threshold": 8,
    "probe_ms": 150,
    "settle_ms": 60,
    "start_dictating": False,
    "log_to_file": True,
    "autostart_bridge": False,       # 开机自动开始桥接
    "close_to_tray": True,           # 点关闭按钮时收进系统托盘而不是退出
}

# ---------------------------------------------------------------- 基础常量
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID = ctypes.c_void_p(-1).value

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001

TRIGGER_BYTE = 1
TRIGGER_MASK = 0x08

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
hid_dll = ctypes.WinDLL("hid.dll", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def source_root():
    """源码运行时的"项目根目录"：src 的上一级（如果看起来像仓库根），否则就是本目录。"""
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    if os.path.basename(here).lower() == "src" and os.path.isfile(
            os.path.join(parent, "README.md")):
        return parent
    return here


def data_dir():
    """配置/日志目录。可用环境变量 G2VB_DATA_DIR 覆盖（打包运行时用得到）。"""
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        path = env
    elif is_frozen():
        path = os.path.join(os.environ.get("APPDATA",
                                           os.path.expanduser("~")),
                            "HecateG2VoiceBridge")
    else:
        path = source_root()
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def config_path():
    return os.path.join(data_dir(), "g2_bridge_config.json")


def log_path():
    return os.path.join(data_dir(), "g2_bridge.log")


# ---------------------------------------------------------------- 配置
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    p = config_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                cfg.update(json.load(fh))
        except Exception:
            pass
    else:
        save_config(cfg)
    return cfg


def save_config(cfg):
    p = config_path()
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 键盘注入
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


user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = w.UINT
user32.VkKeyScanW.restype = ctypes.c_short
user32.VkKeyScanW.argtypes = [ctypes.c_wchar]

EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
                0x5B, 0x5C, 0x6F, 0xA3, 0xA5, 0x0D, 0x2F}
NAME2VK = {
    "ctrl": 0xA2, "control": 0xA2, "lctrl": 0xA2, "rctrl": 0xA3,
    "alt": 0xA4, "lalt": 0xA4, "ralt": 0xA5,
    "shift": 0xA0, "lshift": 0xA0, "rshift": 0xA1,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "meta": 0x5B, "super": 0x5B,
    "space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


def parse_hotkey(spec):
    keys = []
    for tok in spec.lower().replace(" ", "").split("+"):
        if not tok:
            continue
        if tok in NAME2VK:
            keys.append(NAME2VK[tok])
        elif len(tok) == 1:
            vk = user32.VkKeyScanW(tok) & 0xFF
            if vk in (0, 0xFF):
                raise ValueError("无法解析按键: %r" % tok)
            keys.append(vk)
        else:
            raise ValueError("未知按键名: %r" % tok)
    if not keys:
        raise ValueError("快捷键不能为空")
    return keys


def _key_event(vk, up):
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


class Hotkey:
    def __init__(self, spec):
        self.spec = spec
        self.keys = parse_hotkey(spec)
        self.down = False

    def press(self):
        if self.down:
            return True
        ok = all(_key_event(vk, False) for vk in self.keys)
        self.down = True
        return ok

    def release(self):
        ok = all(_key_event(vk, True) for vk in reversed(self.keys))
        self.down = False
        return ok


# ---------------------------------------------------------------- 录音设备
class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", w.WORD), ("nChannels", w.WORD),
                ("nSamplesPerSec", w.DWORD), ("nAvgBytesPerSec", w.DWORD),
                ("nBlockAlign", w.WORD), ("wBitsPerSample", w.WORD),
                ("cbSize", w.WORD)]


class WAVEHDR(ctypes.Structure):
    pass


WAVEHDR._fields_ = [
    ("lpData", ctypes.POINTER(ctypes.c_char)),
    ("dwBufferLength", w.DWORD),
    ("dwBytesRecorded", w.DWORD),
    ("dwUser", ctypes.c_void_p),
    ("dwFlags", w.DWORD),
    ("dwLoops", w.DWORD),
    ("lpNext", ctypes.POINTER(WAVEHDR)),
    ("reserved", ctypes.c_void_p),
]


class WAVEINCAPS(ctypes.Structure):
    _fields_ = [("wMid", w.WORD), ("wPid", w.WORD), ("vDriverVersion", w.UINT),
                ("szPname", w.WCHAR * 32), ("dwFormats", w.DWORD),
                ("wChannels", w.WORD), ("wReserved1", w.WORD)]


for _fn, _args in (
        ("waveInOpen", [ctypes.POINTER(w.HANDLE), w.UINT,
                        ctypes.POINTER(WAVEFORMATEX), ctypes.c_void_p,
                        ctypes.c_void_p, w.DWORD]),
        ("waveInPrepareHeader", [w.HANDLE, ctypes.POINTER(WAVEHDR), w.UINT]),
        ("waveInAddBuffer", [w.HANDLE, ctypes.POINTER(WAVEHDR), w.UINT]),
        ("waveInStart", [w.HANDLE]),
        ("waveInStop", [w.HANDLE]),
        ("waveInReset", [w.HANDLE]),
        ("waveInUnprepareHeader", [w.HANDLE, ctypes.POINTER(WAVEHDR), w.UINT]),
        ("waveInClose", [w.HANDLE])):
    _f = getattr(winmm, _fn)
    _f.argtypes = _args
    _f.restype = w.UINT

winmm.waveInGetNumDevs.restype = w.UINT
winmm.waveInGetDevCapsW.argtypes = [w.UINT, ctypes.POINTER(WAVEINCAPS), w.UINT]
winmm.waveInGetDevCapsW.restype = w.UINT

WAVE_MAPPER = 0xFFFFFFFF


def list_capture_devices():
    """返回 [(索引, 名称)]; 索引 -1 代表系统默认设备"""
    out = [(-1, "【系统默认录音设备】")]
    n = winmm.waveInGetNumDevs()
    for i in range(n):
        caps = WAVEINCAPS()
        if winmm.waveInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            out.append((i, caps.szPname))
    return out


def resolve_capture_device(keyword):
    """按名字关键字找设备索引，找不到返回 WAVE_MAPPER"""
    if not keyword:
        return WAVE_MAPPER
    for idx, name in list_capture_devices():
        if idx >= 0 and keyword.lower() in name.lower():
            return idx
    return WAVE_MAPPER


class MicSampler:
    """打开指定录音设备，按需采集一小段并返回电平统计。"""

    def __init__(self, rate=16000, device=WAVE_MAPPER):
        self.rate = rate
        self.h = w.HANDLE()
        fmt = WAVEFORMATEX(1, 1, rate, rate * 2, 2, 16, 0)
        rc = winmm.waveInOpen(ctypes.byref(self.h), device,
                              ctypes.byref(fmt), None, None, 0)
        if rc != 0:
            raise OSError("waveInOpen 失败 rc=%d（设备被独占或不存在）" % rc)

    def sample(self, ms=150):
        n = int(self.rate * 2 * ms / 1000.0)
        buf = ctypes.create_string_buffer(n)
        hdr = WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
        hdr.dwBufferLength = n
        sz = ctypes.sizeof(hdr)
        winmm.waveInPrepareHeader(self.h, ctypes.byref(hdr), sz)
        winmm.waveInAddBuffer(self.h, ctypes.byref(hdr), sz)
        winmm.waveInStart(self.h)
        time.sleep(ms / 1000.0 + 0.06)
        winmm.waveInStop(self.h)
        winmm.waveInReset(self.h)
        got = hdr.dwBytesRecorded
        data = buf.raw[:got]
        winmm.waveInUnprepareHeader(self.h, ctypes.byref(hdr), sz)
        if len(data) < 2:
            return {"bytes": len(data), "peak": 0, "rms": 0.0, "nonzero": 0}
        samples = memoryview(data).cast("h")
        peak = 0
        total = 0
        nonzero = 0
        for s in samples:
            a = -s if s < 0 else s
            if a > peak:
                peak = a
            total += s * s
            if s:
                nonzero += 1
        return {"bytes": len(data), "peak": peak,
                "rms": (total / len(samples)) ** 0.5, "nonzero": nonzero,
                "n": len(samples)}

    def close(self):
        try:
            winmm.waveInClose(self.h)
        except Exception:
            pass


class MicProbe:
    """采一小段音频来判断耳机开关在"静音"还是"麦克风开"。"""

    def __init__(self, ms=150, threshold=8, device_keyword=""):
        self.ms = int(ms)
        self.threshold = int(threshold)
        self.device = resolve_capture_device(device_keyword)

    def read(self, tries=3):
        last = None
        for _ in range(tries):
            sampler = None
            try:
                sampler = MicSampler(device=self.device)
                st = sampler.sample(self.ms)
                if st["bytes"] < self.ms * 32 * 0.4:
                    last = "采集数据过少(%d 字节)" % st["bytes"]
                    time.sleep(0.15)
                    continue
                return st
            except Exception as exc:
                last = exc
                time.sleep(0.2)
            finally:
                if sampler is not None:
                    sampler.close()
        raise OSError("麦克风采集失败: %s" % last)

    def is_muted(self, st):
        return st["peak"] <= self.threshold


# ---------------------------------------------------------------- HID 设备
kernel32.CreateFileW.restype = w.HANDLE
kernel32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                 w.DWORD, w.DWORD, w.HANDLE]
kernel32.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD,
                              ctypes.POINTER(w.DWORD), ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [w.HANDLE]
kernel32.CancelIoEx.argtypes = [w.HANDLE, ctypes.c_void_p]
kernel32.CreateMutexW.restype = w.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]

MUTEX_NAME = "Local\\G2VoiceBridge_HECATEG2"
ERROR_ALREADY_EXISTS = 183


def acquire_single_instance(name=MUTEX_NAME):
    ctypes.set_last_error(0)
    h = kernel32.CreateMutexW(None, True, name)
    if not h:
        return None
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(h)
        return "exists"
    return h


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", w.ULONG), ("VendorID", w.USHORT),
                ("ProductID", w.USHORT), ("VersionNumber", w.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", w.USHORT), ("UsagePage", w.USHORT),
        ("InputReportByteLength", w.USHORT),
        ("OutputReportByteLength", w.USHORT),
        ("FeatureReportByteLength", w.USHORT),
        ("Reserved", w.USHORT * 17),
        ("NumberLinkCollectionNodes", w.USHORT),
        ("NumberInputButtonCaps", w.USHORT), ("NumberInputValueCaps", w.USHORT),
        ("NumberInputDataIndices", w.USHORT),
        ("NumberOutputButtonCaps", w.USHORT), ("NumberOutputValueCaps", w.USHORT),
        ("NumberOutputDataIndices", w.USHORT),
        ("NumberFeatureButtonCaps", w.USHORT), ("NumberFeatureValueCaps", w.USHORT),
        ("NumberFeatureDataIndices", w.USHORT),
    ]


hid_dll.HidD_GetPreparsedData.argtypes = [w.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid_dll.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid_dll.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid_dll.HidD_GetAttributes.argtypes = [w.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid_dll.HidD_GetProductString.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]


def enum_hid_paths(vid_filter=None):
    """枚举注册表里的 HID 设备，返回 [(实例ID, 接口路径)]"""
    out = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                              r"SYSTEM\CurrentControlSet\Enum\HID")
    except OSError:
        return out
    i = 0
    while True:
        try:
            name = winreg.EnumKey(root, i)
        except OSError:
            break
        i += 1
        if vid_filter and vid_filter.lower() not in name.lower():
            continue
        try:
            key = winreg.OpenKey(root, name)
        except OSError:
            continue
        j = 0
        while True:
            try:
                inst = winreg.EnumKey(key, j)
            except OSError:
                break
            j += 1
            path = "\\\\?\\HID#" + name.replace("\\", "#") + "#" + inst \
                   + "#" + HID_GUID
            out.append((name + "\\" + inst, path))
    return out


def open_hid_path(path):
    ctypes.set_last_error(0)
    h = kernel32.CreateFileW(path, GENERIC_READ,
                             FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                             OPEN_EXISTING, 0, None)
    hv = h.value if hasattr(h, "value") else h
    if hv and hv != INVALID:
        return h
    return None


def open_device(vid_filter=None, prefer_path=None):
    if prefer_path:
        h = open_hid_path(prefer_path)
        if h:
            return h, prefer_path
    for inst_id, path in enum_hid_paths(vid_filter):
        h = open_hid_path(path)
        if h:
            return h, inst_id
    return None, None


def hid_device_info(handle):
    """读取设备的 VID/PID、顶层用法、产品名"""
    info = {"vid": 0, "pid": 0, "usage_page": 0, "usage": 0, "product": ""}
    attrs = HIDD_ATTRIBUTES()
    attrs.Size = ctypes.sizeof(attrs)
    if hid_dll.HidD_GetAttributes(handle, ctypes.byref(attrs)):
        info["vid"], info["pid"] = attrs.VendorID, attrs.ProductID
    buf = ctypes.create_unicode_buffer(128)
    if hid_dll.HidD_GetProductString(handle, buf, ctypes.sizeof(buf)):
        info["product"] = buf.value
    prep = ctypes.c_void_p()
    if hid_dll.HidD_GetPreparsedData(handle, ctypes.byref(prep)):
        caps = HIDP_CAPS()
        if hid_dll.HidP_GetCaps(prep, ctypes.byref(caps)) == 0x00110000:
            info["usage_page"] = caps.UsagePage
            info["usage"] = caps.Usage
            info["input_len"] = caps.InputReportByteLength
        hid_dll.HidD_FreePreparsedData(prep)
    return info


def list_hid_candidates(only_consumer=True):
    """列出可能承载线控按键的 HID 接口（默认只看消费类 0x0C）。"""
    out = []
    seen = set()
    for inst_id, path in enum_hid_paths(None):
        if path in seen:
            continue
        seen.add(path)
        h = open_hid_path(path)
        if not h:
            continue
        try:
            info = hid_device_info(h)
        finally:
            kernel32.CloseHandle(h)
        if only_consumer and info.get("usage_page") != 0x0C:
            continue
        info["instance"] = inst_id
        info["path"] = path
        out.append(info)
    return out


# ---------------------------------------------------------------- 桥接核心
# ------------------------------------------------------- 权限（UIPI）与提权
# Windows 的"用户界面特权隔离"（UIPI）规定：**低完整性进程的合成输入不能投递给
# 高完整性进程的窗口**。也就是说，如果目标程序是以管理员身份运行的，而本工具是
# 普通权限，那么 SendInput 注入的 ctrl+win 会被系统**静默丢弃**——SendInput 照样
# 返回 1、GetLastError 也是 0，但按键根本到不了目标程序（连输入法的全局钩子都
# 看不到），于是"手动按快捷键有效、拨开关没反应"。
#
# 实测（记事本=普通权限 / Trae、WorkBuddy=管理员权限，同一段注入代码）：
#     记事本   → 语音输入启动 ✓
#     Trae     → 未启动 ✗
#     WorkBuddy→ 未启动 ✗
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
advapi32.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
advapi32.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         w.DWORD, ctypes.POINTER(w.DWORD)]
kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel32.OpenProcess.restype = w.HANDLE
kernel32.GetCurrentProcess.restype = w.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR,
                                                ctypes.POINTER(w.DWORD)]
user32.GetForegroundWindow.restype = w.HANDLE
user32.GetWindowThreadProcessId.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
user32.GetWindowTextW.argtypes = [w.HANDLE, w.LPWSTR, ctypes.c_int]


def process_is_elevated(pid=None):
    """进程是否以管理员身份运行。pid 为 None 表示本进程。

    返回 True / False / **None**。None 表示"查不到令牌"——对权限比自己高的进程，
    OpenProcessToken 会直接 ACCESS_DENIED，所以 **None 本身就意味着对方完整性级别
    比我们高**（同级进程是查得到的）。判定 UIPI 时要把 None 当作"比我高"。
    """
    if pid is None:
        handle = kernel32.GetCurrentProcess()
        own = True
    else:
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        own = False
        if not handle:
            return None
    try:
        token = w.HANDLE()
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            val = w.DWORD()
            size = w.DWORD()
            ok = advapi32.GetTokenInformation(token, TOKEN_ELEVATION,
                                              ctypes.byref(val), 4,
                                              ctypes.byref(size))
            return bool(val.value) if ok else None
        finally:
            kernel32.CloseHandle(token)
    finally:
        if not own:
            kernel32.CloseHandle(handle)


def current_is_elevated():
    return bool(process_is_elevated(None))


def process_exe_name(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return "?"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = w.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return "?"
    except Exception:
        return "?"
    finally:
        kernel32.CloseHandle(handle)


def foreground_process():
    """返回前台窗口所属进程的 (pid, exe 名, 是否提权)"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, "", None
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value or pid.value == os.getpid():
        return pid.value, "", None
    return pid.value, process_exe_name(pid.value), process_is_elevated(pid.value)


def uipi_blocks_injection():
    """当前这一下按键会不会被 UIPI 拦掉。

    返回 (是否会被拦, 目标程序名)。

    规则：自己已经是管理员 → 往普通窗口和提权窗口都能注入，不会被拦；
    自己是普通权限时，只要目标窗口属于更高完整性级别（提权= True，
    或者令牌**查不到** = None）就会被系统静默丢弃。
    """
    pid, name, elevated = foreground_process()
    if not pid or pid == os.getpid():
        return False, name
    if current_is_elevated():
        return False, name
    return (elevated is None) or bool(elevated), name


def relaunch_elevated(args=None):
    """用 runas 重新启动自己（会弹一次 UAC）。返回 (是否成功, 错误信息)"""
    if is_frozen():
        exe = sys.executable
        params = " ".join(args or [])
        workdir = os.path.dirname(exe)
    else:
        exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(exe):
            exe = sys.executable
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "bridge_gui.py")
        params = '"%s"' % script
        if args:
            params += " " + " ".join(args)
        workdir = source_root()
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, workdir, 1)
        if rc > 32:
            return True, ""
        return False, "ShellExecute 返回 %d（1223 = 用户在 UAC 里点了取消）" % rc
    except Exception as exc:
        return False, repr(exc)


class Bridge:
    """把"开关拨动 → 量麦克风电平 → 按住/松开快捷键"跑在后台线程里。"""

    def __init__(self, cfg=None, on_log=None, on_event=None, on_state=None,
                 on_level=None, on_warn=None):
        self.cfg = dict(DEFAULT_CONFIG)
        if cfg:
            self.cfg.update(cfg)
        self.on_log = on_log
        self.on_event = on_event
        self.on_state = on_state
        self.on_level = on_level
        self.on_warn = on_warn
        self._uipi_warned = ""

        self._thread = None
        self._stop = threading.Event()
        self._handle = None
        self._hotkey = None
        self._probe = None
        self._lock = threading.Lock()

        self.running = False
        self.mic_muted = True
        self.voice_on = False
        self.last_peak = 0
        self.last_rms = 0.0
        self.events = 0
        self.last_error = ""
        self.device_label = ""

    # ---------- 工具 ----------
    def log(self, msg):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        if self.on_log:
            try:
                self.on_log(line)
            except Exception:
                pass
        try:
            if sys.stdout is not None:
                print(line, flush=True)
        except Exception:
            pass

    def snapshot(self):
        with self._lock:
            return {
                "running": self.running,
                "mic_muted": self.mic_muted,
                "voice_on": self.voice_on,
                "peak": self.last_peak,
                "rms": self.last_rms,
                "events": self.events,
                "error": self.last_error,
                "device": self.device_label,
            }

    def _notify_state(self):
        if self.on_state:
            try:
                self.on_state(self.snapshot())
            except Exception:
                pass

    # ---------- 生命周期 ----------
    def start(self):
        if self.running:
            return True, "已在运行"
        try:
            self._hotkey = Hotkey(self.cfg["hotkey"])
        except Exception as exc:
            return False, "快捷键无效：%s" % exc
        self._probe = MicProbe(self.cfg["probe_ms"],
                               self.cfg["mute_peak_threshold"],
                               self.cfg.get("capture_device", ""))
        self._stop.clear()
        self._hotkey.release()
        try:
            st = self._probe.read()
        except OSError as exc:
            self.last_error = str(exc)
            return False, "无法采集麦克风：%s" % exc
        with self._lock:
            self.mic_muted = self._probe.is_muted(st)
            self.last_peak = st["peak"]
            self.last_rms = st["rms"]
            self.events = 0
            self.voice_on = False
        self.log("麦克风采集正常，启动实测峰值=%d → 开关在【%s】"
                 % (st["peak"], "静音" if self.mic_muted else "麦克风开"))
        if (not self.mic_muted) and self.cfg.get("start_dictating"):
            self._hotkey.press()
            self.voice_on = True
            self.log("按配置立即开始语音输入。")
        self._thread = threading.Thread(target=self._run, name="g2-bridge",
                                        daemon=True)
        self.running = True
        self._thread.start()
        self._notify_state()
        return True, "已启动"

    def stop(self, timeout=3.0):
        if not self.running:
            return True, "未在运行"
        self._stop.set()
        if self._handle:
            try:
                kernel32.CancelIoEx(self._handle, None)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=timeout)
        if self._hotkey:
            self._hotkey.release()
        self.running = False
        self.voice_on = False
        self._notify_state()
        self.log("已停止，并松开快捷键。")
        return True, "已停止"

    def release_keys(self):
        try:
            Hotkey(self.cfg["hotkey"]).release()
            return True
        except Exception:
            return False

    # ---------- 单次探测（界面自检用） ----------
    def probe_level(self):
        probe = MicProbe(self.cfg["probe_ms"], self.cfg["mute_peak_threshold"],
                         self.cfg.get("capture_device", ""))
        st = probe.read()
        with self._lock:
            self.last_peak = st["peak"]
            self.last_rms = st["rms"]
        if self.on_level:
            try:
                self.on_level(st)
            except Exception:
                pass
        return st, probe.is_muted(st)

    # ---------- 主循环 ----------
    def _run(self):
        settle = float(self.cfg["settle_ms"]) / 1000.0
        debounce = float(self.cfg["debounce_ms"]) / 1000.0
        last_event = 0.0
        while not self._stop.is_set():
            if self._handle is None:
                h, inst = open_device(self.cfg.get("vid_pid") or None,
                                      self.cfg.get("hid_path") or None)
                if h is None:
                    self.device_label = "未找到设备"
                    self._notify_state()
                    for _ in range(20):
                        if self._stop.is_set():
                            return
                        time.sleep(0.1)
                    continue
                self._handle = h
                self.device_label = inst
                self.log("已连接 HID 接口：%s" % inst)
                self._notify_state()

            buf = ctypes.create_string_buffer(16)
            read = w.DWORD(0)
            ok = kernel32.ReadFile(self._handle, buf, 16,
                                   ctypes.byref(read), None)
            if self._stop.is_set():
                break
            if not ok:
                err = ctypes.get_last_error()
                self.log("读取失败 err=%d，等待设备重新接入……" % err)
                if self.voice_on and self._hotkey:
                    self._hotkey.release()
                    self.voice_on = False
                try:
                    kernel32.CloseHandle(self._handle)
                except Exception:
                    pass
                self._handle = None
                self._notify_state()
                for _ in range(20):
                    if self._stop.is_set():
                        return
                    time.sleep(0.1)
                continue

            raw = buf.raw[:read.value]
            if len(raw) <= TRIGGER_BYTE or not (raw[TRIGGER_BYTE] & TRIGGER_MASK):
                if any(raw):
                    self.log("未识别报告：%s" % raw.hex(" ").upper())
                continue

            now = time.time()
            if now - last_event < debounce:
                continue
            last_event = now

            # 关键：量麦克风到底有没有信号，用真实电平决定方向
            if settle:
                time.sleep(settle)
            try:
                st = self._probe.read()
            except OSError as exc:
                self.log("采集失败，本次拨动忽略：%s" % exc)
                continue
            with self._lock:
                self.last_peak = st["peak"]
                self.last_rms = st["rms"]
            if self.on_level:
                try:
                    self.on_level(st)
                except Exception:
                    pass

            new_muted = self._probe.is_muted(st)
            if new_muted == self.mic_muted:
                self.log("峰值=%d，位置未变化（重复/抖动事件，忽略）" % st["peak"])
                self._notify_state()
                continue

            self.mic_muted = new_muted
            self.events += 1
            if new_muted:
                self._hotkey.release()
                self.voice_on = False
                msg = "第 %d 次切换 → 【静音】(峰值=%d) → 停止语音输入" % (
                    self.events, st["peak"])
            else:
                # 注入前先看目标窗口的权限：普通权限的自己往管理员窗口注入会被
                # UIPI 静默丢弃，这种情况必须明确告诉用户，否则就是"没反应"
                blocked, who = uipi_blocks_injection()
                if blocked and who and who != self._uipi_warned:
                    self._uipi_warned = who
                    warn = ("⚠ 前台程序「%s」是以管理员身份运行的，而本工具是普通权限，"
                            "Windows 会丢弃注入的按键（UIPI 限制），语音输入不会启动。"
                            "请点界面上的「以管理员身份重启」，或者不用管理员身份运行它。"
                            % who)
                    self.log(warn)
                    if self.on_warn:
                        try:
                            self.on_warn(warn)
                        except Exception:
                            pass
                self._hotkey.press()
                self.voice_on = True
                msg = "第 %d 次切换 → 【麦克风开】(峰值=%d) → 开始语音输入" % (
                    self.events, st["peak"])
            self.log(msg)
            if self.on_event:
                try:
                    self.on_event(msg, self.snapshot())
                except Exception:
                    pass
            self._notify_state()

        try:
            if self._handle:
                kernel32.CloseHandle(self._handle)
        except Exception:
            pass
        self._handle = None


# ---------------------------------------------------------------- 开机自启
AUTOSTART_TASK = "HECATE G2 语音输入桥接"


def startup_lnk_path():
    return os.path.join(os.environ.get("APPDATA", ""),
                        r"Microsoft\Windows\Start Menu\Programs\Startup",
                        "HECATE G2 语音输入桥接.lnk")


def autostart_command():
    """开机自启时实际执行的命令行"""
    if is_frozen():
        return '"%s" --minimized' % sys.executable
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "bridge_gui.py")
    return '"%s" "%s" --minimized' % (exe, script)


def autostart_task_installed():
    import subprocess
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", AUTOSTART_TASK],
                           capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def _install_startup_lnk():
    """退回方案：启动文件夹快捷方式（非提权）"""
    import subprocess
    lnk = startup_lnk_path()
    cmd = autostart_command()
    exe, _, args = cmd.partition(" ")
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath='%s';$s.Arguments='%s';"
        "$s.WorkingDirectory='%s';$s.Description='HECATE G2 语音输入桥接';"
        "$s.Save()" % (lnk, exe.strip('"'), args.strip('"'), data_dir())
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=20)
        return os.path.exists(lnk)
    except Exception:
        return False


def install_autostart(target=None):
    """设置开机自启，返回 (是否成功, 说明)。

    已经是管理员时优先注册**最高权限的计划任务**：这样开机后本工具也是管理员，
    才能把按键注入到以管理员身份运行的程序里（UIPI 限制），而且计划任务启动
    **不会弹 UAC**。普通权限时退回启动文件夹快捷方式，界面会提示这个差别。
    """
    import subprocess
    if current_is_elevated():
        try:
            r = subprocess.run(
                ["schtasks", "/Create", "/F", "/TN", AUTOSTART_TASK,
                 "/SC", "ONLOGON", "/RL", "HIGHEST", "/TR", autostart_command()],
                capture_output=True, timeout=30, text=True, errors="replace")
            if r.returncode == 0 and autostart_task_installed():
                # 顺手清掉旧的启动文件夹快捷方式，避免开机启动两次
                try:
                    if os.path.exists(startup_lnk_path()):
                        os.remove(startup_lnk_path())
                except Exception:
                    pass
                return True, "计划任务（最高权限）"
            return False, (r.stderr or r.stdout or "schtasks 执行失败").strip()
        except Exception as exc:
            return False, repr(exc)
    ok = _install_startup_lnk()
    return ok, (startup_lnk_path() if ok else "创建快捷方式失败")


def remove_autostart():
    import subprocess
    ok = True
    try:
        if os.path.exists(startup_lnk_path()):
            os.remove(startup_lnk_path())
    except Exception:
        ok = False
    if autostart_task_installed():
        try:
            r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", AUTOSTART_TASK],
                               capture_output=True, timeout=20)
            if r.returncode != 0:
                ok = False
        except Exception:
            ok = False
    return ok


def autostart_installed():
    return os.path.exists(startup_lnk_path()) or autostart_task_installed()


def autostart_kind():
    """人类可读的自启方式，用于界面显示"""
    if autostart_task_installed():
        return "计划任务（最高权限）"
    if os.path.exists(startup_lnk_path()):
        return "启动文件夹（普通权限）"
    return "未设置"
