"""Тихое обновление: скачать новую версию и поставить самим.

Отправлять человека на страницу релиза за установщиком — это работа,
которую программа может сделать за него: скачать, сверить контрольную
сумму, запустить установку. Наш установщик кладёт файлы в папку
пользователя и не просит прав администратора, поэтому обновление
проходит без единого окна.

На macOS установщика нет, есть образ .dmg. Обновление делает то же, что
человек руками: монтирует образ, копирует Konspekt.app поверх старого,
снимает карантин и перезапускает программу. Этим занимается короткий
скрипт (`СКРИПТ_MAC`), который дожидается выхода программы: подменять
работающее приложение нельзя. Подписи Apple у сборки пока нет, поэтому
единственная проверка та же, что на Windows: сумма из нашего релиза.

Чего здесь намеренно нет:

- Тихой подмены без проверки. Файл скачан из сети, поэтому перед
  запуском сверяем SHA256 с суммой из релиза. Не совпало — выбрасываем
  и молчим: лучше остаться на старой версии, чем запустить неизвестно
  что от имени пользователя.
- Перезапуска посреди работы. Установщик закрывает программу, и если
  сделать это во время встречи, человек потеряет запись. Поэтому
  скачивание идёт фоном, а установка — при выходе или по кнопке, когда
  запись не идёт.
- Скачивания откуда угодно. Адрес собирается из имени нашего
  репозитория и номера версии, а не берётся из ответа сервера.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import platform
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app import __version__

from . import paths
from .events import APP_QUIT, UPDATE_STATE, bus

log = logging.getLogger(__name__)

REPO = "lamver/konspekt-releases"
# Адрес можно подменить на локальный сервер: иначе живьём проверить
# обновление можно только выложив настоящий релиз, то есть на живых
# пользователях. Обычный запуск переменной не видит.
DOWNLOAD = os.environ.get(
    "KONSPEKT_UPDATE_BASE", f"https://github.com/{REPO}/releases/download"
)
USER_AGENT = f"Konspekt/{__version__} ({platform.system()} {platform.machine()})"
TIMEOUT = 30.0

# Номер версии подставляется в адрес и в имя файла, поэтому пускаем
# только цифры с точками: испорченный latest.json не должен уводить
# загрузку в чужое место.
VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")

# Сколько программа должна простоять свёрнутой, прежде чем ставить
# обновление. Крестик прячет окно в трей, а не закрывает программу,
# поэтому ждать выхода можно неделями. Полчаса — это уже точно не
# «отвлёкся на минуту», а закрытая и забытая программа.
IDLE_BEFORE_INSTALL = float(os.environ.get("KONSPEKT_UPDATE_IDLE", 1800))

# Паузы перед повторной загрузкой после обрыва: минута, две, четыре.
# Дальше не пробуем, дождёмся завтрашней проверки версии.
RETRY_DELAYS = (60.0, 120.0, 240.0)

# Система и процессор в переменных, а не в вызовах по месту: тест на
# Windows подменяет их и проверяет путь macOS, не имея Mac под рукой.
ПЛАТФОРМА = sys.platform
МАШИНА = platform.machine()
ЗАМОРОЖЕНА = bool(getattr(sys, "frozen", False))
ПУТЬ_ПРОГРАММЫ = Path(sys.executable)

# Подмена программы на macOS. Аргументы: pid программы, образ, путь к
# Konspekt.app, перезапускать ли, файл журнала. Ждёт выхода программы,
# монтирует образ, копирует новое приложение рядом со старым, меняет их
# местами одним переименованием и снимает карантин: без него macOS
# назвала бы скопированную программу повреждённой, как и при ручной
# установке без подписи Apple.
СКРИПТ_MAC = r"""#!/bin/bash
pid="$1"; dmg="$2"; app="$3"; restart="$4"; log="$5"
exec >>"$log" 2>&1
echo "== $(date '+%Y-%m-%d %H:%M:%S') обновление: $dmg -> $app"
for i in $(seq 1 120); do
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.5
done
if kill -0 "$pid" 2>/dev/null; then
  echo "программа не вышла за минуту, обновление отложено"
  exit 1
