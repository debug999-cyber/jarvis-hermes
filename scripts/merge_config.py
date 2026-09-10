#!/usr/bin/env python3
"""
Аккуратное слияние YAML-конфигов: merge_config.py <fragment.yaml> <target.yaml>

Правила:
  * словари сливаются рекурсивно;
  * существующие скалярные значения в target НЕ перезаписываются
    (пользовательские настройки важнее наших дефолтов);
  * списки: для plugins.enabled и toolsets.* — объединение без дубликатов,
    для остальных — значение target остаётся, если оно есть;
  * если PyYAML недоступен — пытаемся использовать venv Hermes; если и там нет —
    дописываем фрагмент в конец файла с пометкой (валидно, т.к. YAML верхнего уровня).
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

UNION_LIST_KEYS = {("plugins", "enabled"), ("toolsets",)}


def merge(fragment, target, path=()):
    if isinstance(fragment, dict) and isinstance(target, dict):
        out = dict(target)
        for k, v in fragment.items():
            out[k] = merge(v, target.get(k), path + (k,))
        return out
    if isinstance(fragment, list) and isinstance(target, list):
        if path in UNION_LIST_KEYS or (len(path) >= 1 and path[0] == "toolsets"):
            seen, merged = set(), []
            for item in target + fragment:
                key = repr(item)
                if key not in seen:
                    seen.add(key)
                    merged.append(item)
            return merged
        return target
    if target is None:
        return fragment
    return target  # пользовательское значение побеждает


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    frag_p, tgt_p = Path(sys.argv[1]), Path(sys.argv[2])
    frag_text = frag_p.read_text(encoding="utf-8")
    if yaml is None:
        with tgt_p.open("a", encoding="utf-8") as f:
            f.write("\n# ── JARVIS (appended: PyYAML missing) ──\n" + frag_text)
        print("PyYAML не найден: фрагмент дописан в конец файла.")
        return 0
    fragment = yaml.safe_load(frag_text) or {}
    target = yaml.safe_load(tgt_p.read_text(encoding="utf-8")) if tgt_p.exists() else {}
    target = target or {}
    merged = merge(fragment, target)
    tgt_p.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    print(f"OK: {tgt_p} обновлён ({len(fragment)} верхнеуровневых секций)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
