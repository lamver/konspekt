"""Отчёт о проблеме не выносит из дома лишнего.

Отчёт уходит на публичный GitHub, и человек доверяет кнопке «приложить
журнал». В журнале лежат пути с именем пользователя, имена файлов,
иногда почты и токены. Если чистка ослабнет, утечку заметят не тесты,
а посторонние на странице задачи.

Заодно сторожим бюджет адреса: Windows режет длинный адрес молча, а
кириллица в нём весит девять байт на букву.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

from dataclasses import dataclass, field
from pathlib import Path

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

отчёт = ос.собрать_отчёт("bug", "Падает при старте записи", "@valera", True, settings, {"licensed": True}, журнал)
print(отчёт)

for секрет in (settings.license_key, settings.llm.api_key, settings.llm.base_url,
               "123456789:AAFake", "ivan.petrov@example.com", "345-67-89", дом,
               "зарплату Иванова"):
    проверить(секрет not in отчёт, f"в отчёте нет «{секрет[:30]}»")
проверить("Петровым" in отчёт or "~" in отчёт, "путь заменён на ~, имя файла осталось для разбора")
проверить("Версия: " in отчёт and "Лицензия: есть" in отчёт, "сведения о системе на русском")
проверить("remote, gpt-4o" in отчёт, "у удалённой модели видно только имя")
проверить("Связь: @valera" in отчёт, "контакт попал в отчёт")
проверить(all(len(с) <= ос.МАКС_СТРОКА + 1 for с in отчёт.splitlines()), "длинные строки обрезаны")

без = ос.собрать_отчёт("idea", "Хочу тёмную тему по расписанию", "", False, settings, None, журнал)
проверить("<details>" not in без, "без галочки ни сведений, ни журнала")
проверить(без.startswith("## Идея"), "вид идёт заголовком")

settings.language = "es"
проверить(ос.собрать_отчёт("bug", "No arranca", "", False, settings, None, []).startswith("## Error"),
          "заголовок на языке интерфейса")

длинный = "Очень длинное описание ошибки с подробностями " * 60
адрес = ос.ссылка_github("bug", длинный, "ru")
проверить(len(адрес) <= ос.БЮДЖЕТ_АДРЕСА, f"адрес в бюджете: {len(адрес)} знаков")
проверить(адрес.startswith("https://github.com/lamver/konspekt/issues/new?title="), "адрес новой задачи")
проверить("%D0%9E%D1%88%D0%B8%D0%B1%D0%BA%D0%B0" in адрес, "в заголовке «Ошибка» по-русски")
короткий = ос.ссылка_github("idea", "Короткая идея", "en")
проверить("Short" not in короткий and "Idea" in __import__("urllib.parse").parse.unquote(короткий),
          "короткий текст идёт целиком, без многоточия")
проверить("…" not in __import__("urllib.parse").parse.unquote(короткий).split("&body=")[1].split("\n")[0],
          "у короткого текста нет обрезки")

строка = ос.очистить_строку(f"файл {дом}/Documents/a.txt и {дом.replace(chr(92), '/')}/b.txt")
проверить(дом not in строка and "~/Documents" in строка.replace("\\", "/"), "дом заменён в обоих написаниях")

raise SystemExit(1 if ошибки else 0)
