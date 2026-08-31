"""Проверка разбора голосов: кластеризация и узнавание."""

import testenv  # noqa: F401  русский вывод в консоли Windows

import numpy as np

from app.asr.embedder import cosine
from app.asr.voices import VoiceRoster

rng = np.random.default_rng(7)


def person(dim=256):
    """Голос человека: направление в пространстве отпечатков."""
    v = rng.normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)


def utterance(base, similarity=0.75):
    """Одна фраза этого человека.

    Важна не абсолютная величина шума, а итоговая близость к эталону:
    у настоящего WeSpeaker две фразы одного человека дают примерно 0.7-0.8,
    и проверка должна работать именно на таких числах. Поэтому мешаем
    голос со случайным направлением в заданной пропорции.
    """
    noise = rng.normal(size=base.shape).astype(np.float32)
    noise -= base * float(np.dot(noise, base))      # оставляем только перпендикуляр
    noise /= np.linalg.norm(noise)
    v = base * similarity + noise * float(np.sqrt(1 - similarity**2))
    return (v / np.linalg.norm(v)).astype(np.float32)


anna, boris = person(), person()

# 1. Два голоса в одной дорожке расходятся на два участника
r = VoiceRoster()
for _ in range(6):
    r.assign("them", utterance(anna))
    r.assign("them", utterance(boris))
voices = [v for v in r.voices() if v.track == "them"]
assert len(voices) == 2, f"ожидали двух участников, получили {len(voices)}"
print(f"[ok] два голоса в дорожке разошлись на {len(voices)} участников")

# 2. Реплики распределились поровну, а не 11 к 1
counts = sorted(v.samples for v in voices)
assert counts[0] >= 5, f"перекос: {counts}"
print(f"[ok] реплики распределились ровно: {counts}")

# 3. Один и тот же человек не плодит участников
r2 = VoiceRoster()
for _ in range(15):
    r2.assign("them", utterance(anna))
assert len(r2.voices()) == 1, f"один голос дал {len(r2.voices())} участников"
print("[ok] 15 фраз одного человека остались одним участником")

# 4. Дорожки не смешиваются: тот же голос в микрофоне это отдельная запись
r3 = VoiceRoster()
me = r3.assign("me", utterance(anna))
them = r3.assign("them", utterance(anna))
assert me is not them, "дорожки слились"
assert me.label == "Я", f"в микрофоне ожидали «Я», получили {me.label}"
assert them.label == "Собеседник 1", f"получили {them.label}"
print(f"[ok] дорожки раздельны: «{me.label}» и «{them.label}»")

# 5. Узнавание между встречами: знакомый голос приходит с именем
r4 = VoiceRoster()
r4.remember("p1", "Анна", anna)
v = r4.assign("them", utterance(anna, similarity=0.85))
assert v.label == "Анна", f"не узнали, метка «{v.label}»"
assert v.person_id == "p1"
print(f"[ok] знакомый голос узнан как «{v.label}»")

# 6. Незнакомый голос не получает чужого имени
v2 = r4.assign("them", utterance(boris))
assert v2.label != "Анна", "чужому голосу присвоили имя Анны"
assert v2.person_id is None
print(f"[ok] незнакомый голос остался «{v2.label}»")

# 7. Один человек не может быть двумя участниками встречи
labels = [x.label for x in r4.voices()]
assert labels.count("Анна") == 1, f"Анна продублировалась: {labels}"
print(f"[ok] участники встречи: {labels}")

# 8. Переименование работает
assert r4.rename(v2.id, "Борис")
assert r4.get(v2.id).label == "Борис"
print("[ok] переименование участника работает")

# 9. Короткая фраза без отпечатка не ломает разбор
assert r4.assign("them", None) is None
print("[ok] фраза без отпечатка не создаёт участника")

# 10. Эталон устойчив к одной посредственной фразе.
# Берём такую, которая порог проходит: иначе она уйдёт к новому участнику,
# эталон не сдвинется вообще, и проверка окажется пустой.
r5 = VoiceRoster()
for _ in range(10):
    r5.assign("them", utterance(anna, similarity=0.78))
good = r5.voices()[0]
before = good.centroid.copy()
samples_before = good.samples
r5.assign("them", utterance(anna, similarity=0.60))
after = r5.voices()[0].centroid
assert r5.voices()[0].samples == samples_before + 1, "фраза не была принята, проверка пустая"
assert len(r5.voices()) == 1, "фраза ушла к новому участнику"
shift = cosine(before, after)
assert shift > 0.95, f"одна фраза увела эталон: близость {shift:.3f}"
print(f"[ok] эталон устойчив: после 11-й фразы близость {shift:.3f}")

# 11. Три человека в звонке
r6 = VoiceRoster()
carl = person()
for _ in range(8):
    for p in (anna, boris, carl):
        r6.assign("them", utterance(p))
assert len(r6.voices()) == 3, f"трое дали {len(r6.voices())} участников"
print(f"[ok] три участника звонка: {[v.label for v in r6.voices()]}")

# 12. Мягкий порог для новичка не должен склеивать разных людей.
# Проверяем на десяти независимых наборах, чтобы не поймать удачный случай.
for attempt in range(10):
    a, b, c = person(), person(), person()
    r = VoiceRoster()
    for _ in range(6):
        for p in (a, b, c):
            r.assign("them", utterance(p))
    got = len(r.voices())
    assert got == 3, f"набор {attempt}: трое дали {got} участников"
print("[ok] 10 независимых наборов из троих людей: всегда ровно три участника")

# 13. Реплики не перепутаны между людьми
a, b = person(), person()
r = VoiceRoster()
ids = {"a": [], "b": []}
for _ in range(10):
    ids["a"].append(r.assign("them", utterance(a)).id)
    ids["b"].append(r.assign("them", utterance(b)).id)
assert len(set(ids["a"])) == 1, f"реплики первого разъехались: {set(ids['a'])}"
assert len(set(ids["b"])) == 1, f"реплики второго разъехались: {set(ids['b'])}"
assert set(ids["a"]) != set(ids["b"]), "двух людей слили в одного"
print("[ok] по 10 реплик каждого легли ровно в своего участника")

print("\nВсе проверки разбора голосов пройдены.")
