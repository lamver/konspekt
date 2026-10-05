"""Старые пустые встречи: расшифровка должна появиться сама.

Продолжение lamver/konspekt-releases#3. Пометка пропусков при разборе
файла чинит будущее, но у человека уже лежат встречи, разобранные
старой версией: звук на месте, реплик нет, и в базе никто не отметил,
что их надо досчитать. Досчёт про них не знает, и сами по себе они
никогда не оживут.

Здесь проверяем спасение таких встреч: программа находит их по
признаку «звук есть, а реплик нет» и отправляет в тот же досчёт.

Отдельно проверяем, что попытка ровно одна. Встреча без реплик может
быть просто тишиной или речью на чужом языке — такую мы будем
перемалывать при каждом запуске впустую, а на архиве в сотню встреч
это минуты работы на пустом месте при каждом старте.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import os
import shutil
import sqlite3
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

RATE = 16000


def сделать_запись(путь: Path, секунд: float = 5.0) -> None:
    t = np.arange(int(RATE * секунд), dtype=np.float32) / RATE
    сигнал = (np.sin(2 * np.pi * 220 * t) * 12000).astype(np.int16)
    with wave.open(str(путь), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(сигнал.tobytes())


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-спасение-"))
    os.environ["KONSPEKT_NO_PREFETCH"] = "1"
    try:
        from app.core import paths as paths_mod

        models = tmp / "models"
        models.mkdir(parents=True, exist_ok=True)
        audio = tmp / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        paths_mod.data_dir = lambda: tmp  # type: ignore[assignment]
        paths_mod.audio_dir = lambda: audio  # type: ignore[assignment]
        paths_mod.db_path = lambda: tmp / "konspekt.db"  # type: ignore[assignment]
        paths_mod.models_dir = lambda: models  # type: ignore[assignment]
        paths_mod.settings_path = lambda: tmp / "settings.json"  # type: ignore[assignment]

        from app.audio import NullCapture
        from app.core.models import Meeting, MeetingStatus, Speaker, TranscriptSegment
        from app.core.service import AppService
        from app.storage.db import Store

        путь_бд = str(tmp / "konspekt.db")
        store = Store(путь_бд)

        # --- готовим базу как после старой версии: звук есть, текста нет.
        # Собираем руками, а не импортом: именно так выглядит встреча,
        # разобранная до появления пометок, и оживить её должна сама
        # программа, а не наша подсказка.
        пострадавшая = store.create_meeting(
            Meeting(title="Без расшифровки", status=MeetingStatus.READY)
        )
        папка = audio / пострадавшая.id
        папка.mkdir(parents=True, exist_ok=True)
        дорожка = папка / "import-them.wav"
        сделать_запись(дорожка, 5.0)
        store.add_audio_chunk(пострадавшая.id, "them", str(дорожка), 0.0, 5.0)

        # Здоровая встреча: текст есть. Её трогать нельзя, иначе мы
        # молча перепишем человеку готовую расшифровку.
        здоровая = store.create_meeting(
            Meeting(title="С расшифровкой", status=MeetingStatus.READY)
        )
        папка2 = audio / здоровая.id
        папка2.mkdir(parents=True, exist_ok=True)
        дорожка2 = папка2 / "import-them.wav"
        сделать_запись(дорожка2, 5.0)
        store.add_audio_chunk(здоровая.id, "them", str(дорожка2), 0.0, 5.0)
        store.add_segment(TranscriptSegment(
            meeting_id=здоровая.id, speaker=Speaker("them"),
            text="уже расшифровано", start=0.0, end=1.0,
        ))

        # Пустая встреча без звука: черновик, который человек завёл и
        # бросил. Спасать там нечего, и трогать её незачем.
        черновик = store.create_meeting(Meeting(title="Черновик"))
        store.close()

        вызовы: list[str] = []

        def досчитать(pcm, sample_rate, meeting_id, offset, speaker, **_):
            вызовы.append(meeting_id)
            return [TranscriptSegment(
                meeting_id=meeting_id, speaker=Speaker(speaker),
                text=f"спасено на {offset:.1f}с", start=offset,
                end=offset + len(pcm) / sample_rate,
            )]

        class Рабочий:
            name = "проба"
            languages = ("ru",)

            def is_downloaded(self) -> bool:
                return True

            def transcribe(self, *a, **k):
                return досчитать(*a, **k)

        # --- запуск программы: спасение идёт само, как и досчёт
        service = AppService(
            store=Store(путь_бд), capture=NullCapture(), transcriber=Рабочий()
        )

        край = time.time() + 90
        while time.time() < край:
            if service.store.list_segments(пострадавшая.id):
                break
            time.sleep(0.1)

        сегменты = service.store.list_segments(пострадавшая.id)
        assert сегменты, (
            "встреча со звуком и без реплик не ожила: человеку с уже "
            "испорченным архивом починка ничего не дала"
        )
        print(f"[ok] старая пустая встреча расшифрована сама ({len(сегменты)} реплик)")

        текст = service.store.list_segments(здоровая.id)
        assert len(текст) == 1 and текст[0].text == "уже расшифровано", (
            "готовую расшифровку переписали: так можно испортить архив"
        )
        assert здоровая.id not in вызовы, "здоровую встречу отправили на пересчёт"
        assert черновик.id not in вызовы, "черновик без звука отправили на пересчёт"
        print("[ok] встречи с текстом и пустые черновики не тронуты")

        service.shutdown()

        # --- второй запуск: тишину не перемалываем заново
        было = len(вызовы)

        class Молчун(Рабочий):
            """Распознаватель, который ничего не нашёл: тишина или чужой язык."""

            def transcribe(self, *a, **k):
                досчитать(*a, **k)
                return []

        # Новая пострадавшая, которую спасти не выйдет: сколько ни
        # считай, реплик не появится.
        store = Store(путь_бд)
        тишина = store.create_meeting(
            Meeting(title="Тишина", status=MeetingStatus.READY)
        )
        папка3 = audio / тишина.id
        папка3.mkdir(parents=True, exist_ok=True)
        д3 = папка3 / "import-them.wav"
        сделать_запись(д3, 5.0)
        store.add_audio_chunk(тишина.id, "them", str(д3), 0.0, 5.0)
        store.close()

        s2 = AppService(store=Store(путь_бд), capture=NullCapture(), transcriber=Молчун())
        край = time.time() + 60
        while time.time() < край and тишина.id not in вызовы:
            time.sleep(0.1)
        assert тишина.id in вызовы, "новую пострадавшую встречу даже не попробовали"
        s2.asr_queue.wait_idle(timeout=60)
        попыток_после_первой = вызовы.count(тишина.id)
        s2.shutdown()
        print(f"[ok] попытка сделана ({попыток_после_первой} кусков)")

        s3 = AppService(store=Store(путь_бд), capture=NullCapture(), transcriber=Молчун())
        time.sleep(3)
        s3.asr_queue.wait_idle(timeout=60)
        assert вызовы.count(тишина.id) == попыток_после_первой, (
            "тишину пересчитывают при каждом запуске: на большом архиве "
            "это минуты работы впустую на каждом старте"
        )
        # И здоровые встречи по-прежнему не трогаем.
        assert вызовы.count(здоровая.id) == 0
        assert len(вызовы) > было
        s3.shutdown()
        print("[ok] вторая попытка не делается: спасение пробуется один раз")

        # --- база из прошлой версии: колонки rescued в ней ещё нет.
        # Проверка выше работает на базе, созданной сегодняшней схемой,
        # а у человека база старая — именно её обновление и решает,
        # оживёт его архив или нет.
        старая = tmp / "старая.db"
        con = sqlite3.connect(str(старая))
        Store(str(старая)).close()
        con.execute("ALTER TABLE meetings DROP COLUMN rescued")
        con.execute("PRAGMA user_version=8")
        con.commit()
        колонки = {r[1] for r in con.execute("PRAGMA table_info(meetings)")}
        assert "rescued" not in колонки, "не удалось изобразить старую базу"

        # Встреча со звуком и без текста, как у человека после старой версии.
        старый_id = "старая-встреча"
        con.execute(
            "INSERT INTO meetings(id, title, created_at, status)"
            " VALUES (?,?,?,?)",
            (старый_id, "Из прошлой версии", time.time(), "ready"),
        )
        con.commit()
        con.close()

        store_old = Store(str(старая))
        колонки = {r["name"] for r in store_old._conn.execute("PRAGMA table_info(meetings)")}
        assert "rescued" in колонки, (
            "обновление старой базы не добавило отметку: у человека с "
            "накопленным архивом программа не запустится вовсе"
        )
        папка4 = audio / старый_id
        папка4.mkdir(parents=True, exist_ok=True)
        д4 = папка4 / "import-them.wav"
        сделать_запись(д4, 5.0)
        store_old.add_audio_chunk(старый_id, "them", str(д4), 0.0, 5.0)
        store_old.close()

        s4 = AppService(
            store=Store(str(старая)), capture=NullCapture(), transcriber=Рабочий()
        )
        край = time.time() + 60
        while time.time() < край:
            if s4.store.list_segments(старый_id):
                break
            time.sleep(0.1)
        assert s4.store.list_segments(старый_id), (
            "встреча из базы прошлой версии не ожила"
        )
        s4.shutdown()
        print("[ok] база из прошлой версии обновилась, её встреча расшифрована")

        print("\nСтарые пустые встречи спасаются, и ровно по одному разу.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
