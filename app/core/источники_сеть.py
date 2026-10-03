"""Сетевые источники записей: сервер АТС по SFTP/FTP и облачные телефонии.

Папка встреч берёт записи не только из каталога на диске (core/источники.py),
но и по сети. Устройство у всех одно:
- новые(с, до) — какие записи появились за окно времени (или сейчас лежат
  на сервере, для SFTP и FTP);
- скачать(запись, куда) — положить звук в локальную папку программы.
Дальше запись идёт тем же путём, что и файл с диска: общий импорт, папка,
итоги в Telegram.

Опрос, а не вебхуки: у Конспекта нет публичного адреса, на который
телефония могла бы прислать событие, и заводить его ради этого — значит
пропускать записи через чужой сервер. Опрос раз в несколько минут идёт
прямо с компьютера к провайдеру.

Ключи и пароли хранятся зашифрованными средствами Windows (core/секрет.py)
и в журнал не попадают. С серверов и из телефонии ничего не удаляем.

Протоколы взяты из открытой документации провайдеров:
- Mango Office: ВАТС API, подпись sha256(ключ + json + соль), отчёт о
  звонках запрашивается и забирается по ключу, запись — по recording_id.
- UIS (CoMagic): Data API v2.0, JSON-RPC, метод get.calls_report.
- Битрикс24: входящий вебхук, voximplant.statistic.get и disk.file.get.
"""
from __future__ import annotations

import datetime as dt
import ftplib
import hashlib
import json
import logging
import posixpath
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from . import секрет

log = logging.getLogger(__name__)
# Ключ вебхука Битрикс24 и ключи временных ссылок стоят прямо в адресе.
секрет.прикрыть_журнал_запросов()

ЗВУК = {
    ".mp3", ".wav", ".m4a", ".ogg", ".oga", ".opus", ".flac", ".aac", ".wma",
    ".amr", ".gsm", ".mp4", ".webm", ".mkv", ".3gp",
}
МОСКВА = dt.timezone(dt.timedelta(hours=3))


