"""Скачивание весов модели с прогрессом и докачкой.

Интернет бывает слабым, поэтому:
- качаем каждый файл во временный `.part` и переименовываем только после
  успеха, чтобы битый огрызок никогда не выглядел как готовая модель;
- при обрыве продолжаем с того места, где встали (HTTP Range);
- сверяем размер с тем, что обещал сервер.

Берём напрямую по HTTPS, а не через huggingface_hub: так у нас есть
честный прогресс в процентах для UI и нет кэша с симлинками, который
потом мешает сборке бинарника.
"""

from __future__ import annotations

import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

HF_BASE = "https://huggingface.co/{repo}/resolve/main/{name}"
CHUNK = 1 << 16  # 64 КБ
# Слабый интернет рвёт соединение посреди файла. Каждый обрыв это ещё
# одна попытка с того места, где встали, а не потеря всей загрузки.
#
# Потолок высокий не от щедрости: модель для заметок весит 1.8 ГБ, а на
# живом канале за один заход приходит 200-300 МБ. Это уже 7-9 заходов
# при идеальном раскладе, и двадцати попыток не хватало. Загрузку
# впустую всё равно останавливают три подряд безрезультатные попытки,
# так что большое число здесь ничего не тратит.
RETRIES = 200
RETRY_PAUSE = 3.0  # секунды между попытками

# Hugging Face режет скорость одного соединения. Замер на живом канале:
# в один поток файл идёт 0.3 МБ/с, в восемь — 1.6 МБ/с, то есть модель
# на 214 МБ приходит за две минуты вместо двенадцати. Канал тут ни при
# чём, ограничение именно на соединение, поэтому просим куски файла
# несколькими соединениями сразу.
ПОТОКОВ = 8
# Меньше этого размера дробить не стоит: накладные расходы на лишние
# соединения съедят выигрыш, а мелких файлов у модели большинство.
ПОРОГ_ДРОБЛЕНИЯ = 32 << 20  # 32 МБ

# Колбэк прогресса: (имя файла, скачано байт, всего байт)
ProgressCallback = Callable[[str, int, int], None]


class _ФайлБольшой(Exception):
    """Файл стоит качать несколькими соединениями, а не одним.

    Не беда, а способ сообщить наверх размер, который уже пришёл в
    ответе сервера: отдельный запрос за ним был бы лишним походом в
    сеть на каждый мелкий файл модели.
    """

    def __init__(self, всего: int) -> None:
        super().__init__(f"файл на {всего} байт качаем кусками")
        self.всего = всего


class DownloadCancelled(RuntimeError):
    """Пользователь остановил скачивание."""


class DownloadBusy(RuntimeError):
    """Тот же файл уже качает другой процесс."""


