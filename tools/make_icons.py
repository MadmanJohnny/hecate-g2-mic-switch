# -*- coding: utf-8 -*-
"""生成图标资源。

优先用 assets/icon-source.eps（用户的矢量设计稿）光栅化；
如果该文件不存在，就退回用 src/tray_icon.py 里那套代码画的图形。

EPS 是 cairo 导出的纯文本 PostScript，只用到 moveto / lineto / curveto /
closepath / fill / setrgbcolor / gsave / grestore / rectclip 这几个操作符，
所以这里内置了一个够用的迷你解释器，不依赖 Ghostscript / ImageMagick。

生成：
    assets/app.ico                 exe 与托盘用的图标（多尺寸）
    docs/images/icon-preview.png   各尺寸预览图

用法：
    python tools/make_icons.py
"""
import math
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import tray_icon as ti  # noqa: E402

EPS_PATH = os.path.join(ROOT, "assets", "icon-source.eps")
ICO_PATH = os.path.join(ROOT, "assets", "app.ico")
IMG_DIR = os.path.join(ROOT, "docs", "images")
MASTER = 2048          # 母版分辨率
FILL = 0.96            # 图形占画布的比例（留点边距）


# --------------------------------------------------------------------------
# 迷你 PostScript 解释器：把 EPS 里的填充路径取出来
# --------------------------------------------------------------------------
def _flatten_cubic(p0, p1, p2, p3, steps=16):
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1 - t
        x = (mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0]
             + 3 * mt * t * t * p2[0] + t ** 3 * p3[0])
        y = (mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1]
             + 3 * mt * t * t * p2[1] + t ** 3 * p3[1])
        out.append((x, y))
    return out


def parse_eps(path):
    """返回 (bbox, [(rgb, [子路径...]), ...])，坐标仍是 PostScript 用户坐标"""
    with open(path, "r", encoding="latin-1") as fh:
        text = fh.read()

    bbox = (0.0, 0.0, 762.0, 819.0)
    for line in text.splitlines():
        if line.startswith("%%BoundingBox:"):
            parts = line.split(":")[1].split()
            bbox = tuple(float(v) for v in parts[:4])
            break

    # 只取页面正文
    marker = "%%EndPageSetup"
    idx = text.find(marker)
    body = text[idx + len(marker):] if idx >= 0 else text
    body = body.split("%%Trailer")[0]
    tokens = body.replace("\r", " ").replace("\n", " ").split()

    stack = []
    defstate = {"rgb": (0.0, 0.0, 0.0), "gray": 0.0, "linewidth": 1.0}
    state = dict(defstate)
    gstack = []
    subpaths = []
    cur = None
    fills = []

    def num(tok):
        try:
            return float(tok)
        except ValueError:
            return None

    for tok in tokens:
        v = num(tok)
        if v is not None:
            stack.append(v)
            continue
        if tok == "q":
            gstack.append(dict(state))
        elif tok == "Q":
            if gstack:
                state = gstack.pop()
        elif tok == "rg" and len(stack) >= 3:
            r, g, b = stack[-3], stack[-2], stack[-1]   # PostScript: red green blue
            state["rgb"] = (r, g, b)
            stack = stack[:-3]
        elif tok == "g" and stack:
            state["gray"] = stack[-1]
            state["rgb"] = (stack[-1],) * 3
            stack = stack[:-1]
        elif tok == "w" and stack:
            state["linewidth"] = stack[-1]
            stack = stack[:-1]
        elif tok == "m" and len(stack) >= 2:
            cur = [(stack[-2], stack[-1])]
            subpaths.append(cur)
            stack = stack[:-2]
        elif tok == "l" and len(stack) >= 2 and cur is not None:
            cur.append((stack[-2], stack[-1]))
            stack = stack[:-2]
        elif tok == "c" and len(stack) >= 6 and cur is not None:
            p0 = cur[-1]
            p1 = (stack[-6], stack[-5])
            p2 = (stack[-4], stack[-3])
            p3 = (stack[-2], stack[-1])
            cur.extend(_flatten_cubic(p0, p1, p2, p3))
            stack = stack[:-6]
        elif tok == "v" and len(stack) >= 4 and cur is not None:
            p0 = cur[-1]
            p2 = (stack[-4], stack[-3])
            p3 = (stack[-2], stack[-1])
            cur.extend(_flatten_cubic(p0, p0, p2, p3))
            stack = stack[:-4]
        elif tok == "y" and len(stack) >= 4 and cur is not None:
            p0 = cur[-1]
            p1 = (stack[-4], stack[-3])
            p3 = (stack[-2], stack[-1])
            cur.extend(_flatten_cubic(p0, p1, p3, p3))
            stack = stack[:-4]
        elif tok == "h":
            # 子路径已经存在于 subpaths 里，这里只是闭合它；
            # fill 时按 pts[(i+1) % n] 连回起点，等价于 closepath
            cur = None
        elif tok in ("f", "F", "f*"):
            polys = [p for p in subpaths if len(p) >= 3]
            if polys:
                fills.append((state["rgb"], polys))
            subpaths, cur = [], None
            stack = []
        elif tok in ("n", "N"):
            subpaths, cur = [], None
            stack = []
        elif tok == "S":
            subpaths, cur = [], None
            stack = []
        elif tok in ("re", "rectclip"):
            stack = []
        else:
            # 其余操作符（save/restore/showpage/end/dict/begin 等）忽略
            if tok in ("showpage", "save", "restore", "end", "begin", "dict"):
                stack = []
    return bbox, fills


