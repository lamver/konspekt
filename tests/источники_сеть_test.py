# -*- coding: utf-8 -*-
"""Сетевые источники записей: SFTP, FTP, Mango Office, UIS, Битрикс24.

Телефонии подменены httpx.MockTransport по их открытым протоколам: подпись
Mango, JSON-RPC UIS, REST Битрикс24. SFTP — настоящий: тест поднимает
SFTP-сервер на paramiko у себя на случайном порту. Проверяем то, что бьёт
по человеку:
- записи находятся, называются по номеру и сотруднику, качаются;
- окно телефонии заходит назад, курсор не уходит за запись, которая ещё
  не готова у провайдера;
- одна запись не берётся дважды, даже пришедшая под другим id;
- ключи и пароли в базе зашифрованы и не уходят в окно.
"""
from __future__ import annotations

import testenv  # noqa: F401

import datetime as dt
import hashlib
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from app.core import источники as ист
from app.core import источники_сеть as сеть

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ok] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


ЗВУК = b"ID3" + bytes(range(256)) * 40
МСК = dt.timezone(dt.timedelta(hours=3))

# --- Mango Office -----------------------------------------------------------
mango_запросы: list[tuple[str, dict]] = []


def mango(запрос: httpx.Request) -> httpx.Response:
    путь = запрос.url.path
    if запрос.url.host == "rec.mango.test":
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=ЗВУК)
    форма = {k: v[0] for k, v in parse_qs(запрос.content.decode()).items()}
    тело = json.loads(форма["json"])
    mango_запросы.append((путь, тело))
    ждём = hashlib.sha256(("KEY" + форма["json"] + "SALT").encode()).hexdigest()
    if форма.get("sign") != ждём or форма.get("vpbx_api_key") != "KEY":
        return httpx.Response(200, json={"result": 3101})
    if путь == "/vpbx/account/balance":
        return httpx.Response(200, json={"result": 1000, "balance": 10})
    if путь == "/vpbx/stats/calls/request":
        return httpx.Response(200, json={"result": 1000, "key": "K1"})
    if путь == "/vpbx/stats/calls/result/":
        return httpx.Response(200, json={"result": 1000, "status": "complete", "data": [{"list": [
            {"entry_id": "E1", "context_start_time": 1791026525, "talk_duration": 95, "context_type": 1,
             "caller_number": "79161234567", "called_number": "74950000000",
             "context_calls": [{"recording_id": "R1", "members": [{"call_abonent_info": "Анна Смирнова"}]}]},
            {"entry_id": "E2", "context_start_time": 1791026600, "talk_duration": 0, "context_type": 1,
             "caller_number": "79160000000", "context_calls": [{"recording_id": "R2"}]},
            {"entry_id": "E3", "context_start_time": 1791026700, "talk_duration": 40, "context_type": 2,
             "caller_name": "Игорь", "called_number": "89031112233", "context_calls": [{"members": []}]},
        ]}]})
    if путь == "/vpbx/queries/recording/post":
        return httpx.Response(302, headers={"location": "https://rec.mango.test/r/R1.mp3?token=x"})
    return httpx.Response(404)


клиент = httpx.Client(transport=httpx.MockTransport(mango))
м = сеть.MangoИсточник("KEY", "SALT", клиент, пауза=0)
м.проверить()
проверить(mango_запросы[-1][0] == "/vpbx/account/balance", "Mango: проверка ключа — запрос баланса с подписью")
записи = м.новые(1791026000, 1791027000)
проверить([з.id for з in записи] == ["E1"], "Mango: берётся разговор с записью; без записи и без разговора — нет")
з = записи[0]
проверить(з.номер == "+7 916 123-45-67" and з.сотрудник == "Анна Смирнова" and з.направление == "in",
          f"Mango: номер, сотрудник, направление ({з.номер}, {з.сотрудник})")
заявка = next(т for п, т in mango_запросы if п == "/vpbx/stats/calls/request")
проверить(заявка["start_date"] == dt.datetime.fromtimestamp(1791026000, МСК).strftime("%d.%m.%Y %H:%M:%S"),
          f"Mango: окно по московскому времени: {заявка['start_date']}")
