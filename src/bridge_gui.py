# -*- coding: utf-8 -*-
"""HECATE G2 麦克风开关 → 输入法语音输入：图形界面

直接运行：  python bridge_gui.py
打包后运行： HECATE-G2-语音开关.exe
"""
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_core as core  # noqa: E402
import tray_icon  # noqa: E402

APP_TITLE = "HECATE G2 麦克风开关 → 输入法语音输入"
HOTKEY_PRESETS = ["ctrl+win", "ctrl+alt+space", "ctrl+alt+v", "ralt",
                  "ctrl+shift+space", "f9", "f10"]
LEVEL_MAX = 1500          # 电平条满格对应峰值

HELP_TEXT = """【它是怎么工作的】

  拨动线控开关
        │
        │  耳机通过 HID 接口发来一帧报告
        │  00 08 00 00 00  ← 但两个方向完全一样！
        ▼
  程序收到这帧报告（只把它当"触发器"）
        │
        ▼
  立刻采集 150 毫秒麦克风信号，量出峰值
        │
        ├── 峰值 ≤ 阈值 ──→ 开关在「静音」  ──→ 松开快捷键，停止语音输入
        │
        └── 峰值 > 阈值 ──→ 开关在「麦克风开」──→ 按住快捷键，开始语音输入

【为什么要这么绕】

  这个开关发出的 HID 报告只有「按下」和「松开」两种，两个方向一模一样，
  报告里没有"我现在在哪一边"这个信息。所以不能用它判断开关位置，
  否则程序一漏掉一次拨动（比如拔耳机），开和关就会永久反过来。

  而耳机在静音时，是真的把话筒信号切断了：
        静音        →  峰值 ≈ 1        （完全死寂）
        麦克风开    →  峰值 200 以上   （稳定底噪）
  两者相差 25 倍以上，所以"量一下信号有没有"就能可靠判断开关在哪一边。
  这样拔插耳机、漏事件、一次拨动产生两个事件，都不会造成开/关反向。

【使用方法】

  1. 点「开始桥接」；
  2. 在任意输入框里，把线控开关拨到「麦克风开」→ 自动开始语音输入；
     拨到「静音」→ 自动停止。（先拨哪边都不会出错）
  3. 想让每次开机自动生效，勾选设置页里的「开机自动启动」。
  4. 点窗口右上角的关闭按钮，程序会收进任务栏右下角的托盘小图标继续运行，
     不会退出；托盘图标左键单击可重新打开窗口，右键菜单里有「退出」。
     鼠标悬停在托盘图标上可以看到当前状态（未启动 / 待命 / 录音中）。

【要注意的事】

  · 语音输入开启期间，程序会真的按住 Ctrl+Win。这是微信输入法"长按说话"
    的固有要求，代价是在这段时间里敲键盘会带上 Win 修饰键。说话时别敲键盘
    即可，说完拨回静音就恢复。
  · 程序正常退出会松开按键；如果是用任务管理器强杀，按键可能残留，
    表现为键盘像被 Win 键卡住。此时点「紧急松开按键」按钮，或手动按一下
    左 Ctrl 和左 Win 键各一次。
  · 判定不准时，先点「测一次电平」看看实际数值，再调整「静音判定阈值」。

【适用范围】

  本工具靠"耳机静音时切断话筒信号"这一特性工作。
  换用其它耳机时，可以在「设置」页里重新选择 HID 设备和录音设备，
  并用「测一次电平」确认静音/开启两侧的数值是否能明显区分。
"""