# --------------------------------------------------------------------------
# 把设计稿中间的「圆形播放按钮」换成扁平麦克风
#   EPS 里播放按钮由三组填充构成：橙红圆盘+三角、深蓝圆环、深蓝三角描边。
#   它们的包围盒都落在下面这个区域内，据此整组剔除，再补上新画的麦克风。
# --------------------------------------------------------------------------
PLAY_BOX = (200.0, 225.0, 570.0, 590.0)      # 播放按钮所在区域（EPS 用户坐标）
NAVY = (0.0941176, 0.145098, 0.184314)
SALMON = (1.0, 0.533333, 0.458824)


def _mic_body(cx, cy, grow, steps=28):
    """话筒本体：竖直胶囊"""
    r = 50 + grow
    y0, y1 = cy - 12 - grow, cy + 168 + grow
    pts = []
    for i in range(steps + 1):                       # 下半圆
        a = math.pi + math.pi * i / steps
        pts.append((cx + r * math.cos(a), (y0 + r) + r * math.sin(a)))
    for i in range(steps + 1):                       # 上半圆
        a = math.pi * i / steps
        pts.append((cx + r * math.cos(a), (y1 - r) + r * math.sin(a)))
    return pts


def _mic_arc(cx, cy, grow, steps=30):
    """话筒支架：下方的一段圆环（要够粗，否则小尺寸下看不见）"""
    ro, ri = 126 + grow, 78 - grow
    ccy = cy + 48
    a0, a1 = math.radians(200), math.radians(340)
    pts = []
    for i in range(steps + 1):
        a = a0 + (a1 - a0) * i / steps
        pts.append((cx + ro * math.cos(a), ccy + ro * math.sin(a)))
    for i in range(steps, -1, -1):
        a = a0 + (a1 - a0) * i / steps
        pts.append((cx + ri * math.cos(a), ccy + ri * math.sin(a)))
    return pts


def _rect(cx, _cy, hw, y0, y1, grow=0.0):
    return [(cx - hw - grow, y0 - grow), (cx + hw + grow, y0 - grow),
            (cx + hw + grow, y1 + grow), (cx - hw - grow, y1 + grow)]


def replace_play_button(fills, cx=None, cy=None):
    """剔除播放按钮的图形，换成深蓝描边 + 橙红填充的扁平麦克风"""
    kept, removed_box = [], []
    for rgb, polys in fills:
        xs = [p[0] for poly in polys for p in poly]
        ys = [p[1] for poly in polys for p in poly]
        if xs and min(xs) >= PLAY_BOX[0] and max(xs) <= PLAY_BOX[2] \
                and min(ys) >= PLAY_BOX[1] and max(ys) <= PLAY_BOX[3]:
            removed_box.append((min(xs), min(ys), max(xs), max(ys)))
            continue
        kept.append((rgb, polys))

    if removed_box:
        if cx is None:
            cx = (min(b[0] for b in removed_box) + max(b[2] for b in removed_box)) / 2.0
        if cy is None:
            cy = (min(b[1] for b in removed_box) + max(b[3] for b in removed_box)) / 2.0
    cx = 381.6 if cx is None else cx
    cy = 403.9 if cy is None else cy
    print("  移除播放按钮图形 %d 组，在 (%.1f, %.1f) 处换成扁平麦克风"
          % (len(removed_box), cx, cy))

    # 先铺深蓝（放大 9 个单位当描边），再盖橙红填充。
    # 每个部件单独成一组，避免同一填充内多边形的环绕方向互相抵消。
    for grow, color in ((9.0, NAVY), (0.0, SALMON)):
        for poly in (_mic_body(cx, cy, grow), _mic_arc(cx, cy, grow),
                     _rect(cx, cy, 20, cy - 112, cy - 30, grow),
                     _rect(cx, cy, 86, cy - 142, cy - 100, grow)):
            kept.append((color, [poly]))
    return kept


