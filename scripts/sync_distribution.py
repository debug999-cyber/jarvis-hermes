#!/usr/bin/env python3
"""Держит корневые файлы дистрибутива Hermes в согласии с каноническими: SOUL.md ← config/SOUL.md,
config.yaml ← config/config.jarvis.yaml, distribution.yaml.version ← VERSION.

`hermes profile install github.com/…/jarvis-hermes` читает SOUL.md и config.yaml из КОРНЯ репозитория, а канонические
файлы JARVIS живут в config/ (их использует install.sh). Символические ссылки дистрибутив Hermes отвергает, поэтому копии.
Запуск: python3 scripts/sync_distribution.py [--check]  (тест test_distribution_in_sync следит, чтобы копии не разъехались).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAIRS = ((ROOT / "config" / "SOUL.md", ROOT / "SOUL.md"), (ROOT / "config" / "config.jarvis.yaml", ROOT / "config.yaml"))


def drift() -> list[str]:
    out = [str(dst.relative_to(ROOT)) for src, dst in PAIRS if not dst.exists() or dst.read_text() != src.read_text()]
    version = (ROOT / "VERSION").read_text().strip()
    m = re.search(r"^version:\s*(\S+)", (ROOT / "distribution.yaml").read_text(), re.M)
    if not m or m.group(1) != version:
        out.append("distribution.yaml:version")
    return out


def sync() -> None:
    for src, dst in PAIRS:
        dst.write_text(src.read_text())
    version = (ROOT / "VERSION").read_text().strip()
    d = ROOT / "distribution.yaml"
    d.write_text(re.sub(r"^version:\s*\S+", f"version: {version}", d.read_text(), count=1, flags=re.M))


if __name__ == "__main__":
    if "--check" in sys.argv:
        bad = drift()
        print("ok" if not bad else "разошлись: " + ", ".join(bad))
        sys.exit(1 if bad else 0)
    sync()
    print("synced")
