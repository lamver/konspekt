"""Выдать ключ лицензии Konspekt вручную.

Пока сервер продаж (lamver/ingdg_com#242) не готов, ключ можно выдать
отсюда: первым покупателям, для проверки на живой программе, в подарок.
Тот же формат и та же подпись, что будет у сервера, поэтому такой ключ
не придётся потом менять.

Закрытый ключ читается из файла вне репозитория и в вывод не попадает:

    %USERPROFILE%\\.konspekt-license\\konspekt_personal_v1.seed

Пример:

    python tools/выдать_ключ.py --to "Иван Петров" --email ivan@example.com
    python tools/выдать_ключ.py --to "ООО Ромашка" --email it@romashka.ru --seats 10
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import ed25519, license as license_mod  # noqa: E402

ФАЙЛ = Path.home() / ".konspekt-license" / "konspekt_personal_v1.seed"


def main() -> int:
    разбор = argparse.ArgumentParser(description="Выдать ключ лицензии Konspekt")
    разбор.add_argument("--to", required=True, help="кому: имя или компания")
    разбор.add_argument("--email", required=True, help="почта покупателя")
    разбор.add_argument("--seats", type=int, default=1, help="число мест")
    разбор.add_argument("--edition", default="personal")
    разбор.add_argument("--expires", default="", help="ГГГГ-ММ-ДД, пусто значит бессрочно")
    разбор.add_argument("--seed-file", type=Path, default=ФАЙЛ)
    а = разбор.parse_args()

    seed = bytes.fromhex(а.seed_file.read_text(encoding="ascii").strip())
    if ed25519.public_key(seed) not in license_mod.PUBLIC_KEYS:
        # Ключ, подписанный не той парой, программа отвергнет. Лучше
        # узнать это здесь, чем от покупателя.
        print("Этот закрытый ключ не подходит к открытому в app/core/license.py")
        return 1

    поля = {
        "v": license_mod.FORMAT_VERSION,
        "id": str(uuid.uuid4()),
        "p": license_mod.PRODUCT,
        "ed": а.edition,
        "to": а.to,
        "em": а.email,
        "n": max(1, а.seats),
        "iat": dt.date.today().isoformat(),
    }
    if а.expires:
        dt.date.fromisoformat(а.expires)
        поля["exp"] = а.expires
    ключ = license_mod.make(seed, поля)
    # Сразу проверяем тем же путём, что и программа.
    license_mod.parse(ключ)
    print(ключ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