# --------------------------------------------------------------------------
# 光栅化
# --------------------------------------------------------------------------
def rasterize(bbox, fills, size=MASTER):
    x0, y0, x1, y1 = bbox
    aw, ah = x1 - x0, y1 - y0
    scale = size * FILL / max(aw, ah)
    offx = (size - aw * scale) / 2.0
    offy = (size - ah * scale) / 2.0

    buf = bytearray(size * size * 4)          # RGBA，初始全透明

    def tx(p):
        return (offx + (p[0] - x0) * scale,
                offy + (y1 - p[1]) * scale)   # PostScript 的 y 轴朝上，这里翻转

    for rgb, polys in fills:
        r = max(0, min(255, int(round(rgb[0] * 255))))
        g = max(0, min(255, int(round(rgb[1] * 255))))
        b = max(0, min(255, int(round(rgb[2] * 255))))

        edges = []
        for poly in polys:
            pts = [tx(p) for p in poly]
            n = len(pts)
            for i in range(n):
                ax, ay = pts[i]
                bx, by = pts[(i + 1) % n]
                if ay != by:
                    edges.append((ax, ay, bx, by))
        if not edges:
            continue

        ymin = max(0, int(min(min(e[1], e[3]) for e in edges)))
        ymax = min(size - 1, int(max(max(e[1], e[3]) for e in edges)) + 1)
        for py in range(ymin, ymax + 1):
            yc = py + 0.5
            xs = []
            for (ax, ay, bx, by) in edges:
                if (ay <= yc < by) or (by <= yc < ay):
                    t = (yc - ay) / (by - ay)
                    xs.append((ax + t * (bx - ax), 1 if by > ay else -1))
            if len(xs) < 2:
                continue
            xs.sort()
            wind = 0
            base = py * size * 4
            for i in range(len(xs) - 1):
                wind += xs[i][1]
                if wind == 0:
                    continue
                pa = max(0, int(math.ceil(xs[i][0] - 0.5)))
                pb = min(size - 1, int(math.floor(xs[i + 1][0] - 0.5)))
                for px in range(pa, pb + 1):
                    o = base + px * 4
                    buf[o] = r
                    buf[o + 1] = g
                    buf[o + 2] = b
                    buf[o + 3] = 255
    return buf, size


def halve(buf, n):
    """面积平均降采样一半（n 必须是偶数）"""
    m = n // 2
    out = bytearray(m * m * 4)
    for y in range(m):
        r0 = (2 * y) * n * 4
        r1 = (2 * y + 1) * n * 4
        o = y * m * 4
        for x in range(m):
            a0 = r0 + 4 * x * 2
            a1 = a0 + 4
            b0 = r1 + 4 * x * 2
            b1 = b0 + 4
            for k in range(4):
                out[o + 4 * x + k] = (buf[a0 + k] + buf[a1 + k]
                                      + buf[b0 + k] + buf[b1 + k]) // 4
    return out, m


def resize_area(buf, src, dst):
    """任意比例的面积平均（用于 64->48、32->24 这类非整数倍）"""
    out = bytearray(dst * dst * 4)
    k = src / float(dst)
    for y in range(dst):
        sy0 = int(y * k)
        sy1 = max(sy0 + 1, int((y + 1) * k))
        for x in range(dst):
            sx0 = int(x * k)
            sx1 = max(sx0 + 1, int((x + 1) * k))
            acc = [0, 0, 0, 0]
            cnt = 0
            for sy in range(sy0, min(sy1, src)):
                base = sy * src * 4
                for sx in range(sx0, min(sx1, src)):
                    o = base + sx * 4
                    acc[0] += buf[o]
                    acc[1] += buf[o + 1]
                    acc[2] += buf[o + 2]
                    acc[3] += buf[o + 3]
                    cnt += 1
            if cnt:
                o = (y * dst + x) * 4
                for i in range(4):
                    out[o + i] = acc[i] // cnt
    return out


