# -*- coding: utf-8 -*-
"""Кнопка «Найти личные данные» и копирование без них: в настоящем движке.

Проверяем, что видит человек: полоса с кнопкой есть у расшифровки,
находки подсвечены и подписаны, повторный щелчок убирает подсветку и
возвращает текст ровно как был, а сама расшифровка не вставляется как
HTML (в ней может оказаться что угодно).
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

from app.core import personal

КОРЕНЬ = Path(__file__).resolve().parent
БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


def найти_edge() -> str | None:
    # Свой браузер вместо Edge: Edge, скачавший обновление, до перезапуска
    # молча отдаёт пустую страницу, и все проверки окна падают разом.
    свой = os.environ.get("KONSPEKT_BROWSER")
    if свой:
        return свой
    for путь in (
        os.path.expandvars(r"%ProgramFiles(x86)%\\Microsoft\\Edge\\Application\\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\\Microsoft\\Edge\\Application\\msedge.exe"),
    ):
        if Path(путь).exists():
            return путь
    return shutil.which("msedge") or shutil.which("chromium") or shutil.which("google-chrome")


РЕПЛИКИ = [
    "Запишите: карта 4111 1111 1111 1111, телефон +7 916 123-45-67.",
    "Выручка 12345678 за 2026 год, ничего личного.",
    "<img src=x onerror=\"document.body.dataset.взлом=1\"> почта a@b.ru",
    "почта <b>x</b>@b.ru и больше ничего",  # находку с тегом подложим сами
]
ЗАМЕТКИ = "**Кратко**\nКлиент оставил карту 4111 1111 1111 1111 и почту a@b.ru."

ПРОБА = r"""
<script type="application/json" id="словарь">__СЛОВАРЬ__</script>
<script type="application/json" id="находки">__НАХОДКИ__</script>
<script type="application/json" id="реплики">__РЕПЛИКИ__</script>
<script>
window.addEventListener('load', () => setTimeout(async () => {
  const и = {};
  try {
  i18n.dict = JSON.parse(document.getElementById('словарь').textContent);
  const находки = JSON.parse(document.getElementById('находки').textContent);
  const реплики = JSON.parse(document.getElementById('реплики').textContent);
  const буфер = [];
  Object.defineProperty(navigator, 'clipboard', { configurable: true,
    value: { writeText: async (т) => { буфер.push(т); } } });
  showToast = () => {};
  window.pywebview = { api: {
    find_personal: async (тексты) => тексты.map((т) => находки[т] || []),
    mask_personal: async (т) => (__СКРЫТЫЕ__)[т] ?? null,
    set_copy_mode: async (м) => м,
  } };
  document.getElementById('empty-state').hidden = true;
  document.getElementById('editor').hidden = false;
  document.querySelector('[data-tab="transcript"]').click();
  state.currentId = 'x';
  renderTranscript(реплики.map((текст, i) => ({ id: 's' + i, speaker: i % 2 ? 'me' : 'them',
    text: текст, start: i * 60, end: i * 60 + 5 })));
  const было = Array.from(document.querySelectorAll('.turn__text')).map((б) => б.textContent);
  и.полоса = !ui.personalBar.hidden && ui.personalBar.getBoundingClientRect().height > 0;
  и.кнопка = ui.personalFind.textContent;
  ui.personalFind.click();
  await new Promise((r) => setTimeout(r, 50));
  и.метки = Array.from(document.querySelectorAll('mark.personal')).map((м) => [м.dataset.kind, м.textContent, м.title]);
  и.итог = ui.personalResult.textContent;
  и.кнопка_после = ui.personalFind.textContent;
  и.текст_сохранён = Array.from(document.querySelectorAll('.turn__text')).map((б) => б.textContent);
  и.взлом = document.body.dataset.взлом || '';
  и.картинок = document.querySelectorAll('.turn__text img').length;
  и.жирных = document.querySelectorAll('.turn__text b').length;
  ui.personalFind.click();
  await new Promise((r) => setTimeout(r, 50));
  и.меток_после = document.querySelectorAll('mark.personal').length;
  и.текст_после = Array.from(document.querySelectorAll('.turn__text')).map((б) => б.textContent);
  и.было = было;
  // Копирование заметок без личных данных.
  state.current = { id: 'x', summary: __ЗАМЕТКИ__ };
  renderSummary(state.current.summary);
  await copySummary('masked');
  и.буфер = буфер.slice();
  // Скрыть не вышло: в буфер не кладём ничего.
  буфер.length = 0;
  state.current = { id: 'x', summary: 'не знаю такого' };
  await copySummary('masked');
  и.буфер_при_сбое = буфер.slice();
  } catch (e) { и.ошибка = String(e && e.stack || e); }
  document.body.setAttribute('data-itogi', JSON.stringify(и));
}, 400));
</script>
"""

edge = найти_edge()
if not edge:
    print("[skip] Edge или Chromium не найден")
    raise SystemExit(0)

находки = {т: [н.to_dict() for н in personal.find(т)] for т in РЕПЛИКИ}
# Окно не должно верить находке настолько, чтобы вставить её как HTML:
# подложим находку, в которой есть тег, как если бы правила однажды
# захватили лишнего.
с_тегом = РЕПЛИКИ[3]
начало = с_тегом.index("<b>")
конец = с_тегом.index("@b.ru") + len("@b.ru")
находки[с_тегом] = [{"kind": "email", "start": начало, "end": конец, "text": с_тегом[начало:конец]}]
скрытые = {ЗАМЕТКИ: personal.mask(ЗАМЕТКИ)}
проба = (ПРОБА
         .replace("__СЛОВАРЬ__", (КОРЕНЬ / "web" / "i18n" / "ru.json").read_text(encoding="utf-8"))
         .replace("__НАХОДКИ__", json.dumps(находки, ensure_ascii=False))
         .replace("__РЕПЛИКИ__", json.dumps(РЕПЛИКИ, ensure_ascii=False).replace("</", "<\\/"))
         .replace("__СКРЫТЫЕ__", json.dumps(скрытые, ensure_ascii=False))
         .replace("__ЗАМЕТКИ__", json.dumps(ЗАМЕТКИ, ensure_ascii=False)))

html = (КОРЕНЬ / "web" / "index.html").read_text(encoding="utf-8")
fd, имя = tempfile.mkstemp(suffix=".html", prefix="_личное_", dir=КОРЕНЬ / "web")
os.close(fd)
страница = Path(имя)
try:
    страница.write_text(html.replace("</body>", проба + "</body>"), encoding="utf-8")
    with tempfile.TemporaryDirectory() as профиль:
        вывод = subprocess.run(
            [edge, "--headless", "--disable-gpu", "--no-first-run", f"--user-data-dir={профиль}",
             "--window-size=900,800", "--virtual-time-budget=4000", "--dump-dom", страница.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        ).stdout
finally:
    страница.unlink(missing_ok=True)

m = re.search(r'data-itogi="([^"]+)"', вывод)
if not m and os.environ.get("KONSPEKT_DEBUG"):
    print(вывод[-3000:])
if not m:
    проверить(False, "страница не отчиталась: app.js не загрузился в движке")
else:
    и = json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
    print("движок ответил:", json.dumps(и, ensure_ascii=False)[:600])
    if и.get("ошибка"):
        проверить(False, f"проба упала в движке: {и['ошибка']}")
        raise SystemExit(1)
    проверить(и["полоса"], "у расшифровки видна полоса с кнопкой")
    проверить(и["кнопка"] == "Найти личные данные", f"кнопка подписана по-человечески: {и['кнопка']!r}")
    виды = [м[0] for м in и["метки"]]
    проверить(виды == ["card", "phone", "email", "email"], f"подсвечены карта, телефон, почта: {виды}")
    проверить(all(м[2] for м in и["метки"]), "у каждой подсветки подпись вида")
    проверить("карта 1" in и["итог"] and "телефон 1" in и["итог"] and "почта 2" in и["итог"],
              f"итог назван: {и['итог']!r}")
    проверить(и["кнопка_после"] == "Убрать подсветку", "кнопка меняется на «Убрать подсветку»")
    проверить(и["текст_сохранён"] == и["было"], "с подсветкой текст реплик тот же, ни буквы не потеряно")
    проверить(not и["взлом"] and и["картинок"] == 0 and и["жирных"] == 0,
              "текст расшифровки и сами находки не вставляются как HTML")
    проверить(и["меток_после"] == 0 and и["текст_после"] == и["было"], "повторный щелчок убирает подсветку")
    проверить(len(и["буфер"]) == 1 and "4111" not in и["буфер"][0] and "[карта]" in и["буфер"][0]
              and "[почта]" in и["буфер"][0], f"копия без личных данных: {и['буфер']}")
    проверить(и["буфер_при_сбое"] == [], "не вышло скрыть — в буфер ничего не уходит")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Личные данные подсвечиваются и не уходят при копировании.")
