// Выдача поиска в окне: порядок и подсветка найденного.
//
// Поиск отдаёт встречи лучшими первыми, а окно раньше сортировало их по
// дате: лучшая находка тонула среди двадцати случайных. И подсветка
// искала запрос буквально: основа «выруч» подсвечивала бы кусок слова
// «выручке», а «прием» не нашёл бы «Приём» вовсе.
//
// Гоняем настоящий web/app.js: копия разошлась бы с кодом молча.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const код = fs.readFileSync(path.join(__dirname, 'web', 'app.js'), 'utf8');
const словарь = JSON.parse(fs.readFileSync(path.join(__dirname, 'web', 'i18n', 'ru.json'), 'utf8'));

let провалов = 0;
function проверить(условие, что) {
  console.log(`[${условие ? 'ok' : 'FAIL'}] ${что}`);
  if (!условие) провалов += 1;
}

// Узел, который помнит детей: по ним видно, что подсвечено и в каком порядке.
function узел(имя) {
  const себя = {
    nodeName: (имя || 'div').toUpperCase(),
    className: '', title: '', hidden: false, dataset: {}, style: {},
    children: [],
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    addEventListener() {}, removeEventListener() {},
    setAttribute() {}, removeAttribute() {},
    querySelector: () => узел(), querySelectorAll: () => [],
    appendChild(ребёнок) {
      if (ребёнок && ребёнок.__фрагмент) себя.children.push(...ребёнок.children);
      else себя.children.push(ребёнок);
      return ребёнок;
    },
    append(...дети) { for (const д of дети) себя.appendChild(д); },
  };
  let текст = '';
  Object.defineProperty(себя, 'textContent', {
    get() { return текст || себя.children.map((д) => д.textContent).join(''); },
    set(v) { текст = v; себя.children = []; },
  });
  Object.defineProperty(себя, 'innerHTML', {
    get() { return ''; }, set() { себя.children = []; },
  });
  return себя;
}

const песочница = {
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  document: {
    getElementById: () => узел(),
    createElement: (имя) => узел(имя),
    createTextNode: (т) => ({ nodeName: '#text', textContent: т }),
    createDocumentFragment: () => Object.assign(узел('#fragment'), { __фрагмент: true }),
    querySelector: () => узел(), querySelectorAll: () => [],
    body: узел(), documentElement: узел(), addEventListener() {},
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { userAgent: 'test' },
  fetch: async () => ({ ok: true, json: async () => ({}) }),
};
песочница.addEventListener = () => {};
песочница.matchMedia = () => ({ matches: false, addEventListener() {} });
песочница.window = песочница;
песочница.globalThis = песочница;
vm.createContext(песочница);
vm.runInContext(код + '\nglobalThis.__state = state; globalThis.__ui = ui;'
  + ' globalThis.__highlight = highlight; globalThis.__render = renderMeetingList;',
  песочница, { filename: 'app.js' });
vm.runInContext('i18n.dict = globalThis.__словарь;', Object.assign(песочница, { __словарь: словарь }));

const подсвечено = (текст, запрос) => песочница.__highlight(текст, запрос).children
  .filter((д) => д.nodeName === 'MARK').map((д) => д.textContent);

// --- подсветка ---------------------------------------------------------
проверить(JSON.stringify(подсвечено('Какие перспективы по продажам там, по выручке?', 'выруч продаж'))
  === JSON.stringify(['продажам', 'выручке']),
  'подсвечено слово целиком, в той форме, в какой его сказали');
проверить(JSON.stringify(подсвечено('Приём платежей', 'прием')) === JSON.stringify(['Приём']),
  '«прием» подсвечивает «Приём»: ё сравнивается как е, как в поиске');
проверить(подсвечено('Спасибо, выручаешь', 'выруч').length === 1,
  'совпадение в начале слова подсвечивается');
проверить(подсвечено('перевыручка', 'выруч').length === 0,
  'кусок посреди чужого слова не подсвечивается: поиск его не находил');
const целиком = песочница.__highlight('Всё <b>ок</b> & выручка', 'выруч').children
  .map((д) => д.textContent).join('');
проверить(целиком === 'Всё <b>ок</b> & выручка', 'текст цитаты не теряется и не становится разметкой');

// --- порядок -----------------------------------------------------------
const встречи = [
  { id: 'новая', title: 'Вчерашняя', created_at: 300, duration: 60, status: 'ready' },
  { id: 'средняя', title: 'Позавчерашняя', created_at: 200, duration: 60, status: 'ready' },
  { id: 'старая', title: 'Планёрка 07.09', created_at: 100, duration: 60, status: 'ready' },
  { id: 'мимо', title: 'Выручка: отчёт', created_at: 50, duration: 60, status: 'ready' },
];
const цитата = (т) => ({ hits: 1, quotes: [{ text: т, start: 0 }], terms: ['выруч'] });
const состояние = песочница.__state;
состояние.meetings = встречи;
состояние.filter = 'выручка';
состояние.search.query = 'выручка';
// Поиск вернул лучшую находку первой, хотя она самая старая.
состояние.search.byMeeting = new Map([
  ['старая', цитата('по выручке')],
  ['новая', цитата('выручаешь')],
]);
песочница.__ui.list = узел();
песочница.__render();
const порядок = песочница.__ui.list.children.map((н) => н.dataset.id);
проверить(порядок[0] === 'старая' && порядок[1] === 'новая',
  `встречи идут в порядке поиска, лучшая первой, а не по дате: ${порядок.join(', ')}`);
проверить(порядок.includes('мимо') && порядок[порядок.length - 1] === 'мимо',
  'встреча, найденная по названию, остаётся в списке, после найденных в расшифровке');
проверить(!порядок.includes('средняя'), 'ненайденная встреча в выдачу не попадает');

// Цитата подсвечена по основам, которые вернул поиск, а не по тексту
// запроса: «выручка» в строке не совпадёт с «выручке» в цитате.
const карточка = песочница.__ui.list.children.find((н) => н.dataset.id === 'старая');
const цитатаУзел = карточка.children.find((д) => д.className === 'meeting-item__quote');
const метки = (цитатаУзел ? цитатаУзел.children : [])
  .filter((д) => д.nodeName === 'MARK').map((д) => д.textContent);
проверить(JSON.stringify(метки) === JSON.stringify(['выручке']),
  `в цитате подсвечена найденная форма слова, а не запрос буквально: ${JSON.stringify(метки)}`);

if (провалов) {
  console.log(`\nПровалов: ${провалов}`);
  process.exit(1);
}
console.log('\nОкно показывает лучшие находки первыми и подсвечивает найденные слова.');
