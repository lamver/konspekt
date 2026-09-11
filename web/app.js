/* Konspekt — фронт.
 *
 * Без фреймворков и сборки: приложение маленькое, а PyInstaller любит
 * статику. Состояние держим в одном объекте, рендер точечный.
 */

'use strict';

/* --- Локализация ---------------------------------------------------------
 *
 * Словари лежат в web/i18n/<lang>.json, структура — вложенные объекты
 * с точечными ключами (titlebar.settings). Язык выбирается в настройках
 * и хранится в settings.json на стороне Python; здесь только читаем его
 * из state.settings.language, подставленного при старте.
 */

const SUPPORTED_LANGS = ['ru', 'en', 'es', 'sr'];
let i18n = { lang: 'ru', dict: {} };

/**
 * Загрузить словарь языка.
 *
 * Не fetch(): под pywebview страница открыта с file://, и запрос к
 * соседнему JSON там режется как кросс-origin и тихо проваливается.
 * Словарь идёт через мост, как и любые другие данные из Python.
 */
async function loadDict(lang) {
  // Три попытки с паузой. Мост pywebview иногда отвечает не с первого
  // раза сразу после старта окна, а словарь нужен один раз за запуск:
  // не доехал — и человек до перезапуска сидит с ключами вместо слов.
  // Дешевле подождать пару сотен миллисекунд, чем оставить его без
  // подписей на кнопках.
  for (let попытка = 1; попытка <= 3; попытка += 1) {
    try {
      const dict = await api.get_i18n_dict(lang);
      if (dict && typeof dict === 'object' && Object.keys(dict).length) {
        return dict;
      }
      throw new Error('пустой словарь');
    } catch (e) {
      console.error(`Не удалось загрузить словарь ${lang} (попытка ${попытка}):`, e);
      if (попытка < 3) {
        await new Promise((r) => setTimeout(r, 150 * попытка));
      }
    }
  }
  return null;
}

/**
 * Перевести ключ вида "prefs.tab.audio" с подстановкой {placeholder}.
 *
 * Отсутствующий ключ — не повод падать: возвращаем сам ключ, чтобы
 * дыра была видна на экране и её нашли, а не тихая пустая строка.
 * Текстов в коде сотни, и большинство из них без ключа превратились бы
 * в пустоту: пустая плашка хуже плашки с ключом.
 *
 * А вот там, где надпись уже есть в разметке (кнопки, подписи,
 * подсказки из index.html), ключ показывать нельзя: человек увидит
 * «recording.start» вместо «Начать запись», если словарь опоздал к
 * первой отрисовке. Для таких мест есть `tЕслиЕсть`.
 */
function t(key, vars) {
  const parts = key.split('.');
  let node = i18n.dict;
  for (const p of parts) {
    if (node && typeof node === 'object' && p in node) node = node[p];
    else { node = null; break; }
  }
  if (typeof node !== 'string') return key;
  if (!vars) return node;
  return node.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m));
}

/**
 * Перевод для текста, который уже написан в разметке.
 *
 * Отвечает null, пока словарь не доехал через мост. Тот, кто
 * рисует, в этом случае оставляет то, что уже лежит в index.html:
 * там человеческая надпись, и она лучше голого ключа.
 *
 * Разница с `t()` в том, что здесь есть запасной текст, а в
 * плашках и сообщениях, рождающихся в коде, его нет.
 */
function tЕслиЕсть(key, vars) {
  if (!i18n.dict || !Object.keys(i18n.dict).length) return null;
  const значение = t(key, vars);
  // Ключ вернулся как есть: перевода нет. В разметке лежит
  // русский текст, и он честнее ключа на любом языке.
  return значение === key ? null : значение;
}

/** Русское/славянское склонение через ключи вида foo.one/few/many. */
function tPlural(baseKey, n, vars) {
  const forms = t(`${baseKey}.one`) !== `${baseKey}.one`
    ? { one: t(`${baseKey}.one`), few: t(`${baseKey}.few`), many: t(`${baseKey}.many`) }
    : null;
  if (!forms) return '';
  return plural(n, forms.one, forms.few, forms.many);
}

/** Пройти по DOM и подставить переводы в data-i18n / data-i18n-placeholder.
 *
 * Если `t()` вернул null (словарь ещё не доехал), узел не трогаем:
 * в разметке уже лежит человеческая надпись, и она лучше голого
 * ключа вроде «recording.start». */
function applyI18n(root = document) {
  root.querySelectorAll('[data-i18n]').forEach((node) => {
    const key = node.dataset.i18n;
    const html = tЕслиЕсть(key);
    if (html === null) return;
    if (node.dataset.i18nHtml === '1') node.innerHTML = html;
    else node.textContent = html;
  });
  root.querySelectorAll('[data-i18n-placeholder]').forEach((node) => {
    const текст = tЕслиЕсть(node.dataset.i18nPlaceholder);
    if (текст !== null) node.placeholder = текст;
  });
  root.querySelectorAll('[data-i18n-title]').forEach((node) => {
    const текст = tЕслиЕсть(node.dataset.i18nTitle);
    if (текст !== null) node.title = текст;
  });
  root.querySelectorAll('[data-i18n-aria-label]').forEach((node) => {
    const текст = tЕслиЕсть(node.dataset.i18nAriaLabel);
    if (текст !== null) node.setAttribute('aria-label', текст);
  });
}

/** Сменить язык интерфейса: подгружаем словарь и перерисовываем DOM. */
async function setLanguage(lang, { persist = true } = {}) {
  if (!SUPPORTED_LANGS.includes(lang)) lang = 'ru';
  let dict = await loadDict(lang);
  if (!dict) {
    // Словарь не доехал: мост подвис или окно ещё не догрузилось. Уйти
    // молча нельзя - t() тогда возвращает сами ключи, и человек видит
    // экран из 'prefs.tab.dictation' вместо слов. Русский лежит прямо в
    // сборке, и откатиться на него честнее, чем показывать латиницу.
    if (lang === 'ru') return;
    dict = await loadDict('ru');
    if (!dict) return;
    // Язык в настройках не трогаем: сбой словаря дело временное, а
    // запись переставила бы человеку язык навсегда, и после
    // перезапуска он получил бы русский вместо своего.
    i18n = { lang: 'ru', dict };
    document.documentElement.setAttribute('lang', 'ru');
    applyI18n();
    refreshDynamicTexts();
    syncLanguageSwitch();
    return;
  }
  i18n = { lang, dict };
  document.documentElement.setAttribute('lang', lang === 'sr' ? 'sr-Latn' : lang);
  applyI18n();
  refreshDynamicTexts();
  syncLanguageSwitch();
  if (persist) await api.set_language(lang);
}

/** Перерисовать тексты, которые app.js генерирует сам, а не через data-i18n. */
function refreshDynamicTexts() {
  // null значит «словарь ещё не доехал»: оставляем то, что уже
  // написано в разметке.
  if (ui.recLabel) {
    const надпись = state.isRecording
      ? tЕслиЕсть('recording.stop') : tЕслиЕсть('recording.start');
    if (надпись !== null) ui.recLabel.textContent = надпись;
  }
  if (ui.summaryRun) {
    const has = ui.summaryBody && !ui.summaryBody.hidden && ui.summaryBody.innerHTML.trim();
    const надпись = has ? tЕслиЕсть('summary.redo') : tЕслиЕсть('summary.run');
    if (надпись !== null) ui.summaryRun.textContent = надпись;
  }
  if (state.currentId && state.current) renderMeta(state.current);
  if (ui.list) renderMeetingList();
}

const state = {
  meetings: [],
  currentId: null,
  current: null,
  isRecording: false,
  recordingId: null,
  startedAt: null,
  filter: '',
  // Найденное в расшифровках: запрос, к которому относится результат,
  // и карта «встреча → совпадения». Отдельно от filter, потому что
  // ответ базы приходит с задержкой и может отстать от строки поиска.
  search: { query: '', byMeeting: null, timer: null },
  model: {},
  pinned: true,
  imports: [],
  llm: {},
  llmBusy: false,
  // Какую встречу сейчас разбирает модель.
  busyMeetingId: null,
  summaryText: '',
  // Идёт ли досчёт кусков, не влезших в живую очередь распознавания,
  // и какой встрече после этого полагаются автоматические заметки.
  backfilling: false,
  pendingSummaryId: null,
};

const el = (id) => document.getElementById(id);
const ui = {};

// Пауза, после которой реплика считается новой, в секундах.
//
// Порог не выбран на глаз, он равен SILENCE_TAIL из app/asr/vad.py.
// Распознавание вообще не отдаёт фразу, пока не услышит столько тишины,
// поэтому зазор меньше этого числа бывает ровно в одном случае: речь
// шла без пауз, и её разрезали принудительно. Такие куски склеивать
// обязательно, иначе фраза рвётся посередине слова.
//
// А всё, что больше, это место, где программа уже решила «человек
// договорил». Раньше здесь стояло 1.5, вдвое больше, и окно склеивало
// назад то, что распознавание успело разделить: скажешь «ок», начнёшь
// новую мысль, а она прилипает к предыдущей репликой. На боевой базе
// (19 встреч, 2339 пар подряд идущих реплик одного голоса) таких
// лишних склеек 837, то есть 35% всех пар.
const TURN_GAP = 0.6;

let saveTimer = null;
let timerInterval = null;
let levelResetTimer = null;

/* --- Мост к Python ------------------------------------------------------ */

/** pywebview появляется асинхронно; ждём его перед первым запросом. */
function apiReady() {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) return resolve();
    window.addEventListener('pywebviewready', () => resolve(), { once: true });
    // Подстраховка: событие могло уйти до подписки.
    const poll = setInterval(() => {
      if (window.pywebview && window.pywebview.api) {
        clearInterval(poll);
        resolve();
      }
    }, 40);
  });
}

const api = new Proxy({}, {
  get: (_t, name) => async (...args) => {
    await apiReady();
    try {
      return await window.pywebview.api[name](...args);
    } catch (err) {
      console.error(`${t('api.call_error')} ${String(name)}:`, err);
      return null;
    }
  },
});

/* --- Приём событий из Python -------------------------------------------- */

window.__konspekt_event = function (payload) {
  switch (payload.topic) {
    case 'meetings.changed':
      loadMeetings();
      break;
    case 'recording.started':
      state.isRecording = true;
      state.recordingId = payload.meeting_id;
      state.startedAt = payload.started_at ? payload.started_at * 1000 : Date.now();
      renderRecordingState();
      startTimer();
      // Предложение «не записать ли» стало бессмысленным: запись пошла.
      // Плашка, предлагающая начать уже начатое, только сбивает с толку.
      hideNoticedTalkAsk();
      // Статус в шапке должен сразу стать «Запись», а не остаться прежним.
      if (payload.meeting_id === state.currentId) refreshMeta(payload.meeting_id);
      break;
    case 'recording.stopped':
      state.isRecording = false;
      state.recordingId = null;
      // Запись кончилась: недоговорённое дошлётся настоящими репликами,
      // а висящий черновик — это текст, который уже никогда не уточнится.
      clearDraft();
      // Запись только что появилась, значит и переслушивать теперь есть
      // что. state.hasAudio решает только для будущих реплик — уже
      // отрисованные кнопки .turn__play/.turn__lang были скрыты в момент
      // появления реплики (пока шла запись, hasAudio был false) и сами
      // не перерисуются, поэтому снимаем hidden с них здесь же. Без
      // этого кнопки появлялись только после переключения на другую
      // встречу и обратно — regressed в 0.6, когда live-транскрипт стал
      // рисовать реплики сразу по ходу записи.
      if (payload.meeting_id === state.currentId) {
        state.hasAudio = true;
        ui.transcript.querySelectorAll('.turn__play, .turn__lang').forEach((btn) => {
          btn.hidden = false;
        });
      }
      renderRecordingState();
      stopTimer();
      // Вопрос про системный звук относился к этой записи: она кончилась,
      // отвечать больше не на что. Оставить плашку висеть значило бы
      // предложить выключить то, что уже не пишется.
      hideForeignSpeechAsk();
      // Именно refreshMeta, а не selectMeeting: перезагрузка встречи
      // затёрла бы текст, который пользователь печатает прямо сейчас.
      if (state.currentId) refreshMeta(state.currentId);
      if (payload.backfill) {
        // Часть речи ещё досчитывается из записи. Заметки по такой
        // расшифровке потеряли бы ровно те куски, ради которых всё
        // и затевалось, поэтому ждём конца докатки.
        state.backfilling = true;
        state.pendingSummaryId = payload.meeting_id;
      } else {
        maybeAutoSummary(payload.meeting_id);
      }
      break;
    case 'recording.level':
      renderLevels(payload.me, payload.them);
      break;
    case 'recording.silent':
      // Дорожка немая, но запись идёт: только предупреждаем. Трогать
      // состояние кнопки нельзя, иначе окно решит, что записи нет, и
      // кнопка перестанет слушаться посреди живой встречи.
      showToast(payload.message || t('recording.silent'), 10000);
      break;
    case 'recording.foreign_speech':
      // В системном звуке слышен разговор. Ничего не меняем сами:
      // решает человек. Запись при этом идёт дальше, состояние кнопки
      // не трогаем.
      showForeignSpeechAsk(payload);
      break;
    case 'system.speech_noticed':
      // Разговор идёт, а запись не включена. Ничего не пишем, пока
      // человек не согласится: это предложение, а не действие.
      showNoticedTalkAsk(payload);
      break;
    case 'recording.error':
      // Запись не началась: сообщаем прямо, иначе человек будет думать,
      // что встреча пишется, и потеряет её.
      showToast(payload.message || t('recording.error'));
      state.isRecording = false;
      state.recordingId = null;
      renderRecordingState();
      stopTimer();
      break;
    case 'meeting.updated':
      if (payload.meeting && payload.meeting.id === state.currentId) {
        renderMeta(payload.meeting);
      }
      break;
    case 'transcript.segment':
      // Реплика распознана: показываем сразу, если открыта её встреча.
      if (payload.segment && payload.segment.meeting_id === state.currentId) {
        // Готовая реплика приходит на смену черновику того же куска речи.
        clearDraft(payload.segment.speaker);
        appendSegment(payload.segment);
      }
      break;
    case 'transcript.draft':
      // Человек ещё говорит: показываем сказанное, чтобы экран не стоял
      // пустым всю длинную фразу.
      if (payload.segment && payload.segment.meeting_id === state.currentId) {
        showDraft(payload.segment);
      }
      break;
    case 'model.download':
      onModelProgress(payload);
      break;
    case 'app.new_version':
      showUpdateNote(payload);
      break;
    case 'app.update_state':
      onUpdateState(payload);
      break;
    case 'llm.download':
      // Модель приезжает при первом запросе, и это полтора гигабайта.
      // Без процентов ожидание неотличимо от зависания.
      if (payload.state === 'downloading') {
        setSummaryStatus(t('summary.downloading_model', { percent: payload.percent }));
        // И в боковой панели тоже: заголовок саммари видно только на
        // своей вкладке, а ждать полтора гигабайта человек будет где
        // угодно.
        showModelLoad(payload.bytes, payload.total, t('summary.downloading_model_title'),
          t('summary.download_hint'));
      } else if (payload.state === 'error') {
        hideModelLoad();
        showToast(payload.message || t('summary.download_error'));
      } else {
        hideModelLoad();
        setSummaryStatus(t('summary.model_ready'));
        refreshLlmStatus();
      }
      break;
    case 'import.changed':
      renderImports(payload.tasks || []);
      break;
    case 'import.progress':
      onImportProgress(payload.task);
      break;
    case 'summary.chunk':
      onSummaryChunk(payload);
      break;
    case 'summary.status':
      // Длинная встреча разбирается по частям: без этой строки
      // экран молчит минутами и выглядит зависшим. Пишем в шапку, а не
      // в тело: тело к этому моменту может уже печатать текст.
      if (payload.meeting_id === state.currentId) setSummaryStatus(payload.text);
      break;
    case 'summary.ready':
      onSummaryReady(payload);
      break;
    case 'summary.error':
      setBusy(false);
      renderSummary(state.current ? state.current.summary : '');
      showToast(payload.error || t('summary.error'));
      break;
    case 'chat.chunk':
      onChatChunk(payload);
      break;
    case 'chat.message':
      onChatMessage(payload);
      break;
    case 'chat.error':
      onChatError(payload);
      break;
    case 'recognition.backfill':
      onBackfill(payload);
      break;
  }
};

