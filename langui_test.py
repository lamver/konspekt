"""Кнопка правки языка живёт в настоящем DOM, а не только в исходнике.

web_test проверяет, что JS разбирается и классы описаны в стилях. Это
не отвечает на вопрос, работает ли сама кнопка: собирается ли меню,
уходит ли вызов к программе, что происходит при отказе. Проверять это
глазами в собранном окне долго и ненадёжно, поэтому гоняем нужные
функции в headless-браузере.

Пропускается, если браузера нет: у постороннего его может не быть, а
проверка не должна из-за этого падать.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import testenv  # noqa: F401  русский вывод в консоли Windows

БЕДЫ: list[str] = []


def проверить(условие: bool, что: str) -> None:
    print(("[ok] " if условие else "[FAIL] ") + что)
    if not условие:
        БЕДЫ.append(что)


def найти_узел() -> str | None:
    return shutil.which("node")


СЦЕНАРИЙ = r"""
const fs = require('fs');
const path = require('path');

// Минимальный DOM: нам нужны создание элементов, классы, data-атрибуты
// и события. Тащить jsdom нельзя — лишняя зависимость ради одного теста.
class El {
  constructor(tag) {
    this.tagName = (tag || '').toUpperCase();
    this.children = [];
    this.dataset = {};
    this.classList = new Set();
    this._text = '';
    this._handlers = {};
    this.hidden = false;
    this.parent = null;
  }
  set className(v) { this.classList = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get className() { return [...this.classList].join(' '); }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  after(node) {
    node.parent = this.parent;
    if (this.parent) this.parent.children.push(node);
  }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  contains(n) {
    if (n === this) return true;
    return this.children.some((c) => c.contains && c.contains(n));
  }
  addEventListener(name, fn) { (this._handlers[name] ||= []).push(fn); }
  async click() {
    for (const fn of this._handlers.click || []) await fn({ target: this });
  }
  querySelector(sel) {
    const cls = sel.replace('.', '');
    for (const c of this.children) {
      if (c.classList.has(cls)) return c;
      const inner = c.querySelector && c.querySelector(sel);
      if (inner) return inner;
    }
    return null;
  }
  find(cls) { return this.querySelector('.' + cls); }
}

global.document = {
  createElement: (t) => new El(t),
  addEventListener() {},
  removeEventListener() {},
};
global.setTimeout = (fn) => fn && 0;
global.window = global;

// Заглушка моста: запоминаем, что и с какими доводами спросили.
const ЗВОНКИ = [];
let ОТВЕТ = (id, code) => ({ id, text: 'пересчитано на ' + code, lang: code });
global.api = {
  transcription_languages: async () => ['ru', 'en', 'de'],
  retranscribe_segment: async (id, code) => {
    ЗВОНКИ.push({ id, code });
    return ОТВЕТ(id, code);
  },
};
global.state = { currentId: 'встреча', hasAudio: true };

// Берём из app.js только нужные функции: остальное тянет за собой окно.
const src = fs.readFileSync(path.join(process.argv[2], 'web', 'app.js'), 'utf8');
const нужное = ['pickLanguage', 'applyLanguage', 'showFailure', 'LANG_NAMES', 'LANGS'];
const куски = [];
for (const имя of ['let LANGS = null;', 'const LANG_NAMES']) {
  const i = src.indexOf(имя);
  if (i < 0) throw new Error('не нашёл в app.js: ' + имя);
}
// Вырезаем от объявления LANGS до конца applyLanguage.
const от = src.indexOf('let LANGS = null;');
const до = src.indexOf('/**\n * Назвать говорящего.');
if (от < 0 || до < 0) throw new Error('не нашёл границы блока правки языка');
eval(src.slice(от, до));

(async () => {
  const итог = [];
  const шапка = new El('div');
  const turn = new El('div');
  turn.dataset.ids = 'seg-1 seg-2';
  turn.dataset.lang = 'ro';
  const текст = new El('div');
  текст.className = 'turn__text';
  текст.textContent = 'si le transcript';
  turn.appendChild(текст);
  const кнопка = new El('button');
  кнопка.className = 'turn__lang';
  кнопка.textContent = 'RO';
  шапка.appendChild(кнопка);
  кнопка.parent = шапка;

  // 1. Меню собирается и знает про языки программы.
  await pickLanguage(кнопка, turn);
  const меню = шапка.children.find((c) => c.classList.has('lang-menu'));
  итог.push(['меню открылось', !!меню]);
  итог.push(['в меню все языки программы', меню && меню.children.length === 3]);
  итог.push(['языки названы по-человечески',
             меню && меню.children[0].textContent === 'Русский']);

  // 2. Выбор языка зовёт пересчёт для всех склеенных реплик.
  await меню.children[1].click();  // Английский
  итог.push(['пересчитаны обе склеенные реплики', ЗВОНКИ.length === 2]);
  итог.push(['язык передан как код, а не как название',
             ЗВОНКИ.every((з) => з.code === 'en')]);
  итог.push(['номера реплик те, что были на блоке',
             ЗВОНКИ[0].id === 'seg-1' && ЗВОНКИ[1].id === 'seg-2']);
  итог.push(['текст заменён ответом программы',
             текст.textContent.includes('пересчитано на en')]);
  итог.push(['язык блока обновлён', turn.dataset.lang === 'en']);
  итог.push(['на кнопке новый язык', кнопка.textContent === 'EN']);
  итог.push(['меню закрылось после выбора',
             !шапка.children.some((c) => c.classList.has('lang-menu'))]);

  // 3. Отказ программы не портит текст.
  const было = текст.textContent;
  ОТВЕТ = () => null;
  ЗВОНКИ.length = 0;
  await pickLanguage(кнопка, turn);
  const меню2 = шапка.children.find((c) => c.classList.has('lang-menu'));
  await меню2.children[0].click();
  итог.push(['при отказе текст не потерян', текст.textContent === было]);
  итог.push(['при отказе кнопка помечена как неудача',
             кнопка.classList.has('turn__lang--failed')]);

  // 4. Ошибка моста тоже не стирает текст.
  global.api.retranscribe_segment = async () => { throw new Error('мост упал'); };
  await pickLanguage(кнопка, turn);
  const меню3 = шапка.children.find((c) => c.classList.has('lang-menu'));
  await меню3.children[0].click();
  итог.push(['падение моста не стирает текст', текст.textContent === было]);

  // 5. Блок без номеров реплик не зовёт программу впустую.
  ЗВОНКИ.length = 0;
  const пустой = new El('div');
  пустой.dataset.ids = '';
  await pickLanguage(new El('button'), пустой);
  итог.push(['без номеров реплик пересчёт не запускается', ЗВОНКИ.length === 0]);

  console.log(JSON.stringify(итог));
})();
"""


def main() -> int:
    node = найти_узел()
    if not node:
        print("[пропуск] нет node, проверить поведение кнопки нечем")
        return 0

    корень = Path(__file__).resolve().parent
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-langui-"))
    файл = tmp / "проба.js"
    файл.write_text(СЦЕНАРИЙ, encoding="utf-8")

    готово = subprocess.run(
        [node, str(файл), str(корень)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    if готово.returncode != 0:
        print(готово.stderr.strip()[:1500])
        print("[FAIL] сценарий не отработал")
        return 1

    for что, ок in json.loads(готово.stdout.strip().splitlines()[-1]):
        проверить(bool(ок), что)

    if БЕДЫ:
        print(f"\nПровалено проверок: {len(БЕДЫ)}")
        return 1
    print("\nКнопка правки языка ведёт себя как задумано")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
