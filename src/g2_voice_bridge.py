# -*- coding: utf-8 -*-
"""HECATE G2 麦克风开关 → 输入法语音输入：命令行版（与 GUI 共用 bridge_core）

用法
----
  python g2_voice_bridge.py              正常运行（Ctrl+C 退出）
  python g2_voice_bridge.py --probe      连测三次麦克风电平，报告开关位置
  python g2_voice_bridge.py --list       列出可用的 HID 接口
  python g2_voice_bridge.py --hotkey ctrl+alt+v   临时指定快捷键
"""
import sys
import time

from bridge_core import (Bridge, Hotkey, MicProbe, acquire_single_instance,
                         enum_hid_paths, kernel32, list_hid_candidates,
                         load_config, open_device)  # noqa: F401  供诊断脚本复用


def _print(msg):
    try:
        print(msg, flush=True)
    except Exception:
        pass


def main(argv):
    cfg = load_config()

    for i, a in enumerate(argv):
        if a == "--hotkey" and i + 1 < len(argv):
            cfg["hotkey"] = argv[i + 1]

    if "--list" in argv:
        cands = list_hid_candidates(only_consumer=True)
        if not cands:
            _print("没有找到消费类(0x0C) HID 接口。")
            return 1
        _print("可用的 HID 接口（线控按键通常在其中一个）：")
        for c in cands:
            _print("  VID_%04X&PID_%04X  %-28s %s"
                   % (c["vid"], c["pid"], c.get("product") or "(无名称)",
                      c["instance"]))
        _print("\n当前配置匹配：%s" % (cfg.get("vid_pid") or "(自动)"))
        for inst, _path in enum_hid_paths(cfg.get("vid_pid") or None):
            _print("  -> %s" % inst)
        return 0

    if "--probe" in argv:
        probe = MicProbe(cfg["probe_ms"], cfg["mute_peak_threshold"],
                         cfg.get("capture_device", ""))
        _print("阈值：峰值 ≤ %d 判为「静音」" % cfg["mute_peak_threshold"])
        try:
            for k in range(3):
                st = probe.read()
                _print("第 %d 次：峰值=%-6d RMS=%-7.1f 非零=%-6d → %s"
                       % (k + 1, st["peak"], st["rms"], st.get("nonzero", 0),
                          "静音（无信号）" if probe.is_muted(st) else "麦克风开（有信号）"))
                time.sleep(0.35)
        except OSError as exc:
            _print("采集失败：%s" % exc)
            return 1
        return 0

    guard = acquire_single_instance()
    if guard == "exists":
        _print("已经有一个桥接程序在运行了，本次退出。")
        return 1

    Hotkey(cfg["hotkey"]).release()
    bridge = Bridge(cfg, on_log=_print)

    ok, msg = bridge.start()
    if not ok:
        _print("启动失败：%s" % msg)
        return 1
    _print("桥接运行中，按 Ctrl+C 退出。")
    try:
        while bridge.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        _print("收到 Ctrl+C……")
    finally:
        bridge.stop()
        bridge.release_keys()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
