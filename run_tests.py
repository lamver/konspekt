"""Прогон всех быстрых проверок одной командой.

    uv run python run_tests.py           все быстрые
    uv run python run_tests.py --slow    вместе с медленными
    uv run python run_tests.py smoke     только те, чьё имя содержит «smoke»

Проверки написаны обычными скриптами, а не под pytest: у каждой свой
`main()` либо просто код на уровне модуля, падающий на `assert`. Из-за
этого pytest здесь и не нужен, а в сборке — лишняя зависимость.

Каждая проверка идёт отдельным процессом. Внутри поднимаются модели,
потоки и временные базы, и упавший тест не должен утаскивать за собой
остальные.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent

# Быстрые: без весов моделей и без сети, укладываются в минуту.
FAST = [
    "smoke_test.py",
    "web_test.py",
    "migration_test.py",
    "models_test.py",
    "router_test.py",
    "langid_test.py",
    "voices_test.py",
    "embedder_test.py",
    "import_test.py",
    "version_test.py",
]

# Медленные: качают веса, поднимают LLM или разбирают длинные записи.
SLOW = [
    "audiofile_test.py",
    "drop_test.py",
    "enroll_test.py",
    "llm_download_test.py",
    "llm_test.py",
    "long_import_test.py",
    "long_meeting_test.py",
    "old_meeting_test.py",
    "people_test.py",
    "restart_test.py",
    "summary_test.py",
    "whisper_test.py",
]


def run(name: str) -> tuple[bool, float, str]:
    path = ROOT / name
    if not path.exists():
        return False, 0.0, "файла нет"

    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    spent = time.monotonic() - started

    if result.returncode == 0:
        return True, spent, ""

    tail = (result.stdout + result.stderr).strip().splitlines()
    return False, spent, "\n".join(tail[-25:])


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    names = FAST + SLOW if "--slow" in sys.argv else list(FAST)
    if args:
        names = [n for n in names if any(a in n for a in args)]

    if not names:
        print("Ни одна проверка не подошла под фильтр")
        return 1

    failed: list[str] = []
    for name in names:
        print(f"  {name} ... ", end="", flush=True)
        ok, spent, details = run(name)
        print(f"{'ок' if ok else 'УПАЛ'} ({spent:.1f}с)")
        if not ok:
            failed.append(name)
            for line in details.splitlines():
                print(f"      {line}")

    print()
    if failed:
        print(f"Упало проверок: {len(failed)} из {len(names)}")
        for name in failed:
            print(f"  - {name}")
        return 1

    print(f"Все проверки пройдены ({len(names)} шт.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
