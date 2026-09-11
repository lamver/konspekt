// Плашка «идёт разговор, записать?» и настройки внимания в окне.
//
// Зачем именно так. Событие может считаться в Python правильно, но не
// быть подключено к окну — и человек не увидит ничего. Ровно это уже
// случалось с черновиками речи в 0.7.0. Поэтому гоняем настоящий
// web/app.js и толкаем в него настоящее событие, как это делает Python.
//
// Стережём четыре беды:
//   1. Событие никуда не подключено — предложение не появится вовсе.
//   2. Кнопка «Записать» нарисована, но ничего не делает.
//   3. Две плашки лезут в один угол и закрывают друг друга.
//   4. Галочки в настройках не доезжают до Python или не читаются
//      обратно: человек снял слежку, а она работает.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

// Переводы строк приводим к одному виду: на Windows git отдаёт файлы с
// CRLF, и проверки, которые ищут в коде куски с '\n', падают на здоровом
// коде.
const код = fs.readFileSync(path.join(__dirname, 'web', 'app.js'), 'utf8')
  .replace(/\r\n/g, '\n');

function узел() {
  const себя = {
    hidden: false,
    textContent: '',
    value: '',
    checked: false,
    style: {},
    dataset: {},
    children: [],
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    addEventListener(имя, обработчик) { себя.__обработчики[имя] = обработчик; },
    removeEventListener() {},
    appendChild() {},
    setAttribute() {},
    removeAttribute() {},
    querySelector: () => узел(),
    querySelectorAll: () => [],
    getBoundingClientRect: () => ({ width: 0, height: 0, top: 0, left: 0 }),
    focus() {},
    scrollIntoView() {},
    remove() {},
    __обработчики: {},
  };
  return себя;
}

// Что окно попросило у Python и с чем. Ради этого всё и затевалось:
// предложение без работающего ответа бесполезно.
const вызовы = [];

// Что «Python» отвечает на запрос настроек. Подменяется по ходу.
let настройки = { 'сторож': true, 'уведомления': true, 'работает': true, 'проблема': null };

const песочница = {
  console,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  document: {
    getElementById: () => узел(),
    createElement: () => узел(),
    querySelector: () => узел(),
    querySelectorAll: () => [],
    body: узел(),
    documentElement: узел(),
    addEventListener() {},
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { userAgent: 'test' },
  fetch: async () => ({ ok: true, json: async () => ({}) }),
  pywebview: {
    api: new Proxy({}, {
      get: (_t, имя) => async (...арг) => {
        вызовы.push({ имя: String(имя), арг });
        if (имя === 'attention_settings') return настройки;
        if (имя === 'save_attention_settings') {
          настройки = Object.assign({}, настройки, арг[0] || {});
          return настройки;
        }
        if (имя === 'record_noticed_talk') return { ok: true, meeting: { id: 'встреча-2' } };
        return { ok: true };
      },
    }),
  },
};
песочница.addEventListener = () => {};
песочница.removeEventListener = () => {};
песочница.matchMedia = () => ({ matches: false, addEventListener() {} });
песочница.window = песочница;
песочница.globalThis = песочница;

vm.createContext(песочница);
vm.runInContext(код + '\nglobalThis.__state = state; globalThis.__ui = ui;',
                песочница, { filename: 'app.js' });
песочница.state = песочница.__state;
песочница.ui = песочница.__ui;

песочница.__узел = узел;
vm.runInContext(
  "for (const и of ['record','recLabel','levels','timer','levelMe'," +
  "'levelThem','toast','toastText','foreignSpeech','foreignSpeechText'," +
  "'foreignSpeechOff','foreignSpeechKeep','noticedTalk','noticedTalkText'," +
  "'noticedTalkRec','noticedTalkSkip','attentionWatch','attentionToasts'," +
  "'attentionProblem']) ui[и] = globalThis.__узел();",
  песочница
);

const событие = песочница.window.__konspekt_event;
if (typeof событие !== 'function') {
  throw new Error('app.js не выставил приёмник событий');
}

// Настоящий словарь, а не заглушка: половина смысла проверки в том, что
// человек прочитает осмысленный текст, а не ключ вроде «noticed.ask».
// Заодно ловится забытый перевод.
const словарь = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'web', 'i18n', 'ru.json'), 'utf8')
);
vm.runInContext('i18n.dict = globalThis.__словарь; i18n.lang = "ru";',
                Object.assign(песочница, { __словарь: словарь }));

const сообщения = [];
песочница.showToast = (текст) => сообщения.push(текст);

// --- Предложение появляется ----------------------------------------------
песочница.state.isRecording = false;
песочница.state.recordingId = null;
песочница.ui.noticedTalk.hidden = true;

событие({
  topic: 'system.speech_noticed',
  'секунд': 30,
  'признаки': { 'есть_речь': true, 'доля_звука': 0.5 },
});

