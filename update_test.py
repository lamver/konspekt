"""Тихое обновление: скачивание, проверка суммы, установка.

В сеть не ходим: подменяем `httpx`, отдаём настоящий файл со своей
контрольной суммой и смотрим, что делает `Updater`. Запускать установщик
по-настоящему тоже нельзя, поэтому подменяем `subprocess.Popen` и
проверяем команду.

Главное, что проверяем, — что программа НЕ запустит файл, если сумма не
сошлась. Тихое обновление означает, что человек не смотрит на экран, и
запуск подменённого установщика от его имени был бы худшим, что мы
можем сделать.
"""

from __future__ import annotations

import testenv  # noqa: F401  русский вывод в консоли Windows

import hashlib
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")

from app.core import updater as up
from app.core.events import UPDATE_STATE, bus

PAYLOAD = b"\x4d\x5a" + b"konspekt setup" * 5000  # похоже на exe, ~70 КБ
GOOD_SUM = hashlib.sha256(PAYLOAD).hexdigest()


class FakeService:
    def __init__(self, recording: bool = False, visible: bool = False,
                 importing: bool = False) -> None:
        self._rec = recording
        self._vis = visible
        self._imp = importing

    def is_recording(self) -> bool:
        return self._rec

    def window_visible(self) -> bool:
        return self._vis

    def is_importing(self) -> bool:
        return self._imp


class FakeStream:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.headers = {"content-length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self, size):
        for i in range(0, len(self._data), size):
            yield self._data[i : i + size]


class FakeSums:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self):
        pass


def patched(tmp: Path, sums: str, body: bytes):
    """Подменить сеть и папку обновлений."""
    up._updates_dir = lambda: tmp
    up.httpx.get = lambda *a, **kw: FakeSums(sums)
    up.httpx.stream = lambda method, url, **kw: FakeStream(body)


def run_download(tmp: Path, sums: str, body: bytes) -> tuple[up.Updater, list[dict]]:
    events: list[dict] = []
    off = bus.on(UPDATE_STATE, events.append)
    real_get, real_stream, real_dir = up.httpx.get, up.httpx.stream, up._updates_dir
    patched(tmp, sums, body)
    try:
        u = up.Updater(FakeService())
        u._download("9.9.9")
    finally:
        up.httpx.get, up.httpx.stream, up._updates_dir = real_get, real_stream, real_dir
        off()
    return u, events


def check_happy_path() -> None:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        u, events = run_download(tmp, f"{GOOD_SUM} *konspekt-9.9.9-setup.exe", PAYLOAD)

        assert u.ready is not None, "файл скачан и проверен, а готовности нет"
        assert u.ready.version == "9.9.9"
        assert u.ready.path.exists()
        assert u.ready.path.read_bytes() == PAYLOAD, "файл повреждён при записи"
        assert not list(tmp.glob("*.part")), "временный файл не убран"

        states = [e["state"] for e in events]
        assert "ready" in states, "интерфейсу не сказали, что обновление готово"
        percents = [e.get("percent") for e in events if e["state"] == "downloading"]
        assert percents and percents[-1] == 100, f"проценты не дошли до конца: {percents}"
        assert len(set(percents)) == len(percents), "проценты повторяются, событий больше нужного"
    print("[ok] обновление скачивается, проверяется и готово к установке")


def check_bad_sum() -> None:
    """Подменённый файл не должен ни сохраниться, ни запуститься."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        u, events = run_download(tmp, f"{'0' * 64} *konspekt-9.9.9-setup.exe", PAYLOAD)

        assert u.ready is None, "файл с чужой суммой признан годным"
        assert not list(tmp.glob("*.exe")), "файл с чужой суммой остался на диске"
        assert not list(tmp.glob("*.part")), "обрывок не удалён"
        assert [e["state"] for e in events][-1] == "error"

        # И на всякий случай: установка ничего не запустит.
        started = []
        real = up.subprocess.Popen
        up.subprocess.Popen = lambda *a, **kw: started.append(a)
        try:
            assert u.install_now() is False
        finally:
            up.subprocess.Popen = real
        assert not started, "запустили установщик, которого не должно быть"
    print("[ok] файл с несовпавшей суммой удалён и не запускается")


def check_no_sums_in_release() -> None:
    """Нет суммы в релизе — не качаем вовсе."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        u, events = run_download(tmp, "деадбиф *чужой-файл.exe", PAYLOAD)
        assert u.ready is None
        assert not list(tmp.iterdir()), "что-то скачали без контрольной суммы"
    print("[ok] без контрольной суммы в релизе обновление не качается")