/**
 * Докатка пропущенного: показать, что расшифровка ещё дополняется.
 *
 * Встреча уже помечена готовой, но куски, не влезшие в живую очередь,
 * досчитываются из записи. Без этой строки человек видит расшифровку с
 * дырами и не знает, что они вот-вот заполнятся сами.
 */
function onBackfill(payload) {
  if (payload.meeting_id !== state.currentId && payload.state !== 'done') {
    // Не своя встреча: молчим, но факт незавершённой докатки помним,
    // чтобы автосаммари не ушло в модель по неполной расшифровке.
    state.backfilling = payload.state !== 'done';
    return;
  }
  if (payload.state === 'done') {
    state.backfilling = false;
    hideToast();
    // Докатка кончилась: теперь расшифровка полная, и можно за заметки.
    if (state.pendingSummaryId) {
      const id = state.pendingSummaryId;
      state.pendingSummaryId = null;
      maybeAutoSummary(id);
    }
    return;
  }
  state.backfilling = true;
  const total = payload.total || 0;
  const done = payload.done || 0;
  showToast(
    payload.state === 'start'
      ? t('summary.backfill_start', { total, piece: tPlural('summary.piece_word', total) })
      : t('summary.backfill_progress', { done, total }),
    60000,
  );
}

/** Русское склонение числительного: 1 кусок, 2 куска, 5 кусков. */
function plural(n, one, few, many) {
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return many;
  const mod10 = n % 10;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}

/* --- Саммари и чат ------------------------------------------------------ */

/**
 * Разметка саммари.
 *
 * Модель отвечает markdown, но тащить ради этого библиотеку в билд
 * незачем: нам нужны только жирный текст, списки и абзацы. Всё остальное
 * показываем как есть.
 */
function renderMarkdown(text) {
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  const out = [];
  let list = null;

  for (const raw of String(text || '').split('\n')) {
    const line = raw.trim();
    if (!line) { if (list) { out.push(`<ul>${list.join('')}</ul>`); list = null; } continue; }

    const bold = esc(line).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    const item = bold.match(/^[-*•]\s+(.*)$/);
    if (item) {
      (list = list || []).push(`<li>${item[1]}</li>`);
      continue;
    }
    if (list) { out.push(`<ul>${list.join('')}</ul>`); list = null; }
    // Строка целиком жирная — это заголовок раздела.
    if (/^<strong>[^<]*<\/strong>$/.test(bold)) out.push(`<h4>${bold}</h4>`);
    else out.push(`<p>${bold}</p>`);
  }
  if (list) out.push(`<ul>${list.join('')}</ul>`);
  return out.join('');
}

function renderSummary(text) {
  const has = Boolean((text || '').trim());
  ui.summaryBody.innerHTML = has ? renderMarkdown(text) : '';
  ui.summaryBody.hidden = !has;
  ui.summaryEmpty.hidden = has;
  ui.summaryRun.textContent = has ? t('summary.redo') : t('summary.run');
  setSummaryStatus('');
}

/** Ход разбора в шапке саммари: чем занята модель прямо сейчас. */
function setSummaryStatus(text) {
  ui.summaryTitle.textContent = text || '';
}

/**
 * Идёт ли сейчас генерация: пока идёт, второй запрос не пустим.
 *
 * Модель одна на всю программу, но считается конкретная встреча.
 * Запоминаем какая: иначе человек перешёл на соседнюю запись и видит
 * там «Остановить», будто считается она.
 */
function setBusy(busy, meetingId) {
  state.llmBusy = busy;
  state.busyMeetingId = busy ? (meetingId || state.currentId) : null;
  syncBusyUi();
}

/** Привести кнопки в соответствие с тем, считается ли открытая встреча. */
function syncBusyUi() {
  const mine = state.llmBusy && state.busyMeetingId === state.currentId;
  ui.summaryRun.disabled = state.llmBusy;
  ui.summaryStop.hidden = !mine;
  ui.chatSend.disabled = state.llmBusy;
}

async function runSummary() {
  if (!state.currentId || state.llmBusy) return;
  await startSummary();
}

/**
 * Саммари сразу после остановки записи.
 *
 * Смысл программы в том, чтобы заметки появлялись сами: человек
 * закрывает звонок и видит готовый разбор, а не ещё одну кнопку.
 */
async function maybeAutoSummary(meetingId) {
  if (!meetingId || meetingId !== state.currentId) return;
  if (state.llmBusy) return;
  const status = state.llm && state.llm.backend ? state.llm : await api.llm_status();
  if (status) state.llm = status;
  if (!status || !status.enabled || !status.auto_summary) return;
  // Последние реплики ещё доезжают из распознавания, а без них
  // саммари потеряет концовку встречи.
  setTimeout(() => {
    if (meetingId === state.currentId && !state.llmBusy) startSummary();
  }, 1500);
}

async function startSummary() {
  // Заметки могли быть только что напечатаны: они важнее расшифровки,
  // и уходить в модель должны вместе с ней.
  flushNotes();
  state.summaryText = '';
  ui.summaryBody.innerHTML = '';
  ui.summaryBody.hidden = false;
  ui.summaryEmpty.hidden = true;
  setBusy(true, state.currentId);
  setSummaryStatus(t('summary.reading'));
  const res = await api.generate_summary(state.currentId);
  if (!res || !res.ok) {
    setBusy(false);
    renderSummary(state.current ? state.current.summary : '');
    showToast((res && res.error) || t('summary.error'));
  }
}

function onSummaryChunk(payload) {
  if (payload.meeting_id !== state.currentId) return;
  if (!state.summaryText) setSummaryStatus(t('summary.generating'));
  state.summaryText = (state.summaryText || '') + payload.text;
  ui.summaryBody.innerHTML = renderMarkdown(state.summaryText);
  ui.summaryBody.scrollTop = ui.summaryBody.scrollHeight;
}

function onSummaryReady(payload) {
  setBusy(false);
  setSummaryStatus('');
  if (payload.meeting_id !== state.currentId) return;
  if (state.current) state.current.summary = payload.summary || '';
  renderSummary(payload.summary || state.summaryText);
}

/* --- Переписка ---------------------------------------------------------- */

function renderChat(messages) {
  ui.chatList.innerHTML = '';
  for (const msg of messages) appendChatMessage(msg);
  ui.chatHint.hidden = messages.length > 0;
  ui.chatClear.hidden = messages.length === 0;
}

function appendChatMessage(msg) {
  // Пустая заготовка под ответ уже могла быть отрисована: обновляем её,
  // а не плодим вторую с тем же идентификатором.
  let node = ui.chatList.querySelector(`[data-msg="${msg.id}"]`);
  if (!node) {
    node = document.createElement('div');
    node.className = `bubble bubble--${msg.role === 'user' ? 'me' : 'bot'}`;
    node.dataset.msg = msg.id;
    ui.chatList.appendChild(node);
  }
  setBubbleText(node, msg.text || '');
  ui.chatHint.hidden = true;
  ui.chatClear.hidden = false;
  scrollChat();
}

function setBubbleText(node, text) {
  if (text.trim()) {
    node.classList.remove('is-waiting');
    node.innerHTML = renderMarkdown(text);
  } else {
    // Пустой ответ значит, что модель ещё думает: показываем это,
    // иначе на экране просто пустой прямоугольник.
    node.classList.add('is-waiting');
    node.innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
  }
}

function onChatChunk(payload) {
  if (payload.meeting_id !== state.currentId) return;
  const node = ui.chatList.querySelector(`[data-msg="${payload.message_id}"]`);
  if (!node) return;
  const text = (node.dataset.text || '') + payload.text;
  node.dataset.text = text;
  setBubbleText(node, text);
  scrollChat();
}

function onChatMessage(payload) {
  if (payload.meeting_id !== state.currentId) return;
  const msg = payload.message || {};
  if (payload.done) setBusy(false);
  const node = ui.chatList.querySelector(`[data-msg="${msg.id}"]`);
  if (node) node.dataset.text = msg.text || '';
  appendChatMessage(msg);
}

function onChatError(payload) {
  setBusy(false);
  showToast(payload.error || t('chat.error'));
  if (payload.meeting_id !== state.currentId) return;
  // Пустой пузырь без ответа выглядит как зависшая программа: убираем.
  const node = ui.chatList.querySelector(`[data-msg="${payload.message_id}"]`);
  if (node) node.remove();
}

function scrollChat() {
  ui.chatList.scrollTop = ui.chatList.scrollHeight;
}

async function sendQuestion() {
  const text = ui.chatText.value.trim();
  if (!text) { showToast(t('chat.empty_question')); return; }
  if (!state.currentId) { showToast(t('chat.no_meeting')); return; }
  if (state.llmBusy) return;
  ui.chatText.value = '';
  resizeChatInput();
  setBusy(true, state.currentId);
  const res = await api.ask(state.currentId, text);
  if (!res || !res.ok) {
    setBusy(false);
    showToast((res && res.error) || t('chat.send_error'));
  }
}

/** Поле ввода растёт под текст, но не больше трети экрана. */
function resizeChatInput() {
  ui.chatText.style.height = 'auto';
  ui.chatText.style.height = Math.min(ui.chatText.scrollHeight, 120) + 'px';
}

async function clearChat() {
  if (!state.currentId) return;
  await api.clear_chat(state.currentId);
  renderChat([]);
}

/* --- Настройки модели --------------------------------------------------- */

const LLM_HINTS = {
  local: () => t('notes_settings.hint_local'),
  remote: () => t('notes_settings.hint_remote'),
  'null': () => t('notes_settings.hint_off'),
};

function renderLlmSettings(status) {
  if (!status) return;
  state.llm = status;
  ui.llmBackend.value = status.backend || 'local';
  ui.llmRemote.hidden = status.backend !== 'remote';
  ui.llmUrl.value = status.base_url || '';
  ui.llmModel.value = status.model || '';
  ui.llmAuto.checked = Boolean(status.auto_summary);

  let hint = LLM_HINTS[status.backend] ? LLM_HINTS[status.backend]() : '';
  if (status.backend === 'local' && !status.model_ready) {
    hint += t('notes_settings.hint_local_not_ready');
  }
  ui.llmHint.textContent = hint;
  ui.llmCheckResult.textContent = '';
}

async function refreshLlmStatus() {
  renderLlmSettings(await api.llm_status());
}

async function saveLlmSettings() {
  const fields = {
    backend: ui.llmBackend.value,
    auto_summary: ui.llmAuto.checked,
  };
  if (ui.llmBackend.value === 'remote') {
    fields.base_url = ui.llmUrl.value.trim();
    fields.model = ui.llmModel.value.trim();
    // Пустое поле ключа значит «не менять»: мы его обратно не показываем,
    // и затирать сохранённый ключ пустотой нельзя.
    if (ui.llmKey.value) fields.api_key = ui.llmKey.value;
  }
  renderLlmSettings(await api.save_llm_settings(fields));
}

