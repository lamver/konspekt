"""Мутации для карточек разбора встречи.

Все беды здесь тихие: разбор по SPIN затёр итоги, устаревший разбор
выдаёт себя за свежий, текст удалённой встречи живёт в базе. Человек
не увидит ни одну из них, пока не станет поздно. Каждая мутация ломает
одно такое место, и хотя бы одна проверка обязана упасть.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКИ = [КОРЕНЬ / "tests/разборы_счёт_test.py", КОРЕНЬ / "tests/разборы_test.py", КОРЕНЬ / "tests/разборы_edge_test.py"]
Б = "app/storage/db.py"
С = "app/core/service.py"
Р = "app/llm/lenses.py"
Ф = "web/app.js"
А = "app/core/analysis.py"

МУТАЦИИ = [
    (А, "        if р.who == монолог_кто and р.start - монолог_по < СКЛЕЙКА:\n",
     "        if False:\n", "монолог рвётся на каждой реплике"),
    (А, "            if р.who != прошлый.who and р.start < прошлый.end - ПЕРЕБИЛ_ЗАХЛЁСТ:\n",
     "            if р.who != прошлый.who and р.start < прошлый.end:\n",
     "быстрый ответ считается перебиванием"),
    (А, "    if len(дорожки - {\"\"}) >= 2:\n", "    if True:\n",
     "у одной дорожки врём «ноль перебиваний»"),
    (А, "    заметные = [ч for ч in по_доле if ч[\"seconds\"] >= МЕЛКИЙ_ГОЛОС * всего_речи]\n",
     "    заметные = list(по_доле)\n", "шумовые голоса отдельными строками"),
    (А, "            if р.start - прошлый.end >= ДЛИННАЯ_ПАУЗА:\n",
     "            if р.start - прошлый.start >= ДЛИННАЯ_ПАУЗА:\n", "пауза считается от начала реплики"),
    (А, "        if n < 100:\n", "        if n < 1:\n", "о тоне судим по трём словам"),
    (А, "            elif о.endswith(\"$\"):\n                счёт[к] += sum(1 for с in слова if с == о[:-1])\n",
     "            elif о.endswith(\"$\"):\n                счёт[к] += sum(1 for с in слова if с.startswith(о[:-1]))\n",
     "«ради» и «радио» считаются радостью"),
    (А, "            elif к in (\"я\", \"мы\", \"отрицание\"):\n",
     "            elif False:\n", "«я» ищется как начало слова"),
    (А, "    if самый_длинный[0] >= 180 and len(заметные) >= 2:\n",
     "    if самый_длинный[0] >= 180:\n", "диктовке в одиночку пишут «собеседника потеряли»"),
    (А, "        \"questions\": sum(ч[\"questions\"] for ч in люди.values()) if с_пунктуацией else None,\n",
     "        \"questions\": sum(ч[\"questions\"] for ч in люди.values()),\n",
     "без пунктуации пишем «вопросов 0»"),
    (А, "            if р.who != прошлый.who:\n                смен += 1\n",
     "            смен += 1\n", "каждая реплика считается сменой говорящего"),
    (Б, "    PRIMARY KEY (meeting_id, kind)\n);\n", "    PRIMARY KEY (meeting_id)\n);\n",
     "один разбор на встречу: методики затирают друг друга"),
    (Б, "    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,\n    kind ",
     "    meeting_id  TEXT NOT NULL,\n    kind ",
     "разборы переживают удаление встречи"),
    (Б, "            except sqlite3.IntegrityError:\n                # Встречу удалили, пока шёл разбор: сохранять некуда.\n                self._conn.rollback()\n",
     "            finally:\n                pass\n",
     "разбор удалённой встречи роняет программу"),
    (С, '                "stale": bool(a and a["signature"] and a["signature"] != signature),\n',
     '                "stale": False,\n', "устаревший разбор не помечается"),
    (С, '                "stale": bool(a and a["signature"] and a["signature"] != signature),\n',
     '                "stale": bool(a and a["signature"] != signature),\n',
     "старые итоги без отпечатка пугают пометкой"),
    (С, '            if р.engine == "счёт" and (a is None or a["signature"] != signature):\n',
     '            if р.engine == "счёт" and a is None:\n', "разбор разговора не пересчитывается"),
    (С, "        if meeting is not None and meeting.summary.strip() and \"summary\" not in готовые:\n",
     "        if False:\n", "итоги старых встреч пропадают из карточки"),
    (С, "            if text.strip():\n                self.store.save_analysis(meeting_id, kind, text.strip(), signature, self._model_label())\n",
     "            if text.strip():\n                self.store.save_analysis(meeting_id, \"summary\", text.strip(), signature, self._model_label())\n",
     "разбор по методике пишется поверх итогов"),
    (С, "                self.store.save_analysis(\n                    meeting_id, \"summary\", text.strip(),\n",
     "                if False: self.store.save_analysis(\n                    meeting_id, \"summary\", text.strip(),\n",
     "итоги не становятся карточкой"),
    (С, "        if not self.llm.enabled:\n            return {\"ok\": False, \"error\": self._msg(\"python.summary.disabled\")}\n        with self._llm_lock:\n            if self._llm_busy:\n                return {\"ok\": False, \"error\": self._msg(\"python.summary.busy\")}\n            self._llm_busy = True\n            self._llm_cancel = False\n        threading.Thread(\n",
     "        with self._llm_lock:\n            if self._llm_busy:\n                return {\"ok\": False, \"error\": self._msg(\"python.summary.busy\")}\n            self._llm_busy = True\n            self._llm_cancel = False\n        threading.Thread(\n",
     "разбор запускается при выключенной модели"),
    (С, "            if not transcript.strip():\n                bus.emit(ANALYSIS_ERROR,",
     "            if False:\n                bus.emit(ANALYSIS_ERROR,", "пустая встреча уходит в модель"),
    (Р, "            f\"Заполни строго по этим разделам, заголовки оставь как есть:\\n\\n{р.form}\")\n",
     "            \"Сделай заметки.\")\n", "модель не получает форму методики"),
    (Ф, "  ui.summaryRun.hidden = Boolean(info && info.engine === 'счёт');\n",
     "  ui.summaryRun.hidden = false;\n", "у разбора без модели кнопка запуска"),
    (Ф, "  const текст = lensText(state.openLens || 'summary');\n",
     "  const текст = lensText('summary');\n", "копируются итоги вместо открытого разбора"),
    (Ф, "    else if (info.stale && текст.trim()) метка = t('analysis.stale_badge');\n",
     "", "в сетке нет пометки «устарел»"),
    (Ф, "  if (ui.lensStale) ui.lensStale.hidden = !(info && info.stale && has);\n",
     "  if (ui.lensStale) ui.lensStale.hidden = true;\n", "внутри разбора не видно, что он устарел"),
    (Ф, "    превью.textContent = текст.trim()\n", "    превью.innerHTML = текст.trim()\n",
     "превью вставляется как HTML"),
    (Ф, "  if (payload.meeting_id !== state.currentId || payload.kind !== state.busyKind) return;\n",
     "", "печать разбора лезет куда попало"),
    (Ф, "  state.summaryText = (state.summaryText || '') + payload.text;\n  if (state.openLens !== 'summary') return;\n",
     "  state.summaryText = (state.summaryText || '') + payload.text;\n",
     "итоги печатаются поверх открытого разбора"),
    (Ф, "  if (state.openLens !== payload.kind) return;\n  ui.summaryBody.innerHTML = renderMarkdown(state.summaryText);\n",
     "  ui.summaryBody.innerHTML = renderMarkdown(state.summaryText);\n",
     "печать разбора ломает открытую сетку"),
    (Ф, "  setBusy(false);\n  if (payload.meeting_id !== state.currentId) return;\n  const info = lensInfo(payload.kind);\n",
     "  setBusy(false);\n  const info = lensInfo(payload.kind);\n", "разбор чужой встречи пишется в открытую"),
    (Ф, "  if (kind === 'summary') return runSummary();\n  if (!state.currentId || state.llmBusy) return;\n",
     "  return runSummary();\n", "кнопка всегда запускает итоги"),
    (Ф, "  if (ui.lenses && !ui.lenses.hidden) renderLensGrid();\n", "",
     "смена языка не трогает сетку"),
    (Ф, "  if (ui.lensBack) ui.lensBack.addEventListener('click', backToLenses);\n", "",
     "из разбора не вернуться в сетку"),
]


def прогнать() -> int:
    for проверка in ПРОВЕРКИ:
        код = subprocess.run(
            [sys.executable, str(проверка)], cwd=КОРЕНЬ, capture_output=True,
            env={**os.environ, "PYTHONUTF8": "1"}, timeout=300,
        ).returncode
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
        if упала:
            поймано += 1
            print(f"[поймана] {что}")
        else:
            print(f"[ПРОПУЩЕНА] {что}")
    print(f"\nПоймано {поймано} из {len(МУТАЦИИ)}")
    return 0 if поймано == len(МУТАЦИИ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
