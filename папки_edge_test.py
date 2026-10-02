# -*- coding: utf-8 -*-
"""Папки в списке встреч: в настоящем движке.

Что видит человек:
- папки над встречами вне папок, в папке её встречи и число;
- «+» у папки начинает встречу именно в этой папке;
- папку можно свернуть, и она остаётся свёрнутой;
- меню встречи правой кнопкой: перенести в папку, убрать из папки;
- перетащить встречу на папку — переносит, в пустое место — вынимает;
- удаление папки спрашивает и говорит, что встречи останутся;
- при поиске папки не прячут находки;
- имя папки не вставляется как HTML;
- без папок список выглядит как раньше.
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
  const ждать = (мс = 30) => new Promise((r) => setTimeout(r, мс));
  try {
  i18n.dict = JSON.parse(document.getElementById('словарь').textContent);
  // Мост-заглушка с настоящим поведением в памяти.
  const бд = {
    folders: [{ id: 'f1', name: 'Звонки <b>клиентам</b>', created_at: 1, source: '' },
              { id: 'f2', name: 'Планёрки', created_at: 2, source: '' }],
    meetings: [
      { id: 'm1', title: 'Звонок Анне', created_at: 10, folder_id: 'f1', status: 'done', duration: 60 },
      { id: 'm2', title: 'Планёрка вторник', created_at: 9, folder_id: 'f2', status: 'done', duration: 60 },
      { id: 'm3', title: 'Без папки', created_at: 8, folder_id: null, status: 'done', duration: 60 },
    ],
  };
  const вызовы = [];
  const тосты = [];
  showToast = (т) => тосты.push(т);
  const спросить = [];
  confirmDialog = async (а, б) => { спросить.push([а, б]); return true; };
  window.pywebview = { api: {
    list_meetings: async () => JSON.parse(JSON.stringify(бд.meetings)),
    list_folders: async () => бд.folders.map((f) => ({ ...f, count: бд.meetings.filter((m) => m.folder_id === f.id).length })),
    get_meeting: async (id) => ({ ...бд.meetings.find((m) => m.id === id), segments: [], summary: '' }),
    list_chat_messages: async () => [],
    list_analyses: async () => [],
    create_meeting: async (t_, folder) => {
      вызовы.push(['create_meeting', folder]);
      const m = { id: 'm' + (бд.meetings.length + 1), title: 'Новая', created_at: 100, folder_id: folder || null, status: 'draft', duration: 0 };
      бд.meetings.unshift(m);
      return m;
    },
    create_folder: async (name) => { const f = { id: 'f' + (бд.folders.length + 1), name, created_at: 3, source: '' }; бд.folders.push(f); return { ...f, count: 0 }; },
    rename_folder: async (id, name) => { бд.folders.find((f) => f.id === id).name = name; return { ok: true }; },
    delete_folder: async (id) => {
      вызовы.push(['delete_folder', id]);
      бд.folders = бд.folders.filter((f) => f.id !== id);
      бд.meetings.forEach((m) => { if (m.folder_id === id) m.folder_id = null; });
      return { ok: true };
    },
    move_meeting: async (mid, fid) => {
      вызовы.push(['move_meeting', mid, fid]);
      бд.meetings.find((m) => m.id === mid).folder_id = fid;
      return { ok: true, folder_id: fid };
    },
    set_collapsed_folders: async (ids) => { вызовы.push(['collapsed', ids.slice()]); return ids; },
    search: async () => [],
  } };
  state.currentId = 'm3';
  await loadMeetings();
  const правой = (эл) => эл.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 60, clientY: 200 }));
  // У строки встречи нет своей кнопки меню: строка остаётся чистой.
  и.кнопки_меню_нет = ui.list.querySelectorAll('.meeting-item__more').length === 0;
  const список = () => Array.from(ui.list.children).map((x) => x.classList.contains('folder')
    ? 'папка:' + x.querySelector('.folder__name').textContent + ':' + Array.from(x.querySelectorAll('.meeting-item')).map((m) => m.dataset.id).join(',')
    : 'встреча:' + x.dataset.id);
  и.порядок = список();
  и.тег = ui.list.querySelectorAll('.folder__name b').length;
  и.счётчик = ui.list.querySelector('[data-folder="f1"] .folder__count').textContent;
  и.кнопка_папки = видно(document.getElementById('btn-folder'));

  // «+» у папки начинает встречу в ней.
  ui.list.querySelector('[data-folder="f2"] .folder__add').click();
  await ждать(80);
  и.создана = вызовы.find((в) => в[0] === 'create_meeting');
  и.новая_в_папке = Array.from(ui.list.querySelectorAll('[data-folder="f2"] .meeting-item')).map((m) => m.dataset.id);
  // Кнопка «Новая встреча» — вне папок.
  document.getElementById('btn-new').click();
  await ждать(80);
  и.обычная = вызовы.filter((в) => в[0] === 'create_meeting')[1];

  // Сворачивание.
  ui.list.querySelector('[data-folder="f1"] .folder__head').click();
  await ждать();
  и.свёрнута = !видно(ui.list.querySelector('[data-folder="f1"] .meeting-item'));
  и.запомнено = (вызовы.filter((в) => в[0] === 'collapsed').pop() || [])[1];
  ui.list.querySelector('[data-folder="f1"] .folder__head').click();
  await ждать();
  и.развёрнута = видно(ui.list.querySelector('[data-folder="f1"] .meeting-item'));

  // Меню «⋯» у встречи без папки: перенести в «Планёрки».
  правой(ui.list.querySelector('.meeting-item[data-id="m3"]'));
  await ждать();
  const меню = document.getElementById('ctx-menu');
  и.меню_видно = видно(меню);
  и.меню_пункты = Array.from(меню.querySelectorAll('.ctx-menu__item')).map((b) => b.textContent);
  и.меню_в_окне = меню.getBoundingClientRect().right <= innerWidth && меню.getBoundingClientRect().left >= 0;
  Array.from(меню.querySelectorAll('.ctx-menu__item')).find((b) => b.textContent === 'Планёрки').click();
  await ждать(80);
  и.перенесена = бд.meetings.find((m) => m.id === 'm3').folder_id;
  и.меню_закрыто = !видно(меню);
  и.тост_переноса = тосты.slice(-1)[0] || '';

  // Меню у встречи в папке: текущая папка неактивна, есть «Убрать из папки».
  правой(ui.list.querySelector('.meeting-item[data-id="m3"]'));
  await ждать();
  const тек = Array.from(меню.querySelectorAll('.ctx-menu__item')).find((b) => b.textContent === 'Планёрки');
  и.текущая_неактивна = !!(тек && тек.disabled);
  Array.from(меню.querySelectorAll('.ctx-menu__item')).find((b) => b.textContent === 'Убрать из папки').click();
  await ждать(80);
  и.вынута = бд.meetings.find((m) => m.id === 'm3').folder_id;

  // Перетаскивание встречи на папку и обратно в пустое место.
  const тащить = (откуда, куда) => {
    const dt = new DataTransfer();
    откуда.dispatchEvent(new DragEvent('dragstart', { dataTransfer: dt, bubbles: true }));
    куда.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }));
    куда.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
    откуда.dispatchEvent(new DragEvent('dragend', { dataTransfer: dt, bubbles: true }));
  };
  тащить(ui.list.querySelector('.meeting-item[data-id="m3"]'), ui.list.querySelector('[data-folder="f1"]'));
  await ждать(80);
  и.перетащена = бд.meetings.find((m) => m.id === 'm3').folder_id;
  тащить(ui.list.querySelector('.meeting-item[data-id="m3"]'), ui.list);
  await ждать(80);
  и.вытащена = бд.meetings.find((m) => m.id === 'm3').folder_id;
  и.метки_сняты = !document.querySelector('.folder.is-drop') && !ui.list.classList.contains('is-drop-root');

  // Передумали удалять — папка на месте.
  confirmDialog = async (а, б) => { спросить.push([а, б]); return false; };
  ui.list.querySelector('[data-folder="f2"] .folder__more').click();
  await ждать();
  Array.from(меню.querySelectorAll('.ctx-menu__item')).find((b) => b.textContent === 'Удалить папку').click();
  await ждать(80);
  и.отказ_папка_цела = бд.folders.some((f) => f.id === 'f2') && !вызовы.some((в) => в[0] === 'delete_folder');
  confirmDialog = async (а, б) => { спросить.push([а, б]); return true; };

  // Меню по правой кнопке у самого правого края окна не вылезает за него.
  ui.list.querySelector('.meeting-item[data-id="m3"]').dispatchEvent(new MouseEvent('contextmenu',
    { bubbles: true, cancelable: true, clientX: innerWidth - 3, clientY: 200 }));
  await ждать();
  const кр = меню.getBoundingClientRect();
  и.край = [Math.round(кр.left), Math.round(кр.right), innerWidth];
  и.край_в_окне = видно(меню) && кр.right <= innerWidth && кр.left >= 0;
  document.body.click();
  await ждать();

  // Меню открывается у курсора при любом масштабе интерфейса. Жалоба
  // 30.09: при увеличенном интерфейсе меню «съехало куда-то».
  и.у_курсора = [];
  for (const масштаб of [1, 1.25, 1.5, 0.8]) {
    applyZoom(масштаб);
    await ждать();
    const строка = ui.list.querySelector('.meeting-item[data-id="m3"]');
    const рс = строка.getBoundingClientRect();
    const cx = Math.round(рс.left + 20);
    const cy = Math.round(рс.top + 10);
    строка.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: cx, clientY: cy }));
    await ждать();
    const мр = меню.getBoundingClientRect();
    // Внизу окна места нет — меню поднимается и встаёт низом к курсору.
    const верх = мр.bottom > cy + 2 ? Math.round(мр.top) : Math.round(мр.bottom);
    и.у_курсора.push([масштаб, cx, cy, Math.round(мр.left), верх, мр.bottom <= innerHeight && мр.top >= 0]);
    document.body.click();
    await ждать();
  }
  applyZoom(1);
  await ждать();

  // Удаление папки: спрашивает, встречи остаются.
  ui.list.querySelector('[data-folder="f2"] .folder__more').click();
  await ждать();
  Array.from(меню.querySelectorAll('.ctx-menu__item')).find((b) => b.textContent === 'Удалить папку').click();
  await ждать(80);
  и.спросили = спросить.slice(-1)[0] || [];
  и.встречи_остались = бд.meetings.filter((m) => m.id === 'm2').length === 1 && бд.meetings.find((m) => m.id === 'm2').folder_id === null;
  и.после_удаления = список();

  // Поиск: папки не прячут находки.
  ui.list.querySelector('[data-folder="f1"] .folder__head').click();
  await ждать();
  state.filter = 'звонок';
  renderMeetingList();
  и.поиск = список();
  state.filter = '';
  renderMeetingList();

  // Без папок — как раньше.
  бд.folders = [];
  await loadMeetings();
  и.без_папок = список();
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
fd, имя = tempfile.mkstemp(suffix=".html", prefix="_папки_", dir=КОРЕНЬ / "web")
os.close(fd)
страница = Path(имя)
try:
    страница.write_text(html.replace("</body>", проба + "</body>"), encoding="utf-8")
    with tempfile.TemporaryDirectory() as профиль:
        вывод = subprocess.run(
            [edge, "--headless", "--disable-gpu", "--no-first-run", f"--user-data-dir={профиль}",
             "--window-size=900,800", "--virtual-time-budget=8000", "--dump-dom", страница.as_uri()],
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

проверить(и["порядок"] == ["папка:Звонки <b>клиентам</b>:m1", "папка:Планёрки:m2", "встреча:m3"],
          f"папки над встречами, в папке её встречи: {и['порядок']}")
проверить(и["тег"] == 0, "имя папки не вставляется как HTML")
проверить(и["кнопки_меню_нет"], "у строки встречи нет кнопки «⋯», меню — правой кнопкой")
проверить(и["счётчик"] == "1", "у папки видно число встреч")
проверить(и["кнопка_папки"], "кнопка «Новая папка» видна")
проверить(и["создана"] == ["create_meeting", "f2"], f"«+» у папки начинает встречу в ней: {и['создана']}")
проверить(len(и["новая_в_папке"]) == 2, f"новая встреча видна в папке: {и['новая_в_папке']}")
проверить(и["обычная"] == ["create_meeting", None], f"«Новая встреча» создаёт встречу вне папок: {и['обычная']}")
проверить(и["свёрнута"] and и["запомнено"] == ["f1"], "папка сворачивается, и это запоминается")
проверить(и["развёрнута"], "и разворачивается обратно")
проверить(и["меню_видно"] and "Звонки <b>клиентам</b>" in и["меню_пункты"] and "Планёрки" in и["меню_пункты"],
          f"у встречи меню с папками: {и['меню_пункты']}")
проверить("Убрать из папки" not in и["меню_пункты"], "у встречи вне папок нет «Убрать из папки»")
проверить(и["меню_в_окне"], "меню не вылезает за окно")
проверить(и["перенесена"] == "f2" and и["меню_закрыто"], "выбор в меню переносит встречу и закрывает меню")
проверить("Планёрки" in и["тост_переноса"], f"сказано, куда перенесли: {и['тост_переноса']!r}")
проверить(и["текущая_неактивна"], "текущая папка в меню неактивна")
проверить(и["вынута"] is None, "«Убрать из папки» вынимает встречу")
проверить(и["перетащена"] == "f1", "перетащить встречу на папку — перенести")
проверить(и["вытащена"] is None, "перетащить в пустое место списка — вынуть из папки")
проверить(и["метки_сняты"], "после перетаскивания подсветка снята")
проверить(и["отказ_папка_цела"], "передумали удалять — папка на месте")
проверить(и["край_в_окне"], f"меню у правого края окна не вылезает за него: {и['край']}")
проверить(all(abs(л - x) <= 2 and abs(в - y) <= 2 and в_окне for _, x, y, л, в, в_окне in и["у_курсора"]),
          f"меню встает у курсора при масштабе 100%, 125%, 150% и 80%: {и['у_курсора']}")
проверить(len(и["спросили"]) == 2 and "Планёрки" in и["спросили"][0] and "не удалятся" in и["спросили"][1],
          f"удаление папки спрашивает и говорит, что встречи останутся: {и['спросили']}")
проверить(и["встречи_остались"], "встречи удалённой папки остались и вышли из неё")
проверить(not any(x.startswith("папка:Планёрки") for x in и["после_удаления"]), "удалённой папки нет в списке")
проверить(и["поиск"] == ["встреча:m1"], f"при поиске находка видна даже из свёрнутой папки: {и['поиск']}")
проверить(all(x.startswith("встреча:") for x in и["без_папок"]) and len(и["без_папок"]) == 5,
          f"без папок список как раньше: {и['без_папок']}")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Папки в списке работают.")
