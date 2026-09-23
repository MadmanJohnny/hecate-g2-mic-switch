# -*- coding: utf-8 -*-
"""生成图标资源（全部用标准库，不依赖 Pillow / Ghostscript / ImageMagick）

数据源按优先级自动选择：
    1. assets/app-ICON.png    位图设计稿（推荐）—— 直接解码后按面积平均降采样
    2. assets/icon-source.eps 矢量设计稿 —— 内置迷你 PostScript 解释器光栅化
    3. 都没有 —— 退回用 src/tray_icon.py 里代码画的图形

生成：
    assets/app.ico                 exe 与系统托盘图标（16/24/32/48/64/128/256）
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

PNG_SRC = os.path.join(ROOT, "assets", "app-ICON.png")
EPS_SRC = os.path.join(ROOT, "assets", "icon-source.eps")
ICO_PATH = os.path.join(ROOT, "assets", "app.ico")
IMG_DIR = os.path.join(ROOT, "docs", "images")
SIZES = [16, 24, 32, 48, 64, 128, 256]


# --------------------------------------------------------------------------
# PNG 解码（8 位，非隔行，灰度/RGB/调色板/灰度+Alpha/RGBA 都支持）
# --------------------------------------------------------------------------
def decode_png(path):
    """返回 (宽, 高, RGBA bytes)"""
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 文件: %s" % path)
    pos, idat = 8, []
    w = h = bd = ct = il = None
    plte = None
    while pos < len(data):
        ln = struct.unpack_from(">I", data, pos)[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            w, h, bd, ct, _cm, _fm, il = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"PLTE":
            plte = body
        elif tag == b"IEND":
            break
        pos += 12 + ln

    if bd != 8:
        raise ValueError("只支持 8 位色深，当前 %d" % bd)
    if il != 0:
        raise ValueError("不支持隔行（Adam7）PNG")
    nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    raw = zlib.decompress(b"".join(idat))
    stride = w * nch
    out = bytearray(stride * h)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ft == 1:                       # Sub
            for i in range(nch, stride):
                line[i] = (line[i] + line[i - nch]) & 0xFF
        elif ft == 2:                     # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:                     # Average
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:                     # Paeth
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                b = prev[i]
                c = prev[i - nch] if i >= nch else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line

    if ct == 6:
        rgba = out
    else:
        rgba = bytearray(w * h * 4)
        for i in range(w * h):
            if ct == 0:
                g = out[i]
                rgba[i * 4:i * 4 + 4] = bytes((g, g, g, 255))
            elif ct == 4:
                g, a = out[i * 2], out[i * 2 + 1]
                rgba[i * 4:i * 4 + 4] = bytes((g, g, g, a))
            elif ct == 2:
                rgba[i * 4:i * 4 + 4] = bytes(out[i * 3:i * 3 + 3]) + b"\xff"
            elif ct == 3:
                idx = out[i]
                rgba[i * 4:i * 4 + 4] = bytes(
                    (plte[idx * 3], plte[idx * 3 + 1], plte[idx * 3 + 2], 255))
    return w, h, rgba


def alpha_bbox(rgba, w, h, thresh=8):
    """有内容区域的包围盒 (x0, y0, x1, y1)"""
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        base = y * w * 4
        row_has = False
        for x in range(w):
            if rgba[base + x * 4 + 3] > thresh:
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
                row_has = True
        if row_has:
            if y < y0:
                y0 = y
            y1 = y
    if x1 < 0:
        return 0, 0, w - 1, h - 1
    return x0, y0, x1, y1


# --------------------------------------------------------------------------
# 迷你 PostScript 解释器（矢量稿 *.eps 用，只在没有 PNG 源时才走这条路）
# --------------------------------------------------------------------------
def _flatten_cubic(p0, p1, p2, p3, steps=16):
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1 - t
        out.append((mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0]
                    + 3 * mt * t * t * p2[0] + t ** 3 * p3[0],
                    mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1]
                    + 3 * mt * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def parse_eps(path):
    """返回 (bbox, [(rgb, [子路径...]), ...])，坐标为 PostScript 用户坐标"""
    with open(path, "r", encoding="latin-1") as fh:
        text = fh.read()
    bbox = (0.0, 0.0, 762.0, 819.0)
    for line in text.splitlines():
        if line.startswith("%%BoundingBox:"):
            bbox = tuple(float(v) for v in line.split(":")[1].split()[:4])
            break
    idx = text.find("%%EndPageSetup")
    body = text[idx + len("%%EndPageSetup"):] if idx >= 0 else text
    body = body.split("%%Trailer")[0]
    tokens = body.replace("\r", " ").replace("\n", " ").split()

    stack, gstack, subpaths, cur, fills = [], [], [], None, []
    state = {"rgb": (0.0, 0.0, 0.0)}
    for tok in tokens:
        try:
            stack.append(float(tok))
            continue
        except ValueError:
            pass
        if tok == "q":
            gstack.append(dict(state))
        elif tok == "Q":
            if gstack:
                state = gstack.pop()
        elif tok == "rg" and len(stack) >= 3:
            state["rgb"] = (stack[-3], stack[-2], stack[-1])
            stack = stack[:-3]
        elif tok == "g" and stack:
            state["rgb"] = (stack[-1],) * 3
            stack = stack[:-1]
        elif tok == "m" and len(stack) >= 2:
            cur = [(stack[-2], stack[-1])]
            subpaths.append(cur)
            stack = stack[:-2]
        elif tok == "l" and len(stack) >= 2 and cur is not None:
            cur.append((stack[-2], stack[-1]))
            stack = stack[:-2]
        elif tok == "c" and len(stack) >= 6 and cur is not None:
            cur.extend(_flatten_cubic(cur[-1], (stack[-6], stack[-5]),
                                      (stack[-4], stack[-3]),
                                      (stack[-2], stack[-1])))
            stack = stack[:-6]
        elif tok == "h":
            cur = None
        elif tok in ("f", "F", "f*"):
            polys = [p for p in subpaths if len(p) >= 3]
            if polys:
                fills.append((state["rgb"], polys))
            subpaths, cur, stack = [], None, []
        elif tok in ("n", "N", "S"):
            subpaths, cur, stack = [], None, []
        elif tok in ("re", "rectclip"):
            stack = []
    return bbox, fills


def rasterize_eps(bbox, fills, size):
    x0, y0, x1, y1 = bbox
    aw, ah = x1 - x0, y1 - y0
    scale = size * 0.96 / max(aw, ah)
    offx = (size - aw * scale) / 2.0
    offy = (size - ah * scale) / 2.0
    buf = bytearray(size * size * 4)

    def tx(p):
        return (offx + (p[0] - x0) * scale, offy + (y1 - p[1]) * scale)

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
            wind, base = 0, py * size * 4
            for i in range(len(xs) - 1):
                wind += xs[i][1]
                if wind == 0:
                    continue
                pa = max(0, int(math.ceil(xs[i][0] - 0.5)))
                pb = min(size - 1, int(math.floor(xs[i + 1][0] - 0.5)))
                for px in range(pa, pb + 1):
                    o = base + px * 4
                    buf[o], buf[o + 1], buf[o + 2], buf[o + 3] = r, g, b, 255
    return buf


def render_fallback(size):
    """最后退路：用代码画的图形"""
    live = ti.COLORS["live"]
    rgba = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            u, v = (x + 0.5) / size, (y + 0.5) / size
            dx, dy = u - 0.5, v - 0.5
            if (dx * dx + dy * dy) ** 0.5 > 0.47:
                continue
            o = (y * size + x) * 4
            if ti._in_headset(u, v):
                rgba[o:o + 4] = b"\xff\xff\xff\xff"
            else:
                rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3] = \
                    live[0], live[1], live[2], 255
    return rgba


# --------------------------------------------------------------------------
# 缩放 / 封装
# --------------------------------------------------------------------------
def resize_area(buf, src, dst):
    """面积平均缩放（RGBA，比例任意）"""
    if src == dst:
        return buf
    out = bytearray(dst * dst * 4)
    k = src / float(dst)
    for y in range(dst):
        sy0, sy1 = int(y * k), max(int(y * k) + 1, int((y + 1) * k))
        sy1 = min(sy1, src)
        for x in range(dst):
            sx0, sx1 = int(x * k), max(int(x * k) + 1, int((x + 1) * k))
            sx1 = min(sx1, src)
            acc = [0, 0, 0, 0]
            cnt = 0
            for sy in range(sy0, sy1):
                base = (sy * src + sx0) * 4
                for sx in range(sx0, sx1):
                    o = base + (sx - sx0) * 4
                    acc[0] += buf[o]
                    acc[1] += buf[o + 1]
                    acc[2] += buf[o + 2]
                    acc[3] += buf[o + 3]
                    cnt += 1
            if cnt:
                o = (y * dst + x) * 4
                out[o] = acc[0] // cnt
                out[o + 1] = acc[1] // cnt
                out[o + 2] = acc[2] // cnt
                out[o + 3] = acc[3] // cnt
    return out


def halve(buf, n):
    m = n // 2
    out = bytearray(m * m * 4)
    for y in range(m):
        r0, r1 = (2 * y) * n * 4, (2 * y + 1) * n * 4
        o = y * m * 4
        for x in range(m):
            a0 = r0 + 8 * x
            b0 = r1 + 8 * x
            for k in range(4):
                out[o + 4 * x + k] = (buf[a0 + k] + buf[a0 + 4 + k]
                                      + buf[b0 + k] + buf[b0 + 4 + k]) // 4
    return out


def build_ico(images):
    """images: [(size, RGBA 自上而下)] -> .ico 字节

    注意：ICO 内的 DIB 是 **BGRA** 且自下而上，内部一律用 RGBA，这里必须换序，
    否则红蓝通道会对调。
    """
    blobs = []
    for size, rgba in images:
        rows = []
        for y in range(size - 1, -1, -1):
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
    entries = data = b""
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
    master = master_n = None

    if os.path.exists(PNG_SRC):
        print("数据源: %s" % PNG_SRC)
        w, h, rgba = decode_png(PNG_SRC)
        print("  解码完成 %dx%d RGBA" % (w, h))
        x0, y0, x1, y1 = alpha_bbox(rgba, w, h)
        cw, chh = x1 - x0 + 1, y1 - y0 + 1
        print("  非透明区域: %d x %d（占画布 %.0f%% x %.0f%%）"
              % (cw, chh, 100.0 * cw / w, 100.0 * chh / h))
        master, master_n = rgba, w
    elif os.path.exists(EPS_SRC):
        print("数据源: %s" % EPS_SRC)
        bbox, fills = parse_eps(EPS_SRC)
        print("  矢量填充块: %d 个" % len(fills))
        master_n = 1024
        master = rasterize_eps(bbox, fills, master_n)
    else:
        print("未找到设计稿，改用代码绘制的图形")
        master_n = 512
        master = render_fallback(master_n)

    # 先面积平均到一个好处理的基准尺寸，再逐级折半
    base = 512 if master_n > 512 else master_n
    levels = {master_n: master}
    cur = resize_area(master, master_n, base) if base != master_n else master
    levels[base] = cur
    n = base
    while n > 16:
        cur = halve(cur, n)
        n //= 2
        levels[n] = cur

    def get(target):
        if target in levels:
            return levels[target]
        src = min(k for k in levels if k >= target)
        return resize_area(levels[src], src, target)

    images = [(s, get(s)) for s in SIZES]

    os.makedirs(os.path.dirname(ICO_PATH), exist_ok=True)
    with open(ICO_PATH, "wb") as fh:
        fh.write(build_ico(images))
    print("已生成 %s（%d 字节，%d 个尺寸：%s）"
          % (ICO_PATH, os.path.getsize(ICO_PATH), len(SIZES),
             "/".join(str(s) for s in SIZES)))

    os.makedirs(IMG_DIR, exist_ok=True)
    data, cw, ch = compose([(s, img) for s, img in images if s <= 128])
    with open(os.path.join(IMG_DIR, "icon-preview.png"), "wb") as fh:
        fh.write(data)
    print("已生成 icon-preview.png (%d x %d)" % (cw, ch))


if __name__ == "__main__":
    main()
