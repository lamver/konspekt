#!/usr/bin/env node
/* Встраивает английский текст прямо в разметку страницы.
 *
 * Зачем. Страница рисуется скриптом: в исходном HTML узлы пустые, и
 * робот без JavaScript видит 96 знаков вместо описания продукта. Ни
 * слова «transcribe», «local», «offline» — то есть ровно тех слов, по
 * которым нас должны находить. Google скрипты выполняет отложенно и не
 * всегда, а Bing, Яндекс и сборщики ссылок в мессенджерах чаще читают
 * только исходный текст.
 *
 * Как. Берём английские строки из того же `i18n.js`, из которого их
 * берёт страница в браузере, и кладём в те же узлы. Переключение языка
 * работает как прежде: скрипт перезаписывает содержимое поверх.
 *
 * Почему генератор, а не руками. Текст, вписанный в разметку руками,
 * разойдётся со словарём при первой же правке формулировки, и никто
 * этого не заметит: в браузере всё выглядит верно, потому что скрипт
 * поверх подставит новое. Разойдётся только то, что видит робот, то
 * есть именно то, ради чего всё делается.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const КОРЕНЬ = join(dirname(fileURLToPath(import.meta.url)), '..');
const СТРАНИЦА = join(КОРЕНЬ, 'docs', 'index.html');
const СЛОВАРЬ = join(КОРЕНЬ, 'docs', 'i18n.js');

/** Английские строки из словаря страницы. */
function английский() {
  const код = readFileSync(СЛОВАРЬ, 'utf8');
  // Файл не модуль, а простое присваивание: выполняем его и забираем
  // I18N. Читать словарь регулярными выражениями означало бы завести
  // второй разбор, который разойдётся с первым.
  const fn = new Function(`${код}\n;return I18N;`);
  const I18N = fn();
  if (!I18N?.en) throw new Error('в i18n.js нет английского словаря');
  return I18N.en;
}

/** Текст, безопасный для вставки в разметку. */
function экранировать(текст) {
  return String(текст)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/** Кладёт текст внутрь узла с этим id, не трогая его атрибуты. */
function вставить(html, id, текст) {
  // Ищем открывающий тег с нужным id и его закрывающую пару. Атрибуты
  // сохраняем: там классы, ссылки и data-* для учёта скачиваний.
  const re = new RegExp(`(<([a-z0-9]+)\\b[^>]*\\bid="${id}"[^>]*>)([\\s\\S]*?)(</\\2>)`, 'i');
  if (!re.test(html)) throw new Error(`на странице нет узла с id="${id}"`);
  return html.replace(re, (_, откр, тег, было, закр) => `${откр}${экранировать(текст)}${закр}`);
}

/** Заменяет содержимое узла готовой разметкой. */
function заполнить(html, id, разметка) {
  const re = new RegExp(`(<([a-z0-9]+)\\b[^>]*\\bid="${id}"[^>]*>)([\\s\\S]*?)(</\\2>)`, 'i');
  if (!re.test(html)) throw new Error(`на странице нет узла с id="${id}"`);
  return html.replace(re, (_, откр, тег, было, закр) => `${откр}${разметка}${закр}`);
}

const d = английский();
let html = readFileSync(СТРАНИЦА, 'utf8');

// Заголовок первого уровня: скрипт снимает с него «Konspekt — », делаем
// то же самое, иначе робот и человек увидят разные заголовки.
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

// Карточки возможностей: та же разметка, что строит скрипт.
const возможности = d.features
  .map(
    ([голова, тело]) =>
      `\n      <div class="feature"><h3>${экранировать(голова)}</h3><p>${экранировать(тело)}</p></div>`,
  )
  .join('');
html = заполнить(html, 'features', `${возможности}\n    `);

// Снимки: пути и размеры повторяют то, что делает скрипт, иначе при
// переключении языка картинки дёрнутся или поедет вёрстка.
const снимки = d.shots
  .map(
    ([файл, голова, тело]) =>
      `\n      <div class="shot">` +
      `<img src="screenshots/en/${файл}.png" alt="${экранировать(голова)}"` +
      ` loading="${файл === 'summary' ? 'eager' : 'lazy'}" width="1044" height="721">` +
      `<h3>${экранировать(голова)}</h3><p>${экранировать(тело)}</p></div>`,
  )
  .join('');
html = заполнить(html, 'shots', `${снимки}\n    `);

writeFileSync(СТРАНИЦА, html);

// Считаем то же, что считает робот: текст без скриптов и разметки.
const видно = html
  .replace(/<script[\s\S]*?<\/script>/gi, ' ')
  .replace(/<style[\s\S]*?<\/style>/gi, ' ')
  .replace(/<!--[\s\S]*?-->/g, ' ')
  .replace(/<[^>]+>/g, ' ')
  .replace(/\s+/g, ' ')
  .trim();

console.log(`текста без скриптов: ${видно.length} знаков`);
console.log('ключевые слова:');
for (const слово of ['transcri', 'local', 'record', 'offline', 'privacy', 'Whisper', 'Granola']) {
  const есть = видно.toLowerCase().includes(слово.toLowerCase());
  console.log(`  ${есть ? 'есть' : 'НЕТ '}  ${слово}`);
}
