"""Мутации для папок встреч.

Самые обидные беды здесь тихие: удалили папку — пропали встречи;
встреча «перенеслась», но лежит в двух папках; начатая в папке
встреча оказалась вне её. Каждая мутация ломает одно такое место.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКИ = [КОРЕНЬ / "tests/папки_test.py", КОРЕНЬ / "tests/папки_edge_test.py"]
Б = "app/storage/db.py"
С = "app/core/service.py"
Ф = "web/app.js"

МУТАЦИИ = [
    (Б, "    folder_id   TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE\n",
     "    folder_id   TEXT NOT NULL\n", "удалённая папка держит встречи в пустоте"),
    (Б, "    meeting_id  TEXT PRIMARY KEY REFERENCES meetings(id) ON DELETE CASCADE,\n    folder_id",
     "    meeting_id  TEXT PRIMARY KEY,\n    folder_id", "удалённая встреча числится в папке"),
    (Б, "                           ON CONFLICT(meeting_id) DO UPDATE SET folder_id=excluded.folder_id\"\"\",\n",
     "                           ON CONFLICT(meeting_id) DO NOTHING\"\"\",\n", "встреча не переносится из папки в папку"),
    (Б, "            cur = self._conn.execute(\"DELETE FROM folders WHERE id=?\", (folder_id,))\n",
     "            self._conn.execute(\"DELETE FROM meetings WHERE id IN (SELECT meeting_id FROM meeting_folders WHERE folder_id=?)\", (folder_id,))\n            cur = self._conn.execute(\"DELETE FROM folders WHERE id=?\", (folder_id,))\n",
     "удаление папки удаляет встречи"),
    (Б, "            except sqlite3.IntegrityError:\n                # Встречи или папки уже нет: переносить некуда.\n                self._conn.rollback()\n                return False\n",
     "            finally:\n                pass\n", "перенос в несуществующую папку роняет программу"),
    (С, "        if folder_id and self.store.set_meeting_folder(meeting.id, folder_id):\n",
     "        if False:\n", "встреча, начатая в папке, оказывается вне её"),
    (С, "        return \" \".join((name or \"\").split())[:80]\n", "        return (name or \"\")[:80]\n",
     "имя папки с переносами и пробелами"),
    (С, "        if not имя:\n            return {\"ok\": False}\n", "",
     "папку можно переименовать в пустоту"),
    (С, "        data[\"folder_id\"] = self.store.meeting_folders().get(meeting_id)\n", "",
     "открытая встреча не знает свою папку"),
    (Ф, "  if (q || !state.folders.length) {\n", "  if (!state.folders.length) {\n",
     "при поиске находки прячутся в свёрнутых папках"),
    (Ф, "  name.textContent = f.name;\n", "  name.innerHTML = f.name;\n", "имя папки вставляется как HTML"),
    (Ф, "  el('btn-new').addEventListener('click', () => createMeeting());\n",
     "  el('btn-new').addEventListener('click', createMeeting);\n",
     "«Новая встреча» передаёт событие щелчка вместо папки"),
    (Ф, "  if (api.set_collapsed_folders) api.set_collapsed_folders([...state.collapsedFolders]);\n", "",
     "свёрнутые папки не запоминаются"),
    (Ф, "  if (m.folder_id) items.push({ text: t('folders.move_out'), run: () => moveMeeting(m.id, null) });\n", "",
     "встречу нельзя убрать из папки"),
    (Ф, "    if (it.current) b.disabled = true;\n", "", "текущая папка в меню активна"),
    (Ф, "    node.addEventListener('contextmenu', (e) => {\n      e.preventDefault();\n      openMoveMenu(m, null, e.clientX, e.clientY);\n    });\n",
     "", "у встречи не открывается меню правой кнопкой"),
    (Ф, "    moveMeeting(e.dataTransfer.getData('text/konspekt-meeting'), f.id);\n", "",
     "перетаскивание на папку не переносит"),
    (Ф, "    moveMeeting(e.dataTransfer.getData('text/konspekt-meeting'), null);\n", "",
     "перетаскивание в пустое место не вынимает"),
    (Ф, "  if (!ok) return;\n  await api.delete_folder(f.id);\n", "  await api.delete_folder(f.id);\n",
     "папка удаляется без вопроса"),
    (Ф, "  if (left + w > окноШ - 4) left = окноШ - w - 4;\n", "",
     "меню вылезает за окно"),
    (Ф, "  menu.style.left = `${Math.max(4, left) / z}px`;\n", "  menu.style.left = `${Math.max(4, left)}px`;\n",
     "при увеличенном интерфейсе меню съезжает от курсора вбок"),
    (Ф, "  menu.style.top = `${Math.max(4, top) / z}px`;\n", "  menu.style.top = `${Math.max(4, top)}px`;\n",
     "при увеличенном интерфейсе меню съезжает от курсора вниз"),
    (Ф, "  if (top + h > окноВ - 4) top = Math.max(4, (anchor ? r.top - 4 : r.top) - h);\n", "",
     "у нижнего края окна меню уходит за край"),
]


def прогнать() -> int:
    for проверка in ПРОВЕРКИ:
        код = subprocess.run([sys.executable, str(проверка)], cwd=КОРЕНЬ, capture_output=True,
                             env={**os.environ, "PYTHONUTF8": "1"}, timeout=300).returncode
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
            try:
                упала = прогнать() != 0
            except subprocess.TimeoutExpired:
                упала = True
        finally:
            путь.write_bytes(исходник)
        print(f"[{'поймана' if упала else 'ПРОПУЩЕНА'}] {что}")
        поймано += упала
    print(f"\nПоймано {поймано} из {len(МУТАЦИИ)}")
    return 0 if поймано == len(МУТАЦИИ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