async function checkLlm() {
  ui.llmCheckResult.textContent = t('notes_settings.checking');
  await saveLlmSettings();
  const res = await api.check_llm();
  if (res && res.ok) {
    ui.llmCheckResult.textContent = t('notes_settings.check_ok');
  } else {
    ui.llmCheckResult.textContent = (res && res.error) || t('notes_settings.check_error');
  }
}

/* --- Утилиты ------------------------------------------------------------ */

function fmtDuration(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const pad = (n) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m % 60)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
}

function fmtDate(ts) {
  const d = new Date(ts * 1000);
  const now = new Date();
  const locale = i18n.lang === 'ru' ? 'ru-RU' : i18n.lang === 'es' ? 'es-ES' : i18n.lang === 'sr' ? 'sr-Latn-RS' : 'en-US';
  const time = d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === now.toDateString()) return t('date.today', { time });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return t('date.yesterday', { time });
  return d.toLocaleDateString(locale, { day: 'numeric', month: 'short' }) + `, ${time}`;
}

/* --- Список встреч ------------------------------------------------------ */

async function loadMeetings() {
  const meetings = await api.list_meetings();
  state.meetings = meetings || [];
  renderMeetingList();
  // Если ничего не выбрано, открываем самую свежую встречу.
  if (!state.currentId && state.meetings.length) {
    selectMeeting(state.meetings[0].id);
  }
  toggleEmptyState();
}

function renderMeetingList() {
  const q = state.filter.trim().toLowerCase();
  // Заголовок и заметки ищем на месте, это мгновенно. Расшифровки ищет
  // база: их тысячи строк, и держать их во фронте незачем.
  const found = state.search.query === q ? state.search.byMeeting : null;
  const items = q
    ? state.meetings.filter((m) => (m.title || '').toLowerCase().includes(q)
        || (m.notes || '').toLowerCase().includes(q)
        || (found && found.has(m.id)))
    : state.meetings;

  ui.list.innerHTML = '';
  for (const m of items) {
    const node = document.createElement('div');
    node.className = 'meeting-item' + (m.id === state.currentId ? ' is-active' : '');
    node.dataset.id = m.id;

    const title = document.createElement('div');
    title.className = 'meeting-item__title';
    title.textContent = m.title || t('meeting.untitled');

    const meta = document.createElement('div');
    meta.className = 'meeting-item__meta';
    if (m.status === 'recording') {
      const dot = document.createElement('span');
      dot.className = 'meeting-item__dot';
      meta.appendChild(dot);
      meta.appendChild(document.createTextNode(t('meeting.recording_status')));
    } else {
      meta.textContent = fmtDate(m.created_at)
        + (m.duration > 1 ? ` · ${fmtDuration(m.duration)}` : '');
    }

    // Нашли в расшифровке — показываем цитату. Иначе непонятно, почему
    // встреча вообще попала в выдачу: в её названии искомого слова нет.
    const hit = found && found.get(m.id);
    let quote = null;
    if (hit && hit.quotes.length) {
      quote = document.createElement('div');
      quote.className = 'meeting-item__quote';
      quote.title = t('meeting.quote_tooltip');
      quote.appendChild(highlight(hit.quotes[0].text, q));
      if (hit.hits > 1) {
        const more = document.createElement('span');
        more.className = 'meeting-item__more';
        more.textContent = t('meeting.more_quotes', { count: hit.hits - 1 });
        quote.appendChild(more);
      }
      // Клик по цитате открывает не просто встречу, а нужное место в
      // ней: иначе человек попадает в начало часовой расшифровки и
      // ищет глазами заново то, что уже нашёл.
      quote.addEventListener('click', (e) => {
        e.stopPropagation();
        selectMeeting(m.id, hit.quotes[0].start);
      });
    }

    // Кнопка удаления прямо в списке: удалять хочется там же, где
    // видишь ненужную встречу, а не искать её потом в настройках.
    const del = document.createElement('button');
    del.className = 'meeting-item__del';
    del.type = 'button';
    del.title = t('meeting.delete_tooltip');
    del.innerHTML = '<svg viewBox="0 0 16 16" width="12" height="12">'
      + '<path d="M6.5 3h3M3.5 4.5h9M5 4.5l.6 8h4.8l.6-8M7 7v3.5M9 7v3.5" '
      + 'stroke="currentColor" stroke-width="1.2" fill="none" stroke-linecap="round"/></svg>';
    del.addEventListener('click', (e) => {
      // Иначе клик заодно откроет встречу, которую собираются удалить.
      e.stopPropagation();
      askDeleteMeeting(m);
    });

    node.append(title, meta);
    if (quote) node.appendChild(quote);
    node.appendChild(del);
    node.addEventListener('click', () => selectMeeting(m.id));
    ui.list.appendChild(node);
  }
}

/**
 * Удаление встречи с подтверждением.
 *
 * Спрашиваем всегда: восстановить нечего, корзины у нас нет, а запись
 * часовой встречи вместе с расшифровкой человек по ошибке не вернёт.
 */
async function askDeleteMeeting(meeting) {
  const name = meeting.title || t('meeting.untitled');
  const ok = await confirmDialog(
    t('meeting.delete_confirm_title', { name }),
    t('meeting.delete_confirm_text'),
  );
  if (!ok) return;

  await api.delete_meeting(meeting.id);
  if (state.currentId === meeting.id) {
    state.currentId = null;
    state.current = null;
  }
  // loadMeetings сама откроет следующую встречу, если удалили открытую.
  await loadMeetings();
  showToast(t('meeting.deleted'));
}

/**
 * Спросить «точно?» и дождаться ответа.
 *
 * Своё окно вместо window.confirm: системный диалог подписан адресом
 * вида «127.0.0.1:65373 says» и кнопками на английском. Человек видит,
 * что перед ним браузер, и пугается ровно там, где нужен спокойный
 * ответ на понятный вопрос.
 */
function confirmDialog(title, text) {
  return new Promise((resolve) => {
    ui.confirmTitle.textContent = title;
    ui.confirmText.textContent = text;
    ui.confirmSheet.hidden = false;
    ui.confirmYes.focus();

    const finish = (answer) => {
      ui.confirmSheet.hidden = true;
      ui.confirmYes.removeEventListener('click', onYes);
      ui.confirmNo.removeEventListener('click', onNo);
      ui.confirmSheet.removeEventListener('click', onBackdrop);
      // Тот же флаг capture, что и при подписке: иначе обработчик
      // не снимется и следующий Escape закроет уже закрытое окно.
      document.removeEventListener('keydown', onKey, true);
      resolve(answer);
    };
    const onYes = () => finish(true);
    const onNo = () => finish(false);
    const onBackdrop = (e) => { if (e.target === ui.confirmSheet) finish(false); };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); finish(false); }
      if (e.key === 'Enter') { e.stopPropagation(); finish(true); }
    };

    ui.confirmYes.addEventListener('click', onYes);
    ui.confirmNo.addEventListener('click', onNo);
    ui.confirmSheet.addEventListener('click', onBackdrop);
    // capture: иначе Escape сначала поймает общий обработчик окна.
    document.addEventListener('keydown', onKey, true);
  });
}

/**
 * Спросить базу о расшифровках, но не на каждую букву.
 *
 * Человек печатает «переговоры» за секунду, и без задержки это девять
 * запросов, из которых нужен последний. 200 мс достаточно, чтобы поиск
 * ощущался мгновенным и при этом не дёргал базу зря.
 */
function scheduleSearch() {
  clearTimeout(state.search.timer);
  const q = state.filter.trim().toLowerCase();

  if (q.length < 2) {
    // По одной букве совпадёт половина архива, показывать это бессмысленно.
    state.search.query = '';
    state.search.byMeeting = null;
    renderMeetingList();
    return;
  }

  state.search.timer = setTimeout(async () => {
    const found = await api.search(q);
    // Пока ждали ответ, человек мог напечатать дальше: тогда этот
    // результат уже не про то, что сейчас в строке поиска.
    if (state.filter.trim().toLowerCase() !== q) return;

    state.search.query = q;
    state.search.byMeeting = new Map((found || []).map((m) => [m.meeting_id, m]));
    renderMeetingList();
  }, 200);
}

/**
 * Подсветить найденные слова в цитате.
 *
 * Через текстовые узлы, а не innerHTML: в расшифровке встречается что
 * угодно, включая угловые скобки, и склеивать её в HTML руками значит
 * рано или поздно сломать разметку чужим текстом.
 */
function highlight(text, query) {
  const frag = document.createDocumentFragment();
  const words = query.split(/\s+/).filter((w) => w.length > 1);
  if (!words.length) {
    frag.appendChild(document.createTextNode(text));
    return frag;
  }

  const low = text.toLowerCase();
  const marks = [];
  for (const word of words) {
    let from = 0;
    for (;;) {
      const at = low.indexOf(word, from);
      if (at < 0) break;
      marks.push([at, at + word.length]);
      from = at + word.length;
    }
  }
  if (!marks.length) {
    frag.appendChild(document.createTextNode(text));
    return frag;
  }

  // Совпадения разных слов могут пересекаться: склеиваем, иначе получим
  // вложенные подсветки и рваный текст.
  marks.sort((a, b) => a[0] - b[0]);
  const merged = [marks[0]];
  for (const [from, to] of marks.slice(1)) {
    const last = merged[merged.length - 1];
    if (from <= last[1]) last[1] = Math.max(last[1], to);
    else merged.push([from, to]);
  }

  let pos = 0;
  for (const [from, to] of merged) {
    if (from > pos) frag.appendChild(document.createTextNode(text.slice(pos, from)));
    const mark = document.createElement('mark');
    mark.textContent = text.slice(from, to);
    frag.appendChild(mark);
    pos = to;
  }
  if (pos < text.length) frag.appendChild(document.createTextNode(text.slice(pos)));
  return frag;
}

async function selectMeeting(id, jumpTo = null) {
  // Не теряем несохранённые правки при переключении.
  flushNotes();

  const meeting = await api.get_meeting(id);
  if (!meeting) return;

  state.currentId = id;
  state.current = meeting;
  // Есть ли что переслушивать. У встреч, загруженных файлом до
  // появления этой возможности, звук не сохранялся: кнопка там только
  // обманывала бы.
  state.hasAudio = meeting.has_audio !== false;

  ui.title.value = meeting.title || '';
  ui.notes.value = meeting.notes || '';
  renderMeta(meeting);
  renderTranscript(meeting.segments || []);
  renderSummary(meeting.summary || '');
  state.summaryText = meeting.summary || '';
  // Разбор мог остаться на другой встрече: кнопки должны говорить
  // правду про ту запись, которая открыта сейчас.
  if (state.llmBusy && state.busyMeetingId === id) {
    setSummaryStatus('Заметки ещё собираются…');
  }
  syncBusyUi();
  // Переписка своя у каждой встречи, поэтому тянем её при каждом
  // переключении, а не держим всё в памяти.
  renderChat((await api.list_chat_messages(id)) || []);
  renderMeetingList();
  toggleEmptyState();

  // Пришли из поиска: показываем вкладку с расшифровкой и то самое
  // место, а не начало часовой встречи.
  if (jumpTo !== null) {
    showTab('transcript');
    revealTranscriptAt(jumpTo);
  }
}

/** Переключить вкладку так же, как это делает клик по ней. */
function showTab(name) {
  const tab = document.querySelector(`.tab[data-tab="${name}"]`);
  if (tab) tab.click();
}

/**
 * Показать реплику, сказанную в этот момент встречи.
 *
 * Ищем блок, внутрь которого попадает секунда: реплики склеиваются в
 * блоки, и точного совпадения по началу обычно нет.
 */
function revealTranscriptAt(start) {
  const turns = [...ui.transcript.querySelectorAll('.turn')];
  if (!turns.length) return;

  let target = turns[0];
  for (const turn of turns) {
    if (Number(turn.dataset.start || 0) <= start + 0.01) target = turn;
    else break;
  }

  target.scrollIntoView({ block: 'center', behavior: 'smooth' });
  // Подсветка на пару секунд: без неё непонятно, куда именно смотреть,
  // особенно если рядом несколько похожих реплик.
  target.classList.add('turn--found');
  setTimeout(() => target.classList.remove('turn--found'), 2500);
}

/* --- Транскрипт --------------------------------------------------------- */

/* --- Речь, которая ещё идёт ---------------------------------------------- */

// Черновик на каждую дорожку: собеседник и микрофон говорят одновременно,
// и один черновик затирал бы другой.
const drafts = new Map();

/**
 * Показать речь, которую человек ещё не договорил.
 *
 * Виден бледнее готовых реплик и не даёт себя выделить: это ещё не текст
 * встречи, а предположение программы. Настоящая реплика придёт следом и
 * встанет на его место.
 */
function showDraft(seg) {
  if (!seg.text) return;
  let turn = drafts.get(seg.speaker);
  if (!turn) {
    turn = document.createElement('div');
    turn.className = 'turn turn--draft' + (seg.speaker === 'me' ? ' turn--me' : '');

    const head = document.createElement('div');
    head.className = 'turn__head';
    const who = document.createElement('span');
    who.className = 'turn__who turn__who--plain';
    who.textContent = seg.speaker === 'me' ? t('transcript.speaker_me') : t('transcript.speaker_them');
    head.appendChild(who);

    const body = document.createElement('div');
    body.className = 'turn__text';
    turn.appendChild(head);
    turn.appendChild(body);
    ui.transcript.appendChild(turn);
    drafts.set(seg.speaker, turn);
  }
  turn.querySelector('.turn__text').textContent = seg.text;
  updateTranscriptEmpty();

  // Прокручиваем только если человек и так внизу: иначе выдернем его из
  // места, которое он читает.
  const pane = ui.transcript.parentElement;
  if (pane.scrollHeight - pane.scrollTop - pane.clientHeight < 120) {
    pane.scrollTop = pane.scrollHeight;
  }
}