def check_bad_version() -> None:
    """Испорченный latest.json не должен уводить загрузку в чужое место."""
    u = up.Updater(FakeService())
    for junk in ("", "../../evil", "9.9.9; rm -rf /", "latest", None):
        u.download_later(junk)
        assert u._thread is None, f"начали качать по мусорной версии {junk!r}"
    print("[ok] мусорный номер версии не запускает загрузку")


def check_not_during_recording() -> None:
    """Установщик закрывает программу, поэтому во время записи нельзя."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "setup.exe"
        path.write_bytes(PAYLOAD)

        started: list = []
        real = up.subprocess.Popen
        up.subprocess.Popen = lambda *a, **kw: started.append(a)
        try:
            busy = up.Updater(FakeService(recording=True))
            busy.ready = up.Ready("9.9.9", path)
            assert busy.install_now() is False, "поставили обновление посреди записи"
            assert not started

            free = up.Updater(FakeService(recording=False))
            free.ready = up.Ready("9.9.9", path)
            assert free.install_now() is True, "не поставили обновление, когда было можно"
            assert started, "установщик не запущен"
            args = list(started[0][0])
            assert args[0] == str(path)
            assert "/VERYSILENT" in args, "установка не тихая, человек увидит окна"
        finally:
            up.subprocess.Popen = real
    print("[ok] во время записи не ставим, в покое ставим тихо")


def check_reuses_downloaded() -> None:
    """Уже скачанный файл не качается второй раз: это 60 МБ."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "konspekt-9.9.9-setup.exe").write_bytes(PAYLOAD)

        calls: list = []
        real_get, real_stream, real_dir = up.httpx.get, up.httpx.stream, up._updates_dir
        up._updates_dir = lambda: tmp
        up.httpx.get = lambda *a, **kw: FakeSums(f"{GOOD_SUM} *konspekt-9.9.9-setup.exe")
        up.httpx.stream = lambda *a, **kw: calls.append(a) or FakeStream(PAYLOAD)
        try:
            u = up.Updater(FakeService())
            u._download("9.9.9")
        finally:
            up.httpx.get, up.httpx.stream, up._updates_dir = real_get, real_stream, real_dir

        assert u.ready is not None
        assert not calls, "скачали заново то, что уже лежало на диске"
    print("[ok] уже скачанный файл не качается повторно")


def check_waits_for_idle() -> None:
    """Пока человек в программе, подменять её нельзя.

    Крестик прячет окно в трей, поэтому «выход» может не случиться
    неделями, и обновление ставится в простое. Но простой должен быть
    настоящим: открытое окно, запись или разбор файла его прерывают.
    """
    cases = {
        "открыто окно": FakeService(visible=True),
        "идёт запись": FakeService(recording=True),
        "разбирается файл": FakeService(importing=True),
    }
    for name, service in cases.items():
        u = up.Updater(service)
        assert u._busy(), f"{name}: программа занята, а обновление считает иначе"

    free = up.Updater(FakeService())
    assert not free._busy(), "свёрнутая и молчащая программа считается занятой"
    print("[ok] обновление ждёт настоящего простоя")


def check_installs_after_idle() -> None:
    """Достаточно долгий простой заканчивается тихой установкой."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "setup.exe"
        path.write_bytes(PAYLOAD)

        started: list = []
        real_popen, real_idle = up.subprocess.Popen, up.IDLE_BEFORE_INSTALL
        up.subprocess.Popen = lambda *a, **kw: started.append(list(a[0]))
        up.IDLE_BEFORE_INSTALL = 0.01  # ждать полчаса в тесте некому
        try:
            u = up.Updater(FakeService())
            u.ready = up.Ready("9.9.9", path)
            u._watch_idle()
            for _ in range(60):
                if started:
                    break
                time.sleep(0.5)
        finally:
            up.subprocess.Popen, up.IDLE_BEFORE_INSTALL = real_popen, real_idle

        assert started, "простой затянулся, а обновление так и не поставилось"
        args = started[0]
        assert "/VERYSILENT" in args, "установка не тихая, человек увидит окна"
        assert "/RESTARTKONSPEKT" in args, (
            "после тихой установки программа не вернётся, и значок в трее пропадёт"
        )
    print("[ok] после простоя обновление ставится и программа возвращается")


def main() -> int:
    check_happy_path()
    check_bad_sum()
    check_no_sums_in_release()
    check_bad_version()
    check_not_during_recording()
    check_reuses_downloaded()
    check_waits_for_idle()
    check_installs_after_idle()
    print("\nВсе проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
