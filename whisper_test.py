"""Чистка текста Whisper: петли и пустые реплики.

Whisper на тишине и обрывках зацикливается. Оба случая ниже взяты из
живых записей, остальное придумано вокруг них.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

import random
from pathlib import Path

from app.asr.whisper import _clean

# Настоящий случай из живой записи
assert _clean("In In In In In In In In In In In In") == ""
print("[ok] петля из одного слова выбрасывается целиком")

# Осмысленный текст не трогаем
good = "We should review the architecture before the release date of the product"
assert _clean(good) == good
print("[ok] обычный текст не меняется")

# Хвост повторов обрезается, начало остаётся
assert _clean("The report is ready ready ready ready ready") == "The report is ready ready"
print("[ok] хвост повторов обрезается, смысл сохраняется")

# Человек может повторить слово дважды
assert _clean("no no this is not correct") == "no no this is not correct"
print("[ok] двойной повтор считается живой речью")

# Петля из пары слов, любимая ошибка Whisper
assert _clean("Thank you thank you thank you thank you thank you") == "Thank you thank you"
print("[ok] петля из пары слов схлопывается до двух копий")

# Петля из тройки
loop3 = "see you later " * 6
assert _clean(loop3) == "see you later see you later"
print("[ok] петля из тройки слов тоже ловится")

# Регистр не мешает
assert _clean("Yes YES yes Yes yes yes") == "Yes YES"
print("[ok] петля видна независимо от регистра")

# Одни знаки препинания
assert _clean(". . . . . . . .") == ""
print("[ok] строка из знаков препинания выбрасывается")

# Короткие фразы не трогаем
assert _clean("yes") == "yes"
assert _clean("okay then") == "okay then"
print("[ok] короткие фразы проходят как есть")

# Осмысленный текст с петлёй в конце: смысл должен уцелеть
mixed = "The customer asked for a report yes yes yes yes yes yes"
out = _clean(mixed)
assert out.startswith("The customer asked for a report"), out
assert out.count("yes") <= 2, out
print(f"[ok] смысл уцелел, петля срезана: {out!r}")

# Ничего не зависает и не теряется на случайных текстах
random.seed(5)
words = ["the", "report", "yes", "no", "hello", "and", "we"]
for _ in range(500):
    n = random.randint(1, 30)
    text = " ".join(random.choice(words) for _ in range(n))
    result = _clean(text)
    assert len(result.split()) <= n, f"текст вырос: {text!r} -> {result!r}"
print("[ok] 500 случайных текстов обработаны без зависаний и роста")

# Выбор размера модели по настройке
import app.core.service as service_mod
from app.asr.whisper import SIZES, WhisperTranscriber
from app.core.settings import Settings


class _FakeService:
    """Только то, из чего _whisper_dir достаёт настройку."""

    def __init__(self, size):
        self.settings = Settings()
        self.settings.asr.whisper_size = size

    _whisper_dir = service_mod.AppService._whisper_dir


assert set(SIZES) == {"base", "small"}, SIZES
assert Settings().asr.whisper_size in SIZES
print(f"[ok] по умолчанию берётся {Settings().asr.whisper_size}")

from app.core import paths

on_disk = {s for s, cfg in SIZES.items()
           if WhisperTranscriber(paths.models_dir() / cfg["dir"]).is_downloaded()}
print(f"[..] на диске лежат: {sorted(on_disk) or 'ничего'}")

for size in SIZES:
    got = _FakeService(size)._whisper_dir()
    if size in on_disk:
        assert got == SIZES[size]["dir"], f"{size} скачан, а взяли {got}"
        print(f"[ok] просили {size}, он скачан, взяли {got}")
    elif on_disk:
        assert got in {SIZES[s]["dir"] for s in on_disk}, \
            f"{size} не скачан, а откатились на {got}, которого тоже нет"
        print(f"[ok] {size} не скачан, откатились на {got}")

# Незнакомый размер откатывается на умолчание, а не роняет запуск
odd = _FakeService("huge")._whisper_dir()
assert odd == SIZES[Settings().asr.whisper_size]["dir"], odd
print(f"[ok] незнакомый размер откатился на умолчание: {odd}")

# Просили small, а скачан только base: должны взять base, а не молчать
import tempfile
import shutil

tmp_models = Path(tempfile.mkdtemp(prefix="konspekt-whisper-"))
base_dir = tmp_models / SIZES["base"]["dir"]
real_base = paths.models_dir() / SIZES["base"]["dir"]
if real_base.exists():
    shutil.copytree(real_base, base_dir)
    real_models_dir = paths.models_dir
    paths.models_dir = lambda: tmp_models
    try:
        got = _FakeService("small")._whisper_dir()
        assert got == SIZES["base"]["dir"], f"small не скачан, а взяли {got}"
        print("[ok] small не скачан, честно откатились на base")
        # А если нет вообще ничего, возвращаем просимое, чтобы наверху
        # сработала обычная проверка is_downloaded и язык просто не делился
        paths.models_dir = lambda: tmp_models / "пусто"
        got = _FakeService("small")._whisper_dir()
        assert got == SIZES["small"]["dir"], got
        print("[ok] весов нет вовсе: возвращаем просимый размер, без падения")
    finally:
        paths.models_dir = real_models_dir
        shutil.rmtree(tmp_models, ignore_errors=True)

# Имя транскрайбера показывает размер: по логу видно, чем распознавали
for size, cfg in SIZES.items():
    name = WhisperTranscriber(paths.models_dir() / cfg["dir"]).name
    assert name == cfg["dir"], name
print("[ok] имя движка называет размер модели")

print("\nЧистка текста и выбор размера работают.")
