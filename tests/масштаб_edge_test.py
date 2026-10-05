# -*- coding: utf-8 -*-
"""Масштаб в настоящем движке: окно заполнено, колонка идёт за мышью.

Проверки в node видят только, какое значение записано в свойство zoom.
Что при этом происходит с раскладкой, знает лишь браузер, и ошибки тут
тихие: при неверном способе масштаба низ окна с кнопками уезжает за
край, а граница колонки убегает от курсора в полтора раза.

Поэтому открываем настоящий web/index.html в Edge без окна. Это тот же
Chromium, что внутри WebView2, в котором живёт программа.

Пропускается, если Edge нет: у постороннего на Linux его может не быть.
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

КОРЕНЬ = Path(__file__).resolve().parent.parent
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
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ):
        if Path(путь).exists():
            return путь
    return shutil.which("msedge") or shutil.which("chromium") or shutil.which("google-chrome")


ПРОБА = r"""
<script>
window.addEventListener('load', () => setTimeout(() => {
  const итоги = {};
  for (const z of [1, 1.5, 2, 0.7]) {
    applyZoom(z);
    state.license = { licensed: false }; renderLicense();
    const раскладка = document.querySelector('.layout').getBoundingClientRect();
    const колонка = ui.sidebar.getBoundingClientRect();
    const было = currentSidebarWidth();
    ui.sidebarGrip.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: колонка.left + 300, bubbles: true }));
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    const после = ui.sidebar.getBoundingClientRect();
    setSidebarWidth(было);
    итоги[z] = {
      окно: innerHeight,
      низ: раскладка.bottom,
      плашка: ui.licenseBar.getBoundingClientRect().height,
      край: после.right,
      курсор: колонка.left + 300,
    };
  }
  document.body.setAttribute('data-itogi', JSON.stringify(итоги));
}, 300));
</script>
"""

edge = найти_edge()
if not edge:
    print("[skip] Edge или Chromium не найден, масштаб в движке не проверен")
    raise SystemExit(0)

html = (КОРЕНЬ / "web" / "index.html").read_text(encoding="utf-8")
# Страница кладётся рядом с настоящей: стили, шрифты и app.js берутся по
# тем же относительным путям, что и в программе.
fd, имя = tempfile.mkstemp(suffix=".html", prefix="_масштаб_", dir=КОРЕНЬ / "web")
os.close(fd)
страница = Path(имя)
try:
    страница.write_text(html.replace("</body>", ПРОБА + "</body>"), encoding="utf-8")
    with tempfile.TemporaryDirectory() as профиль:
        вывод = subprocess.run(
            [edge, "--headless", "--disable-gpu", "--no-first-run", f"--user-data-dir={профиль}",
             "--window-size=900,700", "--virtual-time-budget=3000", "--dump-dom", страница.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        ).stdout
finally:
    страница.unlink(missing_ok=True)

m = re.search(r'data-itogi="([^"]+)"', вывод)
if not m:
    проверить(False, "страница не отчиталась: app.js не загрузился в движке")
else:
    итоги = json.loads(m.group(1).replace("&quot;", '"'))
    for z, и in итоги.items():
        проц = round(float(z) * 100)
        проверить(abs(и["низ"] - и["окно"]) <= 1,
                  f"{проц}%: раскладка ровно до низа окна ({и['низ']:.0f} из {и['окно']})")
        проверить(и["плашка"] > 0, f"{проц}%: плашка «Купить» видна, высота {и['плашка']:.0f}")
        # Колонка ограничена 150..420 CSS-пикселями, поэтому на 70% и 200%
        # до курсора она может не дотянуться. Там проверяем, что упёрлась
        # в предел, а не убежала дальше курсора.
        if float(z) in (1, 1.5):
            проверить(abs(и["край"] - и["курсор"]) <= 1,
                      f"{проц}%: граница колонки под курсором ({и['край']:.0f} и {и['курсор']:.0f})")
        else:
            проверить(и["край"] <= и["курсор"] + 1,
                      f"{проц}%: граница колонки не убегает дальше курсора ({и['край']:.0f} и {и['курсор']:.0f})")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Масштаб в движке работает.")
