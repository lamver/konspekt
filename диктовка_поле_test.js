// Диктовка в поле вопроса: сказать быстрее, чем напечатать.
//
// Задача №26, вторая половина: «вопрос к встрече быстрее сказать, чем
// напечатать, особенно длинный. Распознавать уже имеющимся движком,
// вставлять текст в поле, а не отправлять сразу: сказанное надо дать
// поправить перед отправкой».
//
// Отдельно проверяем отказ. Диктовка невозможна, пока идёт запись
// встречи: модель распознавания занята расшифровкой. Мёртвая кнопка без
// объяснения читается как поломка программы.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const код = fs.readFileSync(path.join(__dirname, 'web', 'app.js'), 'utf8');
const словарь = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'web', 'i18n', 'ru.json'), 'utf8')
);

function узел() {
  const себя = {
    hidden: false,
    textContent: '',
    title: '',
    value: '',
    disabled: false,
    style: {},
    dataset: {},
    childNodes: [],
    children: [],
    classList: {
      __набор: new Set(),
      toggle(имя, вкл) { вкл ? this.__набор.add(имя) : this.__набор.delete(имя); },
      add(имя) { this.__набор.add(имя); },
      remove(имя) { this.__набор.delete(имя); },
      contains(имя) { return this.__набор.has(имя); },
    },
    addEventListener(событие, обработчик) {
      (себя.__клики = себя.__клики || {})[событие] = обработчик;
    },
    removeEventListener() {},
    appendChild() {},
    setAttribute() {},
    removeAttribute() {},
    querySelector: () => узел(),
    querySelectorAll: () => [],
    getBoundingClientRect: () => ({ width: 0, height: 0, top: 0, left: 0 }),
    focus() { себя.__фокус = true; },
    setSelectionRange(a, b) { себя.selectionStart = a; себя.selectionEnd = b; },
    scrollIntoView() {},
    remove() {},
  };
  Object.defineProperty(себя, 'innerHTML', {
    get() { return себя.__html || ''; },
    set(v) { себя.__html = v; },
  });
  return себя;
}

const сообщения = [];
// Что программа попросила у питона и что он ответил.
const вызовы = [];
let ответНаСтарт = { ok: true };
let ответНаКонец = { ok: true, 'текст': 'когда мы выкатываем оплату' };

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
  navigator: { userAgent: 'test', clipboard: { writeText: async () => {} } },
  fetch: async () => ({ ok: true, json: async () => ({}) }),
};
песочница.addEventListener = () => {};
песочница.removeEventListener = () => {};
песочница.matchMedia = () => ({ matches: false, addEventListener() {} });
// Мост в питон. Настоящего тут нет, поэтому подсовываем окну поддельный
// pywebview, и до запуска app.js: сам `api` — это Proxy, который ждёт
// появления window.pywebview и без него висит вечно, не падая и не
// проходя. На это я уже попался: проверка молча висела два раза.
песочница.pywebview = {
  api: {
    start_field_dictation: async () => {
      вызовы.push('start');
      return песочница.__ответСтарт;
    },
    finish_field_dictation: async () => {
      вызовы.push('finish');
      return песочница.__ответКонец;
    },
    cancel_field_dictation: async () => {
      вызовы.push('cancel');
      return { ok: true };
    },
  },
};
песочница.window = песочница;
песочница.globalThis = песочница;

vm.createContext(песочница);
vm.runInContext(
  код + '\nglobalThis.__state = state;'
      + '\nglobalThis.__toggle = toggleFieldDictation;'
      + '\nglobalThis.__вставить = вставитьВПоле;',
  песочница, { filename: 'app.js' }
);
песочница.state = песочница.__state;
vm.runInContext('i18n.dict = globalThis.__словарь;', Object.assign(
  песочница, { __словарь: словарь }
));
песочница.showToast = (т) => сообщения.push(т);

песочница.__узел = узел;
vm.runInContext(
  "for (const и of ['chatText','chatMic','chatSend','chatList','chatHint',"
  + "'chatClear','toast','toastText']) ui[и] = globalThis.__узел();",
  песочница
);
// resizeChatInput трогает стиль поля; в поддельном окне этого хватит.
vm.runInContext('ui.chatText.style = {};', песочница);