if (песочница.ui.noticedTalk.hidden) {
  throw new Error(
    'предложение записать не показано: событие не подключено к окну, и ' +
    'человек снова потеряет встречу, о которой забыл нажать кнопку'
  );
}
if (!песочница.ui.noticedTalkText.textContent) {
  throw new Error('предложение показано пустым: человеку нечего прочитать');
}
if (!песочница.ui.noticedTalkText.textContent.includes('30')) {
  throw new Error(
    'в предложении не сказано, сколько уже идёт разговор: человеку нечем ' +
    'решить, та ли это встреча, о которой он думает'
  );
}
console.log('[ok] предложение записать показано, с длительностью разговора');

// --- Плашки не лезут друг на друга ---------------------------------------
песочница.ui.foreignSpeech.hidden = false;
событие({
  topic: 'system.speech_noticed',
  'секунд': 30,
  'признаки': {},
});
if (!песочница.ui.foreignSpeech.hidden) {
  throw new Error(
    'две плашки показаны разом: они лезут в один угол и закрывают друг ' +
    'друга, человек прочитает половину вопроса'
  );
}
console.log('[ok] новая плашка убирает прежнюю: они не наезжают');

(async () => {
  // --- Кнопка «Записать» доходит до Python -------------------------------
  вызовы.length = 0;
  песочница.ui.noticedTalk.hidden = false;
  await песочница.onNoticedTalkRecord();
  if (!вызовы.some((в) => в.имя === 'record_noticed_talk')) {
    throw new Error(
      'кнопка «Записать» не дошла до Python: нарисована, но ничего не ' +
      'делает, и встреча так и не запишется'
    );
  }
  if (!песочница.ui.noticedTalk.hidden) {
    throw new Error('предложение осталось висеть после ответа человека');
  }
  console.log('[ok] кнопка «Записать» дошла до Python');

  // --- Кнопка «Не нужно» -------------------------------------------------
  вызовы.length = 0;
  песочница.ui.noticedTalk.hidden = false;
  await песочница.onNoticedTalkSkip();
  if (!вызовы.some((в) => в.имя === 'ignore_noticed_talk')) {
    throw new Error(
      'отказ не дошёл до Python: программа переспросит через полминуты, а ' +
      'навязчивые уведомления закрывают не читая'
    );
  }
  if (!песочница.ui.noticedTalk.hidden) {
    throw new Error('предложение осталось висеть после отказа');
  }
  console.log('[ok] кнопка «Не нужно» дошла до Python');

  // --- Начало записи снимает предложение ---------------------------------
  песочница.ui.noticedTalk.hidden = false;
  событие({ topic: 'recording.started', meeting_id: 'встреча-3', started_at: null });
  if (!песочница.ui.noticedTalk.hidden) {
    throw new Error(
      'после начала записи предложение осталось висеть: оно предлагает ' +
      'начать то, что уже началось'
    );
  }
  console.log('[ok] начало записи убирает предложение');

  // --- Галочки читаются из Python ----------------------------------------
  настройки = { 'сторож': false, 'уведомления': true, 'работает': false, 'проблема': null };
  await песочница.loadAttention();
  if (песочница.ui.attentionWatch.checked !== false) {
    throw new Error(
      'галочка слежки не читается из настроек: человек выключил её, а в ' +
      'окне она снова стоит'
    );
  }
  if (песочница.ui.attentionToasts.checked !== true) {
    throw new Error('галочка уведомлений не читается из настроек');
  }
  // Слежка выключена самим человеком, значит жаловаться не на что.
  if (!песочница.ui.attentionProblem.hidden) {
    throw new Error(
      'выключенная слежка показана как поломка: человек сам её снял, и ' +
      'ругаться тут не на что'
    );
  }
  console.log('[ok] галочки читаются из настроек');

  // --- Занятое устройство: галочка стоит, а слежки нет -------------------
  настройки = { 'сторож': true, 'уведомления': true, 'работает': false,
                'проблема': 'устройство занято' };
  await песочница.loadAttention();
  if (песочница.ui.attentionProblem.hidden) {
    throw new Error(
      'галочка стоит, слежка не работает, а окно молчит: человек ' +
      'понадеется на программу, которая его не подстрахует'
    );
  }
  if (!песочница.ui.attentionProblem.textContent.includes('занято')) {
    throw new Error('причина поломки слежки не показана человеку');
  }
  console.log('[ok] про неработающую слежку сказано честно, с причиной');

  // --- Галочки доезжают до Python ----------------------------------------
  вызовы.length = 0;
  песочница.ui.attentionWatch.checked = false;
  песочница.ui.attentionToasts.checked = false;
  await песочница.saveAttention();
  const сохранение = вызовы.find((в) => в.имя === 'save_attention_settings');
  if (!сохранение) {
    throw new Error(
      'снятые галочки не доехали до Python: человек выключил слежку, а она ' +
      'продолжает лезть с вопросами'
    );
  }
  if (сохранение.арг[0]['сторож'] !== false
      || сохранение.арг[0]['уведомления'] !== false) {
    throw new Error(
      'до Python доехали не те значения: ' + JSON.stringify(сохранение.арг[0])
    );
  }
  console.log('[ok] снятые галочки доезжают до Python');

  console.log('\nПредложение записать доходит до окна, а настройки — до Python.');
  песочница.stopTimer();
})().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
