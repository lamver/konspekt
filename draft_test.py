"""Речь видно, пока человек говорит.

Главный риск тут не в том, что черновик не покажется, а в том, что он
попадёт куда не следует: в базу, в поиск, в саммари, или останется
висеть на экране вместо настоящей реплики. Проверяем именно это.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

import testenv  # noqa: F401
from app.asr.queue import TranscriptionQueue
from app.asr.vad import PEEK_EVERY, PEEK_MIN, SpeechSegmenter
from app.core.models import TranscriptSegment

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ok] " if ок else "[FAIL] ") + что)
    if not ок:
        БЕДЫ.append(что)


def речь(секунд: float, sr: int = 16000) -> np.ndarray:
    """Похожий на голос сигнал: тон с шумом, заведомо громче фона."""
    t = np.arange(int(sr * секунд)) / sr
    сигнал = 0.25 * np.sin(2 * np.pi * 180 * t)
    сигнал += 0.05 * np.random.RandomState(1).randn(len(t)).astype(np.float32)
    return сигнал.astype(np.float32)


def тишина(секунд: float, sr: int = 16000) -> np.ndarray:
    return np.zeros(int(sr * секунд), dtype=np.float32)


# --- VAD ------------------------------------------------------------------

def проверить_vad() -> None:
    sr = 16000
    seg = SpeechSegmenter(sample_rate=sr)
    звук = речь(6.0, sr)
    шаг = int(sr * 0.5)

    черновики = []
    проверить_длину: list[tuple[float, int]] = []
    время = 0.0
    for i in range(0, len(звук), шаг):
        время += 0.5
        seg.feed(звук[i:i + шаг], время - 0.5)
        п = seg.peek()
        if п is not None:
            черновики.append((время, len(п.pcm) / sr))

    проверить(bool(черновики), "во время речи черновики появляются")
    if черновики:
        первый = черновики[0][0]
        проверить(первый <= PEEK_MIN + 0.6,
                  f"первый черновик быстро, а не через полшага ({первый:.1f}с)")
        проверить(len(черновики) >= 4,
                  f"черновик обновляется по ходу речи ({len(черновики)} раз)")
        растёт = all(b[1] >= a[1] - 0.01 for a, b in zip(черновики, черновики[1:]))
        проверить(растёт, "каждый следующий черновик длиннее предыдущего")

    # Настоящая встреча — это фразы вперемешку с паузами, а не один
    # монолог. После каждой готовой фразы буфер обрезается, и счётчик
    # показов, считающий кадры буфера, начинает врать: черновики редеют
    # или пропадают вовсе. На одиночной фразе это незаметно.
    длинный = SpeechSegmenter(sample_rate=sr)
    поток = np.concatenate([
        np.concatenate([речь(4.0, sr), тишина(1.2, sr)]) for _ in range(4)
    ])
    по_фразам: list[list[float]] = []
    текущая: list[float] = []
    говорил = False
    метка = 0.0
    for i in range(0, len(поток) - шаг, шаг):
        метка += 0.5
        длинный.feed(поток[i:i + шаг], метка - 0.5)
        if длинный._in_speech:
            if not говорил:
                говорил = True
                текущая = []
            п = длинный.peek()
            if п is not None:
                текущая.append(метка)
                # Черновик не должен быть длиннее, чем человек говорит:
                # запас PAD прихватывает тишину до начала фразы, и модель
                # принимает её за конец, дописывая точку посреди слова.
                проверить_длину.append((len(п.pcm) / sr, len(текущая)))
        elif говорил:
            говорил = False
            по_фразам.append(текущая)

    с_черновиком = [ф for ф in по_фразам if ф]
    проверить(len(с_черновиком) == len(по_фразам),
              f"черновики есть у каждой фразы встречи, не только у первой "
              f"({len(с_черновиком)} из {len(по_фразам)})")
    if по_фразам:
        показов = [len(ф) for ф in по_фразам]
        проверить(min(показов) >= 3,
                  f"черновики не редеют к концу встречи (минимум {min(показов)} на фразу)")

    # Длина черновика: ровно сказанное, без запаса тишины по краям.
    # PAD прихватывает 0.15с до начала фразы — там ещё молчание, и
    # модель принимает его за конец, дописывая точку посреди слова.
    ровный = SpeechSegmenter(sample_rate=sr)
    речь_идёт = np.concatenate([тишина(1.0, sr), речь(5.0, sr)])
    начало = None
    хвосты = []
    метка = 0.0
    for i in range(0, len(речь_идёт) - шаг, шаг):
        метка += 0.5
        ровный.feed(речь_идёт[i:i + шаг], метка - 0.5)
        if ровный._in_speech and начало is None:
            начало = метка
        п = ровный.peek()
        if п is not None and начало is not None:
            # Сказано к этому моменту (грубо) против длины черновика.
            сказано = метка - начало + 0.5
            хвосты.append(len(п.pcm) / sr - сказано)
    проверить(bool(хвосты), "есть что мерить по длине черновика")
    if хвосты:
        проверить(max(хвосты) < 0.1,
                  f"черновик не тащит лишнюю тишину по краям (запас {max(хвосты):.2f}с)")

    # Тишина не должна порождать черновиков.
    пустой = SpeechSegmenter(sample_rate=sr)
    т = тишина(4.0, sr)
    без = []
    for i in range(0, len(т), шаг):
        пустой.feed(т[i:i + шаг], i / sr)
        if пустой.peek() is not None:
            без.append(1)
    проверить(not без, "в тишине черновиков нет")

    # Черновик не съедает настоящую фразу.
    seg2 = SpeechSegmenter(sample_rate=sr)
    поток = np.concatenate([речь(3.0, sr), тишина(1.5, sr)])
    готовые = []
    for i in range(0, len(поток), шаг):
        готовые += seg2.feed(поток[i:i + шаг], i / sr)
        seg2.peek()
    проверить(len(готовые) == 1, "после паузы приходит настоящая фраза целиком")
    if готовые:
        проверить(abs(len(готовые[0].pcm) / sr - 3.0) < 0.8,
                  "настоящая фраза не обрезана черновиками")

    # Новая фраза начинает черновики заново.
    seg3 = SpeechSegmenter(sample_rate=sr)
    поток2 = np.concatenate([речь(2.0, sr), тишина(1.5, sr), речь(2.0, sr)])
    первые = []
    метка = 0.0
    for i in range(0, len(поток2), шаг):
        метка += 0.5
        seg3.feed(поток2[i:i + шаг], метка - 0.5)
        п = seg3.peek()
        if п is not None:
            первые.append(len(п.pcm) / sr)
    короткий_после_паузы = any(d < PEEK_MIN + 0.6 for d in первые[1:])
    проверить(короткий_после_паузы, "вторая фраза показывается с начала, а не продолжает первую")


# --- очередь --------------------------------------------------------------

class МодельЗаглушка:
    name = "проба"
    languages = ("ru",)

    def transcribe(self, pcm, sample_rate, meeting_id, offset, speaker, **_):
        return [TranscriptSegment(
            meeting_id=meeting_id, speaker=speaker, text=f"текст {len(pcm)}",
            start=offset, end=offset + len(pcm) / sample_rate,
        )]


def проверить_очередь() -> None:
    настоящие: list[TranscriptSegment] = []
    черновики: list[TranscriptSegment] = []
    опознано: list[str] = []

    class Отпечатки:
        def embed(self, pcm, sr):
            опознано.append("да")
            return np.zeros(8, dtype=np.float32)

    class Список:
        def assign(self, speaker, vector):
            return None

    q = TranscriptionQueue(
        МодельЗаглушка(),
        on_segment=настоящие.append,
        on_draft=черновики.append,
        embedder=Отпечатки(),
        roster=Список(),
    )
    q.start()

    sr = 16000
    шаг = int(sr * 0.5)
    звук = np.concatenate([речь(4.0, sr), тишина(1.5, sr)])
    for i in range(0, len(звук), шаг):
        q.submit("встреча", "me", звук[i:i + шаг], i / sr)
        time.sleep(0.02)
    q.flush("встреча")
    time.sleep(1.5)
    q.stop(timeout=10)

    проверить(bool(черновики), "черновики доходят до интерфейса")
    проверить(bool(настоящие), "настоящие реплики продолжают приходить")
    проверить(len(опознано) == len(настоящие),
              "говорящего ищут только для настоящих реплик, не для черновиков")


# --- интерфейс ------------------------------------------------------------

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
    this.scrollTop = 0; this.scrollHeight = 100; this.clientHeight = 100;
  }
  set className(v) { this.classList = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get className() { return [...this.classList].join(' '); }
  set textContent(v) { this._text = String(v); if (v === '') this.children.length = 0; }
  get textContent() { return this._text; }
  set innerHTML(v) { if (!v) this.children.length = 0; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  remove() { if (this.parent) { this.parent.children = this.parent.children.filter(x => x !== this); this.parent = null; } }
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
// Настоящий узел знает родителя: прокрутка ищет его через parentElement.
transcript.parentElement = pane;
global.ui = { transcript };
global.updateTranscriptEmpty = () => {};
global.state = { currentId: 'встреча' };
// showDraft зовёт t() (i18n) для подписей «Я»/«Собеседник»: сама функция
// объявлена вне вырезанного среза app.js, подставляем заглушку.
global.t = (key) => ({
  'transcript.speaker_me': 'Я',
  'transcript.speaker_them': 'Собеседник',
}[key] || key);

const src = fs.readFileSync(path.join(process.argv[2], 'web', 'app.js'), 'utf8');
const от = src.indexOf('const drafts = new Map();');
const до = src.indexOf('/** Отрисовать транскрипт встречи целиком. */');
if (от < 0 || до < 0) throw new Error('не нашёл блок черновиков');
eval(src.slice(от, до));

(async () => {
  const итог = [];
  const черновых = () => transcript.children.filter(c => c.classList.has('turn--draft')).length;

  showDraft({ speaker: 'me', text: 'привет я', meeting_id: 'встреча' });
  итог.push(['черновик появился на экране', черновых() === 1]);
  итог.push(['черновик помечен как неокончательный',
             transcript.children[0].classList.has('turn--draft')]);

  showDraft({ speaker: 'me', text: 'привет я говорю', meeting_id: 'встреча' });
  итог.push(['черновик обновляется на месте, а не плодит блоки', черновых() === 1]);
  итог.push(['виден свежий текст',
             transcript.children[0].querySelector('.turn__text').textContent === 'привет я говорю']);

  // Вторая дорожка — свой черновик.
  showDraft({ speaker: 'them', text: 'и я', meeting_id: 'встреча' });
  итог.push(['у каждой дорожки свой черновик', черновых() === 2]);

  clearDraft('me');
  итог.push(['готовая реплика убирает черновик своей дорожки', черновых() === 1]);
  итог.push(['чужой черновик при этом остаётся',
             transcript.children[0].querySelector('.turn__text').textContent === 'и я']);

  clearDraft();
  итог.push(['остановка записи убирает все черновики', черновых() === 0]);

  // Дальше проверяем не сами функции, а то, зовёт ли их программа на
  // настоящих событиях. Без этого черновик остаётся висеть рядом с
  // готовой репликой, и человек видит свою фразу дважды.
  const события = src.split('\n');
  const кусок = (маркер, сколько) => {
    const i = события.findIndex((s) => s.includes(маркер));
    return i < 0 ? '' : события.slice(i, i + сколько).join('\n');
  };
  const наГотовой = кусок("case 'transcript.segment':", 10);
  итог.push(['готовая реплика убирает черновик своей дорожки',
             /clearDraft\(\s*payload\.segment\.speaker\s*\)/.test(наГотовой)]);
  const наЧерновике = кусок("case 'transcript.draft':", 10);
  итог.push(['событие черновика доходит до показа',
             /showDraft\(/.test(наЧерновике)]);
  const наСтопе = кусок("case 'recording.stopped':", 12);
  итог.push(['остановка записи снимает черновики',
             /clearDraft\(\s*\)/.test(наСтопе)]);

  // Пустой текст не должен создавать блок.
  showDraft({ speaker: 'me', text: '', meeting_id: 'встреча' });
  итог.push(['пустой черновик не показывается', черновых() === 0]);

  console.log(JSON.stringify(итог));
})();
"""


