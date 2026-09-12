"""Мутации: ловит ли версия_на_странице_test поломки строки версии.

Каждая мутация — правдоподобная ошибка на публичной странице. Если
проверка её пропускает, она не стережёт ничего.
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

СТРАНИЦА = Path("docs/index.html")
СЛОВАРИ = Path("docs/i18n.js")

# (имя, файл, было, стало)
МУТАЦИИ = [
    (
        "строка версии не показывается вовсе",
        СТРАНИЦА,
        "  узел.hidden = false;\n}\n\n/** Адрес страницы выпуска",
        "  узел.hidden = true;\n}\n\n/** Адрес страницы выпуска",
    ),
    (
        "пустой ответ GitHub показывает «Версия undefined»",
        СТРАНИЦА,
        "  if (!тег) return;",
        "  if (!тег) { узел.hidden = false; }",
    ),
    (
        "буква v из тега не убирается",
        СТРАНИЦА,
        "d.releaseVersion.replace('%s', тег.replace(/^v/, ''))",
        "d.releaseVersion.replace('%s', тег)",
    ),
    (
        "ссылка ведёт не на этот выпуск, а на общий список",
        СТРАНИЦА,
        "  ссылка.href = адресВыпуска(релиз, тег);",
        "  ссылка.href = 'https://github.com/lamver/konspekt-releases/releases';",
    ),
    (
        "второй запрос к GitHub (квота кончится вдвое быстрее)",
        СТРАНИЦА,
        "    const релиз = await ответ.json();",
        "    const релиз = await (await fetch(РЕЛИЗЫ)).json();",
    ),
    (
        "сербский перевод потерян",
        СЛОВАРИ,
        "    releaseChanges: 'Šta je novo',",
        "",
    ),
    (
        "испанский перевод подменён английским",
        СЛОВАРИ,
        "    releaseChanges: 'Qué cambió',",
        "    releaseChanges: 'What changed',",
    ),
    (
        "показатьВыпуск перестали звать",
        СТРАНИЦА,
        "    показатьВыпуск(релиз, lang);",
        "",
    ),
]


def прогон() -> bool:
    итог = subprocess.run([sys.executable, "версия_на_странице_test.py"],
                          capture_output=True, text=True, encoding="utf-8")
    # Пропуск (нет node) считается провалом: молча ничего не проверить.
    if "[skip]" in (итог.stdout or ""):
        print("[плохо] node не найден, мутации ничего не значат")
        raise SystemExit(1)
    return итог.returncode == 0


def main() -> int:
    запасы = {}
    for файл in {СТРАНИЦА, СЛОВАРИ}:
        запасы[файл] = файл.read_text(encoding="utf-8")

    поймано = 0
    try:
        if not прогон():
            print("[плохо] проверка падает и без мутаций")
            return 1
        print("[ок] на целом коде проверка проходит")

        for имя, файл, было, стало in МУТАЦИИ:
            целое = запасы[файл]
            if было not in целое:
                print(f"[плохо] мутация неприменима: {имя}")
                return 1
            файл.write_text(целое.replace(было, стало, 1), encoding="utf-8")
            прошла = прогон()
            файл.write_text(целое, encoding="utf-8")
            if прошла:
                print(f"[плохо] мутация прошла незамеченной: {имя}")
                return 1
            поймано += 1
            print(f"[ок] поймана: {имя}")
    finally:
        for файл, целое in запасы.items():
            файл.write_text(целое, encoding="utf-8")

    print(f"\nМутации: {поймано}/{len(МУТАЦИИ)}. Строка версии под присмотром.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