class BridgeGUI:
    def __init__(self, root, minimized=False):
        self.root = root
        self.cfg = core.load_config()
        self.bridge = core.Bridge(
            self.cfg,
            on_log=lambda m: self.root.after(0, self._append_log, m),
            on_state=lambda s: self.root.after(0, self._refresh_state, s),
            on_level=lambda st: self.root.after(0, self._refresh_level, st),
        )
        self._mutex = None
        self._live = {"on": False, "until": 0}
        self._log_file = None
        self._tray = None
        self._tray_hint_shown = False
        self._alive = True
        self._open_log_file()

        root.title(APP_TITLE)
        root.geometry("840x660")
        root.minsize(760, 540)

        self._build_ui()
        self._refresh_device_lists()
        self._setup_tray()
        self._refresh_state(self.bridge.snapshot())
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        # 窗口首次映射时 Tk 会重建顶层窗口（句柄会变），所以图标必须等映射完成再设；
        # 绑定 <Map> 是为了从托盘恢复窗口后也能重新设上
        root.after(200, self._apply_window_icon)
        root.bind("<Map>", lambda _e: self.root.after(60, self._apply_window_icon))

        self._append_log("配置目录：%s" % core.data_dir())
        if self.cfg.get("autostart_bridge"):
            root.after(400, self.start_bridge)
        if minimized:
            self.start_bridge()
            self._hide_to_tray(silent=True)

    # ------------------------------------------------------------ 系统托盘
    def _apply_window_icon(self):
        """把窗口（标题栏 + 任务栏）图标设成我们自己的。

        不设的话会显示 Tk 窗口类自带的默认图标（一根羽毛）：
        Tk 创建 toplevel 时并不设置 WM_GETICON，系统只能落到窗口类的图标上。
        """
        self._win_icons = []
        path = tray_icon.default_icon_path()
        try:
            self.root.iconbitmap(default=path)
            # Tk 的 iconbitmap 是延迟应用的，先让它落地，再用 WM_SETICON 覆盖，
            # 否则我们设的大图标会被 Tk 的默认尺寸盖掉
            self.root.update_idletasks()
        except Exception:
            pass
        try:
            hwnd = int(self.root.wm_frame(), 16)
            if hwnd:
                self._win_icons = tray_icon.set_window_icon(hwnd, path)
                self._append_log("窗口图标已设置为 %s（%d 个句柄）"
                                 % (os.path.basename(path), len(self._win_icons)))
            else:
                self._append_log("拿不到顶层窗口句柄，窗口图标未设置")
        except Exception as exc:
            self._append_log("设置窗口图标失败：%r" % exc)

    def _setup_tray(self):
        try:
            self._tray = tray_icon.TrayIcon(
                tooltip=APP_TITLE,
                on_activate=self._show_window,
                build_menu=self._tray_menu,
                on_command=self._tray_command,
            )
            if self._tray.available:
                self._append_log("系统托盘图标已就绪（左键打开窗口，右键操作菜单）")
            else:
                self._append_log("托盘图标注册失败，关闭窗口将直接退出。")
                self._tray = None
        except Exception as exc:
            self._append_log("托盘图标初始化失败：%r" % exc)
            self._tray = None
        self._pump_tray()

    def _pump_tray(self):
        if self._tray is not None:
            try:
                self._tray.pump()
            except Exception:
                pass
        if self._alive:
            try:
                self.root.after(60, self._pump_tray)
            except Exception:
                pass

    def _show_window(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(220, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except Exception:
            pass

    def _hide_to_tray(self, silent=False):
        try:
            self.root.withdraw()
        except Exception:
            pass
        if not silent and not self._tray_hint_shown:
            self._tray_hint_shown = True
            try:
                self._tray.notify("仍在后台运行",
                                  "程序已最小化到托盘，语音输入控制继续有效。\n"
                                  "双击托盘图标可重新打开，右键可退出。")
            except Exception:
                pass
        self._append_log("窗口已隐藏到托盘（程序继续在后台运行）。")

    def _tray_menu(self):
        s = self.bridge.snapshot()
        items = [(1, "显示主界面"), None]
        items.append((2, "停止桥接") if s["running"] else (2, "开始桥接"))
        items.append(None)
        items.append((9, "退出（停止语音输入控制）"))
        return items

    def _tray_command(self, cid):
        if cid == 1:
            self._show_window()
        elif cid == 2:
            if self.bridge.running:
                self.stop_bridge()
            else:
                self.start_bridge()
        elif cid == 9:
            self._quit_app(confirm=False)

    # ------------------------------------------------------------ 日志
    def _open_log_file(self):
        if not self.cfg.get("log_to_file", True):
            return
        try:
            self._log_file = open(core.log_path(), "a", encoding="utf-8")
        except Exception:
            self._log_file = None

    def _append_log(self, msg):
        if self._log_file:
            try:
                self._log_file.write(msg + "\n")
                self._log_file.flush()
            except Exception:
                pass
        try:
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", msg + "\n")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        except Exception:
            pass

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tab_main = ttk.Frame(nb)
        self.tab_cfg = ttk.Frame(nb)
        self.tab_help = ttk.Frame(nb)
        self.tab_log = ttk.Frame(nb)
        nb.add(self.tab_main, text="  主控  ")
        nb.add(self.tab_cfg, text="  设置  ")
        nb.add(self.tab_help, text="  原理与说明  ")
        nb.add(self.tab_log, text="  日志  ")
        self._build_main()
        self._build_cfg()
        self._build_help()
        self._build_log()

    # ---- 主控页
    def _build_main(self):
        f = self.tab_main

        box = ttk.LabelFrame(f, text="当前状态")
        box.pack(fill="x", padx=10, pady=(10, 6))

        row = ttk.Frame(box)
        row.pack(fill="x", padx=12, pady=10)

        self.lbl_switch = tk.Label(row, text="静音", font=("Microsoft YaHei UI", 20, "bold"),
                                   bg="#8a8a8a", fg="white", width=12, height=2)
        self.lbl_switch.pack(side="left")
        self.lbl_switch_desc = ttk.Label(
            row, text="耳机线控开关位置\n（由实测麦克风电平判断）",
            font=("Microsoft YaHei UI", 10), justify="left")
        self.lbl_switch_desc.pack(side="left", padx=14)

        self.lbl_voice = tk.Label(row, text="语音输入：已停止",
                                  font=("Microsoft YaHei UI", 12, "bold"),
                                  bg="#e8e8e8", width=20, height=2)
        self.lbl_voice.pack(side="right")

        dev = ttk.Frame(box)
        dev.pack(fill="x", padx=12, pady=(0, 10))
        self.lbl_device = ttk.Label(dev, text="设备：检测中……")
        self.lbl_device.pack(side="left")
        self.lbl_stats = ttk.Label(dev, text="")
        self.lbl_stats.pack(side="right")

        lv = ttk.LabelFrame(f, text="麦克风电平（实时可视化，用来判断开关位置）")
        lv.pack(fill="x", padx=10, pady=6)
        self.canvas = tk.Canvas(lv, height=54, bg="white",
                                highlightthickness=1, highlightbackground="#cccccc")
        self.canvas.pack(fill="x", padx=12, pady=10)
        self.canvas.bind("<Configure>", lambda e: self._draw_level())

        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=10, pady=6)
        self.btn_start = ttk.Button(bar, text="开始桥接", command=self.start_bridge)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="停止", command=self.stop_bridge)
        self.btn_stop.pack(side="left", padx=6)
        ttk.Button(bar, text="测一次电平", command=self.probe_once).pack(side="left", padx=6)
        self.btn_live = ttk.Button(bar, text="实时监测 10 秒", command=self.toggle_live)
        self.btn_live.pack(side="left", padx=6)
        ttk.Button(bar, text="紧急松开按键", command=self.emergency_release).pack(
            side="left", padx=6)

        self.lbl_hint = ttk.Label(
            f, text="点「开始桥接」后，拨动耳机线控开关即可控制语音输入。",
            font=("Microsoft YaHei UI", 10))
        self.lbl_hint.pack(anchor="w", padx=14, pady=(2, 10))

    # ---- 设置页
    def _build_cfg(self):
        f = self.tab_cfg
        pad = {"padx": 10, "pady": 4}

        g1 = ttk.LabelFrame(f, text="快捷键与判定")
        g1.pack(fill="x", **pad)
        self.var_hotkey = tk.StringVar(value=self.cfg["hotkey"])
        self.var_thresh = tk.IntVar(value=self.cfg["mute_peak_threshold"])
        self.var_probe = tk.IntVar(value=self.cfg["probe_ms"])
        self.var_settle = tk.IntVar(value=self.cfg["settle_ms"])
        self.var_debounce = tk.IntVar(value=self.cfg["debounce_ms"])

        r = ttk.Frame(g1); r.pack(fill="x", padx=10, pady=6)
        ttk.Label(r, text="语音输入快捷键", width=16).pack(side="left")
        cb = ttk.Combobox(r, textvariable=self.var_hotkey, values=HOTKEY_PRESETS, width=22)
        cb.pack(side="left")
        ttk.Label(r, text="（微信输入法 Windows 版为长按 ctrl+win）").pack(side="left", padx=8)

        r = ttk.Frame(g1); r.pack(fill="x", padx=10, pady=6)
        ttk.Label(r, text="静音判定阈值", width=16).pack(side="left")
        ttk.Spinbox(r, from_=1, to=500, textvariable=self.var_thresh, width=8).pack(side="left")
        ttk.Label(r, text="峰值 ≤ 此值判为「静音」（实测静音≈1，麦克风开≥200）").pack(
            side="left", padx=8)

        r = ttk.Frame(g1); r.pack(fill="x", padx=10, pady=6)
        ttk.Label(r, text="采集时长 (ms)", width=16).pack(side="left")
        ttk.Spinbox(r, from_=40, to=1000, increment=10,
                    textvariable=self.var_probe, width=8).pack(side="left")
        ttk.Label(r, text="拨动后等待 (ms)").pack(side="left", padx=(18, 4))
        ttk.Spinbox(r, from_=0, to=1000, increment=10,
                    textvariable=self.var_settle, width=8).pack(side="left")
        ttk.Label(r, text="去抖 (ms)").pack(side="left", padx=(18, 4))
        ttk.Spinbox(r, from_=0, to=2000, increment=50,
                    textvariable=self.var_debounce, width=8).pack(side="left")

        g2 = ttk.LabelFrame(f, text="设备（换其它耳机时在这里改）")
        g2.pack(fill="x", **pad)
        self.var_hid = tk.StringVar()
        self.var_cap = tk.StringVar()
        r = ttk.Frame(g2); r.pack(fill="x", padx=10, pady=6)
        ttk.Label(r, text="HID 接口", width=16).pack(side="left")
        self.cb_hid = ttk.Combobox(r, textvariable=self.var_hid, width=64, state="readonly")
        self.cb_hid.pack(side="left", fill="x", expand=True)
        r = ttk.Frame(g2); r.pack(fill="x", padx=10, pady=6)
        ttk.Label(r, text="录音设备", width=16).pack(side="left")
        self.cb_cap = ttk.Combobox(r, textvariable=self.var_cap, width=64, state="readonly")
        self.cb_cap.pack(side="left", fill="x", expand=True)
        r = ttk.Frame(g2); r.pack(fill="x", padx=10, pady=6)
        ttk.Button(r, text="刷新设备列表", command=self._refresh_device_lists).pack(side="left")
        ttk.Label(r, text="  只列出「消费类(0x0C)」HID 接口，即线控按键所在的那一个。").pack(
            side="left")
        self._hid_map = {}
        self._cap_map = {}

        g3 = ttk.LabelFrame(f, text="行为")
        g3.pack(fill="x", **pad)
        self.var_startdict = tk.BooleanVar(value=self.cfg.get("start_dictating", False))
        self.var_autostart = tk.BooleanVar(value=core.autostart_installed())
        self.var_autobridge = tk.BooleanVar(value=self.cfg.get("autostart_bridge", False))
        self.var_tray = tk.BooleanVar(value=self.cfg.get("close_to_tray", True))
        ttk.Checkbutton(g3, text="程序启动时若麦克风已经是开的，立即开始语音输入",
                        variable=self.var_startdict).pack(anchor="w", padx=10, pady=3)
        ttk.Checkbutton(g3, text="点击关闭按钮时最小化到托盘，不退出程序"
                                 "（程序常驻任务栏右下角小图标）",
                        variable=self.var_tray).pack(anchor="w", padx=10, pady=3)
        ttk.Checkbutton(g3, text="开机自动启动本程序（在启动文件夹创建快捷方式）",
                        variable=self.var_autostart).pack(anchor="w", padx=10, pady=3)
        ttk.Checkbutton(g3, text="程序启动后自动开始桥接（配合开机自启）",
                        variable=self.var_autobridge).pack(anchor="w", padx=10, pady=3)

        r = ttk.Frame(f); r.pack(fill="x", padx=10, pady=10)
        ttk.Button(r, text="保存设置", command=self.save_config).pack(side="left")
        ttk.Button(r, text="恢复默认", command=self.reset_config).pack(side="left", padx=8)
        ttk.Label(r, text="（保存后需重启桥接生效）").pack(side="left", padx=6)

    # ---- 说明页
    def _build_help(self):
        f = self.tab_help
        self.diagram = tk.Canvas(f, height=250, bg="#fbfbfb",
                                 highlightthickness=1, highlightbackground="#dddddd")
        self.diagram.pack(fill="x", padx=10, pady=(10, 4))
        self.diagram.bind("<Configure>", lambda e: self._draw_diagram())

        box = scrolledtext.ScrolledText(f, wrap="word", height=8,
                                        font=("Microsoft YaHei UI", 10))
        box.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        box.insert("1.0", HELP_TEXT)
        box.configure(state="disabled")

    # ---- 日志页
    def _build_log(self):
        f = self.tab_log
        r = ttk.Frame(f); r.pack(fill="x", padx=10, pady=8)
        ttk.Button(r, text="清空显示", command=self._clear_log).pack(side="left")
        ttk.Button(r, text="打开日志文件", command=self._open_logfile).pack(side="left", padx=8)
        self.lbl_logpath = ttk.Label(r, text=core.log_path())
        self.lbl_logpath.pack(side="left", padx=8)
        self.txt_log = scrolledtext.ScrolledText(f, wrap="none", height=8,
                                                 font=("Consolas", 9))
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.txt_log.configure(state="disabled")

    def _clear_log(self):
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    def _open_logfile(self):
        try:
            os.startfile(core.log_path())
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    # ------------------------------------------------------------ 绘图
    def _draw_level(self):
        c = self.canvas
        c.delete("all")
        wpx = max(c.winfo_width(), 100)
        h = 54
        x0, x1 = 10, wpx - 10
        peak = self.bridge.snapshot()["peak"]
        frac = min(1.0, peak / float(LEVEL_MAX))
        # 背景
        c.create_rectangle(x0, 18, x1, 40, fill="#eeeeee", outline="#cccccc")
        # 阈值刻度（用对数位置，否则 8/1500 看不见）
        th = self.cfg.get("mute_peak_threshold", 8)
        import math
        tx = x0 + (x1 - x0) * min(1.0, math.log10(max(th, 1) + 1) / math.log10(LEVEL_MAX))
        c.create_line(tx, 12, tx, 46, fill="#e08a00", width=2)
        c.create_text(tx, 6, text="阈值 %d" % th, fill="#e08a00", anchor="n",
                      font=("Microsoft YaHei UI", 8))
        # 电平条
        if frac > 0:
            fill = "#2f9e44" if peak > th else "#c0c0c0"
            c.create_rectangle(x0, 18, x0 + (x1 - x0) * frac, 40,
                               fill=fill, outline="")
        c.create_text(x0 + 6, 29, text="峰值 %d" % peak, anchor="w",
                      font=("Microsoft YaHei UI", 10, "bold"),
                      fill="#222222" if peak <= th else "white")
        c.create_text(x1 - 4, 29,
                      text="静音区(< %d)" % th, anchor="e",
                      font=("Microsoft YaHei UI", 8), fill="#888888")

    def _draw_diagram(self):
        c = self.diagram
        c.delete("all")
        wpx = max(c.winfo_width(), 400)
        c.create_text(wpx / 2, 16, text="信号链路（实测结论）",
                      font=("Microsoft YaHei UI", 11, "bold"))

        def box(x, y, bw, bh, text, fill, outline="#999999", fg="#111111"):
            c.create_rectangle(x, y, x + bw, y + bh, fill=fill, outline=outline)
            c.create_text(x + bw / 2, y + bh / 2, text=text, width=bw - 8,
                          font=("Microsoft YaHei UI", 9), fill=fg)

        def arrow(x1, y1, x2, y2):
            c.create_line(x1, y1, x2, y2, arrow="last", fill="#666666", width=2)

        y = 40
        box(20, y, 150, 46, "拨动线控开关", "#e7f5ff")
        arrow(170, y + 23, 205, y + 23)
        box(205, y, 220, 46, "HID 报告 00 08 00 00 00\n(两方向完全相同)", "#fff4e6")
        arrow(425, y + 23, 460, y + 23)
        box(460, y, 230, 46, "采集 150ms 麦克风\n量峰值", "#e6fcf5")
        arrow(wpx / 2, y + 46, wpx / 2, y + 74)

        y2 = y + 74
        box(60, y2, 300, 44, "峰值 ≤ 阈值  →  开关在「静音」\n松开快捷键，停止语音输入",
            "#ffe3e3")
        box(wpx - 360, y2, 300, 44, "峰值 > 阈值  →  开关在「麦克风开」\n按住快捷键，开始语音输入",
            "#d3f9d8")
        c.create_line(wpx / 2, y2 - 4, wpx / 2, y2 + 6, fill="#666666")
        arrow(wpx / 2, y2 + 6, 210, y2 + 6)
        arrow(wpx / 2, y2 + 6, wpx - 210, y2 + 6)

        y3 = y2 + 66
        c.create_text(wpx / 2, y3 + 14,
                      text="实测：静音 ≈ 1　|　麦克风开(安静) 200~300　|　说话 600~7400",
                      font=("Microsoft YaHei UI", 10, "bold"), fill="#1864ab")
        c.create_text(wpx / 2, y3 + 40,
                      text="状态是「量」出来的而不是「数」出来的 —— 拔插耳机、漏事件、重复事件都不会导致开/关反向。",
                      font=("Microsoft YaHei UI", 9), fill="#555555")

    # ------------------------------------------------------------ 状态刷新
    def _refresh_state(self, s):
        muted = s["mic_muted"]
        self.lbl_switch.configure(
            text="静音" if muted else "麦克风开",
            bg="#9e9e9e" if muted else "#2f9e44")
        if s["running"]:
            self.lbl_voice.configure(
                text="语音输入：进行中" if s["voice_on"] else "语音输入：待命",
                bg="#d3f9d8" if s["voice_on"] else "#eeeeee")
        else:
            self.lbl_voice.configure(text="语音输入：未启动", bg="#e8e8e8")
        self.lbl_device.configure(
            text="设备：%s" % (s["device"] or "未连接"),
            foreground="#c92a2a" if not s["device"] or s["device"] == "未找到设备"
            else "#2b8a3e")
        self.lbl_stats.configure(
            text="峰值 %d    RMS %.1f    切换 %d 次"
            % (s["peak"], s["rms"], s["events"]))
        self.btn_start.configure(state="disabled" if s["running"] else "normal")
        self.btn_stop.configure(state="normal" if s["running"] else "disabled")
        if s["error"]:
            self.lbl_hint.configure(text="⚠ %s" % s["error"], foreground="#c92a2a")
        self._draw_level()
        self._update_tray(s)

    def _update_tray(self, s):
        """让托盘图标的配色与提示跟随状态（只在变化时调用 Shell_NotifyIcon）"""
        if self._tray is None or not self._tray.available:
            return
        if s.get("error"):
            state = "error"
        elif not s["running"]:
            state = "idle"
        elif s["voice_on"]:
            state = "live"
        else:
            state = "ready"
        tip = "%s\n状态：%s\n麦克风：%s" % (
            APP_TITLE,
            "未启动" if not s["running"] else ("录音中" if s["voice_on"] else "待命"),
            "静音" if s["mic_muted"] else "开")
        if getattr(self, "_tray_last", None) == (state, tip):
            return
        self._tray_last = (state, tip)
        try:
            self._tray.set_state(state, tip)
        except Exception:
            pass

    def _refresh_level(self, st):
        self.bridge.last_peak = st["peak"]
        self.bridge.last_rms = st["rms"]
        self._refresh_state(self.bridge.snapshot())

    # ------------------------------------------------------------ 动作
    def _refresh_device_lists(self):
        try:
            cands = core.list_hid_candidates(only_consumer=True)
        except Exception:
            cands = []
        self._hid_map = {}
        vals = ["（自动：按 VID/PID %s 查找）" % self.cfg.get("vid_pid", "")]
        for c in cands:
            label = "VID_%04X&PID_%04X  %s  [%s]" % (
                c["vid"], c["pid"], c.get("product") or "(无名称)",
                c["instance"].split("\\")[0])
            vals.append(label)
            self._hid_map[label] = c["path"]
        self.cb_hid.configure(values=vals)
        cur = self.cfg.get("hid_path") or ""
        found = vals[0]
        for label, path in self._hid_map.items():
            if path == cur:
                found = label
        self.var_hid.set(found)

        devs = core.list_capture_devices()
        self._cap_map = {}
        cvals = []
        for idx, name in devs:
            label = name if idx < 0 else "%s  (#%d)" % (name, idx)
            cvals.append(label)
            self._cap_map[label] = "" if idx < 0 else name
        self.cb_cap.configure(values=cvals)
        cur = self.cfg.get("capture_device") or ""
        found = cvals[0] if cvals else ""
        for label, name in self._cap_map.items():
            if name == cur:
                found = label
        self.var_cap.set(found)

    def save_config(self):
        try:
            keys = core.parse_hotkey(self.var_hotkey.get())
        except Exception as exc:
            messagebox.showerror("快捷键无效", str(exc))
            return
        self.cfg["hotkey"] = self.var_hotkey.get().strip().lower()
        self.cfg["mute_peak_threshold"] = int(self.var_thresh.get())
        self.cfg["probe_ms"] = int(self.var_probe.get())
        self.cfg["settle_ms"] = int(self.var_settle.get())
        self.cfg["debounce_ms"] = int(self.var_debounce.get())
        self.cfg["hid_path"] = self._hid_map.get(self.var_hid.get(), "")
        self.cfg["capture_device"] = self._cap_map.get(self.var_cap.get(), "")
        self.cfg["start_dictating"] = bool(self.var_startdict.get())
        self.cfg["autostart_bridge"] = bool(self.var_autobridge.get())
        self.cfg["close_to_tray"] = bool(self.var_tray.get())
        core.save_config(self.cfg)
        self.bridge.cfg = self.cfg
        self.bridge._probe = core.MicProbe(self.cfg["probe_ms"],
                                           self.cfg["mute_peak_threshold"],
                                           self.cfg["capture_device"])

        want = bool(self.var_autostart.get())
        have = core.autostart_installed()
        if want and not have:
            ok, info = core.install_autostart()
            if not ok:
                messagebox.showerror("开机自启失败", str(info))
        elif have and not want:
            core.remove_autostart()

        self._append_log("设置已保存（快捷键 %s，阈值 %d）"
                         % (self.cfg["hotkey"], self.cfg["mute_peak_threshold"]))
        self._draw_level()
        messagebox.showinfo("已保存", "设置已保存。若桥接正在运行，请停止后重新开始以生效。")

    def reset_config(self):
        if not messagebox.askyesno("恢复默认", "确定把所有设置恢复为默认值？"):
            return
        self.cfg = dict(core.DEFAULT_CONFIG)
        self.var_hotkey.set(self.cfg["hotkey"])
        self.var_thresh.set(self.cfg["mute_peak_threshold"])
        self.var_probe.set(self.cfg["probe_ms"])
        self.var_settle.set(self.cfg["settle_ms"])
        self.var_debounce.set(self.cfg["debounce_ms"])
        self.var_startdict.set(self.cfg["start_dictating"])
        self.var_autobridge.set(self.cfg["autostart_bridge"])
        self.var_tray.set(self.cfg.get("close_to_tray", True))
        self._refresh_device_lists()
        self.bridge.cfg = self.cfg

    def start_bridge(self):
        guard = core.acquire_single_instance()
        if guard == "exists":
            messagebox.showwarning(
                "已经有一个在运行",
                "已经有一个桥接程序在运行（可能是命令行版本，或本程序的另一个实例）。\n"
                "同时运行两个会导致按键被重复触发，所以本次没有启动。")
            return
        self._mutex = guard
        self.bridge.cfg = self.cfg
        ok, msg = self.bridge.start()
        if not ok:
            self._append_log("启动失败：%s" % msg)
            messagebox.showerror("启动失败", msg)
            if self._mutex:
                core.kernel32.CloseHandle(self._mutex)
                self._mutex = None
        else:
            self._append_log("桥接已启动。")
        self._refresh_state(self.bridge.snapshot())

    def stop_bridge(self):
        self.bridge.stop()
        if self._mutex:
            try:
                core.kernel32.CloseHandle(self._mutex)
            except Exception:
                pass
            self._mutex = None
        self._refresh_state(self.bridge.snapshot())

    def probe_once(self):
        try:
            st, muted = self.bridge.probe_level()
        except OSError as exc:
            messagebox.showerror("采集失败", str(exc))
            return
        self._append_log("单次探测：峰值=%d RMS=%.1f → %s"
                         % (st["peak"], st["rms"],
                            "静音" if muted else "麦克风开"))
        self._refresh_state(self.bridge.snapshot())

    def toggle_live(self):
        if self._live["on"]:
            self._live["on"] = False
            self.btn_live.configure(text="实时监测 10 秒")
            return
        self._live["on"] = True
        self._live["until"] = time.time() + 10
        self.btn_live.configure(text="停止监测")
        self._append_log("开始实时电平监测（10 秒），可同时拨动开关观察数值变化。")
        threading.Thread(target=self._live_worker, daemon=True).start()

    def _live_worker(self):
        while self._live["on"] and time.time() < self._live["until"]:
            try:
                st, _ = self.bridge.probe_level()
                self.root.after(0, self._refresh_level, st)
            except OSError:
                break
            time.sleep(0.25)
        self._live["on"] = False
        try:
            self.root.after(0, lambda: self.btn_live.configure(text="实时监测 10 秒"))
        except Exception:
            pass

    def emergency_release(self):
        ok = self.bridge.release_keys()
        messagebox.showinfo("紧急松开",
                            "已尝试松开所有快捷键。" if ok else "松开失败，请手动按一下左 Ctrl 和左 Win 键。")

    def on_close(self):
        """点窗口的关闭按钮：按设置决定"收进托盘"还是"直接退出" """
        if (self.cfg.get("close_to_tray", True)
                and self._tray is not None and self._tray.available):
            self._hide_to_tray()
            return
        self._quit_app(confirm=True)

    def _quit_app(self, confirm=False):
        """真正退出：停桥接、松按键、撤掉托盘图标"""
        if confirm:
            try:
                if self.bridge.running and not messagebox.askokcancel(
                        "退出", "桥接正在运行，退出会停止语音输入控制。确定退出？"):
                    return
            except Exception:
                pass
        self._alive = False
        self._live["on"] = False
        try:
            self.bridge.stop()
        except Exception:
            pass
        try:
            self.bridge.release_keys()
        except Exception:
            pass
        if self._tray is not None:
            try:
                self._tray.remove()
            except Exception:
                pass
            self._tray = None
        if self._mutex:
            try:
                core.kernel32.CloseHandle(self._mutex)
            except Exception:
                pass
            self._mutex = None
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
        try:
            self.root.destroy()
        except Exception:
            pass