class ОшибкаИсточника(Exception):
    """code уходит в окно и переводится там: auth, network, not_found, api."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class Запись:
    """Одна запись в источнике: что о ней известно до скачивания."""

    id: str                          # уникально в пределах источника
    имя: str = ""                    # имя файла для скачанного звука
    когда: float | None = None       # unix-время звонка
    номер: str | None = None         # телефон собеседника
    сотрудник: str = ""              # кто говорил с нашей стороны
    направление: str = ""            # in, out
    длительность: float = 0.0
    данные: dict[str, Any] = field(default_factory=dict)  # что нужно для скачивания


def _номер(сырой: str | None) -> str | None:
    """Номер в виде +7 916 123-45-67, если он российский; иначе как пришёл."""
    if not сырой:
        return None
    цифры = re.sub(r"\D", "", str(сырой))
    if len(цифры) == 11 and цифры[0] in "78":
        return f"+7 {цифры[1:4]} {цифры[4:7]}-{цифры[7:9]}-{цифры[9:11]}"
    if len(цифры) == 10 and цифры[0] == "9":
        return f"+7 {цифры[0:3]} {цифры[3:6]}-{цифры[6:8]}-{цифры[8:10]}"
    return str(сырой).strip() or None


def _безопасное_имя(имя: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", имя).strip()[:120] or "запись"


# --- SFTP и FTP -----------------------------------------------------------------

class СерверныйИсточник:
    """Каталог на сервере: записи — звуковые файлы. Новые — те, что лежат сейчас.

    Готовность файла на сервере проверяет общий сторож так же, как на диске:
    размер и время изменения не менялись между двумя обходами.
    """

    ГЛУБИНА = 3
    ПРЕДЕЛ = 20000

    def __init__(self, хост: str, порт: int, логин: str, пароль: str, каталог: str) -> None:
        self.хост = хост
        self.порт = int(порт)
        self.логин = логин
        self.пароль = пароль
        self.каталог = каталог or "/"

    def _ид(self, путь: str, размер: int, время: float) -> str:
        return f"{путь}|{размер}|{int(время)}"


class SftpИсточник(СерверныйИсточник):
    def __init__(self, хост, порт, логин, пароль, каталог, отпечаток_сервера: str = "") -> None:
        super().__init__(хост, порт or 22, логин, пароль, каталог)
        # Отпечаток ключа сервера, запомненный при первом подключении. Сменился —
        # отказываемся: так выглядит и переустановка сервера, и подмена.
        self.отпечаток_сервера = отпечаток_сервера
        self.новый_отпечаток = ""

    def _соединение(self):
        import paramiko

        транспорт = paramiko.Transport((self.хост, self.порт))
        try:
            транспорт.start_client(timeout=20)
            ключ = транспорт.get_remote_server_key()
            отпечаток = hashlib.sha256(ключ.asbytes()).hexdigest()
            if self.отпечаток_сервера and отпечаток != self.отпечаток_сервера:
                raise ОшибкаИсточника("host_key", отпечаток)
            self.новый_отпечаток = отпечаток
            транспорт.auth_password(self.логин, self.пароль)
            if not транспорт.is_authenticated():
                raise ОшибкаИсточника("auth")
            return транспорт, paramiko.SFTPClient.from_transport(транспорт)
        except paramiko.AuthenticationException:
            транспорт.close()
            raise ОшибкаИсточника("auth") from None
        except (paramiko.SSHException, OSError) as беда:
            транспорт.close()
            raise ОшибкаИсточника("network", str(беда)) from None
        except ОшибкаИсточника:
            транспорт.close()
            raise

    def проверить(self) -> str:
        транспорт, sftp = self._соединение()
        try:
            sftp.listdir(self.каталог)
        except OSError:
            raise ОшибкаИсточника("not_found", self.каталог) from None
        finally:
            транспорт.close()
        return self.новый_отпечаток

    def новые(self, с: float | None = None, до: float | None = None) -> list[Запись]:
        import stat as st

        транспорт, sftp = self._соединение()
        найдено: list[Запись] = []
        try:
            очередь = [(self.каталог, 0)]
            while очередь and len(найдено) < self.ПРЕДЕЛ:
                где, уровень = очередь.pop()
                try:
                    элементы = sftp.listdir_attr(где)
                except OSError:
                    continue
                for э in элементы:
                    if э.filename.startswith("."):
                        continue
                    путь = posixpath.join(где, э.filename)
                    if st.S_ISDIR(э.st_mode or 0):
                        if уровень + 1 < self.ГЛУБИНА:
                            очередь.append((путь, уровень + 1))
                    elif posixpath.splitext(э.filename)[1].lower() in ЗВУК and (э.st_size or 0) > 0:
                        найдено.append(Запись(
                            id=self._ид(путь, э.st_size, э.st_mtime or 0), имя=э.filename,
                            когда=float(э.st_mtime or 0) or None,
                            данные={"путь": путь, "размер": э.st_size, "время": э.st_mtime},
                        ))
        finally:
            транспорт.close()
        return найдено

    def скачать(self, запись: Запись, куда: Path) -> Path:
        транспорт, sftp = self._соединение()
        try:
            куда.mkdir(parents=True, exist_ok=True)
            цель = куда / _безопасное_имя(запись.имя)
            sftp.get(запись.данные["путь"], str(цель))
            return цель
        except OSError as беда:
            raise ОшибкаИсточника("network", str(беда)) from None
        finally:
            транспорт.close()


class FtpИсточник(СерверныйИсточник):
    """FTP, по возможности с шифрованием (FTPS). Голый FTP передаёт пароль открытым текстом."""

    def __init__(self, хост, порт, логин, пароль, каталог, шифровать: bool = True) -> None:
        super().__init__(хост, порт or 21, логин, пароль, каталог)
        self.шифровать = шифровать

    def _соединение(self) -> ftplib.FTP:
        ftp = ftplib.FTP_TLS(timeout=30) if self.шифровать else ftplib.FTP(timeout=30)
        try:
            ftp.connect(self.хост, self.порт)
            ftp.login(self.логин, self.пароль)
            if self.шифровать:
                ftp.prot_p()
            return ftp
        except ftplib.error_perm as беда:
            ftp.close()
            raise ОшибкаИсточника("auth", str(беда)) from None
        except (ftplib.Error, OSError) as беда:
            ftp.close()
            raise ОшибкаИсточника("network", str(беда)) from None

    def проверить(self) -> str:
        ftp = self._соединение()
        try:
            ftp.cwd(self.каталог)
        except ftplib.error_perm:
            raise ОшибкаИсточника("not_found", self.каталог) from None
        finally:
            ftp.close()
        return ""

    def новые(self, с: float | None = None, до: float | None = None) -> list[Запись]:
        ftp = self._соединение()
        найдено: list[Запись] = []
        try:
            очередь = [(self.каталог, 0)]
            while очередь and len(найдено) < self.ПРЕДЕЛ:
                где, уровень = очередь.pop()
                try:
                    элементы = list(ftp.mlsd(где, facts=["type", "size", "modify"]))
                except ftplib.Error:
                    continue
                for имя, факты in элементы:
                    if имя in (".", "..") or имя.startswith("."):
                        continue
                    путь = posixpath.join(где, имя)
                    if факты.get("type") == "dir":
                        if уровень + 1 < self.ГЛУБИНА:
                            очередь.append((путь, уровень + 1))
                    elif факты.get("type") == "file" and posixpath.splitext(имя)[1].lower() in ЗВУК:
                        размер = int(факты.get("size") or 0)
                        if not размер:
                            continue
                        try:
                            время = dt.datetime.strptime(факты.get("modify", "")[:14], "%Y%m%d%H%M%S") \
                                .replace(tzinfo=dt.timezone.utc).timestamp()
                        except ValueError:
                            время = 0.0
                        найдено.append(Запись(id=self._ид(путь, размер, время), имя=имя,
                                              когда=время or None,
                                              данные={"путь": путь, "размер": размер, "время": время}))
        finally:
            ftp.close()
        return найдено

    def скачать(self, запись: Запись, куда: Path) -> Path:
        ftp = self._соединение()
        try:
            куда.mkdir(parents=True, exist_ok=True)
            цель = куда / _безопасное_имя(запись.имя)
            with цель.open("wb") as f:
                ftp.retrbinary(f"RETR {запись.данные['путь']}", f.write)
            return цель
        except (ftplib.Error, OSError) as беда:
            raise ОшибкаИсточника("network", str(беда)) from None
        finally:
            ftp.close()


# --- облачные телефонии ------------------------------------------------------

class HttpИсточник:
    ТАЙМАУТ = httpx.Timeout(60, connect=15)

    def __init__(self, клиент: httpx.Client | None = None) -> None:
        self._клиент = клиент

    def _http(self) -> httpx.Client:
        if self._клиент is None:
            self._клиент = httpx.Client(timeout=self.ТАЙМАУТ)
        return self._клиент

    def _скачать_url(self, url: str, запись: Запись, куда: Path, заголовки: dict | None = None) -> Path:
        куда.mkdir(parents=True, exist_ok=True)
        try:
            with self._http().stream("GET", url, headers=заголовки or {}, follow_redirects=True) as ответ:
                ответ.raise_for_status()
                тип = ответ.headers.get("content-type", "")
                if тип.startswith("text/html"):
                    # Вместо записи страница входа: ссылка истекла или нужен доступ.
                    raise ОшибкаИсточника("not_found", "html вместо записи")
                расширение = Path(запись.имя).suffix or {
                    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
                    "audio/ogg": ".ogg", "audio/mp4": ".m4a",
                }.get(тип.split(";")[0].strip(), ".mp3")
                цель = куда / (_безопасное_имя(Path(запись.имя).stem or запись.id) + расширение)
                with цель.open("wb") as f:
                    for кусок in ответ.iter_bytes():
                        f.write(кусок)
        except httpx.HTTPError as беда:
            raise ОшибкаИсточника("network", str(беда)) from None
        if цель.stat().st_size == 0:
            цель.unlink(missing_ok=True)
            raise ОшибкаИсточника("not_ready")
        return цель


class MangoИсточник(HttpИсточник):
    """Mango Office, ВАТС API: ключ и соль из кабинета (Настройки → API)."""

    АДРЕС = "https://app.mango-office.ru"

    def __init__(self, ключ: str, соль: str, клиент: httpx.Client | None = None,
                 пауза: float = 1.0) -> None:
        super().__init__(клиент)
        self.ключ = ключ
        self.соль = соль
        self.пауза = пауза

    def _подпись(self, тело: str) -> str:
        return hashlib.sha256((self.ключ + тело + self.соль).encode("utf-8")).hexdigest()

    def _запрос(self, путь: str, данные: dict, без_перехода: bool = False) -> httpx.Response:
        тело = json.dumps(данные, separators=(",", ":"), ensure_ascii=False)
        try:
            ответ = self._http().post(
                self.АДРЕС + путь,
                data={"vpbx_api_key": self.ключ, "sign": self._подпись(тело), "json": тело},
                follow_redirects=not без_перехода,
            )
        except httpx.HTTPError as беда:
            raise ОшибкаИсточника("network", str(беда)) from None
        if ответ.status_code in (401, 403):
            raise ОшибкаИсточника("auth")
        return ответ

    def _json(self, путь: str, данные: dict) -> dict:
        ответ = self._запрос(путь, данные)
        try:
            итог = ответ.json()
        except ValueError:
            raise ОшибкаИсточника("api", f"{ответ.status_code}") from None
        код = итог.get("result")
        if код in (3101, 3102, 3103):  # неверный ключ или подпись
            raise ОшибкаИсточника("auth", str(код))
        return итог

    def проверить(self) -> str:
        self._json("/vpbx/account/balance", {})
        return ""

    def новые(self, с: float, до: float) -> list[Запись]:
        def время(t: float) -> str:
            return dt.datetime.fromtimestamp(t, МОСКВА).strftime("%d.%m.%Y %H:%M:%S")

        записи: dict[str, Запись] = {}
        сдвиг = 0
        for _ in range(20):
            заявка = self._json("/vpbx/stats/calls/request", {
                "start_date": время(с), "end_date": время(до), "limit": 1000, "offset": сдвиг,
            })
            ключ = заявка.get("key")
            if not ключ:
                raise ОшибкаИсточника("api", "нет ключа отчёта")
            отчёт = None
            for _ in range(60):
                ответ = self._json("/vpbx/stats/calls/result/", {"key": ключ})
                if ответ.get("status") == "complete" or ответ.get("data"):
                    отчёт = ответ
                    break
                time.sleep(self.пауза)
            if отчёт is None:
                raise ОшибкаИсточника("api", "отчёт не готов")
            звонки = ((отчёт.get("data") or [{}])[0] or {}).get("list") or []
            новых = 0
            for з in звонки:
                ид = str(з.get("entry_id") or "")
                запись_ид = next((c.get("recording_id") for c in з.get("context_calls") or []
                                  if c.get("recording_id")), None)
                if not ид or not запись_ид or ид in записи:
                    continue
                входящий = з.get("context_type") == 1
                участник = ((з.get("context_calls") or [{}])[0].get("members") or [{}])[0]
                записи[ид] = Запись(
                    id=ид, имя=f"mango-{ид}",
                    когда=float(з.get("context_start_time") or 0) or None,
                    номер=_номер(з.get("caller_number") if входящий else з.get("called_number")),
                    сотрудник=str((участник or {}).get("call_abonent_info") or "") if входящий
                    else str(з.get("caller_name") or ""),
                    направление="in" if входящий else "out",
                    длительность=float(з.get("talk_duration") or 0),
                    данные={"recording_id": запись_ид},
                )
                новых += 1
            if len(звонки) < 1000 or not новых:
                break
            сдвиг += 1000
        return [з for з in записи.values() if з.длительность > 0]

    def скачать(self, запись: Запись, куда: Path) -> Path:
        ответ = self._запрос("/vpbx/queries/recording/post",
                             {"recording_id": запись.данные["recording_id"], "action": "download"},
                             без_перехода=True)
        ссылка = ответ.headers.get("location")
        if not ссылка:
            raise ОшибкаИсточника("not_ready", str(ответ.status_code))
        return self._скачать_url(ссылка, запись, куда)


class UisИсточник(HttpИсточник):
    """UIS (CoMagic), Data API v2.0: ключ из кабинета (Настройки → API)."""

    АДРЕС = "https://dataapi.uiscom.ru/v2.0"
    ЗАПИСЬ = "https://app.uiscom.ru/system/media/talk/{communication_id}/{record}/"
    ПОЛЯ = ["id", "start_time", "talk_duration", "direction", "communication_id", "call_records",
            "is_lost", "last_answered_employee_full_name", "contact_phone_number"]

    def __init__(self, ключ: str, клиент: httpx.Client | None = None) -> None:
        super().__init__(клиент)
        self.ключ = ключ

    def _вызов(self, метод: str, параметры: dict) -> dict:
        тело = {"jsonrpc": "2.0", "id": 1, "method": метод,
                "params": {"access_token": self.ключ, **параметры}}
        try:
            ответ = self._http().post(self.АДРЕС, json=тело)
            данные = ответ.json()
        except httpx.HTTPError as беда:
            raise ОшибкаИсточника("network", str(беда)) from None
        except ValueError:
            raise ОшибкаИсточника("api", "не JSON") from None
        беда = данные.get("error")
        if беда:
            код = беда.get("code")
            raise ОшибкаИсточника("auth" if код in (-32001, -32003) else "api", str(беда.get("message")))
        return данные.get("result") or {}

    def проверить(self) -> str:
        сейчас = time.time()
        self.новые(сейчас - 3600, сейчас)
        return ""

    def новые(self, с: float, до: float) -> list[Запись]:
        def время(t: float) -> str:
            return dt.datetime.fromtimestamp(t, МОСКВА).strftime("%Y-%m-%d %H:%M:%S")

        записи: list[Запись] = []
        сдвиг = 0
        for _ in range(100):
            итог = self._вызов("get.calls_report", {
                "date_from": время(с), "date_till": время(до), "limit": 1000, "offset": сдвиг,
                "fields": self.ПОЛЯ,
            })
            звонки = (итог.get("data") or [])
            for з in звонки:
                if з.get("is_lost") or not з.get("call_records"):
                    continue
                try:
                    когда = dt.datetime.strptime(str(з.get("start_time")), "%Y-%m-%d %H:%M:%S") \
                        .replace(tzinfo=МОСКВА).timestamp()
                except ValueError:
                    когда = None
                записи.append(Запись(
                    id=str(з.get("id")), имя=f"uis-{з.get('id')}", когда=когда,
                    номер=_номер(з.get("contact_phone_number")),
                    сотрудник=str(з.get("last_answered_employee_full_name") or ""),
                    направление="in" if з.get("direction") == "in" else "out",
                    длительность=float(з.get("talk_duration") or 0),
                    данные={"communication_id": з.get("communication_id"), "record": з["call_records"][0]},
                ))
            if len(звонки) < 1000:
                break
            сдвиг += 1000
        return записи

    def скачать(self, запись: Запись, куда: Path) -> Path:
        return self._скачать_url(self.ЗАПИСЬ.format(**запись.данные), запись, куда)


class БитриксИсточник(HttpИсточник):
    """Битрикс24: входящий вебхук с правами telephony, disk и user."""

    def __init__(self, вебхук: str, клиент: httpx.Client | None = None) -> None:
        super().__init__(клиент)
        self.вебхук = вебхук.rstrip("/") + "/"
        self._сотрудники: dict[str, str] = {}

    def _вызов(self, метод: str, параметры: dict) -> dict:
        try:
            ответ = self._http().post(f"{self.вебхук}{метод}.json", json=параметры)
            данные = ответ.json()
        except httpx.HTTPError as беда:
            raise ОшибкаИсточника("network", str(беда)) from None
        except ValueError:
            raise ОшибкаИсточника("api", f"{ответ.status_code}") from None
        if данные.get("error"):
            код = str(данные.get("error"))
            raise ОшибкаИсточника(
                "auth" if код in ("INVALID_CREDENTIALS", "insufficient_scope", "NO_AUTH_FOUND") else "api",
                str(данные.get("error_description") or код))
        return данные

    def проверить(self) -> str:
        self._вызов("voximplant.statistic.get", {"FILTER": {">CALL_DURATION": 0}, "start": 0})
        return ""

    def _сотрудник(self, ид: str) -> str:
        if not ид:
            return ""
        if ид not in self._сотрудники:
            try:
                люди = self._вызов("user.get", {"ID": ид}).get("result") or []
                ч = люди[0] if люди else {}
                self._сотрудники[ид] = " ".join(x for x in (ч.get("NAME"), ч.get("LAST_NAME")) if x)
            except ОшибкаИсточника:
                self._сотрудники[ид] = ""
        return self._сотрудники[ид]

    def новые(self, с: float, до: float) -> list[Запись]:
        def время(t: float) -> str:
            return dt.datetime.fromtimestamp(t, МОСКВА).isoformat()

        записи: list[Запись] = []
        начало = 0
        for _ in range(200):
            данные = self._вызов("voximplant.statistic.get", {
                "FILTER": {">=CALL_START_DATE": время(с), "<=CALL_START_DATE": время(до), ">CALL_DURATION": 0},
                "SORT": "CALL_START_DATE", "ORDER": "ASC", "start": начало,
            })
            for з in данные.get("result") or []:
                if not з.get("RECORD_FILE_ID"):
                    continue  # запись ещё не готова или не велась: возьмём на следующем обходе
                try:
                    когда = dt.datetime.fromisoformat(str(з.get("CALL_START_DATE"))).timestamp()
                except ValueError:
                    когда = None
                записи.append(Запись(
                    id=str(з.get("CALL_ID") or з.get("ID")), имя=f"bitrix-{з.get('ID')}", когда=когда,
                    номер=_номер(з.get("PHONE_NUMBER")),
                    сотрудник=self._сотрудник(str(з.get("PORTAL_USER_ID") or "")),
                    направление="out" if str(з.get("CALL_TYPE")) == "1" else "in",
                    длительность=float(з.get("CALL_DURATION") or 0),
                    данные={"file_id": з.get("RECORD_FILE_ID")},
                ))
            следующий = данные.get("next")
            if not следующий:
                break
            начало = int(следующий)
        return записи

    def скачать(self, запись: Запись, куда: Path) -> Path:
        файл = self._вызов("disk.file.get", {"id": запись.данные["file_id"]}).get("result") or {}
        ссылка = файл.get("DOWNLOAD_URL")
        if not ссылка:
            raise ОшибкаИсточника("not_ready")
        if файл.get("NAME"):
            запись.имя = файл["NAME"]
        return self._скачать_url(ссылка, запись, куда)


ВИДЫ = ("disk", "sftp", "ftp", "mango", "uis", "bitrix")
# Какие поля конфигурации секретные: шифруются и не уходят в окно.
СЕКРЕТЫ = {"sftp": ("password",), "ftp": ("password",), "mango": ("key", "salt"),
           "uis": ("key",), "bitrix": ("webhook",)}
# Серверы с файлами опрашиваем часто: там проверка готовности по двум обходам.
# Телефонии — реже: у них квоты на запросы, а запись звонка и так появляется
# с задержкой в минуты.
ИНТЕРВАЛЫ = {"sftp": 60.0, "ftp": 60.0, "mango": 300.0, "uis": 300.0, "bitrix": 300.0}


def собрать(вид: str, конфиг: dict[str, Any], клиент: httpx.Client | None = None):
    """Источник по виду и уже расшифрованной конфигурации."""
    if вид == "sftp":
        return SftpИсточник(конфиг.get("host", ""), конфиг.get("port") or 22, конфиг.get("user", ""),
                            конфиг.get("password", ""), конфиг.get("dir", "/"), конфиг.get("host_key", ""))
    if вид == "ftp":
        return FtpИсточник(конфиг.get("host", ""), конфиг.get("port") or 21, конфиг.get("user", ""),
                           конфиг.get("password", ""), конфиг.get("dir", "/"), bool(конфиг.get("tls", True)))
    if вид == "mango":
        return MangoИсточник(конфиг.get("key", ""), конфиг.get("salt", ""), клиент)
    if вид == "uis":
        return UisИсточник(конфиг.get("key", ""), клиент)
    if вид == "bitrix":
        return БитриксИсточник(конфиг.get("webhook", ""), клиент)
    raise ОшибкаИсточника("api", f"неизвестный вид {вид}")


def описание(вид: str, конфиг: dict[str, Any]) -> str:
    """Что показать у папки вместо пути: без секретов."""
    if вид in ("sftp", "ftp"):
        return f"{вид}://{конфиг.get('user', '')}@{конфиг.get('host', '')}{конфиг.get('dir', '/')}"
    return {"mango": "Mango Office", "uis": "UIS", "bitrix": "Битрикс24"}.get(вид, вид)
