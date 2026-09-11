// Голый ключ перевода не попадает на экран.
//
// Беда, из-за которой это написано. Человек запустил программу и увидел
// на главной кнопке «recording.start» вместо «Начать запись». Словарь
// едет из Python через мост и к первой отрисовке успевает не всегда, а
// `t()` на отсутствующий словарь возвращал сам ключ. Ключ на экране —
// это не «видно дыру в переводе», это сломанный интерфейс: человек не
// понимает, что за кнопка перед ним.
//
// Разница важная. Нет ключа в словаре — показываем ключ, чтобы дыру
// нашли: она наша и чинится переводом. Нет словаря вовсе — оставляем
// то, что уже написано в разметке: там лежит человеческая надпись, а
// словарь вот-вот доедет.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const корень = __dirname;
const код = fs.readFileSync(path.join(корень, 'web', 'app.js'), 'utf8')
  .replace(/\r\n/g, '\n');

const беды = [];
function проверить(ок, что) {
  console.log((ок ? '[ok] ' : '[FAIL] ') + что);
  if (!ок) беды.push(что);
}

function узел(текст) {
  const себя = {
    hidden: false,
    textContent: текст || '',
    placeholder: '',
    title: '',
    value: '',
    checked: false,
    style: {},
    dataset: {},
    children: [],
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    addEventListener() {},
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
  return себя;
}

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
  pywebview: { api: new Proxy({}, { get: () => async () => ({}) }) },
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

// --- 1. Словаря нет вовсе: ключ на экран не попадает ---------------------
vm.runInContext('i18n = { lang: "ru", dict: {} };', песочница);

const пусто = vm.runInContext('tЕслиЕсть("recording.start")', песочница);
проверить(пусто === null,
  'без словаря tЕслиЕсть() отвечает null, а не ключом: иначе человек увидит ' +
  '«recording.start» на главной кнопке (вернулось: ' + JSON.stringify(пусто) + ')');

// А обычный t() ключ показывает: плашки и сообщения рождаются в коде,
// и запасного текста в разметке у них нет. Пустая плашка хуже плашки
// с ключом: человек вообще не поймёт, о чём его спросили.
const вКоде = vm.runInContext('t("noticed.ask")', песочница);
проверить(вКоде === 'noticed.ask',
  'текст, которого нет в разметке, показывается ключом, а не пустотой ' +
  '(вернулось: ' + JSON.stringify(вКоде) + ')');

// Кнопка записи с человеческой надписью из разметки.
песочница.ui.record = узел();
песочница.ui.recLabel = узел('Начать запись');
песочница.ui.levels = узел();
песочница.ui.timer = узел();
песочница.ui.levelMe = узел();
песочница.ui.levelThem = узел();
песочница.state.isRecording = false;

песочница.renderRecordingState();
проверить(песочница.ui.recLabel.textContent === 'Начать запись',
  'надпись на кнопке записи не затёрта ключом (сейчас: «' +
  песочница.ui.recLabel.textContent + '»)');

песочница.refreshDynamicTexts();
проверить(песочница.ui.recLabel.textContent === 'Начать запись',
  'перерисовка текстов тоже не затирает надпись (сейчас: «' +
  песочница.ui.recLabel.textContent + '»)');

// applyI18n проходит по всей разметке. Если словаря нет, он не имеет
// права затирать надписи: в index.html лежат человеческие слова, и
// пустая строка вместо них — это голый интерфейс без единой подписи.
const подопытный = узел('Начать запись');
подопытный.dataset.i18n = 'recording.start';
const местоВвода = узел();
местоВвода.dataset.i18nPlaceholder = 'search.placeholder';
местоВвода.placeholder = 'Поиск по встречам';
const сПодсказкой = узел();
сПодсказкой.dataset.i18nTitle = 'meeting.delete_tooltip';
сПодсказкой.title = 'Удалить встречу';

песочница.document.querySelectorAll = (sel) => {
  if (sel === '[data-i18n]') return [подопытный];
  if (sel === '[data-i18n-placeholder]') return [местоВвода];
  if (sel === '[data-i18n-title]') return [сПодсказкой];
  return [];
};
песочница.applyI18n();
проверить(подопытный.textContent === 'Начать запись',
  'без словаря applyI18n не затирает надпись в разметке (сейчас: «' +
  подопытный.textContent + '»)');
проверить(местоВвода.placeholder === 'Поиск по встречам',
  'без словаря не затирается подсказка в поле ввода (сейчас: «' +
  местоВвода.placeholder + '»)');
проверить(сПодсказкой.title === 'Удалить встречу',
  'без словаря не затирается всплывающая подсказка (сейчас: «' +
  сПодсказкой.title + '»)');
песочница.document.querySelectorAll = () => [];

// --- 2. Словарь есть, ключа в нём нет: ключ показываем -------------------
// Это уже наша дыра в переводе, и её должно быть видно.
vm.runInContext('i18n = { lang: "ru", dict: { recording: { stop: "Стоп" } } };',
                песочница);
const дыра = vm.runInContext('t("prefs.tab.nowhere")', песочница);
проверить(дыра === 'prefs.tab.nowhere',
  'пропущенный ключ при живом словаре виден как ключ: это наша дыра ' +
  'в переводе, и её надо заметить (вернулось: ' + JSON.stringify(дыра) + ')');

// А в разметке даже при живом словаре ключ показывать нельзя: там уже
// лежит русский текст, и он честнее «prefs.tab.nowhere» на любом языке.
// Забытый перевод ловится отдельной проверкой словарей ниже, а человеку
// в это время лучше видеть русское слово, чем техническую строку.
const сЗапасом = vm.runInContext('tЕслиЕсть("prefs.tab.nowhere")', песочница);
проверить(сЗапасом === null,
  'при забытом переводе в разметке остаётся её собственный текст, а не ' +
  'ключ (вернулось: ' + JSON.stringify(сЗапасом) + ')');

const забытый = узел('Настройки звука');
забытый.dataset.i18n = 'prefs.tab.nowhere';
песочница.document.querySelectorAll = (sel) =>
  (sel === '[data-i18n]' ? [забытый] : []);
песочница.applyI18n();
песочница.document.querySelectorAll = () => [];
проверить(забытый.textContent === 'Настройки звука',
  'забытый перевод не превращает надпись в ключ (сейчас: «' +
  забытый.textContent + '»)');

// --- 3. Словарь доехал: человек видит слова ------------------------------
const словарь = JSON.parse(
  fs.readFileSync(path.join(корень, 'web', 'i18n', 'ru.json'), 'utf8')
);
песочница.__словарь = словарь;
vm.runInContext('i18n = { lang: "ru", dict: globalThis.__словарь };', песочница);

песочница.renderRecordingState();
проверить(песочница.ui.recLabel.textContent === 'Начать запись',
  'с живым словарём на кнопке человеческая надпись (сейчас: «' +
  песочница.ui.recLabel.textContent + '»)');

песочница.state.isRecording = true;
песочница.renderRecordingState();
проверить(песочница.ui.recLabel.textContent === 'Остановить',
  'во время записи надпись меняется на «Остановить» (сейчас: «' +
  песочница.ui.recLabel.textContent + '»)');

// --- 4. Каждый ключ из разметки есть во всех четырёх словарях ------------
// Дыра в переводе видна только тому, кто открыл программу на своём
// языке. Проверяем все языки разом, а не тот, на котором работаем сами.
const html = fs.readFileSync(path.join(корень, 'web', 'index.html'), 'utf8');
const ключи = new Set();
for (const м of html.matchAll(/data-i18n(?:-placeholder|-title|-aria-label)?="([^"]+)"/g)) {
  if (м[1] !== '1') ключи.add(м[1]);
}
проверить(ключи.size > 50, 'ключи из разметки найдены (' + ключи.size + ')');

