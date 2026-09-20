# -*- coding: utf-8 -*-
"""HECATE G2 麦克风开关探针（纯标准库 ctypes + winreg）

用法:
  python hid_probe.py caps             列出 HID 接口能力与支持的 usage
  python hid_probe.py watch [秒数]     实时打印报告与按键 usage（默认 60 秒）

设计目标：在不装任何第三方库的前提下，判断耳机线控的麦克风开关
是否真的向 Windows 发送 HID 报告，以及发的是哪个 usage。
"""
import ctypes
import sys
import time
from ctypes import wintypes as w

import winreg

HID_GUID = "{4d1e55b2-f16f-11cf-88cb-001111000030}"
HIDP_INPUT = 0
HIDP_OUTPUT = 1
HIDP_FEATURE = 2

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
hid = ctypes.WinDLL("hid.dll", use_last_error=True)

kernel32.CreateFileW.restype = w.HANDLE
kernel32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                 w.DWORD, w.DWORD, w.HANDLE]
kernel32.CloseHandle.argtypes = [w.HANDLE]
kernel32.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD,
                              ctypes.POINTER(w.DWORD), ctypes.c_void_p]


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
        ("NumberInputButtonCaps", w.USHORT),
        ("NumberInputValueCaps", w.USHORT),
        ("NumberInputDataIndices", w.USHORT),
        ("NumberOutputButtonCaps", w.USHORT),
        ("NumberOutputValueCaps", w.USHORT),
        ("NumberOutputDataIndices", w.USHORT),
        ("NumberFeatureButtonCaps", w.USHORT),
        ("NumberFeatureValueCaps", w.USHORT),
        ("NumberFeatureDataIndices", w.USHORT),
    ]


class HIDP_BUTTON_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", w.USHORT), ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte), ("BitField", w.USHORT),
        ("LinkCollection", w.USHORT), ("LinkUsage", w.USHORT),
        ("LinkUsagePage", w.USHORT),
        ("IsRange", ctypes.c_ubyte), ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte), ("IsAbsolute", ctypes.c_ubyte),
        ("HasNull", ctypes.c_ubyte), ("Reserved", ctypes.c_ubyte),
        ("BitSize", w.USHORT), ("ReportCount", w.USHORT),
        ("Reserved2", w.USHORT * 5),
        ("UsageMin", w.USHORT), ("UsageMax", w.USHORT),
        ("StringMin", w.USHORT), ("StringMax", w.USHORT),
        ("DesignatorMin", w.USHORT), ("DesignatorMax", w.USHORT),
        ("DataIndexMin", w.USHORT), ("DataIndexMax", w.USHORT),
    ]


