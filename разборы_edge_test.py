# -*- coding: utf-8 -*-
"""Карточки разбора на вкладке «Саммари»: в настоящем движке.

Что видит человек:
- сетка карточек, у сделанных превью, у несделанных подсказка;
- щелчок открывает разбор целиком, «Все разборы» возвращает в сетку;
- у разбора без модели нет кнопки запуска, у методики есть;
- копирование берёт открытую карточку, а не итоги;
- устаревший разбор помечен и в сетке, и внутри;
- текст разбора в превью не вставляется как HTML;
- печать разбора по SPIN не лезет в открытые итоги, и наоборот.
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

from app.llm import lenses

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


def карточки() -> list[dict]:
    текст = {
        "summary": "**Решения**\n- ставим программу",
        "talk": "**Кто сколько говорил**\n- Анна — 60%",
        "tone": "Слишком мало речи, чтобы судить о тоне.",
        "sales": "**Проблемы и боли**\n- <img src=x onerror=\"document.body.dataset.взлом=1\"> теряются",
    }
    return [{
        "kind": k, "engine": lenses.РАЗРЕЗЫ[k].engine, "text": текст.get(k, ""),
        "created_at": 1.0 if k in текст else None, "model": "",
        "stale": k == "sales",
    } for k in lenses.ПОРЯДОК]


ПРОБА = r"""
<script type="application/json" id="словарь">__СЛОВАРЬ__</script>
<script type="application/json" id="карточки">__КАРТОЧКИ__</script>
<script>
window.addEventListener('load', () => setTimeout(async () => {
  const и = {};
  const видно = (эл) => !!эл && !эл.hidden && getComputedStyle(эл).display !== 'none' && эл.getBoundingClientRect().height > 0;
  const ждать = () => new Promise((r) => setTimeout(r, 30));
  try {
  i18n.dict = JSON.parse(document.getElementById('словарь').textContent);
  const список = JSON.parse(document.getElementById('карточки').textContent);
  const буфер = [];
  const запуски = [];
  Object.defineProperty(navigator, 'clipboard', { configurable: true,
    value: { writeText: async (т) => { буфер.push(т); } } });
  showToast = () => {};
  window.pywebview = { api: {
    list_analyses: async () => JSON.parse(JSON.stringify(список)),
    run_analysis: async (id, kind) => { запуски.push(kind); return { ok: true, started: true }; },
    generate_summary: async () => { запуски.push('summary'); return { ok: true, started: true }; },
    set_copy_mode: async (м) => м,
    get_meeting: async () => ({ id: 'x', title: 'Звонок', summary: список[0].text, segments: [] }),
    list_chat_messages: async () => [],
  } };
  document.getElementById('empty-state').hidden = true;
  document.getElementById('editor').hidden = false;
  document.querySelector('[data-tab="summary"]').click();
  state.currentId = 'x';
  state.current = { id: 'x', summary: список[0].text };
  state.lenses = JSON.parse(JSON.stringify(список));
  renderLensGrid();

  const сетка = () => Array.from(ui.lenses.querySelectorAll('.lens'));
  и.сетка_видна = видно(ui.lenses);
  и.карточек = сетка().length;
  и.порядок = сетка().map((к) => к.dataset.kind);
  и.заголовки = сетка().map((к) => к.querySelector('.lens__title').textContent);
  и.пустые = сетка().filter((к) => к.classList.contains('is-empty')).map((к) => к.dataset.kind);
  const продажа = ui.lenses.querySelector('[data-kind="sales"]');
  и.метка_продажи = (продажа.querySelector('.lens__badge') || {}).textContent || '';
  и.превью_продажи = продажа.querySelector('.lens__preview').textContent;
  и.взлом = document.body.dataset.взлом || '';
  и.картинок = ui.lenses.querySelectorAll('img').length;
  и.превью_пустой = ui.lenses.querySelector('[data-kind="interview"] .lens__preview').textContent;
  и.в_сетке_нет_копирования = !видно(ui.summaryCopyGroup);
  и.в_сетке_нет_назад = !видно(ui.lensBack);
  и.в_сетке_нет_запуска = !видно(ui.summaryRun);
  и.карточки_в_окне = сетка().every((к) => к.getBoundingClientRect().right <= innerWidth + 1);

  // Разбор без модели: открыть, запускать нечего, копировать можно.
  ui.lenses.querySelector('[data-kind="talk"]').click();
  await ждать();
  и.разговор_открыт = видно(ui.summaryBody) && ui.summaryBody.textContent.includes('Анна');
  и.разговор_без_запуска = !видно(ui.summaryRun);
  и.назад_видно = видно(ui.lensBack);
  ui.summaryCopy.click();
  await ждать();
  и.копия_разговора = буфер.slice(-1)[0] || '';

  // Назад в сетку.
  ui.lensBack.click();
  await ждать();
  и.вернулись = видно(ui.lenses) && !видно(ui.summaryBody);

  // Устаревший SPIN: пометка внутри и кнопка «Пересобрать».
  ui.lenses.querySelector('[data-kind="sales"]').click();
  await ждать();
  и.пометка_внутри = видно(ui.lensStale);
  и.кнопка_продажи = ui.summaryRun.textContent;
  и.картинок_внутри = ui.summaryBody.querySelectorAll('img').length;

  // Несделанная методика: пустое место со своим текстом и запуск.
  ui.lensBack.click();
  await ждать();
  ui.lenses.querySelector('[data-kind="interview"]').click();
  await ждать();
  и.пусто_заголовок = ui.summaryEmpty.querySelector('h3').textContent;
  и.пусто_видно = видно(ui.summaryEmpty);
  и.кнопка_пустой = ui.summaryRun.textContent;
  и.без_копирования = !видно(ui.summaryCopyGroup);
  ui.summaryRun.click();
  await ждать();
  и.запуски = запуски.slice();
  и.идёт = state.llmBusy && state.busyKind === 'interview';
  // Печать идёт в открытую карточку.
  window.__konspekt_event({ topic: 'analysis.chunk',  meeting_id: 'x', kind: 'interview', text: '**Опыт кандидата**\n- пять лет' });
  и.печать_видна = ui.summaryBody.textContent.includes('пять лет');
  // Кусок итогов не лезет в открытый разбор.
  window.__konspekt_event({ topic: 'summary.chunk',  meeting_id: 'x', text: 'ЧУЖОЕ' });
  и.чужое_не_видно = !ui.summaryBody.textContent.includes('ЧУЖОЕ');
  // Кусок разбора другой встречи сюда не печатается.
  window.__konspekt_event({ topic: 'analysis.chunk', meeting_id: 'другая', kind: 'interview', text: 'ДРУГАЯ' });
  и.другая_не_видна = !ui.summaryBody.textContent.includes('ДРУГАЯ');
  // Пока печатается STAR, человек открыл разбор разговора: печать не
  // должна затирать то, что он читает.
  ui.lensBack.click();
  await ждать();
  ui.lenses.querySelector('[data-kind="talk"]').click();
  await ждать();
  window.__konspekt_event({ topic: 'analysis.chunk', meeting_id: 'x', kind: 'interview', text: ' ПЕЧАТЬ' });
  и.чтение_цело = ui.summaryBody.textContent.includes('Анна') && !ui.summaryBody.textContent.includes('ПЕЧАТЬ');
  // Ушли в сетку во время печати: карточка показывает, что считается.
  ui.lensBack.click();
  await ждать();
  и.метка_идёт = (ui.lenses.querySelector('[data-kind="interview"] .lens__badge') || {}).textContent || '';
  // Кусок разбора, пока открыта сетка, сетку не ломает.
  window.__konspekt_event({ topic: 'analysis.chunk',  meeting_id: 'x', kind: 'interview', text: ' и ещё' });
  и.сетка_цела = видно(ui.lenses) && !видно(ui.summaryBody);
  window.__konspekt_event({ topic: 'analysis.ready',  meeting_id: 'x', kind: 'interview', text: '**Опыт кандидата**\n- пять лет и ещё' });
  await ждать();
  и.свободно = !state.llmBusy;
  и.превью_после = ui.lenses.querySelector('[data-kind="interview"] .lens__preview').textContent;
  // Готовый разбор чужой встречи сюда не пишется.
  window.__konspekt_event({ topic: 'analysis.ready',  meeting_id: 'другая', kind: 'standup', text: 'ЧУЖАЯ ВСТРЕЧА' });
  await ждать();
  и.чужая_встреча = ui.lenses.textContent.includes('ЧУЖАЯ ВСТРЕЧА');

  // Итоги открываются, копирование берёт итоги.
  ui.lenses.querySelector('[data-kind="summary"]').click();
  await ждать();
  ui.summaryCopy.click();
  await ждать();
  и.копия_итогов = буфер.slice(-1)[0] || '';
  и.кнопка_итогов = ui.summaryRun.textContent;

  // Ошибка разбора: карточка возвращается к прежнему тексту.
  ui.lensBack.click();
  await ждать();
  ui.lenses.querySelector('[data-kind="sales"]').click();
  await ждать();
  ui.summaryRun.click();
  await ждать();
  window.__konspekt_event({ topic: 'analysis.error',  meeting_id: 'x', kind: 'sales', error: 'сломалось' });
  await ждать();
  и.после_ошибки = ui.summaryBody.textContent.includes('теряются') && !state.llmBusy;

  // Смена языка перерисовывает сетку.
  ui.lensBack.click();
  await ждать();
  i18n.dict.analysis.kinds.talk.title = 'Talk';
  refreshDynamicTexts();
  и.язык = (ui.lenses.querySelector('[data-kind="talk"] .lens__title') || {}).textContent;
  } catch (e) { и.ошибка = String(e && e.stack || e); }
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
         .replace("__КАРТОЧКИ__", json.dumps(карточки(), ensure_ascii=False).replace("</", "<\\/")))
html = (КОРЕНЬ / "web" / "index.html").read_text(encoding="utf-8")
fd, имя = tempfile.mkstemp(suffix=".html", prefix="_разборы_", dir=КОРЕНЬ / "web")
os.close(fd)
страница = Path(имя)
try:
    страница.write_text(html.replace("</body>", проба + "</body>"), encoding="utf-8")
    with tempfile.TemporaryDirectory() as профиль:
        вывод = subprocess.run(
            [edge, "--headless", "--disable-gpu", "--no-first-run", f"--user-data-dir={профиль}",
             "--window-size=560,800", "--virtual-time-budget=6000", "--dump-dom", страница.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        ).stdout
finally:
    страница.unlink(missing_ok=True)

m = re.search(r'data-itogi="([^"]+)"', вывод)
if not m:
    проверить(False, "страница не отчиталась: app.js не загрузился в движке")
    raise SystemExit(1)
и = json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))
print("движок ответил:", json.dumps(и, ensure_ascii=False)[:900])
if и.get("ошибка"):
    проверить(False, f"проба упала в движке: {и['ошибка']}")
    raise SystemExit(1)

проверить(и["сетка_видна"] and и["карточек"] == len(lenses.ПОРЯДОК), f"сетка из {и['карточек']} карточек видна")
проверить(и["порядок"] == list(lenses.ПОРЯДОК), "карточки в заданном порядке, итоги первыми")
проверить(и["заголовки"][0] == "Итоги" and "Продажа" in и["заголовки"], f"заголовки по-человечески: {и['заголовки']}")
проверить("." not in "".join(и["заголовки"]), "ни одного ключа словаря вместо заголовка")
проверить(set(и["пустые"]) == {"interview", "one_on_one", "negotiation", "standup", "medical"},
          f"несделанные помечены: {и['пустые']}")
проверить(и["метка_продажи"] == "Устарел", "устаревший разбор помечен в сетке")
проверить("теряются" in и["превью_продажи"], "превью показывает начало разбора")
проверить(not и["взлом"] and и["картинок"] == 0, "текст разбора в превью не вставляется как HTML")
проверить("STAR" in и["превью_пустой"] and "Не сделан" in и["превью_пустой"],
          f"у несделанной карточки подсказка о методике: {и['превью_пустой']!r}")
проверить(и["в_сетке_нет_копирования"] and и["в_сетке_нет_назад"] and и["в_сетке_нет_запуска"],
          "в сетке нет кнопок одной карточки")
проверить(и["карточки_в_окне"], "карточки не вылезают за окно в 560 точек")
проверить(и["разговор_открыт"] and и["назад_видно"], "щелчок открывает разбор целиком, есть «Все разборы»")
проверить(и["разговор_без_запуска"], "у разбора без модели нет кнопки запуска")
проверить("Анна" in и["копия_разговора"], "копирование берёт открытый разбор, а не итоги")
проверить(и["вернулись"], "«Все разборы» возвращает в сетку")
проверить(и["пометка_внутри"], "внутри устаревшего разбора объяснено, что встреча дополнилась")
проверить(и["кнопка_продажи"] == "Пересобрать", "у сделанного разбора кнопка «Пересобрать»")
проверить(и["картинок_внутри"] == 0, "и внутри разбора HTML не исполняется")
проверить(и["пусто_видно"] and и["пусто_заголовок"] == "Разбора пока нет", "у несделанного разбора свой пустой экран")
проверить(и["кнопка_пустой"] == "Сделать разбор" and и["без_копирования"], "запуск есть, копировать нечего")
проверить(и["запуски"] == ["interview"] and и["идёт"], f"запускается именно открытый разбор: {и['запуски']}")
проверить(и["печать_видна"], "текст разбора печатается в открытой карточке")
проверить(и["чужое_не_видно"], "кусок итогов не попадает в открытый разбор")
проверить(и["другая_не_видна"], "кусок разбора другой встречи не печатается в открытую")
проверить(и["чтение_цело"], "печать разбора не затирает другую открытую карточку")
проверить(и["метка_идёт"] == "…", "в сетке видно, какой разбор считается")
проверить(и["сетка_цела"], "печать разбора не ломает открытую сетку")
проверить(и["свободно"] and "и ещё" in и["превью_после"], "готовый разбор сразу виден в превью")
проверить(not и["чужая_встреча"], "разбор другой встречи сюда не пишется")
проверить("ставим программу" in и["копия_итогов"], "у итогов копируются итоги")
проверить(и["кнопка_итогов"] == "Пересобрать", "у итогов прежняя кнопка «Пересобрать»")
проверить(и["после_ошибки"], "после ошибки прежний разбор на месте, модель свободна")
проверить(и["язык"] == "Talk", "смена языка перерисовывает сетку")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Карточки разбора работают.")
