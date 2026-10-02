"""Ключ лицензии Konspekt: разбор и проверка без интернета.

Ключ выдаёт сервер продаж после оплаты (lamver/ingdg_com#242) и
подписывает закрытым ключом Ed25519. Программа сверяет подпись открытым
ключом, зашитым ниже, и никуда за этим не ходит. Konspekt продаётся
обещанием «ничего не уходит с компьютера», и проверка лицензии через
сеть при каждом запуске это обещание нарушала бы. Заодно ключ работает,
даже если сервер продаж лежит или его больше нет.

Формат, одна строка:

    KSPK1.<нагрузка base64url>.<подпись base64url>

Подписываются байты ASCII строки `KSPK1.<нагрузка base64url>`, то есть
приставка тоже. Ключ другой программы с той же нагрузкой не подойдёт,
даже если когда-нибудь пары ключей совпадут по ошибке.

Нагрузка — JSON: `v` версия формата, `id` номер лицензии, `p` товар,
`ed` редакция, `to` кому выдана, `em` почта, `n` число мест, `iat`
дата выдачи, `exp` дата окончания (нет поля — бессрочная), `upd` последний
день обновлений (нет поля — обновления навсегда).

`upd` — это не срок работы, а срок обновлений (решение 01.10): ключ «на
3 года» подходит всем версиям, собранным до этого дня включительно,
сколько бы лет ни прошло. Сверяется с датой сборки, а не с часами
компьютера, поэтому перевод часов ничего не даёт. Старые версии про `upd`
не знают и считают такой ключ бессрочным, и это верно: все они вышли
раньше конца срока.

Код открыт, и вырезать проверку можно пересборкой. Это сознательно
принято (docs/tasks/openness-and-licensing.md): кто так сделает, тот и
не заплатил бы. Защищаемся не от него, а от подделки ключа, которую
без закрытого ключа не сделать.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

from . import ed25519

PREFIX = "KSPK1"
PRODUCT = "konspekt"
FORMAT_VERSION = 1

# Открытый ключ пары `konspekt_personal_v1`. Закрытый живёт только у
# сервера продаж. Сменить пару значит обесценить все выданные ключи,
# поэтому новая пара добавляется сюда рядом, а не вместо.
PUBLIC_KEYS: tuple[bytes, ...] = (
    bytes.fromhex("3db3240b68f741a5a52e2803d35ea1977355358ddff0ae8a3b7efebb79e90d0c"),
)

# Куда ведёт кнопка «Купить». Метки нужны, чтобы отличить покупки из
# программы от покупок с сайта.
# Прямо на страницу продажи, без переадресации с konspekt.aisearch.ru:
# лишний шаг — лишнее место, где покупка может сломаться (решение 02.10).
BUY_URL = "https://aisearch.ru/pricing/license/konspekt?utm_source=app&utm_medium=banner"

# Пробный период: столько встреч программа работает целиком без лицензии.
# Считаются встречи, а не дни (решение 30.09): человек, который поставил
# программу и неделю ею не пользовался, ничего не потерял, а тот, кто
# записал десять встреч, уже знает, за что платит.
TRIAL_MEETINGS = 10
# Сколько распознанной речи нужно, чтобы встреча пошла в счёт. Случайное
# «Запись» — «Стоп» или минута тишины пробную встречу не съедает.
TRIAL_MIN_SPEECH_S = 60.0


@dataclass(frozen=True)
class License:
    id: str
    to: str
    email: str
    edition: str
    seats: int
    issued: str
    expires: str
    updates: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "to": self.to, "email": self.email,
            "edition": self.edition, "seats": self.seats,
            "issued": self.issued, "expires": self.expires,
            "updates": self.updates,
        }


class LicenseError(ValueError):
    """Ключ не подошёл. `code` уходит в окно и переводится там."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def _b64decode(part: str) -> bytes:
    # base64url без выравнивания: так короче и без «=» в конце, которые
    # при копировании из PDF любят теряться.
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def clean(raw: str) -> str:
    """Убрать всё, что прилипает к ключу при копировании.

    PDF и почтовые программы переносят длинную строку, добавляют
    пробелы и невидимые символы. Внутри ключа их не бывает, поэтому
    выбрасываем любые пробельные и невидимые знаки.
    """
    return "".join(ch for ch in (raw or "") if ch.isprintable() and not ch.isspace())


def build_date() -> dt.date:
    """Когда собрана эта версия. С ней сверяется срок обновлений `upd`.

    Дату пишет packaging/build.py в app/build_info.py. В разработке файла
    нет, и берётся сегодняшний день. Собранная программа без даты — ошибка
    сборки, её ловит самопроверка: иначе срок сверялся бы с часами
    компьютера, и через три года ключ отказал бы на старой версии.
    """
    try:
        from app.build_info import BUILD_DATE
    except ImportError:
        return dt.date.today()
    return dt.date.fromisoformat(BUILD_DATE)


def parse(raw: str, *, today: dt.date | None = None,
          public_keys: tuple[bytes, ...] | None = None,
          built: dt.date | None = None) -> License:
    """Проверить ключ и вернуть, кому он выдан. Не подошёл — LicenseError."""
    key = clean(raw)
    if not key:
        raise LicenseError("empty")
    parts = key.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise LicenseError("format")
    try:
        payload_bytes = _b64decode(parts[1])
        signature = _b64decode(parts[2])
    except (binascii.Error, ValueError):
        raise LicenseError("format") from None

    signed = f"{parts[0]}.{parts[1]}".encode("ascii")
    keys = PUBLIC_KEYS if public_keys is None else public_keys
    if not any(ed25519.verify(pub, signed, signature) for pub in keys):
        raise LicenseError("signature")

    # Разбираем только после проверки подписи: чужому тексту не
    # доверяем даже настолько, чтобы читать его JSON.
    try:
        data = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LicenseError("format") from None
    if not isinstance(data, dict):
        raise LicenseError("format")
    if data.get("v") != FORMAT_VERSION:
        raise LicenseError("version")
    if data.get("p") != PRODUCT:
        raise LicenseError("product")

    expires = str(data.get("exp") or "")
    if expires:
        try:
            until = dt.date.fromisoformat(expires)
        except ValueError:
            raise LicenseError("format") from None
        if (today or dt.date.today()) > until:
            raise LicenseError("expired", expires)

    updates = str(data.get("upd") or "")
    if updates:
        try:
            last = dt.date.fromisoformat(updates)
        except ValueError:
            raise LicenseError("format") from None
        if (built or build_date()) > last:
            raise LicenseError("updates_ended", updates)

    try:
        seats = max(1, int(data.get("n") or 1))
    except (TypeError, ValueError):
        seats = 1
    return License(
        id=str(data.get("id") or ""),
        to=str(data.get("to") or ""),
        email=str(data.get("em") or ""),
        edition=str(data.get("ed") or ""),
        seats=seats,
        issued=str(data.get("iat") or ""),
        expires=expires,
        updates=updates,
    )


def make(seed: bytes, fields: dict[str, Any]) -> str:
    """Выпустить ключ. Программе не нужно: для выдачи вручную и проверок."""
    payload = json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    head = f"{PREFIX}.{body}"
    sig = ed25519.sign(seed, head.encode("ascii"))
    return head + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