def cli_mode(argv):
    """把命令行参数转交命令行版执行，并用弹窗显示结果。

    打包成窗口程序后没有控制台，所以结果用弹窗展示，
    同时写入数据目录下的 cli_output.txt 备用。
    设置环境变量 G2VB_NO_DIALOG=1 可只写文件不弹窗（自动化测试用）。
    """
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            import g2_voice_bridge
            g2_voice_bridge.main(argv)
        except SystemExit:
            pass
        except Exception as exc:  # pragma: no cover
            buf.write("执行出错：%r\n" % (exc,))
    text = buf.getvalue() or "(没有输出)"

    path = os.path.join(core.data_dir(), "cli_output.txt")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except Exception:
        path = "(写文件失败)"

    try:
        if sys.stdout is not None:
            sys.stdout.write(text)
    except Exception:
        pass

    if os.environ.get("G2VB_NO_DIALOG"):
        return 0
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("运行结果（也保存在 %s）" % path, text)
        root.destroy()
    except Exception:
        pass
    return 0


def selftest():
    """无界面自检：确认打包后的 exe 仍能加载 tkinter、枚举设备、采集麦克风。
    结果写入 %TEMP%\\g2vb_selftest.txt 与数据目录下的 selftest.txt。"""
    lines = []
    lines.append("frozen=%s" % core.is_frozen())
    lines.append("executable=%s" % sys.executable)
    lines.append("data_dir=%s" % core.data_dir())
    try:
        import tkinter as _tk
        r = _tk.Tk()
        r.withdraw()
        r.update()
        r.destroy()
        lines.append("tkinter=OK")
    except Exception as exc:
        lines.append("tkinter=FAIL %r" % exc)
    try:
        cands = core.list_hid_candidates(only_consumer=True)
        lines.append("hid_candidates=%d" % len(cands))
        for c in cands:
            lines.append("   %04X:%04X  %s" % (c["vid"], c["pid"],
                                               c.get("product") or "?"))
    except Exception as exc:
        lines.append("hid_list=FAIL %r" % exc)
    cfg = core.load_config()
    lines.append("hotkey=%s threshold=%s" % (cfg.get("hotkey"),
                                             cfg.get("mute_peak_threshold")))
    try:
        h, inst = core.open_device(cfg.get("vid_pid") or None,
                                   cfg.get("hid_path") or None)
        lines.append("hid_open=%s" % (inst if h else "FAIL"))
        if h:
            core.kernel32.CloseHandle(h)
    except Exception as exc:
        lines.append("hid_open=FAIL %r" % exc)
    try:
        probe = core.MicProbe(cfg["probe_ms"], cfg["mute_peak_threshold"],
                              cfg.get("capture_device", ""))
        st = probe.read()
        lines.append("mic_peak=%d rms=%.1f muted=%s"
                     % (st["peak"], st["rms"], probe.is_muted(st)))
    except Exception as exc:
        lines.append("mic=FAIL %r" % exc)
    lines.append("capture_devices=%s" % [n for _i, n in core.list_capture_devices()])
    try:
        import tray_icon
        p = tray_icon.default_icon_path()
        lines.append("asset_dir=%s" % tray_icon.asset_dir())
        lines.append("icon_file=%s exists=%s" % (p, os.path.exists(p)))
        lines.append("icon_small_size=%d" % tray_icon.small_icon_size())
        lines.append("icon_loaded=%s" % bool(tray_icon.load_icon_file(p)))
    except Exception as exc:
        lines.append("tray=FAIL %r" % exc)
    text = "\n".join(lines)
    for path in (os.path.join(os.environ.get("TEMP", "."), "g2vb_selftest.txt"),
                 os.path.join(core.data_dir(), "selftest.txt")):
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception:
            pass
    return 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    for flag in ("--probe", "--list"):
        if flag in sys.argv:
            return cli_mode([a for a in sys.argv[1:] if a != "--minimized"])
    minimized = "--minimized" in sys.argv
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except Exception:
        pass
    BridgeGUI(root, minimized=minimized)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
