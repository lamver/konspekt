"""Указание авторства моделей: файл NOTICE и раздел «О программе».

Лицензия WeSpeaker (CC BY 4.0) требует, чтобы авторство видел
пользователь. Потерять это легко и молча: файл не попадёт в сборку,
кнопку в интерфейсе уберут при правке настроек. Тогда мы нарушаем
лицензию и узнаём об этом от чужого юриста, а не от теста.

Заодно сверяем версию: она живёт в трёх местах, и разъезжается при
каждом выпуске.
"""

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
    assert 'id="about-sheet"' in html, "в разметке нет окна «О программе»"
    assert 'id="about-notice"' in html, "в окне «О программе» негде показать NOTICE"
    assert "notice_text" in js, "фронт не запрашивает NOTICE"
    assert "openAboutSheet" in js, "окно «О программе» не открывается"
    print("[ok] раздел «О программе» есть в интерфейсе")

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
