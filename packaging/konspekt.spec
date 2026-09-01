# Сборка Konspekt.
#
# Папкой, а не одним файлом: onefile распаковывает 200 МБ во временный
# каталог при каждом запуске, это несколько секунд ожидания на пустом месте
# и типичный повод для антивируса заинтересоваться. Папку потом упаковывает
# установщик.
#
# Веса моделей внутрь не кладём: они качаются при первом обращении и живут
# в данных пользователя. Иначе дистрибутив вырастет с 120 МБ до 700 МБ,
# причём большая часть этого человеку может не понадобиться никогда.
#
# Про другие системы. Собирать под них пока нечем: PyInstaller не умеет
# кросс-сборку, каждой нужна своя машина, и это работа для CI. Но всё, что
# зависит от системы, собрано здесь в ветки по `sys.platform`, а не
# размазано по файлу. Отдельные spec-файлы заводить не стоит: они
# разъезжаются молча.
#
# macOS: сверх этих веток нужен BUNDLE в конце (он уже написан). Без
# нотариата приложение просто не запустится у чужого человека, а это
# 99 долларов в год, поэтому очередь за Windows.
#
# Linux: движок окна там WebKitGTK, и он системный, внутрь дистрибутива не
# кладётся. Значит либо зависимость пакета, либо AppImage. Трей и горячие
# клавиши под Wayland работают иначе, чем под X11, это надо будет проверять
# живьём, а не надеяться.

import os
import re
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    copy_metadata,
)

# Обычная сборка идёт без консоли, но тогда упавший запуск не оставляет
# никаких следов. KONSPEKT_CONSOLE=1 собирает то же самое с консолью,
# чтобы увидеть traceback.
CONSOLE = os.environ.get("KONSPEKT_CONSOLE") == "1"

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "web"), "web"),
    # Указание авторства моделей: CC BY 4.0 у WeSpeaker требует, чтобы
    # оно ехало вместе с программой, а не только лежало в репозитории.
    (str(ROOT / "NOTICE"), "."),
]


# Движок llama.cpp здесь сознательно не перечислен. PyInstaller разбирает
# все .dll из datas как свои и раскладывает их копии в корень сборки: так
# 31 МБ движка превращались в 140 МБ дубликатов. Его копирует build.py
# после сборки, целой папкой.

# onnx_asr держит рядом с кодом описания моделей, без них распознавание
# не поднимется.
datas += collect_data_files("onnx_asr")

# py3langid носит обученную модель отдельным файлом data/model.plzma.
# Метаданные PyInstaller кладёт сам (список зависимостей ниже), а вот сам
# файл модели — нет: он не .py, и без него первая же сверка языка по
# тексту падает уже в установленной программе, а в разработке всё цело.
datas += collect_data_files("py3langid")

# И его метаданные: onnx_asr на первой же строке спрашивает свою версию
# через importlib.metadata. Без папки .dist-info импорт падает с
# PackageNotFoundError, распознавание не поднимается вовсе, и в готовой
# сборке это выглядит как молчащая программа. В разработке беды не видно:
# там метаданные лежат в окружении.
#
# Перечислять библиотеки руками — та же ловушка с другим именем: новая
# зависимость молча останется без метаданных, и узнаем мы об этом снова
# от пользователя. Поэтому список берётся из pyproject.toml, а спрашивать
# свою версию умеет слишком много библиотек, чтобы гадать, какая начнёт.
_ЗАВИСИМОСТИ = tomllib.loads(
    (ROOT / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["dependencies"]

for _строка in _ЗАВИСИМОСТИ:
    # "onnx-asr[cpu,hub]>=0.12.0" -> "onnx-asr"
    _имя = re.split(r"[\[<>=!~;\s]", _строка, maxsplit=1)[0]
    try:
        datas += copy_metadata(_имя)
    except Exception as _беда:
        # Не молчим: без метаданных сборка может оказаться нерабочей,
        # и это должно быть видно в журнале сборки.
        print(f"ВНИМАНИЕ: нет метаданных для {_имя}: {_беда}")

binaries = []
# soundcard и av носят свои нативные библиотеки, автоматически они не
# находятся: обе обращаются к ним через cffi и ctypes.
for package in ("soundcard", "av", "onnxruntime"):
    try:
        binaries += collect_dynamic_libs(package)
    except Exception:
        pass

# Реализация окна, трея и горячих клавиш выбирается во время работы, поэтому
# статически её не видно и импорты приходится называть руками.
hiddenimports = ["webview"]
if WINDOWS:
    hiddenimports += [
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "pystray._win32",
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "clr_loader",
    ]
elif MACOS:
    hiddenimports += [
        "webview.platforms.cocoa",
        "pystray._darwin",
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
    ]
else:
    hiddenimports += [
        "webview.platforms.gtk",
        "pystray._xorg",
        "pynput.keyboard._xorg",
        "pynput.mouse._xorg",
    ]

icon = ROOT / "packaging" / ("konspekt.icns" if MACOS else "konspekt.ico")

a = Analysis(
    # Не app/__main__.py: PyInstaller запускает указанный файл как скрипт,
    # без пакета вокруг, и относительные импорты внутри него падают.
    # Подробности в самом launcher.py.
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Тяжёлые библиотеки, которые тянутся транзитивно и нам не нужны.
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "torch", "fontTools"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Konspekt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX резко повышает шанс ложного срабатывания антивируса
    console=CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon) if icon.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Konspekt",
)

if MACOS:
    # Без Info.plist macOS не спросит разрешение на микрофон, а молча
    # выдаст приложению тишину: NSMicrophoneUsageDescription обязателен.
    app = BUNDLE(
        coll,
        name="Konspekt.app",
        icon=str(icon) if icon.exists() else None,
        bundle_identifier="com.konspekt.app",
        info_plist={
            "NSMicrophoneUsageDescription": "Konspekt записывает встречу с микрофона, чтобы расшифровать её и собрать заметки.",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
