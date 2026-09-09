// Проверка: сбой словаря не должен оставлять человека наедине с ключами.
//
// Живой случай, пойманный в общем прогоне: заголовок раздела настроек
// вместо «Диктовка» показал 'prefs.tab.dictation'. Значит словарь до
// t() не доехал, а интерфейс всё равно нарисовался - буквами вида
// 'prefs.tab.audio' вместо слов.
//
// Так и задумано в t(): отсутствующий ключ возвращает сам себя, чтобы
// дыра была видна разработчику. Но это про один пропущенный ключ, а не
// про весь словарь сразу. Когда не доехал весь словарь, показывать
// человеку экран из точек и латиницы нельзя: русский язык у нас есть
// прямо в сборке, и откатиться на него честнее.
//
// Гоняем настоящий web/app.js, а не копию: копия разошлась бы с кодом
// молча, и проверка охраняла бы уже не то.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const код = fs.readFileSync(path.join(__dirname, 'web', 'app.js'), 'utf8');

function узел() {
  const себя = {
    hidden: false,
    textContent: '',
    value: '',
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

// Узел с data-i18n: то самое, что человек видит на экране. Проверять
// один t() мало - он вернёт перевод, даже если DOM никто не тронул.
const заголовокНаЭкране = {
  dataset: { i18n: 'prefs.tab.dictation' },
  textContent: '',
  innerHTML: '',
  placeholder: '',
  title: '',
  setAttribute() {},
};

const песочница = {
  console,
  // Таймеры глушим: app.js заводит секундомеры и опросы, а node живёт,
  // пока жив хоть один. Проверке нужен перевод, а не ход времени.
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  document: {
    getElementById: () => узел(),
    createElement: () => узел(),
    querySelector: () => узел(),
    querySelectorAll: (sel) => (sel === '[data-i18n]' ? [заголовокНаЭкране] : []),
    body: узел(),
    documentElement: узел(),
    addEventListener() {},
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { userAgent: 'test' },
  fetch: async () => ({ ok: true, json: async () => ({}) }),
};
песочница.addEventListener = () => {};
песочница.removeEventListener = () => {};
песочница.matchMedia = () => ({ matches: false, addEventListener() {} });
песочница.window = песочница;
песочница.globalThis = песочница;

vm.createContext(песочница);
vm.runInContext(
  код + '\nglobalThis.__setLanguage = setLanguage;' +
        '\nglobalThis.__t = t;',
  песочница,
  { filename: 'app.js' }
);

// Мост из Python: словарь не доехал. Так бывает при сбое в pywebview и
// при спешке - клик по настройкам раньше, чем окно догрузилось.
//
// Подменяем не api, а window.pywebview.api: api в app.js это Proxy,
// который ходит именно туда и ждёт появления моста. Подмена самого api
// молча не сработала бы, и проверка проверяла бы пустоту.
const запрошено = [];
// Что программа записала в настройки. Откат на русский из-за сбоя не
// должен переставлять человеку язык навсегда.
const сохранено = [];
песочница.pywebview = {
  api: {
    get_i18n_dict: async (lang) => {
      запрошено.push(lang);
      // Английский не отдаём вовсе, русский - настоящий словарь из сборки.
      if (lang === 'ru') {
        return JSON.parse(
          fs.readFileSync(path.join(__dirname, 'web', 'i18n', 'ru.json'), 'utf8')
        );
      }
      return null;
    },
    set_language: async (lang) => { сохранено.push(lang); },
  },
};

async function main() {
  await песочница.__setLanguage('en');

  const заголовок = песочница.__t('prefs.tab.dictation');

  if (заголовок === 'prefs.tab.dictation') {
    throw new Error(
      'словарь не доехал, и человеку показывают ключ ' +
      "'prefs.tab.dictation' вместо слова: весь интерфейс превращается " +
      'в точки и латиницу'
    );
  }
  console.log(`[ок] при сбое словаря показывается слово, а не ключ: ${заголовок}`);

  if (заголовокНаЭкране.textContent !== заголовок) {
    throw new Error(
      'словарь откатили, а на экране осталось ' +
      `${JSON.stringify(заголовокНаЭкране.textContent)}: человек всё равно ` +
      'смотрит на ключи, пока не переоткроет окно'
    );
  }
  console.log('[ок] текст на экране перерисован, а не только в словаре');

  if (!запрошено.includes('ru')) {
    throw new Error(
      'русский словарь даже не пробовали: он лежит прямо в сборке, и ' +
      'откатиться на него честнее, чем показывать ключи'
    );
  }
  console.log('[ок] при сбое подставляется язык из сборки');

  if (сохранено.length) {
    throw new Error(
      `при сбое словаря язык переписали в настройках на ${сохранено[0]}: ` +
      'человек получил бы чужой язык навсегда, хотя сбой был временным'
    );
  }
  console.log('[ок] выбранный человеком язык в настройках не тронут');

  // Когда словарь доехал, ничего откатывать не нужно.
  запрошено.length = 0;
  песочница.pywebview.api.get_i18n_dict = async (lang) => {
    запрошено.push(lang);
    return JSON.parse(
      fs.readFileSync(path.join(__dirname, 'web', 'i18n', 'en.json'), 'utf8')
    );
  };
  await песочница.__setLanguage('en', { persist: false });
  if (запрошено.length !== 1 || запрошено[0] !== 'en') {
    throw new Error(
      `рабочий словарь всё равно откатывают: запрошено ${JSON.stringify(запрошено)}`
    );
  }
  const английский = песочница.__t('prefs.tab.dictation');
  if (!английский || английский === 'prefs.tab.dictation') {
    throw new Error('английский словарь доехал, а перевода нет');
  }
  console.log(`[ок] рабочий язык не подменяется русским: ${английский}`);

  console.log('\nСбой словаря не оставляет человека с ключами на экране.');
}

main().catch((e) => {
  console.error(String(e.message || e));
  process.exit(1);
});
