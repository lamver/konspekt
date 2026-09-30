# -*- coding: utf-8 -*-
"""Пробный период: 10 встреч целиком, дальше только просмотр.

Решение 30.09. Что сторожит:
1. Встреча идёт в счёт только с минутой речи: «Запись» — «Стоп» не съедает пробу.
2. Удалил посчитанную встречу — пробу себе не вернул.
3. Встречи до обновления в счёт не идут: первые пользователи не получают
   «только просмотр» в день обновления.
4. После 10 встреч новая запись и загрузка не начинаются, и сказано почему.
5. Десятую встречу можно дописать: отнимать её конец нечестно.
6. Модель после пробы закрыта для новых встреч, но не для пробных и старых.
7. Калькулятор в чате работает и без лицензии.
8. С лицензией счёта нет вовсе.
9. Просмотр, поиск и копирование не закрываются никогда.
10. Пачка файлов при остатке 2 загружает 2 и говорит, сколько не влезло.
"""
from __future__ import annotations

import testenv  # noqa: F401  песочница вместо боевого профиля

import tempfile
from pathlib import Path

from app.audio import NullCapture
from app.core import license as lic
from app.core import settings as settings_mod
from app.core.events import TRIAL_BLOCKED, bus
from app.core.models import Meeting, Speaker, TranscriptSegment
from app.core.service import AppService
from app.storage.db import Store

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


class Модель:
    enabled = True
    backend = "local"
    tier = "fast"

    def client(self):
        raise AssertionError("в этой проверке модель не зовут")

    def status(self):
        return {"model_ready": True}

    def shutdown(self):
        pass


тмп = Path(tempfile.mkdtemp())
база = тмп / "t.db"

# --- 3. Старые встречи до обновления -------------------------------------------
до = Store(str(база))
# Имитируем базу до пробного периода: снести отметку и завести старые встречи.
with до._lock:
    до._conn.execute("DELETE FROM trial_state")
    до._conn.execute("DELETE FROM trial_legacy")
    до._conn.commit()
старые = []
for i in range(15):
    m = до.create_meeting(Meeting(title=f"Старая {i}"))
    до.add_segment(TranscriptSegment(meeting_id=m.id, speaker=Speaker.ME, text="речь",
                                     start=0, end=300))
    старые.append(m.id)
до.close()

сервис = AppService(store=Store(str(база)), capture=NullCapture())
сервис.settings = settings_mod.Settings()
сервис.llm = Модель()
заблокировано: list[dict] = []
bus.on(TRIAL_BLOCKED, заблокировано.append)

с = сервис.trial_state()
проверить(с["left"] == lic.TRIAL_MEETINGS and not с["over"],
          f"15 встреч до обновления не съели пробу: осталось {с['left']}")


def встреча(секунд: float) -> str:
    mid = сервис.store.create_meeting(Meeting(title="Новая")).id
    сервис.store.add_segment(TranscriptSegment(meeting_id=mid, speaker=Speaker.ME,
                                               text="речь", start=0, end=секунд))
    return mid


# --- 1. Короткие встречи не считаются ------------------------------------------
for _ in range(5):
    встреча(20)
проверить(сервис.trial_state()["left"] == lic.TRIAL_MEETINGS,
          "пять встреч по 20 секунд не съели ни одной пробной")
пустая = сервис.store.create_meeting(Meeting(title="Пустая")).id
проверить(сервис.trial_state()["left"] == lic.TRIAL_MEETINGS, "встреча без речи не считается")

# --- 2. Удаление не возвращает пробу -------------------------------------------
первая = встреча(120)
проверить(сервис.trial_state()["left"] == lic.TRIAL_MEETINGS - 1, "встреча с двумя минутами речи в счёте")
сервис.delete_meeting(первая)
проверить(сервис.trial_state()["left"] == lic.TRIAL_MEETINGS - 1, "удалил встречу — проба не вернулась")

пробные = [встреча(90) for _ in range(lic.TRIAL_MEETINGS - 2)]
с = сервис.trial_state()
проверить(с["left"] == 1 and not с["over"], f"после 9 встреч осталась одна: {с['left']}")

# --- 10. Пачка файлов при остатке ----------------------------------------------
добавлено: list = []
сервис.importer.add = lambda paths: (добавлено.extend(paths), [{"id": str(p)} for p in paths])[1]
заблокировано.clear()
сервис.import_files(["a.wav", "b.wav", "c.wav"])
проверить(добавлено == ["a.wav"], f"из трёх файлов при остатке 1 загружен один: {добавлено}")
проверить(заблокировано and "1 из 3" in заблокировано[-1]["message"],
          f"сказано, сколько не влезло: {заблокировано[-1]['message'] if заблокировано else '—'}")

# Две встречи набрали минуту речи между проверками (загрузка шла фоном),
# а проба оставила одну: пробной становится только первая.
десятая = встреча(70)
лишняя = встреча(70)
с = сервис.trial_state()
проверить(с["left"] == 0 and с["over"], "после десятой встречи проба закончилась")
проверить(сервис.store.trial_counts(десятая) and not сервис.store.trial_counts(лишняя),
          "из двух встреч, набравших речь разом, пробной стала только первая")

