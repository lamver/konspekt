# -*- coding: utf-8 -*-
"""Заметки и поле вопроса в настоящем движке.

Жалоба со снимка экрана (29 сентября):
- две кнопки «Копировать» и «Без разметки» рядом непонятны, нужна одна
  со стрелкой и выбором;
- саммари нельзя свернуть, а место нужно переписке;
- поле вопроса в одну строку, длинный вопрос пишется вслепую.

Раскладку знает только браузер: node видит свойства, но не видит, влезло
ли меню в окно и выросла ли переписка. Поэтому открываем настоящий
web/index.html в Edge без окна — это тот же Chromium, что в WebView2.
"""
from __future__ import annotations

import testenv  # noqa: F401  русский вывод в консоли Windows

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent
БЕДЫ: list[str] = []


def найти_edge() -> str | None:
    for путь in (
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ):
        if Path(путь).exists():
            return путь
    return shutil.which("msedge") or shutil.which("chromium") or shutil.which("google-chrome")


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


ПРОБА = r"""
<script>
window.addEventListener('load', () => setTimeout(() => {
  const и = {};
  const видно = (эл) => !!эл && getComputedStyle(эл).display !== 'none' && эл.getBoundingClientRect().height > 0;
  // Смотрим до первого щелчка: любой щелчок по странице закрывает меню,
  // и открытое с самого начала меню иначе спряталось бы незаметно.
  и.меню_скрыто_сначала = ui.summaryCopyMenu.hidden === true;
  // Открыть экран встречи и вкладку заметок, положить готовое саммари.
  document.getElementById('empty-state').hidden = true;
  document.getElementById('editor').hidden = false;
  document.querySelector('[data-tab="summary"]').click();
  state.currentId = 'x';
  state.current = { id: 'x', summary: '**Кратко**\nВстреча.\n\n**Задачи**\n- Петя чинит вход' };
  renderSummary(state.current.summary);

  и.кнопок_копирования = document.querySelectorAll('.summary__actions > [id^="summary-copy"]').length;
  и.группа_видна = видно(ui.summaryCopyGroup);

  ui.summaryCopyMore.click();
  const меню = ui.summaryCopyMenu.getBoundingClientRect();
  и.меню_края = [Math.round(меню.left), Math.round(меню.right), innerWidth];
  // Меню привязано к правому краю кнопки: кнопка стоит у правого края
  // окна, и меню, раскрытое вправо, в узком окне обрезается.
  и.меню_у_правого_края_кнопки = Math.abs(меню.right - ui.summaryCopyGroup.getBoundingClientRect().right) <= 1;
  и.меню_открылось = видно(ui.summaryCopyMenu);
  и.меню_в_окне = меню.left >= 0 && меню.right <= innerWidth && меню.bottom <= innerHeight;
  и.пунктов = ui.summaryCopyMenu.querySelectorAll('[role="menuitem"]').length;
  и.подписи = Array.from(ui.summaryCopyMenu.querySelectorAll('.split__name')).map((x) => x.textContent.trim());
  // Щелчок мимо закрывает меню.
  document.body.click();
  и.закрылось_щелчком_мимо = !видно(ui.summaryCopyMenu);
  ui.summaryCopyMore.click();
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
  и.закрылось_esc = !видно(ui.summaryCopyMenu);

  // Сворачивание: тело прячется, переписка получает место.
  const чатДо = ui.chatList.getBoundingClientRect().height;
  и.тело_видно_до = видно(ui.summaryBody);
  ui.summaryToggle.click();
  и.тело_скрыто = !видно(ui.summaryBody);
  и.шапка_видна = видно(document.querySelector('.summary__head'));
  и.чат_вырос = ui.chatList.getBoundingClientRect().height - чатДо;
  и.aria = ui.summaryToggle.getAttribute('aria-expanded');
  ui.summaryToggle.click();
  и.развернулось = видно(ui.summaryBody);

  // Поле вопроса: две строки сразу, растёт под текст.
  const поле = ui.chatText;
  const строка = parseFloat(getComputedStyle(поле).lineHeight);
  и.поле_высота = поле.getBoundingClientRect().height;
  и.строка = строка;
  поле.value = 'раз\nдва\nтри\nчетыре\nпять';
  поле.dispatchEvent(new Event('input'));
  и.поле_выросло = поле.getBoundingClientRect().height;
  поле.value = Array(60).fill('строка').join('\n');
  поле.dispatchEvent(new Event('input'));
  и.поле_потолок = поле.getBoundingClientRect().height;
  и.окно = innerHeight;
  и.прокрутка = getComputedStyle(поле).overflowY;
  document.body.setAttribute('data-itogi', JSON.stringify(и));
}, 400));
</script>
"""

