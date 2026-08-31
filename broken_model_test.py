"""Битые веса и два экземпляра: транскрипт не должен молча пропадать.

Жалоба пользователя (issue #1): программа обновилась до 0.5.3, микрофон
показывает уровень, разбор файла доходит до «готово», а транскрипта нет
ни у записи, ни у файлов. В его журнале видно причину: работали два
экземпляра Konspekt, оба качали веса модели в один и тот же `.part`,
куски перемешались. Итоговый размер случайно совпал с настоящим, поэтому
все проверки прошли, файл лёг на диск как готовая модель, а onnxruntime
на нём говорит «Protobuf parsing failed». Ошибка глохла внутри
`transcribe`, и человек видел исправную с виду программу без текста.

Проверяем три вещи:
1. повреждённые веса замечаются, выбрасываются и качаются заново;
2. второй процесс не лезет в чужую загрузку;
3. второй запуск программы не поднимает вторую копию, а показывает окно.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import os
import shutil
import tempfile
import time
from pathlib import Path

FAILS = []


def check(ok: bool, message: str) -> None:
    if ok:
        print(f"[ok] {message}")
    else:
        print(f"[FAIL] {message}")
        FAILS.append(message)


def test_broken_weights_are_refetched(tmp: Path) -> None:
    """Битый файл модели: заметить, выбросить, скачать заново."""
    from app.asr.gigaam import MODEL_FILES, GigaamTranscriber, ModelBroken

    model_dir = tmp / "broken"
    model_dir.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES:
        # Ровно случай пользователя: файлы на месте, содержимое мусор.
        (model_dir / name).write_bytes(b"\x00\x01\x02" * 100)

    engine = GigaamTranscriber(model_dir)
    check(engine.is_downloaded(), "по файлам на диске модель выглядит готовой")

    broken = False
    try:
        engine.load()
    except ModelBroken:
        broken = True
    except Exception as exc:  # pragma: no cover
        print(f"    неожиданная ошибка: {exc!r}")
    check(broken, "повреждённые веса распознаны как повреждённые, а не как рабочие")

    engine.discard()
    check(not engine.is_downloaded(),
          "повреждённые веса удалены, программа снова считает модель нескачанной")


def test_service_repairs_and_reports(tmp: Path) -> None:
    """Сервис при битых весах качает заново и говорит об этом в интерфейс."""
    from app.core import paths as paths_mod

    models = tmp / "models"
    models.mkdir(parents=True, exist_ok=True)
    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    paths_mod.data_dir = lambda: data  # type: ignore[assignment]
    paths_mod.audio_dir = lambda: data  # type: ignore[assignment]
    paths_mod.db_path = lambda: data / "konspekt.db"  # type: ignore[assignment]
    paths_mod.models_dir = lambda: models  # type: ignore[assignment]
    paths_mod.settings_path = lambda: data / "settings.json"  # type: ignore[assignment]

    from app.asr.gigaam import ModelBroken
    from app.audio import NullCapture
    from app.core.events import MODEL_DOWNLOAD, bus
    from app.core.service import AppService
    from app.storage.db import Store
    from app.asr.gigaam import MODEL_DIR_NAME, MODEL_FILES

    # Файлы должны существовать по-настоящему: сервис не станет пробовать
    # загрузку там, где весов физически нет, и правильно сделает.
    weights = models / MODEL_DIR_NAME
    weights.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES:
        (weights / name).write_bytes(b"\x00\x01\x02" * 100)

    service = AppService(store=Store(str(data / "konspekt.db")), capture=NullCapture())

    state = {"broken": True, "downloads": 0, "discarded": 0}

    def fake_load():
        if state["broken"]:
            raise ModelBroken("Файлы модели повреждены: Protobuf parsing failed")

    def fake_discard():
        state["discarded"] += 1
        state["broken"] = False
        # Как настоящий discard: файлов больше нет, значит сервис обязан
        # пойти качать заново, а не просто повторно дёрнуть load.
        state["on_disk"] = False
        for name in MODEL_FILES:
            (weights / name).unlink(missing_ok=True)

    def fake_download(on_progress=None):
        state["downloads"] += 1
        state["on_disk"] = True
        for name in MODEL_FILES:
            (weights / name).write_bytes(b"\x00\x01\x02" * 100)

    engine = getattr(service.transcriber, "russian", service.transcriber)
    engine.load = fake_load
    engine.discard = fake_discard
    state["on_disk"] = True
    engine.is_downloaded = lambda: bool(state["on_disk"])
    service.transcriber.is_downloaded = lambda: bool(state["on_disk"])
    service.downloader.run_blocking = fake_download

    events = []
    bus.on(MODEL_DOWNLOAD, events.append)

    ok = service._ensure_asr_model()
    check(ok, "после починки распознавание объявлено готовым")
    check(state["discarded"] == 1, "повреждённые веса выброшены ровно один раз")
    check(state["downloads"] >= 1, "после выброса битых весов пошла новая загрузка")

    texts = " ".join(str(e.get("message", "")) for e in events)
    check("повреждены" in texts,
          "человеку сказали про повреждение, а не оставили пустой транскрипт")

    # Порча обязана всплывать при запуске, а не при первой реплике.
    # Иначе человек нажимает запись, говорит, ждёт и получает пустоту:
    # ровно то, с чем он и пришёл жаловаться.
    state["broken"] = True
    state["discarded"] = 0
    service._asr_repaired = False
    os.environ.pop("KONSPEKT_NO_PREFETCH", None)
    service._prefetch_asr_model()
    os.environ["KONSPEKT_NO_PREFETCH"] = "1"
    deadline = time.time() + 10
    while time.time() < deadline and not state["discarded"]:
        time.sleep(0.05)
    check(state["discarded"] >= 1,
          "битые веса замечены сразу при запуске, до первой записи")


def test_second_process_does_not_corrupt(tmp: Path) -> None:
    """Второй загрузчик не лезет в файл, который качает первый."""
    from app.asr.download import DownloadBusy, ModelDownloader

    dest = tmp / "race"
    first = ModelDownloader("repo", ("a.bin",), dest)
    second = ModelDownloader("repo", ("a.bin",), dest)

    started = []
    release = {"go": False}

    def slow_download(on_progress=None):
        started.append(1)
        while not release["go"]:
            time.sleep(0.01)

    first._download_all = slow_download

    import threading

    thread = threading.Thread(target=first.run_blocking, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while not started and time.time() < deadline:
        time.sleep(0.01)

    busy = False
    try:
        second.run_blocking()
    except DownloadBusy:
        busy = True
    check(busy, "второй экземпляр не полез в чужую загрузку (иначе веса склеятся)")

    release["go"] = True
    thread.join(timeout=5)

    # Замок снимается: следующая загрузка обязана пройти.
    second._download_all = lambda on_progress=None: None
    free = True
    try:
        second.run_blocking()
    except DownloadBusy:
        free = False
    check(free, "после окончания загрузки замок отпущен")


def test_single_instance(tmp: Path) -> None:
    """Второй запуск не поднимает вторую копию, а просит показать окно."""
    from app.core.single import SHOW_REQUEST, SingleInstance, wake_running_instance

    data = tmp / "single"
    data.mkdir(parents=True, exist_ok=True)

    first = SingleInstance(data / "konspekt.lock")
    check(first.acquire(), "первый запуск берёт замок")

    second = SingleInstance(data / "konspekt.lock")
    check(not second.acquire(), "второй запуск не поднимает вторую копию программы")

    wake_running_instance(data)
    check((data / SHOW_REQUEST).exists(),
          "второй запуск просит работающую программу показать окно")

    first.release()
    third = SingleInstance(data / "konspekt.lock")
    check(third.acquire(), "после выхода замок свободен для нового запуска")
    third.release()


def main() -> int:
    os.environ["KONSPEKT_NO_PREFETCH"] = "1"
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-broken-"))
    try:
        test_broken_weights_are_refetched(tmp)
        test_service_repairs_and_reports(tmp)
        test_second_process_does_not_corrupt(tmp)
        test_single_instance(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if FAILS:
        print(f"\nПровалено проверок: {len(FAILS)}")
        return 1
    print("\nВсё хорошо: битые веса чинятся, вторая копия не мешает первой")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
