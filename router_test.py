"""Роутер языков: русский в GigaAM, английский в Whisper."""

import testenv  # noqa: F401  русский вывод в консоли Windows

import subprocess
import wave
from pathlib import Path

import numpy as np

from app.asr.gigaam import MODEL_DIR_NAME as GIGAAM_DIR
from app.asr.gigaam import GigaamTranscriber
from app.asr.langid import MODEL_DIR_NAME as LANGID_DIR
from app.asr.langid import LanguageDetector
from app.asr.router import LanguageRouter
from app.asr.whisper import MODEL_DIR_NAME as WHISPER_DIR
from app.asr.whisper import WhisperTranscriber
from app.core import paths

tmp = Path("_voices_tmp")
tmp.mkdir(exist_ok=True)


def say_to_wav(text, path):
    ps = (f'Add-Type -AssemblyName System.Speech; '
          f'$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; '
          f'$s.SetOutputToWaveFile("{path.resolve()}"); $s.Speak("{text}"); $s.Dispose()')
    subprocess.run(["powershell", "-Command", ps], capture_output=True)


def read16k(path):
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() == 2:
            d = d.reshape(-1, 2).mean(axis=1).astype(np.int16)
    x = d.astype(np.float32) / 32768.0
    if rate != 16000:
        n = int(x.size * 16000 / rate)
        x = np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float32)
    return x


root = paths.models_dir()
whisper = WhisperTranscriber(root / WHISPER_DIR)
assert whisper.is_downloaded(), "Whisper не скачан"
print("[ok] файлы Whisper на месте")

gigaam = GigaamTranscriber(root / GIGAAM_DIR)
detector = LanguageDetector(root / LANGID_DIR)
router = LanguageRouter(gigaam, detector, whisper)

# --- Английская фраза должна пойти в Whisper ------------------------------
EN = "we should review the architecture before the release date of the product"
p = tmp / "router-en.wav"
if not p.exists():
    say_to_wav(EN, p)
wave_en = read16k(p)

segs = list(router.transcribe(wave_en, 16000, "m1", 0.0, "them"))
assert segs, "английская фраза не распознана"
text_en = segs[0].text
print(f"английская фраза -> {segs[0].lang}: {text_en!r}")
assert segs[0].lang == "en", f"язык помечен как {segs[0].lang}"
latin = sum(c.isascii() and c.isalpha() for c in text_en)
cyrillic = sum('а' <= c.lower() <= 'я' for c in text_en)
print(f"   латиница: {latin}, кириллица: {cyrillic}")
assert latin > cyrillic, "английская фраза записана кириллицей, роутер не сработал"
print("[ok] английская фраза записана латиницей, а не «холло дис из»")

# Разумность текста: несколько ключевых слов должны найтись
low = text_en.lower()
hits = sum(w in low for w in ("architecture", "release", "review", "product"))
print(f"   узнано ключевых слов: {hits} из 4")
assert hits >= 2, f"текст не похож на сказанное: {text_en!r}"
print("[ok] Whisper разобрал фразу по смыслу")

# --- Русская фраза должна пойти в GigaAM ----------------------------------
wavs = sorted(paths.audio_dir().rglob("*.wav"), key=lambda p: p.stat().st_size, reverse=True)
ru_done = False
for path in wavs[:6]:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if d.size < rate * 20:
        continue
    piece = d[rate * 5: rate * 15].astype(np.float32) / 32768.0
    if float(np.sqrt(np.mean(piece ** 2))) < 0.015:
        continue
    segs = list(router.transcribe(piece, rate, "m2", 0.0, "me"))
    if not segs:
        continue
    text = segs[0].text
    print(f"\nрусская запись -> {segs[0].lang}: {text[:70]!r}")
    assert segs[0].lang == "ru", f"русскую речь отправили в {segs[0].lang}"
    cyr = sum('а' <= c.lower() <= 'я' for c in text)
    lat = sum(c.isascii() and c.isalpha() for c in text)
    assert cyr > lat, "русская речь записана латиницей"
    print("[ok] русская запись осталась в GigaAM и записана кириллицей")
    ru_done = True
    wave_ru, rate_ru = piece, rate      # пригодится дальше
    break
assert ru_done, "не нашлось русской записи для проверки"

