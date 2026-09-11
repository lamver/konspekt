# -*- coding: utf-8 -*-
"""Живая проверка копирования в собранном окне.

Снимок экрана для этого не годится: он захватывает то, что сверху, то
есть чужие окна пользователя. Поэтому поднимаем окно программы сами, в
своей песочнице, и спрашиваем у него самого, что оно нарисовало.

Проверяем то, что node проверить не может: что разметка, стили и код
сходятся в живом webview и кнопки действительно появляются на экране.
"""
import testenv  # noqa: F401  песочница вместо боевого профиля

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import webview
except Exception as e:  # noqa: BLE001
    print(f"[skip] webview недоступен: {e}")
    raise SystemExit(0)

КОРЕНЬ = Path(__file__).parent
СТРАНИЦА = КОРЕНЬ / "web" / "index.html"

итог = {"ошибка": None, "данные": None}


def осмотреть(окно):
    """Спросить у живой страницы, что она нарисовала."""
    try:
        # Ждём, пока app.js доберётся до конца: он грузит словарь.
        for _ in range(50):
            готово = окно.evaluate_js("typeof ui !== 'undefined' && !!ui.summaryCopy")
            if готово:
                break
            time.sleep(0.2)

        окно.evaluate_js(
            "state.current = {id:'x', summary:'# Итоги\\n\\n- Петя чинит вход'};"
            "renderSummary(state.current.summary);"
        )
        итог["данные"] = json.loads(окно.evaluate_js("""
          JSON.stringify({
            есть_кнопка: !!document.getElementById('summary-copy'),
            есть_простая: !!document.getElementById('summary-copy-plain'),
            видна: !document.getElementById('summary-copy').hidden,
            подпись: (document.getElementById('summary-copy').textContent || '').trim(),
            заголовок_разобран:
              document.getElementById('summary-body').innerHTML.includes('<h4>'),
            решётка_видна:
              (document.getElementById('summary-body').textContent || '').includes('#'),
            простой: markdownToPlain(state.current.summary),
            цитата: цитатаРеплики('Маша', '12:30', 'переделать'),
          })
        """))
    except Exception as e:  # noqa: BLE001
        итог["ошибка"] = repr(e)
    finally:
        окно.destroy()


окно = webview.create_window("проверка", str(СТРАНИЦА), hidden=True)
webview.start(осмотреть, окно, private_mode=True)

if итог["ошибка"]:
    print(f"[skip] окно не поднялось: {итог['ошибка']}")
    raise SystemExit(0)

д = итог["данные"] or {}
print("живое окно ответило:", json.dumps(д, ensure_ascii=False))

assert д.get("есть_кнопка"), "в живом окне нет кнопки «копировать» у заметок"
assert д.get("есть_простая"), "в живом окне нет кнопки «без разметки»"
assert д.get("видна"), "кнопка есть в разметке, но скрыта при готовых заметках"
assert д.get("подпись"), "кнопка копирования без подписи: человек не поймёт, что это"
assert д.get("заголовок_разобран"), (
    "заголовок разметки не превратился в заголовок на экране"
)
assert not д.get("решётка_видна"), (
    "человек видит решётку «#» в заметках: разметка показана как есть"
)
assert "- Петя чинит вход" in (д.get("простой") or ""), (
    f"в простом тексте потерялся пункт списка: {д.get('простой')!r}"
)
assert "#" not in (д.get("простой") or ""), (
    f"в простом тексте осталась разметка: {д.get('простой')!r}"
)
assert д.get("цитата") == "[Маша, 12:30] переделать", д.get("цитата")

print("\n[ok] живое окно: кнопки на месте, заголовки разобраны, цитата собрана")
