# -*- coding: utf-8 -*-
"""Волна уровня во время записи: столбики в настоящем движке.

Вместо двух тонких полосок «Я» и «Они» теперь волна: каждый уровень
становится столбиком справа, история уплывает влево. Ошибки здесь тихие:
волна может застыть на последнем громком звуке, если поток уровней
оборвался, или остаться висеть после «Остановить».

Открываем настоящий web/index.html в Edge без окна (или в браузере из
KONSPEKT_BROWSER), подаём уровни той же функцией, что и событие
recording.level, и читаем высоты столбиков.
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
window.addEventListener('load', () => setTimeout(async () => {
  const и = {};
  const высоты = (эл) => Array.from(эл.children).map((с) => parseFloat(с.style.height) || 0);
  state.isRecording = true;
  renderRecordingState();
  и.видна = !ui.levels.hidden;
  // Говорю я, громче и громче; собеседник молчит. 70 уровней — больше,
  // чем столбиков: старые обязаны уехать за левый край.
  for (let i = 1; i <= 70; i++) renderLevels(i / 70, 0);
  const я = высоты(ui.levelMe), они = высоты(ui.levelThem);
  и.столбиков = я.length;
  и.последний = я[я.length - 1];
  и.первый = я[0];
  и.растёт = я.every((h, i) => i === 0 || h >= я[i - 1]);
  и.они_тишина = они.every((h) => h === 2);
  // Тихая речь (уровень 0.05) должна быть видна, а не слиться с тишиной.
  renderLevels(0.05, 0);
  const после_тихой = высоты(ui.levelMe);
  и.тихая = после_тихой[после_тихой.length - 1];
  // Поток оборвался: волна плывёт тишиной, а не стоит на последнем звуке.
  await new Promise((r) => setTimeout(r, 1000));
  const без_потока = высоты(ui.levelMe);
  и.после_обрыва = без_потока[без_потока.length - 1];
  // Остановили запись: волна сброшена и больше не двигается.
  state.isRecording = false;
  renderRecordingState();
  и.скрыта = ui.levels.hidden;
  и.после_стопа = высоты(ui.levelMe).every((h) => h === 2);
  const до = JSON.stringify(волны.me);
  await new Promise((r) => setTimeout(r, 900));
  и.после_стопа_стоит = JSON.stringify(волны.me) === до;
  document.body.setAttribute('data-itogi', JSON.stringify(и));
}, 300));
</script>
"""

edge = найти_edge()
if not edge:
    print("[skip] Edge или Chromium не найден, волна в движке не проверена")
    raise SystemExit(0)

html = (КОРЕНЬ / "web" / "index.html").read_text(encoding="utf-8")
fd, имя = tempfile.mkstemp(suffix=".html", prefix="_волна_", dir=КОРЕНЬ / "web")
os.close(fd)
страница = Path(имя)
try:
    страница.write_text(html.replace("</body>", ПРОБА + "</body>"), encoding="utf-8")
    with tempfile.TemporaryDirectory() as профиль:
        вывод = subprocess.run(
            [edge, "--headless", "--disable-gpu", "--no-first-run", f"--user-data-dir={профиль}",
             "--window-size=900,700", "--virtual-time-budget=6000", "--dump-dom", страница.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        ).stdout
finally:
    страница.unlink(missing_ok=True)

m = re.search(r'data-itogi="([^"]+)"', вывод)
if not m:
    проверить(False, "страница не отчиталась: app.js не загрузился в движке")
else:
    и = json.loads(m.group(1).replace("&quot;", '"'))
    print(f"   [..] {и}")
    проверить(и["видна"], "во время записи волна видна")
    проверить(и["столбиков"] == 64, f"столбиков 64 ({и['столбиков']})")
    проверить(и["последний"] == 16, f"свежий громкий звук справа во всю высоту ({и['последний']})")
    проверить(и["первый"] < 6, f"старые уровни уехали влево, слева тихие ({и['первый']})")
    проверить(и["растёт"], "волна идёт от старого к новому слева направо")
    проверить(и["они_тишина"], "молчащий собеседник — ровная линия")
    проверить(и["тихая"] >= 3, f"тихая речь видна, а не слилась с тишиной ({и['тихая']})")
    проверить(и["после_обрыва"] == 2, f"оборвался поток уровней — волна плывёт тишиной ({и['после_обрыва']})")
    проверить(и["скрыта"], "после остановки волна спрятана")
    проверить(и["после_стопа"], "после остановки волна сброшена")
    проверить(и["после_стопа_стоит"], "после остановки волна больше не двигается")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Волна уровня работает.")