hid.HidD_GetPreparsedData.argtypes = [w.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetButtonCaps.argtypes = [ctypes.c_int, ctypes.POINTER(HIDP_BUTTON_CAPS),
                                   ctypes.POINTER(w.USHORT), ctypes.c_void_p]
hid.HidP_GetUsages.argtypes = [ctypes.c_int, w.USHORT, w.USHORT,
                               ctypes.POINTER(w.USHORT), ctypes.POINTER(w.ULONG),
                               ctypes.c_void_p, ctypes.c_char_p, w.ULONG]

# 常用的 HID Consumer Page (0x0C) usage 名称
CONSUMER_NAMES = {
    0x01: "Consumer Control",
    0x24: "?0x24", 0x2F: "Phone Mute(麦克风静音)",
    0xB0: "Play", 0xB1: "Pause", 0xB2: "Record", 0xB3: "FastForward",
    0xB4: "Rewind", 0xB5: "ScanNextTrack", 0xB6: "ScanPrevTrack",
    0xB7: "Stop", 0xCD: "Play/Pause", 0xE2: "Mute(系统静音)",
    0xE9: "Volume Up", 0xEA: "Volume Down", 0x6F: "Brightness Up",
    0x70: "Brightness Down", 0x183: "AL Consumer Control Config",
}
USAGE_PAGE_NAMES = {0x01: "Generic Desktop", 0x02: "Simulation",
                    0x07: "Keyboard", 0x08: "LED", 0x09: "Button",
                    0x0B: "Telephony", 0x0C: "Consumer",
                    0x0D: "Digitizer", 0xFF00: "Vendor 0xFF00",
                    0xFF01: "Vendor 0xFF01", 0xFFA0: "Vendor 0xFFA0"}


def enum_hid_paths(vid_filter=None):
    """从注册表枚举 HID 设备实例，返回 [(实例ID, [候选接口路径])]"""
    out = []
    base = r"SYSTEM\CurrentControlSet\Enum\HID"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError as exc:
        print("无法读取 HKLM\\%s : %s" % (base, exc))
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
            inst_id = name + "\\" + inst
            cands = []
            try:
                sub = winreg.OpenKey(key, inst)
                sym, _ = winreg.QueryValueEx(sub, "SymbolicLink")
                sym = sym.replace("\\??\\", "\\\\?\\")
                cands.append(sym)
                cands.append(sym + "#" + HID_GUID)
            except OSError:
                pass
            cands.append("\\\\?\\HID#" + name.replace("\\", "#") + "#" + inst
                         + "#" + HID_GUID)
            seen, uniq = set(), []
            for c in cands:
                if c not in seen:
                    seen.add(c)
                    uniq.append(c)
            out.append((inst_id, uniq))
    return out


INVALID = ctypes.c_void_p(-1).value

kernel32.DeviceIoControl.restype = w.BOOL
kernel32.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD,
                                     ctypes.c_void_p, w.DWORD,
                                     ctypes.POINTER(w.DWORD), ctypes.c_void_p]

IOCTL_CANDIDATES = (0x000B0007, 0x000B0192)


def read_report_descriptor(handle):
    """通过 IOCTL 读取 HID 报告描述符"""
    buf = ctypes.create_string_buffer(4096)
    returned = w.DWORD(0)
    for code in IOCTL_CANDIDATES:
        ctypes.set_last_error(0)
        ok = kernel32.DeviceIoControl(handle, code, None, 0, buf, 4096,
                                      ctypes.byref(returned), None)
        if ok and returned.value:
            return code, buf.raw[:returned.value]
    return None, None


ITEM_TYPE = {0: "Main", 1: "Global", 2: "Local"}
GLOBAL_TAGS = {0x0: "UsagePage", 0x1: "LogicalMin", 0x2: "LogicalMax",
               0x3: "PhysicalMin", 0x4: "PhysicalMax", 0x5: "UnitExponent",
               0x6: "Unit", 0x7: "ReportSize", 0x8: "ReportID",
               0x9: "ReportCount", 0xA: "Push", 0xB: "Pop"}
LOCAL_TAGS = {0x0: "Usage", 0x1: "UsageMin", 0x2: "UsageMax",
              0x3: "DesignatorIndex", 0x4: "DesignatorMin",
              0x5: "DesignatorMax", 0x6: "StringIndex", 0x7: "StringMin",
              0x8: "StringMax"}
MAIN_TAGS = {0x8: "Input", 0x9: "Output", 0xA: "Collection",
             0xB: "Feature", 0xC: "EndCollection"}


