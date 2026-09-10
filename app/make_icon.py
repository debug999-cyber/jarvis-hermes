#!/usr/bin/env python3
"""Иконка JARVIS.app в стиле HUD 1.5 («Cinematic Glass»): тёмный стеклянный squircle,
циановые кольца с сегментами и янтарное ядро. Только стандартная библиотека
(PNG пишем сами через zlib/struct), чтобы сборка работала на чистом macOS.

Использование: python3 make_icon.py <папка.iconset>   (дальше iconutil -c icns …)
"""
import math, struct, sys, zlib
from pathlib import Path

CYAN = (55, 230, 255)
CYAN_DIM = (30, 150, 185)
AMBER = (255, 181, 71)
BG_TOP = (18, 26, 40)
BG_BOT = (5, 8, 14)


def png(w, h, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def smooth(edge0, edge1, x):
    t = min(1.0, max(0.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3 - 2 * t)


def ring(d, r, w, aa):
    """Мягкое кольцо радиуса r толщиной w (0..1 покрытие)."""
    return 1 - smooth(w / 2, w / 2 + aa, abs(d - r))


def mix(c1, c2, k):
    return tuple(c1[i] + (c2[i] - c1[i]) * k for i in range(3))


def render(n):
    c = (n - 1) / 2
    R = n / 2
    aa = 1.2 / R                      # ширина сглаживания в нормированных единицах
    rows = []
    for y in range(n):
        row = []
        for x in range(n):
            nx, ny = (x - c) / R, (y - c) / R
            # macOS-squircle (суперэллипс) с полем ~10 % как у системных иконок
            s = (abs(nx) ** 5 + abs(ny) ** 5) ** 0.2
            alpha = 1 - smooth(0.90, 0.90 + aa, s)
            if alpha <= 0:
                row += [0, 0, 0, 0]; continue

            # фон: вертикальный градиент + виньетка
            k = (ny + 1) / 2
            col = mix(BG_TOP, BG_BOT, k)
            d = math.hypot(nx, ny)
            a = math.atan2(ny, nx)

            # свечение ядра
            glow = math.exp(-(d / 0.34) ** 2) * 0.85
            col = mix(col, CYAN, glow * 0.55)

            # внешние тонкие кольца
            cov = 0.0
            cov = max(cov, ring(d, 0.74, 0.012, aa) * 0.55)
            cov = max(cov, ring(d, 0.66, 0.010, aa) * 0.35)
            # сегментированное кольцо (12 сегментов с зазорами)
            seg = int((a + math.pi) / (2 * math.pi) * 12 + 0.5) % 12
            phase = ((a + math.pi) / (2 * math.pi) * 12 + 0.5) % 1
            if 0.12 < phase < 0.88:
                cov = max(cov, ring(d, 0.56, 0.055, aa) * (0.9 if seg % 3 else 0.55))
            # риски-тики снаружи
            tph = ((a + math.pi) / (2 * math.pi) * 36) % 1
            if 0.42 < tph < 0.58:
                cov = max(cov, ring(d, 0.83, 0.05, aa) * 0.45)
            col = mix(col, CYAN_DIM, min(1.0, cov))
            col = mix(col, CYAN, min(1.0, cov) * 0.6)

            # внутреннее кольцо и линза
            inner = ring(d, 0.40, 0.03, aa)
            col = mix(col, CYAN, inner * 0.95)
            lens = 1 - smooth(0.36, 0.36 + aa, d)
            if lens > 0:
                kk = max(0.0, 1 - d / 0.36)
                lens_col = mix((14, 60, 80), CYAN, kk ** 1.6)
                col = mix(col, lens_col, lens)
            # янтарное ядро
            core = math.exp(-(d / 0.11) ** 2)
            col = mix(col, AMBER, min(1.0, core * 1.15))
            hot = math.exp(-(d / 0.045) ** 2)
            col = mix(col, (255, 245, 220), hot)

            # стеклянный блик сверху
            gloss = (1 - smooth(-0.25, 0.05, ny)) * 0.10
            col = mix(col, (255, 255, 255), gloss)
            # тонкая кромка
            edge = ring(s, 0.90, 0.02, aa) * 0.35
            col = mix(col, (120, 200, 230), edge)

            row += [int(col[0]), int(col[1]), int(col[2]), int(255 * alpha)]
        rows.append(row)
    return png(n, n, rows)


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    cache = {}
    for size in (16, 32, 128, 256, 512):
        for px, name in ((size, f"icon_{size}x{size}.png"), (size * 2, f"icon_{size}x{size}@2x.png")):
            if px not in cache:
                cache[px] = render(px)
            (out / name).write_bytes(cache[px])