def build_ico(images):
    """images: [(size, RGBA bytes 自上而下)] -> .ico 文件字节

    注意：ICO 内的 DIB 是 **BGRA** 顺序且自下而上，而这里内部一律用 RGBA，
    所以要在这里换序，否则红蓝通道会对调。
    """
    blobs = []
    for size, rgba in images:
        rows = []
        for y in range(size - 1, -1, -1):        # 自下而上
            row = bytearray()
            for x in range(size):
                o = (y * size + x) * 4
                row += bytes((rgba[o + 2], rgba[o + 1], rgba[o], rgba[o + 3]))
            rows.append(bytes(row))
        xor = b"".join(rows)
        and_mask = b"\x00" * (size * 4)
        bih = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                          len(xor), 0, 0, 0, 0)
        blobs.append(bih + xor + and_mask)
    n = len(images)
    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    entries = b""
    data = b""
    for (size, _), blob in zip(images, blobs):
        b = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        data += blob
    return header + entries + data


def png_bytes(rgba, w, h):
    raw = b"".join(b"\x00" + bytes(rgba[y * w * 4:(y + 1) * w * 4])
                   for y in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def compose(items, pad=14, bg=(0xF2, 0xF2, 0xF4)):
    cw = sum(s for s, _ in items) + pad * (len(items) + 1)
    ch = max(s for s, _ in items) + pad * 2
    canvas = bytearray((bytes(bg) + b"\xff") * (cw * ch))
    x = pad
    for size, img in items:
        y0 = pad + (max(s for s, _ in items) - size) // 2
        for row in range(size):
            for col in range(size):
                si = (row * size + col) * 4
                a = img[si + 3] / 255.0
                if a <= 0:
                    continue
                di = ((y0 + row) * cw + (x + col)) * 4
                for k in range(3):
                    canvas[di + k] = int(img[si + k] * a
                                         + canvas[di + k] * (1 - a))
                canvas[di + 3] = 255
        x += size + pad
    return png_bytes(canvas, cw, ch), cw, ch


def main():
    if os.path.exists(EPS_PATH):
        print("数据源: %s" % EPS_PATH)
        bbox, fills = parse_eps(EPS_PATH)
        print("  矢量填充块: %d 个（颜色 %s）"
              % (len(fills), ["#%02X%02X%02X" % tuple(
                  int(round(c * 255)) for c in rgb) for rgb, _ in fills]))
        fills = replace_play_button(fills)
        master, n = rasterize(bbox, fills, MASTER)
        print("  已光栅化 %d x %d" % (n, n))
    else:
        print("未找到 %s，改用代码绘制的图形" % EPS_PATH)
        live = ti.COLORS["live"]
        master = None
        n = 512
        rgba = bytearray(n * n * 4)
        for y in range(n):
            for x in range(n):
                u, v = (x + 0.5) / n, (y + 0.5) / n
                dx, dy = u - 0.5, v - 0.5
                if (dx * dx + dy * dy) ** 0.5 > 0.47:
                    continue
                o = (y * n + x) * 4
                if ti._in_headset(u, v):
                    rgba[o:o + 4] = b"\xff\xff\xff\xff"
                else:
                    rgba[o] = live[0]
                    rgba[o + 1] = live[1]
                    rgba[o + 2] = live[2]
                    rgba[o + 3] = 255
        master = rgba

    # 逐级折半，再按需做非整数倍缩放
    levels = {n: master}
    cur, cn = master, n
    while cn > 16:
        cur, cn = halve(cur, cn)
        levels[cn] = cur

    def get(target):
        # 找到 >= target 的最小已有尺寸，再面积平均
        src = min(k for k in levels if k >= target)
        return resize_area(levels[src], src, target) if src != target \
            else levels[src]

    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [(s, get(s)) for s in sizes]

    os.makedirs(os.path.dirname(ICO_PATH), exist_ok=True)
    with open(ICO_PATH, "wb") as fh:
        fh.write(build_ico(images))
    print("已生成 %s (%d 字节, %d 个尺寸)"
          % (ICO_PATH, os.path.getsize(ICO_PATH), len(sizes)))

    os.makedirs(IMG_DIR, exist_ok=True)
    data, cw, ch = compose([(s, img) for s, img in images if s <= 128])
    with open(os.path.join(IMG_DIR, "icon-preview.png"), "wb") as fh:
        fh.write(data)
    print("已生成 icon-preview.png (%d x %d)" % (cw, ch))


if __name__ == "__main__":
    main()