def parse_descriptor(data):
    i = 0
    usage_page = 0
    usages, umin, umax = [], None, None
    rsize = rcount = rid = 0
    out = []
    while i < len(data):
        prefix = data[i]
        i += 1
        if prefix == 0xFE:          # long item
            size = data[i] if i < len(data) else 0
            i += 2 + size
            continue
        bsize = prefix & 0x03
        btype = (prefix >> 2) & 0x03
        btag = (prefix >> 4) & 0x0F
        n = 4 if bsize == 3 else bsize
        raw = data[i:i + n]
        i += n
        val = int.from_bytes(raw, "little") if raw else 0
        if btype == 1:      # Global
            name = GLOBAL_TAGS.get(btag, "G?0x%X" % btag)
            if btag == 0x0:
                usage_page = val
            elif btag == 0x7:
                rsize = val
            elif btag == 0x8:
                rid = val
            elif btag == 0x9:
                rcount = val
            out.append("      Global %s = 0x%X" % (name, val))
        elif btype == 2:    # Local
            name = LOCAL_TAGS.get(btag, "L?0x%X" % btag)
            if btag == 0x0:
                usages.append(val)
            elif btag == 0x1:
                umin = val
            elif btag == 0x2:
                umax = val
            out.append("      Local %s = 0x%X" % (name, val))
        else:               # Main
            name = MAIN_TAGS.get(btag, "M?0x%X" % btag)
            line = "   Main %s reportID=%d size=%d count=%d 用法: " % (
                name, rid, rsize, rcount)
            if usages:
                line += "page0x%02X " % usage_page + ",".join(
                    "0x%02X(%s)" % (u, CONSUMER_NAMES.get(u, "?"))
                    if usage_page == 0x0C else "0x%02X" % u for u in usages)
            elif umin is not None or umax is not None:
                a = umin if umin is not None else 0
                b = umax if umax is not None else a
                names = ",".join(
                    "0x%02X(%s)" % (u, CONSUMER_NAMES.get(u, "?"))
                    if usage_page == 0x0C else "0x%02X" % u
                    for u in range(a, min(b, a + 15) + 1))
                line += "page0x%02X 范围 %s%s" % (
                    usage_page, names, " ..." if b - a > 15 else "")
            out.append(line)
            usages, umin, umax = [], None, None
    return out


def open_hid(cands, verbose=False):
    last = None
    for path in cands:
        ctypes.set_last_error(0)
        h = kernel32.CreateFileW(path, GENERIC_READ,
                                 FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                 OPEN_EXISTING, 0, None)
        hv = h.value if hasattr(h, "value") else h
        if hv and hv != INVALID:
            return h, path
        last = (path, ctypes.get_last_error())
        if verbose:
            print("   打开失败 err=%d : %s" % (last[1], path))
    return None, last


def describe(handle, path, inst_id, caps, prep):
    attrs = HIDD_ATTRIBUTES()
    attrs.Size = ctypes.sizeof(attrs)
    hid.HidD_GetAttributes(handle, ctypes.byref(attrs))
    print("设备实例 : %s" % inst_id)
    print("接口路径 : %s" % path)
    print("VID:PID  : %04X:%04X  ver=%04X" %
          (attrs.VendorID, attrs.ProductID, attrs.VersionNumber))
    print("顶层用法 : page=0x%04X (%s) usage=0x%04X (%s)" % (
        caps.UsagePage, USAGE_PAGE_NAMES.get(caps.UsagePage, "?"),
        caps.Usage, CONSUMER_NAMES.get(caps.Usage, "")))
    print("报告长度 : in=%d out=%d feature=%d  链路集合=%d" % (
        caps.InputReportByteLength, caps.OutputReportByteLength,
        caps.FeatureReportByteLength, caps.NumberLinkCollectionNodes))
    print("输入按钮 caps 数 : %d" % caps.NumberInputButtonCaps)
    n = caps.NumberInputButtonCaps
    if n:
        arr = (HIDP_BUTTON_CAPS * n)()
        cnt = w.USHORT(n)
        res = hid.HidP_GetButtonCaps(HIDP_INPUT, arr, ctypes.byref(cnt), prep)
        print("  HidP_GetButtonCaps -> %d, 实际 %d" % (res, cnt.value))
        for k in range(cnt.value):
            bc = arr[k]
            if bc.IsRange:
                usages = list(range(bc.UsageMin, bc.UsageMax + 1))
            else:
                usages = [bc.UsageMin]
            names = ", ".join(
                "0x%02X(%s)" % (u, CONSUMER_NAMES.get(u, "?")) if bc.UsagePage == 0x0C
                else "0x%02X" % u for u in usages)
            print("   [%d] page=0x%04X reportID=%d linkColl=%d 按钮数=%d usage: %s"
                  % (k, bc.UsagePage, bc.ReportID, bc.LinkCollection,
                     bc.ReportCount, names))
    hid.HidD_FreePreparsedData(prep)
    return caps


