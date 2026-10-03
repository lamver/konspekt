# -*- coding: utf-8 -*-
"""Окно ждёт моста к Python целиком, а не его пустую заготовку.

Беда (жалоба 04.10, после обновления до 0.15.1): выбранный масштаб не
восстановился. pywebview сначала кладёт в страницу пустой
`window.pywebview.api`, а методы добавляет позже. Окно считало мост
готовым уже на пустой заготовке, первый же `get_settings` падал, и
настройки окна не применялись: масштаб, язык, ширина колонки. На
холодном старте сразу после обновления промежуток длиннее, поэтому
беда вылезала именно тогда.

Проверяем в настоящем движке: заготовка есть сразу, методы через 0,6 с.
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


# Как делает pywebview: сначала пустая заготовка (api.js), методы и
# событие pywebviewready потом (finish.js).
ЗАГОТОВКА = r"""
<script>
window.pywebview = { api: {} };
window.__вызовы = [];
setTimeout(() => {
  const настройки = { ui_zoom: 1.5, language: 'ru', theme: 'light', window: { sidebar_width: 333 } };
  const api = window.pywebview.api;
  api.get_settings = async () => { window.__вызовы.push('get_settings'); return настройки; };
  api.get_i18n_dict = async () => JSON.parse(document.getElementById('словарь').textContent);
  window.dispatchEvent(new CustomEvent('pywebviewready'));
}, 600);
</script>
"""

ПРОБА = r"""
<script type="application/json" id="словарь">__СЛОВАРЬ__</script>
<script>
window.addEventListener('load', () => setTimeout(() => {
  document.body.setAttribute('data-itogi', JSON.stringify({
    масштаб: document.documentElement.style.zoom,
    надпись: (document.getElementById('zoom-value') || {}).textContent || '',
    вызовы: window.__вызовы,
  }));
}, 2500));
</script>
"""

edge = найти_edge()
if not edge:
    print("[skip] Edge или Chromium не найден")
    raise SystemExit(0)

html = (КОРЕНЬ / "web" / "index.html").read_text(encoding="utf-8")
assert html.count('<script src="app.js"></script>') == 1
проба = ПРОБА.replace("__СЛОВАРЬ__", (КОРЕНЬ / "web" / "i18n" / "ru.json").read_text(encoding="utf-8"))
html = html.replace('<script src="app.js"></script>', ЗАГОТОВКА + '<script src="app.js"></script>')
fd, имя = tempfile.mkstemp(suffix=".html", prefix="_мост_", dir=КОРЕНЬ / "web")
os.close(fd)
страница = Path(имя)
try:
    страница.write_text(html.replace("</body>", проба + "</body>"), encoding="utf-8")
    with tempfile.TemporaryDirectory() as профиль:
        вывод = subprocess.run(
            [edge, "--headless", "--disable-gpu", "--no-first-run", f"--user-data-dir={профиль}",
             "--window-size=900,800", "--virtual-time-budget=6000", "--dump-dom", страница.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        ).stdout
finally:
    страница.unlink(missing_ok=True)

m = re.search(r'data-itogi="([^"]+)"', вывод)
if not m:
    проверить(False, "страница не отчиталась: app.js не загрузился в движке")
    raise SystemExit(1)
и = json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))

проверить("get_settings" in и["вызовы"], f"настройки запрошены, когда мост готов: {и['вызовы']}")
проверить(и["масштаб"] == "1.5", f"сохранённый масштаб 150% применён: {и['масштаб']!r}")
проверить(и["надпись"] == "150%", f"и показан на кнопке масштаба: {и['надпись']!r}")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Окно дожидается моста и восстанавливает масштаб.")
