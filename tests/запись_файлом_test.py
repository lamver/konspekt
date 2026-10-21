"""Скачать звук: фраза и вся встреча одним WAV.

Запись лежит заходами и дорожками, каждый кусок со своим местом во
времени. Собранный файл должен держать общую шкалу: пауза между заходами
остаётся тишиной, вторая дорожка ложится в свой канал. Как и в
clip_test, куски размечены тонами, по частоте видно, тот ли кусок попал
в то ли место.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import io
import math
import shutil
import tempfile
import wave
from pathlib import Path

import numpy as np

RATE = 16000


def tone(path: Path, freq: float, seconds: float) -> None:
    t = np.arange(int(RATE * seconds), dtype=np.float32) / RATE
    data = (np.sin(2 * math.pi * freq * t) * 16000).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())


def main_freq(arr: np.ndarray) -> float:
    arr = arr.astype(np.float64)
    if arr.size < 64:
        return 0.0
    spectrum = np.abs(np.fft.rfft(arr * np.hanning(arr.size)))
    return float(np.fft.rfftfreq(arr.size, 1 / RATE)[int(np.argmax(spectrum))])


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == RATE
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        return data.reshape(-1, w.getnchannels()), w.getnchannels()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-wavout-"))
    try:
        from app.core import paths as paths_mod

        paths_mod.data_dir = lambda: tmp  # type: ignore[assignment]
        audio = tmp / "audio"
        audio.mkdir(parents=True, exist_ok=True)
        paths_mod.audio_dir = lambda: audio  # type: ignore[assignment]
        paths_mod.db_path = lambda: tmp / "konspekt.db"  # type: ignore[assignment]

        from app.core.models import Meeting
        from app.core.service import AppService
        from app.storage.db import Store

        store = Store(str(tmp / "konspekt.db"))
        meeting = Meeting(title="Проверка: скачать / запись?")
        store.create_meeting(meeting)

        # Два захода с паузой в пять секунд: 0–10 с и 15–25 с. В каждом
        # свои тона на дорожках, чтобы отличить и заход, и дорожку.
        plan = [(0.0, 440.0, 880.0), (15.0, 1320.0, 660.0)]
        for start, f_me, f_them in plan:
            for track, freq in (("me", f_me), ("them", f_them)):
                f = audio / meeting.id / f"{int(start):02d}-{track}.wav"
                tone(f, freq, 10.0)
                store.add_audio_chunk(meeting.id, track, str(f), start, 10.0)

        service = AppService.__new__(AppService)
        service.store = store

        # --- вся встреча: стерео, общая шкала, пауза тишиной
        out = tmp / "вся.wav"
        length = service.write_meeting_audio(meeting.id, out)
        assert length and abs(length - 25.0) < 0.01, f"длительность {length}"
        data, channels = read_wav(out)
        assert channels == 2, "две дорожки должны лечь в два канала"
        assert data.shape[0] == 25 * RATE, f"кадров {data.shape[0]}"
        sec = lambda a, b: slice(int(a * RATE), int(b * RATE))  # noqa: E731
        checks = [
            (0, 2.0, 4.0, 440.0, "я, первый заход"),
            (1, 2.0, 4.0, 880.0, "собеседник, первый заход"),
            (0, 17.0, 19.0, 1320.0, "я, второй заход"),
            (1, 17.0, 19.0, 660.0, "собеседник, второй заход"),
        ]
        for ch, a, b, expect, name in checks:
            got = main_freq(data[sec(a, b), ch])
            assert abs(got - expect) < 30, f"{name}: ждали {expect:.0f} Гц, получили {got:.0f}"
            print(f"[ok] {name}: {got:.0f} Гц на своём месте и в своём канале")
        gap = np.abs(data[sec(11.0, 14.0), :]).max()
        assert gap == 0, f"в паузе между заходами не тишина: {gap}"
        print("[ok] пауза между заходами осталась тишиной, шкала времени общая")

        # --- одна дорожка: файл моно
        solo = Meeting(title="Только микрофон")
        store.create_meeting(solo)
        f = audio / solo.id / "00-me.wav"
        tone(f, 440.0, 3.0)
        store.add_audio_chunk(solo.id, "me", str(f), 0.0, 3.0)
        out2 = tmp / "моно.wav"
        assert service.write_meeting_audio(solo.id, out2) == 3.0
        _, ch = read_wav(out2)
        assert ch == 1, "одна дорожка должна дать моно"
        print("[ok] одна дорожка пишется в моно")

        # --- фраза файлом: тот же кусок, что у «переслушать»
        wav = service.clip_wav(meeting.id, 16.0, 18.0, "them")
        assert wav and wav[:4] == b"RIFF", "фраза должна быть готовым WAV"
        with wave.open(io.BytesIO(wav)) as w:
            arr = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        got = main_freq(arr)
        assert abs(got - 660.0) < 30, f"фраза собеседника из второго захода: {got:.0f} Гц"
        print(f"[ok] фраза файлом: {got:.0f} Гц, кусок из нужного захода и дорожки")

        # --- без записи: тихий отказ
        empty = Meeting(title="Без записи")
        store.create_meeting(empty)
        assert service.write_meeting_audio(empty.id, tmp / "нет.wav") is None
        assert service.clip_wav(empty.id, 0.0, 1.0) is None
        print("[ok] у встречи без записи сохранять нечего, программа не падает")

        # --- имя файла из названия встречи без запрещённых символов
        from app.ui.api import _имя_файла

        assert _имя_файла('Проверка: скачать / запись?') == "Проверка скачать запись"
        assert _имя_файла("") == "запись"
        print("[ok] название встречи превращается в допустимое имя файла")

        # --- подпись фильтра диалога такая, какую принимает pywebview
        import re

        from app.core.i18n import t as перевод

        правило = r"^([\w ]+)\((\*(?:\.(?:\w+|\*))*(?:;\*(?:\.(?:\w+|\*))*)*)\)$"
        for язык in ("ru", "en", "es", "sr"):
            подпись = перевод("python.file_dialog.wav_filter", язык)
            assert re.search(правило, подпись), f"{язык}: pywebview отвергнет фильтр {подпись!r}"
        print("[ok] фильтр «Сохранить как» на всех языках принимает pywebview")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
