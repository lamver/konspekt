// Плашка «Купить лицензию» и масштаб интерфейса в окне.
//
// Гоняем настоящий web/app.js в node: копия разошлась бы с кодом молча.
//
// Что сторожим. Плашка обязана висеть без лицензии и пропадать с ней:
// плашка у заплатившего человека злит сильнее, чем её отсутствие у не
// заплатившего. Масштаб обязан ходить ступенями туда и обратно, держать
// пределы и ловить Ctrl + плюс на любой раскладке.

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const код = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
const словарь = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'web', 'i18n', 'ru.json'), 'utf8'));

let провалов = 0;
function проверить(условие, что) {
  console.log(`[${условие ? 'ok' : 'FAIL'}] ${что}`);
  if (!условие) провалов += 1;
}

function узел() {
  const классы = new Set();
  return {
    hidden: false, textContent: '', value: '', disabled: false, style: {}, dataset: {},
    classList: {
      toggle(к, да) { if (да === undefined ? !классы.has(к) : да) классы.add(к); else классы.delete(к); },
      add(к) { классы.add(к); }, remove(к) { классы.delete(к); }, contains: (к) => классы.has(к),
    },
    addEventListener() {}, removeEventListener() {}, setAttribute() {}, removeAttribute() {},
    querySelector: () => узел(), querySelectorAll: () => [], focus() {},
  };
}

const вызовы = [];
let ответЛицензии = { licensed: false };
let ответАктивации = { ok: false, error: 'signature' };

const корень = узел();
const песочница = {
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  document: {
    getElementById: () => узел(), createElement: () => узел(),
    querySelector: () => узел(), querySelectorAll: () => [],
    body: узел(), documentElement: корень, addEventListener() {},
  },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  navigator: { userAgent: 'test' },
  pywebview: {
    api: {
      set_ui_zoom: async (z) => { вызовы.push(['set_ui_zoom', z]); return z; },
      license_state: async () => { вызовы.push(['license_state']); return ответЛицензии; },
      activate_license: async (k) => { вызовы.push(['activate_license', k]); return ответАктивации; },
      open_buy_page: async () => { вызовы.push(['open_buy_page']); return true; },
    },
  },
};
песочница.addEventListener = () => {};
песочница.matchMedia = () => ({ matches: false, addEventListener() {} });
песочница.window = песочница;
песочница.globalThis = песочница;
vm.createContext(песочница);
vm.runInContext(код + `
globalThis.__state = state; globalThis.__ui = ui;
globalThis.__z = { applyZoom, setZoom, stepZoom, zoomKeyStep, currentZoom };
globalThis.__l = { refreshLicense, renderLicense, activateLicense };
`, песочница, { filename: 'app.js' });
vm.runInContext('i18n.dict = globalThis.__словарь;', Object.assign(песочница, { __словарь: словарь }));

const ui = песочница.__ui;
const state = песочница.__state;
const z = песочница.__z;
const l = песочница.__l;
for (const имя of ['licenseBar', 'licenseOwned', 'licenseMissing', 'licenseWho',
  'licenseKey', 'licenseResult', 'licenseActivate', 'zoomValue', 'toast', 'toastText']) {
  ui[имя] = узел();
}

