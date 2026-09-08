"""Указание авторства моделей: файл NOTICE и раздел «О программе».

Лицензия WeSpeaker (CC BY 4.0) требует, чтобы авторство видел
пользователь. Потерять это легко и молча: файл не попадёт в сборку,
кнопку в интерфейсе уберут при правке настроек. Тогда мы нарушаем
лицензию и узнаём об этом от чужого юриста, а не от теста.

Заодно сверяем версию: она живёт в трёх местах, и разъезжается при
каждом выпуске.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent

# Модели, авторство которых обязано быть в NOTICE. Ключ — строка поиска,
# значение — зачем она там.
REQUIRED = {
    "WeSpeaker": "CC BY 4.0 требует заметного указания авторства",
    "CC BY 4.0": "название лицензии WeSpeaker",
    "creativecommons.org/licenses/by/4.0": "ссылка на текст лицензии",
    "GigaAM": "распознавание русской речи",
    "Whisper": "распознавание остальных языков",
    "VoxLingua107": "определение языка",
    "Qwen3": "заметки и чат",
}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    for needle, why in REQUIRED.items():
        assert needle in notice, f"в NOTICE нет «{needle}»: {why}"
    print(f"[ok] NOTICE на месте, {len(REQUIRED)} обязательных упоминаний")

    # --- NOTICE едет в сборку -------------------------------------------
    spec = (ROOT / "packaging" / "konspekt.spec").read_text(encoding="utf-8-sig")
    assert '"NOTICE"' in spec, "NOTICE не попадает в дистрибутив: нет в datas у spec"
    print("[ok] NOTICE положен в дистрибутив")

    # --- приложение умеет его отдать -------------------------------------
    from app.ui.api import Api

    api = Api.__new__(Api)
    text = Api.notice_text(api)
    assert "WeSpeaker" in text, "notice_text() не читает файл"
    print(f"[ok] приложение отдаёт NOTICE, {len(text)} символов")

    # --- кнопка и окно в интерфейсе --------------------------------------
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'data-tab="about"' in html, "в настройках нет раздела «О программе»"
    assert 'id="about-notice"' in html, "в разделе «О программе» негде показать NOTICE"
    assert "notice_text" in js, "фронт не запрашивает NOTICE"
    assert "showPrefsTab('about')" in js, "раздел «О программе» не открывается"
    print("[ok] раздел «О программе» есть в интерфейсе")

    # --- проверка обновлений видна и отключается ------------------------
    assert 'id="update-auto"' in html, "нет выключателя проверки обновлений"
    assert 'id="update-check"' in html, "нет кнопки «Проверить сейчас»"
    assert "check_updates_now" in js, "фронт не умеет проверять обновления"
    assert "set_check_updates" in js, "проверку обновлений нельзя выключить"
    api_src = (ROOT / "app" / "ui" / "api.py").read_text(encoding="utf-8")
    for method in ("check_updates_now", "set_check_updates", "get_update_settings"):
        assert f"def {method}" in api_src, f"в мосту нет метода {method}"
    print("[ok] обновления проверяются по кнопке и отключаются")

    # --- разделы настроек не разъехались с обработчиками -----------------
    tabs = set(re.findall(r'class="prefs__tab[^"]*" data-tab="(\w+)"', html))
    pages = set(re.findall(r'class="prefs__page" data-page="(\w+)"', html))
    assert tabs, "не нашлось ни одного раздела настроек"
    assert tabs == pages, f"разделы и страницы настроек разошлись: {tabs ^ pages}"
    titles = set(re.findall(r"^\s+(\w+): \(\) => t\('prefs\.tab\.\w+'\)", js, re.M))
    assert tabs <= titles, f"у разделов нет заголовков: {sorted(tabs - titles)}"
    print(f"[ok] разделов настроек: {len(tabs)}, у всех есть страница и заголовок")

    # --- версия в трёх местах --------------------------------------------
    from app import __version__

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__, (
        f"версия разъехалась: pyproject {pyproject['project']['version']}, "
        f"код {__version__}"
    )

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    released = re.findall(r"^## (\d+\.\d+(?:\.\d+)?)", changelog, re.M)
    assert released, "в ченджлоге нет ни одной выпущенной версии"
    top = released[0]
    assert __version__.startswith(top) or top.startswith(__version__), (
        f"версия {__version__} не совпадает с верхней в ченджлоге ({top})"
    )
    print(f"[ok] версия {__version__} согласована с pyproject и ченджлогом")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