def watch(handle, caps, prep, seconds):
    buf_len = max(caps.InputReportByteLength, 8)
    buf = ctypes.create_string_buffer(buf_len)
    read = w.DWORD(0)
    print("=== 开始监听 HID 报告 ===")
    print("请拨动耳机线控上的麦克风开关：关→开→关→开 各来一次")
    print("（有输出 = Windows 能收到这个开关的事件；无输出 = 纯硬件静音）")
    print("-" * 60)
    deadline = None if seconds <= 0 else time.time() + seconds
    while deadline is None or time.time() < deadline:
        read.value = 0
        ok = kernel32.ReadFile(handle, buf, buf_len, ctypes.byref(read), None)
        if not ok:
            print("ReadFile 失败 err=%d" % ctypes.get_last_error())
            return
        raw = buf.raw[:read.value]
        parts = []
        for page, label in ((0x0C, "Consumer"), (0x0B, "Telephony"),
                            (0x01, "Desktop"), (0x07, "Keyboard")):
            usages = (w.USHORT * 64)()
            ln = w.ULONG(64)
            res = hid.HidP_GetUsages(HIDP_INPUT, page, 0, usages,
                                     ctypes.byref(ln), prep, raw, len(raw))
            if res == 0x00110000 and ln.value:
                for k in range(ln.value):
                    u = usages[k]
                    nm = CONSUMER_NAMES.get(u, "?") if page == 0x0C else "?"
                    parts.append("page0x%02X usage0x%02X(%s)" % (page, u, nm))
        print("%s  raw=%s  ->  %s" % (time.strftime("%H:%M:%S"),
                                      raw.hex(" ").upper(),
                                      ", ".join(parts) if parts else "(未解析出按键)"))
        sys.stdout.flush()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "caps"
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    vid = sys.argv[3] if len(sys.argv) > 3 else "VID_2D99"
    paths = enum_hid_paths(vid)
    if not paths:
        print("未找到匹配 %s 的 HID 设备" % vid)
        return 1
    print("找到 %d 个 HID 设备实例\n" % len(paths))
    print("sizeof(HIDP_BUTTON_CAPS) = %d" % ctypes.sizeof(HIDP_BUTTON_CAPS))
    for inst_id, cands in paths:
        handle, path = open_hid(cands, verbose=True)
        if not handle:
            print("无法打开 %s（err=%s）" % (inst_id, path))
            continue
        if mode == "desc":
            code, data = read_report_descriptor(handle)
            print("设备实例 : %s" % inst_id)
            if not data:
                print("  读取报告描述符失败（IOCTL 均被拒绝）")
            else:
                print("  报告描述符 %d 字节（IOCTL 0x%08X）:" % (len(data), code))
                print("  原始: %s" % data.hex(" ").upper())
                print("  解析:")
                for line in parse_descriptor(data):
                    print(line)
            kernel32.CloseHandle(handle)
            print("-" * 60)
            continue
        prep = ctypes.c_void_p()
        caps = None
        if hid.HidD_GetPreparsedData(handle, ctypes.byref(prep)):
            caps = HIDP_CAPS()
            hid.HidP_GetCaps(prep, ctypes.byref(caps))
        if not caps:
            print("无法获取 HID 能力信息")
            kernel32.CloseHandle(handle)
            continue
        if mode == "watch":
            print()
            watch(handle, caps, prep, seconds)
            hid.HidD_FreePreparsedData(prep)
        else:
            describe(handle, path, inst_id, caps, prep)
        kernel32.CloseHandle(handle)
        print("-" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
