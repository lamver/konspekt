"""Чистка текста Whisper: петли и пустые реплики.

Whisper на тишине и обрывках зацикливается. Оба случая ниже взяты из
живых записей, остальное придумано вокруг них.
"""
import random

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

print("\nЧистка текста работает.")