class _FileLock:
    """Замок между процессами на каталог модели.

    Зачем. По жалобе пользователя: у него одновременно работали два
    экземпляра программы, и оба дописывали веса в один и тот же `.part`.
    Куски перемешались, итоговый размер случайно совпал с настоящим, файл
    прошёл все проверки и лёг на диск как готовая модель. А при загрузке
    onnxruntime говорил «Protobuf parsing failed», ошибка глохла, и
    человек видел просто пустой транскрипт без единого намёка на причину.

    Делаем эксклюзивное создание файла-замка: второй процесс не лезет в
    чужую загрузку, а спокойно ждёт, пока первый закончит.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if self._stale():
                # Прошлый процесс убили, замок остался. Иначе загрузка была
                # бы заблокирована навсегда до ручной чистки.
                self.path.unlink(missing_ok=True)
                return self.acquire()
            return False
        os.write(self._fd, str(os.getpid()).encode())
        return True

    def _stale(self) -> bool:
        try:
            pid = int(self.path.read_text().strip() or 0)
        except (OSError, ValueError):
            return True
        if pid <= 0:
            return True
        if pid == os.getpid():
            # Замок держит этот же процесс: другой поток уже качает.
            # Считать его залипшим нельзя, иначе два потока снова полезут
            # в один файл — ровно та поломка, от которой замок и стоит.
            return False
        return not _pid_alive(pid)

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self.path.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс. Замок мёртвого процесса снимаем сами."""
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class ModelDownloader:
    """Качает набор файлов модели в каталог. Можно отменить."""

    def __init__(self, repo: str, files: tuple[str, ...], dest: Path) -> None:
        self.repo = repo
        self.files = files
        self.dest = Path(dest)
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self) -> None:
        self._cancel.set()

    def start(
        self,
        on_progress: ProgressCallback | None = None,
        on_done: Callable[[str | None], None] | None = None,
    ) -> None:
        """Запустить скачивание в фоне. on_done получает текст ошибки или None."""
        if self.is_running:
            log.warning("Скачивание уже идёт")
            return
        self._cancel.clear()

        def run() -> None:
            error: str | None = None
            try:
                self.run_blocking(on_progress)
            except DownloadCancelled:
                error = "Скачивание отменено"
            except Exception as exc:
                error = str(exc)
                log.exception("Скачивание модели не удалось")
            if on_done:
                on_done(error)

        self._thread = threading.Thread(target=run, name="model-download", daemon=True)
        self._thread.start()

    def run_blocking(self, on_progress: ProgressCallback | None = None) -> None:
        self.dest.mkdir(parents=True, exist_ok=True)
        lock = _FileLock(self.dest / ".download.lock")
        if not lock.acquire():
            raise DownloadBusy(
                "Модель уже качает другой экземпляр Konspekt"
            )
        try:
            self._download_all(on_progress)
        finally:
            lock.release()

    def _download_all(self, on_progress: ProgressCallback | None = None) -> None:
        for name in self.files:
            target = self.dest / name
            if target.exists():
                # Файл готов, но рядом мог остаться недокачанный кусок от
                # прошлой попытки: он занимает место и сбивает подсчёт
                # скачанного в окне.
                target.with_suffix(target.suffix + ".part").unlink(missing_ok=True)
                continue
            # Имя файла может содержать путь, как «onnx/encoder.onnx» у
            # моделей Hugging Face. Без этого запись падает на несуществующем
            # каталоге.
            target.parent.mkdir(parents=True, exist_ok=True)
            self._fetch_with_retries(name, target, on_progress)

    def _fetch_with_retries(
        self, name: str, target: Path, on_progress: ProgressCallback | None
    ) -> None:
        """Качать, пока не выйдет. Каждая попытка продолжает с места обрыва.

        На слабом канале соединение рвётся посреди большого файла, и без
        этого цикла двухсотмегабайтная модель почти никогда не докачается.
        Прогресса ради считаем попытку неудачной только если она не сдвинула
        файл ни на байт: тогда дело не в канале, а в чём-то постоянном.
        """
        part = target.with_suffix(target.suffix + ".part")
        last_error: Exception | None = None
        # Считаем именно безрезультатные попытки подряд. Если файл растёт,
        # канал живой и обрывы ничего не значат: на большой модели их
        # бывает три десятка, и обрывать загрузку по общему числу попыток
        # значило бы не докачать её никогда.
        впустую = 0
        for attempt in range(1, RETRIES + 1):
            if target.exists():
                # Файл появился, пока мы качали. Так бывает, если его
                # положил другой экземпляр программы или человек скопировал
                # руками из соседней установки. Качать второй раз то же
                # самое незачем, и без этой проверки загрузка продолжалась
                # часами при готовом файле на диске.
                log.info("Файл %s уже на месте, качать не надо", name)
                part.unlink(missing_ok=True)
                return
            before = part.stat().st_size if part.exists() else 0
            try:
                self._fetch(name, target, on_progress)
                return
            except _ФайлБольшой as большой:
                # Файл крупный и мы только начали: докачивать нечего,
                # поэтому берём его несколькими соединениями сразу.
                self._качать_кусками(name, target, большой.всего, on_progress)
                return
            except DownloadCancelled:
                raise
            except Exception as exc:
                last_error = exc
                after = part.stat().st_size if part.exists() else 0
                впустую = впустую + 1 if after <= before else 0
                log.warning(
                    "Обрыв на %s (попытка %d из %d, на диске %d из %d байт): %s",
                    name, attempt, RETRIES, after, before, exc,
                )
                if впустую >= 3:
                    # Три попытки подряд без единого нового байта: дело не
                    # в канале, а в чём-то постоянном.
                    break
                if self._cancel.wait(RETRY_PAUSE):
                    raise DownloadCancelled(name) from exc
        raise RuntimeError(f"Не удалось скачать {name}: {last_error}")

    def _размер_на_сервере(self, name: str) -> int:
        """Сколько весит файл и берёт ли сервер запросы по кускам.

        Ноль значит «дробить нельзя»: либо размер неизвестен, либо
        сервер не понимает Range. Тогда качаем как раньше, одним
        потоком, и ничего не ломаем.
        """
        url = HF_BASE.format(repo=self.repo, name=name)
        запрос = urllib.request.Request(
            url, headers={"User-Agent": "konspekt", "Range": "bytes=0-0"})
        try:
            with urllib.request.urlopen(запрос, timeout=30) as ответ:
                if ответ.status != 206:
                    return 0
                диапазон = ответ.headers.get("Content-Range", "")
                return int(диапазон.rsplit("/", 1)[-1])
        except (OSError, ValueError, urllib.error.URLError):
            return 0

    def _качать_кусками(
        self, name: str, target: Path, всего: int,
        on_progress: ProgressCallback | None,
    ) -> None:
        """Качать файл несколькими соединениями сразу.

        Каждый поток тянет свой отрезок в отдельный файл и умеет
        продолжать с места обрыва, поэтому оборванная загрузка не
        начинается заново. Склеиваем только когда все куски дотянуты до
        последнего байта: иначе на диск лёг бы обрубок, который весит
        правдоподобно и открывается как испорченная модель.
        """
        куски = self.dest / (name + ".куски")
        куски.mkdir(parents=True, exist_ok=True)
        длина = всего // ПОТОКОВ + 1
        границы = []
        for н in range(ПОТОКОВ):
            начало = н * длина
            if начало >= всего:
                break
            границы.append((н, начало, min(начало + длина, всего) - 1))

        url = HF_BASE.format(repo=self.repo, name=name)
        готово = {н: 0 for н, _, _ in границы}
        беда: list[Exception] = []
        замок = threading.Lock()

        def тянуть(н: int, начало: int, конец: int) -> None:
            ф = куски / f"{н}.часть"
            нужно = конец - начало + 1
            for _ in range(RETRIES):
                if self._cancel.is_set():
                    return
                есть = ф.stat().st_size if ф.exists() else 0
                with замок:
                    готово[н] = есть
                    if on_progress:
                        on_progress(name, sum(готово.values()), всего)
                if есть >= нужно:
                    return
                запрос = urllib.request.Request(
                    url, headers={"User-Agent": "konspekt",
                                  "Range": f"bytes={начало + есть}-{конец}"})
                try:
                    with urllib.request.urlopen(запрос, timeout=60) as ответ, \
                            open(ф, "ab") as fh:
                        while блок := ответ.read(CHUNK):
                            if self._cancel.is_set():
                                return
                            fh.write(блок)
                            with замок:
                                готово[н] += len(блок)
                                if on_progress:
                                    on_progress(name, sum(готово.values()), всего)
                except Exception as exc:  # обрыв: продолжим с этого места
                    if self._cancel.wait(RETRY_PAUSE):
                        return
                    беда.append(exc)

        нити = [threading.Thread(target=тянуть, args=г, name=f"качаем-{name}-{г[0]}",
                                 daemon=True) for г in границы]
        for н in нити:
            н.start()
        for н in нити:
            н.join()

        if self._cancel.is_set():
            raise DownloadCancelled(name)

        # Проверяем каждый кусок до склейки. Без этого обрубок ложится
        # на диск как готовая модель и падает уже при распознавании,
        # где причину не видно.
        недобор = []
        for н, начало, конец in границы:
            ф = куски / f"{н}.часть"
            есть = ф.stat().st_size if ф.exists() else 0
            нужно = конец - начало + 1
            if есть != нужно:
                недобор.append(f"кусок {н}: {есть} из {нужно}")
        if недобор:
            raise RuntimeError(
                f"Файл {name} скачан не полностью: {'; '.join(недобор)}"
            )

        part = target.with_suffix(target.suffix + ".part")
        with open(part, "wb") as out:
            for н, _, _ in границы:
                out.write((куски / f"{н}.часть").read_bytes())
        if part.stat().st_size != всего:
            part.unlink(missing_ok=True)
            raise RuntimeError(
                f"Файл {name} собран неверно: {part.stat().st_size} вместо {всего}"
            )
        part.replace(target)
        for ф in куски.glob("*.часть"):
            ф.unlink()
        куски.rmdir()
        log.info("Файл %s готов (%d байт, качали в %d потоков)", name, всего, len(границы))

    def _fetch(self, name: str, target: Path, on_progress: ProgressCallback | None) -> None:
        part = target.with_suffix(target.suffix + ".part")
        done = part.stat().st_size if part.exists() else 0
        url = HF_BASE.format(repo=self.repo, name=name)

        request = urllib.request.Request(url, headers={"User-Agent": "konspekt"})
        if done:
            # Докачка: просим сервер отдать хвост.
            request.add_header("Range", f"bytes={done}-")
            log.info("Продолжаем качать %s с %d байт", name, done)

        with urllib.request.urlopen(request, timeout=60) as response:
            if done and response.status != 206:
                # Сервер не понял Range, начинаем файл заново.
                log.info("Докачка не поддержана, качаем %s целиком", name)
                done = 0
                part.unlink(missing_ok=True)
            total = int(response.headers.get("Content-Length") or 0) + done
            if total and done > total:
                # Недокачанный кусок больше целого файла. Так бывает, если
                # две загрузки одного файла шли одновременно и дописывали
                # в один и тот же .part: получается склейка, которая весит
                # больше настоящего файла и не открывается. Чинится только
                # загрузкой заново.
                log.warning(
                    "Файл %s на диске больше настоящего (%d против %d), качаем заново",
                    name, done, total,
                )
                part.unlink(missing_ok=True)
                raise RuntimeError(f"Повреждённая докачка {name}, начинаем сначала")
            if not done and total >= ПОРОГ_ДРОБЛЕНИЯ and response.headers.get("Accept-Ranges") != "none":
                # Большой файл, качать только начали. Hugging Face режет
                # скорость одного соединения, поэтому дальше тянем его
                # кусками параллельно. Отдельного запроса за размером не
                # делаем: он уже пришёл в этом ответе.
                raise _ФайлБольшой(total)
            mode = "ab" if done else "wb"
            with open(part, mode) as fh:
                while True:
                    if self._cancel.is_set():
                        raise DownloadCancelled(name)
                    block = response.read(CHUNK)
                    if not block:
                        break
                    fh.write(block)
                    done += len(block)
                    if on_progress:
                        on_progress(name, done, total)

        if total and done != total:
            # Размер не сошёлся. Раньше здесь недокачанный кусок стирался
            # целиком, и это делало докачку бессмысленной: на файле в
            # 1.8 ГБ каждый обрыв отбрасывал загрузку к нулю, а «скачано
            # 0 байт» в журнале выглядело как мёртвый канал, хотя на
            # самом деле успевало прийти по 200-300 МБ. Модель весом
            # 214 МБ при этом доходила за семь заходов, а большая не
            # доходила никогда.
            #
            # Обрыв связи не портит уже записанное: байты в файле те же,
            # что отдал сервер. Поэтому кусок оставляем, и следующая
            # попытка продолжит с этого места. Испорченные случаи ловятся
            # отдельно: склейку больше настоящего размера отбрасываем
            # выше, а негодные веса выбраковывает проверка модели.
            raise RuntimeError(
                f"Файл {name} скачан не полностью: {done} байт из {total}"
            )
        part.replace(target)
        log.info("Файл %s готов (%d байт)", name, done)

    # --- сведения для UI -------------------------------------------------

    def downloaded_bytes(self) -> int:
        """Сколько уже лежит на диске, включая недокачанные куски."""
        total = 0
        for name in self.files:
            for path in (self.dest / name, self.dest / (name + ".part")):
                try:
                    total += path.stat().st_size
                except OSError:
                    # Файла нет или он исчез прямо сейчас: веса как раз
                    # выбрасывают из-за порчи, а окно в этот момент
                    # спрашивает размер. Проверка «существует, потом
                    # stat» тут не спасает, между ними файл и пропадает.
                    continue
        return total