async function проверить() {
  // --- Диктовка кладёт текст в поле, а не отправляет --------------------
  песочница.__ответСтарт = ответНаСтарт;
  песочница.__ответКонец = ответНаКонец;

  await vm.runInContext('__toggle();', песочница);
  if (!vm.runInContext('state.dictating', песочница)) {
    throw new Error('после нажатия на микрофон диктовка не началась');
  }
  if (!vm.runInContext("ui.chatMic.classList.contains('is-listening')", песочница)) {
    throw new Error(
      'кнопка не показывает, что слушает: человек не поймёт, что можно '
      + 'говорить, и будет ждать'
    );
  }
  console.log('[ok] первое нажатие начинает слушать и это видно на кнопке');

  await vm.runInContext('__toggle();', песочница);
  const вПоле = vm.runInContext('ui.chatText.value', песочница);
  if (вПоле !== 'когда мы выкатываем оплату') {
    throw new Error('продиктованное не попало в поле: ' + JSON.stringify(вПоле));
  }
  if (вызовы.includes('send')) {
    throw new Error(
      'вопрос отправился сразу: человек не успел поправить сказанное, '
      + 'а говорят небрежнее, чем пишут'
    );
  }
  if (vm.runInContext("ui.chatMic.classList.contains('is-listening')", песочница)) {
    throw new Error('кнопка так и осталась в режиме «слушаю»');
  }
  console.log('[ok] текст попадает в поле, а не уходит сразу: ' + вПоле);

  // --- Написанное руками не затирается ----------------------------------
  vm.runInContext(
    "ui.chatText.value = 'что решили по'; ui.chatText.selectionStart = 13;"
    + "ui.chatText.selectionEnd = 13;", песочница
  );
  песочница.__ответКонец = { ok: true, 'текст': 'оплате' };
  await vm.runInContext('__toggle();', песочница);  // начать
  await vm.runInContext('__toggle();', песочница);  // закончить
  const вместе = vm.runInContext('ui.chatText.value', песочница);
  if (!вместе.startsWith('что решили по')) {
    throw new Error(
      'продиктованное затёрло написанное руками: ' + JSON.stringify(вместе)
      + '. Человек мог начать печатать и договорить голосом'
    );
  }
  if (!вместе.includes('оплате')) {
    throw new Error('продиктованное потерялось: ' + вместе);
  }
  if (вместе.includes('пооплате')) {
    throw new Error('слова слиплись без пробела: ' + вместе);
  }
  console.log('[ok] продиктованное дописывается к написанному: ' + вместе);

  // --- Идёт запись встречи: честно объясняем, почему нельзя -------------
  сообщения.length = 0;
  песочница.__ответСтарт = { ok: false, 'причина': 'идёт запись встречи, модель занята' };
  await vm.runInContext('__toggle();', песочница);
  if (vm.runInContext('state.dictating', песочница)) {
    throw new Error('диктовка началась, хотя питон отказал');
  }
  if (!сообщения.length) {
    throw new Error(
      'кнопка ничего не сделала и промолчала: мёртвая кнопка без '
      + 'объяснения читается как поломка программы'
    );
  }
  if (!сообщения[0].includes('идёт запись встречи')) {
    throw new Error(
      'человеку не сказали настоящую причину отказа: ' + сообщения[0]
    );
  }
  console.log('[ok] при записи встречи честно объясняем отказ: ' + сообщения[0]);

  // --- Ничего не расслышали ---------------------------------------------
  сообщения.length = 0;
  песочница.__ответСтарт = { ok: true };
  песочница.__ответКонец = { ok: true, 'текст': '' };
  vm.runInContext("ui.chatText.value = '';", песочница);
  await vm.runInContext('__toggle();', песочница);
  await vm.runInContext('__toggle();', песочница);
  if (vm.runInContext('ui.chatText.value', песочница) !== '') {
    throw new Error('в поле попало что-то, хотя распознавание молчало');
  }
  if (сообщения[0] !== словарь.chat.dictate_empty) {
    throw new Error(
      'на пустом распознавании человеку ничего не сказали: '
      + JSON.stringify(сообщения[0])
    );
  }
  console.log('[ok] когда не расслышали, честно говорим об этом');

  console.log('\nВопрос к встрече можно сказать, а не печатать.');
}

проверить().then(() => {
  // Окно завело секундомер записи: он на setInterval и держал бы node
  // вечно, а проверка висела бы в прогоне, не падая и не проходя.
  песочница.stopTimer();
  process.exit(0);
}).catch((e) => {
  console.error(e.message);
  песочница.stopTimer();
  process.exit(1);
});
