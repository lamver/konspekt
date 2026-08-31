"""Фронт: синтаксис JS и связность с разметкой.

Ловит две частые ошибки, которые иначе видно только глазами в готовом
окне: опечатку в JS и обращение к элементу, которого нет в HTML.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

import re
import shutil
import subprocess
from pathlib import Path

WEB = Path(__file__).parent / "web"


def main() -> int:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")

    # --- синтаксис JS ---------------------------------------------------
    node = shutil.which("node")
    if node:
        res = subprocess.run([node, "--check", str(WEB / "app.js")],
                             capture_output=True, text=True)
        assert res.returncode == 0, f"app.js не разбирается:\n{res.stderr}"
        print("[ok] app.js синтаксически верен")
    else:
        # Node есть не везде, а проверка связности важнее и работает без него.
        compile_check = re.findall(r"\bfunction\s+(\w+)", js)
        assert compile_check, "в app.js не нашлось ни одной функции"
        print("[skip] node не найден, синтаксис не проверен")

    # --- el('...') против id в разметке ----------------------------------
    ids = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r"el\('([^']+)'\)", js))
    missing = sorted(used - ids)
    assert not missing, f"JS обращается к несуществующим элементам: {missing}"
    print(f"[ok] все {len(used)} элементов из JS есть в разметке")

    # --- обработчики событий из Python -----------------------------------
    events = Path("app/core/events.py").read_text(encoding="utf-8")
    topics = set(re.findall(r'^\w+ = "([\w.]+)"', events, re.M))
    # Только имена с точкой: это темы шины. Остальные case в коде это
    # статусы задач импорта, к событиям они отношения не имеют.
    handled = {c for c in re.findall(r"case '([\w.]+)':", js) if "." in c}
    unknown = sorted(handled - topics)
    assert not unknown, f"фронт ждёт события, которых нет в шине: {unknown}"
    print(f"[ok] фронт разбирает {len(handled)} событий, и все они существуют")

    # --- классы из JS против стилей --------------------------------------
    # Проверяем только те, что создаются в коде: разметку видно глазами,
    # а созданный из JS узел без стиля выглядит сломанным и молча.
    for cls in ("bubble", "bubble--me", "bubble--bot", "dots", "is-waiting",
                "meeting-item__quote", "meeting-item__more", "meeting-item__del",
                "turn--found", "turn__play"):
        assert f".{cls}" in css, f"класс .{cls} создаётся в JS, но не описан в стилях"
    print("[ok] классы, которые создаёт JS, описаны в стилях")

    # --- шрифты ----------------------------------------------------------
    # Файл шрифта легко забыть при переносе или сборке, и тогда русский
    # текст молча нарисуется системным: заголовки поедут, а ошибки не
    # будет нигде.
    for name in re.findall(r'url\("(fonts/[^"]+)"\)', css):
        assert (WEB / name).exists(), f"стили ссылаются на {name}, а файла нет"
        assert (WEB / name).stat().st_size > 10_000, f"{name} подозрительно мал"
    fonts = re.findall(r'url\("(fonts/[^"]+)"\)', css)
    assert fonts, "свои шрифты не подключены, интерфейс поедет на чужой машине"
    # Латинская сборка шрифта весит столько же и выглядит так же: подмену
    # видно только на экране, когда русский текст рисуется системным.
    try:
        from fontTools.ttLib import TTFont
        # woff2 сжат brotli, и без него fontTools падает уже при чтении.
        import brotli  # noqa: F401
    except ImportError:
        print(f"[skip] все {len(fonts)} шрифтов на месте, кириллица не проверена")
    else:
        for name in set(fonts):
            chars = set()
            for table in TTFont(WEB / name)["cmap"].tables:
                chars |= set(table.cmap)
            for letter in "АЯЁабяё":
                assert ord(letter) in chars, f"в {name} нет буквы {letter}"
        print(f"[ok] все {len(set(fonts))} шрифтов на месте и знают кириллицу")

    # --- цвета против тёмной темы ----------------------------------------
    # Тема живёт в переменных, и любой прямой цвет фона мимо них — это
    # пятно, которое в одной из тем перестаёт читаться. Так уже случилось
    # с полями выбора устройства: белый фон и светлый текст.
    for block in re.findall(r"\{[^{}]*\}", css):
        if "background" not in block:
            continue
        hard = re.findall(r"background(?:-color)?:\s*(#[0-9a-fA-F]{3,6})", block)
        # Белый на акцентной кнопке — это цвет текста, а не фона, и
        # претензий к нему нет: там фон задан переменной.
        assert not hard, f"прямой цвет фона мимо темы: {hard} в {block[:60]}"

    # Переменная с опечаткой не ошибка для браузера: он просто ничего не
    # применит, и элемент останется прозрачным. Такое было с --bg-2.
    declared = set(re.findall(r"^\s*(--[\w-]+):", css, re.M))
    used_vars = set(re.findall(r"var\((--[\w-]+)", css))
    unknown_vars = sorted(used_vars - declared)
    assert not unknown_vars, f"стили зовут несуществующие переменные: {unknown_vars}"

    # Системные элементы (стрелка списка, полосы прокрутки) движок рисует
    # сам и без этой подсказки всегда считает фон светлым.
    assert "color-scheme: dark" in css, "тёмной теме не объявлена color-scheme"
    print("[ok] цвета берутся из темы, тёмная тема объявлена движку")

    # --- вызовы моста против методов Api ---------------------------------
    api_py = Path("app/ui/api.py").read_text(encoding="utf-8")
    methods = set(re.findall(r"^    def (\w+)", api_py, re.M))
    called = set(re.findall(r"\bapi\.(\w+)\(", js))
    absent = sorted(c for c in called if c not in methods)
    assert not absent, f"фронт зовёт методы, которых нет в мосте: {absent}"
    print(f"[ok] все {len(called)} вызовов моста есть в Api")

    print("\nФронт согласован с бэкендом.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