with tempfile.TemporaryDirectory() as tmp:
    путь = м.скачать(з, Path(tmp))
    проверить(путь.read_bytes() == ЗВУК and путь.suffix == ".mp3", "Mango: запись скачана по временной ссылке")
плохой = сеть.MangoИсточник("KEY", "ДРУГАЯ", клиент, пауза=0)
try:
    плохой.проверить()
    проверить(False, "Mango: неверная соль даёт ошибку")
except сеть.ОшибкаИсточника as беда:
    проверить(беда.code == "auth", "Mango: неверная соль — ошибка «auth», а не молчание")

# --- UIS ----------------------------------------------------------------------
uis_вызовы: list[dict] = []


def uis(запрос: httpx.Request) -> httpx.Response:
    if запрос.url.host == "app.uiscom.ru":
        проверить(запрос.url.path == "/system/media/talk/C77/REC9/", f"UIS: адрес записи {запрос.url.path}")
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=ЗВУК)
    тело = json.loads(запрос.content)
    uis_вызовы.append(тело)
    if тело["params"]["access_token"] != "TOKEN":
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32001, "message": "auth"}})
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"data": [
        {"id": 501, "start_time": "2026-10-03 14:22:05", "talk_duration": 61, "direction": "in",
         "communication_id": "C77", "call_records": ["REC9"], "is_lost": False,
         "last_answered_employee_full_name": "Пётр", "contact_phone_number": "79031234567"},
        {"id": 502, "start_time": "2026-10-03 14:30:00", "talk_duration": 0, "direction": "in",
         "communication_id": "C78", "call_records": [], "is_lost": True},
    ]}})


у = сеть.UisИсточник("TOKEN", httpx.Client(transport=httpx.MockTransport(uis)))
записи = у.новые(time.time() - 3600, time.time())
проверить([з.id for з in записи] == ["501"] and записи[0].сотрудник == "Пётр", "UIS: пропущенные и без записи не берутся")
проверить("fields" in uis_вызовы[-1]["params"] and "call_records" in uis_вызовы[-1]["params"]["fields"],
          "UIS: поля отчёта перечислены явно, иначе нет сотрудника")
проверить(записи[0].когда == dt.datetime(2026, 10, 3, 14, 22, 5, tzinfo=МСК).timestamp(), "UIS: время по Москве")
with tempfile.TemporaryDirectory() as tmp:
    проверить(у.скачать(записи[0], Path(tmp)).read_bytes() == ЗВУК, "UIS: запись скачана")
try:
    сеть.UisИсточник("ЧУЖОЙ", httpx.Client(transport=httpx.MockTransport(uis))).проверить()
    проверить(False, "UIS: чужой ключ")
except сеть.ОшибкаИсточника as беда:
    проверить(беда.code == "auth", "UIS: неверный ключ — ошибка «auth»")

# --- Битрикс24 ------------------------------------------------------------------
битрикс_вызовы: list[tuple[str, dict]] = []


