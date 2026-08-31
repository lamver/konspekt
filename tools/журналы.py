"""Хвосты журналов всех профилей Konspekt: чем занята каждая копия.

Отдельный скрипт, потому что консоль Windows живёт в cp1251 и русский
вывод через `python -c` теряется молча, а разбирать поломку по пустому
экрану невозможно.
"""

import sys
from pathlib import Path

хвост = int(sys.argv[1]) if len(sys.argv) > 1 else 40

база = Path.home() / "AppData" / "Roaming"
куски: list[str] = []
for каталог in sorted(база.glob("Konspekt*")):
    журнал = каталог / "konspekt.log"
    if not журнал.is_file():
        continue
    строки = журнал.read_bytes().decode("utf-8", "replace").splitlines()
    куски.append(f"===== {каталог.name} ({len(строки)} строк)")
    куски += строки[-хвост:]

Path("_res.txt").write_text("\n".join(куски), encoding="utf-8")
