// Проверка пути «в системном звуке слышен разговор» от события до окна.
//
// Зачем именно так. В 0.7.0 черновики речи считались правильно и
// отправлялись, но событие не было прописано в списке доходящих до окна,
// и человек не видел ничего. Проверки при этом были зелёными: они звали
// отрисовку напрямую, минуя окно. Здесь гоняем настоящий web/app.js и
// толкаем в него настоящее событие, как это делает Python.
//
// Стережём три отдельные беды:
//   1. Событие никуда не подключено — вопрос не появится вовсе.
//   2. Вопрос сбивает состояние записи — кнопка перестанет слушаться
//      посреди живой встречи, как было с предупреждением о тишине.
//   3. Ответ человека не доходит до Python — кнопки нарисованы, но
//      ничего не делают.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

// Переводы строк приводим к одному виду: на Windows git отдаёт файлы с
// CRLF, и проверки, которые ищут в коде куски с '\n', падают на здоровом
// коде. Ровно на этом однажды упала соседняя проверка кнопки языка.
const код = fs.readFileSync(path.join(__dirname, 'web', 'app.js'), 'utf8')
  .replace(/\r\n/g, '\n');

function узел() {
  const себя = {
    hidden: false,
    textContent: '',
    value: '',
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

// Что окно попросило у Python. Ради этого всё и затевалось: вопрос без
// работающего ответа бесполезен.
const вызовы = [];

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
        вызовы.push(String(имя));
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
// state и ui объявлены через const и наружу сами не выходят. Достаём их
// хвостовой строкой, а не копией: проверять надо то самое состояние,
// которым живёт настоящее окно.
vm.runInContext(код + '\nglobalThis.__state = state; globalThis.__ui = ui;',
                песочница, { filename: 'app.js' });
песочница.state = песочница.__state;
песочница.ui = песочница.__ui;

песочница.__узел = узел;
vm.runInContext(
  "for (const и of ['record','recLabel','levels','timer','levelMe'," +
  "'levelThem','toast','toastText','foreignSpeech','foreignSpeechText'," +
  "'foreignSpeechOff','foreignSpeechKeep']) ui[и] = globalThis.__узел();",
  песочница
);

const событие = песочница.window.__konspekt_event;
if (typeof событие !== 'function') {
  throw new Error('app.js не выставил приёмник событий');
}

песочница.state.isRecording = true;
песочница.state.recordingId = 'встреча-1';
песочница.state.startedAt = Date.now();

const сообщения = [];
песочница.showToast = (текст) => сообщения.push(текст);

// --- Вопрос появляется ---------------------------------------------------
if (!песочница.ui.foreignSpeech.hidden) {
  песочница.ui.foreignSpeech.hidden = true;   // исходное состояние
}
событие({
  topic: 'recording.foreign_speech',
  track: 'them',
  признаки: { есть_речь: true, доля_звука: 0.5, переключений: 6.0 },
});

if (песочница.ui.foreignSpeech.hidden) {
  throw new Error(
    'вопрос про системный звук не показан: событие никуда не подключено, ' +
    'и человек снова узнает о чужом ролике только по испорченному саммари'
  );
}
if (!песочница.ui.foreignSpeechText.textContent) {
  throw new Error('вопрос показан пустым: человеку нечего прочитать');
}
console.log('[ok] вопрос про системный звук показан');

// --- Запись не сбита -----------------------------------------------------
if (!песочница.state.isRecording) {
  throw new Error(
    'вопрос сбил состояние записи: кнопка перестанет слушаться, а ' +
    'визуализация звука пропадёт посреди живой встречи'
  );
}
if (песочница.state.recordingId !== 'встреча-1') {
  throw new Error('вопрос потерял текущую запись');
}
console.log('[ok] вопрос не трогает состояние записи');

// --- Ответ «не писать» доходит до Python ---------------------------------
(async () => {
  await песочница.onForeignSpeechOff();
  if (!вызовы.includes('turn_off_system_audio')) {
    throw new Error(
      'ответ «не писать» не дошёл до Python: кнопка нарисована, но ничего ' +
      'не делает, и чужой звук продолжает уезжать в расшифровку'
    );
  }
  if (!песочница.ui.foreignSpeech.hidden) {
    throw new Error('вопрос остался висеть после ответа человека');
  }
  if (песочница.state.isRecording !== true) {
    throw new Error(
      'выключение системного звука остановило всю запись: человек ответил ' +
      'честно, а потерял встречу'
    );
  }
  console.log('[ok] ответ «не писать» дошёл до Python, запись продолжается');

  // --- Ответ «это собеседник» ---------------------------------------------
  вызовы.length = 0;
  песочница.ui.foreignSpeech.hidden = false;
  await песочница.onForeignSpeechKeep();
  if (!вызовы.includes('keep_system_audio')) {
    throw new Error(
      'ответ «это собеседник» не дошёл до Python: вопрос задастся снова ' +
      'на той же встрече, а такие уведомления закрывают не читая'
    );
  }
  if (!песочница.ui.foreignSpeech.hidden) {
    throw new Error('вопрос остался висеть после ответа «это собеседник»');
  }
  console.log('[ok] ответ «это собеседник» дошёл до Python');

  // --- Конец записи снимает вопрос ----------------------------------------
  песочница.ui.foreignSpeech.hidden = false;
  событие({
    topic: 'recording.stopped',
    meeting_id: 'встреча-1',
    audio_path: null,
  });
  if (!песочница.ui.foreignSpeech.hidden) {
    throw new Error(
      'после остановки записи вопрос остался висеть: он предлагает ' +
      'выключить то, что уже не пишется'
    );
  }
  console.log('[ok] конец записи убирает вопрос');

  console.log('\nПуть «чужая речь в системном звуке» доходит до окна и обратно.');
  песочница.stopTimer();
})().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