def битрикс(запрос: httpx.Request) -> httpx.Response:
    if запрос.url.host == "files.bitrix.test":
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=ЗВУК)
    метод = запрос.url.path.rsplit("/", 1)[-1].removesuffix(".json")
    тело = json.loads(запрос.content or b"{}")
    битрикс_вызовы.append((метод, тело))
    if "/rest/1/GOODKEY/" not in запрос.url.path:
        return httpx.Response(401, json={"error": "INVALID_CREDENTIALS", "error_description": "нет"})
    if метод == "voximplant.statistic.get":
        if тело.get("start", 0) == 0:
            return httpx.Response(200, json={"result": [
                {"ID": "1", "CALL_ID": "A1", "CALL_START_DATE": "2026-10-03T14:22:05+03:00", "CALL_DURATION": "75",
                 "CALL_TYPE": "2", "PHONE_NUMBER": "+79161234567", "PORTAL_USER_ID": "7", "RECORD_FILE_ID": "55"},
                {"ID": "2", "CALL_ID": "A2", "CALL_START_DATE": "2026-10-03T14:25:00+03:00", "CALL_DURATION": "30",
                 "CALL_TYPE": "1", "PHONE_NUMBER": "+79160000000", "PORTAL_USER_ID": "7", "RECORD_FILE_ID": ""},
            ], "next": 50})
        return httpx.Response(200, json={"result": [
            {"ID": "3", "CALL_ID": "A3", "CALL_START_DATE": "2026-10-03T15:00:00+03:00", "CALL_DURATION": "12",
             "CALL_TYPE": "1", "PHONE_NUMBER": "89031112233", "PORTAL_USER_ID": "8", "RECORD_FILE_ID": "56"},
        ]})
    if метод == "user.get":
        return httpx.Response(200, json={"result": [{"NAME": "Мария", "LAST_NAME": "Котова"}]})
    if метод == "disk.file.get":
        return httpx.Response(200, json={"result": {"NAME": "call_55.mp3",
                                                    "DOWNLOAD_URL": "https://files.bitrix.test/d/55?auth=x"}})
    return httpx.Response(404, json={"error": "ERROR_METHOD_NOT_FOUND"})


б = сеть.БитриксИсточник("https://x.bitrix24.ru/rest/1/GOODKEY/", httpx.Client(transport=httpx.MockTransport(битрикс)))
записи = б.новые(time.time() - 86400, time.time())
проверить([з.id for з in записи] == ["A1", "A3"], "Битрикс24: страницы пролистаны, звонок без файла записи отложен")
проверить(записи[0].сотрудник == "Мария Котова" and записи[0].направление == "in" and записи[1].направление == "out",
          "Битрикс24: сотрудник по PORTAL_USER_ID, направление по CALL_TYPE")
проверить(sum(1 for м_, _ in битрикс_вызовы if м_ == "user.get") == 2,
          "Битрикс24: каждый сотрудник спрашивается один раз, а не на каждый звонок")
with tempfile.TemporaryDirectory() as tmp:
    путь = б.скачать(записи[0], Path(tmp))
    проверить(путь.name == "call_55.mp3" and путь.read_bytes() == ЗВУК, "Битрикс24: запись с диска портала")
try:
    сеть.БитриксИсточник("https://x.bitrix24.ru/rest/1/BAD/", httpx.Client(transport=httpx.MockTransport(битрикс))).проверить()
    проверить(False, "Битрикс24: плохой вебхук")
except сеть.ОшибкаИсточника as беда:
    проверить(беда.code == "auth", "Битрикс24: неверный вебхук — ошибка «auth»")

# --- SFTP: настоящий сервер на paramiko ------------------------------------------
import paramiko  # noqa: E402


def поднять_sftp(корень: Path) -> tuple[int, threading.Event]:
    ключ = paramiko.RSAKey.generate(2048)

    class Сервер(paramiko.ServerInterface):
        def check_auth_password(self, user, password):
            return paramiko.AUTH_SUCCESSFUL if (user, password) == ("ats", "pa$$") else paramiko.AUTH_FAILED

        def get_allowed_auths(self, user):
            return "password"

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED

    class Файлы(paramiko.SFTPServerInterface):
        def _п(self, путь):
            return корень / путь.lstrip("/")

        def list_folder(self, путь):
            итог = []
            for p in self._п(путь).iterdir():
                а = paramiko.SFTPAttributes.from_stat(p.stat())
                а.filename = p.name
                итог.append(а)
            return итог

        def stat(self, путь):
            return paramiko.SFTPAttributes.from_stat(self._п(путь).stat())

        lstat = stat

        def open(self, путь, flags, attr):
            h = paramiko.SFTPHandle(flags)
            h.readfile = open(self._п(путь), "rb")
            h.filename = str(self._п(путь))
            return h

    гнездо = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    гнездо.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    гнездо.bind(("127.0.0.1", 0))
    гнездо.listen(10)
    стоп = threading.Event()

    def принимать():
        гнездо.settimeout(0.5)
        while not стоп.is_set():
            try:
                соединение, _ = гнездо.accept()
            except OSError:
                continue
            т = paramiko.Transport(соединение)
            т.add_server_key(ключ)
            т.set_subsystem_handler("sftp", paramiko.SFTPServer, Файлы)
            т.start_server(server=Сервер())
        гнездо.close()

    threading.Thread(target=принимать, daemon=True).start()
    return гнездо.getsockname()[1], стоп


