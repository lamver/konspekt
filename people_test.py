"""Узнавание голоса между встречами через полный сервис.

Проверяем ровно то, что просил пользователь: назвал человека один раз,
и на следующей встрече он определяется сам.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

import subprocess
import wave
from pathlib import Path

import numpy as np

from app.core.models import Speaker, TranscriptSegment
from app.core.service import AppService

tmp = Path("_voices_tmp")
tmp.mkdir(exist_ok=True)

VOICES = ["Microsoft Hazel Desktop", "Microsoft David Desktop"]
PHRASES = [
    "we should review the architecture before the release date",
    "the second point is about performance and memory usage",
    "finally i want to discuss the plan for the next quarter",
    "let us also check the documentation and the test coverage",
]


def say_to_wav(voice, text, path):
    ps = (f'Add-Type -AssemblyName System.Speech; '
          f'$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; '
          f'$s.SelectVoice("{voice}"); '
          f'$s.SetOutputToWaveFile("{path.resolve()}"); $s.Speak("{text}"); $s.Dispose()')
    subprocess.run(["powershell", "-Command", ps], capture_output=True)


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1).astype(np.int16)
    return data, rate


svc = AppService()
# Чистим базу голосов от прошлых прогонов
for p in svc.store.list_people():
    svc.store.delete_person(p["id"] if isinstance(p, dict) else p.id)

emb = svc.embedder
if not emb.is_downloaded():
    print("[пропуск] модель различения голосов не скачана")
    svc.shutdown()
    raise SystemExit(0)
emb.load()


def hold_meeting(title):
    """Провести встречу: каждый голос произносит свои фразы."""
    mid = svc.create_meeting(title)["id"]
    svc._prepare_voices(mid)
    t = 0.0
    for round_no, phrase in enumerate(PHRASES):
        for vi, voice in enumerate(VOICES):
            p = tmp / f"meet-{vi}-{round_no}.wav"
            if not p.exists():
                say_to_wav(voice, phrase, p)
            pcm, rate = read_wav(p)
            vec = emb.embed(pcm, rate)
            assigned = svc.roster.assign("them", vec)
            seg = TranscriptSegment(
                meeting_id=mid, speaker=Speaker.THEM, text=phrase,
                start=t, end=t + 2.0,
                voice_id=assigned.id if assigned else "",
                voice_label=assigned.label if assigned else "",
                person_id=assigned.person_id if assigned else None,
            )
            svc.store.add_segment(seg)
            t += 2.5
    return mid


# --- Встреча первая: людей никто не знает --------------------------------
m1 = hold_meeting("встреча первая")
voices1 = svc.meeting_voices(m1)
print("встреча 1:", [(v["label"], v["lines"]) for v in voices1])
assert len(voices1) == 2, f"ожидали двоих, получили {len(voices1)}"
assert all(v["label"].startswith("Собеседник") for v in voices1), \
    "незнакомые голоса получили имена"
print("[ok] два незнакомых голоса, оба безымянные")

# Называем обоих
first_voice = voices1[0]["voice_id"]
second_voice = voices1[1]["voice_id"]
r1 = svc.name_voice(m1, first_voice, "Анна")
r2 = svc.name_voice(m1, second_voice, "Борис")
print(f"[ok] названы: Анна ({r1['person_id'][:8]}), Борис ({r2['person_id'][:8]})")

named = svc.meeting_voices(m1)
assert {v["label"] for v in named} == {"Анна", "Борис"}, f"метки не обновились: {named}"
print("[ok] имена проставились во всех репликах встречи")

svc._remember_voices(m1)
people = svc.store.list_people()
assert len(people) == 2, f"в базе {len(people)} голосов вместо двух"
print(f"[ok] в базе голосов: {[p.name for p in people]}")

# --- Встреча вторая: те же люди, должны узнаться --------------------------
m2 = hold_meeting("встреча вторая")
voices2 = svc.meeting_voices(m2)
print("встреча 2:", [(v["label"], v["lines"]) for v in voices2])

labels2 = {v["label"] for v in voices2}
assert labels2 == {"Анна", "Борис"}, f"не узнали: {labels2}"
print("[ok] на новой встрече оба голоса узнаны по именам")

assert all(v["person_id"] for v in voices2), "узнаны, но не привязаны к базе"
print("[ok] реплики привязаны к людям из базы")

# Совпадают ли это те же самые люди, а не новые записи
ids1 = {r1["person_id"], r2["person_id"]}
ids2 = {v["person_id"] for v in voices2}
assert ids1 == ids2, f"узнали как других людей: {ids1} против {ids2}"
assert len(svc.store.list_people()) == 2, "база голосов размножилась"
print("[ok] это те же самые люди, база не размножилась")

# --- Третья встреча: незнакомый голос не получает чужого имени ------------
m3 = svc.create_meeting("встреча третья")["id"]
svc._prepare_voices(m3)
p = tmp / "stranger.wav"
if not p.exists():
    say_to_wav("Microsoft Zira Desktop", PHRASES[0], p)
pcm, rate = read_wav(p)
v = svc.roster.assign("them", emb.embed(pcm, rate))
assert v.label.startswith("Собеседник"), f"чужому голосу дали имя «{v.label}»"
print(f"[ok] третий голос остался безымянным: «{v.label}»")

for mid in (m1, m2, m3):
    svc.delete_meeting(mid)
for person in svc.store.list_people():
    svc.store.delete_person(person.id)

print("\nУзнавание голосов между встречами работает.")
