# -*- coding: utf-8 -*-
"""Ключ лицензии: подделку не пропускаем, настоящий ключ не теряем.

Две беды, от которых это сторожит, и обе тихие.

Первая: проверка подписи пропускает подделку. Тогда ключ генерирует
любой, и продажа лицензий теряет смысл, а мы узнаём об этом последними.

Вторая, обратная и хуже для людей: настоящий ключ из письма не
принимается. Человек заплатил, вставил ключ, а плашка «Купить» висит.
Поэтому проверяем и то, что ключ переживает копирование из PDF и
письма (переносы строк, пробелы, невидимые символы).

Подпись у нас своя, на чистом питоне, поэтому сверяем её с эталонными
векторами RFC 8032 и с Go, на котором будет написан сервер продаж.
"""
from __future__ import annotations

import testenv  # noqa: F401  русский вывод и песочница вместо боевого профиля

import base64
import datetime as dt
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core import ed25519
from app.core import license as lic

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ок] " if ок else "[БЕДА] ") + что)
    if not ок:
        БЕДЫ.append(что)


def код_ошибки(ключ: str, **kw) -> str:
    try:
        lic.parse(ключ, **kw)
    except lic.LicenseError as e:
        return e.code
    return "принят"


# --- Ed25519: эталонные векторы RFC 8032, раздел 7.1 ------------------------

ВЕКТОРЫ = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
     "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]

for seed, pub, msg, sig in ВЕКТОРЫ:
    s, p, m, g = (bytes.fromhex(x) for x in (seed, pub, msg, sig))
    проверить(ed25519.public_key(s) == p, f"RFC 8032: открытый ключ {pub[:12]}… выводится из закрытого")
    проверить(ed25519.sign(s, m) == g, f"RFC 8032: подпись сообщения «{msg or 'пусто'}» совпала байт в байт")
    проверить(ed25519.verify(p, m, g), f"RFC 8032: подпись сообщения «{msg or 'пусто'}» принята")
    проверить(not ed25519.verify(p, m + b"!", g), f"RFC 8032: к изменённому сообщению «{msg}!» подпись не подходит")

# Подпись с s + L: та же точка, но другая запись. Без проверки s < L
# одну выданную подпись можно размножить.
s, p, m, g = (bytes.fromhex(x) for x in ВЕКТОРЫ[0])
L = 2**252 + 27742317777372353535851937790883648493
растянутая = g[:32] + int.to_bytes(int.from_bytes(g[32:], "little") + L, 32, "little")
проверить(not ed25519.verify(p, m, растянутая), "подпись с s + L не принимается")
проверить(not ed25519.verify(p, m, g[:63]), "обрезанная подпись не принимается и не роняет программу")
проверить(not ed25519.verify(b"\xff" * 32, m, g), "мусор вместо открытого ключа не роняет программу")

# --- сверка с Go (на нём будет сервер продаж) -------------------------------

go = shutil.which("go")
if not go:
    print("[пропуск] go не найден: сверка с сервером на Go не сделана")
else:
    with tempfile.TemporaryDirectory() as папка:
        (Path(папка) / "go.mod").write_text("module sverka\n\ngo 1.21\n", encoding="utf-8")
        (Path(папка) / "main.go").write_text(
            'package main\n'
            'import ("crypto/ed25519";"encoding/hex";"fmt";"os")\n'
            'func main(){seed,_:=hex.DecodeString(os.Args[1]);k:=ed25519.NewKeyFromSeed(seed)\n'
            'if os.Args[2]=="sign"{fmt.Print(hex.EncodeToString(ed25519.Sign(k,[]byte(os.Args[3]))));return}\n'
            'sig,_:=hex.DecodeString(os.Args[4]);fmt.Print(ed25519.Verify(k.Public().(ed25519.PublicKey),[]byte(os.Args[3]),sig))}\n',
            encoding="utf-8")
        сборка = subprocess.run([go, "build", "-o", "sverka.exe", "."], cwd=папка,
                                capture_output=True, text=True)
        if сборка.returncode != 0:
            проверить(False, f"помощник на Go не собрался: {сборка.stderr[:200]}")
        else:
            exe = str(Path(папка) / "sverka.exe")
            seed = bytes(range(32))
            тело = "KSPK1.eyJ2IjoxfQ"
            от_go = subprocess.run([exe, seed.hex(), "sign", тело], capture_output=True, text=True).stdout
            проверить(от_go == ed25519.sign(seed, тело.encode()).hex(),
                      "подпись Go и наша совпадают байт в байт")
            наша = ed25519.sign(seed, тело.encode()).hex()
            проверил_go = subprocess.run([exe, seed.hex(), "verify", тело, наша], capture_output=True, text=True).stdout
            проверить(проверил_go == "true", "Go принимает нашу подпись")
            проверить(ed25519.verify(ed25519.public_key(seed), тело.encode(), bytes.fromhex(от_go)),
                      "мы принимаем подпись Go")

