# -*- coding: utf-8 -*-
"""麦克风电平表（诊断工具，逻辑在 bridge_core 里）

用法:
  python mic_level.py [总秒数] [每段毫秒] [设备名关键字]
"""
import sys
import time

from bridge_core import MicSampler, list_capture_devices, resolve_capture_device


def main():
    total = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    chunk_ms = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    keyword = sys.argv[3] if len(sys.argv) > 3 else ""

    print("可用录音设备：")
    for idx, name in list_capture_devices():
        print("   %s%s" % (name, "" if idx < 0 else "   (#%d)" % idx))
    dev = resolve_capture_device(keyword)
    print("\n使用设备索引: %s\n" % ("默认" if dev == 0xFFFFFFFF else dev))

    sampler = MicSampler(device=dev)
    print("每段 %d ms，共 %.0f 秒。可拨动耳机开关观察数值变化。" % (chunk_ms, total))
    print("-" * 68)
    print("%-12s %8s %10s %10s  %s" % ("时间", "峰值", "RMS", "非零点数", "条形"))
    t0 = time.time()
    while time.time() - t0 < total:
        st = sampler.sample(chunk_ms)
        bar = "#" * min(50, int(st["peak"] / 20) + (1 if st["peak"] else 0))
        print("%-12s %8d %10.1f %10d  %s"
              % (time.strftime("%H:%M:%S"), st["peak"], st["rms"],
                 st.get("nonzero", 0), bar), flush=True)
    sampler.close()
    print("采样结束")


if __name__ == "__main__":
    main()
