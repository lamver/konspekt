"""Эталон голоса владельца: запись, сохранение, узнавание."""

import testenv  # noqa: F401  русский вывод в консоли Windows

import subprocess
import time
import wave
from pathlib import Path

import numpy as np

from app.asr.enroll import MIN_SECONDS, VoiceEnrollment
from app.core.models import Speaker, TranscriptSegment
from app.core.service import AppService

tmp = Path("_voices_tmp")
tmp.mkdir(exist_ok=True)


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
    if rate != 16000:
        n = int(data.size * 16000 / rate)
        data = np.interp(np.linspace(0, data.size - 1, n),
                         np.arange(data.size), data).astype(np.int16)
    return data


svc = AppService()
for p in svc.store.list_people():
    svc.store.delete_person(p.id)
svc.embedder.load()

OWNER = "Microsoft David Desktop"
OTHER = "Microsoft Hazel Desktop"
LONG = ("Today is a good day and i am testing how the voice recording works "
        "we will discuss deadlines budget and the tasks for the next week "
        "the wide electrification of the southern regions will give a powerful "
        "push to the production and the development of new technologies")

# --- Слишком короткая запись эталона не годится ---------------------------
short = VoiceEnrollment(svc.embedder)
p = tmp / "enroll-short.wav"
if not p.exists():
    say_to_wav(OWNER, "just a couple of words here", p)
short.feed(read_wav(p))
assert short.result() is None, "короткая запись прошла как эталон"
print(f"[ok] короткая запись ({short.seconds:.1f}с) эталоном не стала")

# --- Нормальная запись ----------------------------------------------------
enroll = VoiceEnrollment(svc.embedder)
p = tmp / "enroll-long.wav"
if not p.exists():
    say_to_wav(OWNER, LONG, p)
pcm = read_wav(p)
print(f"длина записи: {pcm.size/16000:.1f}с")

# Кормим кусками по секунде, как это делает настоящий захват
step = 16000
for i in range(0, pcm.size, step):
    enroll.feed(pcm[i:i + step])

print(f"речи набрано: {enroll.seconds:.1f}с, кусков: {enroll.samples()}")
assert enroll.seconds >= MIN_SECONDS, f"речи мало: {enroll.seconds:.1f}с"
assert enroll.enough, "запись не признана достаточной"
print(f"[ok] набрано достаточно речи: {enroll.seconds:.1f}с")

vector = enroll.result()
assert vector is not None and vector.shape == (256,), f"эталон плохой: {vector}"
assert abs(np.linalg.norm(vector) - 1.0) < 1e-5, "эталон не нормирован"
print(f"[ok] эталон посчитан: {vector.shape[0]} чисел, длина 1.0")

# --- Тишина в эталон не идёт ----------------------------------------------
quiet = VoiceEnrollment(svc.embedder)
quiet.feed(np.zeros(16000 * 20, dtype=np.int16))
assert quiet.seconds == 0.0, f"тишина попала в эталон: {quiet.seconds}с"
assert quiet.result() is None
print("[ok] двадцать секунд тишины дают ноль секунд речи")

# --- Эталон отличает владельца от чужого ----------------------------------
from app.asr.embedder import cosine

p_owner = tmp / "own-check.wav"
p_other = tmp / "other-check.wav"
if not p_owner.exists():
    say_to_wav(OWNER, "this is a completely different sentence from the enrollment", p_owner)
if not p_other.exists():
    say_to_wav(OTHER, "this is a completely different sentence from the enrollment", p_other)

own = svc.embedder.embed(read_wav(p_owner), 16000)
other = svc.embedder.embed(read_wav(p_other), 16000)
s_own, s_other = cosine(vector, own), cosine(vector, other)
print(f"близость к эталону: владелец {s_own:.3f}, чужой {s_other:.3f}")
assert s_own > s_other + 0.2, "эталон плохо отличает владельца"
print("[ok] владелец заметно ближе к эталону, чем чужой голос")

# --- Сохранение через сервис ----------------------------------------------
svc._enrollment = enroll
res = svc.finish_enrollment("Валерий")
assert res["has_owner"] and res["owner_name"] == "Валерий", res
print(f"[ok] эталон сохранён как «{res['owner_name']}»")

owner = svc.store.get_owner()
assert owner is not None and owner.kind == "owner"
assert np.allclose(owner.embedding, vector, atol=1e-6), "вектор исказился"
print("[ok] владелец лежит в базе, вектор цел")

# Перезапись не плодит второго владельца
svc._enrollment = enroll
svc.finish_enrollment("Валерий Петрович")
owners = [p for p in svc.store.list_people() if p.kind == "owner"]
assert len(owners) == 1, f"владельцев стало {len(owners)}"
assert owners[0].name == "Валерий Петрович"
print("[ok] перезапись заменяет эталон, а не плодит второго владельца")

# --- Владелец узнаётся на встрече -----------------------------------------
mid = svc.create_meeting("встреча с владельцем")["id"]
svc._prepare_voices(mid)
v_own = svc.roster.assign("me", own)
v_other = svc.roster.assign("them", other)
print(f"на встрече: микрофон -> «{v_own.label}», динамик -> «{v_other.label}»")
assert v_own.label == "Валерий Петрович", f"владельца не узнали: «{v_own.label}»"
assert v_other.label != "Валерий Петрович", "чужой голос принят за владельца"
print("[ok] владелец узнан по эталону, чужой голос отделён")

svc.delete_meeting(mid)
for p in svc.store.list_people():
    svc.store.delete_person(p.id)
print("\nЭталон голоса владельца работает.")