# Перезапуск программы: новые встречи не должны стать «старыми», иначе
# каждый запуск обнулял бы пробный период.
одиннадцатая = сервис.store.create_meeting(Meeting(title="После перезапуска")).id
второй_запуск = Store(str(база))
проверить(not второй_запуск.trial_legacy(одиннадцатая),
          "перезапуск не записывает новые встречи в «старые»")
второй_запуск.close()
# Речь в одиннадцатой встрече (например, дописанной) не делает её пробной.
сервис.store.add_segment(TranscriptSegment(meeting_id=одиннадцатая, speaker=Speaker.ME,
                                           text="речь", start=0, end=120))
проверить(сервис.trial_state()["over"], "перезапуск программы не продлевает пробу")
проверить(not сервис._can_use_model(одиннадцатая),
          "встреча, заведённая до перезапуска, не стала «старой» и в модель не пускается")

# --- 4. Новая запись и загрузка не начинаются -----------------------------------
заблокировано.clear()
проверить(сервис.start_recording() is None and not сервис.capture.is_recording,
          "новая запись после пробы не начинается")
проверить(заблокировано and заблокировано[-1]["action"] == "record"
          and str(lic.TRIAL_MEETINGS) in заблокировано[-1]["message"],
          "окну сказано, почему запись не пошла")
проверить(сервис.start_recording(пустая) is None, "в пустую не посчитанную встречу тоже не пишем")
добавлено.clear()
заблокировано.clear()
проверить(сервис.import_files(["d.wav"]) == [] and добавлено == [], "файлы после пробы не загружаются")
проверить(bool(заблокировано), "и об этом сказано")

# --- 5. Десятую встречу можно дописать -----------------------------------------
проверить(сервис.start_recording(десятая) is not None and сервис.capture.is_recording,
          "десятую встречу можно дописать после паузы")
сервис.stop_recording()

# --- 6. Модель ----------------------------------------------------------------
новая = сервис.store.create_meeting(Meeting(title="Сверх пробы")).id
for что, вызов in [("заметки", lambda m: сервис.generate_summary(m)),
                   ("разбор", lambda m: сервис.run_analysis(m, "sales")),
                   ("вопрос", lambda m: сервис.ask(m, "о чём встреча?"))]:
    r = вызов(новая)
    проверить(not r["ok"] and r.get("trial"), f"{что} для встречи сверх пробы закрыт с пометкой trial")
проверить(сервис._can_use_model(десятая), "у пробной встречи модель остаётся")
проверить(сервис._can_use_model(старые[0]), "у встречи до обновления модель остаётся")
проверить(not сервис.run_analysis(новая, "talk").get("trial"), "разбор без модели доступен всегда")

# --- 7. Калькулятор -------------------------------------------------------------
r = сервис.ask(новая, "2+2")
проверить(r["ok"], "счёт в чате работает и без лицензии")
import time  # noqa: E402

for _ in range(50):
    if not сервис._llm_busy:
        break
    time.sleep(0.05)
тексты = [m.text for m in сервис.store.list_chat_messages(новая)]
проверить(тексты[-1:] == ["4"], f"и отвечает верно: {тексты}")

# --- 9. Просмотр --------------------------------------------------------------
проверить(сервис.get_meeting(десятая) is not None and сервис.get_meeting(старые[3]) is not None,
          "встречи открываются")
проверить(isinstance(сервис.list_analyses(десятая), list), "карточки разбора открываются")
проверить(isinstance(сервис.search("речь"), (list, dict)), "поиск работает")

# --- 8. Лицензия --------------------------------------------------------------
seed = Path.home() / ".konspekt-license" / "konspekt_personal_v1.seed"
if seed.exists():
    ключ = lic.make(bytes.fromhex(seed.read_text(encoding="ascii").strip()),
                    {"v": 1, "p": "konspekt", "ed": "personal", "id": "T1", "to": "Проба", "em": "t@t.ru", "n": 1, "iat": "2026-09-30"})
    сервис.settings.license_key = ключ
    с = сервис.trial_state()
    проверить(с["licensed"] and с["left"] is None and not с["over"], "с лицензией счёта нет")
    проверить(сервис.start_recording() is not None, "с лицензией запись идёт")
    сервис.stop_recording()
    проверить(сервис.license_state()["trial"]["licensed"], "окно видит, что счёта нет")
else:
    сервис.settings.license_key = "KSPK1.подделка.подпись"
    проверить(сервис.trial_state()["over"], "подделанный ключ пробу не продлевает")
    print("[пропуск] закрытого ключа нет, настоящая лицензия не проверена")
сервис.settings.license_key = "KSPK1.подделка.подпись"
проверить(сервис.trial_state()["over"], "подделанный ключ пробу не продлевает")

сервис.store.close()
print()
print("Всё в порядке" if not БЕДЫ else f"Бед: {len(БЕДЫ)}")
raise SystemExit(1 if БЕДЫ else 0)
