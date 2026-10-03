#!/usr/bin/env node
/* Снимает ролик.html покадрово через Chrome без окна.
 *
 * Кадр рисует сама страница: window.кадр(t). Мы только говорим, какой
 * момент нужен, и делаем снимок. Так ролик не зависит от скорости
 * машины: тормозит компьютер — кадры просто снимаются дольше.
 *
 * Запуск: node promo/снять.mjs [кадров_в_секунду] [страница] [параметры] [папка_кадров]
 *   node promo/снять.mjs                       — первый ролик, 30 кадров в секунду
 *   node promo/снять.mjs 30 обзор/ролик.html "язык=en" out/обзор-en/кадры
 * Страница и папка кадров — относительно promo/. По умолчанию кадры
 * кладутся в promo/out/кадры/00000.png и дальше.
 */
import { spawn } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ПАПКА = dirname(fileURLToPath(import.meta.url));
const ЧАСТОТА = Number(process.argv[2] || 30);
const СТРАНИЦА = process.argv[3] || 'ролик.html';
const ПАРАМЕТРЫ = process.argv[4] ? `&${process.argv[4]}` : '';
const КАДРЫ = join(ПАПКА, process.argv[5] || join('out', 'кадры'));
const ПОРТ = 9333;

const БРАУЗЕРЫ = [
  process.env.KONSPEKT_BROWSER,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
].filter(Boolean);
const браузер = БРАУЗЕРЫ.find((п) => existsSync(п));
if (!браузер) throw new Error('нет Chrome или Edge: укажите путь в KONSPEKT_BROWSER');

rmSync(КАДРЫ, { recursive: true, force: true });
mkdirSync(КАДРЫ, { recursive: true });

const профиль = join(tmpdir(), `konspekt-promo-${process.pid}`);
const chrome = spawn(браузер, [
  '--headless=new', `--remote-debugging-port=${ПОРТ}`, `--user-data-dir=${профиль}`,
  '--hide-scrollbars', '--force-device-scale-factor=1', '--window-size=1920,1080',
  '--no-first-run', '--disable-gpu', 'about:blank',
], { stdio: 'ignore' });

const пауза = (мс) => new Promise((r) => setTimeout(r, мс));

async function цель() {
  for (let i = 0; i < 100; i++) {
    try {
      const список = await (await fetch(`http://127.0.0.1:${ПОРТ}/json/list`)).json();
      const страница = список.find((с) => с.type === 'page');
      if (страница) return страница.webSocketDebuggerUrl;
    } catch { /* браузер ещё поднимается */ }
    await пауза(100);
  }
  throw new Error('браузер не ответил');
}

const ws = new WebSocket(await цель());
await new Promise((r, e) => { ws.onopen = r; ws.onerror = e; });
let номер = 0;
const ждут = new Map();
ws.onmessage = (м) => {
  const д = JSON.parse(м.data);
  if (д.id && ждут.has(д.id)) {
    const { r, e } = ждут.get(д.id); ждут.delete(д.id);
    д.error ? e(new Error(JSON.stringify(д.error))) : r(д.result);
  }
};
const cdp = (method, params = {}) => new Promise((r, e) => {
  const id = ++номер; ждут.set(id, { r, e });
  ws.send(JSON.stringify({ id, method, params }));
});
const выполнить = async (код) => {
  const о = await cdp('Runtime.evaluate', { expression: код, awaitPromise: true, returnByValue: true });
  if (о.exceptionDetails) throw new Error(о.exceptionDetails.exception?.description || 'ошибка в странице');
  return о.result.value;
};

try {
  await cdp('Page.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
  const адрес = pathToFileURL(join(ПАПКА, СТРАНИЦА)).href + '?съёмка=1' + encodeURI(ПАРАМЕТРЫ);
  await cdp('Page.navigate', { url: адрес });
  // Страница готова, когда знает свою длину: обзорный ролик узнаёт её,
  // только подгрузив сценарий и тексты языка.
  for (let i = 0; i < 100 && !(await выполнить('typeof window.кадр === "function" && window.ДЛИНА > 0').catch(() => false)); i++) await пауза(100);
  // Снимки программы должны догрузиться до первого кадра.
  await выполнить('Promise.all(Array.from(document.images).map((i) => i.complete ? 0 : new Promise((r) => { i.onload = i.onerror = r; })))');
  await выполнить('document.fonts.ready.then(() => document.fonts.size)');
  const длина = await выполнить('window.ДЛИНА');
  const всего = Math.round(длина * ЧАСТОТА);
  const начало = Date.now();
  for (let i = 0; i < всего; i++) {
    await выполнить(`window.кадр(${i / ЧАСТОТА})`);
    const { data } = await cdp('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    writeFileSync(join(КАДРЫ, `${String(i).padStart(5, '0')}.png`), Buffer.from(data, 'base64'));
    if (i % ЧАСТОТА === 0) process.stdout.write(`\rкадр ${i}/${всего}`);
  }
  console.log(`\rснято ${всего} кадров за ${((Date.now() - начало) / 1000).toFixed(0)} с`);
} finally {
  ws.close();
  chrome.kill();
  await пауза(300);
  rmSync(профиль, { recursive: true, force: true });
}
