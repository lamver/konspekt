# -*- coding: utf-8 -*-
"""Разметка ответов в чате и заметках: код, таблицы, списки, цитаты.

Жалоба со снимка экрана (30.09): модель прислала код на PHP в ```php, а в
чате он развалился на строки обычного текста, без отступов, с ``` и
именем языка прямо в тексте.

Что сторожит:
- блок кода показан кодом, с отступами, без ```; у него своя кнопка копии,
  и в буфер уходит только код;
- пока ответ печатается и закрывающих ``` нет, код уже показан кодом;
- внутри кода * и _ не превращаются в жирный и курсив;
- `код` в строке, **жирный**, *курсив*, ~~зачёркнутый~~, списки с номерами,
  цитаты, таблицы, черта;
- HTML из ответа модели не исполняется нигде: ни в тексте, ни в коде, ни
  в таблице, ни в ссылке;
- ссылка не уводит окно программы со встречи.
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


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


def найти_edge() -> str | None:
    for путь in (
        os.path.expandvars(r"%ProgramFiles(x86)%\\Microsoft\\Edge\\Application\\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\\Microsoft\\Edge\\Application\\msedge.exe"),
    ):
        if Path(путь).exists():
            return путь
    return shutil.which("msedge") or shutil.which("chromium") or shutil.which("google-chrome")


КОД = """function fibGenerator($n) {
    $a = 0;
    $b = 1;

    for ($i = 0; $i < $n; $i++) {
        yield $a;
        [$a, $b] = [$b, $a + $b];
    }
}
// **не жирный** и *не курсив* и <b>не тег</b>
$очень_длинная_строка = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";"""

ОТВЕТ = f"""Код PHP для генератора чисел Фибоначчи:
```php
{КОД}
```
Этот генератор выдаёт первые `$n` чисел, **быстро** и *лениво*, ~~без рекурсии~~.

1. Первый шаг
2. Второй шаг

> Цитата из встречи

| Кто | Что |
|-----|-----|
| Петя | <img src=x onerror="document.body.dataset.взлом=1"> вход |
| Маша | **отчёт** |

---
[Документация](https://php.net/generators) и <script>document.body.dataset.взлом=2</script>"""

