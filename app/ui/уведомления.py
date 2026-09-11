"""Системные уведомления Windows с кнопками.

Зачем. Про чужой звук в системе надо спросить так, чтобы человек
увидел вопрос, не открывая окно: во время встречи Konspekt сидит в
трее. Обычное «облачко» у трея кнопок не имеет вовсе, а тост Windows
имеет — и ответить можно прямо из угла экрана, не бросая разговор.

Почему не через PowerShell. Раньше тост показывался запуском
`powershell -ExecutionPolicy Bypass -Command ...` в скрытом окне. Это
работало, но выглядело ровно так, как ведёт себя закрепившийся в
системе троян: запуск интерпретатора с обходом политики выполнения,
без окна, из чужого процесса. Microsoft Defender в 0.9.0 так и решил —
`Trojan:Win32/Wacatac.C!ml`, а он стоит у каждого пользователя Windows.
Цена ошибки здесь не «одна тревога из 75»: человек просто не сможет
запустить программу.

Поэтому тост показывается напрямую через WinRT из самого процесса,
никаких дочерних интерпретаторов. Библиотека `winrt-Windows.UI.Notifications`
ставится как зависимость: пара сотен килобайт против сломанной
установки у половины людей.

Как возвращается ответ. Кнопка в тосте умеет только одно: запустить
программу с аргументом. Поэтому кнопки пишут ответ в файл, а
работающая копия его подхватывает — тем же способом, каким второй
запуск просит показать окно.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

ОТВЕТ = "toast-answer"          # файл с ответом человека

# XML тоста. `launch` на кнопках — это протокол konspekt:, который
# регистрируется при запуске; если он не зарегистрирован, кнопки
# просто откроют программу, и человек ответит в окне.
ШАБЛОН = (
    '<toast activationType="protocol" launch="{протокол}:show">'
    '<visual><binding template="ToastGeneric">'
    '<text>{заголовок}</text><text>{текст}</text>'
    '</binding></visual>'
    '<actions>'
    '<action content="{кнопка1}" activationType="protocol" arguments="{протокол}:{ответ1}"/>'
    '<action content="{кнопка2}" activationType="protocol" arguments="{протокол}:{ответ2}"/>'
    '</actions></toast>'
)


def _экранировать(текст: str) -> str:
    """XML не терпит угловых скобок и амперсанда в тексте."""
    return (текст.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;"))


def доступны() -> bool:
    """Тосты бывают только на Windows, и только если есть WinRT."""
    if sys.platform != "win32":
        return False
    try:
        import winrt.windows.ui.notifications  # noqa: F401
    except ImportError:
        return False
    return True


def _показать_тост(xml: str, приложение: str) -> None:
    """Отдать готовый XML системе. Зовётся из фонового потока."""
    from winrt.windows.data.xml.dom import XmlDocument
    from winrt.windows.ui.notifications import (
        ToastNotification,
        ToastNotificationManager,
    )

    документ = XmlDocument()
    документ.load_xml(xml)
    ToastNotificationManager.create_toast_notifier(приложение).show(
        ToastNotification(документ)
    )


def показать_вопрос(
    заголовок: str,
    текст: str,
    кнопка1: str,
    ответ1: str,
    кнопка2: str,
    ответ2: str,
    протокол: str = "konspekt",
    приложение: str = "Konspekt",
) -> bool:
    """Показать уведомление с двумя кнопками. Возвращает, удалось ли.

    Работает в фоновом потоке: WinRT отвечает быстро, но держать из-за
    него поток захвата звука всё равно незачем.
    """
    if not доступны():
        return False

    xml = ШАБЛОН.format(
        заголовок=_экранировать(заголовок),
        текст=_экранировать(текст),
        кнопка1=_экранировать(кнопка1),
        кнопка2=_экранировать(кнопка2),
        ответ1=ответ1,
        ответ2=ответ2,
        протокол=протокол,
    )

    def запустить() -> None:
        try:
            _показать_тост(xml, приложение)
        except Exception:
            # Уведомление — вежливость, а не работа программы: его
            # поломка не имеет права мешать записи встречи.
            log.debug("Не удалось показать тост", exc_info=True)

    threading.Thread(target=запустить, name="toast", daemon=True).start()
    return True


def прочитать_ответ(каталог: Path) -> str | None:
    """Забрать ответ человека, оставленный кнопкой тоста."""
    файл = каталог / ОТВЕТ
    try:
        if not файл.exists():
            return None
        значение = файл.read_text(encoding="utf-8").strip()
        файл.unlink(missing_ok=True)
        return значение or None
    except OSError:
        return None


def записать_ответ(каталог: Path, значение: str) -> None:
    """Оставить ответ для работающей копии программы."""
    try:
        (каталог / ОТВЕТ).write_text(значение, encoding="utf-8")
    except OSError:
        log.warning("Не удалось передать ответ работающей копии")


ПРОТОКОЛ = "konspekt"


def зарегистрировать_протокол(протокол: str = ПРОТОКОЛ) -> bool:
    """Научить Windows открывать ссылки `konspekt:` нашей программой.

    Без этого кнопки в уведомлении нажимаются вхолостую: система не
    знает, кому отдать ответ. Пишем в ветку текущего пользователя —
    прав администратора не нужно, и чужим копиям мы не мешаем.

    Зовём при каждом запуске: это дёшево, а иначе после переустановки
    или переезда папки кнопки тихо перестали бы работать.
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False

    # Путь к самой программе. Для собранного .exe это он сам, для
    # запуска из исходников — питон с ключом -m app.
    if getattr(sys, "frozen", False):
        команда = f'"{sys.executable}" "%1"'
    else:
        корень = Path(__file__).resolve().parent.parent.parent
        команда = f'"{sys.executable}" -m app "%1"'
        # Запуск из исходников требует рабочего каталога проекта,
        # иначе `-m app` не найдётся.
        команда = f'cmd /c cd /d "{корень}" && {команда}'

    try:
        ключ = rf"Software\Classes\{протокол}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, ключ) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, f"URL:{протокол}")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              rf"{ключ}\shell\open\command") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, команда)
        return True
    except OSError:
        log.debug("Не удалось зарегистрировать протокол %s", протокол, exc_info=True)
        return False