/** Убрать черновик дорожки: пришла готовая реплика или встреча кончилась. */
function clearDraft(speaker) {
  if (speaker === undefined) {
    for (const turn of drafts.values()) turn.remove();
    drafts.clear();
    return;
  }
  const turn = drafts.get(speaker);
  if (turn) {
    turn.remove();
    drafts.delete(speaker);
  }
}

/** Отрисовать транскрипт встречи целиком. */
function renderTranscript(segments) {
  // Черновики принадлежали прошлой встрече: их узлы уже не в дереве.
  drafts.clear();
  ui.transcript.innerHTML = '';
  for (const seg of segments) appendSegment(seg, false);
  updateTranscriptEmpty();
}

const ICON_PLAY = '<svg viewBox="0 0 16 16" width="11" height="11"><path d="M5 3.5v9l7-4.5z" fill="currentColor"/></svg>';
const ICON_STOP = '<svg viewBox="0 0 16 16" width="11" height="11"><rect x="4.5" y="4.5" width="7" height="7" rx="1" fill="currentColor"/></svg>';

/** Проигрыватель на всё окно: вторая фраза останавливает первую. */
let currentAudio = null;
let currentPlayBtn = null;

function stopPlayback() {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  if (currentPlayBtn) {
    currentPlayBtn.innerHTML = ICON_PLAY;
    currentPlayBtn.classList.remove('is-playing');
    currentPlayBtn = null;
  }
}

/**
 * Переслушать реплику.
 *
 * Звук приходит из питона готовым куском в base64: у страницы нет
 * доступа к диску, а поднимать ради этого локальный сервер значило бы
 * открыть порт в приложении, которое обещает приватность.
 */
async function playTurn(btn, turn) {
  // Повторное нажатие по играющей реплике останавливает её.
  if (currentPlayBtn === btn) {
    stopPlayback();
    return;
  }
  stopPlayback();
  if (!state.currentId) return;

  const start = Number(turn.dataset.start || 0);
  const end = Number(turn.dataset.end || 0) || start + 3;
  const track = turn.dataset.speaker || '';

  btn.classList.add('is-loading');
  let clip = null;
  try {
    clip = await api.audio_clip(state.currentId, start, end, track);
  } catch (e) {
    clip = null;
  }
  btn.classList.remove('is-loading');

  if (!clip || !clip.wav) {
    // Записи может не быть вовсе: встречу загрузили файлом или запись
    // удалили. Молчать было бы непонятно, поэтому говорим прямо.
    showToast(t('transcript.audio_clip_not_found'), 3000);
    return;
  }

  const audio = new Audio('data:audio/wav;base64,' + clip.wav);
  currentAudio = audio;
  currentPlayBtn = btn;
  btn.innerHTML = ICON_STOP;
  btn.classList.add('is-playing');
  audio.addEventListener('ended', stopPlayback);
  audio.addEventListener('error', stopPlayback);
  audio.play().catch(() => stopPlayback());
}

/**
 * Найти блок, перед которым должна встать реплика.
 *
 * Обычно реплики приходят по порядку и место — конец списка. Но после
 * «стоп» докатка досчитывает куски, пропущенные при перегрузке, и они
 * приходят из середины встречи. Дописывать их в конец значит показать
 * человеку разговор с перепутанным порядком реплик.
 */
function findTurnAfter(start) {
  const turns = ui.transcript.children;
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    if (Number(turns[i].dataset.start || 0) <= start) return turns[i].nextElementSibling;
  }
  return turns[0] || null;
}

/**
 * Добавить реплику в транскрипт, на её место по времени.
 *
 * Реплики одного источника склеиваем в один блок, но только пока между
 * ними нет заметной паузы: без склейки связная речь рвётся на лесенку,
 * а со склейкой напролом весь разговор превращается в одну простыню.
 */
function appendSegment(seg, scroll = true) {
  const isMe = seg.speaker === 'me';
  // Куда встаёт реплика. Для живой речи это конец, для досчитанного
  // куска — середина, перед первой репликой, которая началась позже.
  const before = findTurnAfter(seg.start);
  // Склеивать можно только с предыдущим по времени блоком, а не с
  // последним нарисованным: иначе досчитанный кусок из начала встречи
  // прилипал бы к её концу.
  const last = before ? before.previousElementSibling : ui.transcript.lastElementChild;

  // Склеиваем только то, что человек сказал подряд. Если между репликами
  // была заметная пауза, это уже новая мысль и новый абзац, иначе весь
  // монолог слипается в одну простыню без единого разрыва.
  // Разные голоса не склеиваем никогда, даже внутри одной дорожки: в
  // звонке участники говорят встык, и слить их было бы хуже всего.
  const gap = last ? seg.start - Number(last.dataset.end || 0) : Infinity;
  const sameVoice = (last?.dataset.voice || '') === (seg.voice_id || '');
  if (last && last.dataset.speaker === seg.speaker && sameVoice && gap < TURN_GAP) {
    const body = last.querySelector('.turn__text');
    body.textContent = `${body.textContent} ${seg.text}`.trim();
    last.dataset.end = seg.end;
    // Блок склеен из нескольких реплик: чтобы поправить язык, надо
    // знать номера всех, а не только первой.
    last.dataset.ids = `${last.dataset.ids || ''} ${seg.id}`.trim();
  } else {
    const turn = document.createElement('div');
    turn.className = 'turn' + (isMe ? ' turn--me' : '');
    turn.dataset.speaker = seg.speaker;
    turn.dataset.end = seg.end;
    // Начало блока: по нему находим нужное место, когда человек пришёл
    // сюда из поиска.
    turn.dataset.start = seg.start;
    turn.dataset.voice = seg.voice_id || '';
    turn.dataset.ids = seg.id || '';
    turn.dataset.lang = seg.lang || '';
    // Реплика под сомнением: человек сказал, что это был чужой ролик.
    // Показываем это прямо в расшифровке, иначе пометка молчаливая:
    // текст в саммари не попал, а почему — непонятно.
    if (seg.doubtful) {
      turn.classList.add('turn--doubtful');
      turn.title = t('foreign.doubtful_hint');
    }

    const head = document.createElement('div');
    head.className = 'turn__head';

    // Имя участника, а не просто дорожка: за компьютером и в звонке
    // может быть несколько человек. По клику имя можно поменять, и оно
    // запомнится за голосом на будущие встречи.
    const who = document.createElement('button');
    who.className = 'turn__who';
    who.type = 'button';
    who.textContent = seg.voice_label || (isMe ? t('transcript.speaker_me') : t('transcript.speaker_them'));
    if (seg.voice_id) {
      who.dataset.voice = seg.voice_id;
      who.title = t('transcript.rename_tooltip');
      who.addEventListener('click', () => renameVoice(who, seg.voice_id));
    } else {
      who.classList.add('turn__who--plain');
      who.disabled = true;
    }

    const time = document.createElement('span');
    time.className = 'turn__time';
    time.textContent = fmtDuration(seg.start);

    // Переслушать фразу. Распознавание местами врёт, и без возможности
    // проверить спорное место расшифровке приходится просто верить.
    const play = document.createElement('button');
    play.className = 'turn__play';
    play.type = 'button';
    play.title = t('transcript.play_tooltip');
    play.innerHTML = ICON_PLAY;
    play.addEventListener('click', () => playTurn(play, turn));
    // У встреч без записи кнопки нет вовсе: лучше её отсутствие,
    // чем кнопка, которая всегда отвечает «записи нет».
    play.hidden = state.hasAudio === false;

    // Поправить язык фразы. Определитель языка ошибается редко, но
    // одна испорченная реплика в часовой встрече заметна, а сделать с
    // ней человеку было нечего.
    const lang = document.createElement('button');
    lang.className = 'turn__lang';
    lang.type = 'button';
    lang.title = t('transcript.lang_tooltip');
    lang.textContent = (seg.lang || '').toUpperCase() || t('transcript.lang_placeholder');
    lang.addEventListener('click', () => pickLanguage(lang, turn));
    lang.hidden = state.hasAudio === false;

    head.appendChild(who);
    head.appendChild(time);
    head.appendChild(lang);
    head.appendChild(play);

    const text = document.createElement('div');
    text.className = 'turn__text';
    text.textContent = seg.text;

    turn.appendChild(head);
    turn.appendChild(text);
    ui.transcript.insertBefore(turn, before);
  }

  updateTranscriptEmpty();
  // Прокручиваем к свежей реплике, но только если человек не листает выше.
  // За досчитанным куском из середины встречи не прыгаем: человек в этот
  // момент читает конец расшифровки, и рывок вверх был бы неожиданным.
  if (scroll && !before) {
    const pane = ui.transcript.parentElement;
    const nearBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 80;
    if (nearBottom) pane.scrollTop = pane.scrollHeight;
  }
}

// Языки, на которых можно пересчитать реплику. Спрашиваем у программы
// один раз: список зависит от того, какие модели у неё есть.
let LANGS = null;

const LANG_NAMES = {
  get ru() { return t('lang.ru'); }, get en() { return t('lang.en'); },
  get de() { return t('lang.de'); }, get fr() { return t('lang.fr'); },
  get es() { return t('lang.es'); }, get it() { return t('lang.it'); },
  get pt() { return t('lang.pt'); }, get pl() { return t('lang.pl'); },
  get uk() { return t('lang.uk'); }, get sr() { return t('lang.sr'); },
  get tr() { return t('lang.tr'); }, get nl() { return t('lang.nl'); },
};

/**
 * Поправить язык реплики.
 *
 * Автоматика ошибается редко, но метко: фраза уезжает в чужую модель и
 * возвращается кашей. Человек говорит, на каком языке она была, и
 * программа пересчитывает этот кусок записи заново.
 */
async function pickLanguage(button, turn) {
  if (button.dataset.busy === '1') return;
  const ids = (turn.dataset.ids || '').split(/\s+/).filter(Boolean);
  if (!ids.length) return;

  if (!LANGS) {
    try {
      LANGS = await api.transcription_languages();
    } catch (e) {
      LANGS = ['ru'];
    }
  }

  const menu = document.createElement('div');
  menu.className = 'lang-menu';
  for (const code of LANGS) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'lang-menu__item';
    item.textContent = LANG_NAMES[code] || code.toUpperCase();
    if (code === turn.dataset.lang) item.classList.add('is-current');
    item.addEventListener('click', async () => {
      menu.remove();
      await applyLanguage(button, turn, ids, code);
    });
    menu.appendChild(item);
  }

  // Меню закрывается кликом мимо: иначе оно висит поверх текста и мешает
  // читать то самое место, ради которого его открыли.
  const close = (e) => {
    if (menu.contains(e.target) || e.target === button) return;
    menu.remove();
    document.removeEventListener('click', close, true);
  };
  document.addEventListener('click', close, true);

  button.after(menu);
}

function showFailure(button, was, why) {
  button.textContent = '—';
  button.title = why;
  button.classList.add('turn__lang--failed');
  setTimeout(() => {
    button.textContent = was;
    button.title = t('transcript.lang_tooltip');
    button.classList.remove('turn__lang--failed');
  }, 2500);
}

async function applyLanguage(button, turn, ids, code) {
  const was = button.textContent;
  button.dataset.busy = '1';
  button.textContent = '...';
  const body = turn.querySelector('.turn__text');
  const prevText = body.textContent;
  try {
    const parts = [];
    for (const id of ids) {
      const res = await api.retranscribe_segment(id, code);
      if (res && res.text) parts.push(res.text);
    }
    if (parts.length) {
      body.textContent = parts.join(' ');
      turn.dataset.lang = code;
      button.textContent = code.toUpperCase();
    } else {
      // Пересчитать не вышло: нет записи или модель промолчала. Текст
      // не трогаем, иначе человек потеряет и то, что было.
      body.textContent = prevText;
      showFailure(button, was, t('transcript.lang_failed'));
    }
  } catch (e) {
    body.textContent = prevText;
    showFailure(button, was, t('transcript.lang_retry_failed'));
  } finally {
    button.dataset.busy = '';
  }
}

/**
 * Назвать говорящего.
 *
 * Имя закрепляется за голосом, а не за встречей: на следующих встречах
 * этот же человек определится сам.
 */
