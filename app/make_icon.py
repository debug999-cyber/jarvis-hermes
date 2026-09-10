#!/usr/bin/env python3
"""Иконка JARVIS.app (арк-реактор) → папка .iconset для iconutil. Только стандартная библиотека + zlib/struct (PNG пишем сами)."""
import math, struct, sys, zlib
from pathlib import Path


def png(w, h, pix):
    raw = b"".join(b"\x00" + bytes(pix[y]) for y in range(h))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def render(n):
    c = (n - 1) / 2; rows = []
    for y in range(n):
        row = []
        for x in range(n):
            d = math.hypot(x - c, y - c) / (n / 2); a = math.atan2(y - c, x - c)
            r, g, b, al = 8, 14, 24, 255
            if d > 0.98: al = 0
            elif d > 0.92: r, g, b = 0, 160, 200
            elif 0.62 < d < 0.72 and int((a + math.pi) / (2 * math.pi) * 10) % 2 == 0: r, g, b = 0, 220, 255
            elif d < 0.5:
                k = 1 - d / 0.5; r, g, b = int(60 + 195 * k), int(200 + 55 * k), 255
            row += [r, g, b, al]
        rows.append(row)
    return png(n, n, rows)


out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
for size in (16, 32, 128, 256, 512):
    (out / f"icon_{size}x{size}.png").write_bytes(render(size))
    (out / f"icon_{size}x{size}@2x.png").write_bytes(render(size * 2))