def проверить_окно() -> None:
    node = shutil.which("node")
    if not node:
        print("[пропуск] нет node")
        return
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-draft-"))
    ф = tmp / "проба.js"
    ф.write_text(СЦЕНАРИЙ, encoding="utf-8")
    r = subprocess.run([node, str(ф), str(Path(__file__).resolve().parent)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    if r.returncode != 0:
        print(r.stderr.strip()[:1200])
        проверить(False, "сценарий интерфейса отработал")
        return
    for что, ок in json.loads(r.stdout.strip().splitlines()[-1]):
        проверить(bool(ок), что)


СЦЕНАРИЙ_КНОПОК = r"""
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
  }
  set className(v) { this.classList = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get className() { return [...this.classList].join(' '); }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  querySelectorAll(sel) {
    const классы = sel.split(',').map((s) => s.trim().replace('.', ''));
    const итог = [];
    const обойти = (узел) => {
      for (const c of узел.children) {
        if (классы.some((cls) => c.classList.has(cls))) итог.push(c);
        обойти(c);
      }
    };
    обойти(this);
    return итог;
  }
  addEventListener() {}
}

global.document = { createElement: (t) => new El(t) };
global.window = global;
const transcript = new El('div');
global.ui = { transcript };
global.state = { currentId: 'встреча', isRecording: true, recordingId: 'встреча' };
global.refreshMeta = () => {};
global.renderRecordingState = () => {};
global.stopTimer = () => {};
global.startTimer = () => {};
global.maybeAutoSummary = () => {};
global.clearDraft = () => {};
global.appendSegment = () => {};
global.showDraft = () => {};
global.setSummaryStatus = () => {};
global.onModelProgress = () => {};
global.showUpdateNote = () => {};
global.onUpdateState = () => {};
global.onSummaryChunk = () => {};
global.onSummaryReady = () => {};
global.renderSummary = () => ({});
global.setBusy = () => {};
global.showToast = () => {};
global.renderMeta = () => {};
global.loadMeetings = () => {};
global.onChatChunk = () => {};
global.onChatMessage = () => {};
global.onChatError = () => {};
global.renderImports = () => {};
global.onImportProgress = () => {};

const src = fs.readFileSync(path.join(process.argv[2], 'web', 'app.js'), 'utf8');
const от = src.indexOf('window.__konspekt_event = function (payload) {');
const до = src.indexOf('/* --- Саммари и чат');
if (от < 0 || до < 0) throw new Error('не нашёл обработчик событий __konspekt_event');
eval(src.slice(от, до));

// Реплика, отрисованная во время записи: кнопки были скрыты, потому что
// в этот момент state.hasAudio ещё не true (has_audio на сервере false,
// пока нет ни одного audio-чанка).
const turn = new El('div');
const play = new El('button');
play.className = 'turn__play';
play.hidden = true;
const lang = new El('button');
lang.className = 'turn__lang';
lang.hidden = true;
turn.appendChild(play);
turn.appendChild(lang);
transcript.appendChild(turn);

(async () => {
  const итог = [];
  await window.__konspekt_event({ topic: 'recording.stopped', meeting_id: 'встреча' });
  итог.push(['кнопка "переслушать" открылась после остановки записи', play.hidden === false]);
  итог.push(['кнопка "язык" открылась после остановки записи', lang.hidden === false]);
  console.log(JSON.stringify(итог));
})();
"""


def проверить_кнопки_после_записи() -> None:
    """Кнопки «переслушать» и «язык» появляются сразу по завершении записи.

    Реплики рисуются по ходу записи, когда has_audio ещё false, и кнопки
    у них создаются скрытыми (иначе они всегда отвечали бы «записи нет»).
    Регресс: recording.stopped выставлял state.hasAudio = true, но не
    трогал уже нарисованные кнопки — они оставались скрытыми до
    переключения на другую встречу и обратно (полный renderTranscript).
    """
    node = shutil.which("node")
    if not node:
        print("[пропуск] нет node")
        return
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-buttons-"))
    ф = tmp / "проба.js"
    ф.write_text(СЦЕНАРИЙ_КНОПОК, encoding="utf-8")
    r = subprocess.run([node, str(ф), str(Path(__file__).resolve().parent)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    if r.returncode != 0:
        print(r.stderr.strip()[:1200])
        проверить(False, "сценарий кнопок после записи отработал")
        return
    for что, ок in json.loads(r.stdout.strip().splitlines()[-1]):
        проверить(bool(ок), что)


def проверить_путь_до_окна() -> None:
    """Событие черновика доходит до окна, а не теряется по дороге.

    В 0.7.0 всё работало по отдельности: VAD отдавал черновики, очередь
    их считала, окно умело рисовать. А списка пересылаемых в окно тем
    событие не знало, и до человека не доходило ничего. Прежние проверки
    звали отрисовку напрямую и этого не видели.
    """
    from app.core import events as ev
    from app.ui import window as win

    проверить(ev.TRANSCRIPT_DRAFT in win.FORWARDED_EVENTS,
              "черновик пересылается в окно, а не теряется в питоне")

    # Каждое событие, которое фронт умеет принимать, должно доезжать.
    скрипт = (Path(__file__).resolve().parent / "web" / "app.js").read_text(encoding="utf-8")
    import re
    ждёт = set(re.findall(r"case '([a-z_]+\.[a-z_]+)':", скрипт))
    шлём = set(win.FORWARDED_EVENTS)
    # Эти окно получает не через общий список, а отдельной подпиской.
    личные = {"app.new_version", "app.update_state", "summary.chunk",
              "summary.ready", "summary.status", "summary.error",
              "chat.chunk", "chat.message", "chat.error", "import.progress"}
    потерянные = sorted(ждёт - шлём - личные)
    проверить(not потерянные,
              f"фронт не ждёт событий, которых ему не шлют: {потерянные}")


def main() -> int:
    проверить_vad()
    проверить_путь_до_окна()
    проверить_очередь()
    проверить_окно()
    проверить_кнопки_после_записи()
    if БЕДЫ:
        print(f"\nПровалено проверок: {len(БЕДЫ)}")
        return 1
    print("\nРечь видно по ходу, а в базу идёт только готовое")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
