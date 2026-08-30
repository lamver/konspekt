"""Проверка декодера файлов: форматы, ловушки, каналы.

Синтетику делаем сами, поэтому проверка работает и на голом
репозитории. Если рядом лежит папка audio_examples, дополнительно
гоняем настоящие записи: там нарочно намешаны подменённые расширения
(текст с именем .mp3 и mp3 с именем .txt), видео без звука и разные
частоты дискретизации.
"""
import wave
from pathlib import Path

import numpy as np

from app.asr.audiofile import UnsupportedAudio, decode, probe

EXAMPLES = Path("audio_examples")
# Примеры в репозиторий не кладём: это 12 МБ звука.
HAVE_EXAMPLES = EXAMPLES.is_dir() and any(EXAMPLES.iterdir())

tmp = Path("_t_audiofile.wav")


def write_wav(path: Path, channels: list[np.ndarray], rate: int = 16000) -> None:
    """Свой wav: столько каналов, сколько дали."""
    n = min(len(c) for c in channels)
    inter = np.empty(n * len(channels), dtype=np.int16)
    for i, ch in enumerate(channels):
        inter[i::len(channels)] = np.clip(ch[:n] * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(len(channels))
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(inter.tobytes())


rng = np.random.default_rng(11)
sec = 16000

# --- основа: свой файл, без внешних примеров ------------------------------
tone = (0.3 * np.sin(np.arange(sec * 5) * 0.05)).astype(np.float32)
write_wav(tmp, [tone])

info = probe(tmp)
assert info.channels == 1, info.channels
assert abs(info.duration - 5.0) < 0.1, info.duration
assert info.title == tmp.stem, info.title
print(f"[ok] свой файл прочитан: {info.codec}, {info.duration:.1f} c, "
      f"заголовок из имени {info.title!r}")

# Звук приходит нормализованным: 16 кГц, моно, float32, в пределах [-1, 1]
for track, pcm, offset in decode(tmp):
    assert pcm.dtype == np.float32, pcm.dtype
    assert pcm.ndim == 1, pcm.shape
    assert np.abs(pcm).max() <= 1.001, np.abs(pcm).max()
    assert offset >= 0
    break
print("[ok] звук приходит как float32 моно 16 кГц в пределах [-1, 1]")

# Ничего не теряем: сколько в файле, столько и прочитали
total = sum(len(pcm) for _t, pcm, _o in decode(tmp))
assert abs(total / 16000 - 5.0) < 0.1, total / 16000
print(f"[ok] прочитано ровно столько, сколько в файле: {total / 16000:.1f} c")

# Смещения идут подряд и без дыр
offsets = [round(o, 3) for _t, _p, o in decode(tmp, chunk_seconds=1.0)]
assert offsets == sorted(offsets), offsets
assert len(set(offsets)) == len(offsets), "смещения повторяются"
assert abs(offsets[1] - offsets[0] - 1.0) < 0.01, offsets
print(f"[ok] смещения идут подряд: {offsets[:4]}... шаг 1 с")

# Частота приводится к 16 кГц, какой бы ни была исходная
write_wav(tmp, [tone[: sec * 2]], rate=44100)
got = sum(len(pcm) for _t, pcm, _o in decode(tmp))
assert abs(got / 16000 - (sec * 2) / 44100) < 0.1, got / 16000
print("[ok] запись 44,1 кГц приводится к 16 кГц без потери длительности")

# --- каналы ---------------------------------------------------------------
# Диалог: говорят по очереди, как в записи звонка
left = np.zeros(sec * 12, dtype=np.float32)
right = np.zeros(sec * 12, dtype=np.float32)
for i in range(6):
    a, b = i * 2 * sec, (i * 2 + 1) * sec
    speech = rng.normal(0, 0.25, sec).astype(np.float32)
    if i % 2 == 0:
        left[a:b] = speech
    else:
        right[a:b] = speech
write_wav(tmp, [left, right])
info = probe(tmp)
assert info.channels == 2, info.channels
assert info.stereo_split, "диалог по каналам не распознан как две дорожки"
print("[ok] диалог по каналам распознан: ведём как две дорожки")

# И разбор действительно идёт двумя дорожками
tracks = {}
for track, pcm, _off in decode(tmp, chunk_seconds=2.0, split_channels=True):
    tracks[track] = tracks.get(track, 0) + len(pcm)
assert set(tracks) == {"me", "them"}, tracks
assert min(tracks.values()) > 0
print(f"[ok] разбор идёт двумя дорожками: {tracks}")

# Обычное стерео: один и тот же звук в обоих каналах
same = rng.normal(0, 0.2, sec * 8).astype(np.float32)
write_wav(tmp, [same, same * 0.98])
assert not probe(tmp).stereo_split, "одинаковые каналы разделили зря"
print("[ok] одинаковые каналы сводятся в моно")

# Стерео, где второй канал пустой: это моно, записанное как стерео
write_wav(tmp, [same, np.zeros_like(same)])
assert not probe(tmp).stereo_split, "пустой канал приняли за собеседника"
print("[ok] пустой второй канал не превращается в собеседника")

# Стерео сводится в моно, когда разделять не просили
write_wav(tmp, [same, same * 0.98])
for _track, pcm, _o in decode(tmp):
    assert pcm.ndim == 1, pcm.shape
    break
print("[ok] стерео сводится в одну дорожку, когда разделять не просили")

tmp.unlink(missing_ok=True)

# --- негодные файлы -------------------------------------------------------
junk = Path("_t_junk.mp3")
junk.write_text("это обычный текст, а не музыка", encoding="utf-8")
try:
    probe(junk)
    raise AssertionError("текст приняли за аудио")
except UnsupportedAudio as e:
    print(f"[ok] текст с именем .mp3 отклонён: {e}")
junk.unlink(missing_ok=True)

empty = Path("_t_empty.wav")
empty.write_bytes(b"")
try:
    probe(empty)
    raise AssertionError("пустой файл приняли")
except UnsupportedAudio as e:
    assert "пуст" in str(e), e
    print(f"[ok] пустой файл отклонён: {e}")
empty.unlink(missing_ok=True)

for bad_path, why in ((Path("нет-такого.wav"), "пропавший файл"), (Path("."), "папка")):
    try:
        probe(bad_path)
        raise AssertionError(f"{why}: ошибки не было")
    except UnsupportedAudio:
        pass
print("[ok] пропавший файл и папка отклоняются понятной ошибкой")

# --- настоящие записи, если они рядом -------------------------------------
if not HAVE_EXAMPLES:
    print("[--] папки audio_examples нет, живые записи пропускаем")
else:
    # Настоящий случай: mp3, которому кто-то поставил расширение .txt
    mp3_as_txt = EXAMPLES / "music_1763907667.txt"
    if mp3_as_txt.exists():
        info = probe(mp3_as_txt)
        assert info.codec.startswith("mp3"), info.codec
        assert info.duration > 5, info.duration
        print(f"[ok] mp3 под именем .txt распознан: {info.codec}, {info.duration:.1f} c")

    # Обратный случай: текст, которому поставили расширение .mp3
    txt_as_mp3 = EXAMPLES / "0aba19b7f3d1.mp3"
    if txt_as_mp3.exists():
        try:
            probe(txt_as_mp3)
            raise AssertionError("текст приняли за аудио")
        except UnsupportedAudio as e:
            print(f"[ok] живой текст с именем .mp3 отклонён: {e}")

    # Видео без звуковой дорожки
    mp4 = EXAMPLES / "6035729_Mother_Father_1280x720.mp4"
    if mp4.exists():
        try:
            probe(mp4)
            raise AssertionError("видео без звука приняли")
        except UnsupportedAudio as e:
            assert "нет звуковой дорожки" in str(e), e
            print(f"[ok] видео без звука отклонено: {e}")

    good, bad = [], []
    for p in sorted(EXAMPLES.iterdir()):
        try:
            good.append(probe(p))
        except UnsupportedAudio:
            bad.append(p.name)
    assert good, "ни один пример не прочитался"
    print(f"[ok] прочитано {len(good)} живых файлов, отклонено {len(bad)}")

    # Длительность из заголовка совпадает с тем, сколько реально прочитали
    for item in good[:6]:
        seconds = sum(len(pcm) for _t, pcm, _o in decode(item.path)) / 16000
        assert seconds > 0, item.path.name
        if item.duration > 0:
            assert abs(seconds - item.duration) < 1.0, \
                f"{item.path.name}: заголовок {item.duration:.1f} c, прочитали {seconds:.1f} c"
    print("[ok] прочитанная длительность совпадает с заголовком")

    # Музыкальное стерео разделять нельзя
    music = EXAMPLES / "music_1763907434.mp3"
    if music.exists():
        info = probe(music)
        assert info.channels == 2, info.channels
        assert not info.stereo_split, "музыку разделили на двух говорящих"
        print("[ok] музыкальное стерео сводится в моно")

print("\nЧтение аудиофайлов работает.")
