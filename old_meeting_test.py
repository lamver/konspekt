"""Назвать говорящего в старой встрече, уже после перезапуска программы.

Именно здесь был баг: состав участников жил только в памяти, поэтому
открыв вчерашнюю встречу, назвать в ней человека было невозможно.
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
for p in svc.store.list_people():
    svc.store.delete_person(p.id)

svc.embedder.load()
mid = svc.create_meeting("вчерашняя встреча")["id"]
svc._prepare_voices(mid)

t = 0.0
for round_no, phrase in enumerate(PHRASES):
    for vi, voice in enumerate(VOICES):
        p = tmp / f"old-{vi}-{round_no}.wav"
        if not p.exists():
            say_to_wav(voice, phrase, p)
        pcm, rate = read_wav(p)
        assigned = svc.roster.assign("them", svc.embedder.embed(pcm, rate))
        svc.store.add_segment(TranscriptSegment(
            meeting_id=mid, speaker=Speaker.THEM, text=phrase,
            start=t, end=t + 2.0,
            voice_id=assigned.id if assigned else "",
            voice_label=assigned.label if assigned else ""))
        t += 2.5

svc._remember_voices(mid)
print("встреча записана:", [(v["label"], v["lines"]) for v in svc.meeting_voices(mid)])

# --- Программу закрыли и открыли заново -----------------------------------
svc.shutdown()
fresh = AppService()          # новый процесс: состав участников пуст
assert not fresh.roster.voices(), "состав участников не пуст на старте"
print("[ok] после перезапуска состав участников пуст, как и должно быть")

voices = fresh.meeting_voices(mid)
print("в старой встрече:", [(v["label"], v["lines"]) for v in voices])
assert len(voices) == 2, f"участников {len(voices)}"

target = voices[0]
res = fresh.name_voice(mid, target["voice_id"], "Анна")
assert res["person_id"], "голос не попал в базу: тот самый баг вернулся"
print(f"[ok] в старой встрече говорящий назван, голос сохранён ({res['person_id'][:8]})")

after = fresh.meeting_voices(mid)
named = [v for v in after if v["label"] == "Анна"]
assert named and named[0]["lines"] == target["lines"]
print(f"[ok] имя разошлось по всем {named[0]['lines']} репликам")

people = fresh.store.list_people()
assert any(p.name == "Анна" for p in people), "в базе голосов пусто"
print(f"[ok] в базе знакомых: {[p.name for p in people]}")

# --- И теперь этот голос должен узнаваться на новой встрече ---------------
m2 = fresh.create_meeting("сегодняшняя встреча")["id"]
fresh._prepare_voices(m2)
pcm, rate = read_wav(tmp / "old-0-0.wav")
v = fresh.roster.assign("them", fresh.embedder.embed(pcm, rate))
assert v.label == "Анна", f"не узнали, метка «{v.label}»"
print(f"[ok] на новой встрече голос узнан как «{v.label}»")

fresh.delete_meeting(mid)
fresh.delete_meeting(m2)
for p in fresh.store.list_people():
    fresh.store.delete_person(p.id)
print("\nИмена работают и в старых встречах.")