ПРОБА = r"""
<script type="application/json" id="словарь">__СЛОВАРЬ__</script>
<script type="application/json" id="ответ">__ОТВЕТ__</script>
<script type="application/json" id="код">__КОД__</script>
<script>
window.addEventListener('load', () => setTimeout(async () => {
  const и = {};
  try {
  i18n.dict = JSON.parse(document.getElementById('словарь').textContent);
  const ответ = JSON.parse(document.getElementById('ответ').textContent);
  const код = JSON.parse(document.getElementById('код').textContent);
  const буфер = [];
  Object.defineProperty(navigator, 'clipboard', { configurable: true,
    value: { writeText: async (т) => { буфер.push(т); } } });
  showToast = () => {};
  document.getElementById('empty-state').hidden = true;
  document.getElementById('editor').hidden = false;
  document.querySelector('[data-tab="summary"]').click();
  state.currentId = 'x';

  // Недописанный ответ: код начат, закрывающих ``` ещё нет.
  const пузырь = document.createElement('div');
  пузырь.className = 'bubble bubble--bot';
  ui.chatList.appendChild(пузырь);
  const половина = ответ.slice(0, ответ.indexOf('yield'));
  setBubbleText(пузырь, половина);
  const pre0 = пузырь.querySelector('pre.md-code code');
  и.печать_код = !!pre0;
  и.печать_без_оград = !пузырь.textContent.includes('```');

  setBubbleText(пузырь, ответ);
  const pre = пузырь.querySelector('pre.md-code');
  const code = pre && pre.querySelector('code');
  и.блоков = пузырь.querySelectorAll('pre.md-code').length;
  и.код_как_есть = code ? code.textContent : '';
  и.язык = (pre && pre.querySelector('.md-code__lang') || {}).textContent || '';
  и.оград_нет = !пузырь.textContent.includes('```');
  и.моноширинный = code ? getComputedStyle(code).fontFamily : '';
  и.отступы_видны = code ? getComputedStyle(code).whiteSpace : '';
  и.в_коде_нет_жирного = code ? code.querySelectorAll('strong,em,b').length === 0 : false;
  и.строчный_код = Array.from(пузырь.querySelectorAll('p code')).map((x) => x.textContent);
  и.жирный = Array.from(пузырь.querySelectorAll('strong')).map((x) => x.textContent);
  и.курсив = Array.from(пузырь.querySelectorAll('em')).map((x) => x.textContent);
  и.зачёркнутый = Array.from(пузырь.querySelectorAll('del')).map((x) => x.textContent);
  и.нумерованный = Array.from(пузырь.querySelectorAll('ol li')).map((x) => x.textContent);
  и.цитата = (пузырь.querySelector('blockquote') || {}).textContent || '';
  и.таблица = Array.from(пузырь.querySelectorAll('table tr')).map((r) => Array.from(r.children).map((c) => c.textContent.trim()));
  и.черта = пузырь.querySelectorAll('hr').length;
  и.ссылка = (пузырь.querySelector('.md-link') || {}).title || '';
  и.ссылок_a = пузырь.querySelectorAll('a').length;
  и.картинок = пузырь.querySelectorAll('img').length;
  и.скриптов = пузырь.querySelectorAll('script').length;
  и.кнопка_кода = !!(pre && pre.querySelector('.md-code__copy'));
  pre.querySelector('.md-code__copy').click();
  await new Promise((r) => setTimeout(r, 30));
  и.буфер_кода = буфер.slice(-1)[0] || '';
  // Повторная отрисовка (пришёл новый кусок) не плодит кнопки.
  setBubbleText(пузырь, ответ);
  и.кнопок_кода = пузырь.querySelectorAll('.md-code__copy').length;
  и.код_ожидали = код;
  // Длинная строка кода прокручивается внутри блока, а не торчит за
  // пузырь и край окна. Смотрим на сам текст кода: где кончается его
  // видимая часть. Без прокрутки у блока текст вылезает наружу, хотя
  // рамка блока остаётся на месте.
  // Блок пересоздан новой отрисовкой: берём свежий.
  const свежий = пузырь.querySelector('pre.md-code');
  const рамка = свежий.getBoundingClientRect();
  const текст = document.createRange();
  текст.selectNodeContents(свежий.querySelector('code'));
  const pre_ = свежий;
  const видимо = Array.from(текст.getClientRects()).map((r) => Math.min(r.right, рамка.right + (getComputedStyle(pre_).overflowX === 'visible' ? 1e6 : 0)));
  и.вылезает = Math.max(...видимо) > рамка.right + 1;
  и.пузырь_в_окне = пузырь.getBoundingClientRect().right <= innerWidth + 1;

  // Заметки тем же рендером.
  state.current = { id: 'x', summary: '**Итоги**\n1. Первое\n2. Второе\n\n`код` в заметках' };
  renderSummary(state.current.summary);
  и.заметки_ol = ui.summaryBody.querySelectorAll('ol li').length;
  и.заметки_code = ui.summaryBody.querySelectorAll('code').length;
  // Копирование простым текстом не теряет пункты с номерами.
  и.простой = markdownToPlain('1. Первое\n2. Второе\n```\nx = 1\n```');
  } catch (e) { и.ошибка = String(e && e.stack || e); }
  и.взлом = document.body.dataset.взлом || '';
  document.body.setAttribute('data-itogi', JSON.stringify(и));
}, 400));
</script>
"""

edge = найти_edge()
if not edge:
    print("[skip] Edge или Chromium не найден")
    raise SystemExit(0)