function renameVoice(button, voiceId) {
  if (!state.currentId || !voiceId || button.dataset.editing === '1') return;

  // Правим прямо на месте, а не через window.prompt: системный диалог
  // внутри webview блокирует поток UI и на окне поверх всех выглядит
  // чужеродно, а тут человек просто дописывает имя там, где его видит.
  const current = button.textContent;
  button.dataset.editing = '1';

  const input = document.createElement('input');
  input.className = 'turn__who-input';
  input.value = current;
  input.spellcheck = false;
  input.setAttribute('aria-label', t('transcript.voice_aria_label'));
  button.replaceWith(input);
  input.focus();
  input.select();

  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const name = input.value.trim();
    input.replaceWith(button);
    button.dataset.editing = '';
    if (!save || !name || name === current) return;

    button.textContent = name;
    try {
      await api.name_voice(state.currentId, voiceId, name);
    } catch (err) {
      console.error('Не удалось назвать говорящего', err);
      button.textContent = current;
      return;
    }
    // Остальные реплики того же голоса переименовываем на месте:
    // перезагружать транскрипт ради имени значит дёрнуть прокрутку
    // у человека, который его читает.
    for (const other of ui.transcript.querySelectorAll('.turn__who')) {
      if (other !== button && other.dataset.voice === voiceId) other.textContent = name;
    }
  };

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    if (e.key === 'Escape') { e.preventDefault(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}

function updateTranscriptEmpty() {
  ui.transcriptEmpty.hidden = ui.transcript.childElementCount > 0;
}

/* --- Модель распознавания ------------------------------------------------ */

function fmtMb(bytes) {
  return (bytes / 1048576).toFixed(0);
}

/** Показать состояние весов: скачать, идёт загрузка или всё готово. */
async function refreshModelStatus() {
  let st;
  try {
    st = await api.model_status();
  } catch (e) {
    return;
  }
  state.model = st;

  if (st.backend === 'null') {
    ui.modelBar.hidden = true;
    return;
  }
  ui.modelBar.hidden = false;

  if (st.downloaded) {
    ui.modelText.textContent = st.loaded
      ? t('model.status_ready')
      : t('model.status_loaded');
    ui.modelProgress.hidden = true;
    ui.modelAction.hidden = true;
    ui.modelBar.classList.add('is-ready');
    hideModelLoad();
  } else if (st.downloading) {
    ui.modelBar.classList.remove('is-ready');
    ui.modelAction.hidden = false;
    ui.modelAction.textContent = t('model.action_cancel');
    ui.modelAction.dataset.act = 'cancel';
    setModelProgress(st.bytes, st.total_bytes);
    // Загрузка началась ещё до открытия окна: показываем её сразу, а не
    // ждём первого события прогресса.
    showModelLoad(st.bytes, st.total_bytes, t('model.download_title'),
      t('model.download_hint'));
  } else {
    ui.modelBar.classList.remove('is-ready');
    ui.modelProgress.hidden = true;
    ui.modelText.textContent = st.bytes
      ? t('model.status_partial', { done: fmtMb(st.bytes), total: fmtMb(st.total_bytes) })
      : t('model.status_need_download', { total: fmtMb(st.total_bytes) });
    ui.modelAction.hidden = false;
    ui.modelAction.textContent = st.bytes ? t('model.action_resume') : t('model.action_download');
    ui.modelAction.dataset.act = 'download';
  }
}

function setModelProgress(done, total) {
  ui.modelProgress.hidden = false;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  ui.modelFill.style.width = pct + '%';
  ui.modelText.textContent = t('model.download_progress', { done: fmtMb(done), total: fmtMb(total), pct });
}

function onModelProgress(payload) {
  if (payload.state === 'downloading') {
    ui.modelBar.hidden = false;
    ui.modelAction.hidden = false;
    ui.modelAction.textContent = t('model.action_cancel');
    ui.modelAction.dataset.act = 'cancel';
    setModelProgress(payload.bytes, payload.total || state.model.total_bytes);
    showModelLoad(payload.bytes, payload.total || state.model.total_bytes,
      t('model.download_title'),
      t('model.download_hint'));
    // Плашка живёт во вкладке «Транскрипт», а человек в этот момент
    // обычно смотрит на список загруженных файлов и не понимает, почему
    // расшифровки нет. Поэтому о первой загрузке говорим на всё окно.
    if (!state.modelToastShown) {
      state.modelToastShown = true;
      showToast(t('model.first_download_toast'), 12000);
    }
    return;
  }
  if (payload.state === 'error') {
    hideModelLoad();
    showToast(payload.message || t('model.download_failed'));
  }
  if (payload.state === 'ready' && state.modelToastShown) {
    state.modelToastShown = false;
    hideModelLoad();
    showToast(t('model.download_ready'), 4000);
  }
  if (payload.state === 'ready') hideModelLoad();
  refreshModelStatus();
}

/**
 * Полоса загрузки модели в боковой панели: видна на любом экране.
 *
 * Одна и та же полоса и для распознавания, и для модели заметок: две
 * сразу не качаются, а человеку важно одно — сколько ещё ждать.
 */
function showModelLoad(done, total, title, hint) {
  if (!ui.modelLoad) return;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  ui.modelLoad.hidden = false;
  if (title) ui.modelLoadText.textContent = title;
  if (hint) ui.modelLoadHint.textContent = hint;
  ui.modelLoadPct.textContent = pct + '%';
  ui.modelLoadFill.style.width = pct + '%';
}

function hideModelLoad() {
  if (ui.modelLoad) ui.modelLoad.hidden = true;
}

async function onModelAction() {
  if (ui.modelAction.dataset.act === 'cancel') {
    await api.cancel_model_download();
  } else {
    ui.modelText.textContent = t('model.starting_download');
    await api.download_model();
  }
  refreshModelStatus();
}

function renderMeta(meeting) {
  const parts = [fmtDate(meeting.created_at)];
  if (meeting.duration > 1) parts.push(fmtDuration(meeting.duration));
  const labels = {
    draft: t('status.draft'), recording: t('status.recording'),
    processing: t('status.processing'), ready: t('status.ready'),
  };
  parts.push(labels[meeting.status] || meeting.status);
  ui.meta.textContent = parts.join(' · ');
}

/** Перечитать метаданные встречи, не трогая текст заметок. */
async function refreshMeta(id) {
  const meeting = await api.get_meeting(id);
  if (meeting && meeting.id === state.currentId) renderMeta(meeting);
}

function toggleEmptyState() {
  const has = Boolean(state.currentId);
  ui.empty.hidden = has;
  ui.editor.hidden = !has;
}

/* --- Запись ------------------------------------------------------------- */

async function toggleRecording() {
  if (state.isRecording) {
    await api.stop_recording();
  } else {
    if (!state.currentId) await createMeeting();
    await api.start_recording(state.currentId);
  }
}

function renderRecordingState() {
  const active = state.isRecording;
  ui.record.classList.toggle('is-recording', active);
  // Словарь едет через мост и может опоздать к первой отрисовке.
  // Затирать надпись ключом нельзя: человек увидит на главной
  // кнопке «recording.start» вместо «Начать запись».
  const надпись = active ? tЕслиЕсть('recording.stop') : tЕслиЕсть('recording.start');
  if (надпись !== null) ui.recLabel.textContent = надпись;
  ui.levels.hidden = !active;
  ui.timer.hidden = !active;
  if (!active) {
    ui.levelMe.style.width = '0%';
    ui.levelThem.style.width = '0%';
  }
}

function renderLevels(me, them) {
  ui.levelMe.style.width = `${Math.min(100, (me || 0) * 100)}%`;
  ui.levelThem.style.width = `${Math.min(100, (them || 0) * 100)}%`;
  // Если поток уровней оборвался, полоски не должны застрять.
  clearTimeout(levelResetTimer);
  levelResetTimer = setTimeout(() => {
    ui.levelMe.style.width = '0%';
    ui.levelThem.style.width = '0%';
  }, 600);
}

function startTimer() {
  stopTimer();
  const tick = () => {
    if (!state.startedAt) return;
    ui.timer.textContent = fmtDuration((Date.now() - state.startedAt) / 1000);
  };
  tick();
  timerInterval = setInterval(tick, 1000);
}

function stopTimer() {
  if (timerInterval) clearInterval(timerInterval);
  timerInterval = null;
}

/* --- Заметки ------------------------------------------------------------ */

function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushNotes, 700);
}

function flushNotes() {
  clearTimeout(saveTimer);
  if (!state.currentId) return;
  const notes = ui.notes.value;
  api.save_notes(state.currentId, notes);
  const meeting = state.meetings.find((m) => m.id === state.currentId);
  if (meeting) meeting.notes = notes;
  showSaveHint();
}

function showSaveHint() {
  ui.saveHint.textContent = t('notes.saved');
  ui.saveHint.classList.add('is-visible');
  setTimeout(() => ui.saveHint.classList.remove('is-visible'), 1200);
}

async function createMeeting() {
  const meeting = await api.create_meeting();
  if (!meeting) return;
  await loadMeetings();
  refreshModelStatus();
  await selectMeeting(meeting.id);
  ui.title.focus();
  ui.title.select();
}

/* --- Перетаскивание frameless-окна -------------------------------------- */

/**
 * Перетаскивание frameless-окна.
 *
 * pywebview easy_drag ломает выделение текста, поэтому тащим сами.
 * На Windows move_window запускает в Python отдельный поток, который водит
 * окно за курсором, пока зажата левая кнопка. Мост при этом свободен, так что
 * занятость Python распознаванием перетаскиванию не мешает. На других
 * платформах — запасной путь через мост, по смещению за раз.
 */
function setupDrag() {
  ui.titlebar.addEventListener('mousedown', (e) => {
    if (e.button !== 0 || e.target.closest('.no-drag')) return;
    e.preventDefault();
    if (window.pywebview && window.pywebview.api && window.pywebview.api.move_window) {
      // Один вызов: дальше окно ведёт Python, до отпускания кнопки.
      window.pywebview.api.move_window(0, 0);
    }
  });
}

/* --- Изменение размера окна --------------------------------------------- */

/**
 * Тянем окно за невидимые полосы по краям.
 *
 * Смещения копим и отправляем не чаще раза на кадр: каждый вызов идёт
 * через мост в Python, и на каждом mousemove окно начинало дёргаться.
 */
function setupResize() {
  let edge = null;
  let lastX = 0;
  let lastY = 0;
  let pendingX = 0;
  let pendingY = 0;
  let frame = null;

  const flush = () => {
    frame = null;
    const dx = pendingX;
    const dy = pendingY;
    pendingX = 0;
    pendingY = 0;
    if (!edge || (!dx && !dy)) return;
    if (window.pywebview && window.pywebview.api && window.pywebview.api.resize_window) {
      window.pywebview.api.resize_window(dx, dy, edge);
    }
  };

  document.querySelectorAll('.rz').forEach((zone) => {
    zone.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      edge = zone.dataset.edge;
      lastX = e.screenX;
      lastY = e.screenY;
      document.body.classList.add('is-resizing');
      e.preventDefault();
    });
  });

  window.addEventListener('mousemove', (e) => {
    if (!edge) return;
    pendingX += e.screenX - lastX;
    pendingY += e.screenY - lastY;
    lastX = e.screenX;
    lastY = e.screenY;
    if (!frame) frame = requestAnimationFrame(flush);
  });

  window.addEventListener('mouseup', () => {
    if (!edge) return;
    flush();
    edge = null;
    document.body.classList.remove('is-resizing');
  });
}

/* --- Плашка сообщений --------------------------------------------------- */

let toastTimer = null;

/* --- Тема оформления ----------------------------------------------------- */

// "system" снимает атрибут вовсе: дальше решает медиазапрос prefers-color-scheme.
function applyTheme(theme) {
  state.theme = theme;
  if (theme === 'light' || theme === 'dark') {
    document.documentElement.setAttribute('data-theme', theme);
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
  try { localStorage.setItem('konspekt.theme', theme); } catch (e) {}

  const box = document.getElementById('theme-switch');
  if (box) {
    box.querySelectorAll('button').forEach((b) => {
      b.classList.toggle('is-active', b.dataset.theme === theme);
    });
  }
}

async function onThemeClick(event) {
  const btn = event.target.closest('button[data-theme]');
  if (!btn) return;
  applyTheme(btn.dataset.theme);
  await api.set_theme(btn.dataset.theme);
}

/** Клик по кнопке языка в разделе «Оформление». */
async function onLanguageClick(event) {
  const btn = event.target.closest('button[data-lang]');
  if (!btn) return;
  await setLanguage(btn.dataset.lang);
  syncLanguageSwitch();
}

function syncLanguageSwitch() {
  const box = document.getElementById('language-switch');
  if (!box) return;
  box.querySelectorAll('button').forEach((b) => {
    b.classList.toggle('is-active', b.dataset.lang === i18n.lang);
  });
}

function showToast(text, ms = 7000) {
  if (!ui.toast) return;
  ui.toastText.textContent = text;
  ui.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, ms);
}

function hideToast() {
  if (ui.toast) ui.toast.hidden = true;
}

/* --- Вопрос про системный звук -------------------------------------------

   Программа пишет системный звук всегда, и человек может не знать, что в
   расшифровку уезжает ролик из соседней вкладки. Спрашиваем один раз за
   запись и даём выключить в один щелчок, не уводя в настройки. Сами
   ничего не меняем: молча выключить звук так же плохо, как молча его
   писать. */

function showForeignSpeechAsk(payload) {
  if (!ui.foreignSpeech) return;
  // Вопрос сторожа («не записать ли») уже не к месту: запись идёт.
  // Две плашки в одном углу закрывают друг друга.
  hideNoticedTalkAsk();
  // Два разных вопроса. Звук пишется — не выключить ли его (вдруг это
  // чужой ролик). Не пишется — не включить ли: собеседника прямо сейчас
  // нет в расшифровке.
  const пишется = !payload || payload['пишется'] !== false;
  state.foreignSpeechOn = !пишется;
  if (ui.foreignSpeechText) {
    ui.foreignSpeechText.textContent = пишется
      ? t('foreign.ask') : t('foreign.ask_on');
  }
  if (ui.foreignSpeechOff) {
    ui.foreignSpeechOff.textContent = пишется
      ? t('foreign.turn_off') : t('foreign.turn_on');
  }
  if (ui.foreignSpeechKeep) {
    ui.foreignSpeechKeep.textContent = пишется
      ? t('foreign.keep') : t('foreign.no_need');
  }
  ui.foreignSpeech.hidden = false;
}

function hideForeignSpeechAsk() {
  if (ui.foreignSpeech) ui.foreignSpeech.hidden = true;
}

async function onForeignSpeechOff() {
  hideForeignSpeechAsk();
  try {
    if (state.foreignSpeechOn) {
      const res = await api.turn_on_system_audio();
      showToast(res && res.ok ? t('foreign.on_done') : t('foreign.on_failed'), 8000);
    } else {
      const res = await api.turn_off_system_audio();
      // Если что-то уже успело записаться, честно говорим, что с ним
      // стало: иначе человек не поймёт, почему часть расшифровки
      // приглушена и не попала в заметки.
      const помечено = res && res['помечено'];
      showToast(помечено ? t('foreign.off_done_marked') : t('foreign.off_done'), 8000);
      if (помечено && state.currentId) selectMeeting(state.currentId);
    }
  } catch (e) {
    showToast(String(e));
  }
}