# --- ключ лицензии -----------------------------------------------------------

СЕКРЕТ = bytes(range(32, 64))
СВОЙ = (ed25519.public_key(СЕКРЕТ),)
ЧУЖОЙ_СЕКРЕТ = bytes(range(64, 96))

ПОЛЯ = {"v": 1, "id": "7f0c", "p": "konspekt", "ed": "personal",
        "to": "Иван Петров", "em": "ivan@example.com", "n": 1, "iat": "2026-09-29"}
ключ = lic.make(СЕКРЕТ, ПОЛЯ)

проверить(ключ.startswith("KSPK1."), "ключ начинается с KSPK1.")
л = lic.parse(ключ, public_keys=СВОЙ)
проверить(л.to == "Иван Петров" and л.email == "ivan@example.com",
          f"настоящий ключ принят, кому выдан прочитано: {л.to}, {л.email}")
проверить(л.expires == "" and л.seats == 1, "без срока ключ бессрочный, мест одно")

# Копирование из PDF и письма.
разорванный = "\n".join(ключ[i:i + 40] for i in range(0, len(ключ), 40))
проверить(код_ошибки("  " + разорванный + " \r\n", public_keys=СВОЙ) == "принят",
          "ключ, разорванный переносами строк и с пробелами по краям, принят")
проверить(код_ошибки(ключ[:20] + "\u200b" + ключ[20:], public_keys=СВОЙ) == "принят",
          "невидимый пробел из PDF внутри ключа не мешает")

# Подделки.
испорченный = ключ[:-3] + ("A" if ключ[-3] != "A" else "B") + ключ[-2:]
проверить(код_ошибки(испорченный, public_keys=СВОЙ) == "signature", "испорченная подпись: signature")

голова, нагрузка, подпись = ключ.split(".")
чужие_поля = dict(ПОЛЯ, n=500)
чужая_нагрузка = base64.urlsafe_b64encode(json.dumps(чужие_поля).encode()).rstrip(b"=").decode()
проверить(код_ошибки(f"{голова}.{чужая_нагрузка}.{подпись}", public_keys=СВОЙ) == "signature",
          "нагрузку подменили (500 мест вместо одного), подпись старая: signature")

чужой = lic.make(ЧУЖОЙ_СЕКРЕТ, ПОЛЯ)
проверить(код_ошибки(чужой, public_keys=СВОЙ) == "signature", "ключ, подписанный чужим закрытым ключом: signature")

другая_программа = ключ.replace("KSPK1.", "XXXX1.", 1)
проверить(код_ошибки(другая_программа, public_keys=СВОЙ) == "format", "чужая приставка: format")
проверить(код_ошибки("", public_keys=СВОЙ) == "empty", "пусто: empty")
проверить(код_ошибки("просто текст", public_keys=СВОЙ) == "format", "произвольный текст: format")
проверить(код_ошибки("KSPK1.!!!.???", public_keys=СВОЙ) in ("format", "signature"),
          "мусор в частях ключа не роняет программу")

проверить(код_ошибки(lic.make(СЕКРЕТ, dict(ПОЛЯ, p="другая")), public_keys=СВОЙ) == "product",
          "подписанный нами ключ другого товара: product")
проверить(код_ошибки(lic.make(СЕКРЕТ, dict(ПОЛЯ, v=2)), public_keys=СВОЙ) == "version",
          "ключ будущего формата: version, а не молчаливое «принят»")

срочный = lic.make(СЕКРЕТ, dict(ПОЛЯ, exp="2026-12-31"))
проверить(код_ошибки(срочный, public_keys=СВОЙ, today=dt.date(2026, 12, 31)) == "принят",
          "срочный ключ в последний день ещё действует")