with tempfile.TemporaryDirectory() as tmp:
    корень = Path(tmp) / "srv"
    (корень / "rec" / "2026-10").mkdir(parents=True)
    (корень / "rec" / "2026-10" / "out-79161234567-20261003-142205.wav").write_bytes(b"RIFF" + os.urandom(40000))
    (корень / "rec" / "readme.txt").write_text("x")
    порт, стоп = поднять_sftp(корень)
    try:
        с = сеть.SftpИсточник("127.0.0.1", порт, "ats", "pa$$", "/rec")
        отпечаток = с.проверить()
        проверить(len(отпечаток) == 64, "SFTP: подключение по паролю, отпечаток ключа сервера запомнен")
        записи = с.новые()
        проверить([з.имя for з in записи] == ["out-79161234567-20261003-142205.wav"],
                  "SFTP: звуковые файлы найдены в подкаталогах, текст не берётся")
        путь = с.скачать(записи[0], Path(tmp) / "down")
        проверить(путь.read_bytes() == (корень / "rec" / "2026-10" / записи[0].имя).read_bytes(), "SFTP: файл скачан целиком")
        try:
            сеть.SftpИсточник("127.0.0.1", порт, "ats", "неверно", "/rec").проверить()
            проверить(False, "SFTP: неверный пароль")
        except сеть.ОшибкаИсточника as беда:
            проверить(беда.code == "auth", "SFTP: неверный пароль — ошибка «auth»")
        try:
            сеть.SftpИсточник("127.0.0.1", порт, "ats", "pa$$", "/rec", отпечаток_сервера="00" * 32).проверить()
            проверить(False, "SFTP: чужой ключ сервера")
        except сеть.ОшибкаИсточника as беда:
            проверить(беда.code == "host_key", "SFTP: сменился ключ сервера — отказ, как при подмене")
    finally:
        стоп.set()

# --- Сторож: сетевой обход ------------------------------------------------------
class Телефония:
    def __init__(self):
        self.окна: list[tuple[float, float]] = []
        self.записи: list[сеть.Запись] = []
        self.не_готовы: set[str] = set()

    def новые(self, с=None, до=None):
        self.окна.append((с, до))
        return list(self.записи)

    def скачать(self, запись, куда: Path):
        if запись.id in self.не_готовы:
            raise сеть.ОшибкаИсточника("not_ready")
        куда.mkdir(parents=True, exist_ok=True)
        путь = куда / f"{запись.id}.mp3"
        путь.write_bytes(запись.данные.get("байты", ЗВУК))
        return путь


