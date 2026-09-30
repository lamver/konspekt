# -*- coding: utf-8 -*-
"""Выбор своей модели в настройках: в настоящем движке.

Три модели: быстрая, умная, мощная. Проверяем то, что видит человек:
карточки на месте и подписаны, выбранная выделена, щелчок уходит в
программу, а при нехватке памяти честное предупреждение, а не молчание.
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


ПРОБА = r"""
<script type="application/json" id="словарь">__СЛОВАРЬ__</script>
<script>
window.addEventListener('load', () => setTimeout(async () => {
  const и = {};
  const вызовы = [];
  const модели = (выбрана) => [
    { code: 'fast', bytes: 1834426016, ram_gb: 8, downloaded: true },
    { code: 'smart', bytes: 2497280256, ram_gb: 12, downloaded: выбрана === 'smart' },
    { code: 'strong', bytes: 5027783488, ram_gb: 16, downloaded: false },
  ];
  const статус = (выбрана, ram) => ({ backend: 'local', local_model: выбрана, local_models: модели(выбрана),
    ram_gb: ram, model_ready: true, auto_summary: true });
  window.pywebview = { api: {
    save_llm_settings: async (f) => { вызовы.push(f); return статус(f.local_model || 'fast', 8); },
  } };
  // Словарь кладём прямо: мост в пробе поддельный.
  i18n.dict = JSON.parse(document.getElementById('словарь').textContent);
  document.querySelector('[data-page="notes"]').hidden = false;
  document.getElementById('prefs') && (document.getElementById('prefs').hidden = false);
  renderLlmSettings(статус('fast', 8));
  и.подпись_быстрая = ui.chatModel.hidden ? '' : ui.chatModel.textContent;
  const карточки = () => Array.from(ui.llmTiers.querySelectorAll('.llm-tier'));
  и.видно = !ui.llmTiers.hidden;
  и.карточек = карточки().length;
  и.имена = карточки().map((к) => к.querySelector('.llm-tier__name').textContent);
  и.размеры = карточки().map((к) => к.querySelector('.llm-tier__meta').textContent);
  и.выбрана = карточки().filter((к) => к.classList.contains('is-current')).map((к) => к.dataset.tier);
  и.aria = карточки().map((к) => к.getAttribute('aria-checked'));
  и.состояния = карточки().map((к) => к.querySelector('.llm-tier__state').textContent);
  и.предупреждения = карточки().map((к) => !!к.querySelector('.llm-tier__warn'));
  карточки()[1].click();
  await new Promise((r) => setTimeout(r, 50));
  и.вызовы = вызовы;
  и.выбрана_после = карточки().filter((к) => к.classList.contains('is-current')).map((к) => к.dataset.tier);
  и.подпись_после = ui.chatModel.textContent;
  // Свой сервер: карточки своих моделей не нужны.
  renderLlmSettings({ ...статус('fast', 32), backend: 'remote' });
  и.скрыто_у_сервера = ui.llmTiers.hidden;
  renderLlmSettings({ ...статус('fast', 32), backend: 'remote', model: 'gpt-4o-mini' });
  и.подпись_сервер = ui.chatModel.textContent;
  renderLlmSettings({ ...статус('fast', 32), backend: 'null' });
  и.подпись_выкл = ui.chatModel.hidden;
  renderLlmSettings(статус('fast', 32));
  и.предупреждений_при_32 = карточки().filter((к) => к.querySelector('.llm-tier__warn')).length;
  document.body.setAttribute('data-itogi', JSON.stringify(и));
}, 400));
</script>
"""

edge = найти_edge()
if not edge:
    print("[skip] Edge или Chromium не найден")
    raise SystemExit(0)

html = (КОРЕНЬ / "web" / "index.html").read_text(encoding="utf-8")
fd, имя = tempfile.mkstemp(suffix=".html", prefix="_модели_", dir=КОРЕНЬ / "web")
os.close(fd)
страница = Path(имя)
try:
    словарь = (КОРЕНЬ / "web" / "i18n" / "ru.json").read_text(encoding="utf-8")
    страница.write_text(html.replace("</body>", ПРОБА.replace("__СЛОВАРЬ__", словарь) + "</body>"),
                        encoding="utf-8")
    with tempfile.TemporaryDirectory() as профиль:
        вывод = subprocess.run(
            [edge, "--headless", "--disable-gpu", "--no-first-run", f"--user-data-dir={профиль}",
             "--window-size=900,800", "--virtual-time-budget=4000", "--dump-dom", страница.as_uri()],
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
    проверить(и["видно"] and и["карточек"] == 3, f"три карточки моделей видны: {и['карточек']}")
    проверить(и["имена"] == ["Быстрая", "Умная", "Мощная"], f"названия переведены: {и['имена']}")
    проверить(all("ГБ" in r and "памяти" in r for r in и["размеры"]), f"у каждой вес и память: {и['размеры']}")
    проверить(и["выбрана"] == ["fast"] and и["aria"] == ["true", "false", "false"],
              "выбранная модель выделена и видна диктору")
    проверить(и["состояния"][0] == "скачана" and "первом" in и["состояния"][2],
              f"видно, какая скачана, а какая приедет: {и['состояния']}")
    проверить(и["предупреждения"] == [False, True, True],
              f"на 8 ГБ памяти умная и мощная предупреждают: {и['предупреждения']}")
    проверить(и["вызовы"] == [{"local_model": "smart"}], f"щелчок сохраняет выбор: {и['вызовы']}")
    проверить(и["выбрана_после"] == ["smart"], "после щелчка выделена новая модель")
    проверить(и["скрыто_у_сервера"], "у своего сервера карточки своих моделей скрыты")
    проверить(и["предупреждений_при_32"] == 0, "на 32 ГБ предупреждений нет")
    проверить(и["подпись_быстрая"] == "Быстрая модель", f"у переписки видно, какая модель отвечает: {и['подпись_быстрая']!r}")
    проверить(и["подпись_после"] == "Умная модель", f"после смены подпись меняется: {и['подпись_после']!r}")
    проверить(и["подпись_сервер"] == "Сервер: gpt-4o-mini", f"у своего сервера видно имя модели: {и['подпись_сервер']!r}")
    проверить(и["подпись_выкл"], "без модели подписи нет")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Выбор модели в настройках работает.")