async function onForeignSpeechKeep() {
  hideForeignSpeechAsk();
  try {
    await api.keep_system_audio();
  } catch (e) {
    // Ответ «всё верно» ничего не меняет, поэтому и сообщать не о чем:
    // худшее, что случится, — вопрос задастся ещё раз.
  }
}

/* --- Разговор при выключенной записи --------------------------------------

   Сторож услышал разговор на компьютере, а запись не идёт. Открыл Zoom,
   а кнопку нажать забыл: вспоминают об этом в конце встречи, когда уже
   ничего не вернуть. Предлагаем начать, но ничего не пишем сами. */

function showNoticedTalkAsk(payload) {
  if (!ui.noticedTalk) return;
  // Плашка про системный звук и эта — про разные вещи, но выглядят
  // одинаково и лезут в один угол. Показывать обе разом значит закрыть
  // одну другой, поэтому прежнюю убираем.
  hideForeignSpeechAsk();
  const секунд = (payload && payload['секунд']) || 30;
  if (ui.noticedTalkText) {
    ui.noticedTalkText.textContent = t('noticed.ask', { seconds: секунд });
  }
  ui.noticedTalk.hidden = false;
}

function hideNoticedTalkAsk() {
  if (ui.noticedTalk) ui.noticedTalk.hidden = true;
}

async function onNoticedTalkRecord() {
  hideNoticedTalkAsk();
  try {
    const res = await api.record_noticed_talk();
    if (res && res.ok) {
      // Дальше всё как при обычном нажатии кнопки: событие
      // recording.started придёт само и переключит окно.
      showToast(t('noticed.started'), 6000);
    } else {
      showToast(t('noticed.start_failed'));
    }
  } catch (e) {
    showToast(String(e));
  }
}

async function onNoticedTalkSkip() {
  hideNoticedTalkAsk();
  try {
    await api.ignore_noticed_talk();
  } catch (e) {
    // Отказ ничего не ломает: худшее, что случится, — вопрос
    // задастся ещё раз, когда начнётся другой разговор.
  }
}

/* --- Настройки звука ----------------------------------------------------- */

function fillDeviceSelect(select, items, selectedId) {
  select.innerHTML = '';
  const auto = document.createElement('option');
  auto.value = '';
  const def = items.find((d) => d.is_default);
  auto.textContent = def ? t('audio.device_default_named', { name: def.name }) : t('audio.device_default');
  select.appendChild(auto);
  items.forEach((d) => {
    const opt = document.createElement('option');
    opt.value = d.id;
    opt.textContent = d.name;
    select.appendChild(opt);
  });
  // Устройство могли отключить: тогда молча падаем на «по умолчанию».
  select.value = items.some((d) => d.id === selectedId) ? selectedId : '';
}

async function openAudioSheet() {
  const data = await api.list_audio_devices();
  if (!data) {
    showToast(t('audio.device_list_failed'));
    return;
  }
  const sel = data.selected || {};
  fillDeviceSelect(ui.micSelect, data.microphones || [], sel.mic_device_id || '');
  fillDeviceSelect(ui.loopbackSelect, data.speakers || [], sel.loopback_device_id || '');
  ui.micEnabled.checked = sel.capture_mic !== false;
  ui.systemEnabled.checked = sel.capture_system !== false;
  loadAttention();
  refreshEnrollment();
  refreshPeople();
  refreshLlmStatus();
  showVersion();
  showPrefsTab('audio');
  ui.audioSheet.hidden = false;
}

/** Версия сборки в углу настроек: по жалобе сразу видно, что у человека. */
async function showVersion() {
  if (!ui.appVersion || ui.appVersion.textContent) return;
  const version = await api.app_version();
  if (version) ui.appVersion.textContent = t('about.version_prefix') + version;
}

/* --- Когда программа подаёт голос сама ------------------------------------

   Две галочки про одно: как Konspekt лезет к человеку. Слежка за звуком
   при выключенной записи и системные уведомления поверх других окон.
   Обе включены по умолчанию и обе выключаются насовсем. */

async function loadAttention() {
  if (!ui.attentionWatch) return;
  const s = await api.attention_settings();
  if (!s) return;
  ui.attentionWatch.checked = s['сторож'] !== false;
  ui.attentionToasts.checked = s['уведомления'] !== false;
  showAttentionProblem(s);
}

/** Сказать, почему слежка не работает, хотя галочка стоит.
 *
 * Устройство мог занять кто-то другой, и тогда галочка врёт: человек
 * надеется, что программа его подстрахует, а она глухая. Молчать об
 * этом хуже, чем не иметь слежки вовсе. */
function showAttentionProblem(s) {
  if (!ui.attentionProblem) return;
  let текст = '';
  if (s['сторож'] && !s['работает']) {
    текст = s['проблема']
      ? t('attention.problem_reason', { reason: s['проблема'] })
      : t('attention.problem');
  }
  ui.attentionProblem.textContent = текст;
  ui.attentionProblem.hidden = !текст;
}

async function saveAttention() {
  const s = await api.save_attention_settings({
    'сторож': ui.attentionWatch.checked,
    'уведомления': ui.attentionToasts.checked,
  });
  if (s) showAttentionProblem(s);
}

/* --- Разделы настроек ---------------------------------------------------- */

const PREFS_TITLES = {
  audio: () => t('prefs.tab.audio'),
  speech: () => t('prefs.tab.speech'),
  dictation: () => t('prefs.tab.dictation'),
  voices: () => t('prefs.tab.voices'),
  notes: () => t('prefs.tab.notes'),
  look: () => t('prefs.tab.look'),
  data: () => t('prefs.tab.data'),
  about: () => t('prefs.tab.about'),
};

/** Показать один раздел настроек и подсветить его в списке слева. */
function showPrefsTab(tab) {
  if (!PREFS_TITLES[tab]) tab = 'audio';
  for (const btn of document.querySelectorAll('.prefs__tab')) {
    btn.classList.toggle('is-active', btn.dataset.tab === tab);
  }
  for (const page of document.querySelectorAll('.prefs__page')) {
    page.hidden = page.dataset.page !== tab;
    // Раздел сменился, а прокрутка осталась от прежнего: человек видит
    // середину нового раздела и думает, что тот пустой.
    if (!page.hidden) page.scrollTop = 0;
  }
  ui.prefsTitle.textContent = PREFS_TITLES[tab]();
  if (tab === 'about') loadAbout();
  if (tab === 'data') loadUsage();
  if (tab === 'speech') loadAsrSettings();
  if (tab === 'dictation') loadDictation();
}

/* --- Диктовка ------------------------------------------------------------ */

async function loadDictation() {
  const s = await api.dictation_settings();
  if (!s) return;
  ui.dictEnabled.checked = !!s.enabled;
  ui.dictHotkey.value = s.hotkey || '<ctrl>+<shift>+d';
  ui.dictMode.value = s.mode || 'hold';
  ui.dictPaste.value = s.paste_method === 'type' ? 'type' : 'auto';
  showDictationProblem(s);
}

/** Показать, почему диктовка не работает, даже если галочка стоит.
 *
 * Включить мало: клавишу мог перехватить кто-то другой, а модель может
 * быть не скачана. Промолчать здесь значит оставить человека наедине с
 * молчащей программой. */
function showDictationProblem(s) {
  let текст = '';
  if (s.enabled && !s.running) {
    текст = t('dictation.hotkey_taken');
  } else if (s.enabled && s.problem) {
    // s.problem приходит из питона по-русски: это внутренняя причина
    // (занята модель, нет прав и т.п.), отдельного словаря на неё пока
    // нет, показываем как есть.
    текст = s.problem[0].toUpperCase() + s.problem.slice(1) + '.';
  }
  ui.dictProblem.textContent = текст;
  ui.dictProblem.hidden = !текст;
}

async function saveDictation() {
  const s = await api.save_dictation_settings({
    enabled: ui.dictEnabled.checked,
    hotkey: ui.dictHotkey.value,
    mode: ui.dictMode.value,
    paste_method: ui.dictPaste.value,
  });
  if (s) showDictationProblem(s);
}

/* --- Распознавание ------------------------------------------------------- */

// Языки, на которых говорит основная модель. Whisper знает больше, но
// список из сотни строк в выпадашке бесполезен: оставляем те, что
// реально встречаются на встречах.
const ASR_LANG_CODES = ['ru', 'en', 'de', 'fr', 'es', 'it', 'pt', 'pl', 'uk', 'sr', 'tr', 'nl'];

function asrLangName(code) {
  return t(`lang.${code}`);
}

function sizeName(code) {
  return code === 'base' ? t('speech.size.fast')
    : code === 'small' ? t('speech.size.accurate')
    : code;
}

async function loadAsrSettings() {
  const s = await api.asr_settings();
  if (!s) return;

  if (!ui.asrLanguage.options.length) {
    for (const code of ASR_LANG_CODES) {
      const o = document.createElement('option');
      o.value = code;
      o.textContent = asrLangName(code);
      ui.asrLanguage.appendChild(o);
    }
  }
  ui.asrLanguage.value = s.language || 'ru';

  ui.asrSize.textContent = '';
  for (const size of s.sizes || []) {
    const o = document.createElement('option');
    o.value = size.code;
    // Честно пишем, что модели нет на диске: иначе человек выберет её,
    // а распознавание молча продолжит работать на прежней.
    o.textContent = sizeName(size.code)
      + ' (' + fmtBytes(size.bytes) + ')'
      + (size.downloaded ? '' : t('speech.size.not_downloaded'));
    ui.asrSize.appendChild(o);
  }
  ui.asrSize.value = s.whisper_size || 'small';

  ui.asrDetect.checked = s.detect_language !== false;
  // Определитель языка не скачан: галочка ничего не даст, и врать об
  // этом хуже, чем показать причину.
  ui.asrDetect.disabled = s.langid_ready === false;
  ui.asrDetectHint.textContent = s.langid_ready === false
    ? t('speech.langid_missing')
    : t('speech.detect_hint');
  updateAsrForeign();
}

/** Настройки чужого языка не нужны, если он выключен. */
function updateAsrForeign() {
  ui.asrForeign.hidden = !ui.asrDetect.checked;
}

async function saveAsrSettings() {
  updateAsrForeign();
  const s = await api.save_asr_settings({
    language: ui.asrLanguage.value,
    detect_language: ui.asrDetect.checked,
    whisper_size: ui.asrSize.value,
  });
  // Программа могла не согласиться (например, откатиться на скачанную
  // модель). Показываем то, что получилось на самом деле.
  if (s && s.active_size) {
    const выбран = (s.sizes || []).find((x) => x.code === s.whisper_size);
    ui.asrSizeHint.textContent = выбран && !выбран.downloaded
      ? t('speech.size_queued')
      : '';
  }
}

/** Человеческий размер: 1.2 ГБ понятнее, чем 1288490188 байт. */
function fmtBytes(bytes) {
  if (!bytes) return '0 МБ';
  const mb = bytes / 1024 / 1024;
  if (mb >= 1024) return (mb / 1024).toFixed(1) + ' ГБ';
  if (mb >= 1) return Math.round(mb) + ' МБ';
  return Math.max(1, Math.round(bytes / 1024)) + ' КБ';
}

/** Что приложение держит на диске. */
async function loadUsage() {
  const u = await api.storage_usage();
  if (!u) return;

  const rows = [
    [t('data.audio_size_label'), fmtBytes(u.audio_bytes)],
    [t('data.db_size_label'), fmtBytes(u.db_bytes)],
    [t('data.models_size_label'), fmtBytes(u.models_bytes)],
  ];
  if (u.orphan_folders) {
    rows.push([t('data.orphan_label'), fmtBytes(u.orphan_bytes) + t('data.orphan_suffix')]);
  }

  ui.usageList.innerHTML = '';
  for (const [name, value] of rows) {
    const li = document.createElement('li');
    const label = document.createElement('span');
    label.textContent = name;
    const size = document.createElement('b');
    size.textContent = value;
    li.append(label, size);
    ui.usageList.appendChild(li);
  }
}

/** Убрать записи без встреч и сжать базу. */
async function runCleanup() {
  ui.cleanupResult.textContent = t('data.cleanup_running');
  const res = await api.cleanup_storage();
  if (!res) {
    ui.cleanupResult.textContent = t('data.cleanup_failed');
    return;
  }
  const freed = (res.bytes || 0) + (res.db_bytes || 0);
  ui.cleanupResult.textContent = freed
    ? t('data.cleanup_freed', { size: fmtBytes(freed) })
    : t('data.cleanup_nothing');
  loadUsage();
}

/**
 * «О программе»: версия, обновления и указание авторства моделей.
 *
 * Авторство не украшение: лицензия WeSpeaker (CC BY 4.0) требует, чтобы
 * его видел пользователь, а не только тот, кто откроет исходники.
 */
async function loadAbout() {
  if (ui.aboutVersion && !ui.aboutVersion.textContent) {
    const version = await api.app_version();
    if (version) ui.aboutVersion.textContent = version;
  }
  if (ui.aboutNotice && !ui.aboutNotice.dataset.loaded) {
    const text = await api.notice_text();
    ui.aboutNotice.textContent = text || t('about.notice_not_found');
    ui.aboutNotice.dataset.loaded = '1';
  }
  const state = await api.get_update_settings();
  if (state) {
    ui.updateAuto.checked = state.check_updates !== false;
    ui.updateSilent.checked = state.auto_update !== false;
  }
}

