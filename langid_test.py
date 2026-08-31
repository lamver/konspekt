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
assert det.is_downloaded(), "модель определения языка не скачана"
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
assert checked >= 3, "мало записей для проверки"
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

print("\nОпределение языка работает.")
