# Проверка: база, которую обновила новая версия, читается прежней.
#
# В 0.8.1 схема хранилища выросла с 8 до 9 ради пометки о попытке
# досчёта. Человек, которому новая версия чем-то не подошла, поставит
# прежнюю поверх уже обновлённой базы, и его архив обязан выжить. Это
# проверяется запуском настоящего кода прошлого выпуска, а не рассуждением
# о том, что SQLite вроде бы терпит лишние колонки.
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.storage.db import SCHEMA_VERSION, Store  # noqa: E402

КОРЕНЬ = Path(__file__).resolve().parent
ПРОШЛЫЙ_ТЕГ = "v0.8.0"
врем = Path(tempfile.mkdtemp(prefix="konspekt-откат-"))
дерево = врем / "прошлый"


def прибрать() -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(дерево)],
        cwd=КОРЕНЬ,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    shutil.rmtree(врем, ignore_errors=True)


try:
    # Берём боевую базу, если она есть: на ней видно, что миграция
    # переживает настоящий архив, а не три строки из пробирки.
    боевая = Path(os.environ.get("APPDATA", "")) / "Konspekt" / "konspekt.db"
    копия = врем / "konspekt.db"
    if боевая.exists():
        shutil.copy(боевая, копия)
        откуда = "копия боевой базы"
    else:
        склад = Store(копия)
        встреча = склад.create_meeting(title="Проверка отката")
        склад.add_segment(
            meeting_id=встреча.id if hasattr(встреча, "id") else встреча,
            start=0.0,
            end=1.0,
            text="Реплика, которая обязана пережить откат",
        )
        склад.close()
        откуда = "свежая база"

    было = sqlite3.connect(копия)
    # Приводим копию к состоянию прошлого выпуска: у меня на машине база
    # давно обновлена, и без этого миграция ничего бы не делала, а
    # проверка молча превратилась бы в проверку пустого места.
    # Сносим все колонки, добавленные миграциями, и откатываем версию в
    # ноль. Раньше здесь стояло `SCHEMA_VERSION - 1` и сносилась одна
    # колонка: со следующей же миграцией это разъезжалось, и проверка
    # падала на ровном месте — база оказывалась без колонки, но с
    # версией, при которой её уже не добавляют.
    поздние = {
        "meetings": ["rescued"],
        "transcript_segments": ["doubtful"],
    }
    for таблица, колонки in поздние.items():
        есть = [
            с[1] for с in было.execute(f"PRAGMA table_info({таблица})").fetchall()
        ]
        for колонка in колонки:
            if колонка in есть:
                было.execute(f"ALTER TABLE {таблица} DROP COLUMN {колонка}")
    было.execute("PRAGMA user_version=0")
    было.commit()
    исходно_встреч = было.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    исходно_реплик = было.execute(
        "SELECT COUNT(*) FROM transcript_segments"
    ).fetchone()[0]
    было.close()
    print(f"[..] {откуда}: {исходно_встреч} встреч, {исходно_реплик} реплик")

    новый = Store(копия)
    новый.find_unrescued()
    новый.close()
    конн = sqlite3.connect(копия)
    стало = конн.execute("PRAGMA user_version").fetchone()[0]
    конн.close()
    assert стало == SCHEMA_VERSION, f"новый код не довёл схему: {стало}"
    print(f"[ok] новая версия подняла схему до {стало}")

    # Код прошлого выпуска берём из самого репозитория: так проверка не
    # устареет молча, когда рабочее дерево уедет вперёд. На сервере
    # сборки история обрезана и тега может не быть, тогда доносим его
    # отдельно: пропустить проверку значит остаться без неё там, где
    # она нужнее всего.
    есть_тег = subprocess.run(
        ["git", "rev-parse", "--verify", f"{ПРОШЛЫЙ_ТЕГ}^{{commit}}"],
        cwd=КОРЕНЬ,
        capture_output=True,
    )
    if есть_тег.returncode != 0:
        subprocess.run(
            ["git", "fetch", "--depth=1", "origin", "tag", ПРОШЛЫЙ_ТЕГ],
            cwd=КОРЕНЬ,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    итог = subprocess.run(
        ["git", "worktree", "add", "--force", "--detach", str(дерево), ПРОШЛЫЙ_ТЕГ],
        cwd=КОРЕНЬ,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert итог.returncode == 0, f"не поднял дерево {ПРОШЛЫЙ_ТЕГ}:\n{итог.stderr}"

    скрипт = врем / "прошлый_читает.py"
    скрипт.write_text(
        "import sys\n"
        f"sys.path.insert(0, r'{дерево}')\n"
        "from app.storage.db import Store, SCHEMA_VERSION\n"
        f"s = Store(r'{копия}')\n"
        "встречи = s.list_meetings()\n"
        "реплик = sum(len(s.list_segments(m.id)) for m in встречи)\n"
        "print('ПРОШЛЫЙ', SCHEMA_VERSION, len(встречи), реплик)\n"
        "s.close()\n",
        encoding="utf-8",
    )
    среда = dict(os.environ, PYTHONIOENCODING="utf-8")
    прогон = subprocess.run(
        [sys.executable, str(скрипт)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(дерево),
        env=среда,
    )
    assert прогон.returncode == 0, (
        f"код {ПРОШЛЫЙ_ТЕГ} упал на новой базе:\n{прогон.stderr[-2000:]}"
    )
    строки = [s for s in прогон.stdout.splitlines() if s.startswith("ПРОШЛЫЙ")]
    assert строки, f"прошлая версия ничего не ответила:\n{прогон.stdout[-1000:]}"
    _, схема, встреч, реплик = строки[0].split()
    assert int(схема) < SCHEMA_VERSION, (
        f"дерево {ПРОШЛЫЙ_ТЕГ} знает схему {схема}: проверка потеряла смысл"
    )
    print(f"[ok] код {ПРОШЛЫЙ_ТЕГ} (схема {схема}) открыл базу схемы {стало}")

    assert int(встреч) == исходно_встреч, (
        f"прошлая версия видит {встреч} встреч вместо {исходно_встреч}"
    )
    assert int(реплик) == исходно_реплик, (
        f"прошлая версия видит {реплик} реплик вместо {исходно_реплик}"
    )
    print(f"[ok] архив цел: {встреч} встреч, {реплик} реплик")

    снова = Store(копия)
    # Не просто открыть, а воспользоваться тем, ради чего росла схема:
    # если пометку забыли добавить, а версию проставили, база выглядит
    # обновлённой и ломается позже, при первом же поиске пустых встреч.
    снова.find_unrescued()
    снова.close()
    конн = sqlite3.connect(копия)
    итог_версия = конн.execute("PRAGMA user_version").fetchone()[0]
    итог_встреч = конн.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    конн.close()
    assert итог_версия == SCHEMA_VERSION, (
        f"после отката новая версия не вернула схему: {итог_версия}"
    )
    assert итог_встреч == исходно_встреч, "встречи потерялись на обратном пути"
    print("[ok] возврат на новую версию: схема на месте, встречи целы")
finally:
    прибрать()

print("\nОткат безопасен: прошлый выпуск читает обновлённую базу целиком.")
