"""Мутации: ломаем лицензию и масштаб и ждём, что проверки это заметят.

Сломанная проверка лицензии молчит в обе стороны: пропускает
поддельные ключи или не принимает настоящий. Узнаём мы об этом от
покупателя, поэтому каждую поломку пробуем отдельно.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКИ = [КОРЕНЬ / "лицензия_test.py", КОРЕНЬ / "лицензия_окно_test.py",
            КОРЕНЬ / "масштаб_edge_test.py"]

МУТАЦИИ = [
    # --- подпись ---
    ("app/core/ed25519.py",
     "    if s >= _L:\n        return False\n", "",
     "подпись с s + L принимается"),
    ("app/core/ed25519.py",
     "    return _equal(_mul(s, _G), _add(point_r, _mul(h, point_a)))\n",
     "    return True\n",
     "любая подпись верна"),
    ("app/core/ed25519.py",
     "    h = _sha512_int(r_bytes + public + message) % _L\n    return _equal",
     "    h = _sha512_int(r_bytes + public) % _L\n    return _equal",
     "подпись не зависит от сообщения"),
    ("app/core/ed25519.py",
     "    a &= (1 << 254) - 8\n", "",
     "закрытый ключ не обрезается по RFC"),
    ("app/core/ed25519.py",
     "    if (x & 1) != sign:\n        x = _P - x\n", "",
     "знак точки при распаковке теряется"),
    # --- разбор ключа ---
    ("app/core/license.py",
     '    signed = f"{parts[0]}.{parts[1]}".encode("ascii")\n',
     '    signed = parts[1].encode("ascii")\n',
     "приставка KSPK1 не входит в подписанное"),
    ("app/core/license.py",
     "    if not any(ed25519.verify(pub, signed, signature) for pub in keys):\n        raise LicenseError(\"signature\")\n",
     "",
     "подпись вообще не проверяется"),
    ("app/core/license.py",
     '    if data.get("p") != PRODUCT:\n        raise LicenseError("product")\n', "",
     "ключ другого товара подходит"),
    ("app/core/license.py",
     '    if data.get("v") != FORMAT_VERSION:\n        raise LicenseError("version")\n', "",
     "ключ будущего формата принимается молча"),
    ("app/core/license.py",
     "        if (today or dt.date.today()) > until:\n",
     "        if (today or dt.date.today()) >= until:\n",
     "в последний день срочный ключ уже не действует"),
    ("app/core/license.py",
     "        if (today or dt.date.today()) > until:\n            raise LicenseError(\"expired\", expires)\n", "",
     "истёкший ключ действует"),
    ("app/core/license.py",
     '    return "".join(ch for ch in (raw or "") if ch.isprintable() and not ch.isspace())\n',
     '    return (raw or "").strip()\n',
     "ключ с переносами строк из письма не принимается"),
    ("app/core/license.py",
     '    if len(parts) != 3 or parts[0] != PREFIX:\n',
     '    if len(parts) != 3:\n',
     "чужая приставка не отсекается как «не тот формат»"),
    # --- сервис ---
    ("app/core/service.py",
     "            return {\"ok\": False, \"error\": err.code, \"detail\": err.detail}\n        self.settings.license_key = license_mod.clean(key)\n",
     "            self.settings.license_key = key\n            return {\"ok\": False, \"error\": err.code, \"detail\": err.detail}\n        self.settings.license_key = license_mod.clean(key)\n",
     "неудачная вставка затирает рабочий ключ"),
    ("app/core/service.py",
     "        self.settings.license_key = license_mod.clean(key)\n        settings_mod.save(self.settings)\n",
     "        self.settings.license_key = license_mod.clean(key)\n",
     "ключ не сохраняется в файл и теряется при перезапуске"),
    ("app/core/service.py",
     "        try:\n            lic = license_mod.parse(key)\n        except license_mod.LicenseError as err:\n            # Ключ был",
     "        if key:\n            return {\"licensed\": True, \"buy_url\": license_mod.BUY_URL}\n        try:\n            lic = license_mod.parse(key)\n        except license_mod.LicenseError as err:\n            # Ключ был",
     "сохранённый ключ не перепроверяется: хватает любой строки в файле"),
    ("app/core/service.py",
     "        value = round(max(UI_ZOOM_MIN, min(UI_ZOOM_MAX, value)), 2)\n",
     "        value = round(value, 2)\n",
     "масштаб без пределов"),
    ("app/core/service.py",
     "        if value != value:  # NaN из битого файла\n            value = 1.0\n", "",
     "NaN из файла настроек проходит"),
    # --- окно ---
    ("web/app.js",
     "  if (ui.licenseBar) ui.licenseBar.hidden = licensed || !!state.licenseBarHidden;\n",
     "  if (ui.licenseBar) ui.licenseBar.hidden = !!state.licenseBarHidden;\n",
     "плашка висит и у заплатившего"),
    ("web/app.js",
     "  state.license = s || { licensed: true };\n",
     "  state.license = s || { licensed: false };\n",
     "мост не ответил, а плашка просит денег"),
    ("web/app.js",
     "      ui.licenseKey.value = '';\n      showLicenseResult(t('license.activated'), true);\n      state.license = res;\n",
     "      ui.licenseKey.value = '';\n      showLicenseResult(t('license.activated'), true);\n",
     "после ввода ключа плашка остаётся до перезапуска"),
    ("web/app.js",
     "    const text = tЕслиЕсть(`license.error.${code}`, { date: (res && res.detail) || '' });\n",
     "    const text = code;\n",
     "человеку показан код ошибки вместо объяснения"),
    ("web/app.js",
     "  if (!key) {\n    showLicenseResult(t('license.error.empty'), false);\n    ui.licenseKey.focus();\n    return;\n  }\n", "",
     "пустой ключ уходит в программу"),
    ("web/app.js",
     "const ZOOM_STEPS = [0.7, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];\n",
     "const ZOOM_STEPS = [0.7, 0.8, 0.9, 1.1, 1.25, 1.5, 1.75, 2];\n",
     "ступеньками не вернуться ровно к 100%"),
    ("web/app.js",
     "  return Math.round(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, n)) * 100) / 100;\n",
     "  return Math.round(n * 100) / 100;\n",
     "масштаб в окне без пределов"),
    ("web/app.js",
     "  document.documentElement.style.zoom = value === 1 ? '' : String(value);\n",
     "",
     "масштаб считается, но к окну не применяется"),
    ("web/app.js",
     "  clearTimeout(zoomSaveTimer);\n  zoomSaveTimer = setTimeout(() => api.set_ui_zoom(value), 400);\n",
     "  api.set_ui_zoom(value);\n",
     "каждый щелчок колеса пишет файл настроек"),
    ("web/app.js",
     "  if (key === '+' || key === '=' || code === 'Equal' || code === 'NumpadAdd') return 1;\n",
     "  if (key === '+') return 1;\n",
     "Ctrl + плюс без Shift и на русской раскладке не работает"),
    ("web/app.js",
     "  if (!(e.ctrlKey || e.metaKey) || e.altKey) return null;\n",
     "  if (e.altKey) return null;\n",
     "«=» без Ctrl меняет масштаб вместо того, чтобы печататься"),
    ("web/app.js",
     "getBoundingClientRect().left) / currentZoom());\n",
     "getBoundingClientRect().left));\n",
     "под масштабом граница колонки убегает от курсора"),
    ("web/app.js",
     "  document.documentElement.style.zoom = value === 1 ? '' : String(value);\n",
     "  document.documentElement.style.transform = value === 1 ? '' : `scale(${value})`;\n"
     "  document.documentElement.style.zoom = '';\n",
     "масштаб трансформацией: низ окна уезжает за край"),
]


def прогнать() -> int:
    for проверка in ПРОВЕРКИ:
        код = subprocess.run(
            [sys.executable, str(проверка)], cwd=КОРЕНЬ, capture_output=True,
            env={**os.environ, "PYTHONUTF8": "1"},
        ).returncode
        if код != 0:
            return код
    return 0


def main() -> int:
    if прогнать() != 0:
        print("ПЛОХО: проверки падают ещё до мутаций")
        return 1
    поймано = 0
    for файл, было, стало, что in МУТАЦИИ:
        путь = КОРЕНЬ / файл
        исходник = путь.read_bytes()
        текст = исходник.decode("utf-8")
        crlf = "\r\n" in текст
        текст = текст.replace("\r\n", "\n")
        if текст.count(было) != 1:
            print(f"[НЕ ПРИМЕНИЛАСЬ] {что}: кусок найден {текст.count(было)} раз")
            continue
        новое = текст.replace(было, стало)
        if crlf:
            новое = новое.replace("\n", "\r\n")
        путь.write_bytes(новое.encode("utf-8"))
        try:
            упала = прогнать() != 0
        finally:
            путь.write_bytes(исходник)
        if упала:
            поймано += 1
            print(f"[поймана] {что}")
        else:
            print(f"[ПРОПУЩЕНА] {что}")
    print(f"\nПоймано {поймано} из {len(МУТАЦИИ)}")
    return 0 if поймано == len(МУТАЦИИ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