# --- Без определителя всё уходит в русский --------------------------------
plain = LanguageRouter(gigaam)
segs = list(plain.transcribe(wave_en, 16000, "m3", 0.0, "them"))
assert segs and segs[0].lang == "ru"
print("\n[ok] без определителя роутер ведёт себя как раньше: всё в GigaAM")

# --- Неуверенный язык наследуется у той же дорожки -------------------------
router.reset()
router._last["them"] = "de"        # прошлая фраза была немецкой
short = np.zeros(4000, dtype=np.float32)   # слишком коротко для определителя
assert router._decide(short, 16000, "them") == "de", "язык дорожки не унаследован"
assert router._decide(short, 16000, "me") == "ru", "по умолчанию должен быть русский"
print("[ok] на неразборчивой фразе берётся язык прошлой реплики той же дорожки")

router.reset()
assert router._decide(short, 16000, "them") == "ru", "reset не очистил языки"
print("[ok] новая встреча начинается без памяти о языках")

# --- Настроенный язык по умолчанию, а не жёсткое «ru» ----------------------
# Испаноязычный человек не должен получать русский на первой же фразе.
es = LanguageRouter(gigaam, detector, whisper, fallback_lang="es")
assert es._decide(short, 16000, "new_speaker") == "es", \
    "fallback_lang из настроек не используется"
print("[ok] на первой неразборчивой фразе берётся настроенный язык, а не всегда ru")

# --- Неанглийская чужая речь помечается своим кодом -------------------
# Раньше всё нерусское помечалось как «en», даже немецкая речь.
class _FakeDetector:
    """Определитель, всегда слышащий немецкий."""
    def detect(self, pcm, sample_rate=16000):
        return ("de", 0.9)

fake = LanguageRouter(gigaam, _FakeDetector(), whisper)
segs = list(fake.transcribe(wave_en, 16000, "m4", 0.0, "them"))
assert segs, "немецкая ветка ничего не вернула"
assert segs[0].lang == "de", f"язык помечен как {segs[0].lang!r}, а не de"
print(f"[ok] неанглийская чужая речь помечена как {segs[0].lang}, а не огульно en")

# Украинская речь кириллическая: её честнее отдать GigaAM, чем писать латиницей.
class _UkDetector:
    def detect(self, pcm, sample_rate=16000):
        return ("uk", 0.9)

uk = LanguageRouter(gigaam, _UkDetector(), whisper)
segs = list(uk.transcribe(wave_ru, rate_ru, "m5", 0.0, "me"))
if segs:
    assert segs[0].lang == "uk", f"язык помечен как {segs[0].lang!r}"
    text = segs[0].text
    assert any("а" <= c.lower() <= "я" for c in text), "кириллическая речь ушла не в GigaAM"
    print("[ok] украинская речь осталась в GigaAM и помечена как uk")

# --- Текстовая сверка разрывает заражение (issue #2) -----------------------
# Найдено на боевой записи: акустика один раз уверенно (0.93) ошиблась и
# отправила русскую фразу в Whisper; без сверки вся дорожка застревала в
# чужом языке до конца встречи. Тут гоняем настоящую русскую фразу через
# фальшивый детектор, который всегда лжёт "lt", и проверяем, что текстовая
# сверка распознаёт кириллицу в результате и чинит и сегмент, и память.
class _AlwaysLt:
    def detect(self, pcm, sample_rate=16000):
        return ("lt", 0.93)

contagion = LanguageRouter(gigaam, _AlwaysLt(), whisper)
contagion._last["me"] = "ru"  # до ошибки дорожка была русской
segs = list(contagion.transcribe(wave_ru, rate_ru, "m6", 0.0, "me"))
assert segs, "фраза не распознана"
cyr = sum("а" <= c.lower() <= "я" for c in segs[0].text)
if cyr >= 2:
    # Текст получился читаемым кириллическим — сверка обязана была поймать
    # расхождение и переписать язык, иначе баг воспроизведён.
    assert segs[0].lang != "lt", (
        f"текстовая сверка не сработала: акустика соврала lt, "
        f"текст явно кириллический {segs[0].text!r}, а язык остался lt"
    )
    assert contagion._last["me"] != "lt", "память дорожки не починена, заражение продолжится"
    print(f"[ok] текстовая сверка поймала ложный lt и починила дорожку на {segs[0].lang}")
else:
    print("[skip] Whisper на этом куске не дал читаемого кириллического текста, "
          "сверке нечего ловить в этот раз")

print("\nРоутер языков работает.")
