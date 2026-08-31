"""Эмбеддер на настоящем звуке: разные голоса должны расходиться.

Синтезатор Windows даёт несколько голосов, это и есть разные говорящие.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

import subprocess
import wave
from pathlib import Path

import numpy as np

from app.asr.embedder import VoiceEmbedder, cosine
from app.core import paths

tmp = Path("_voices_tmp")
tmp.mkdir(exist_ok=True)


def voices_list():
    ps = ('Add-Type -AssemblyName System.Speech; '
          '(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() '
          '| ForEach-Object { $_.VoiceInfo.Name }')
    r = subprocess.run(["powershell", "-Command", ps], capture_output=True, text=True)
    return [v.strip() for v in r.stdout.splitlines() if v.strip()]


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


names = voices_list()
print("голоса в системе:", names)
assert len(names) >= 2, "нужно хотя бы два голоса синтезатора"

emb = VoiceEmbedder(paths.models_dir() / "wespeaker")
assert emb.is_downloaded(), "модель эмбеддера не скачана"
emb.load()
print(f"[ok] модель загружена, вход: {emb._input_name}")

PHRASES = [
    "the quick brown fox jumps over the lazy dog near the river bank",
    "software development requires careful attention to important details",
    "yesterday we discussed the quarterly results with the whole team",
]

vecs = {}
for v in names[:2]:
    vecs[v] = []
    for i, text in enumerate(PHRASES):
        p = tmp / f"{v.split()[1]}-{i}.wav"
        if not p.exists():
            say_to_wav(v, text, p)
        pcm, rate = read_wav(p)
        e = emb.embed(pcm, rate)
        assert e is not None, f"пустой отпечаток для {v} фраза {i}"
        assert e.shape == (256,), f"неожиданный размер {e.shape}"
        vecs[v].append(e)
    print(f"[ok] {v}: {len(vecs[v])} отпечатков по 256 чисел")

a, b = names[0], names[1]
same_a = [cosine(vecs[a][i], vecs[a][j]) for i in range(3) for j in range(i + 1, 3)]
same_b = [cosine(vecs[b][i], vecs[b][j]) for i in range(3) for j in range(i + 1, 3)]
diff = [cosine(x, y) for x in vecs[a] for y in vecs[b]]

print(f"\nодин голос ({a}):  {[f'{s:.3f}' for s in same_a]}  среднее {np.mean(same_a):.3f}")
print(f"один голос ({b}):  {[f'{s:.3f}' for s in same_b]}  среднее {np.mean(same_b):.3f}")
print(f"разные голоса:     среднее {np.mean(diff):.3f}, максимум {max(diff):.3f}")

assert np.mean(same_a) > max(diff), "фразы одного голоса не ближе, чем чужие"
assert np.mean(same_b) > max(diff), "фразы второго голоса не ближе, чем чужие"
print("\n[ok] свой голос всегда ближе чужого")

# Тот же звук должен давать тот же вектор
pcm, rate = read_wav(tmp / f"{a.split()[1]}-0.wav")
assert cosine(emb.embed(pcm, rate), emb.embed(pcm, rate)) > 0.999
print("[ok] повторный расчёт даёт тот же отпечаток")

# Слишком короткий кусок отпечатка не даёт
assert emb.embed(np.zeros(1000, dtype=np.int16), 16000) is None
print("[ok] слишком короткий кусок отпечатка не даёт")

print("\nЭмбеддер работает на настоящем звуке.")
