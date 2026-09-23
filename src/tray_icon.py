# -*- coding: utf-8 -*-
"""Windows 系统托盘图标（纯 ctypes 实现，不依赖任何第三方库）

为什么不用 pystray / pywin32：
    本项目对外承诺"运行时零第三方依赖"。托盘图标用 Shell_NotifyIconW 就能做，
    图标本身按 ICO 格式在内存里生成，再用 CreateIconFromResourceEx 装载。

用法：
    tray = TrayIcon(tooltip="...", on_activate=cb, build_menu=menu_cb, on_command=cmd_cb)
    # 在主线程的事件循环里定期调用（例如 tkinter 的 after）
    tray.pump()
    tray.set_state("live", "状态：录音中")
    tray.remove()
"""
import ctypes
import os
import struct
import sys
from ctypes import wintypes as w

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- 消息与常量 ----
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_DESTROY = 0x0002
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_GRAYED = 0x00000001
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
PM_REMOVE = 0x0001
HWND_MESSAGE = -3
LR_DEFAULTCOLOR = 0x0000
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXSMICON = 49
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

user32.LoadImageW.restype = w.HANDLE
user32.LoadImageW.argtypes = [w.HANDLE, w.LPCWSTR, w.UINT, ctypes.c_int,
                              ctypes.c_int, w.UINT]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
try:
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
except AttributeError:
    pass