проба = (ПРОБА
         .replace("__СЛОВАРЬ__", (КОРЕНЬ / "web" / "i18n" / "ru.json").read_text(encoding="utf-8"))
         .replace("__ОТВЕТ__", json.dumps(ОТВЕТ, ensure_ascii=False).replace("</", "<\\/"))
         .replace("__КОД__", json.dumps(КОД, ensure_ascii=False).replace("</", "<\\/")))
html = (КОРЕНЬ / "web" / "index.html").read_text(encoding="utf-8")
fd, имя = tempfile.mkstemp(suffix=".html", prefix="_разметка_", dir=КОРЕНЬ / "web")
os.close(fd)
страница = Path(имя)
try:
    страница.write_text(html.replace("</body>", проба + "</body>"), encoding="utf-8")
    with tempfile.TemporaryDirectory() as профиль:
        вывод = subprocess.run(
            [edge, "--headless", "--disable-gpu", "--no-first-run", f"--user-data-dir={профиль}",
             "--window-size=560,800", "--virtual-time-budget=5000", "--dump-dom", страница.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        ).stdout
finally:
    страница.unlink(missing_ok=True)

m = re.search(r'data-itogi="([^"]+)"', вывод)
if not m:
    проверить(False, "страница не отчиталась: app.js не загрузился в движке")
    raise SystemExit(1)
и = json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
if и.get("ошибка"):
    проверить(False, f"проба упала в движке: {и['ошибка']}")
    raise SystemExit(1)

проверить(и["печать_код"] and и["печать_без_оград"], "пока ответ печатается, код уже показан кодом")
проверить(и["блоков"] == 1, f"один блок кода, а не {и['блоков']}")
проверить(и["код_как_есть"] == КОД, "код показан ровно как прислан: с отступами, $, <, **")
проверить(и["язык"] == "php", f"подписан язык: {и['язык']!r}")
проверить(и["оград_нет"], "``` не видны в тексте")
проверить("mono" in и["моноширинный"].lower() or "consolas" in и["моноширинный"].lower(),
          f"код моноширинным шрифтом: {и['моноширинный']}")
проверить(и["отступы_видны"] == "pre", "отступы в коде не схлопываются")
проверить(и["в_коде_нет_жирного"], "звёздочки в коде не становятся жирным")
проверить(и["строчный_код"] == ["$n"], f"`код` в строке: {и['строчный_код']}")
проверить("быстро" in и["жирный"] and "лениво" in и["курсив"] and "без рекурсии" in и["зачёркнутый"],
          "жирный, курсив, зачёркнутый")
проверить(и["нумерованный"] == ["Первый шаг", "Второй шаг"], f"список с номерами: {и['нумерованный']}")
проверить("Цитата из встречи" in и["цитата"], "цитата")
проверить(len(и["таблица"]) == 3 and и["таблица"][0] == ["Кто", "Что"] and и["таблица"][2] == ["Маша", "отчёт"],
          f"таблица: {и['таблица']}")
проверить(и["черта"] == 1, "черта ---")
проверить(и["ссылка"] == "https://php.net/generators" and и["ссылок_a"] == 0,
          "ссылка показана текстом с адресом и не уводит окно")
проверить(not и["взлом"] and и["картинок"] == 0 and и["скриптов"] == 0,
          "HTML из ответа модели не исполняется нигде")
проверить(и["кнопка_кода"] and и["буфер_кода"] == КОД, "кнопка у кода копирует только код")
проверить(и["кнопок_кода"] == 1, "новый кусок ответа не плодит кнопки")
проверить(not и["вылезает"] and и["пузырь_в_окне"], "длинная строка кода прокручивается, а не вылезает за окно")
проверить(и["заметки_ol"] == 2 and и["заметки_code"] == 1, "в заметках тот же рендер")
проверить("1. Первое" in и["простой"] or "- Первое" in и["простой"],
          f"простым текстом пункты не теряются: {и['простой']!r}")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Разметка ответов работает.")
