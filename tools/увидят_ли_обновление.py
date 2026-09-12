# -*- coding: utf-8 -*-
"""Увидят ли установленные копии новую версию.

Запускать после каждого выпуска, руками. Спрашиваем тем же кодом и с той
же точки, что и человек: из сети, а не из наших файлов. Без этого выпуск
остаётся невидимым — так уже было с 0.9.1, о котором никто не узнал.

Отдельно смотрим тип notes. Словарь переводов понимают копии от 0.8.1 и
новее, а тем, что старше, он достанется как «{'ru': ...}» со скобками.
Таких выпусков семнадцать, и они ещё стоят у людей.

Важно про кэш: raw.githubusercontent отдаёт файл до пяти минут из кэша,
поэтому сразу после записи здесь может быть старое содержимое. Это не
поломка, просто подождите.
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

АДРЕС = "https://raw.githubusercontent.com/lamver/konspekt-releases/master/latest.json"

ответ = httpx.get(АДРЕС, timeout=20, follow_redirects=True)
ответ.raise_for_status()
данные = ответ.json()

print("версия в latest.json:", данные.get("version"))
print("установщик:", (данные.get("url") or "")[-46:])
новость = данные.get("notes")
print("новость:", новость if isinstance(новость, str) else type(новость).__name__)
print("переводы:", list((данные.get("notes_i18n") or {}).keys()))

# Та самая проверка, что и в программе: строка, а не словарь. Старые
# копии ждут строку и на словаре молча ничего не покажут.
assert isinstance(новость, str), (
    f"notes не строка, а {type(новость).__name__}: копии старше 0.8.1 "
    f"не покажут новость о выпуске"
)
assert данные.get("version") == "0.10.0", данные.get("version")
# Ссылка ведёт на «последний релиз», а не на конкретный номер: так она
# не протухает и не требует правки на каждый выпуск.
assert "konspekt-releases/releases" in (данные.get("url") or ""), данные.get("url")

# Установщик должен и правда лежать по этой ссылке.
голова = httpx.head(данные["url"], timeout=20, follow_redirects=True)
print("установщик отвечает:", голова.status_code,
      round(int(голова.headers.get("content-length", 0)) / 1024 / 1024, 1), "МБ")
assert голова.status_code == 200, голова.status_code

print("\n[ok] установленные копии увидят новую версию и смогут её скачать")