fi
mnt="$(mktemp -d /tmp/konspekt-update.XXXXXX)"
if ! hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$mnt" "$dmg"; then
  echo "образ не смонтировался"
  rmdir "$mnt"
  exit 1
fi
dir="$(dirname "$app")"
new="$dir/.Konspekt.new.app"
old="$dir/.Konspekt.old.app"
rm -rf "$new" "$old"
ok=0
if ditto "$mnt/Konspekt.app" "$new"; then
  xattr -cr "$new" 2>/dev/null || true
  if mv "$app" "$old" && mv "$new" "$app"; then
    rm -rf "$old"
    ok=1
    echo "программа подменена"
  else
    echo "не удалось поменять местами, возвращаем старую"
    [ -d "$old" ] && [ ! -d "$app" ] && mv "$old" "$app"
    rm -rf "$new"
  fi
else
  echo "копирование из образа не удалось"
  rm -rf "$new"
fi
hdiutil detach "$mnt" -force >/dev/null 2>&1
rmdir "$mnt" 2>/dev/null
if [ "$ok" = "1" ] && [ "$restart" = "1" ]; then
  open -n "$app"
fi
exit $((1 - ok))
"""


def asset_for(version: str) -> tuple[str, str] | None:
    """Файл обновления и файл сумм для этой системы. None: тут не ставим."""
    if ПЛАТФОРМА == "win32":
        return f"konspekt-{version}-setup.exe", "SHA256SUMS"
    if ПЛАТФОРМА == "darwin":
        arch = "arm64" if МАШИНА == "arm64" else "x64"
        return f"konspekt-{version}-mac-{arch}.dmg", f"SHA256SUMS-mac-{arch}"
    return None


def _bundle() -> Path | None:
    """Konspekt.app, из которого запущена программа. Из исходников: None."""
    if ПЛАТФОРМА != "darwin" or not ЗАМОРОЖЕНА:
        return None
    # .../Konspekt.app/Contents/MacOS/Konspekt
    for родитель in ПУТЬ_ПРОГРАММЫ.parents:
        if родитель.suffix == ".app":
            return родитель
    return None


def can_self_update() -> bool:
    """Умеет ли программа поставить обновление сама на этой машине.

    На Windows всегда: установщик пишет в папку пользователя. На macOS
    только из собранного Konspekt.app, который мы вправе переписать:
    в чужой папке или без прав остаётся страница выпуска.
    """
    if ПЛАТФОРМА == "win32":
        return True
    if ПЛАТФОРМА == "darwin":
        app = _bundle()
        return app is not None and os.access(app, os.W_OK) and os.access(app.parent, os.W_OK)
    return False


@dataclass
class Ready:
    """Скачанный и проверенный установщик, готовый к запуску."""

    version: str
    path: Path


def _updates_dir() -> Path:
    d = paths.data_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _expected_sum(version: str, name: str, sums: str = "SHA256SUMS") -> str | None:
    """Забрать контрольную сумму установщика из файла релиза."""
    url = f"{DOWNLOAD}/v{version}/{sums}"
    r = httpx.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=TIMEOUT)
    r.raise_for_status()
    for line in r.text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


class Updater:
    """Качает обновление фоном и ставит его, когда это безопасно."""

    def __init__(self, service) -> None:
        self._service = service
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._idle_thread: threading.Thread | None = None
        # Сколько раз пробовали скачать каждую версию: после обрыва
        # повторяем, но не бесконечно, чтобы не долбить сеть.
        self._attempts: dict[str, int] = {}
        self.ready: Ready | None = None

    # --- скачивание ------------------------------------------------------

    def download_later(self, version: str) -> None:
        """Начать загрузку в фоне. Повторные вызовы не плодят потоков."""
        if not VERSION_RE.match(version or ""):
            log.warning("Странный номер версии, обновление пропущено: %r", version)
            return
        if asset_for(version) is None or not can_self_update():
            # Linux, запуск из исходников, Konspekt.app в чужой папке:
            # человек видит, что есть новая версия, и ставит её сам.
            log.info("Обновление %s: на этой системе ставим руками", version)
            return
        with self._lock:
            if self.ready and self.ready.version == version:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._download, args=(version,), daemon=True, name="update"
            )
            self._thread.start()

    def _download(self, version: str) -> None:
        name, sums = asset_for(version) or (f"konspekt-{version}-setup.exe", "SHA256SUMS")
        target = _updates_dir() / name
        try:
            expected = _expected_sum(version, name, sums)
            if not expected:
                log.warning("В релизе %s нет суммы для %s, обновление отменено", version, name)
                bus.emit(UPDATE_STATE, {"state": "error"})
                return

            # Файл мог остаться с прошлого раза: не качаем 60 МБ заново.
            if target.exists() and _sha256(target) == expected:
                self._finish(version, target)
                return

            bus.emit(UPDATE_STATE, {"state": "downloading", "percent": 0, "version": version})
            tmp = target.with_suffix(".part")
            url = f"{DOWNLOAD}/v{version}/{name}"
            done = 0
            last = -1
            with httpx.stream(
                "GET", url, headers={"User-Agent": USER_AGENT},
                follow_redirects=True, timeout=TIMEOUT,
            ) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0)
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = int(done * 100 / total)
                            # Событие на каждый кусок — это сотни вызовов
                            # в секунду через мост в браузер.
                            if pct != last:
                                last = pct
                                bus.emit(UPDATE_STATE, {
                                    "state": "downloading", "percent": pct, "version": version,
                                })

            got = _sha256(tmp)
            if got != expected:
                log.error("Сумма не сошлась (%s вместо %s), файл удалён", got, expected)
                tmp.unlink(missing_ok=True)
                bus.emit(UPDATE_STATE, {"state": "error"})
                return

            tmp.replace(target)
            self._finish(version, target)

        except Exception as e:
            log.info("Не удалось скачать обновление: %s", e)
            bus.emit(UPDATE_STATE, {"state": "error"})
            # Проверка версии ходит в сеть раз в сутки, поэтому одна
            # оборванная загрузка означала бы сутки без обновления.
            # Пробуем ещё несколько раз, с растущими паузами.
            self._retry(version)

    def _retry(self, version: str) -> None:
        attempt = self._attempts.get(version, 0)
        self._attempts[version] = attempt + 1
        if attempt >= len(RETRY_DELAYS):
            log.info("Обновление %s не скачалось, ждём следующей проверки", version)
            return
        delay = RETRY_DELAYS[attempt]
        log.info("Повторим загрузку %s через %s с", version, delay)

        def again() -> None:
            time.sleep(delay)
            with self._lock:
                self._thread = None
            self.download_later(version)

        threading.Thread(target=again, daemon=True, name="update-retry").start()

    def _finish(self, version: str, path: Path) -> None:
        with self._lock:
            self.ready = Ready(version=version, path=path)
        log.info("Обновление %s скачано и проверено", version)
        bus.emit(UPDATE_STATE, {"state": "ready", "version": version})
        self._watch_idle()

    # --- ожидание удобного момента ---------------------------------------

    def _watch_idle(self) -> None:
        """Дождаться, когда программу можно тихо подменить.

        Ждём, пока окно спрятано в трей и не идёт запись. Ставить, пока
        человек смотрит в окно, нельзя: программа исчезнет у него из-под
        рук. А ждать полного выхода бессмысленно, потому что крестик
        прячет в трей, и «выход» может не случиться неделями.
        """
        if self._idle_thread is not None and self._idle_thread.is_alive():
            return
        self._idle_thread = threading.Thread(
            target=self._idle_loop, daemon=True, name="update-idle"
        )
        self._idle_thread.start()

    def _idle_loop(self) -> None:
        quiet = 0.0
        step = 15.0
        while self.ready:
            time.sleep(step)
            if self._busy():
                quiet = 0.0
                continue
            quiet += step
            if quiet >= IDLE_BEFORE_INSTALL:
                log.info("Программа простаивает, ставим обновление")
                self.install_now(restart=True)
                return

    def _busy(self) -> bool:
        """Мешает ли что-нибудь подменить программу прямо сейчас."""
        rec = getattr(self._service, "is_recording", None)
        if callable(rec) and rec():
            return True
        # Открытое окно означает, что человек за программой: пусть
        # обновление подождёт, пока он свернёт её или уйдёт.
        visible = getattr(self._service, "window_visible", None)
        if callable(visible) and visible():
            return True
        # Разбор записи тоже нельзя обрывать: файл придётся грузить заново.
        importing = getattr(self._service, "is_importing", None)
        if callable(importing) and importing():
            return True
        return False

    # --- установка -------------------------------------------------------

    def install_now(self, restart: bool = False, quit: bool = True) -> bool:
        """Запустить установку. Программа при этом закрывается.

        `restart` просит установщик поднять программу обратно: при тихой
        установке в простое человек ничего не нажимал, и пропавший
        значок в трее выглядел бы поломкой.

        Во время записи не трогаем: установщик закрывает Konspekt, и
        незаконченная встреча пропала бы вместе с ним.

        `quit` — закрыть ли программу самим. На Windows это делает
        установщик, на macOS скрипт ждёт нашего выхода, и выходим мы.
        При установке на выходе программа и так закрывается.
        """
        ready = self.ready
        if not ready or not ready.path.exists():
            return False
        # `is_recording` — метод: без вызова получился бы всегда истинный
        # объект функции, и обновление не ставилось бы никогда.
        busy = getattr(self._service, "is_recording", None)
        if callable(busy) and busy():
            log.info("Идёт запись, установка отложена")
            return False
        if ПЛАТФОРМА == "darwin":
            return self._install_mac(ready, restart, quit)

        try:
            args = [str(ready.path), "/VERYSILENT", "/SP-", "/NORESTART", "/NOCANCEL"]
            if restart:
                # Установщик сам поднимет программу после подмены файлов.
                args.append("/RESTARTKONSPEKT")
            # /VERYSILENT — без окон, /NORESTART — не перезагружать
            # систему, /SP- — без вопроса «продолжить установку?».
            # Запускаем открепившись: установщик закроет нас самих.
            subprocess.Popen(
                args,
                creationflags=(
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                ),
                close_fds=True,
                cwd=os.environ.get("TEMP") or str(Path.home()),
            )
            log.info("Запущена установка %s", ready.version)
            return True
        except Exception as e:
            log.error("Не удалось запустить установку: %s", e)
            return False

    def _install_mac(self, ready: Ready, restart: bool, quit: bool) -> bool:
        """Подменить Konspekt.app скриптом, который переживёт наш выход."""
        app = _bundle()
        if app is None:
            log.info("Программа запущена не из Konspekt.app, подменять нечего")
            return False
        try:
            script = _updates_dir() / "обновить.sh"
            script.write_text(СКРИПТ_MAC, encoding="utf-8")
            script.chmod(0o755)
            журнал = _updates_dir() / "обновление.log"
            subprocess.Popen(
                ["/bin/bash", str(script), str(os.getpid()), str(ready.path),
                 str(app), "1" if restart else "0", str(журнал)],
                start_new_session=True, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=str(Path.home()),
            )
            log.info("Запущена подмена %s, выходим", ready.version)
        except Exception as e:
            log.error("Не удалось запустить подмену программы: %s", e)
            return False
        if quit:
            bus.emit(APP_QUIT, {})
        return True

    def install_on_quit(self) -> None:
        """Поставить обновление при выходе из программы.

        Самый спокойный момент: человек сам закрыл окно, ничего не
        потеряется, а при следующем запуске он уже на новой версии.
        """
        if self.ready:
            self.install_now(quit=False)
