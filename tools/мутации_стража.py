"""Мутации: ломаем стража выпуска и ждём, что страж_test это заметит.

Страж молчит, когда всё хорошо. Сломанный страж тоже молчит: пропускает
установщик с тревогой, и мы узнаём об этом от человека, у которого
Defender удалил файл. Поэтому каждую его поломку проверяем отдельно.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКА = КОРЕНЬ / "страж_test.py"

МУТАЦИИ = [
    ("tools/страж_выпуска.py",
     '    тревог = int(итог.get("malicious", 0)) + int(итог.get("suspicious", 0))\n',
     '    тревог = int(итог.get("malicious", 0))\n',
     "«подозрительно» не считается тревогой"),
    ("tools/страж_выпуска.py",
     "ДОПУСТИМО = 0\n", "ДОПУСТИМО = 1\n",
     "одна тревога — ровно случай 0.10.1 — считается допустимой"),
    ("tools/страж_выпуска.py",
     "    return проверили > 0 and тревог <= ДОПУСТИМО\n",
     "    return тревог <= ДОПУСТИМО\n",
     "файл, который никто не проверил, считается чистым"),
    ("tools/страж_выпуска.py",
     '    проверили = тревог + int(итог.get("undetected", 0)) + int(итог.get("harmless", 0))\n',
     "    проверили = sum(int(в) for в in итог.values())\n",
     "не открывшие файл движки считаются проверившими"),
    ("tools/страж_выпуска.py",
     '        if вывод.get("category") in ("malicious", "suspicious")\n',
     "",
     "в отчёте перечислены все движки, а не ругающиеся"),
    ("tools/страж_выпуска.py",
     "    if len(аргументы) != 1:\n        print(__doc__)\n        return 2\n",
     "    if len(аргументы) != 1:\n        return 0\n",
     "без файла страж говорит «чисто»"),
    (".github/workflows/build.yml",
     "          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n",
     "",
     "сборка не останавливается на тревоге"),
    (".github/workflows/build.yml",
     "      - name: Страж выпуска — проверка антивирусами до публикации\n"
     "        if: startsWith(github.ref, 'refs/tags/v')\n",
     "      - name: Страж выпуска — проверка антивирусами до публикации\n"
     "        if: startsWith(github.ref, 'refs/tags/v')\n"
     "        continue-on-error: true\n",
     "провал стража проглатывается, выпуск идёт дальше"),
    (".github/workflows/build.yml",
     "        if: startsWith(github.ref, 'refs/tags/v')\n        shell: pwsh\n        env:\n          VT_API_KEY",
     "        if: false\n        shell: pwsh\n        env:\n          VT_API_KEY",
     "страж отключён для выпуска по тегу"),
]


def прогнать() -> int:
    return subprocess.run(
        [sys.executable, str(ПРОВЕРКА)], cwd=КОРЕНЬ, capture_output=True,
        env={**os.environ, "PYTHONUTF8": "1"},
    ).returncode


def main() -> int:
    if прогнать() != 0:
        print("ПЛОХО: проверка падает ещё до мутаций")
        return 1
    поймано = 0
    for имя, было, стало, беда in МУТАЦИИ:
        файл = КОРЕНЬ / имя
        сырой = файл.read_bytes()
        исходник = сырой.decode("utf-8")
        if было not in исходник:
            print(f"[ПЛОХО] {беда}: не нашли что ломать в {имя}")
            continue
        файл.write_bytes(исходник.replace(было, стало, 1).encode("utf-8"))
        try:
            код = прогнать()
        finally:
            файл.write_bytes(сырой)
        if код != 0:
            поймано += 1
            print(f"[OK   ] поймано: {беда}")
        else:
            print(f"[ПЛОХО] НЕ поймано: {беда}")
    print(f"\nмутаций поймано: {поймано} из {len(МУТАЦИИ)}")
    return 0 if поймано == len(МУТАЦИИ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