/** Проверка новой версии по кнопке, с ответом на месте. */
async function checkUpdatesNow() {
  ui.updateResult.textContent = t('about.checking');
  const res = await api.check_updates_now();
  if (!res || !res.ok) {
    ui.updateResult.textContent = (res && res.error) || t('about.check_failed');
    return;
  }
  if (res.has_update) {
    // Мало сказать «вышла версия»: человек нажал кнопку и вправе знать,
    // что произойдёт дальше и надо ли ему что-то делать.
    ui.updateResult.textContent = ui.updateSilent.checked
      ? t('about.found_silent', { version: res.latest })
      : t('about.found_manual', { version: res.latest });
    showUpdateNote(res);
  } else {
    ui.updateResult.textContent = t('about.up_to_date');
  }
}

/**
 * Плашка «вышла новая версия» в боковой панели.
 *
 * Раньше находка превращалась во всплывающее сообщение, которое исчезало
 * через пару секунд, и человек оставался на старой сборке, ничего не
 * поняв. Плашка висит, пока её не закроют или не нажмут кнопку.
 */
function showUpdateNote(payload) {
  if (!payload || !payload.latest || !ui.updateNote) return;
  ui.updateNoteText.textContent = t('update.new_version', { version: payload.latest });
  // Если человек запретил ставить обновления самим, обещать «установится
  // сама» нельзя: он останется на старой версии и будет ждать напрасно.
  const silent = !ui.updateSilent || ui.updateSilent.checked;
  const hint = silent
    ? (payload.notes || t('update.silent_hint'))
    : t('update.manual_hint');
  ui.updateNoteNotes.textContent = hint;
  ui.updateNoteNotes.hidden = false;
  ui.updateNote.hidden = false;
}

/**
 * Ход тихого обновления.
 *
 * Пока качается — полоса, потом кнопка «Установить и перезапустить».
 * Ничего не нажимать тоже правильный путь: обновление встанет само,
 * когда программа побудет свёрнутой, и об этом сказано в плашке.
 */
function onUpdateState(payload) {
  if (!ui.updateNote || !payload) return;
  if (payload.state === 'downloading') {
    ui.updateNote.hidden = false;
    ui.updateNoteText.textContent = t('update.downloading', { version: payload.version || '' });
    ui.updateNoteBar.hidden = false;
    ui.updateNoteFill.style.width = (payload.percent || 0) + '%';
    ui.updateNoteNotes.textContent = t('update.background_hint');
    ui.updateNoteNotes.hidden = false;
    ui.updateNoteInstall.hidden = true;
  } else if (payload.state === 'ready') {
    ui.updateNote.hidden = false;
    ui.updateNoteText.textContent = t('update.ready', { version: payload.version || '' });
    ui.updateNoteBar.hidden = true;
    // Не «при выходе»: крестик прячет окно в трей, и выхода может не
    // случиться неделями. Ставим, когда программа свёрнута и свободна.
    ui.updateNoteNotes.textContent = t('update.window_hint');
    ui.updateNoteNotes.hidden = false;
    ui.updateNoteInstall.hidden = false;
  } else {
    // Не скачалось: молчим. Человек ничего не просил, и ошибка сети в
    // фоновой задаче не повод пугать его красной плашкой.
    ui.updateNote.hidden = true;
  }
}

/* --- Мой голос ----------------------------------------------------------- */

/**
 * Показать состояние эталона.
 *
 * Пока идёт запись, показываем, сколько речи уже услышали: без этого
 * человек не понимает, достаточно ли он наговорил и когда остановиться.
 */
async function refreshEnrollment() {
  const st = await api.enrollment_status();
  if (!st) return;
  state.enroll = st;

  if (st.recording) {
    const left = Math.max(0, st.target - st.seconds);
    ui.enrollState.textContent = st.enough
      ? t('voices.progress_enough', { sec: st.seconds.toFixed(0) })
      : t('voices.progress_left', { sec: st.seconds.toFixed(0), left: left.toFixed(0) });
    ui.enrollStart.textContent = st.enough ? t('voices.save_button') : t('voices.stop_button');
    ui.enrollForget.hidden = true;
    // Фразы показываем только во время записи: в остальное время они
    // просто занимают место в настройках.
    ui.enrollPrompt.hidden = false;
    ui.enrollPrompt.textContent = (st.prompts || []).join(' ');
  } else {
    ui.enrollState.textContent = st.has_owner
      ? t('voices.has_owner', { name: st.owner_name })
      : t('voices.not_recorded');
    ui.enrollStart.textContent = st.has_owner ? t('voices.overwrite') : t('voices.record');
    ui.enrollForget.hidden = !st.has_owner;
    ui.enrollPrompt.hidden = true;
  }
}

async function onEnrollClick() {
  const st = state.enroll || {};
  if (!st.recording) {
    try {
      await api.start_enrollment();
    } catch (err) {
      showToast(t('voices.start_failed'));
      console.error(err);
      return;
    }
    await refreshEnrollment();
    // Пока идёт запись, обновляем счётчик: это единственная подсказка,
    // по которой человек понимает, что его слышат.
    state.enrollTimer = setInterval(refreshEnrollment, 700);
    return;
  }

  clearInterval(state.enrollTimer);
  state.enrollTimer = null;
  if (!st.enough) {
    await api.cancel_enrollment();
    showToast(t('voices.cancelled'));
    await refreshEnrollment();
    return;
  }
  try {
    await api.finish_enrollment('');
    showToast(t('voices.saved'));
  } catch (err) {
    showToast(t('voices.save_failed'));
    console.error(err);
  }
  await refreshEnrollment();
  await refreshPeople();
}

async function onForgetOwner() {
  const people = await api.list_people();
  const owner = (people || []).find((p) => p.kind === 'owner');
  if (!owner) return;
  await api.forget_person(owner.id);
  await refreshEnrollment();
  await refreshPeople();
}

async function refreshPeople() {
  const people = (await api.list_people()) || [];
  ui.peopleField.hidden = people.length === 0;
  ui.peopleList.textContent = '';
  for (const person of people) {
    const li = document.createElement('li');
    li.className = 'people__item';

    const name = document.createElement('span');
    name.textContent = person.kind === 'owner' ? person.name + t('voices.you_suffix') : person.name;

    const forget = document.createElement('button');
    forget.className = 'btn-ghost';
    forget.type = 'button';
    forget.textContent = t('voices.forget');
    forget.addEventListener('click', async () => {
      await api.forget_person(person.id);
      await refreshPeople();
      await refreshEnrollment();
    });

    li.appendChild(name);
    li.appendChild(forget);
    ui.peopleList.appendChild(li);
  }
}

async function saveAudioSheet() {
  if (!ui.micEnabled.checked && !ui.systemEnabled.checked) {
    showToast(t('audio.save_at_least_one'));
    return;
  }
  await api.save_audio_settings({
    mic_device_id: ui.micSelect.value,
    loopback_device_id: ui.loopbackSelect.value,
    capture_mic: ui.micEnabled.checked,
    capture_system: ui.systemEnabled.checked,
  });
  ui.audioSheet.hidden = true;
}

/* --- Загрузка готовых записей ------------------------------------------- */

/**
 * Перетаскивание файлов в окно.
 *
 * Тонкость pywebview: настоящий путь к файлу браузеру недоступен, движок
 * дописывает его отдельным полем, но только если на элементе висит
 * python-обработчик drop. Поэтому саму подписку ставит бэкенд, а здесь
 * мы показываем подсказку и подчищаем состояние.
 */
function setupDropzone() {
  let depth = 0;   // dragenter/dragleave сыплются и от дочерних узлов

  const hide = () => { depth = 0; ui.dropzone.hidden = true; };

  window.addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    depth += 1;
    ui.dropzone.hidden = false;
  });

  window.addEventListener('dragover', (e) => {
    if (!hasFiles(e)) return;
    // Без этого браузер откроет файл вместо того, чтобы отдать его нам.
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });

  window.addEventListener('dragleave', (e) => {
    if (!hasFiles(e)) return;
    depth -= 1;
    if (depth <= 0) hide();
  });

  window.addEventListener('drop', (e) => {
    e.preventDefault();
    hide();
    // Пути придут из питона: браузеру их не видно. Здесь только сообщаем
    // человеку, что бросок принят.
    const count = e.dataTransfer && e.dataTransfer.files
      ? e.dataTransfer.files.length : 0;
    if (count > 0) {
      setImportsTitle(t('imports.reading_files', { count, files: plural(count, t('imports.file_word.one'), t('imports.file_word.few'), t('imports.file_word.many')) }));
    }
  });
}

function hasFiles(e) {
  const dt = e.dataTransfer;
  if (!dt) return false;
  if (dt.types && dt.types.includes) return dt.types.includes('Files');
  return true;
}

function plural(n, one, few, many) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function setImportsTitle(text) {
  ui.imports.hidden = false;
  el('imports-title').textContent = text;
}

/** Выбор файлов через системный диалог: запасной путь к тому же импорту. */
async function pickFiles() {
  setImportsTitle(t('imports.picking_files'));
  await api.pick_and_import();
  await refreshImports();
}

/** Полный список очереди: после броска файлов и при запуске окна. */
async function refreshImports() {
  const status = await api.import_status();
  renderImports(status && status.tasks ? status.tasks : []);
}

function renderImports(tasks) {
  state.imports = tasks || [];
  if (state.imports.length === 0) {
    ui.imports.hidden = true;
    ui.importsList.textContent = '';
    return;
  }

  ui.imports.hidden = false;
  const left = state.imports.filter(
    (task) => task.status === 'running' || task.status === 'waiting').length;
  el('imports-title').textContent = left > 0
    ? t('imports.remaining', { left })
    : t('imports.title');
  // Кнопка очистки нужна, только когда есть что убирать.
  el('imports-clear').hidden = !state.imports.some(
    (t) => t.status === 'done' || t.status === 'failed' || t.status === 'cancelled');

  ui.importsList.textContent = '';
  state.imports.forEach((task) => ui.importsList.appendChild(importRow(task)));
}

function importRow(task) {
  const node = document.createElement('div');
  node.className = `import-item import-item--${task.status}`;

  const row = document.createElement('div');
  row.className = 'import-item__row';

  const name = document.createElement('span');
  name.className = 'import-item__name';
  name.textContent = task.name;
  name.title = task.name;
  row.appendChild(name);

  const label = document.createElement('span');
  label.className = 'import-item__state';
  label.textContent = importStateText(task);
  row.appendChild(label);

  // Отменить можно только то, что ещё не доработало.
  if (task.status === 'waiting' || task.status === 'running') {
    const cancel = document.createElement('button');
    cancel.className = 'import-item__cancel';
    cancel.textContent = '×';
    cancel.title = t('imports.cancel_tooltip');
    cancel.addEventListener('click', async (e) => {
      e.stopPropagation();
      const status = await api.cancel_import(task.id);
      renderImports(status && status.tasks ? status.tasks : []);
    });
    row.appendChild(cancel);
  }

  node.appendChild(row);

  if (task.status === 'running') {
    const bar = document.createElement('div');
    bar.className = 'import-item__bar';
    const fill = document.createElement('div');
    fill.className = 'import-item__fill';
    fill.style.width = `${Math.round((task.progress || 0) * 100)}%`;
    bar.appendChild(fill);
    node.appendChild(bar);
  }

  if (task.status === 'failed' && task.error) {
    const err = document.createElement('div');
    err.className = 'import-item__error';
    err.textContent = task.error;
    node.appendChild(err);
  }

  // Готовую встречу открываем кликом: человек только что её ждал.
  if (task.meeting_id) {
    node.style.cursor = 'pointer';
    node.title = t('imports.open_tooltip');
    node.addEventListener('click', () => selectMeeting(task.meeting_id));
  }

  return node;
}

function importStateText(task) {
  switch (task.status) {
    case 'waiting':
      return t('imports.state.waiting');
    case 'running': {
      const pct = Math.round((task.progress || 0) * 100);
      return `${pct}%${task.stereo_split ? ', ' + t('imports.state.stereo') : ''}`;
    }
    case 'done':
      return t('imports.state.done');
    case 'failed':
      return t('imports.state.failed');
    case 'cancelled':
      return t('imports.state.cancelled');
    default:
      return task.status;
  }
}

/** Прогресс одного файла: правим список точечно. */
function onImportProgress(task) {
  if (!task) return;
  const list = (state.imports || []).slice();
  const idx = list.findIndex((t) => t.id === task.id);
  if (idx === -1) list.push(task); else list[idx] = task;
  renderImports(list);

  // Открытая встреча меняется по ходу разбора: обновим её шапку.
  if (task.meeting_id && task.meeting_id === state.currentId) {
    refreshMeta(task.meeting_id);
  }
}

/* --- Инициализация ------------------------------------------------------ */

