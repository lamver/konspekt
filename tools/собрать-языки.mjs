#!/usr/bin/env node
/* Собирает готовые страницы для русского, испанского и сербского.
 *
 * Зачем. `встроить-текст.mjs` уже вшил в разметку английский текст,
 * чтобы робот видел слова, а не пустые заголовки. Но язык у страницы
 * по-прежнему один: в сыром ответе сервера при любом `?lang=` стоит
 * `lang="en"`, английский `title` и английское описание. Переключение
 * языка живёт в скрипте, а скрипты читают не все:
 *
 *  - Bing и Яндекс по большей части их не выполняют;
 *  - Google выполняет с отсрочкой и не всегда;
 *  - мессенджеры и соцсети не выполняют никогда: карточка ссылки
 *    собирается из og-тегов разметки.
 *
 * Отсюда следствие, которое и было: для поиска сайт существовал только
 * по-английски, а ссылка, отправленная в чат, разворачивалась
 * по-английски кому угодно.
 *
 * Как. Из той же `docs/index.html` и того же `i18n.js` собираются
 * `docs/ru/`, `docs/es/`, `docs/sr/`: текст, заголовок, описание и
 * og-теги уже на нужном языке, без всякого JS. Исходник остаётся один,
 * руками эти файлы не правятся.
 *
 * Запуск: node tools/собрать-языки.mjs
 */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const КОРЕНЬ = join(dirname(fileURLToPath(import.meta.url)), '..');
const СТРАНИЦА = join(КОРЕНЬ, 'docs', 'index.html');
const СЛОВАРЬ = join(КОРЕНЬ, 'docs', 'i18n.js');

// Английский живёт в корне: это главный адрес и x-default.
const ЯЗЫКИ = ['ru', 'es', 'sr'];
const ГЛАВНЫЙ = 'https://konspekt.aisearch.tech';
const РУССКИЙ = 'https://konspekt.aisearch.ru';

/** Адрес страницы для каждого языка. */
const АДРЕСА = {
  en: `${ГЛАВНЫЙ}/`,
  es: `${ГЛАВНЫЙ}/es/`,
  sr: `${ГЛАВНЫЙ}/sr/`,
  // Русский домен и русская подпапка.
  //
  // Соблазнительно объявить русской версией просто `${РУССКИЙ}/`, но по
  // этому адресу лежит тот же корневой файл, что и на главном домене, то
  // есть английская страница: сервер русского домена отдаёт статику из
  // той же папки и ничего не подменяет. Человек английского не увидит —
  // скрипт переключит язык по домену, — а робот увидит именно её.
  //
  // Поэтому русской версией объявляется адрес, который правда отдаёт
  // русскую разметку. Домен при этом остаётся русским, так что для
  // читателя ничего не меняется. Как только корень русского домена
  // начнёт отдавать `/ru/index.html` (одна строка в настройках nginx),
  // сюда можно будет вернуть просто `${РУССКИЙ}/`.
  ru: `${РУССКИЙ}/ru/`,
};

function словари() {
  const код = readFileSync(СЛОВАРЬ, 'utf8');
  return new Function(`${код}\n;return I18N;`)();
}