def asset_dir():
    """assets 目录：打包后在 PyInstaller 的解包目录里，源码运行时在项目根目录"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "assets")
    return os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "assets")


def default_icon_path():
    return os.path.join(asset_dir(), "app.ico")


def small_icon_size():
    """系统托盘小图标的真实像素尺寸。

    本进程是 DPI 不感知的，GetSystemMetrics 会被虚拟化（缩放 125% 时只返回 16，
    实际需要 20）。这里临时把当前线程切成 DPI 感知取一次真实值，取完立刻还原，
    不影响 tkinter。
    """
    ctx = None
    try:
        ctx = user32.SetThreadDpiAwarenessContext(
            ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    except Exception:
        ctx = None
    try:
        s = user32.GetSystemMetrics(SM_CXSMICON) or 16
    except Exception:
        s = 16
    finally:
        if ctx:
            try:
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(ctx))
            except Exception:
                pass
    return max(16, min(int(s), 64))


def load_icon_file(path, size=None):
    """从 .ico 文件装载 HICON（按系统实际尺寸，避免缩放发虚）"""
    if not path or not os.path.exists(path):
        return None
    if size is None:
        size = small_icon_size()
    for flag_size in (size, 0):
        try:
            h = user32.LoadImageW(None, path, IMAGE_ICON, flag_size, flag_size,
                                  LR_LOADFROMFILE)
            if h:
                return h
        except Exception:
            pass
    return None

# 状态配色（RGB）
COLORS = {
    "idle": (0x8A, 0x8A, 0x8A),      # 未启动
    "ready": (0x4C, 0x6E, 0xF5),     # 运行中·待命
    "live": (0x2F, 0x9E, 0x44),      # 运行中·录音中
    "error": (0xC9, 0x2A, 0x2A),     # 出错
}

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HANDLE), ("hIcon", w.HANDLE),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HANDLE),
                ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR),
                ("hIconSm", w.HANDLE)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT),
        ("uFlags", w.UINT), ("uCallbackMessage", w.UINT), ("hIcon", w.HANDLE),
        ("szTip", w.WCHAR * 128), ("dwState", w.DWORD), ("dwStateMask", w.DWORD),
        ("szInfo", w.WCHAR * 256), ("uVersion", w.UINT),
        ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", w.HANDLE),
    ]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT),
                ("guidItem", ctypes.c_byte * 16)]


user32.CreateWindowExW.restype = w.HWND
user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, w.HWND, w.HANDLE, w.HANDLE,
                                   ctypes.c_void_p]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.RegisterClassExW.restype = w.ATOM
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.DestroyWindow.argtypes = [w.HWND]
user32.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(w.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
user32.CreatePopupMenu.restype = w.HANDLE
user32.AppendMenuW.argtypes = [w.HANDLE, w.UINT, ctypes.c_size_t, w.LPCWSTR]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [w.HANDLE, w.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, w.HWND, ctypes.c_void_p]
user32.DestroyMenu.argtypes = [w.HANDLE]
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.CreateIconFromResourceEx.restype = w.HANDLE
user32.CreateIconFromResourceEx.argtypes = [ctypes.c_void_p, w.DWORD, w.BOOL,
                                            w.DWORD, ctypes.c_int, ctypes.c_int,
                                            w.UINT]
user32.LoadIconW.restype = w.HANDLE
user32.LoadIconW.argtypes = [w.HANDLE, w.LPCWSTR]
user32.DestroyIcon.argtypes = [w.HANDLE]
user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]

shell32.Shell_NotifyIconW.restype = w.BOOL
shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long
shell32.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(NOTIFYICONIDENTIFIER),
                                            ctypes.POINTER(w.RECT)]

kernel32.GetModuleHandleW.restype = w.HANDLE
kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]


# --------------------------------------------------------------------------
# 图标生成：按 ICO 格式在内存里画出 32x32 图标
# --------------------------------------------------------------------------
def _in_round_rect(u, v, x0, y0, x1, y1, r):
    """点是否落在圆角矩形内"""
    if u < x0 or u > x1 or v < y0 or v > y1:
        return False
    cx = min(max(u, x0 + r), x1 - r)
    cy = min(max(v, y0 + r), y1 - r)
    return (u - cx) ** 2 + (v - cy) ** 2 <= r * r


def _in_capsule(u, v, x0, y0, x1, y1, r):
    """点是否落在一条有粗细的线段（胶囊）周围"""
    dx, dy = x1 - x0, y1 - y0
    l2 = dx * dx + dy * dy
    if l2 <= 0:
        return (u - x0) ** 2 + (v - y0) ** 2 <= r * r
    t = ((u - x0) * dx + (v - y0) * dy) / l2
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    px, py = x0 + t * dx, y0 + t * dy
    return (u - px) ** 2 + (v - py) ** 2 <= r * r


# 头戴式耳机的各个部件（u, v 为 0~1 的相对坐标）
_BAND_CX, _BAND_CY = 0.50, 0.55      # 头梁圆弧的圆心
_BAND_R, _BAND_T = 0.295, 0.044      # 半径与半厚度
_BAND_RIN2 = (_BAND_R - _BAND_T) ** 2
_BAND_ROUT2 = (_BAND_R + _BAND_T) ** 2


def _in_headset(u, v):
    """头戴式耳机（带麦克风）图形"""
    # 头梁：一段圆环的上半部分
    if v <= 0.60:
        dx, dy = u - _BAND_CX, v - _BAND_CY
        d2 = dx * dx + dy * dy
        if _BAND_RIN2 <= d2 <= _BAND_ROUT2:
            return True
    # 左右耳罩（比头梁明显更宽，这样轮廓才认得出来）
    if _in_round_rect(u, v, 0.112, 0.470, 0.302, 0.700, 0.068):
        return True
    if _in_round_rect(u, v, 0.698, 0.470, 0.888, 0.700, 0.068):
        return True
    # 麦克风臂（从左耳罩下沿伸向右下）
    if _in_capsule(u, v, 0.235, 0.690, 0.400, 0.790, 0.022):
        return True
    # 麦克风头
    if _in_round_rect(u, v, 0.378, 0.755, 0.487, 0.850, 0.038):
        return True
    return False


def make_icon_image(size, rgb, ss=4):
    """生成图标的"资源"数据：BITMAPINFOHEADER + XOR 位图 + AND 掩码。
    这正是 CreateIconFromResourceEx 需要的格式（注意：不含 ICO 文件头）。"""
    r8, g8, b8 = rgb
    W = size * ss
    cx = cy = (W - 1) / 2.0
    R = W * 0.47
    cov_bg = [0.0] * (size * size)
    cov_fg = [0.0] * (size * size)
    for py in range(W):
        for px in range(W):
            fx, fy = px + 0.5, py + 0.5
            if ((fx - cx) ** 2 + (fy - cy) ** 2) ** 0.5 > R:
                continue
            i = (py // ss) * size + (px // ss)
            cov_bg[i] += 1.0
            if _in_headset(fx / W, fy / W):
                cov_fg[i] += 1.0
    norm = float(ss * ss)

    rows = []
    for y in range(size - 1, -1, -1):          # DIB 是自下而上
        row = bytearray()
        for x in range(size):
            i = y * size + x
            a = cov_bg[i] / norm
            f = cov_fg[i] / norm
            if a <= 0.0:
                row += b"\x00\x00\x00\x00"
                continue
            r = r8 * (1 - f) + 255 * f
            g = g8 * (1 - f) + 255 * f
            b = b8 * (1 - f) + 255 * f
            row += bytes((int(b), int(g), int(r), int(round(a * 255))))
        rows.append(bytes(row))
    xor = b"".join(rows)
    and_mask = b"\x00" * (size * 4)            # 32 位图标靠 alpha，掩码置 0
    bih = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                      len(xor), 0, 0, 0, 0)
    return bih + xor + and_mask


def make_ico_file(size, rgb, ss=4):
    """生成完整 .ico 文件字节（单尺寸）"""
    img = make_icon_image(size, rgb, ss)
    icondir = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                        len(img), 22)
    return icondir + entry + img


def make_ico_file_multi(rgb, sizes=(16, 24, 32, 48, 64, 128)):
    """生成多尺寸 .ico 文件字节（给 exe 图标用，各尺寸都清晰）"""
    imgs = []
    for s in sizes:
        ss = 4 if s <= 32 else (3 if s <= 64 else 2)
        imgs.append(make_icon_image(s, rgb, ss))
    n = len(sizes)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    entries = b""
    data = b""
    for s, img in zip(sizes, imgs):
        b = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
        data += img
    return header + entries + data


def load_icon_from_image(image):
    """把资源格式的图标数据装载成 HICON"""
    buf = ctypes.create_string_buffer(image, len(image))
    return user32.CreateIconFromResourceEx(buf, len(image), True, 0x00030000,
                                           0, 0, LR_DEFAULTCOLOR)


# --------------------------------------------------------------------------
# 托盘图标
# --------------------------------------------------------------------------
class TrayIcon:
    """系统托盘图标。

    on_activate()     左键单击/双击
    build_menu()      返回 [(id, 文字) | None(分隔线), ...]，文字为 '-' 也算分隔线
    on_command(cid)   菜单项被选中
    """

    _class_registered = False

    def __init__(self, tooltip="", on_activate=None, build_menu=None,
                 on_command=None, uid=1, icon_file=None):
        self.tooltip = tooltip[:127]
        self.on_activate = on_activate
        self.build_menu = build_menu
        self.on_command = on_command
        self.uid = uid
        self.icons = {}
        self.hicon = None
        self.available = False
        self.hwnd = None
        self._hicon_owned = set()
        self.file_icon = None

        self._create_window()

        # 优先用 assets/app.ico（由用户的矢量设计稿光栅化而来）
        path = icon_file if icon_file is not None else default_icon_path()
        self.file_icon = load_icon_file(path)
        if self.file_icon:
            self._hicon_owned.add(self.file_icon)
            self.hicon = self.file_icon

        if not self.file_icon:
            # 退路：用代码画的图形，并按状态换颜色
            for name, rgb in COLORS.items():
                try:
                    ic = load_icon_from_image(make_icon_image(32, rgb))
                    if ic:
                        self.icons[name] = ic
                        self._hicon_owned.add(ic)
                except Exception:
                    pass
            if not self.icons:
                ic = user32.LoadIconW(None, ctypes.c_wchar_p(32512))
                if ic:
                    self.icons["idle"] = ic
            self.hicon = self.icons.get("idle")

        self.nid = NOTIFYICONDATAW()
        self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.nid.hWnd = self.hwnd
        self.nid.uID = self.uid
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.uCallbackMessage = WM_TRAYICON
        self.nid.hIcon = self.hicon
        self.nid.szTip = self.tooltip
        self.available = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid)))

    # ---------- 窗口 ----------
    def _create_window(self):
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._on_message)     # 必须持引用，否则被回收
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = "HecateG2TrayWnd"
        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            err = ctypes.get_last_error()
            if err != 1410:                            # 1410 = 类已注册
                raise OSError("RegisterClassExW 失败 err=%d" % err)
        self.hwnd = user32.CreateWindowExW(
            0, "HecateG2TrayWnd", "", 0, 0, 0, 0, 0,
            w.HWND(HWND_MESSAGE) if hasattr(w, "HWND") else ctypes.c_void_p(HWND_MESSAGE),
            None, hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW 失败 err=%d" % ctypes.get_last_error())

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAYICON:
                ev = lparam & 0xFFFF
                if ev in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    if self.on_activate:
                        self.on_activate()
                    return 0
                if ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu()
                    return 0
            elif msg == WM_DESTROY:
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self):
        if not self.build_menu:
            return
        items = self.build_menu() or []
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        try:
            for it in items:
                if it is None:
                    user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                    continue
                cid, label = it
                if label == "-":
                    user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                else:
                    user32.AppendMenuW(hmenu, MF_STRING, ctypes.c_size_t(cid), label)
            pt = w.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(self.hwnd)
            cmd = user32.TrackPopupMenu(
                hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | 0x0080,   # 0x0080 = NONOTIFY
                pt.x, pt.y, 0, self.hwnd, None)
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
            if cmd and self.on_command:
                self.on_command(int(cmd))
        finally:
            user32.DestroyMenu(hmenu)

    # ---------- 对外接口 ----------
    def pump(self):
        """在主线程事件循环里定期调用，处理托盘消息"""
        if not self.hwnd:
            return
        msg = w.MSG()
        n = 0
        while n < 20 and user32.PeekMessageW(ctypes.byref(msg), self.hwnd,
                                             0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
            n += 1

    def set_state(self, state, tooltip=None):
        """切换提示文字（若没有 app.ico，则同时切换程序化图标的配色）"""
        if tooltip is not None:
            self.tooltip = tooltip[:127]
        new_icon = self.file_icon or self.icons.get(state) or self.hicon
        if not self.available:
            return
        self.nid.uFlags = NIF_ICON | NIF_TIP
        self.nid.hIcon = new_icon
        self.nid.szTip = self.tooltip
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP

    def notify(self, title, text, warning=False):
        """气泡提示"""
        if not self.available:
            return
        self.nid.uFlags = NIF_INFO
        self.nid.szInfoTitle = title[:63]
        self.nid.szInfo = text[:255]
        self.nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP

    def get_rect(self):
        """返回图标在通知区域的位置（用于自动化验证），失败返回 None"""
        nid = NOTIFYICONIDENTIFIER()
        nid.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
        nid.hWnd = self.hwnd
        nid.uID = self.uid
        rc = w.RECT()
        hr = shell32.Shell_NotifyIconGetRect(ctypes.byref(nid), ctypes.byref(rc))
        if hr != 0:
            return None
        return (rc.left, rc.top, rc.right, rc.bottom)

    def remove(self):
        if self.available:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
            except Exception:
                pass
            self.available = False
        if self.hwnd:
            try:
                user32.DestroyWindow(self.hwnd)
            except Exception:
                pass
            self.hwnd = None
        for ic in list(self._hicon_owned):
            try:
                user32.DestroyIcon(ic)
            except Exception:
                pass
        self._hicon_owned.clear()