edge = найти_edge()
if not edge:
    print("[skip] Edge или Chromium не найден, раскладка заметок не проверена")
    raise SystemExit(0)

html = (КОРЕНЬ / "web" / "index.html").read_text(encoding="utf-8")
fd, имя = tempfile.mkstemp(suffix=".html", prefix="_заметки_", dir=КОРЕНЬ / "web")
os.close(fd)
страница = Path(имя)
try:
    страница.write_text(html.replace("</body>", ПРОБА + "</body>"), encoding="utf-8")
    with tempfile.TemporaryDirectory() as профиль:
        вывод = subprocess.run(
            [edge, "--headless", "--disable-gpu", "--no-first-run", f"--user-data-dir={профиль}",
             "--window-size=560,800", "--virtual-time-budget=4000", "--dump-dom", страница.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        ).stdout
finally:
    страница.unlink(missing_ok=True)

m = re.search(r'data-itogi="([^"]+)"', вывод)
if not m:
    проверить(False, "страница не отчиталась: app.js не загрузился в движке")
else:
    и = json.loads(m.group(1).replace("&quot;", '"'))
    print("движок ответил:", json.dumps(и, ensure_ascii=False))
    проверить(и["кнопок_копирования"] == 1, f"рядом с заметками одна кнопка копирования, а не {и['кнопок_копирования']}")
    проверить(и["группа_видна"], "кнопка копирования видна при готовых заметках")
    проверить(и["меню_скрыто_сначала"], "меню копирования закрыто, пока не нажали стрелку")
    проверить(и["меню_открылось"], "стрелка открывает меню")
    проверить(и["меню_в_окне"], "меню целиком в окне, не обрезано краем")
    проверить(и["меню_у_правого_края_кнопки"], f"меню раскрывается влево от кнопки, а не за край окна: {и['меню_края']}")
    проверить(и["пунктов"] == 2, f"в меню два вида копирования, а не {и['пунктов']}")
    проверить(all(и["подписи"]), f"у пунктов меню есть подписи: {и['подписи']}")
    проверить(и["закрылось_щелчком_мимо"], "щелчок мимо закрывает меню")
    проверить(и["закрылось_esc"], "Esc закрывает меню")
    проверить(и["тело_видно_до"] and и["тело_скрыто"], "стрелка сворачивает заметки")
    проверить(и["шапка_видна"], "у свёрнутых заметок остаётся шапка, чтобы развернуть")
    проверить(и["чат_вырос"] > 50, f"свёрнутые заметки отдают место переписке (+{и['чат_вырос']:.0f} px)")
    проверить(и["aria"] == "false", "свёрнутое состояние видно экранному диктору")
    проверить(и["развернулось"], "повторный щелчок разворачивает заметки")
    проверить(и["поле_высота"] >= и["строка"] * 2, f"поле вопроса сразу в две строки ({и['поле_высота']:.0f} px)")
    проверить(и["поле_выросло"] > и["поле_высота"] + и["строка"], "поле растёт под длинный вопрос")
    проверить(и["поле_потолок"] <= и["окно"] * 0.25, f"поле не съедает окно ({и['поле_потолок']:.0f} из {и['окно']})")
    проверить(и["прокрутка"] == "auto", "за потолком в поле появляется прокрутка")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Заметки и поле вопроса ведут себя как задумано.")