function экранировать(текст) {
  return String(текст)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function вставить(html, id, текст) {
  const re = new RegExp(`(<([a-z0-9]+)\\b[^>]*\\bid="${id}"[^>]*>)([\\s\\S]*?)(</\\2>)`, 'i');
  if (!re.test(html)) throw new Error(`нет узла id="${id}"`);
  return html.replace(re, (_, откр, тег, было, закр) => `${откр}${экранировать(текст)}${закр}`);
}

/** Заменить содержимое узла, считая вложенные теги.
 *
 *  Простой шаблон «до первого </div>» здесь не годится: внутри
 *  #features лежат карточки, тоже из div. Такой шаблон закрывал узел на
 *  первой же карточке, и новая разметка вставлялась перед старой — на
 *  русской странице получилось одиннадцать карточек вместо шести,
 *  русские вперемешку с английскими.
 */
function заполнить(html, id, разметка) {
  const открывающий = new RegExp(`<([a-z0-9]+)\\b[^>]*\\bid="${id}"[^>]*>`, 'i');
  const м = html.match(открывающий);
  if (!м) throw new Error(`нет узла id="${id}"`);

  const тег = м[1];
  const началоСодержимого = м.index + м[0].length;

  // Идём по тегам того же имени и считаем глубину, пока не закроется наш.
  const шаблон = new RegExp(`<(/?)${тег}\\b[^>]*>`, 'gi');
  шаблон.lastIndex = началоСодержимого;
  let глубина = 1;
  let совпадение;
  while ((совпадение = шаблон.exec(html)) !== null) {
    глубина += совпадение[1] === '/' ? -1 : 1;
    if (глубина === 0) {
      return html.slice(0, началоСодержимого) + разметка + html.slice(совпадение.index);
    }
  }
  throw new Error(`у узла id="${id}" не нашлось закрывающего тега`);
}

/** Заменить один тег целиком, ругаясь, если его не нашлось. */
function подменить(html, образец, чем) {
  if (!образец.test(html)) throw new Error(`не нашёл: ${образец}`);
  return html.replace(образец, чем);
}

/** Блок canonical и hreflang: ссылки на настоящие адреса.
 *
 * Раньше испанский и сербский объявлялись через `?lang=`, то есть
 * указывали на страницу, которая в сыром виде английская. Для поиска это
 * обещание, которое не выполняется.
 */
function ссылки(язык) {
  const строки = [`<link rel="canonical" href="${АДРЕСА[язык]}" id="canonical">`];
  for (const [код, адрес] of Object.entries(АДРЕСА)) {
    строки.push(`<link rel="alternate" hreflang="${код}" href="${адрес}">`);
  }
  строки.push(`<link rel="alternate" hreflang="x-default" href="${АДРЕСА.en}">`);
  return строки.join('\n');
}

/** Заменить блок ссылок целиком, чтобы они не разъехались между собой. */
function заменитьСсылки(html, язык) {
  const начало = html.indexOf('<link rel="canonical"');
  const хвост = html.indexOf('hreflang="x-default"');
  if (начало < 0 || хвост < 0) throw new Error('не нашёл блок canonical/hreflang');
  const конец = html.indexOf('>', хвост) + 1;
  return html.slice(0, начало) + ссылки(язык) + html.slice(конец);
}

function собрать(язык, d, исходник) {
  let html = исходник;

  // Язык страницы: ровно то, из-за чего всё затевалось.
  html = подменить(html, /<html lang="[^"]*">/, `<html lang="${d.htmlLang}">`);

  // Заголовок и описание для выдачи.
  html = подменить(html, /<title>[\s\S]*?<\/title>/, `<title>${экранировать(d.title)}</title>`);
  html = подменить(
    html,
    /<meta name="description" content="[^"]*">/,
    `<meta name="description" content="${экранировать(d.description)}">`,
  );

  // Карточка для мессенджеров и соцсетей.
  html = подменить(
    html,
    /<meta property="og:title" content="[^"]*">/,
    `<meta property="og:title" content="${экранировать(d.title)}">`,
  );
  html = подменить(
    html,
    /<meta property="og:description" content="[^"]*">/,
    `<meta property="og:description" content="${экранировать(d.ogDescription)}">`,
  );
  html = подменить(
    html,
    /<meta property="og:url" content="[^"]*">/,
    `<meta property="og:url" content="${АДРЕСА[язык]}">`,
  );

  // og:locale: без него соцсети считают карточку английской при любом
  // языке. Тег может уже стоять (в корневой странице он есть) или нет —
  // поэтому сначала пробуем заменить, а если нечего, заводим рядом с
  // og:type.
  const локаль = d.htmlLang.replace('-', '_');
  if (/<meta property="og:locale" content="[^"]*">/.test(html)) {
    html = html.replace(
      /<meta property="og:locale" content="[^"]*">/,
      `<meta property="og:locale" content="${локаль}">`,
    );
  } else {
    html = подменить(
      html,
      /<meta property="og:type" content="website">/,
      `<meta property="og:type" content="website">\n<meta property="og:locale" content="${локаль}">`,
    );
  }

  html = заменитьСсылки(html, язык);

  // Видимый текст: те же узлы, что заполняет скрипт в браузере.
  const h1 = d.title.replace(/^Konspekt\s*[—-]\s*/, '');
  const простые = [
    ['t-title', h1],
    ['t-tagline', d.tagline],
    ['t-lead', d.lead],
    ['t-download', d.download],
    ['t-download-note', d.downloadNote],
    ['t-source-link', d.sourceLink],
    ['t-features-title', d.featuresTitle],
    ['t-shots-title', d.shotsTitle],
    ['t-privacy-title', d.privacyTitle],
    ['t-privacy-text', d.privacyText],
    ['t-langs-title', d.langsTitle],
    ['t-langs-text', d.langsText],
    ['t-verify-title', d.verifyTitle],
    ['t-verify-text', d.verifyText],
    ['t-license-title', d.licenseTitle],
    ['t-license-text', d.licenseText],
    ['t-license-link', d.licenseLink],
  ];
  for (const [id, текст] of простые) html = вставить(html, id, текст);

  const возможности = d.features
    .map(
      ([голова, тело]) =>
        `\n      <div class="feature"><h3>${экранировать(голова)}</h3><p>${экранировать(тело)}</p></div>`,
    )
    .join('');
  html = заполнить(html, 'features', `${возможности}\n    `);

  // Снимки экрана переведены не для всех языков: берём английские, но
  // подписи ставим на нужном. Лучше английский снимок с русской
  // подписью, чем битая картинка.
  const снимки = d.shots
    .map(
      ([файл, голова, тело]) =>
        `\n      <div class="shot">` +
        `<img src="../screenshots/en/${файл}.png" alt="${экранировать(голова)}"` +
        ` loading="${файл === 'summary' ? 'eager' : 'lazy'}" width="1044" height="721">` +
        `<h3>${экранировать(голова)}</h3><p>${экранировать(тело)}</p></div>`,
    )
    .join('');
  html = заполнить(html, 'shots', `${снимки}\n    `);

  // Страница уехала на уровень глубже: пути к общим файлам поправить.
  html = html.replace(/src="i18n\.js"/g, 'src="../i18n.js"');
  html = html.replace(/src="schet\.js"/g, 'src="../schet.js"');

  html = html.replace(
    '<head>',
    '<head>\n<!-- Собрано из ../index.html: node tools/собрать-языки.mjs.\n' +
      '     Правки вносить в исходник, иначе они пропадут при следующей сборке. -->',
  );
  return html;
}

const I18N = словари();
const исходник = readFileSync(СТРАНИЦА, 'utf8');

for (const язык of ЯЗЫКИ) {
  const d = I18N[язык];
  if (!d) throw new Error(`в i18n.js нет словаря «${язык}»`);
  const папка = join(КОРЕНЬ, 'docs', язык);
  mkdirSync(папка, { recursive: true });
  const html = собрать(язык, d, исходник);
  writeFileSync(join(папка, 'index.html'), html);

  const видно = html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  console.log(`[ок] docs/${язык}/index.html  lang=${d.htmlLang}  ${видно.length} знаков текста`);
}

// Корневая английская страница: ей тоже нужны ссылки на новые адреса.
let корень = заменитьСсылки(исходник, 'en');
if (!корень.includes('property="og:locale"')) {
  корень = корень.replace(
    '<meta property="og:type" content="website">',
    '<meta property="og:type" content="website">\n<meta property="og:locale" content="en">',
  );
}
writeFileSync(СТРАНИЦА, корень);
console.log('[ок] docs/index.html: ссылки на языковые версии обновлены');
