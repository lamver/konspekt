// Копирование заметок: ради этого человек и разбирал встречу.
//
// Жалоба (задача №26): «текст нельзя выделить и скопировать: ни в
// транскрипте, ни в заметках, ни в чате. Человек разобрал встречу ради
// того, чтобы отправить решение в чат команды, а может только
// перепечатать руками».
//
// Выделение мышью к этому моменту уже разрешено в содержательных
// областях (user-select: text в styles.css). Оставалось самое частое
// действие: забрать заметки целиком, не возя мышью по всему тексту.
//
// Гоняем настоящий web/app.js, а не копию: копия разошлась бы с кодом
// молча, и проверка охраняла бы уже не то.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const код = fs.readFileSync(path.join(__dirname, 'web', 'app.js'), 'utf8');
const словарьRu = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'web', 'i18n', 'ru.json'), 'utf8')
);

// --- поддельное окно ----------------------------------------------------
//
// Нужен настоящий разбор HTML: markdownToPlain ходит по дереву, а не по
// строкам. Городить полный DOM ради этого незачем, но и врать нельзя —
// собираем узел, который честно разбирает вложенные списки.
function разобрать(html) {
  // Мини-разбор: нам хватает li, ul/ol и текста между тегами.
  const узлы = [];
  const рег = /<(\/?)(\w+)[^>]*>|([^<]+)/g;
  const стек = [{ nodeName: 'div', childNodes: узлы }];
  let м;
  while ((м = рег.exec(html)) !== null) {
    const [, закрытие, тег, текст] = м;
    if (текст !== undefined) {
      const чистый = текст.replace(/&lt;/g, '<').replace(/&gt;/g, '>')
        .replace(/&amp;/g, '&');
      if (чистый.trim()) {
        стек[стек.length - 1].childNodes.push({
          nodeName: '#text', childNodes: [], textContent: чистый,
        });
      }
      continue;
    }
    if (закрытие) {
      if (стек.length > 1) стек.pop();
      continue;
    }
    const узел = { nodeName: тег, childNodes: [] };
    стек[стек.length - 1].childNodes.push(узел);
    if (!/^(br|hr|img|input)$/i.test(тег)) стек.push(узел);
  }
  const текстУзла = (у) => {
    if (у.nodeName === '#text') return у.textContent;
    return у.childNodes.map(текстУзла).join('');
  };
  for (const у of узлы) Object.defineProperty(у, 'textContent', {
    get() { return текстУзла(this); },
  });
  const пройтись = (список) => {
    for (const у of список) {
      if (у.nodeName !== '#text' && !Object.getOwnPropertyDescriptor(у, 'textContent')) {
        Object.defineProperty(у, 'textContent', { get() { return текстУзла(this); } });
      }
      пройтись(у.childNodes);
    }
  };
  пройтись(узлы);
  return узлы;
}

function узел(имя) {
  const себя = {
    nodeName: имя || 'div',
    hidden: false,
    textContent: '',
    value: '',
    disabled: false,
    style: {},
    dataset: {},
    childNodes: [],
    children: [],
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
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
    focus() {},
    scrollIntoView() {},
    remove() {},
  };
  // innerHTML = ... должен наполнять дерево: на нём стоит markdownToPlain.
  Object.defineProperty(себя, 'innerHTML', {
    get() { return себя.__html || ''; },
    set(v) { себя.__html = v; себя.childNodes = разобрать(v); },
  });
  return себя;
}

// Что уехало в буфер обмена и что показали человеку.
const буфер = [];
const сообщения = [];

