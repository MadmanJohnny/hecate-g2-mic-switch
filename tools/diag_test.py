# -*- coding: utf-8 -*-
"""一次诊断两件事：
  ① 一次物理拨动到底产生几个 HID 事件（毫秒级时间戳，看有没有抖动/双触发）
  ② 开关拨到「静音」时，麦克风信号是否真的被切断（配合持续说话来判断）

用法: python diag_test.py [秒数]
"""
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes as w

# 本脚本在 tools\ 下，主程序在 ..\src\ 下
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import g2_voice_bridge as gb  # noqa: E402
import mic_level  # noqa: E402

TRIGGER_BYTE = 1
TRIGGER_MASK = 0x08


def hid_reader(events, stop, vid):
    handle, inst = gb.open_device(vid)
    if not handle:
        events.append(("ERR", time.time(), "无法打开 HID 设备"))
        return
    events.append(("OPEN", time.time(), inst))
    buf = ctypes.create_string_buffer(16)
    while not stop["flag"]:
        read = w.DWORD(0)
        ok = gb.kernel32.ReadFile(handle, buf, 16, ctypes.byref(read), None)
        if not ok:
            events.append(("ERR", time.time(),
                           "读取失败 err=%d" % ctypes.get_last_error()))
            return
        raw = buf.raw[:read.value]
        if len(raw) > TRIGGER_BYTE and (raw[TRIGGER_BYTE] & TRIGGER_MASK):
            events.append(("EVT", time.time(), raw.hex(" ").upper()))
        elif any(raw):
            events.append(("OTHER", time.time(), raw.hex(" ").upper()))


def mic_reader(levels, stop, chunk_ms=300):
    try:
        sampler = mic_level.MicSampler()
    except OSError as exc:
        levels.append(("ERR", time.time(), str(exc), 0, 0))
        return
    while not stop["flag"]:
        t = time.time()
        st = sampler.sample(chunk_ms)
        levels.append(("LVL", t, st["peak"], st["rms"], st.get("nonzero", 0)))
    sampler.close()


def main():
    total = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
    vid = gb.load_config()["vid_pid"]
    events, levels = [], []
    stop = {"flag": False}

    te = threading.Thread(target=hid_reader, args=(events, stop, vid), daemon=True)
    tm = threading.Thread(target=mic_reader, args=(levels, stop), daemon=True)
    te.start()
    tm.start()

    print("=== 诊断开始（%.0f 秒）===" % total)
    print("请做两件事：")
    print("  ① 对着麦克风【持续说话】（读一段文字，别停）")
    print("  ② 同时把线控开关拨 3 次，每次间隔约 8 秒，拨完就停")
    print("-" * 74)
    t0 = time.time()
    while time.time() - t0 < total:
        time.sleep(1.0)
    stop["flag"] = True
    time.sleep(0.8)

    # 合并时间线
    lv = [x for x in levels if x[0] == "LVL"]
    ev = [x for x in events if x[0] in ("EVT", "OTHER")]
    print("\n%-13s %8s %9s   %s" % ("时间", "峰值", "RMS", "事件"))
    if lv:
        start = min(x[1] for x in lv)
        end = max(x[1] for x in lv)
        for x in lv:
            _, t, peak, rms, _nz = x
            hit = [e for e in ev if t <= e[1] < t + 0.3]
            mark = ("  ← " + ", ".join("事件 %s @%.3f" % (e[2], e[1] - start)
                                       for e in hit)) if hit else ""
            print("%-13s %8d %9.1f %s"
                  % (time.strftime("%H:%M:%S", time.localtime(t)) + ".%03d"
                     % int((t % 1) * 1000), peak, rms, mark))

    print("\n=== 事件统计 ===")
    for kind, t, info in events:
        tag = {"OPEN": "打开设备", "ERR": "错误", "EVT": "★ 拨动事件",
               "OTHER": "其他报告"}.get(kind, kind)
        print("  %s.%03d  %s  %s"
              % (time.strftime("%H:%M:%S", time.localtime(t)),
                 int((t % 1) * 1000), tag, info))
    if len(ev) > 1:
        print("\n事件间隔(秒): %s"
              % ", ".join("%.3f" % (ev[i + 1][1] - ev[i][1])
                          for i in range(len(ev) - 1)))
    print("\n麦克风电平区间：峰值 %d ~ %d，RMS %.1f ~ %.1f"
          % (min(x[2] for x in lv), max(x[2] for x in lv),
             min(x[3] for x in lv), max(x[3] for x in lv)) if lv else "无电平数据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
