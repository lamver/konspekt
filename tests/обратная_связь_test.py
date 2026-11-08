"""Сведения для поддержки не выносят из дома лишнего.

Человек копирует их по просьбе поддержки и вставляет в форму на сайте.
В журнале лежат пути с именем пользователя, имена файлов, иногда почты
и токены. Если чистка ослабнет, утечку заметят не тесты, а тот, кто
читает сообщения с сайта.

Заодно сторожим адрес витрины: в нём только версия, система и язык.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from app import __version__
from app.core import обратная_связь as ос


@dataclass
class Asr:
    backend: str = "gigaam"
    language: str = "ru"
    detect_language: bool = True
    whisper_size: str = "small"


@dataclass
class Llm:
    backend: str = "remote"
    model: str = "gpt-4o"
    base_url: str = "https://secret-proxy.example.net/v1"
    api_key: str = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    local_model: str = "smart"


@dataclass
class Tg:
    enabled: bool = True
    token: str = "plain:MTIzNDU2Nzg5OkFBRmFrZV90b2tlbl93aXRoX3RoaXJ0eV9jaGFyc19sb25nXzAx"


@dataclass
class Settings:
    language: str = "ru"
    license_key: str = "KONSPEKT-SUPER-SECRET-LICENSE-KEY-1234567890"
    asr: Asr = field(default_factory=Asr)
    llm: Llm = field(default_factory=Llm)
    telegram: Tg = field(default_factory=Tg)


ошибки = 0


def проверить(условие, что):
    global ошибки
    print(("[ok]  " if условие else "[FAIL]") + " " + что)
    if not условие:
        ошибки += 1


settings = Settings()
дом = str(Path.home())
журнал = [
    f"12:00:00 INFO    app.core.importer: Импорт {дом}\\Downloads\\встреча с Петровым.mp3 -> встреча abc",
    "12:00:01 INFO    app.core.service: Письмо на ivan.petrov@example.com отправлено, звонил +7 912 345-67-89",
    f"12:00:02 WARNING app.core.license: ключ {settings.license_key} не подошёл",
    f"12:00:03 INFO    app.llm.remote: {settings.llm.base_url} Authorization: Bearer {settings.llm.api_key}",
    "12:00:04 INFO    app.core.telegram_bot: токен 123456789:AAFake_token_with_thirty_chars_long_01 принят",
    "12:00:05 DEBUG   app.asr.vad: чужая речь: «а давайте обсудим зарплату Иванова»",
    "12:00:06 INFO    app.asr.live: " + "очень длинная расшифровка " * 40,
]

чистый = "\n".join(ос.очистить_журнал(журнал, ос.секреты_из_настроек(settings)))
print(чистый)
for секрет in (settings.license_key, settings.llm.api_key, settings.llm.base_url,
               "123456789:AAFake", "ivan.petrov@example.com", "345-67-89", дом,
               "зарплату Иванова"):
    проверить(секрет not in чистый, f"в журнале нет «{секрет[:30]}»")
проверить("Петровым" in чистый, "имя файла осталось для разбора, путь заменён на ~")
проверить(all(len(с) <= ос.МАКС_СТРОКА + 1 for с in чистый.splitlines()), "длинные строки обрезаны")

сведения = "\n".join(ос.сведения_о_системе(settings, {"licensed": True}, "ru"))
print(сведения)
проверить("Версия: " in сведения and "Лицензия: есть" in сведения, "сведения о системе на русском")
проверить("remote, gpt-4o" in сведения, "у удалённой модели видно только имя")
проверить(settings.llm.base_url not in сведения and settings.llm.api_key not in сведения,
          "адреса и ключа модели в сведениях нет")
проверить("Licencia: no" in "\n".join(ос.сведения_о_системе(settings, {"licensed": False}, "es")),
          "сведения на языке интерфейса")

адрес = ос.ссылка_на_витрину("ru")
разбор = urllib.parse.urlparse(адрес)
параметры = dict(urllib.parse.parse_qsl(разбор.query))
проверить(разбор.netloc == "aisearch.ru", f"русский интерфейс ведёт на aisearch.ru: {адрес}")
проверить(параметры.get("v") == __version__ and параметры.get("src") == "app", "в адресе версия и пометка «из программы»")
проверить(set(параметры) == {"v", "os", "lang", "src"}, "в адресе ничего лишнего")
проверить(urllib.parse.urlparse(ос.ссылка_на_витрину("es")).netloc == "aisearch.tech", "остальные языки на aisearch.tech")
проверить(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(ос.ссылка_на_витрину("xx")).query))["lang"] == "en",
          "неизвестный язык становится английским")

строка = ос.очистить_строку(f"файл {дом}/Documents/a.txt и {дом.replace(chr(92), '/')}/b.txt")
проверить(дом not in строка and "~/Documents" in строка.replace("\\", "/"), "дом заменён в обоих написаниях")

raise SystemExit(1 if ошибки else 0)
