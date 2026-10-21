"""Выгрузка расшифровки: форматы, выбор реплик, звук выбранных фраз.

Субтитры проверяем строго по форме: плеер не прощает запятую вместо
точки в WebVTT и наоборот, и такой файл просто не откроется. Звук
выбранных фраз, как и в других проверках записи, размечен тонами.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import io
import json
import math
import shutil
import tempfile
import wave
from pathlib import Path

import numpy as np

from app.core import выгрузка

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
    spectrum = np.abs(np.fft.rfft(arr * np.hanning(arr.size)))
    return float(np.fft.rfftfreq(arr.size, 1 / RATE)[int(np.argmax(spectrum))])


def check_formats() -> None:
    строки = [
        {"start": 0.0, "end": 2.5, "who": "Я", "text": "Привет"},
        {"start": 3661.2, "end": 3661.2, "who": "Собеседник", "text": "Пока"},
    ]
    srt = выгрузка.собрать("srt", "Встреча", строки)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,500\nЯ: Привет\n"), srt
    assert "2\n01:01:01,200 --> 01:01:02,200\nСобеседник: Пока" in srt, "у реплики без длины субтитр должен жить секунду"
    vtt = выгрузка.собрать("vtt", "Встреча", строки)
    assert vtt.startswith("WEBVTT\n\n00:00:00.000 --> 00:00:02.500\n<v Я>Привет\n"), vtt
    data = json.loads(выгрузка.собрать("json", "Встреча", строки))
    assert data["title"] == "Встреча" and data["segments"][1]["speaker"] == "Собеседник"
    assert data["segments"][0]["end"] == 2.5
    txt = выгрузка.собрать("txt", "Встреча", строки)
    assert txt == "Встреча\n\n[00:00] Я: Привет\n[1:01:01] Собеседник: Пока\n", repr(txt)
    md = выгрузка.собрать("md", "Встреча", строки)
    assert md.startswith("# Встреча\n\n**Я** · 00:00  \nПривет\n"), md
    assert выгрузка.формат_по_имени(r"C:\a\встреча.SRT") == "srt"
    assert выгрузка.формат_по_имени("встреча.docx") == "txt"
    assert выгрузка.формат_по_имени("без расширения") == "txt"
    print("[ok] txt, markdown, srt, vtt и json собираются по форме")


def check_service(tmp: Path) -> None:
    from app.core import paths as paths_mod

    paths_mod.data_dir = lambda: tmp  # type: ignore[assignment]
    audio = tmp / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    paths_mod.audio_dir = lambda: audio  # type: ignore[assignment]
    paths_mod.db_path = lambda: tmp / "konspekt.db"  # type: ignore[assignment]

    from app.core.models import Meeting, Speaker, TranscriptSegment
    from app.core.service import AppService
    from app.storage.db import Store

    store = Store(str(tmp / "konspekt.db"))
    meeting = Meeting(title="Созвон")
    store.create_meeting(meeting)
    f_me, f_them = audio / meeting.id / "me.wav", audio / meeting.id / "them.wav"
    tone(f_me, 440.0, 10.0)
    tone(f_them, 880.0, 10.0)
    store.add_audio_chunk(meeting.id, "me", str(f_me), 0.0, 10.0)
    store.add_audio_chunk(meeting.id, "them", str(f_them), 0.0, 10.0)
    segs = [
        TranscriptSegment(meeting_id=meeting.id, speaker=Speaker.ME, text="раз", start=1.0, end=3.0),
        TranscriptSegment(meeting_id=meeting.id, speaker=Speaker.THEM, text="два", start=4.0, end=6.0,
                          voice_label="Анна"),
        TranscriptSegment(meeting_id=meeting.id, speaker=Speaker.ME, text="три", start=7.0, end=9.0),
    ]
    for s in segs:
        store.add_segment(s)

    service = AppService.__new__(AppService)
    service.store = store

    # --- вся расшифровка в файл, имя голоса в подписи
    out = tmp / "созвон.srt"
    assert service.write_transcript(meeting.id, out) == 3
    текст = out.read_text(encoding="utf-8")
    assert "Анна: два" in текст and "Я: раз" in текст, текст
    print("[ok] расшифровка пишется в файл, голоса подписаны именами")

    # --- выбранные реплики: только они, в их порядке
    out2 = tmp / "выбор.json"
    assert service.write_transcript(meeting.id, out2, [segs[2].id, segs[0].id]) == 2
    data = json.loads(out2.read_text(encoding="utf-8"))
    assert [с["text"] for с in data["segments"]] == ["раз", "три"]
    print("[ok] выбранные реплики выгружаются в порядке встречи")

    # --- звук выбранных реплик одним файлом: первая со своей дорожки,
    #     вторая со своей, между ними пауза
    wav = service.clips_wav(meeting.id, [segs[1].id, segs[0].id])
    assert wav and wav[:4] == b"RIFF"
    with wave.open(io.BytesIO(wav)) as w:
        arr = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    длина = arr.size / RATE
    assert 4.9 <= длина <= 5.6, f"две фразы по 2 с с запасом и пауза: {длина:.2f} с"
    первая = main_freq(arr[int(0.5 * RATE): int(2.0 * RATE)])
    вторая = main_freq(arr[int(3.4 * RATE): int(4.9 * RATE)])
    assert abs(первая - 440.0) < 30, f"первая фраза с дорожки «я»: {первая:.0f} Гц"
    assert abs(вторая - 880.0) < 30, f"вторая фраза с дорожки собеседника: {вторая:.0f} Гц"
    пауза = np.abs(arr[int(2.6 * RATE): int(2.9 * RATE)]).max()
    assert пауза == 0, f"между фразами должна быть тишина: {пауза}"
    print(f"[ok] звук выбранных фраз одним файлом: {длина:.1f} с, фразы со своих дорожек, между ними пауза")

    assert service.clips_wav(meeting.id, []) is None
    print("[ok] пустой выбор не даёт файла")


def main() -> int:
    check_formats()
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-export-"))
    try:
        check_service(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
