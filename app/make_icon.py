#!/usr/bin/env python3
"""Иконка JARVIS.app в стиле HUD («Minimal Glass»): тёмный squircle, светящаяся
циан-фиолетовая сфера с зерном и две орбиты — как сфера на экране. Только стандартная библиотека
(PNG пишем сами через zlib/struct), чтобы сборка работала на чистом macOS.

Использование: python3 make_icon.py <папка.iconset>   (дальше iconutil -c icns …)
"""
import math, struct, sys, zlib
from pathlib import Path

CYAN_HI = (150, 240, 255)


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
    """Сфера JARVIS (циан → фиолет, зерно, две орбиты) на тёмном стеклянном squircle."""
    import random
    rnd = random.Random(7)
    c = (n - 1) / 2
    R = n / 2
    aa = 1.2 / R
    SR = 0.46                                    # радиус сферы
    grain = [(rnd.random() * 2 - 1, rnd.random() * 2 - 1, rnd.random()) for _ in range(int(1400 * (n / 1024) ** 2))]  # плотность не зависит от размера
    grain = [(x, y, o) for x, y, o in grain if x * x + y * y < 1]
    rows = []
    for y in range(n):
        row = []
        for x in range(n):
            nx, ny = (x - c) / R, (y - c) / R
            s = (abs(nx) ** 5 + abs(ny) ** 5) ** 0.2
            alpha = 1 - smooth(0.90, 0.90 + aa, s)
            if alpha <= 0:
                row += [0, 0, 0, 0]; continue
            k = (ny + 1) / 2
            col = mix((16, 20, 34), (5, 7, 13), k)
            d = math.hypot(nx, ny)
            # ореол
            halo = math.exp(-(d / 0.78) ** 2) * 0.55
            col = mix(col, (70, 120, 255), halo)
            # орбиты (два наклонённых эллипса)
            for rot, tilt in ((0.5, 0.36), (-0.9, 0.55)):
                ca, sa = math.cos(rot), math.sin(rot)
                ex, ey = nx * ca + ny * sa, -nx * sa + ny * ca
                e = math.hypot(ex / (SR * 1.32), ey / (SR * 1.32 * tilt))
                cov = ring(e, 1.0, max(0.006, 1.2 / R) / (SR * 1.32), aa / (SR * 1.32))
                behind = ey < 0 and d < SR  # за сферой — не рисуем
                if not behind:
                    col = mix(col, (170, 200, 255), cov * 0.45)
            # сфера
            inside = 1 - smooth(SR, SR + aa, d)
            if inside > 0:
                lx, ly = nx + SR * 0.35, ny + SR * 0.4       # источник света слева-сверху
                t = min(1.0, math.hypot(lx, ly) / (SR * 1.35))
                body = mix(mix(CYAN_HI, (60, 140, 255), smooth(0, 0.55, t)), (70, 40, 170), smooth(0.45, 1.0, t))
                rim = smooth(SR * 0.82, SR, d)
                body = mix(body, (14, 10, 50), rim * 0.55)
                col = mix(col, body, inside)
            row += [int(col[0]), int(col[1]), int(col[2]), int(255 * alpha)]
        rows.append(row)
    # зерно поверх сферы
    for gx, gy, o in grain:
        px, py = int(c + gx * SR * R), int(c + gy * SR * R)
        if 0 <= px < n and 0 <= py < n:
            i = px * 4
            a = 0.2 + 0.45 * o
            for ch in range(3):
                rows[py][i + ch] = int(rows[py][i + ch] + (255 - rows[py][i + ch]) * a * 0.9)
    return png(n, n, rows)


if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    cache = {}
    for size in (16, 32, 128, 256, 512):
        for px, name in ((size, f"icon_{size}x{size}.png"), (size * 2, f"icon_{size}x{size}@2x.png")):
            if px not in cache:
                cache[px] = render(px)
            (out / name).write_bytes(cache[px])
