# -*- coding: utf-8 -*-
"""重新生成图标资源（都是代码算出来的，改了形状就跑一下这个脚本）

生成：
    assets/app.ico                 exe 图标（16/24/32/48/64/128 多尺寸）
    docs/images/icon-preview.png   各尺寸预览图（README 用）
    docs/images/icon-states.png    四种状态配色对照

用法：
    python tools/make_icons.py
"""
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import tray_icon as ti  # noqa: E402


def render_rgba(size, rgb, ss=4):
    """与 tray_icon.make_icon_image 相同的绘制逻辑，直接输出自上而下的 RGBA"""
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
            if ti._in_headset(fx / W, fy / W):
                cov_fg[i] += 1.0
    norm = float(ss * ss)
    out = bytearray(size * size * 4)
    for i in range(size * size):
        a = cov_bg[i] / norm
        f = cov_fg[i] / norm
        if a <= 0:
            continue
        out[i * 4 + 0] = int(r8 * (1 - f) + 255 * f)
        out[i * 4 + 1] = int(g8 * (1 - f) + 255 * f)
        out[i * 4 + 2] = int(b8 * (1 - f) + 255 * f)
        out[i * 4 + 3] = int(round(a * 255))
    return out


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


def compose(items, pad=12, bg=(0xF0, 0xF0, 0xF0)):
    """items: [(size, rgba)]，横向排在一张浅灰底图上"""
    cw = sum(s for s, _ in items) + pad * (len(items) + 1)
    ch = max(s for s, _ in items) + pad * 2
    canvas = bytearray(list(bg) + [255] * 0) * 0
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
    ico_path = os.path.join(ROOT, "assets", "app.ico")
    os.makedirs(os.path.dirname(ico_path), exist_ok=True)
    live = ti.COLORS["live"]
    with open(ico_path, "wb") as fh:
        fh.write(ti.make_ico_file_multi(live))
    print("已生成 %s (%d 字节)" % (ico_path, os.path.getsize(ico_path)))

    img_dir = os.path.join(ROOT, "docs", "images")
    os.makedirs(img_dir, exist_ok=True)

    sizes = [16, 24, 32, 48, 64, 96, 128]
    data, cw, ch = compose([(s, render_rgba(s, live, 4 if s <= 48 else 2))
                            for s in sizes])
    p1 = os.path.join(img_dir, "icon-preview.png")
    with open(p1, "wb") as fh:
        fh.write(data)
    print("已生成 %s (%d x %d)" % (p1, cw, ch))

    data2, cw2, ch2 = compose([(64, render_rgba(64, c, 3))
                               for c in ti.COLORS.values()])
    p2 = os.path.join(img_dir, "icon-states.png")
    with open(p2, "wb") as fh:
        fh.write(data2)
    print("已生成 %s (%d x %d)" % (p2, cw2, ch2))


if __name__ == "__main__":
    main()
