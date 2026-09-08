"""Окно во время досчёта пропущенных кусков речи.

Досчёт после «стоп» уже работает: куски, не влезшие в живую очередь,
вырезаются из записи и распознаются заново. Но приходят они из середины
встречи, а окно рисовало любую реплику в конец списка — расшифровка
получалась с перепутанным порядком. Плюс заметки уходили в модель сразу
по «стоп», то есть по той самой дырявой расшифровке, ради починки
которой досчёт и делался.

Проверка гоняет настоящий web/app.js в node с заглушкой DOM: тащить
jsdom ради одного теста — лишняя зависимость.
"""

import testenv  # noqa: F401  русский вывод в консоли Windows

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

БЕДЫ: list[str] = []


def проверить(условие: bool, что: str) -> None:
    if условие:
        print(f"[ок] {что}")
    else:
        print(f"[БЕДА] {что}")
        БЕДЫ.append(что)


СЦЕНАРИЙ = r"""
const fs = require('fs');
const path = require('path');

class El {
  constructor(tag) {
    this.tagName = (tag || '').toUpperCase();
    this.children = [];
    this.classList = new Set();
    this.dataset = {};
    this._text = '';
    this.parent = null;
    this.hidden = false;
    this.scrollTop = 0; this.scrollHeight = 100; this.clientHeight = 100;
  }
  set className(v) { this.classList = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get className() { return [...this.classList].join(' '); }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text; }
  set innerHTML(v) { if (!v) this.children.length = 0; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  insertBefore(c, ref) {
    c.parent = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
    return c;
  }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  get nextElementSibling() {
    if (!this.parent) return null;
    return this.parent.children[this.parent.children.indexOf(this) + 1] || null;
  }
  get previousElementSibling() {
    if (!this.parent) return null;
    return this.parent.children[this.parent.children.indexOf(this) - 1] || null;
  }
  querySelector(sel) {
    const cls = sel.replace('.', '');
    for (const c of this.children) {
      if (c.classList.has(cls)) return c;
      const i = c.querySelector && c.querySelector(sel);
      if (i) return i;
    }
    return null;
  }
  addEventListener() {}
}

global.document = { createElement: (t) => new El(t) };
global.window = global;
const pane = new El('div');
const transcript = new El('div');
pane.appendChild(transcript);
transcript.parentElement = pane;
global.ui = { transcript };
global.updateTranscriptEmpty = () => {};
global.state = { currentId: 'встреча', hasAudio: true };
global.fmtDuration = (s) => String(s);
global.playTurn = () => {};
global.pickLanguage = () => {};
global.renameVoice = () => {};
global.ICON_PLAY = '';
global.TURN_GAP = 0.6;
// Вырезанный кусок app.js зовёт t() (i18n) для подписей «Я»/«Собеседник»
// и подсказок кнопок: сама функция t() объявлена вне среза, подставляем
// простую заглушку с теми же текстами.
global.t = (key) => ({
  'transcript.speaker_me': 'Я',
  'transcript.speaker_them': 'Собеседник',
  'transcript.rename_tooltip': 'Нажмите, чтобы назвать говорящего',
  'transcript.play_tooltip': 'Переслушать фразу',
  'transcript.lang_tooltip': 'Фраза распознана не на том языке?',
  'transcript.lang_placeholder': 'ЯЗ',
}[key] || key);

const src = fs.readFileSync(path.join(process.argv[2], 'web', 'app.js'), 'utf8');
const строки = src.split('\n');
const вырезать = (от, до) => {
  const a = src.indexOf(от);
  const b = src.indexOf(до);
  if (a < 0 || b < 0) throw new Error('не нашёл блок: ' + от);
  return src.slice(a, b);
};
eval(вырезать('function findTurnAfter(', '// Языки, на которых можно пересчитать'));

const итог = [];
const тексты = () => transcript.children.map(
  (c) => c.querySelector('.turn__text').textContent);

// Живая встреча: реплики идут по порядку, как и всегда.
appendSegment({ id: '1', speaker: 'me', text: 'первая', start: 0, end: 1, voice_id: 'a' });
appendSegment({ id: '2', speaker: 'them', text: 'вторая', start: 10, end: 11, voice_id: 'b' });
appendSegment({ id: '3', speaker: 'me', text: 'третья', start: 20, end: 21, voice_id: 'a' });
итог.push(['живые реплики идут по порядку',
           тексты().join('|') === 'первая|вторая|третья']);

// Досчитанный кусок из середины встречи: место по времени, а не конец.
appendSegment({ id: '4', speaker: 'them', text: 'досчитанная', start: 15, end: 16, voice_id: 'b' });
итог.push(['досчитанный кусок встал на своё место по времени',
           тексты().join('|') === 'первая|вторая|досчитанная|третья']);

// И не приклеился к чужому блоку: между ним и соседями секунды паузы.
итог.push(['досчитанный кусок не слился с соседней репликой',
           transcript.children.length === 4]);

// Досчитанный кусок в самом начале встречи.
appendSegment({ id: '5', speaker: 'me', text: 'нулевая', start: -0.5, end: 0, voice_id: 'c' });
итог.push(['кусок из начала встречи встал первым',
           тексты()[0] === 'нулевая']);

// Склейка подряд идущей речи не сломалась: пауза меньше TURN_GAP.
const было = transcript.children.length;
appendSegment({ id: '6', speaker: 'me', text: 'хвост', start: 21.2, end: 22, voice_id: 'a' });
итог.push(['подряд идущая речь по-прежнему склеивается в один блок',
           transcript.children.length === было &&
           тексты().slice(-1)[0] === 'третья хвост']);

// --- заметки ждут конца досчёта --------------------------------------
const кусок = (маркер, сколько) => {
  const i = строки.findIndex((s) => s.includes(маркер));
  return i < 0 ? '' : строки.slice(i, i + сколько).join('\n');
};
const наСтопе = кусок("case 'recording.stopped':", 40);
итог.push(['по «стоп» с досчётом заметки откладываются, а не пускаются сразу',
           /payload\.backfill/.test(наСтопе) &&
           /pendingSummaryId/.test(наСтопе)]);
const обработчик = кусок('function onBackfill(', 30);
итог.push(['по концу досчёта отложенные заметки всё-таки запускаются',
           /maybeAutoSummary\(/.test(обработчик)]);
итог.push(['событие досчёта доходит до окна',
           /case 'recognition\.backfill':/.test(src)]);

console.log(JSON.stringify(итог));
"""


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("[пропуск] node не найден, поведение окна не проверено")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-backfill-ui-"))
    ф = tmp / "проба.js"
    ф.write_text(СЦЕНАРИЙ, encoding="utf-8")
    r = subprocess.run([node, str(ф), str(Path(__file__).resolve().parent)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    if r.returncode != 0:
        print(r.stderr.strip()[:2000])
        проверить(False, "сценарий интерфейса отработал")
    else:
        for что, ок in json.loads(r.stdout.strip().splitlines()[-1]):
            проверить(bool(ок), что)

    if БЕДЫ:
        print(f"\nПровалено проверок: {len(БЕДЫ)}")
        return 1
    print("\nДосчитанные куски встают на своё место, заметки ждут их")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
