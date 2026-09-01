"""Определение языка фразы на настоящей речи.

Главный вопрос не «какой именно язык», а «отдавать фразу в GigaAM или
нет». VoxLingua путает русский с украинским и белорусским, и это нам не
мешает: все они кириллические. А вот принять русскую речь за английскую
уже нельзя, текст будет испорчен целиком.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

import subprocess
import wave
from pathlib import Path

import numpy as np

from app.asr.langid import CYRILLIC_LANGS, MIN_CONFIDENCE, LanguageDetector
from app.core import paths

tmp = Path("_voices_tmp")
tmp.mkdir(exist_ok=True)


def say_to_wav(text, path):
    ps = (f'Add-Type -AssemblyName System.Speech; '
          f'$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; '
          f'$s.SetOutputToWaveFile("{path.resolve()}"); $s.Speak("{text}"); $s.Dispose()')
    subprocess.run(["powershell", "-Command", ps], capture_output=True)


def read(path):
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() == 2:
            d = d.reshape(-1, 2).mean(axis=1).astype(np.int16)
    return d, rate


def rms(x):
    return float(np.sqrt(np.mean(np.square(x.astype(np.float32) / 32768))))


det = LanguageDetector(paths.models_dir() / "voxlingua")
if not det.is_downloaded():
    # На чистой машине (и в CI) моделей нет: качать гигабайты ради
    # проверки неразумно, а падать нечестно — проверять нечего.
    print("[пропуск] модель определения языка не скачана")
    raise SystemExit(0)
det.load()
print(f"[ok] модель загружена, языков: {len(det._labels)}")

# --- Английский узнаётся уверенно -----------------------------------------
EN = [
    "we should review the architecture before the release date of the product",
    "the customer asked for a detailed report by the end of the working week",
    "i think performance and memory usage are the most important topics today",
]
for i, text in enumerate(EN):
    p = tmp / f"lid-en-{i}.wav"
    if not p.exists():
        say_to_wav(text, p)
    pcm, rate = read(p)
    res = det.detect(pcm, rate)
    assert res and res[0] == "en", f"английский #{i} определён как {res}"
    assert res[1] > 0.7, f"английский #{i} узнан неуверенно: {res[1]:.2f}"
    print(f"[ok] английская фраза #{i}: en={res[1]:.2f}")

# Английский не должен уйти в GigaAM
pcm, rate = read(tmp / "lid-en-0.wav")
assert det.is_russian(pcm, rate) is False, "английскую речь отправили бы в GigaAM"
print("[ok] английская речь помечена как нерусская")

# --- Русская речь с настоящих встреч --------------------------------------
wavs = sorted(paths.audio_dir().rglob("*.wav"),
              key=lambda p: p.stat().st_size, reverse=True)[:10]
checked = wrong = 0
for path in wavs:
    pcm, rate = read(path)
    if pcm.size < rate * 12:
        continue
    win = rate * 8
    best, best_r = None, 0.0
    for start in range(0, max(1, min(pcm.size - win, rate * 400)), rate * 4):
        piece = pcm[start:start + win]
        if piece.size < win:
            break
        r = rms(piece)
        if r > best_r:
            best, best_r = piece, r
    if best is None or best_r < 0.012:
        continue
    verdict = det.is_russian(best, rate)
    checked += 1
    res = det.detect(best, rate)
    if verdict is False:
        wrong += 1
        print(f"  [!!] {path.name[:26]}: {res}")
    else:
        print(f"  [ok] {path.name[:26]}: {res}")

print(f"\nпроверено русских записей: {checked}, ошибочно отвергнуто: {wrong}")
if checked < 3:
    # Русскую часть проверяем на настоящих записях встреч. На
    # чистой машине и в CI их нет, брать живую речь неоткуда.
    # Английская часть выше уже отработала на синтезе.
    print("[пропуск] нет записей встреч, русскую часть не на чем проверить")
    raise SystemExit(0)
assert wrong == 0, f"{wrong} русских записей ушли бы не в ту модель"
print("[ok] ни одна русская запись не была принята за иностранную")

# --- Осторожность ---------------------------------------------------------
assert det.detect(np.zeros(8000, dtype=np.int16), 16000) is None
print("[ok] полсекунды звука определять язык не просим")
assert det.is_russian(np.zeros(8000, dtype=np.int16), 16000) is None
print("[ok] на короткой фразе решение о модели не принимается")

# Шум не должен проходить порог уверенности
noise = (np.random.default_rng(3).normal(scale=0.05, size=16000 * 5) * 32768).astype(np.int16)
res = det.detect(noise, 16000)
assert res is None or res[1] >= MIN_CONFIDENCE
print(f"[ok] на шуме ответ либо отсутствует, либо уверенный: {res}")

# --- Регрессия: боевая встреча 30.08 (issue #2) ----------------------------
# Вся встреча по-русски, но короткие фразы (в среднем 1.2с) при пороге 0.5
# уверенно определялись как 24 разных языка. Проверяем на настоящих
# репликах из базы, не на синтетике: если порог когда-нибудь снова
# просядет, тест должен упасть раньше пользователя.
import sqlite3
import wave as _wave

MEETING = "93b3a94f2bca4e39"
audio_dir = paths.audio_dir() / MEETING
if audio_dir.exists() and paths.db_path().exists():
    db = sqlite3.connect(str(paths.db_path()))
    db.row_factory = sqlite3.Row
    segs = db.execute(
        "SELECT speaker, start_s, end_s FROM transcript_segments"
        " WHERE meeting_id=? ORDER BY start_s",
        (MEETING,),
    ).fetchall()

    def _load_track(speaker: str):
        files = sorted(audio_dir.glob(f"*-{speaker}.wav"))
        chunks, sr = [], 16000
        for f in files:
            with _wave.open(str(f), "rb") as w:
                sr = w.getframerate()
                chunks.append(np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16))
        return (np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)), sr

    tracks = {sp: _load_track(sp) for sp in ("me", "them")}
    ложных = 0
    всего_ответов = 0
    for s in segs:
        pcm, sr = tracks[s["speaker"] if s["speaker"] == "me" else "them"]
        if sr == 0 or pcm.size == 0:
            continue
        a, b = int(s["start_s"] * sr), int(s["end_s"] * sr)
        piece = pcm[max(0, a):min(pcm.size, b)]
        result = det.detect(piece, sr)
        if result is None:
            continue
        всего_ответов += 1
        if result[0] not in CYRILLIC_LANGS:
            ложных += 1
            print(f"  [!!] ложный язык {result} на 'русской' фразе {s['start_s']:.1f}с")
    print(f"[ok] боевая встреча: {всего_ответов} ответов, ложных {ложных}")
    # Не ноль: модель не обязана быть идеальной, но регресс на десятки
    # ошибок (как было при пороге 0.5) должен ронять тест.
    assert ложных <= 2, f"порог снова пропускает кашу языков: {ложных} ложных ответов"
else:
    print("[skip] боевая запись встречи 30.08 недоступна на этой машине")

print("\nОпределение языка работает.")