function взять(словарь, ключ) {
  let узел = словарь;
  for (const часть of ключ.split('.')) {
    if (узел && typeof узел === 'object' && часть in узел) узел = узел[часть];
    else return null;
  }
  return typeof узел === 'string' ? узел : null;
}

for (const язык of ['ru', 'en', 'es', 'sr']) {
  const с = JSON.parse(
    fs.readFileSync(path.join(корень, 'web', 'i18n', язык + '.json'), 'utf8')
  );
  const нет = [...ключи].filter((к) => взять(с, к) === null);
  проверить(нет.length === 0,
    'в словаре ' + язык + ' есть все ключи из разметки' +
    (нет.length ? ' (нет: ' + нет.slice(0, 8).join(', ') + ')' : ''));
}

// --- 5. Ключи, которые app.js просит сам ---------------------------------
// Их в разметке нет, и забытый перевод виден только на экране в нужный
// момент: например, только когда сторож заметит разговор.
const изКода = new Set();
for (const м of код.matchAll(/\bt\('([a-z0-9_]+(?:\.[a-z0-9_]+)+)'/g)) {
  изКода.add(м[1]);
}
проверить(изКода.size > 20, 'ключи из кода найдены (' + изКода.size + ')');

for (const язык of ['ru', 'en', 'es', 'sr']) {
  const с = JSON.parse(
    fs.readFileSync(path.join(корень, 'web', 'i18n', язык + '.json'), 'utf8')
  );
  // Склонения живут тройками one/few/many, их код просит через tPlural
  // с базовым ключом: проверяем, что есть хотя бы одна из форм.
  const нет = [...изКода].filter((к) => {
    if (взять(с, к) !== null) return false;
    return ['one', 'few', 'many'].every((ф) => взять(с, к + '.' + ф) === null);
  });
  проверить(нет.length === 0,
    'в словаре ' + язык + ' есть все ключи, которые просит код' +
    (нет.length ? ' (нет: ' + нет.slice(0, 8).join(', ') + ')' : ''));
}

if (беды.length) {
  console.log('\nНе прошло проверок: ' + беды.length);
  process.exit(1);
}
console.log('\nЧеловек видит слова, а не ключи перевода.');
