"""Фронт: синтаксис JS и связность с разметкой.

Ловит две частые ошибки, которые иначе видно только глазами в готовом
окне: опечатку в JS и обращение к элементу, которого нет в HTML.
"""

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
    for cls in ("bubble", "bubble--me", "bubble--bot", "dots", "is-waiting"):
        assert f".{cls}" in css, f"класс .{cls} создаётся в JS, но не описан в стилях"
    print("[ok] классы, которые создаёт JS, описаны в стилях")

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
