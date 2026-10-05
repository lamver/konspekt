# -*- coding: utf-8 -*-
"""Плашка пробного периода в настоящем движке.

Что видит человек: сколько встреч осталось, с верным склонением; после
пробы плашку не спрятать, и сказано, что записи на месте; «Запись», не
сработавшая после пробы, объяснена; вопрос, не ушедший в модель, не
пропадает из поля.
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
        os.path.expandvars(r"%ProgramFiles(x86)%\\Microsoft\\Edge\\Application\\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\\Microsoft\\Edge\\Application\\msedge.exe"),
    ):
        if Path(путь).exists():
            return путь
    return shutil.which("msedge") or shutil.which("chromium") or shutil.which("google-chrome")


ПРОБА = r"""
<script type="application/json" id="словарь">__СЛОВАРЬ__</script>
<script>
window.addEventListener('load', () => setTimeout(async () => {
  const и = {};
  const видно = (эл) => !!эл && !эл.hidden && getComputedStyle(эл).display !== 'none' && эл.getBoundingClientRect().height > 0;
  const ждать = () => new Promise((r) => setTimeout(r, 30));
  try {
  i18n.dict = JSON.parse(document.getElementById('словарь').textContent);
  let состояние = null;
  const тосты = [];
  showToast = (т) => тосты.push(т);
  window.pywebview = { api: {
    license_state: async () => состояние,
    ask: async () => ({ ok: false, error: 'Пробный период закончился.', trial: true }),
  } };
  const текст = () => ui.licenseBar.querySelector('.license-bar__text').textContent;
  const крестик = document.getElementById('license-bar-hide');

  for (const [n, ключ] of [[3, 'три'], [1, 'одна'], [5, 'пять']]) {
    состояние = { licensed: false, trial: { licensed: false, limit: 10, left: n, over: false } };
    await refreshLicense();
    и[ключ] = текст();
  }
  и.видна = видно(ui.licenseBar);
  и.крестик_виден = видно(крестик);
  крестик.click();
  await ждать();
  и.прячется_до_конца = !видно(ui.licenseBar);

  состояние = { licensed: false, trial: { licensed: false, limit: 10, left: 0, over: true } };
  await refreshLicense();
  и.после_видна = видно(ui.licenseBar);
  и.после_текст = текст();
  и.после_крестик = видно(крестик);
  и.не_обрезан = getComputedStyle(ui.licenseBar.querySelector('.license-bar__text')).whiteSpace !== 'nowrap';

  состояние = { licensed: false, trial: { licensed: false, limit: 10, left: 0, over: true } };
  window.__konspekt_event({ topic: 'trial.blocked', action: 'record', message: 'Пробный период закончился: 10 встреч позади.' });
  await ждать();
  и.тост = тосты.slice(-1)[0] || '';

  document.getElementById('empty-state').hidden = true;
  document.getElementById('editor').hidden = false;
  state.currentId = 'x';
  ui.chatText.value = 'о чём встреча?';
  await sendQuestion();
  await ждать();
  и.вопрос_остался = ui.chatText.value;
  и.модель_свободна = !state.llmBusy;

  состояние = { licensed: true, license: { to: 'Иван' }, trial: { licensed: true, limit: 10, left: null, over: false } };
  await refreshLicense();
  и.с_лицензией_скрыта = !видно(ui.licenseBar);
  } catch (e) { и.ошибка = String(e && e.stack || e); }
  document.body.setAttribute('data-itogi', JSON.stringify(и));
}, 400));
</script>
"""

edge = найти_edge()
if not edge:
    print("[skip] Edge или Chromium не найден")
    raise SystemExit(0)

html = (КОРЕНЬ / "web" / "index.html").read_text(encoding="utf-8")
проба = ПРОБА.replace("__СЛОВАРЬ__", (КОРЕНЬ / "web" / "i18n" / "ru.json").read_text(encoding="utf-8"))
fd, имя = tempfile.mkstemp(suffix=".html", prefix="_проба_", dir=КОРЕНЬ / "web")
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
print("движок ответил:", json.dumps(и, ensure_ascii=False)[:700])
if и.get("ошибка"):
    проверить(False, f"проба упала в движке: {и['ошибка']}")
    raise SystemExit(1)

проверить("осталось 3 встречи из 10" in и["три"], f"три встречи: {и['три']!r}")
проверить("осталось 1 встреча из 10" in и["одна"], f"одна встреча: {и['одна']!r}")
проверить("осталось 5 встреч из 10" in и["пять"], f"пять встреч: {и['пять']!r}")
проверить(и["видна"] and и["крестик_виден"] and и["прячется_до_конца"],
          "пока проба идёт, плашку можно спрятать крестиком")
проверить(и["после_видна"], "после пробы плашка видна, даже если её прятали")
проверить("Все записи на месте" in и["после_текст"], f"сказано, что записи на месте: {и['после_текст']!r}")
проверить(not и["после_крестик"], "после пробы плашку не спрятать")
проверить(и["не_обрезан"], "текст после пробы не обрезан многоточием")
проверить("10 встреч" in и["тост"], "не сработавшая «Запись» объяснена")
проверить(и["вопрос_остался"] == "о чём встреча?" and и["модель_свободна"],
          "вопрос, не ушедший в модель, остался в поле")
проверить(и["с_лицензией_скрыта"], "с лицензией плашки нет")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Плашка пробного периода работает.")
