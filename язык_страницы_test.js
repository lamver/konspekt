// Проверка: язык страницы выбирается правильно, в том числе по домену.
//
// Одна и та же страница отдаётся с трёх адресов: lamver.github.io,
// konspekt.aisearch.ru (заведён как русский) и konspekt.aisearch.tech
// (английский). Человек, пришедший по русской ссылке, должен увидеть
// русский, даже если браузер у него английский.
//
// Порядок важнее самого факта выбора, поэтому проверяем именно его:
// ?lang сильнее сохранённого выбора, сохранённый выбор сильнее домена,
// домен сильнее браузера. Если домен станет сильнее выбора человека,
// переключатель языков на .ru будет работать вхолостую, а это молчаливая
// поломка: кнопки нажимаются, язык не меняется.
//
// Берём настоящий pickLang из docs/index.html, а не копию: копия
// разошлась бы с кодом молча.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(path.join(__dirname, 'docs', 'index.html'), 'utf8');

// Вырезаем блок от объявления языков до конца pickLang. Границы явные,
// чтобы при переносе кода проверка падала, а не тихо брала не то.
const начало = html.indexOf("const LANGS = [");
const конецМетки = html.indexOf("function setText");
if (начало < 0 || конецМетки < 0 || конецМетки < начало) {
  console.error('[БЕДА] не нашёл код выбора языка в docs/index.html');
  process.exit(1);
}
const код = html.slice(начало, конецМетки);

if (!код.includes('DOMAIN_LANGS') || !код.includes('langFromDomain')) {
  console.error('[БЕДА] в коде выбора языка нет привязки к домену');
  process.exit(1);
}

const беды = [];

function проверить(ок, что) {
  console.log((ок ? '[ок] ' : '[БЕДА] ') + что);
  if (!ок) беды.push(что);
}

// Одна попытка выбора языка в заданных условиях.
function выбор({ host = 'lamver.github.io', search = '', saved = null,
                 languages = ['en-US'] } = {}) {
  const хранилище = {
    getItem: (k) => (k === 'konspekt-lang' ? saved : null),
    setItem: () => {},
  };
  const песочница = {
    location: { hostname: host, search },
    localStorage: хранилище,
    navigator: { languages, language: languages[0] },
    URLSearchParams,
    console,
  };
  vm.createContext(песочница);
  vm.runInContext(код + '\npickLang();', песочница);
  return vm.runInContext('pickLang()', песочница);
}

// Домен решает, когда человек ничего не выбирал.
проверить(выбор({ host: 'konspekt.aisearch.ru' }) === 'ru',
  'на konspekt.aisearch.ru русский, даже если браузер английский');
проверить(выбор({ host: 'konspekt.aisearch.tech', languages: ['ru-RU'] }) === 'en',
  'на konspekt.aisearch.tech английский, даже если браузер русский');
проверить(выбор({ host: 'www.konspekt.aisearch.ru' }) === 'ru',
  'поддомен www тоже считается русским');

// Домен не должен ломать остальные адреса.
проверить(выбор({ host: 'lamver.github.io', languages: ['ru-RU'] }) === 'ru',
  'на github.io язык по-прежнему берётся из браузера');
проверить(выбор({ host: 'lamver.github.io', languages: ['de-DE'] }) === 'en',
  'незнакомый язык браузера даёт английский');
проверить(выбор({ host: 'localhost', languages: ['sr-Latn-RS'] }) === 'sr',
  'на localhost работает как раньше');

// Порядок: человек сильнее домена.
проверить(выбор({ host: 'konspekt.aisearch.ru', saved: 'en' }) === 'en',
  'сохранённый выбор человека сильнее домена');
проверить(выбор({ host: 'konspekt.aisearch.ru', search: '?lang=es' }) === 'es',
  'ссылка с ?lang сильнее домена');
проверить(выбор({ host: 'konspekt.aisearch.tech', saved: 'ru' }) === 'ru',
  'выбравший русский на .tech видит русский');

// Хитрость: чужой домен, кончающийся похоже, не должен считаться нашим.
проверить(выбор({ host: 'konspekt.aisearch.ru.example.com' }) === 'en',
  'чужой домен с похожим хвостом не считается русским');

if (беды.length) {
  console.error(`\nНе прошло проверок: ${беды.length}`);
  process.exit(1);
}
console.log('\nВыбор языка страницы в порядке.');