const песочница = {
  console,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  document: {
    getElementById: () => узел(),
    createElement: (имя) => узел(имя),
    querySelector: () => узел(),
    querySelectorAll: () => [],
    body: узел(),
    documentElement: узел(),
    addEventListener() {},
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: {
    userAgent: 'test',
    clipboard: { writeText: async (t) => { буфер.push(t); } },
  },
  fetch: async () => ({ ok: true, json: async () => ({}) }),
};
песочница.addEventListener = () => {};
песочница.removeEventListener = () => {};
песочница.matchMedia = () => ({ matches: false, addEventListener() {} });
песочница.window = песочница;
песочница.globalThis = песочница;

vm.createContext(песочница);
vm.runInContext(
  код + '\nglobalThis.__state = state;'
      + '\nglobalThis.__копировать = копировать;'
      + '\nglobalThis.__markdownToPlain = markdownToPlain;'
      + '\nglobalThis.__renderSummary = renderSummary;',
  песочница,
  { filename: 'app.js' }
);
песочница.state = песочница.__state;
vm.runInContext('i18n.dict = globalThis.__словарь;', Object.assign(
  песочница, { __словарь: словарьRu }
));
песочница.showToast = (текст) => сообщения.push(текст);

// Кладём поддельные узлы: bindUi() звать нельзя, он заводит таймеры.
песочница.__узел = узел;
vm.runInContext(
  "for (const и of ['summaryBody','summaryEmpty','summaryRun','summaryStop',"
  + "'summaryCopy','summaryCopyPlain','summaryTitle','toast','toastText'])"
  + " ui[и] = globalThis.__узел();",
  песочница
);

const ЗАМЕТКИ = [
  '# Итоги встречи',
  '',
  'Договорились про **сроки**.',
  '',
  '- Петя чинит вход до пятницы',
  '- Маша пишет письмо клиенту',
].join('\n');

// --- Копирование с разметкой --------------------------------------------
песочница.state.current = { summary: ЗАМЕТКИ };
vm.runInContext('__копировать(state.current.summary, "copy.done");', песочница);

// Дождаться промиса внутри копировать().
setTimeout(() => {
  if (буфер.length !== 1) {
    throw new Error(
      'кнопка «копировать» ничего не положила в буфер обмена: человек '
      + 'решит, что скопировал, и вставит в чат команды пустоту'
    );
  }
  if (буфер[0] !== ЗАМЕТКИ) {
    throw new Error('в буфер уехали не те заметки: ' + JSON.stringify(буфер[0]));
  }
  if (!сообщения.length) {
    throw new Error(
      'человеку не сказали, что заметки скопированы. Буфер обмена невидим, '
      + 'и молчание читается как поломка: он нажмёт кнопку ещё раз'
    );
  }
  console.log('[ok] заметки копируются с разметкой: ' + сообщения[0]);

  // --- Копирование без разметки -----------------------------------------
  const простой = vm.runInContext(
    '__markdownToPlain(state.current.summary);', песочница
  );
  if (/[*#]/.test(простой)) {
    throw new Error(
      'в «без разметки» остались звёздочки и решётки: "' + простой + '". '
      + 'В поле, которое разметку не понимает, это выглядит мусором'
    );
  }
  if (!простой.includes('Петя чинит вход до пятницы')) {
    throw new Error('при очистке разметки потерялся пункт списка: ' + простой);
  }
  if (!простой.includes('Итоги встречи')) {
    throw new Error('при очистке разметки потерялся заголовок: ' + простой);
  }
  if (!простой.includes('- ')) {
    throw new Error(
      'список превратился в сплошной текст: "' + простой + '". Задачи из '
      + 'встречи должны остаться списком, иначе их не разобрать'
    );
  }
  console.log('[ok] «без разметки» оставляет текст и списки, убирает знаки');

  // --- Пустые заметки: не врём, что скопировали --------------------------
  буфер.length = 0;
  сообщения.length = 0;
  vm.runInContext('__копировать("", "copy.done");', песочница);
  setTimeout(() => {
    if (буфер.length) {
      throw new Error('в буфер положили пустоту вместо честного отказа');
    }
    if (сообщения[0] !== словарьRu.copy.nothing) {
      throw new Error(
        'на пустых заметках не сказали, что копировать нечего: '
        + JSON.stringify(сообщения[0])
      );
    }
    console.log('[ok] на пустых заметках честно говорим, что копировать нечего');

    // --- Кнопки прячутся, пока заметок нет ------------------------------
    vm.runInContext('__renderSummary("");', песочница);
    const скрыта = vm.runInContext('ui.summaryCopy.hidden', песочница);
    if (!скрыта) {
      throw new Error(
        'кнопка «копировать» живая, когда заметок ещё нет: человек нажмёт '
        + 'и решит, что скопировал пустоту'
      );
    }
    vm.runInContext('__renderSummary("# Есть заметки");', песочница);
    const видна = vm.runInContext('!ui.summaryCopy.hidden', песочница);
    if (!видна) {
      throw new Error('заметки есть, а кнопка «копировать» так и не появилась');
    }
    console.log('[ok] кнопки копирования появляются вместе с заметками');

    // --- Ответ чата тоже копируется --------------------------------------
    //
    // Ответ на вопрос к встрече — такой же итог работы, как заметки: его
    // несут в чат команды или в задачу.
    const ОТВЕТ = 'Решили: **выкатываем в среду**.\n- Петя готовит откат';
    const пузырь = узел('div');
    пузырь.classList = {
      add() {}, remove() {}, toggle() {},
      contains: (имя) => имя === 'bubble--bot',
    };
    const дети = [];
    пузырь.appendChild = (у) => дети.push(у);
    vm.runInContext('globalThis.__setBubbleText = setBubbleText;', песочница);
    песочница.__пузырь = пузырь;
    песочница.__ответ = ОТВЕТ;
    vm.runInContext('__setBubbleText(__пузырь, __ответ);', песочница);

    const кнопка = дети.find((у) => у.className === 'bubble__copy');
    if (!кнопка) {
      throw new Error(
        'у ответа чата нет кнопки «копировать»: длинный ответ придётся '
        + 'выделять мышью или перепечатывать'
      );
    }
    if (!кнопка.textContent) {
      throw new Error('кнопка копирования в ответе без подписи');
    }

    буфер.length = 0;
    сообщения.length = 0;
    кнопка.__клики.click({ stopPropagation() {} });
    setTimeout(() => {
      if (буфер[0] !== ОТВЕТ) {
        throw new Error(
          'из ответа скопировали не разметку, а "' + буфер[0] + '": в '
          + 'трекере и мессенджере список развалится в сплошной текст'
        );
      }
      if (сообщения[0] !== словарьRu.copy.done_answer) {
        throw new Error('после копирования ответа человеку ничего не сказали');
      }
      console.log('[ok] ответ чата копируется с разметкой: ' + сообщения[0]);

      // Свой вопрос копировать незачем: человек его и так знает, а
      // кнопка у каждой строки превращает переписку в мусорку.
      const мой = узел('div');
      мой.classList = {
        add() {}, remove() {}, toggle() {},
        contains: (имя) => имя === 'bubble--me',
      };
      const детиМоего = [];
      мой.appendChild = (у) => детиМоего.push(у);
      песочница.__пузырь = мой;
      vm.runInContext('__setBubbleText(__пузырь, "мой вопрос");', песочница);
      if (детиМоего.some((у) => у.className === 'bubble__copy')) {
        throw new Error('кнопку копирования повесили и на вопрос человека');
      }
      console.log('[ok] у своего вопроса кнопки копирования нет');

      // --- Реплика копируется цитатой ------------------------------------
      //
      // Голый текст в чате команды бесполезен: «это надо переделать» без
      // говорящего и места во встрече ничего не значит.
      vm.runInContext('globalThis.__цитата = цитатаРеплики;', песочница);
      const цитата = vm.runInContext(
        '__цитата("Маша", "12:30", "это надо переделать");', песочница
      );
      if (!цитата.includes('Маша')) {
        throw new Error('в цитате нет говорящего: ' + цитата);
      }
      if (!цитата.includes('12:30')) {
        throw new Error('в цитате нет времени: ' + цитата);
      }
      if (!цитата.includes('это надо переделать')) {
        throw new Error('в цитате потерялся сам текст: ' + цитата);
      }
      console.log('[ok] реплика копируется цитатой: ' + цитата);

      // Пустая реплика не должна превращаться в «[Маша, 12:30] » —
      // человек вставит в чат пустую скобку и не заметит.
      const пустая = vm.runInContext('__цитата("Маша", "12:30", "  ");', песочница);
      if (пустая !== '') {
        throw new Error('из пустой реплики собрали цитату: ' + JSON.stringify(пустая));
      }
      console.log('[ok] пустая реплика цитатой не становится');

      console.log('\nЗаметки, ответы и реплики можно забрать из программы.');
    }, 0);
  }, 0);
}, 0);