function bindUi() {
  Object.assign(ui, {
    titlebar: el('titlebar'),
    list: el('meeting-list'),
    sidebar: el('sidebar'),
    sidebarGrip: el('sidebar-grip'),
    search: el('search'),
    empty: el('empty-state'),
    editor: el('editor'),
    title: el('meeting-title'),
    meta: el('meeting-meta'),
    notes: el('notes'),
    saveHint: el('save-hint'),
    record: el('btn-record'),
    recLabel: el('rec-label'),
    levels: el('levels'),
    levelMe: el('level-me'),
    levelThem: el('level-them'),
    timer: el('timer'),
    pin: el('btn-pin'),
    toast: el('toast'),
    toastText: el('toast-text'),
    foreignSpeech: el('foreign-speech'),
    foreignSpeechText: el('foreign-speech-text'),
    foreignSpeechOff: el('foreign-speech-off'),
    foreignSpeechKeep: el('foreign-speech-keep'),
    noticedTalk: el('noticed-talk'),
    noticedTalkText: el('noticed-talk-text'),
    noticedTalkRec: el('noticed-talk-rec'),
    noticedTalkSkip: el('noticed-talk-skip'),
    attentionWatch: el('attention-watch'),
    attentionToasts: el('attention-toasts'),
    attentionProblem: el('attention-problem'),
    audioSheet: el('audio-sheet'),
    appVersion: el('app-version'),
    aboutVersion: el('about-version'),
    aboutNotice: el('about-notice'),
    prefsTitle: el('prefs-title'),
    updateAuto: el('update-auto'),
    updateSilent: el('update-silent'),
    updateCheck: el('update-check'),
    updateResult: el('update-result'),
    updateNote: el('update-note'),
    updateNoteText: el('update-note-text'),
    updateNoteNotes: el('update-note-notes'),
    updateNoteBar: el('update-note-bar'),
    updateNoteFill: el('update-note-fill'),
    updateNoteInstall: el('update-note-install'),
    updateNoteHide: el('update-note-hide'),
    usageList: el('usage-list'),
    cleanupResult: el('cleanup-result'),
    confirmSheet: el('confirm-sheet'),
    confirmTitle: el('confirm-title'),
    confirmText: el('confirm-text'),
    confirmYes: el('confirm-yes'),
    confirmNo: el('confirm-no'),
    micSelect: el('mic-select'),
    loopbackSelect: el('loopback-select'),
    micEnabled: el('mic-enabled'),
    asrLanguage: el('asr-language'),
    asrDetect: el('asr-detect'),
    asrDetectHint: el('asr-detect-hint'),
    asrSize: el('asr-size'),
    asrSizeHint: el('asr-size-hint'),
    asrForeign: el('asr-foreign'),
    dictEnabled: el('dictation-enabled'),
    dictHotkey: el('dictation-hotkey'),
    dictMode: el('dictation-mode'),
    dictPaste: el('dictation-paste'),
    dictProblem: el('dictation-problem'),
    systemEnabled: el('system-enabled'),
    enrollState: el('enroll-state'),
    enrollPrompt: el('enroll-prompt'),
    enrollStart: el('enroll-start'),
    enrollForget: el('enroll-forget'),
    peopleField: el('people-field'),
    peopleList: el('people-list'),
    transcript: el('transcript'),
    transcriptEmpty: el('transcript-empty'),
    modelBar: el('model-bar'),
    modelText: el('model-text'),
    modelProgress: el('model-progress'),
    modelFill: el('model-fill'),
    modelAction: el('model-action'),
    modelLoad: el('model-load'),
    modelLoadText: el('model-load-text'),
    modelLoadHint: el('model-load-hint'),
    modelLoadPct: el('model-load-pct'),
    modelLoadFill: el('model-load-fill'),
    imports: el('imports'),
    importsList: el('imports-list'),
    dropzone: el('dropzone'),
    summaryBody: el('summary-body'),
  summaryTitle: el('summary-title'),
    summaryEmpty: el('summary-empty'),
    summaryRun: el('summary-run'),
    summaryStop: el('summary-stop'),
    chatList: el('chat-list'),
    chatHint: el('chat-hint'),
    chatText: el('chat-text'),
    chatSend: el('chat-send'),
    chatClear: el('chat-clear'),
    llmBackend: el('llm-backend'),
    llmRemote: el('llm-remote'),
    llmUrl: el('llm-url'),
    llmKey: el('llm-key'),
    llmModel: el('llm-model'),
    llmAuto: el('llm-auto'),
    llmHint: el('llm-hint'),
    llmCheck: el('llm-check'),
    llmCheckResult: el('llm-check-result'),
  });

  el('btn-new').addEventListener('click', createMeeting);
  el('btn-empty-new').addEventListener('click', createMeeting);
  el('btn-import').addEventListener('click', pickFiles);
  el('btn-empty-import').addEventListener('click', pickFiles);
  el('imports-clear').addEventListener('click', async () => {
    const status = await api.clear_imports();
    renderImports(status && status.tasks ? status.tasks : []);
  });
  ui.record.addEventListener('click', toggleRecording);

  el('btn-hide').addEventListener('click', () => api.hide_window());
  el('btn-minimize').addEventListener('click', () => api.minimize_window());

  el('btn-audio').addEventListener('click', openAudioSheet);
  el('audio-close').addEventListener('click', () => { ui.audioSheet.hidden = true; });
  ui.appVersion.addEventListener('click', () => showPrefsTab('about'));
  ui.updateCheck.addEventListener('click', checkUpdatesNow);
  ui.updateNoteInstall.addEventListener('click', async () => {
    ui.updateNoteInstall.disabled = true;
    const ok = await api.install_update();
    if (!ok) {
      ui.updateNoteInstall.disabled = false;
      showToast(t('update.recording_blocked'));
    }
  });
  ui.updateNoteHide.addEventListener('click', () => {
    ui.updateNote.hidden = true;
  });
  el('cleanup-run').addEventListener('click', runCleanup);
  ui.updateAuto.addEventListener('change', async () => {
    await api.set_check_updates(ui.updateAuto.checked);
  });
  ui.updateSilent.addEventListener('change', async () => {
    await api.set_auto_update(ui.updateSilent.checked);
  });
  for (const btn of document.querySelectorAll('.prefs__tab')) {
    btn.addEventListener('click', () => showPrefsTab(btn.dataset.tab));
  }
  el('theme-switch').addEventListener('click', onThemeClick);
  el('language-switch').addEventListener('click', onLanguageClick);
  el('audio-save').addEventListener('click', saveAudioSheet);
  el('enroll-start').addEventListener('click', onEnrollClick);
  el('enroll-forget').addEventListener('click', onForgetOwner);
  el('toast-close').addEventListener('click', hideToast);
  if (ui.foreignSpeechOff) {
    ui.foreignSpeechOff.addEventListener('click', onForeignSpeechOff);
  }
  if (ui.noticedTalkRec) {
    ui.noticedTalkRec.addEventListener('click', onNoticedTalkRecord);
  }
  if (ui.noticedTalkSkip) {
    ui.noticedTalkSkip.addEventListener('click', onNoticedTalkSkip);
  }
  // Галочки применяются сразу: человек снял их, чтобы программа
  // перестала лезть, и заставлять его искать кнопку «сохранить»
  // было бы издевательством.
  if (ui.attentionWatch) {
    ui.attentionWatch.addEventListener('change', saveAttention);
  }
  if (ui.attentionToasts) {
    ui.attentionToasts.addEventListener('change', saveAttention);
  }
  if (ui.foreignSpeechKeep) {
    ui.foreignSpeechKeep.addEventListener('click', onForeignSpeechKeep);
  }
  ui.modelAction.addEventListener('click', onModelAction);

  ui.summaryRun.addEventListener('click', runSummary);
  ui.summaryStop.addEventListener('click', () => api.stop_generation());
  ui.chatSend.addEventListener('click', sendQuestion);
  ui.chatClear.addEventListener('click', clearChat);
  ui.chatText.addEventListener('input', resizeChatInput);
  ui.chatText.addEventListener('keydown', (e) => {
    // Enter отправляет, Shift+Enter переносит строку: вопросы
    // короткие, и тянуться к кнопке каждый раз утомительно.
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendQuestion(); }
  });

  ui.llmBackend.addEventListener('change', saveLlmSettings);
  ui.llmAuto.addEventListener('change', saveLlmSettings);
  ui.asrLanguage.addEventListener('change', saveAsrSettings);
  ui.asrDetect.addEventListener('change', saveAsrSettings);
  ui.asrSize.addEventListener('change', saveAsrSettings);
  ui.dictEnabled.addEventListener('change', saveDictation);
  ui.dictHotkey.addEventListener('change', saveDictation);
  ui.dictMode.addEventListener('change', saveDictation);
  ui.dictPaste.addEventListener('change', saveDictation);
  ui.llmUrl.addEventListener('change', saveLlmSettings);
  ui.llmKey.addEventListener('change', saveLlmSettings);
  ui.llmModel.addEventListener('change', saveLlmSettings);
  ui.llmCheck.addEventListener('click', checkLlm);
  // Клик по затемнению закрывает панель, как принято в подобных окнах.
  ui.audioSheet.addEventListener('click', (e) => {
    if (e.target === ui.audioSheet) ui.audioSheet.hidden = true;
  });

  ui.pin.addEventListener('click', async () => {
    state.pinned = !state.pinned;
    ui.pin.setAttribute('aria-pressed', String(state.pinned));
    await api.toggle_pin(state.pinned);
  });

  ui.notes.addEventListener('input', scheduleSave);
  ui.notes.addEventListener('blur', flushNotes);

  ui.title.addEventListener('change', () => {
    if (!state.currentId) return;
    api.update_meeting(state.currentId, { title: ui.title.value });
  });
  ui.title.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); ui.notes.focus(); }
  });

  ui.search.addEventListener('input', () => {
    state.filter = ui.search.value;
    renderMeetingList();
    scheduleSearch();
  });

  // Вкладки: заметки / саммари / транскрипт.
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((tabEl) => tabEl.classList.remove('is-active'));
      document.querySelectorAll('.pane').forEach((p) => p.classList.remove('is-active'));
      tab.classList.add('is-active');
      const pane = document.querySelector(`.pane[data-pane="${tab.dataset.tab}"]`);
      if (pane) pane.classList.add('is-active');
      if (tab.dataset.tab === 'notes') ui.notes.focus();
    });
  });

  // Горячие клавиши внутри окна.
  window.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key === 'n') { e.preventDefault(); createMeeting(); }
    if (mod && e.key === 'r') { e.preventDefault(); toggleRecording(); }
    if (mod && e.key === 's') { e.preventDefault(); flushNotes(); }
    if (e.key === 'Escape' && document.activeElement === ui.search) {
      ui.search.value = '';
      state.filter = '';
      state.search.query = '';
      state.search.byMeeting = null;
      renderMeetingList();
    }
    if (e.key === 'Escape' && !ui.audioSheet.hidden) ui.audioSheet.hidden = true;
  });

  // Страховка от потери правок при закрытии окна.
  window.addEventListener('beforeunload', flushNotes);
}

/* --- Ширина боковой колонки --------------------------------------------- */

const SIDEBAR_MIN = 150;
const SIDEBAR_MAX = 420;

/**
 * Колонка со списком встреч тянется мышью.
 *
 * В 168 пикселей название встречи почти всегда обрезается многоточием,
 * а на широком экране та же колонка выглядит узкой полоской.
 * Ширина живёт в localStorage: выбранный размер не должен слетать при
 * каждом запуске.
 */
function setSidebarWidth(px) {
  const width = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(px)));
  document.documentElement.style.setProperty('--sidebar-width', `${width}px`);
  return width;
}

function setupSidebarGrip() {
  const grip = ui.sidebarGrip;
  if (!grip) return;

  grip.addEventListener('mousedown', (event) => {
    event.preventDefault();
    grip.classList.add('is-dragging');
    document.body.classList.add('is-resizing');

    const move = (e) => {
      // Считаем от левого края колонки, а не от смещения курсора:
      // так граница не убегает от мыши на упорах.
      setSidebarWidth(e.clientX - ui.sidebar.getBoundingClientRect().left);
    };

    const up = () => {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      grip.classList.remove('is-dragging');
      document.body.classList.remove('is-resizing');
      // Ширину хранит бэкенд: localStorage в webview очищается между
      // запусками, и выбранный размер каждый раз слетал к исходному.
      api.set_sidebar_width(currentSidebarWidth());
    };

    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });

  // Двойной щелчок возвращает исходную ширину: утащить колонку
  // в край легко, а вернуть ровно как было глазом уже нет.
  grip.addEventListener('dblclick', () => {
    api.set_sidebar_width(setSidebarWidth(240));
  });
}

/** Текущая ширина колонки в пикселях. */
function currentSidebarWidth() {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue('--sidebar-width').trim();
  return parseInt(raw, 10) || 240;
}

async function init() {
  bindUi();
  setupDrag();
  setupResize();
  setupSidebarGrip();
  setupDropzone();

  const settings = await api.get_settings();
  if (settings) {
    state.pinned = Boolean(settings.always_on_top);
    ui.pin.setAttribute('aria-pressed', String(state.pinned));
    applyTheme(settings.theme || 'system');
    // Язык подставляем до первой отрисовки списков, иначе часть
    // текста мелькнёт на русском и тут же переключится.
    await setLanguage(settings.language || 'ru', { persist: false });
    if (settings.window && settings.window.sidebar_width) {
      setSidebarWidth(settings.window.sidebar_width);
    }
  } else {
    await setLanguage('ru', { persist: false });
  }
  // Словарь перевода приезжает через мост и может ещё не долететь,
  // если что-то дёрнет t() совсем рано (клик по настройкам до полной
  // загрузки окна). Флаг — способ дождаться этого явно, а не гадать.
  window.__konspekt_i18n_ready = true;

  await loadMeetings();
  // Разбор файлов мог продолжаться, пока окно было скрыто в трее.
  await refreshImports();
  // Обновление могло скачаться до того, как окно открыли: тогда события
  // мы не слышали, и без этого запроса кнопка установки не появилась бы.
  onUpdateState(await api.update_state());

  // Восстанавливаем состояние записи, если окно открыли посреди встречи.
  const rec = await api.recording_state();
  if (rec && rec.is_recording) {
    state.isRecording = true;
    state.recordingId = rec.meeting_id;
    const meeting = state.meetings.find((m) => m.id === rec.meeting_id);
    state.startedAt = meeting && meeting.started_at ? meeting.started_at * 1000 : Date.now();
    renderRecordingState();
    startTimer();
  }
}

document.addEventListener('DOMContentLoaded', init);