проверить(код_ошибки(срочный, public_keys=СВОЙ, today=dt.date(2027, 1, 1)) == "expired",
          "срочный ключ на следующий день: expired")

# Настоящий открытый ключ программы: закрытого у проверки нет, поэтому
# убеждаемся хотя бы, что ключ, подписанный не им, отвергается, а сам
# открытый ключ — корректная точка кривой.
проверить(len(lic.PUBLIC_KEYS) >= 1 and all(len(k) == 32 for k in lic.PUBLIC_KEYS),
          "в программе зашит открытый ключ правильной длины")
проверить(all(ed25519._decompress(k) is not None for k in lic.PUBLIC_KEYS),
          "зашитый открытый ключ — настоящая точка кривой, а не заглушка")
проверить(код_ошибки(ключ) == "signature", "ключ от тестовой пары программой не принимается")

# Выданный вручную ключ, если закрытый ключ лежит на этой машине.
seed_file = Path.home() / ".konspekt-license" / "konspekt_personal_v1.seed"
if seed_file.exists():
    настоящий_секрет = bytes.fromhex(seed_file.read_text(encoding="ascii").strip())
    проверить(ed25519.public_key(настоящий_секрет) in lic.PUBLIC_KEYS,
              "закрытый ключ на этой машине подходит к зашитому открытому")
    проверить(код_ошибки(lic.make(настоящий_секрет, ПОЛЯ)) == "принят",
              "ключ, выданный настоящей парой, принимается программой")
else:
    print("[пропуск] закрытого ключа на этой машине нет, настоящая пара не проверена")

# --- сервис: запоминает только подошедший ключ -------------------------------

from app.core import settings as settings_mod  # noqa: E402
from app.core.service import AppService  # noqa: E402

настоящий = None
if seed_file.exists():
    настоящий = lic.make(bytes.fromhex(seed_file.read_text(encoding="ascii").strip()), ПОЛЯ)

сервис = AppService.__new__(AppService)
сервис.settings = settings_mod.Settings()
проверить(сервис.license_state()["licensed"] is False, "без ключа лицензии нет, плашка нужна")
ответ = сервис.activate_license(испорченный)
проверить(not ответ["ok"] and ответ["error"] == "signature" and сервис.settings.license_key == "",
          "испорченный ключ не принят и не записан")
if настоящий:
    ответ = сервис.activate_license("\n" + настоящий[:30] + "\n" + настоящий[30:] + "  ")
    проверить(ответ["ok"] and ответ["licensed"] and сервис.settings.license_key == настоящий,
              "настоящий ключ принят и записан уже без переносов")
    проверить(settings_mod.load().license_key == настоящий, "ключ пережил перезапуск: он в файле настроек")
    ответ = сервис.activate_license("мусор")
    проверить(not ответ["ok"] and сервис.settings.license_key == настоящий,
              "неудачная вставка не затирает рабочий ключ")
    сервис.settings.license_key = настоящий[:-2] + "xx"
    проверить(сервис.license_state()["licensed"] is False,
              "отметку в файле настроек подправили руками: лицензии нет")
    сервис.settings.license_key = настоящий
    проверить(сервис.remove_license()["licensed"] is False and сервис.settings.license_key == "",
              "«Убрать ключ» убирает его")

# --- масштаб: пределы на стороне программы -----------------------------------

проверить(сервис.set_ui_zoom(5) == 2.0, "масштаб больше 200% обрезается до 200%")
проверить(сервис.set_ui_zoom(0.1) == 0.7, "масштаб меньше 70% поднимается до 70%")
проверить(сервис.set_ui_zoom("мусор") == 1.0, "мусор вместо масштаба даёт 100%")
проверить(сервис.set_ui_zoom(float("nan")) == 1.0, "NaN вместо масштаба даёт 100%")
проверить(сервис.set_ui_zoom(1.25) == 1.25 and settings_mod.load().ui_zoom == 1.25,
          "масштаб 125% запомнен в файле настроек")

print()
if БЕДЫ:
    print(f"Бед: {len(БЕДЫ)}")
    raise SystemExit(1)
print("Ключ лицензии проверяется как надо.")
