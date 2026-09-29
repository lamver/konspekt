"""Мутации: ломаем поиск по смыслу и ждём, что проверки это заметят.

Проверка, которая не падает на сломанном коде, ничего не доказывает.
У поиска по смыслу это особенно важно: его поломки не роняют программу,
а тихо ухудшают выдачу, и увидеть их можно только проверкой.

Каждая мутация — отдельная беда, от которой мы защищаемся.
"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКИ = [КОРЕНЬ / "смысл_test.py", КОРЕНЬ / "смысл_модель_test.py"]

# (файл, что заменить, на что, описание беды)
МУТАЦИИ = [
    # --- нарезка
    (
        "app/search/meaning.py",
        "        if len(text) >= MIN_CHUNK_CHARS:\n",
        "        if True:\n",
        "обрывки «а.» и «Д» снова становятся кусками и находятся везде",
    ),
    (
        "app/search/meaning.py",
        "        if size >= CHUNK_CHARS:\n",
        "        if size >= 0:\n",
        "каждая реплика идёт отдельным куском, без соседей",
    ),
    (
        "app/search/meaning.py",
        '                "start": float(current[0]["start"]),\n',
        '                "start": float(current[-1]["start"]),\n',
        "клик по цитате открывает не начало куска, а его конец",
    ),
    # --- отбор
    (
        "app/search/meaning.py",
        "        if standout < MIN_STANDOUT:\n            break\n",
        "",
        "шум проходит: кусок, похожий на всё сразу, считается находкой",
    ),
    (
        "app/search/meaning.py",
        "        if score < MIN_SCORE:\n            break\n",
        "",
        "перестали требовать близость: выделяющийся чужой кусок проходит",
    ),
    # --- слияние
    (
        "app/search/meaning.py",
        "        if mid in seen:\n            continue\n",
        "",
        "встреча, найденная словами, повторяется ещё раз как «по смыслу»",
    ),
    (
        "app/search/meaning.py",
        "    result = list(by_words)\n",
        "    result = []\n",
        "найденное словами пропадает из выдачи",
    ),
    (
        "app/search/meaning.py",
        '                "by_meaning": True,\n',
        "",
        "найденное по смыслу не помечено: цитата без слова выглядит ошибкой",
    ),
    (
        "app/search/meaning.py",
        "            if len(added) >= MAX_MEETINGS:\n                continue\n",
        "",
        "по смыслу добавляются все хоть чуть похожие встречи",
    ),
    (
        "app/search/meaning.py",
        "    result.extend(sorted(added.values(), key=lambda m: -m[\"score\"]))\n",
        "    result = sorted(added.values(), key=lambda m: -m[\"score\"]) + result\n",
        "похожее по смыслу встаёт выше точного совпадения по слову",
    ),
    # --- указатель и база
    (
        "app/storage/db.py",
        '            if r["model"] != model or r["signature"] != self.meaning_signature(r["id"]):\n',
        '            if r["model"] != model:\n',
        "дописанные реплики не пересчитываются: указатель тихо отстаёт",
    ),
    (
        "app/storage/db.py",
        '            if r["model"] != model or r["signature"] != self.meaning_signature(r["id"]):\n',
        '            if True:\n',
        "весь архив пересчитывается на каждом проходе",
    ),
    (
        "app/storage/db.py",
        '                   LEFT JOIN meaning_state s ON s.meeting_id = m.id\n'
        '                   ORDER BY m.created_at DESC"""',
        '                   LEFT JOIN meaning_state s ON s.meeting_id = m.id\n'
        "                   WHERE m.status != 'recording'\n"
        '                   ORDER BY m.created_at DESC"""',
        "встреча, застрявшая в записи, выпадает из указателя навсегда",
    ),
    (
        "app/storage/db.py",
        '        return f"{row[0]}:{row[1]}:{row[2]:.2f}"\n',
        '        return f"{row[0]}:{row[2]:.2f}"\n',
        "исправленная реплика не требует пересчёта: в указателе старый текст",
    ),
    (
        "app/storage/db.py",
        "    meeting_id  TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,\n"
        "    start_s     REAL NOT NULL DEFAULT 0,\n"
        "    who ",
        "    meeting_id  TEXT NOT NULL,\n"
        "    start_s     REAL NOT NULL DEFAULT 0,\n"
        "    who ",
        "текст удалённой встречи остаётся лежать в указателе",
    ),
    (
        "app/storage/db.py",
        "                   WHERE c.model=?\"\"\",\n",
        "                   \"\"\",\n",
        "векторы старой модели смешиваются с новыми",
    ),
    (
        "app/search/meaning.py",
        "        with self._cache_lock:\n            self._cache = None\n\n    def _run",
        "        pass\n\n    def _run",
        "удалённая встреча продолжает находиться, пока указатель в памяти",
    ),
    (
        "app/search/meaning.py",
        "            if changed and self._on_done:\n",
        "            if False:\n",
        "окно не узнаёт, что указатель досчитался, и выдача не обновляется",
    ),
    (
        "app/search/meaning.py",
        "        if len(query) < 2 or not self.model.is_downloaded():\n",
        "        if len(query) < 2:\n",
        "без скачанной модели поиск падает вместо того, чтобы промолчать",
    ),
    # --- сервис и окно
    (
        "app/core/service.py",
        "        try:\n            by_meaning = self.meaning.search(query)\n"
        "        except Exception:\n"
        "            log.exception(\"Поиск по смыслу не удался, отдаём найденное словами\")\n"
        "            by_meaning = []\n",
        "        by_meaning = self.meaning.search(query)\n",
        "поломка модели роняет и поиск по словам",
    ),
    (
        "app/ui/window.py",
        "    # Без этой строки окно не узнало бы, что поиск стал умнее.\n    MEANING_STATE,\n",
        "",
        "событие о поиске по смыслу не доходит до окна",
    ),
    # --- сама модель
    (
        "app/search/model.py",
        "        return self._embed([text], QUERY)[0]\n",
        "        return self._embed([text], PASSAGE)[0]\n",
        "запрос считается без пометки «query: », и модель понимает его хуже",
    ),
    (
        "app/search/model.py",
        "    m = mask[..., None].astype(np.float32)\n",
        "    m = np.ones_like(mask)[..., None].astype(np.float32)\n",
        "заполнитель пачки входит в среднее и портит смысл коротких фраз",
    ),
    (
        "app/search/model.py",
        "    return (vectors / np.maximum(norms, 1e-12)).astype(np.float32)\n",
        "    return vectors.astype(np.float32)\n",
        "векторы не нормированы: длинный кусок обгоняет близкий",
    ),
]


