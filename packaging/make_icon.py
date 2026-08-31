"""Сделать .ico для exe, ярлыка и установщика.

Иконку рисует то же самое, что рисует значок в трее: два источника
неизбежно разъезжаются, и в какой-то момент в трее одно, а на ярлыке
другое. Запускается руками при изменении рисунка, результат лежит в
репозитории: сборке не нужен Pillow ради картинки.

    python packaging/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.tray.icon import _make_icon  # noqa: E402

OUT = ROOT / "packaging" / "konspekt.ico"
# Windows берёт разный размер в разных местах: 16 в заголовке окна,
# 32 на рабочем столе, 256 в крупных значках проводника.
SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> int:
    # Консоль Windows по умолчанию в cp1251, и русский вывод её роняет.
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    base = _make_icon(False, size=256)
    base.save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"готово: {OUT.relative_to(ROOT)}, {OUT.stat().st_size} байт")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
