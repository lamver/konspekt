"""Другой компьютер: Конспекты на разных устройствах отдают друг другу встречи.

Зачем. Запись идёт на ноутбуке в переговорной, а разбирать её удобно за
рабочим столом; у менеджера свои встречи, у руководителя свои. Облака у
Конспекта нет и не будет: обещание «ничего не уходит с компьютера»
становится «уходит только на устройства, которые вы сами сопрягли, и
только зашифрованным».

Как устроено, по образцу FileHunter (AiFolder, p2p.rs). Связь держит
библиотека iroh: QUIC с шифрованием, в одной сети компьютеры соединяются
напрямую, через интернет пробивают NAT, а когда не выходит, идут через
ретранслятор, который видит только шифр и номера устройств. Своего
сервера в цепочке нет.

Доверие по коду. Один компьютер создаёт код на десять минут, второй его
вводит. Тот, кто создал код, открывает свои встречи тому, кто ввёл; с
галочкой «и получить доступ в ответ» связь двусторонняя. Чужие запросы
отклоняются до чтения тела: несопряжённому устройству доступно только
сопряжение, и то в пределах четырёх килобайт. Все проверки на стороне,
которая отдаёт данные: чужому коду верить нельзя.

Встречи забираются копией: записи, расшифровка, заметки и итоги ложатся в
папку с именем того компьютера и дальше живут как свои. Копия работает,
когда тот компьютер выключен, и ничего не меняет у него.

Чего здесь нет сознательно. Правки в обе стороны и общая база: это уже
совместная работа, а не обмен. Своя криптография: ключи и шифр у iroh.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import platform
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import paths
from .models import Meeting, MeetingStatus, NoteLine, Speaker, TranscriptSegment, now

try:  # Intel Mac: колёс iroh под x86_64 macOS нет, там раздел только объясняет это.
    import iroh
except Exception:  # pragma: no cover - зависит от платформы
    iroh = None

log = logging.getLogger(__name__)

ALPN = b"konspekt/1"
# Растёт, когда появляются новые запросы. Старая сторона на незнакомый
# запрос отвечает unknown, и окно просит обновить тот компьютер.
ПРОТОКОЛ = 1
ПРИСТАВКА = "KS1"
КОД_ЖИВЁТ = 10 * 60
# Несопряжённый узел может прислать только сопряжение: больше четырёх
# килобайт ему читать не станем, чтобы чужак не заставил выделять память.
ЛИМИТ_ЧУЖОГО = 4096
МАКС_СООБЩЕНИЕ = 8 << 20
КУСОК = 4 << 20
ТАЙМАУТ = 60.0
# Сколько раз за десять минут можно ошибиться кодом с одного устройства.
ПОПЫТОК_КОДА = 10
ОПРОС_СЕКУНД = 30.0
# Толчок соседям после своего изменения: собрать пачку правок в один.
ТОЛЧОК_СЕКУНД = 1.5
ФАЙЛ_КЛЮЧА = "устройство.key"
ФАЙЛ_УСТРОЙСТВ = "устройства.json"
УРОВНИ = ("none", "view")


def доступно() -> bool:
    return iroh is not None


@dataclass
class Устройство:
    """Сопряжённый компьютер и всё, что мы о нём знаем."""

    id: str
    name: str
    added: float = field(default_factory=now)
    # Что ЭТОТ компьютер открыл тому: none или view. По умолчанию ничего.
    share: str = "none"
    # Забирать его новые встречи самим, пока оба включены. Включено с
    # сопряжения: человек ждёт, что всё приедет само и дальше будет
    # подтягиваться, а не ищет кнопку.
    auto_pull: bool = True
    # Чужой номер встречи -> наш: вторую копию не заводим.
    taken: dict[str, str] = field(default_factory=dict)
    # Чужой номер -> отпечаток содержимого на момент копии: по нему видно,
    # что у источника встречу переименовали, дописали или перенесли.
    stamps: dict[str, str] = field(default_factory=dict)
    # Чужие номера встреч, чьи заметки с копии ещё не доехали до
    # источника: он был выключен. Уйдут при следующем опросе.
    pending: list[str] = field(default_factory=list)
    # Папка, куда ложатся копии. Пусто, пока не забрали ни одной.
    folder_id: str = ""
    # Последний известный адрес: ретранслятор и прямые адреса. Через
    # интернет iroh найдёт устройство и по номеру, в сети без интернета
    # выручают прямые адреса.
    relay: str = ""
    direct: list[str] = field(default_factory=list)
    # Что узнали при последнем опросе, в файл не пишется.
    online: bool = False
    remote_share: str = ""
    remote_proto: int = 0
    last_seen: float = 0.0
    remote_meetings: int = -1

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["taken"] = len(self.taken)
        return d

    def в_файл(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("online", "remote_share", "remote_proto", "last_seen", "remote_meetings"):
            d.pop(k, None)
        return d


class Ошибка(Exception):
    """Ошибка с машинным кодом: окно переводит код, а не текст."""

    def __init__(self, код: str, подробности: str = "") -> None:
        super().__init__(код if not подробности else f"{код}: {подробности}")
        self.код = код


def _записать_атомарно(путь: Path, данные: bytes) -> None:
    """Список пар и ключ устройства теряются вместе: писать только целиком."""
    времянка = путь.with_suffix(путь.suffix + ".tmp")
    времянка.write_bytes(данные)
    os.replace(времянка, путь)


def разобрать_код(код: str) -> tuple[str, str]:
    """`KS1-<билет>-<секрет>` -> (билет, секрет). Пробелы и регистр прощаем."""
    части = код.strip().replace(" ", "").split("-", 2)
    if len(части) != 3 or части[0].upper() != ПРИСТАВКА or not части[1] or not части[2]:
        raise Ошибка("bad_code")
    return части[1], части[2].lower()


def имя_компьютера() -> str:
    try:
        имя = platform.node().strip()
    except Exception:
        имя = ""
    return имя or "Konspekt"


def _безопасное_имя(имя: str) -> bool:
    """Имя файла записи, а не путь. Проверяется с обеих сторон: и когда
    отдаём, и когда принимаем список от другого компьютера.

    Windows отбрасывает точки и пробелы в конце имени, поэтому «.. » это
    то же «..», а имя из одних точек и пробелов пусто. Урок FileHunter
    (0.1.10): там такой список уводил запись выше папки назначения."""
    if not имя or len(имя) > 255 or имя != имя.strip():
        return False
    if any(з in имя for з in ("/", "\\", "\x00", ":")):
        return False
    if имя.rstrip(". ") == "" or ".." in имя or any(ord(з) < 32 for з in имя):
        return False
    return Path(имя).name == имя


class Обмен:
    """Что отдаём и как принимаем: всё, что знает о встречах, в одном месте.

    Один и тот же класс стоит с обеих сторон провода, поэтому тест на двух
    базах во временных папках проверяет ровно тот путь, что у людей.
    """

    def __init__(self, store: Any, audio_dir: Path | None = None,
                 на_изменение: Callable[[], None] | None = None,
                 на_встречу: Callable[[str], None] | None = None) -> None:
        self.store = store
        self.audio_dir = audio_dir or paths.audio_dir()
        self.на_изменение = на_изменение or (lambda: None)
        # Открытая в окне встреча изменилась снаружи: окну надо перечитать.
        self.на_встречу = на_встречу or (lambda meeting_id: None)
        # Узел подставляет себя: обмен должен знать, что у нас копии, а
        # что своё. Отдаём только своё, иначе при связи в обе стороны
        # встречи и папки отражаются друг в друге без конца.
        self.узел: Any = None

    def _чужое(self) -> tuple[set[str], set[str]]:
        """Номера встреч-копий и имена сопряжённых компьютеров."""
        if self.узел is None:
            return set(), set()
        return self.узел.копии(), self.узел.имена()

    def _зеркальная_папка(self, имя: str, имена: set[str]) -> bool:
        return any(имя == к or имя.startswith(f"{к} / ") for к in имена)

    # --- заметки в обе стороны -------------------------------------------

    def заметки(self, meeting_id: str) -> dict[str, Any]:
        """Что копия шлёт источнику: свободный текст и строки с номерами."""
        m = self.store.get_meeting(meeting_id)
        if m is None:
            raise Ошибка("not_found")
        return {
            "notes": m.notes or "",
            "note_lines": [n.to_dict() for n in self.store.list_note_lines(meeting_id)],
        }

    def применить_заметки(self, meeting_id: str, данные: dict[str, Any]) -> bool:
        """Заметки с копии легли в источник.

        Строки сливаются по номерам: у каждой свой, и строка, которой тут
        нет, добавляется. Свободный текст один на встречу, правило
        «последняя правка побеждает»: копия прислала то, что человек
        только что сохранил, это и есть последняя правка."""
        m = self.store.get_meeting(meeting_id)
        if m is None or m.status != MeetingStatus.READY:
            raise Ошибка("not_found")
        изменилось = False
        текст = данные.get("notes")
        if isinstance(текст, str) and текст != (m.notes or ""):
            self.store.update_meeting(meeting_id, notes=текст)
            изменилось = True
        есть = {n.id for n in self.store.list_note_lines(meeting_id)}
        for n in данные.get("note_lines") or []:
            номер = str(n.get("id") or "")
            if not номер or номер in есть:
                continue
            self.store.add_note_line(NoteLine(
                id=номер, meeting_id=meeting_id, text=str(n.get("text") or ""),
                offset=float(n.get("offset") or 0.0),
                created_at=float(n.get("created_at") or now()),
            ))
            есть.add(номер)
            изменилось = True
        if изменилось:
            self.на_встречу(meeting_id)
            self.на_изменение()
        return изменилось

    # --- отдаём ---------------------------------------------------------

    def встречи(self) -> list[dict[str, Any]]:
        папки = {f["id"]: f["name"] for f in self.store.list_folders()}
        где = self.store.meeting_folders()
        отпечатки = self.store.meeting_stamps() if hasattr(self.store, "meeting_stamps") else {}
        копии, _ = self._чужое()
        out = []
        for m in self.store.list_meetings(limit=100000):
            if m.status != MeetingStatus.READY or m.id in копии:
                continue
            папка = папки.get(где.get(m.id) or "", "")
            out.append({
                "id": m.id, "title": m.title, "created_at": m.created_at,
                "started_at": m.started_at, "ended_at": m.ended_at,
                "duration": m.duration, "language": m.language, "template": m.template,
                "folder": папка,
                # Всё, что меняется у готовой встречи и должно доехать до копии.
                "stamp": "|".join(str(x) for x in (
                    m.title, папка, len(m.summary or ""), len(m.notes or ""),
                    m.template, m.language, m.ended_at, отпечатки.get(m.id, "0:0"),
                )),
            })
        return out

    def папки(self) -> list[str]:
        """Имена своих папок, включая пустые: у копии они должны быть.
        Папки других компьютеров не отдаём: это не наше."""
        _, имена = self._чужое()
        return [f["name"] for f in self.store.list_folders()
                if not self._зеркальная_папка(f["name"], имена)]

    def зеркало_папок(self, устройство: Устройство, имена: list[str]) -> bool:
        """Завести у себя папки источника, которых ещё нет, и убрать
        пустые, которых у источника больше нет: переименовал или удалил.
        Папку со встречами не трогаем, даже если у источника её не стало."""
        изменилось = False
        свои = {f["name"]: f for f in self.store.list_folders()}
        нужные = {f"{устройство.name} / {имя}" for имя in имена if имя}
        for имя in нужные:
            if имя not in свои:
                self.store.create_folder(имя)
                изменилось = True
        приставка = f"{устройство.name} / "
        for имя, f in свои.items():
            if имя.startswith(приставка) and имя not in нужные and not f.get("source") and not f.get("count"):
                self.store.delete_folder(f["id"])
                изменилось = True
        if изменилось:
            self.на_изменение()
        return изменилось

    def встреча(self, meeting_id: str) -> dict[str, Any]:
        m = self.store.get_meeting(meeting_id)
        if m is None or m.status != MeetingStatus.READY or meeting_id in self._чужое()[0]:
            raise Ошибка("not_found")
        звук = []
        for ч in self.store.list_audio_chunks(meeting_id):
            путь = Path(ч["path"])
            if not путь.exists():
                continue
            звук.append({
                "track": ч["track"], "name": путь.name, "start": ч["start_s"],
                "duration": ч["duration_s"], "size": путь.stat().st_size,
            })
        d = m.to_dict()
        папки = {f["id"]: f["name"] for f in self.store.list_folders()}
        d["folder"] = папки.get(self.store.meeting_folders().get(meeting_id) or "", "")
        d["segments"] = [s.to_dict() for s in self.store.list_segments(meeting_id)]
        d["note_lines"] = [n.to_dict() for n in self.store.list_note_lines(meeting_id)]
        d["audio"] = звук
        return d

    def читать_звук(self, meeting_id: str, name: str, offset: int, size: int) -> bytes:
        """Кусок файла записи. Только файлы, записанные за этой встречей:
        имя сверяется со списком, путь не собирается из чужих частей."""
        if not _безопасное_имя(name):
            raise Ошибка("not_found")
        for ч in self.store.list_audio_chunks(meeting_id):
            путь = Path(ч["path"])
            if путь.name == name and путь.exists():
                with путь.open("rb") as f:
                    f.seek(max(0, int(offset)))
                    return f.read(max(0, min(int(size), КУСОК)))
        raise Ошибка("not_found")

    # --- принимаем -----------------------------------------------------

    def папка_устройства(self, устройство: Устройство, подпапка: str = "") -> str:
        """Копии лежат в папке с именем того компьютера, а его папки
        повторяются рядом как «Компьютер / Папка»: вложенных папок у
        Конспекта нет, а где что лежало у источника, видно и так."""
        имя = f"{устройство.name} / {подпапка}" if подпапка else устройство.name
        for f in self.store.list_folders():
            if f["name"] == имя and not f.get("source"):
                if not подпапка:
                    устройство.folder_id = f["id"]
                return f["id"]
        папка = self.store.create_folder(имя)
        if not подпапка:
            устройство.folder_id = папка["id"]
        return папка["id"]

    def сохранить(self, устройство: Устройство, данные: dict[str, Any],
                  скачать: Callable[[str, int, int], bytes]) -> str:
        """Положить чужую встречу к себе. Возвращает наш номер встречи."""
        чужой = str(данные.get("id") or "")
        if чужой in устройство.taken:
            return устройство.taken[чужой]
        meeting = Meeting(
            title=str(данные.get("title") or ""),
            created_at=float(данные.get("created_at") or now()),
            started_at=данные.get("started_at"),
            ended_at=данные.get("ended_at"),
            status=MeetingStatus.READY,
            template=str(данные.get("template") or "general"),
            notes=str(данные.get("notes") or ""),
            summary=str(данные.get("summary") or ""),
            language=str(данные.get("language") or ""),
        )
        self.store.create_meeting(meeting)
        # Копия не ест пробный период: записывали не здесь.
        if hasattr(self.store, "mark_trial_legacy"):
            self.store.mark_trial_legacy(meeting.id)
        if hasattr(self.store, "set_origin"):
            self.store.set_origin(meeting.id, устройство.id)
        self._наполнить(устройство, meeting.id, данные, скачать)
        устройство.taken[чужой] = meeting.id
        self.на_изменение()
        return meeting.id

    def обновить(self, устройство: Устройство, наш: str, данные: dict[str, Any],
                 скачать: Callable[[str, int, int], bytes]) -> bool:
        """Догнать источник: название, итоги, заметки, расшифровка, папка.
        Записи докачиваются только недостающие. Копия остаётся той же
        встречей, с тем же номером."""
        if self.store.get_meeting(наш) is None:
            # Копию удалили здесь: заново не заводим, человек так решил.
            return False
        self.store.update_meeting(
            наш,
            title=str(данные.get("title") or ""),
            started_at=данные.get("started_at"), ended_at=данные.get("ended_at"),
            template=str(данные.get("template") or "general"),
            notes=str(данные.get("notes") or ""), summary=str(данные.get("summary") or ""),
            language=str(данные.get("language") or ""),
        )
        self._наполнить(устройство, наш, данные, скачать, заменить=True)
        self.на_встречу(наш)
        self.на_изменение()
        return True

    def _наполнить(self, устройство: Устройство, наш: str, данные: dict[str, Any],
                   скачать: Callable[[str, int, int], bytes], заменить: bool = False) -> None:
        реплики = []
        for s in данные.get("segments") or []:
            try:
                speaker = Speaker(s.get("speaker") or "them")
            except ValueError:
                speaker = Speaker.THEM
            реплики.append(TranscriptSegment(
                meeting_id=наш, speaker=speaker, text=str(s.get("text") or ""),
                start=float(s.get("start") or 0.0), end=float(s.get("end") or 0.0),
                lang=str(s.get("lang") or "ru"), voice_id=str(s.get("voice_id") or ""),
                voice_label=str(s.get("voice_label") or ""), doubtful=bool(s.get("doubtful")),
            ))
        строки = [NoteLine(
            id=str(n.get("id") or "") or NoteLine().id,
            meeting_id=наш, text=str(n.get("text") or ""),
            offset=float(n.get("offset") or 0.0),
            created_at=float(n.get("created_at") or now()),
        ) for n in данные.get("note_lines") or []]
        if заменить:
            self.store.replace_transcript(наш, реплики)
            # Строки, написанные здесь и ещё не доехавшие до источника,
            # не теряем: объединение, а не замена.
            свои = {n.id: n for n in self.store.list_note_lines(наш)}
            номера = {n.id for n in строки}
            self.store.replace_note_lines(наш, строки + [n for i, n in свои.items() if i not in номера])
        else:
            for s in реплики:
                self.store.add_segment(s)
            for n in строки:
                self.store.add_note_line(n)
        папка = self.audio_dir / наш
        есть = {Path(ч["path"]).name for ч in self.store.list_audio_chunks(наш)} if заменить else set()
        for ф in данные.get("audio") or []:
            имя = str(ф.get("name") or "")
            размер = int(ф.get("size") or 0)
            if not _безопасное_имя(имя) or размер <= 0 or имя in есть:
                continue
            папка.mkdir(parents=True, exist_ok=True)
            путь = папка / имя
            try:
                with путь.open("wb") as f:
                    сдвиг = 0
                    while сдвиг < размер:
                        кусок = скачать(имя, сдвиг, min(КУСОК, размер - сдвиг))
                        if not кусок:
                            raise Ошибка("transfer", f"пустой кусок {имя} на {сдвиг}")
                        f.write(кусок)
                        сдвиг += len(кусок)
            except Exception:
                путь.unlink(missing_ok=True)
                raise
            self.store.add_audio_chunk(
                наш, str(ф.get("track") or "mic"), str(путь),
                float(ф.get("start") or 0.0), float(ф.get("duration") or 0.0),
            )
        self.store.set_meeting_folder(
            наш, self.папка_устройства(устройство, str(данные.get("folder") or "")))

class Узел:
    """Наше устройство в сети Конспектов: ключ, сопряжения, запросы."""

    def __init__(self, обмен: Обмен, data_dir: Path | None = None,
                 имя: str | None = None, на_изменение: Callable[[], None] | None = None,
                 минимально: bool | None = None) -> None:
        self.обмен = обмен
        self.data_dir = data_dir or paths.data_dir()
        self.имя = имя or имя_компьютера()
        self.на_изменение = на_изменение or (lambda: None)
        # Тестам и сети без интернета: без ретрансляторов и поиска.
        if минимально is None:
            минимально = os.environ.get("KONSPEKT_P2P_MINIMAL", "") == "1"
        self.минимально = минимально
        self.устройства: dict[str, Устройство] = {}
        self._бывшие: list[str] = []
        self._коды: list[tuple[str, float, bool]] = []
        self._попытки: dict[str, list[float]] = {}
        self._замок = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._поток: threading.Thread | None = None
        self._endpoint = None
        self._соединения: dict[str, Any] = {}
        self._замки_забора: dict[str, asyncio.Lock] = {}
        self._таймер_толчка: threading.Timer | None = None
        self._опросы_в_очереди: set[str] = set()
        self._готов = threading.Event()
        self._ошибка_старта = ""
        self._стоп = False
        self._загрузить()
        обмен.узел = self

    def копии(self) -> set[str]:
        with self._замок:
            свои = {свой for у in self.устройства.values() for свой in у.taken.values()}
        if hasattr(self.обмен.store, "copies"):
            свои |= self.обмен.store.copies()
        return свои

    def имена(self) -> set[str]:
        """Имена сопряжённых компьютеров и бывших: их папки остаются после
        отвязки и отдавать их как свои нельзя."""
        with self._замок:
            return {у.name for у in self.устройства.values()} | set(self._бывшие)

    # --- файлы -----------------------------------------------------------

    def _загрузить(self) -> None:
        путь = self.data_dir / ФАЙЛ_УСТРОЙСТВ
        if not путь.exists():
            return
        try:
            raw = json.loads(путь.read_text(encoding="utf-8"))
            self._бывшие = [str(x) for x in raw.get("former", [])]
            for d in raw.get("devices", []):
                поля = {k: v for k, v in d.items() if k in Устройство.__dataclass_fields__}
                у = Устройство(**поля)
                if у.share not in УРОВНИ:
                    у.share = "none"
                self.устройства[у.id] = у
        except Exception:
            log.exception("Не удалось прочитать список устройств, начинаем с пустого")

    def _сохранить(self) -> None:
        with self._замок:
            данные = {"devices": [у.в_файл() for у in self.устройства.values()], "former": self._бывшие}
        _записать_атомарно(
            self.data_dir / ФАЙЛ_УСТРОЙСТВ,
            json.dumps(данные, ensure_ascii=False, indent=2).encode("utf-8"),
        )

    def _ключ(self) -> bytes:
        путь = self.data_dir / ФАЙЛ_КЛЮЧА
        if путь.exists():
            данные = путь.read_bytes()
            if len(данные) == 32:
                return данные
            log.warning("Ключ устройства повреждён, заводим новый: прежние пары отвалятся")
        ключ = iroh.SecretKey.generate().to_bytes()
        _записать_атомарно(путь, ключ)
        return ключ

    # --- жизнь узла ------------------------------------------------------

    def запустить(self) -> None:
        if not доступно() or self._поток is not None:
            return
        self._стоп = False
        self._поток = threading.Thread(target=self._крутить, name="konspekt-p2p", daemon=True)
        self._поток.start()
        self._готов.wait(20)

    def остановить(self) -> None:
        self._стоп = True
        with self._замок:
            if self._таймер_толчка is not None:
                self._таймер_толчка.cancel()
        loop, self._loop = self._loop, None
        if loop is not None and self._endpoint is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._закрыть(), loop).result(5)
            except Exception:
                pass
            loop.call_soon_threadsafe(self._погасить, loop)
        if self._поток is not None:
            self._поток.join(5)
            self._поток = None

    @staticmethod
    def _погасить(loop: asyncio.AbstractEventLoop) -> None:
        """Снять фоновые задачи до остановки цикла: иначе Python ругается
        на недоделанные задачи при выходе."""
        for задача in asyncio.all_tasks(loop):
            задача.cancel()
        loop.call_soon(loop.stop)

    def _крутить(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._поднять())
        except Exception as e:
            log.exception("Узел обмена не поднялся")
            self._ошибка_старта = str(e)
            self._готов.set()
            return
        self._готов.set()
        loop.create_task(self._принимать())
        loop.create_task(self._опрашивать())
        try:
            loop.run_forever()
        finally:
            loop.close()

    async def _поднять(self) -> None:
        b = iroh.EndpointBuilder()
        if self.минимально:
            b.apply_minimal()
        else:
            b.apply_n0()
        b.secret_key(self._ключ())
        b.alpns([ALPN])
        self._endpoint = await b.bind()
        # Ретранслятор подхватывается в фоне: ждать его при старте незачем,
        # прямые адреса в билете есть сразу, а через интернет iroh сам
        # дождётся связи с ретранслятором перед соединением.
        log.info("Узел обмена поднят: %s", str(self._endpoint.id())[:10])

    async def _закрыть(self) -> None:
        for c in list(self._соединения.values()):
            try:
                c.close(0, b"bye")
            except Exception:
                pass
        self._соединения.clear()
        try:
            await self._endpoint.close()
        except Exception:
            pass

    # --- что видно окну --------------------------------------------------

    @property
    def id(self) -> str:
        return str(self._endpoint.id()) if self._endpoint is not None else ""

    def состояние(self) -> dict[str, Any]:
        with self._замок:
            устройства = [у.to_dict() for у in self.устройства.values()]
        устройства.sort(key=lambda d: d["added"])
        return {
            "available": доступно(),
            "running": self._endpoint is not None,
            "error": self._ошибка_старта,
            "id": self.id[:10],
            "name": self.имя,
            "proto": ПРОТОКОЛ,
            "devices": устройства,
        }

    def билет(self) -> str:
        return str(iroh.EndpointTicket.from_addr(self._endpoint.addr()))

    def создать_код(self, взаимно: bool) -> str:
        """Код на десять минут. Чем длиннее, тем надёжнее, но вводят его руками
        редко: обычно копируют, поэтому берём билет целиком — он несёт и
        прямые адреса, и с ним стыковка идёт без интернета."""
        if self._endpoint is None:
            raise Ошибка("unavailable")
        секрет = base64.b32encode(secrets.token_bytes(10)).decode().lower().rstrip("=")
        with self._замок:
            self._коды = [(с, t, в) for с, t, в in self._коды if time.time() < t]
            self._коды.append((секрет, time.time() + КОД_ЖИВЁТ, взаимно))
        return f"{ПРИСТАВКА}-{self.билет()}-{секрет}"

    def _взять_код(self, секрет: str) -> bool | None:
        with self._замок:
            живые = [(с, t, в) for с, t, в in self._коды if time.time() < t]
            for с, t, в in живые:
                if secrets.compare_digest(с, секрет):
                    живые.remove((с, t, в))
                    self._коды = живые
                    return в
            self._коды = живые
        return None

    def _лимит_попыток(self, кто: str) -> bool:
        with self._замок:
            окно = [t for t in self._попытки.get(кто, []) if time.time() - t < КОД_ЖИВЁТ]
            окно.append(time.time())
            self._попытки[кто] = окно
            return len(окно) <= ПОПЫТОК_КОДА

    def _выполнить(self, coro, таймаут: float = ТАЙМАУТ):
        if self._loop is None or self._endpoint is None:
            raise Ошибка("unavailable", self._ошибка_старта)
        try:
            return asyncio.run_coroutine_threadsafe(coro, self._loop).result(таймаут)
        except Ошибка:
            raise
        except asyncio.TimeoutError:
            raise Ошибка("offline", "время вышло")
        except Exception as e:
            raise Ошибка("offline", str(e))

    def подключить(self, код: str) -> dict[str, Any]:
        билет, секрет = разобрать_код(код)
        try:
            addr = iroh.EndpointTicket.from_string(билет).endpoint_addr()
        except Exception:
            raise Ошибка("bad_code")
        if str(addr.id()) == self.id:
            raise Ошибка("self_code")
        return self._выполнить(self._сопрячься(addr, секрет), 40)

    def отвязать(self, id: str) -> None:
        with self._замок:
            у = self.устройства.pop(id, None)
            if у is not None and у.name not in self._бывшие:
                self._бывшие.append(у.name)
        if у is None:
            return
        self._сохранить()
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._разорвать(id), self._loop)
        self.на_изменение()

    def открыть(self, id: str, уровень: str) -> None:
        if уровень not in УРОВНИ:
            raise Ошибка("bad_level")
        with self._замок:
            у = self.устройства.get(id)
            if у is None:
                raise Ошибка("not_paired")
            у.share = уровень
        self._сохранить()
        self.на_изменение()

    def забирать_самим(self, id: str, включено: bool) -> None:
        with self._замок:
            у = self.устройства.get(id)
            if у is None:
                raise Ошибка("not_paired")
            у.auto_pull = bool(включено)
        self._сохранить()
        self.на_изменение()
        if включено:
            self.обновить(id)

    def обновить(self, id: str | None = None) -> dict[str, Any]:
        """Спросить устройства, живы ли, и забрать новое у тех, где это
        включено. Зовётся при открытии раздела и кнопкой."""
        self._выполнить(self._опросить(id), 45)
        return self.состояние()

    def встречи_у(self, id: str) -> list[dict[str, Any]]:
        return self._выполнить(self._вызвать(id, "meetings", {}), 30)[0]

    def забрать(self, id: str, meeting_id: str) -> str:
        return self._выполнить(self._забрать(id, meeting_id), 600)

    def забрать_новые(self, id: str) -> int:
        return self._выполнить(self._забрать_новые(id), 3600)

    def толкнуть(self) -> None:
        """Что-то изменилось у нас: сказать тем, кому мы открыли встречи,
        чтобы они спросили сейчас, а не через полминуты. Правки идут
        пачками (импорт десяти файлов), поэтому ждём полторы секунды тишины."""
        if self._loop is None:
            return
        with self._замок:
            if self._таймер_толчка is not None:
                self._таймер_толчка.cancel()
            self._таймер_толчка = threading.Timer(ТОЛЧОК_СЕКУНД, self._толкнуть_сейчас)
            self._таймер_толчка.daemon = True
            self._таймер_толчка.start()

    def _толкнуть_сейчас(self) -> None:
        loop = self._loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._толкнуть_всех(), loop)

    async def _толкнуть_всех(self) -> None:
        with self._замок:
            ids = [у.id for у in self.устройства.values() if у.share == "view"]
        for id in ids:
            try:
                await asyncio.wait_for(self._вызвать(id, "poke", {}), 10)
                log.debug("Толкнули %s", id[:10])
            except Exception as e:
                # Выключен или старая версия: заберёт при своём опросе.
                log.debug("Толчок до %s не дошёл: %s", id[:10], e)

    def _опросить_скоро(self, id: str) -> None:
        """Опрос по толчку, один за раз на устройство."""
        if id in self._опросы_в_очереди:
            return
        self._опросы_в_очереди.add(id)

        async def опрос() -> None:
            try:
                await self._опросить(id)
            finally:
                self._опросы_в_очереди.discard(id)

        asyncio.get_running_loop().create_task(опрос())

    def отправить_заметки(self, наш: str) -> bool:
        """Заметки сохранили у копии: отправить источнику, если это копия.
        Не ждём ответа, окно не должно висеть на сети."""
        with self._замок:
            пара = next(((у, чужой) for у in self.устройства.values()
                         for чужой, свой in у.taken.items() if свой == наш), None)
        if пара is None or self._loop is None:
            return False
        у, чужой = пара
        if чужой not in у.pending:
            у.pending.append(чужой)
        self._сохранить()
        asyncio.run_coroutine_threadsafe(self._послать_заметки(у.id), self._loop)
        return True

    async def _послать_заметки(self, id: str) -> None:
        with self._замок:
            у = self.устройства.get(id)
        if у is None or not у.pending:
            return
        for чужой in list(у.pending):
            наш = у.taken.get(чужой)
            if not наш:
                у.pending.remove(чужой)
                continue
            try:
                данные = self.обмен.заметки(наш)
                await self._вызвать(id, "notes", {"id": чужой, **данные})
                у.pending.remove(чужой)
            except Ошибка as e:
                if e.код in ("not_found", "no_access", "not_paired", "unknown"):
                    # Источнику эти заметки не нужны или он их не понимает:
                    # держать в очереди бессмысленно.
                    log.info("Заметки для %s не приняты: %s", у.name, e)
                    у.pending.remove(чужой)
                else:
                    log.info("Заметки для %s подождут: %s", у.name, e)
                break
        self._сохранить()

    # --- сторона, которая спрашивает -------------------------------------

    def _адрес(self, у: Устройство):
        return iroh.EndpointAddr(iroh.EndpointId.from_string(у.id), у.relay or None, list(у.direct))

    async def _соединение(self, id: str):
        conn = self._соединения.get(id)
        if conn is not None and conn.close_reason() is None:
            return conn
        with self._замок:
            у = self.устройства.get(id)
        if у is None:
            raise Ошибка("not_paired")
        conn = await asyncio.wait_for(self._endpoint.connect(self._адрес(у), ALPN), 25)
        self._соединения[id] = conn
        return conn

    async def _разорвать(self, id: str) -> None:
        conn = self._соединения.pop(id, None)
        if conn is not None:
            try:
                conn.close(0, b"unpaired")
            except Exception:
                pass

    async def _запрос(self, conn, метод: str, аргументы: dict[str, Any]) -> tuple[Any, bytes]:
        bi = await conn.open_bi()
        тело = json.dumps({"m": метод, "a": аргументы, "p": ПРОТОКОЛ, "who": self.имя}).encode("utf-8")
        await bi.send().write_all(len(тело).to_bytes(4, "big") + тело)
        await bi.send().finish()
        ответ, данные = await _прочитать(bi.recv(), МАКС_СООБЩЕНИЕ)
        if not ответ.get("ok"):
            raise Ошибка(str(ответ.get("e") or "unknown"), str(ответ.get("d") or ""))
        return ответ.get("r"), данные

    async def _вызвать(self, id: str, метод: str, аргументы: dict[str, Any]) -> tuple[Any, bytes]:
        try:
            conn = await self._соединение(id)
            return await asyncio.wait_for(self._запрос(conn, метод, аргументы), ТАЙМАУТ)
        except Ошибка:
            raise
        except Exception as e:
            self._соединения.pop(id, None)
            raise Ошибка("offline", str(e))

    async def _сопрячься(self, addr, секрет: str) -> dict[str, Any]:
        try:
            conn = await asyncio.wait_for(self._endpoint.connect(addr, ALPN), 25)
            ответ, _ = await asyncio.wait_for(self._запрос(conn, "pair", {
                "secret": секрет, "name": self.имя, "ticket": self.билет(),
            }), 30)
        except Ошибка:
            raise
        except Exception as e:
            raise Ошибка("offline", str(e))
        id = str(conn.remote_id())
        у = Устройство(
            id=id, name=str(ответ.get("name") or id[:10]),
            share="view" if ответ.get("mutual") else "none",
            relay=addr.relay_url() or "", direct=list(addr.direct_addresses()),
            online=True, remote_share="view", remote_proto=int(ответ.get("proto") or 0),
            last_seen=now(),
        )
        with self._замок:
            self.устройства[id] = у
        self._соединения[id] = conn
        self._сохранить()
        self.на_изменение()
        # Не ждать полминуты до опроса: первый забор сразу, в фоне.
        asyncio.get_running_loop().create_task(self._опросить(id))
        return у.to_dict()

    async def _опросить(self, только: str | None) -> None:
        with self._замок:
            ids = [i for i in self.устройства if только is None or i == только]
        for id in ids:
            with self._замок:
                у = self.устройства.get(id)
            if у is None:
                continue
            try:
                ответ, _ = await self._вызвать(id, "ping", {})
                у.online = True
                у.last_seen = now()
                у.remote_share = str(ответ.get("share") or "none")
                у.remote_proto = int(ответ.get("proto") or 0)
                if ответ.get("name"):
                    у.name = str(ответ["name"])
                if у.pending:
                    await self._послать_заметки(id)
                if у.remote_share == "view":
                    список, _ = await self._вызвать(id, "meetings", {})
                    у.remote_meetings = len(список)
                    await self._зеркало_папок(id, у)
                    if у.auto_pull and any(self._надо_забрать(у, m) for m in список):
                        # Забор идёт отдельной задачей: записи большие, а опрос
                        # зовёт и окно, ему нельзя висеть минуты. Замок на
                        # устройство не даст двум заборам сойтись.
                        asyncio.get_running_loop().create_task(self._забрать_новые(id))
                else:
                    у.remote_meetings = -1
            except Ошибка as e:
                у.online = e.код not in ("offline", "unavailable")
                if e.код == "not_paired":
                    у.remote_share = "none"
            except Exception:
                у.online = False
        self.на_изменение()

    async def _опрашивать(self) -> None:
        while not self._стоп:
            try:
                await self._опросить(None)
            except Exception:
                log.exception("Опрос устройств не удался")
            await asyncio.sleep(ОПРОС_СЕКУНД)

    def _замок_забора(self, id: str) -> asyncio.Lock:
        замок = self._замки_забора.get(id)
        if замок is None:
            замок = self._замки_забора[id] = asyncio.Lock()
        return замок

    async def _забрать(self, id: str, meeting_id: str) -> str:
        with self._замок:
            у = self.устройства.get(id)
        if у is None:
            raise Ошибка("not_paired")
        # Опрос по расписанию, забор после сопряжения и кнопка могут
        # сойтись на одной встрече: без замка выйдут две копии.
        async with self._замок_забора(id):
            if meeting_id in у.taken:
                return у.taken[meeting_id]
            return await self._забрать_под_замком(id, у, meeting_id)

    @staticmethod
    def _надо_забрать(у: Устройство, m: dict[str, Any]) -> bool:
        """Новая или изменившаяся у источника встреча."""
        чужой = m["id"]
        if чужой not in у.taken:
            return True
        return bool(m.get("stamp")) and у.stamps.get(чужой) != m.get("stamp")

    async def _обновить(self, id: str, у: Устройство, m: dict[str, Any]) -> bool:
        """Копия есть, но у источника встреча изменилась: догнать."""
        async with self._замок_забора(id):
            if у.stamps.get(m["id"]) == m.get("stamp"):
                return False
            данные, _ = await self._вызвать(id, "meeting", {"id": m["id"]})
            loop = asyncio.get_running_loop()
            скачать = self._скачиватель(id, m["id"], loop)
            жива = await loop.run_in_executor(None, self.обмен.обновить, у, у.taken[m["id"]], данные, скачать)
            у.stamps[m["id"]] = str(m.get("stamp") or "")
            self._сохранить()
            return жива

    def _скачиватель(self, id: str, meeting_id: str, loop):
        def скачать(имя: str, сдвиг: int, размер: int) -> bytes:
            # Зовётся из потока хранилища: гоняем запрос через наш цикл.
            будущее = asyncio.run_coroutine_threadsafe(
                self._вызвать(id, "audio", {"id": meeting_id, "name": имя, "offset": сдвиг, "size": размер}),
                loop,
            )
            return будущее.result(ТАЙМАУТ)
        return lambda имя, сдвиг, размер: скачать(имя, сдвиг, размер)[1]

    async def _забрать_под_замком(self, id: str, у: Устройство, meeting_id: str,
                                  stamp: str = "") -> str:
        данные, _ = await self._вызвать(id, "meeting", {"id": meeting_id})
        loop = asyncio.get_running_loop()
        скачать = self._скачиватель(id, meeting_id, loop)
        наш = await loop.run_in_executor(None, self.обмен.сохранить, у, данные, скачать)
        у.stamps[meeting_id] = stamp
        self._сохранить()
        self.на_изменение()
        return наш

    async def _зеркало_папок(self, id: str, у: Устройство) -> None:
        """Папки источника, в том числе пустые. Старая версия источника
        запроса не знает: тогда папки появятся только со встречами."""
        try:
            имена, _ = await self._вызвать(id, "folders", {})
        except Ошибка as e:
            if e.код == "unknown":
                return
            raise
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.обмен.зеркало_папок, у, list(имена or []))

    async def _забрать_новые(self, id: str) -> int:
        with self._замок:
            у = self.устройства.get(id)
        if у is None:
            raise Ошибка("not_paired")
        список, _ = await self._вызвать(id, "meetings", {})
        у.remote_meetings = len(список)
        await self._зеркало_папок(id, у)
        сколько = 0
        try:
            for m in список:
                if m["id"] not in у.taken:
                    async with self._замок_забора(id):
                        if m["id"] in у.taken:
                            continue
                        await self._забрать_под_замком(id, у, m["id"], str(m.get("stamp") or ""))
                    сколько += 1
                elif self._надо_забрать(у, m):
                    if await self._обновить(id, у, m):
                        сколько += 1
        except Ошибка as e:
            log.info("Забор у %s прервался после %d встреч: %s", у.name, сколько, e)
            if сколько == 0:
                raise
        self.на_изменение()
        return сколько

    # --- сторона, которая отдаёт -----------------------------------------

    async def _принимать(self) -> None:
        while not self._стоп:
            try:
                inc = await self._endpoint.accept_next()
            except Exception:
                if self._стоп:
                    return
                await asyncio.sleep(0.5)
                continue
            if inc is None:
                return
            asyncio.get_running_loop().create_task(self._обслужить(inc))

    async def _обслужить(self, inc) -> None:
        try:
            acc = await inc.accept()
            conn = await acc.connect()
        except Exception:
            return
        кто = str(conn.remote_id())
        while not self._стоп:
            try:
                bi = await conn.accept_bi()
            except Exception:
                break
            asyncio.get_running_loop().create_task(self._ответить(кто, bi))

    async def _ответить(self, кто: str, bi) -> None:
        with self._замок:
            у = self.устройства.get(кто)
        лимит = МАКС_СООБЩЕНИЕ if у is not None else ЛИМИТ_ЧУЖОГО
        данные: bytes = b""
        try:
            запрос, _ = await asyncio.wait_for(_прочитать(bi.recv(), лимит), ТАЙМАУТ)
            метод = str(запрос.get("m") or "")
            аргументы = запрос.get("a") or {}
            if метод == "pair":
                результат = self._сопряжение(кто, аргументы)
            elif у is None:
                raise Ошибка("not_paired")
            elif метод == "ping":
                у.name = str(запрос.get("who") or у.name)
                у.remote_proto = int(запрос.get("p") or 0)
                у.online = True
                у.last_seen = now()
                результат = {"proto": ПРОТОКОЛ, "name": self.имя, "share": у.share}
            elif метод == "poke":
                # У того компьютера что-то изменилось: спросим его сейчас.
                # Толчок идёт от того, кто открыл встречи НАМ, поэтому он
                # стоит до проверки, открыли ли мы что-то ему.
                log.debug("Толчок от %s, открыт нам: %s", кто[:10], у.remote_share)
                if у.remote_share == "view":
                    self._опросить_скоро(кто)
                результат = {"ok": True}
            elif у.share != "view":
                raise Ошибка("no_access")
            elif метод == "meetings":
                результат = self.обмен.встречи()
            elif метод == "folders":
                результат = self.обмен.папки()
            elif метод == "meeting":
                результат = self.обмен.встреча(str(аргументы.get("id") or ""))
            elif метод == "notes":
                # Тот, кому мы открыли встречи, пишет к ним заметки: это его
                # копия нашей встречи, и его тезисы нам нужны.
                результат = {"changed": self.обмен.применить_заметки(
                    str(аргументы.get("id") or ""), аргументы)}
            elif метод == "audio":
                данные = self.обмен.читать_звук(
                    str(аргументы.get("id") or ""), str(аргументы.get("name") or ""),
                    int(аргументы.get("offset") or 0), int(аргументы.get("size") or КУСОК),
                )
                результат = {"n": len(данные)}
            else:
                raise Ошибка("unknown", метод)
            ответ = {"ok": True, "r": результат, "n": len(данные)}
        except Ошибка as e:
            ответ = {"ok": False, "e": e.код, "d": str(e)}
            данные = b""
        except Exception as e:
            log.exception("Запрос от %s не обработан", кто[:10])
            ответ = {"ok": False, "e": "internal", "d": type(e).__name__}
            данные = b""
        try:
            тело = json.dumps(ответ, ensure_ascii=False).encode("utf-8")
            await bi.send().write_all(len(тело).to_bytes(4, "big") + тело + данные)
            await bi.send().finish()
        except Exception:
            pass

    def _сопряжение(self, кто: str, аргументы: dict[str, Any]) -> dict[str, Any]:
        if not self._лимит_попыток(кто):
            raise Ошибка("too_many")
        взаимно = self._взять_код(str(аргументы.get("secret") or ""))
        if взаимно is None:
            raise Ошибка("bad_code")
        relay, direct = "", []
        try:
            addr = iroh.EndpointTicket.from_string(str(аргументы.get("ticket") or "")).endpoint_addr()
            if str(addr.id()) == кто:
                relay, direct = addr.relay_url() or "", list(addr.direct_addresses())
        except Exception:
            pass
        у = Устройство(
            id=кто, name=str(аргументы.get("name") or кто[:10]), share="view",
            relay=relay, direct=direct, online=True, last_seen=now(),
            remote_share="view" if взаимно else "none",
        )
        with self._замок:
            self.устройства[кто] = у
        self._сохранить()
        self.на_изменение()
        if взаимно:
            # Тот компьютер открылся нам в ответ: забираем его встречи, не
            # дожидаясь опроса. Соединение к нему поднимется своё.
            asyncio.get_running_loop().create_task(self._опросить(кто))
        return {"name": self.имя, "mutual": взаимно, "proto": ПРОТОКОЛ}


async def _прочитать(recv, лимит: int) -> tuple[dict[str, Any], bytes]:
    """4 байта длины, JSON, затем сырые байты, сколько JSON обещал в n."""
    длина = int.from_bytes(await recv.read_exact(4), "big")
    if длина <= 0 or длина > лимит:
        raise Ошибка("too_big", str(длина))
    тело = json.loads((await recv.read_exact(длина)).decode("utf-8"))
    n = int(тело.get("n") or 0)
    if n < 0 or n > КУСОК:
        raise Ошибка("too_big", str(n))
    данные = await recv.read_exact(n) if n else b""
    return тело, данные