with tempfile.TemporaryDirectory() as tmp:
    взято: dict = {}
    импорт: list = []
    курсоры: dict = {}
    проба = {"кончилась": False}
    т = Телефония()
    сейчас = time.time()
    курсоры["f1"] = сейчас - 600

    def импорт_(путь, папка, имя, когда):
        if проба["кончилась"]:
            return "trial"
        импорт.append((имя, когда))
        return "ok"

    сторож = ист.СторожПапок(
        lambda: [{"id": "f1", "source": "Mango Office", "source_kind": "mango"}],
        lambda п, с: (п, с) in взято, lambda п, с, путь: взято.__setitem__((п, с), путь), импорт_,
        lambda: "Звонок", сеть=lambda папка: (т, курсоры.get("f1")),
        сдвинуть_курсор=lambda п, к: курсоры.__setitem__(п, к), папка_данных=Path(tmp),
        интервалы_сети={"mango": 0},
    )
    т.записи = [сеть.Запись(id="E1", когда=сейчас - 300, номер="+7 916 123-45-67", сотрудник="Анна"),
                сеть.Запись(id="E2", когда=сейчас - 200, номер="+7 903 111-22-33",
                            данные={"байты": ЗВУК + b"x"})]
    т.не_готовы = {"E2"}
    сторож.обойти()
    проверить(abs(т.окна[0][0] - (сейчас - 600 - ист.НАХЛЁСТ)) < 1, "окно телефонии заходит на два часа назад")
    проверить(импорт == [("Звонок +7 916 123-45-67 · Анна", сейчас - 300)], "встреча называется по номеру и сотруднику")
    проверить(abs(курсоры["f1"] - (сейчас - 200)) < 1, "курсор не уходит дальше записи, которая ещё не готова")
    т.не_готовы = set()
    сторож.обойти()
    проверить(len(импорт) == 2, "запись, готовая со второго раза, взята")
    сторож.обойти()
    проверить(len(импорт) == 2, "повторный обход ничего не берёт заново")
    т.записи.append(сеть.Запись(id="E1-дубль", когда=сейчас - 100))
    сторож.обойти()
    проверить(len(импорт) == 2, "та же запись под другим id не берётся: отпечаток по содержимому")
    проба["кончилась"] = True
    т.записи.append(сеть.Запись(id="E9", когда=сейчас - 50, данные={"байты": ЗВУК + b"new"}))
    сторож.обойти()
    проверить(сторож.состояние("f1").get("trial") and ("f1", "remote:E9") not in взято,
              "после пробы запись не помечена взятой и ждёт лицензии")
    проба["кончилась"] = False
    сторож.обойти()
    проверить(len(импорт) == 3, "с лицензией отложенная запись приходит")

    class Сломан:
        def новые(self, с=None, до=None):
            raise сеть.ОшибкаИсточника("auth")
    сторож._сеть = lambda папка: (Сломан(), None)
    сторож.обойти()
    проверить(сторож.состояние("f1").get("error") == "auth", "ключ отозвали — папка показывает ошибку входа")

# --- Сервис: секреты зашифрованы и не уходят в окно ------------------------------
from app.core import service as service_mod  # noqa: E402
from app.core import settings as settings_mod  # noqa: E402
from app.storage import Store  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    база = Store(str(Path(tmp) / "k.db"))
    папка = база.create_folder("Звонки")
    сервис = service_mod.AppService.__new__(service_mod.AppService)
    сервис.store = база
    сервис.settings = settings_mod.Settings()
    сервис._сторож_папок = None
    сервис.запустить_источники = lambda: None

    class Проверяемый:
        def проверить(self):
            return ""

    полученные: list[dict] = []
    настоящий_собрать = сеть.собрать
    сеть.собрать = lambda вид, поля, клиент=None: (полученные.append(dict(поля)), Проверяемый())[1]
    try:
        итог = сервис.folder_set_remote_source(папка["id"], "mango", {"key": "KEY123", "salt": "SALT456"}, "week")
        проверить(итог == {"ok": True}, "Mango подключается к папке")
        сырые = база.list_folders()[0]
        проверить("KEY123" not in сырые["source_config"] and "SALT456" not in сырые["source_config"],
                  "ключ и соль в базе зашифрованы")
        проверить(сырые["source"] == "Mango Office" and сырые["source_kind"] == "mango", "у папки видно «Mango Office»")
        настройки = json.loads(сырые["source_config"])
        проверить(abs(настройки["since"] - (time.time() - 7 * 86400)) < 5, "«за неделю»: курсор неделю назад")
        проверить("source_config" not in сервис.list_folders()[0], "настройки источника не уходят в окно")
        инфо = сервис.folder_source_info(папка["id"])
        проверить(инфо["kind"] == "mango" and sorted(инфо["secrets"]) == ["key", "salt"] and "key" not in инфо["fields"],
                  "окну правки — только отметка, что ключ есть, без самого ключа")
        сервис.folder_source_test("mango", {"key": "", "salt": ""}, папка["id"])
        проверить(полученные[-1] == {"key": "KEY123", "salt": "SALT456"},
                  "пустые поля при правке — прежние ключ и соль, расшифрованные только для проверки")
    finally:
        сеть.собрать = настоящий_собрать
    база.close()

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Сетевые источники работают.")