def прогнать() -> int:
    for проверка in ПРОВЕРКИ:
        r = subprocess.run(
            [sys.executable, str(проверка)], cwd=КОРЕНЬ,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**__import__("os").environ, "PYTHONUTF8": "1"},
        )
        if r.returncode != 0:
            return r.returncode
    return 0


def main() -> int:
    print("сначала убеждаемся, что на целом коде проверки проходят")
    if прогнать() != 0:
        print("ПЛОХО: проверки падают ещё до мутаций")
        return 1
    print("  ок, проходят\n")

    поймано = 0
    for имя, было, стало, беда in МУТАЦИИ:
        файл = КОРЕНЬ / имя
        сырой = файл.read_bytes()
        исходник = сырой.decode("utf-8")
        if было not in исходник:
            print(f"[ПЛОХО] {беда}: не нашли что ломать в {имя}")
            continue
        файл.write_bytes(исходник.replace(было, стало, 1).encode("utf-8"))
        try:
            код = прогнать()
        finally:
            файл.write_bytes(сырой)
        if код != 0:
            поймано += 1
            print(f"[OK   ] поймано: {беда}")
        else:
            print(f"[ПЛОХО] НЕ поймано: {беда}")

    print(f"\nмутаций поймано: {поймано} из {len(МУТАЦИИ)}")
    return 0 if поймано == len(МУТАЦИИ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
