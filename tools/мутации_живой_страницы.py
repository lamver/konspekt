"""Мутации: ловит ли живая_страница_test беду с отставшими словарями.

Главная беда, которую она нашла: словари `i18n.js` едут отдельным
файлом, и пока браузер держит старую копию, обращение к новой строке
падает — а падение уносит с собой плашку проверки антивирусами, стоящую
ниже. Поддельный DOM этого не видел.

Проверяем именно это: ломаем словари и защиту, ждём падения проверки.
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

СТРАНИЦА = Path("docs/index.html")
СЛОВАРИ = Path("docs/i18n.js")

МУТАЦИИ = [
    (
        "словари отстали, а защиты нет (ровно найденная беда)",
        [
            (СТРАНИЦА, "const подпись = d.releaseVersion || 'Version %s';",
             "const подпись = d.releaseVersion;"),
            (СЛОВАРИ, "    releaseVersion: 'Версия %s',", ""),
        ],
    ),
    (
        "падение строки версии гасит плашку антивирусов",
        [
            (СТРАНИЦА, "    try {\n      показатьВыпуск(релиз, lang);\n    } catch (e) {\n      /* номер версии не показался, остальное работает */\n    }",
             "    показатьВыпуск(релиз, lang);\n    null.сломать();"),
        ],
    ),
    (
        "строка версии не показывается вовсе",
        [
            (СТРАНИЦА, "  узел.append(версия, document.createTextNode(' · '), ссылка);\n  узел.hidden = false;",
             "  узел.append(версия, document.createTextNode(' · '), ссылка);"),
        ],
    ),
    (
        "ссылка ведёт не на выпуск, а на общий список",
        [
            (СТРАНИЦА, "  ссылка.href = адресВыпуска(релиз, тег);",
             "  ссылка.href = 'https://github.com/lamver/konspekt-releases';"),
        ],
    ),
]


def прогон() -> tuple[bool, str]:
    итог = subprocess.run([sys.executable, "живая_страница_test.py"],
                          capture_output=True, text=True, encoding="utf-8")
    вывод = (итог.stdout or "") + (итог.stderr or "")
    return итог.returncode == 0, вывод


def main() -> int:
    запасы = {ф: ф.read_text(encoding="utf-8") for ф in (СТРАНИЦА, СЛОВАРИ)}

    поймано = 0
    try:
        прошла, вывод = прогон()
        if "[skip]" in вывод:
            print("[плохо] проверка пропускается, мутации ничего не значат:")
            print(вывод.strip()[:200])
            return 1
        if not прошла:
            print("[плохо] проверка падает и без мутаций")
            return 1
        print("[ок] на целом коде проверка проходит")

        for имя, правки in МУТАЦИИ:
            for файл, было, стало in правки:
                целое = запасы[файл]
                if было not in целое:
                    print(f"[плохо] мутация неприменима: {имя}")
                    return 1
                файл.write_text(целое.replace(было, стало, 1), encoding="utf-8")

            прошла, вывод = прогон()

            for файл, целое in запасы.items():
                файл.write_text(целое, encoding="utf-8")

            if "[skip]" in вывод:
                print(f"[плохо] мутация ушла в пропуск, а не в падение: {имя}")
                return 1
            if прошла:
                print(f"[плохо] мутация прошла незамеченной: {имя}")
                return 1
            поймано += 1
            print(f"[ок] поймана: {имя}")
    finally:
        for файл, целое in запасы.items():
            файл.write_text(целое, encoding="utf-8")

    print(f"\nМутации: {поймано}/{len(МУТАЦИИ)}. Живая страница под присмотром.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
