"""Мутации для кнопки копирования, сворачивания заметок и поля вопроса.

Ломаем по одному месту и смотрим, заметят ли проверки. Пропущенная
мутация значит, что поломку увидит только человек.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКИ = [КОРЕНЬ / "заметки_edge_test.py", КОРЕНЬ / "заметки_настройки_test.py"]
УЗЕЛ = [КОРЕНЬ / "копирование_test.js"]

МУТАЦИИ = [
    ("web/app.js",
     "  if (ui.summaryCopyGroup) ui.summaryCopyGroup.hidden = !has;\n",
     "",
     "кнопка копирования видна без заметок"),
    ("web/app.js",
     "  if (вид === 'plain') return копировать(markdownToPlain(текст), 'copy.done_plain');\n",
     "",
     "«простым текстом» копирует разметку"),
    ("web/app.js",
     "  if (вид !== state.copyMode) {\n    state.copyMode = вид;\n",
     "  if (вид !== state.copyMode) {\n",
     "главная кнопка забывает выбранный вид"),
    ("web/app.js",
     "    if (api.set_copy_mode) api.set_copy_mode(вид);\n",
     "",
     "выбранный вид не сохраняется"),
    ("web/app.js",
     "  ui.summaryCopyMenu.hidden = false;\n",
     "",
     "стрелка не открывает меню"),
    ("web/app.js",
     "    if (ui.summaryCopyGroup && !ui.summaryCopyGroup.contains(e.target)) closeCopyMenu();\n",
     "",
     "щелчок мимо не закрывает меню"),
    ("web/app.js",
     "    if (e.key === 'Escape') closeCopyMenu();\n",
     "",
     "Esc не закрывает меню"),
    ("web/app.js",
     "  if (ui.summary) ui.summary.classList.toggle('is-collapsed', state.summaryCollapsed);\n",
     "",
     "стрелка не сворачивает заметки"),
    ("web/app.js",
     "    ui.summaryToggle.setAttribute('aria-expanded', String(!state.summaryCollapsed));\n",
     "",
     "свёрнутость не видна диктору"),
    ("web/styles.css",
     ".summary.is-collapsed .summary__body,\n",
     ".summary.is-collapsed .nothing,\n",
     "свёрнутые заметки не прячут текст"),
    ("web/index.html",
     '<div class="split__menu" id="summary-copy-menu" role="menu" hidden>',
     '<div class="split__menu" id="summary-copy-menu" role="menu">',
     "меню видно сразу, без стрелки"),
    ("web/styles.css",
     "  top: calc(100% + 4px);\n  right: 0;\n",
     "  top: calc(100% + 4px);\n  left: 0;\n",
     "меню уезжает за правый край окна"),
    ("web/index.html",
     '<textarea id="chat-text" rows="2"',
     '<textarea id="chat-text" rows="1"',
     "поле вопроса снова в одну строку"),
    ("web/app.js",
     "  ui.chatText.style.height = Math.min(ui.chatText.scrollHeight + 2, потолок) + 'px';\n",
     "  ui.chatText.style.height = Math.min(ui.chatText.scrollHeight + 2, 60) + 'px';\n",
     "поле не растёт под длинный вопрос"),
    ("web/app.js",
     "  ui.chatText.style.height = Math.min(ui.chatText.scrollHeight + 2, потолок) + 'px';\n",
     "  ui.chatText.style.height = (ui.chatText.scrollHeight + 2) + 'px';\n",
     "поле без потолка съедает окно"),
    ("web/app.js",
     "  ui.chatText.style.overflowY = ui.chatText.scrollHeight + 2 > потолок ? 'auto' : 'hidden';\n",
     "  ui.chatText.style.overflowY = 'hidden';\n",
     "за потолком нет прокрутки"),
    ("app/core/service.py",
     '        value = mode if mode in ("markdown", "plain") else "markdown"\n',
     "        value = mode\n",
     "мусор вместо вида копирования записывается"),
    ("app/core/service.py",
     "        self.settings.summary_collapsed = bool(collapsed)\n        settings_mod.save(self.settings)\n",
     "        self.settings.summary_collapsed = bool(collapsed)\n",
     "свёрнутость не сохраняется"),
    ("app/core/settings.py",
     '    copy_mode: str = "markdown"\n',
     '    copy_mode: str = "plain"\n',
     "по умолчанию копируем без разметки"),
]


def прогнать() -> int:
    for проверка in ПРОВЕРКИ:
        код = subprocess.run(
            [sys.executable, str(проверка)], cwd=КОРЕНЬ, capture_output=True,
            env={**os.environ, "PYTHONUTF8": "1"},
        ).returncode
        if код != 0:
            return код
    for проверка in УЗЕЛ:
        код = subprocess.run(["node", str(проверка)], cwd=КОРЕНЬ, capture_output=True).returncode
        if код != 0:
            return код
    return 0


def main() -> int:
    if прогнать() != 0:
        print("ПЛОХО: проверки падают ещё до мутаций")
        return 1
    поймано = 0
    for файл, было, стало, что in МУТАЦИИ:
        путь = КОРЕНЬ / файл
        исходник = путь.read_bytes()
        текст = исходник.decode("utf-8")
        crlf = "\r\n" in текст
        текст = текст.replace("\r\n", "\n")
        if текст.count(было) != 1:
            print(f"[НЕ ПРИМЕНИЛАСЬ] {что}: кусок найден {текст.count(было)} раз")
            continue
        новое = текст.replace(было, стало)
        if crlf:
            новое = новое.replace("\n", "\r\n")
        путь.write_bytes(новое.encode("utf-8"))
        try:
            упала = прогнать() != 0
        finally:
            путь.write_bytes(исходник)
        if упала:
            поймано += 1
            print(f"[поймана] {что}")
        else:
            print(f"[ПРОПУЩЕНА] {что}")
    print(f"\nПоймано {поймано} из {len(МУТАЦИИ)}")
    return 0 if поймано == len(МУТАЦИИ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
