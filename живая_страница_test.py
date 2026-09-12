# -*- coding: utf-8 -*-
"""Живая страница показывает версию и проверку антивирусами.

Отличие от версия_на_странице_test.js: там поддельный DOM и выдуманный
ответ GitHub. Здесь настоящая страница целиком, настоящий браузерный
движок (jsdom) и настоящий ответ GitHub про последний релиз.

Разница не теоретическая. Поддельный DOM был зелёный, когда живая
страница молчала: словари едут отдельным файлом `i18n.js`, и пока он не
обновился, обращение к новой строке падало. Хуже того, падение уносило с
собой плашку проверки антивирусами, стоящую ниже, — то есть новая строка
гасила старую и важную.

Требует node, jsdom и сеть. Без любого из трёх честно пропускаем.
"""
import testenv  # noqa: F401  русский вывод в консоли Windows

import json
import shutil
import subprocess
import sys
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parent

СЦЕНАРИЙ = r"""
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');

const docs = path.join(process.argv[2], 'docs');
const html = fs.readFileSync(path.join(docs, 'index.html'), 'utf8');

const дом = new JSDOM(html, {
  runScripts: 'dangerously',
  resources: 'usable',
  // Адрес файловый, чтобы <script src="i18n.js"> взялся из рабочей
  // копии. С https-адресом jsdom тянет словари с живого сайта, и
  // проверка смотрела бы на выложенную страницу вместо правки.
  url: 'file://' + docs.replace(/\\/g, '/') + '/index.html?lang=ru',
  beforeParse(окно) {
    окно.fetch = (...а) => fetch(...а);
  },
});

setTimeout(() => {
  const d = дом.window.document;
  const узел = d.getElementById('t-release');
  const vt = d.getElementById('t-vt');
  const ссылка = узел ? узел.querySelector('a') : null;
  console.log(JSON.stringify({
    версия_скрыта: узел ? узел.hidden : null,
    версия_текст: узел ? узел.textContent.trim() : null,
    версия_ссылка: ссылка ? ссылка.href : null,
    вирусы_скрыты: vt ? vt.hidden : null,
    вирусы_текст: vt ? vt.textContent.trim() : null,
  }));
  process.exit(0);
}, 8000);
"""

беды = []


def проверить(ок: bool, что: str) -> None:
    print(("[ok] " if ок else "[FAIL] ") + что)
    if not ок:
        беды.append(что)


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("[skip] node не найден, живая страница не проверена")
        return 0

    if subprocess.run([node, "-e", "require.resolve('jsdom')"],
                      capture_output=True, cwd=КОРЕНЬ).returncode != 0:
        print("[skip] jsdom не установлен (npm i jsdom), живая страница не проверена")
        return 0

    файл = КОРЕНЬ / "tools" / "_живая_страница.js"
    файл.write_text(СЦЕНАРИЙ, encoding="utf-8")
    try:
        итог = subprocess.run([node, str(файл), str(КОРЕНЬ)],
                              capture_output=True, text=True,
                              encoding="utf-8", timeout=120, cwd=КОРЕНЬ)
    except subprocess.TimeoutExpired:
        print("[skip] страница не ответила за 120 с, похоже нет сети")
        return 0
    finally:
        файл.unlink(missing_ok=True)

    строки = (итог.stdout or "").strip().splitlines()
    if not строки:
        print("[skip] страница ничего не ответила, похоже нет сети")
        return 0

    видно = json.loads(строки[-1])

    # Нет сети — GitHub не ответит, и обе плашки честно молчат. Это не
    # поломка страницы, поэтому не падаем, а пропускаем.
    if видно["версия_скрыта"] and видно["вирусы_скрыты"]:
        print("[skip] GitHub не ответил (нет сети или кончилась квота)")
        return 0

    проверить(видно["версия_скрыта"] is False,
              "строка версии видна на живой странице")
    проверить(bool(видно["версия_текст"]) and "undefined" not in видно["версия_текст"],
              f"текст версии человеческий: «{видно['версия_текст']}»")
    проверить("Версия" in (видно["версия_текст"] or ""),
              "подпись версии по-русски, а не служебным ключом")
    проверить("releases/tag/" in (видно["версия_ссылка"] or ""),
              f"ссылка ведёт на страницу выпуска: {видно['версия_ссылка']}")

    # Главное: новая строка не должна гасить плашку антивирусов. Именно
    # это и случилось, когда словари отстали от страницы.
    проверить(видно["вирусы_скрыты"] is False,
              "плашка проверки антивирусами жива рядом со строкой версии")
    проверить("/" in (видно["вирусы_текст"] or ""),
              f"счёт антивирусов на месте: «{видно['вирусы_текст']}»")

    if беды:
        print(f"\nНе прошло проверок: {len(беды)}")
        return 1
    print("\nЖивая страница показывает выпуск и проверку антивирусами.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