(async () => {
  // --- плашка -------------------------------------------------------------
  ответЛицензии = { licensed: false };
  await l.refreshLicense();
  проверить(ui.licenseBar.hidden === false, 'без лицензии плашка видна');
  проверить(ui.licenseMissing.hidden === false && ui.licenseOwned.hidden === true,
    'без лицензии в настройках «Лицензии нет» и кнопка «Купить»');

  state.licenseBarHidden = true;
  l.renderLicense();
  проверить(ui.licenseBar.hidden === true, 'крестик прячет плашку до перезапуска');
  state.licenseBarHidden = false;

  ответЛицензии = { licensed: true, license: { to: 'Иван Петров', email: 'ivan@example.com', seats: 1, id: '7f0c' } };
  await l.refreshLicense();
  проверить(ui.licenseBar.hidden === true, 'с лицензией плашки нет');
  проверить(ui.licenseOwned.hidden === false && ui.licenseMissing.hidden === true,
    'с лицензией в настройках «Лицензия активна»');
  проверить(ui.licenseWho.textContent.includes('Иван Петров') && ui.licenseWho.textContent.includes('7f0c'),
    `видно, кому выдана: «${ui.licenseWho.textContent}»`);

  // Мост не ответил: денег не просим.
  песочница.pywebview.api.license_state = async () => { throw new Error('мост упал'); };
  await l.refreshLicense();
  проверить(ui.licenseBar.hidden === true, 'мост не ответил: плашку у возможно заплатившего не показываем');
  песочница.pywebview.api.license_state = async () => ответЛицензии;

  // --- ввод ключа ---------------------------------------------------------
  ответЛицензии = { licensed: false };
  await l.refreshLicense();
  ui.licenseKey.value = '  ';
  вызовы.length = 0;
  await l.activateLicense();
  проверить(!вызовы.some((в) => в[0] === 'activate_license') && ui.licenseResult.textContent === словарь.license.error.empty,
    'пустой ключ до программы не доходит, человеку сказано вставить ключ');

  ui.licenseKey.value = 'KSPK1.испорчен';
  ответАктивации = { ok: false, error: 'signature' };
  await l.activateLicense();
  проверить(ui.licenseResult.textContent === словарь.license.error.signature,
    'испорченный ключ: понятное объяснение, а не код ошибки');
  проверить(ui.licenseBar.hidden === false, 'после неудачной вставки плашка на месте');
  проверить(ui.licenseKey.value === 'KSPK1.испорчен', 'неподошедший ключ остаётся в поле, чтобы поправить');

  ответАктивации = { ok: false, error: 'expired', detail: '2026-12-31' };
  await l.activateLicense();
  проверить(ui.licenseResult.textContent.includes('2026-12-31'), 'истёкший ключ: названа дата');

  ответАктивации = { ok: true, licensed: true, license: { to: 'Иван', email: 'i@e.com', seats: 1 } };
  ui.licenseKey.value = 'KSPK1.хороший';
  await l.activateLicense();
  проверить(ui.licenseBar.hidden === true, 'после настоящего ключа плашка исчезла сразу, без перезапуска');
  проверить(ui.licenseKey.value === '', 'поле ключа очищено');
  проверить(ui.licenseResult.textContent === словарь.license.activated, 'сказано спасибо');

  // --- масштаб -------------------------------------------------------------
  z.applyZoom(1);
  проверить(корень.style.zoom === '', '100%: свойство zoom не задано вовсе');
  проверить(z.stepZoom(1) === 1.1 && z.stepZoom(1) === 1.25 && z.stepZoom(1) === 1.5,
    'крупнее ступенями: 110%, 125%, 150%');
  проверить(корень.style.zoom === '1.5' && ui.zoomValue.textContent === '150%', 'масштаб применён ко всему окну и показан');
  z.stepZoom(-1); z.stepZoom(-1); z.stepZoom(-1);
  проверить(z.currentZoom() === 1, 'три раза мельче возвращают ровно к 100%');
  for (let i = 0; i < 20; i += 1) z.stepZoom(1);
  проверить(z.currentZoom() === 2, 'крупнее 200% не растёт');
  for (let i = 0; i < 20; i += 1) z.stepZoom(-1);
  проверить(z.currentZoom() === 0.7, 'мельче 70% не уменьшается');
  проверить(z.applyZoom('мусор') === 1 && z.applyZoom(99) === 2, 'мусор и огромное число из настроек не ломают окно');

  вызовы.length = 0;
  z.setZoom(1.25); z.setZoom(1.5); z.setZoom(1.75);
  await new Promise((r) => setTimeout(r, 600));
  const записи = вызовы.filter((в) => в[0] === 'set_ui_zoom');
  проверить(записи.length === 1 && записи[0][1] === 1.75,
    'быстрая прокрутка колесом пишет в настройки один раз, последнее значение');

  // --- клавиши -------------------------------------------------------------
  const к = (key, code, extra = {}) => z.zoomKeyStep({ key, code, ctrlKey: true, altKey: false, ...extra });
  проверить(к('=', 'Equal') === 1, 'Ctrl + «=» (плюс без Shift) крупнее');
  проверить(к('+', 'Equal') === 1, 'Ctrl + «+» крупнее');
  проверить(к('+', 'NumpadAdd') === 1, 'Ctrl + плюс на цифровой клавиатуре крупнее');
  проверить(к('-', 'Minus') === -1 && к('-', 'NumpadSubtract') === -1, 'Ctrl + минус мельче');
  проверить(к('0', 'Digit0') === 0 && к('0', 'Numpad0') === 0, 'Ctrl + 0 сбрасывает');
  проверить(к('ъ', 'Equal') === 1, 'на русской раскладке та же клавиша тоже работает');
  проверить(z.zoomKeyStep({ key: '=', code: 'Equal', ctrlKey: false }) === null, 'без Ctrl «=» просто печатается');
  проверить(к('n', 'KeyN') === null, 'Ctrl + N остаётся за «Новой встречей»');
  проверить(к('=', 'Equal', { altKey: true }) === null, 'Ctrl + Alt + = не трогаем: это AltGr на части раскладок');

  console.log(провалов ? `\nПровалов: ${провалов}` : '\nПлашка и масштаб работают как надо.');
  process.exit(провалов ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
