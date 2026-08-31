/* Konspekt — фронт.
 *
 * Без фреймворков и сборки: приложение маленькое, а PyInstaller любит
 * статику. Состояние держим в одном объекте, рендер точечный.
 */

'use strict';

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
};

const el = (id) => document.getElementById(id);
const ui = {};

// Пауза, после которой реплика считается новой, в секундах. Полторы
// секунды это уже отчётливая остановка, а не вдох посреди фразы.
const TURN_GAP = 1.5;

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
      console.error(`Ошибка вызова ${String(name)}:`, err);
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
      // Статус в шапке должен сразу стать «Запись», а не остаться прежним.
      if (payload.meeting_id === state.currentId) refreshMeta(payload.meeting_id);
      break;
    case 'recording.stopped':
      state.isRecording = false;
      state.recordingId = null;
      // Запись только что появилась, значит и переслушивать теперь есть
      // что: иначе кнопки не было бы до перехода на другую встречу.
      if (payload.meeting_id === state.currentId) state.hasAudio = true;
      renderRecordingState();
      stopTimer();
      // Именно refreshMeta, а не selectMeeting: перезагрузка встречи
      // затёрла бы текст, который пользователь печатает прямо сейчас.
      if (state.currentId) refreshMeta(state.currentId);
      maybeAutoSummary(payload.meeting_id);
      break;
    case 'recording.level':
      renderLevels(payload.me, payload.them);
      break;
    case 'recording.error':
      // Запись не началась: сообщаем прямо, иначе человек будет думать,
      // что встреча пишется, и потеряет её.
      showToast(payload.message || 'Не удалось начать запись');
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
        appendSegment(payload.segment);
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
        setSummaryStatus(`Скачиваем модель… ${payload.percent}%`);
        // И в боковой панели тоже: заголовок саммари видно только на
        // своей вкладке, а ждать полтора гигабайта человек будет где
        // угодно.
        showModelLoad(payload.bytes, payload.total, 'Качаем модель заметок',
          'Один раз, 1,7 ГБ. Нужна для саммари и вопросов по встрече.');
      } else if (payload.state === 'error') {
        hideModelLoad();
        showToast(payload.message || 'Не удалось скачать модель');
      } else {
        hideModelLoad();
        setSummaryStatus('Модель готова, запускаем…');
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
      showToast(payload.error || 'Не удалось сделать заметки');
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
  }
};

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
  ui.summaryRun.textContent = has ? 'Пересобрать' : 'Сделать заметки';
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
  setSummaryStatus('Читаем расшифровку…');
  const res = await api.generate_summary(state.currentId);
  if (!res || !res.ok) {
    setBusy(false);
    renderSummary(state.current ? state.current.summary : '');
    showToast((res && res.error) || 'Не удалось сделать заметки');
  }
}

function onSummaryChunk(payload) {
  if (payload.meeting_id !== state.currentId) return;
  if (!state.summaryText) setSummaryStatus('Печатаем заметки…');
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
  showToast(payload.error || 'Не удалось получить ответ');
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
  if (!text || !state.currentId || state.llmBusy) return;
  ui.chatText.value = '';
  resizeChatInput();
  setBusy(true, state.currentId);
  const res = await api.ask(state.currentId, text);
  if (!res || !res.ok) {
    setBusy(false);
    showToast((res && res.error) || 'Не удалось задать вопрос');
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
  local: 'Заметки считаются на этом компьютере. Ничего не уходит в сеть, но первый ответ ждёт загрузки модели.',
  remote: 'Запись уходит на указанный сервер. Быстрее и умнее, но это уже не приватно.',
  'null': 'Заметки и ответы выключены. Останутся запись, расшифровка и ваши пометки.',
};

function renderLlmSettings(status) {
  if (!status) return;
  state.llm = status;
  ui.llmBackend.value = status.backend || 'local';
  ui.llmRemote.hidden = status.backend !== 'remote';
  ui.llmUrl.value = status.base_url || '';
  ui.llmModel.value = status.model || '';
  ui.llmAuto.checked = Boolean(status.auto_summary);

  let hint = LLM_HINTS[status.backend] || '';
  if (status.backend === 'local' && !status.model_ready) {
    hint += ' Модель ещё не скачана: полтора гигабайта приедут при первом запросе.';
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
  ui.llmCheckResult.textContent = 'Проверяем…';
  await saveLlmSettings();
  const res = await api.check_llm();
  if (res && res.ok) {
    ui.llmCheckResult.textContent = 'Связь есть, модель отвечает';
  } else {
    ui.llmCheckResult.textContent = (res && res.error) || 'Связи нет';
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
  const time = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === now.toDateString()) return `Сегодня, ${time}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return `Вчера, ${time}`;
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) + `, ${time}`;
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
    title.textContent = m.title || 'Без названия';

    const meta = document.createElement('div');
    meta.className = 'meeting-item__meta';
    if (m.status === 'recording') {
      const dot = document.createElement('span');
      dot.className = 'meeting-item__dot';
      meta.appendChild(dot);
      meta.appendChild(document.createTextNode('Идёт запись'));
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
      quote.title = 'Показать это место в расшифровке';
      quote.appendChild(highlight(hit.quotes[0].text, q));
      if (hit.hits > 1) {
        const more = document.createElement('span');
        more.className = 'meeting-item__more';
        more.textContent = ` ещё ${hit.hits - 1}`;
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
    del.title = 'Удалить встречу';
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
  const name = meeting.title || 'Без названия';
  const ok = await confirmDialog(
    `Удалить встречу «${name}»?`,
    'Вместе с ней исчезнут запись, расшифровка и заметки. '
    + 'Отменить это будет нельзя.'
  );
  if (!ok) return;

  await api.delete_meeting(meeting.id);
  if (state.currentId === meeting.id) {
    state.currentId = null;
    state.current = null;
  }
  // loadMeetings сама откроет следующую встречу, если удалили открытую.
  await loadMeetings();
  showToast('Встреча удалена');
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

/** Отрисовать транскрипт встречи целиком. */
function renderTranscript(segments) {
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
    showToast('Записи этой фразы нет', 3000);
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
 * Добавить реплику в конец транскрипта.
 *
 * Реплики одного источника склеиваем в один блок, но только пока между
 * ними нет заметной паузы: без склейки связная речь рвётся на лесенку,
 * а со склейкой напролом весь разговор превращается в одну простыню.
 */
function appendSegment(seg, scroll = true) {
  const isMe = seg.speaker === 'me';
  const last = ui.transcript.lastElementChild;

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
  } else {
    const turn = document.createElement('div');
    turn.className = 'turn' + (isMe ? ' turn--me' : '');
    turn.dataset.speaker = seg.speaker;
    turn.dataset.end = seg.end;
    // Начало блока: по нему находим нужное место, когда человек пришёл
    // сюда из поиска.
    turn.dataset.start = seg.start;
    turn.dataset.voice = seg.voice_id || '';

    const head = document.createElement('div');
    head.className = 'turn__head';

    // Имя участника, а не просто дорожка: за компьютером и в звонке
    // может быть несколько человек. По клику имя можно поменять, и оно
    // запомнится за голосом на будущие встречи.
    const who = document.createElement('button');
    who.className = 'turn__who';
    who.type = 'button';
    who.textContent = seg.voice_label || (isMe ? 'Я' : 'Собеседник');
    if (seg.voice_id) {
      who.dataset.voice = seg.voice_id;
      who.title = 'Нажмите, чтобы назвать говорящего';
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
    play.title = 'Переслушать фразу';
    play.innerHTML = ICON_PLAY;
    play.addEventListener('click', () => playTurn(play, turn));
    // У встреч без записи кнопки нет вовсе: лучше её отсутствие,
    // чем кнопка, которая всегда отвечает «записи нет».
    play.hidden = state.hasAudio === false;

    head.appendChild(who);
    head.appendChild(time);
    head.appendChild(play);

    const text = document.createElement('div');
    text.className = 'turn__text';
    text.textContent = seg.text;

    turn.appendChild(head);
    turn.appendChild(text);
    ui.transcript.appendChild(turn);
  }

  updateTranscriptEmpty();
  // Прокручиваем к свежей реплике, но только если человек не листает выше.
  if (scroll) {
    const pane = ui.transcript.parentElement;
    const nearBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 80;
    if (nearBottom) pane.scrollTop = pane.scrollHeight;
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
  input.setAttribute('aria-label', 'Имя говорящего');
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
      ? 'Распознавание готово (GigaAM v3)'
      : 'Модель на месте, загрузится при первой записи';
    ui.modelProgress.hidden = true;
    ui.modelAction.hidden = true;
    ui.modelBar.classList.add('is-ready');
    hideModelLoad();
  } else if (st.downloading) {
    ui.modelBar.classList.remove('is-ready');
    ui.modelAction.hidden = false;
    ui.modelAction.textContent = 'Отменить';
    ui.modelAction.dataset.act = 'cancel';
    setModelProgress(st.bytes, st.total_bytes);
    // Загрузка началась ещё до открытия окна: показываем её сразу, а не
    // ждём первого события прогресса.
    showModelLoad(st.bytes, st.total_bytes, 'Качаем модель распознавания',
      'Один раз, 220 МБ. Пользоваться программой можно уже сейчас.');
  } else {
    ui.modelBar.classList.remove('is-ready');
    ui.modelProgress.hidden = true;
    ui.modelText.textContent = st.bytes
      ? `Модель скачана частично (${fmtMb(st.bytes)} из ${fmtMb(st.total_bytes)} МБ)`
      : `Для распознавания нужна модель, ${fmtMb(st.total_bytes)} МБ`;
    ui.modelAction.hidden = false;
    ui.modelAction.textContent = st.bytes ? 'Продолжить' : 'Скачать';
    ui.modelAction.dataset.act = 'download';
  }
}

function setModelProgress(done, total) {
  ui.modelProgress.hidden = false;
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  ui.modelFill.style.width = pct + '%';
  ui.modelText.textContent = `Качаем модель: ${fmtMb(done)} из ${fmtMb(total)} МБ (${pct}%)`;
}

function onModelProgress(payload) {
  if (payload.state === 'downloading') {
    ui.modelBar.hidden = false;
    ui.modelAction.hidden = false;
    ui.modelAction.textContent = 'Отменить';
    ui.modelAction.dataset.act = 'cancel';
    setModelProgress(payload.bytes, payload.total || state.model.total_bytes);
    showModelLoad(payload.bytes, payload.total || state.model.total_bytes,
      'Качаем модель распознавания',
      'Один раз, 220 МБ. Пользоваться программой можно уже сейчас.');
    // Плашка живёт во вкладке «Транскрипт», а человек в этот момент
    // обычно смотрит на список загруженных файлов и не понимает, почему
    // расшифровки нет. Поэтому о первой загрузке говорим на всё окно.
    if (!state.modelToastShown) {
      state.modelToastShown = true;
      showToast('Качаем модель распознавания, 220 МБ. Программой можно пользоваться, '
        + 'расшифровка заработает по окончании', 12000);
    }
    return;
  }
  if (payload.state === 'error') {
    hideModelLoad();
    showToast(payload.message || 'Не удалось скачать модель');
  }
  if (payload.state === 'ready' && state.modelToastShown) {
    state.modelToastShown = false;
    hideModelLoad();
    showToast('Модель распознавания готова', 4000);
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
    ui.modelText.textContent = 'Начинаем скачивание…';
    await api.download_model();
  }
  refreshModelStatus();
}

function renderMeta(meeting) {
  const parts = [fmtDate(meeting.created_at)];
  if (meeting.duration > 1) parts.push(fmtDuration(meeting.duration));
  const labels = { draft: 'Черновик', recording: 'Запись', processing: 'Обработка', ready: 'Готово' };
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
  ui.recLabel.textContent = active ? 'Остановить' : 'Начать запись';
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
  ui.saveHint.textContent = 'Сохранено';
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

/** pywebview easy_drag ломает выделение текста, поэтому тащим сами. */
function setupDrag() {
  let dragging = false;
  let originX = 0;
  let originY = 0;

  ui.titlebar.addEventListener('mousedown', (e) => {
    if (e.button !== 0 || e.target.closest('.no-drag')) return;
    dragging = true;
    originX = e.screenX;
    originY = e.screenY;
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const dx = e.screenX - originX;
    const dy = e.screenY - originY;
    if (dx || dy) {
      originX = e.screenX;
      originY = e.screenY;
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.move_window && window.pywebview.api.move_window(dx, dy);
      }
    }
  });

  window.addEventListener('mouseup', () => { dragging = false; });
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

/* --- Настройки звука ----------------------------------------------------- */

function fillDeviceSelect(select, items, selectedId) {
  select.innerHTML = '';
  const auto = document.createElement('option');
  auto.value = '';
  const def = items.find((d) => d.is_default);
  auto.textContent = def ? `По умолчанию (${def.name})` : 'По умолчанию';
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
    showToast('Не удалось получить список аудиоустройств');
    return;
  }
  const sel = data.selected || {};
  fillDeviceSelect(ui.micSelect, data.microphones || [], sel.mic_device_id || '');
  fillDeviceSelect(ui.loopbackSelect, data.speakers || [], sel.loopback_device_id || '');
  ui.micEnabled.checked = sel.capture_mic !== false;
  ui.systemEnabled.checked = sel.capture_system !== false;
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
  if (version) ui.appVersion.textContent = 'Версия ' + version;
}

/* --- Разделы настроек ---------------------------------------------------- */

const PREFS_TITLES = {
  audio: 'Звук',
  voices: 'Голоса',
  notes: 'Заметки',
  look: 'Оформление',
  data: 'Данные',
  about: 'О программе',
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
  ui.prefsTitle.textContent = PREFS_TITLES[tab];
  if (tab === 'about') loadAbout();
  if (tab === 'data') loadUsage();
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
    ['Записи встреч', fmtBytes(u.audio_bytes)],
    ['База встреч и расшифровок', fmtBytes(u.db_bytes)],
    ['Модели распознавания и заметок', fmtBytes(u.models_bytes)],
  ];
  if (u.orphan_folders) {
    rows.push(['Записи без встречи', fmtBytes(u.orphan_bytes) + ' — можно убрать']);
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
  ui.cleanupResult.textContent = 'Убираем…';
  const res = await api.cleanup_storage();
  if (!res) {
    ui.cleanupResult.textContent = 'Не получилось';
    return;
  }
  const freed = (res.bytes || 0) + (res.db_bytes || 0);
  ui.cleanupResult.textContent = freed
    ? `Освободилось ${fmtBytes(freed)}`
    : 'Лишнего не нашлось';
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
    ui.aboutNotice.textContent = text || 'Файл NOTICE не найден рядом с программой';
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
  ui.updateResult.textContent = 'Смотрим…';
  const res = await api.check_updates_now();
  if (!res || !res.ok) {
    ui.updateResult.textContent = (res && res.error) || 'Не получилось проверить';
    return;
  }
  if (res.has_update) {
    ui.updateResult.textContent = 'Вышла версия ' + res.latest;
    showUpdateNote(res);
  } else {
    ui.updateResult.textContent = 'Установлена свежая версия';
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
  ui.updateNoteText.textContent = 'Вышла версия ' + payload.latest;
  ui.updateNoteNotes.textContent = payload.notes || '';
  ui.updateNoteNotes.hidden = !payload.notes;
  ui.updateNote.hidden = false;
}

/**
 * Ход тихого обновления.
 *
 * Пока качается — полоса, потом кнопка «Установить и перезапустить».
 * Ничего не нажимать тоже правильный путь: обновление встанет само при
 * следующем выходе из программы, и об этом сказано прямо в плашке.
 */
function onUpdateState(payload) {
  if (!ui.updateNote || !payload) return;
  if (payload.state === 'downloading') {
    ui.updateNote.hidden = false;
    ui.updateNoteText.textContent = 'Качаем версию ' + (payload.version || '');
    ui.updateNoteBar.hidden = false;
    ui.updateNoteFill.style.width = (payload.percent || 0) + '%';
    ui.updateNoteNotes.textContent = 'Скачается фоном, установится при выходе.';
    ui.updateNoteNotes.hidden = false;
    ui.updateNoteInstall.hidden = true;
  } else if (payload.state === 'ready') {
    ui.updateNote.hidden = false;
    ui.updateNoteText.textContent = 'Версия ' + (payload.version || '') + ' готова';
    ui.updateNoteBar.hidden = true;
    ui.updateNoteNotes.textContent = 'Установится сама при выходе из программы.';
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
      ? `Записано ${st.seconds.toFixed(0)} с, уже достаточно`
      : `Записано ${st.seconds.toFixed(0)} с, осталось около ${left.toFixed(0)} с`;
    ui.enrollStart.textContent = st.enough ? 'Сохранить голос' : 'Остановить';
    ui.enrollForget.hidden = true;
    // Фразы показываем только во время записи: в остальное время они
    // просто занимают место в настройках.
    ui.enrollPrompt.hidden = false;
    ui.enrollPrompt.textContent = (st.prompts || []).join(' ');
  } else {
    ui.enrollState.textContent = st.has_owner
      ? `Голос записан: ${st.owner_name}`
      : 'Голос не записан';
    ui.enrollStart.textContent = st.has_owner ? 'Перезаписать голос' : 'Записать голос';
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
      showToast('Не удалось начать запись голоса');
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
    showToast('Запись голоса отменена: речи было слишком мало');
    await refreshEnrollment();
    return;
  }
  try {
    await api.finish_enrollment('');
    showToast('Голос запомнен');
  } catch (err) {
    showToast('Не удалось сохранить голос');
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
    name.textContent = person.kind === 'owner' ? `${person.name} (это вы)` : person.name;

    const forget = document.createElement('button');
    forget.className = 'btn-ghost';
    forget.type = 'button';
    forget.textContent = 'Забыть';
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
    showToast('Нужна хотя бы одна дорожка: микрофон или системный звук');
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
      setImportsTitle(`Читаем ${count} ${plural(count, 'файл', 'файла', 'файлов')}…`);
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
  setImportsTitle('Выбор файлов…');
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
    (t) => t.status === 'running' || t.status === 'waiting').length;
  el('imports-title').textContent = left > 0
    ? `Разбор записей: осталось ${left}`
    : 'Разбор записей';
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
    cancel.title = 'Отменить';
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
    node.title = 'Открыть встречу';
    node.addEventListener('click', () => selectMeeting(task.meeting_id));
  }

  return node;
}

function importStateText(task) {
  switch (task.status) {
    case 'waiting':
      return 'в очереди';
    case 'running': {
      const pct = Math.round((task.progress || 0) * 100);
      return `${pct}%${task.stereo_split ? ', 2 канала' : ''}`;
    }
    case 'done':
      return 'готово';
    case 'failed':
      return 'не аудио';
    case 'cancelled':
      return 'отменён';
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
      showToast('Сейчас идёт запись, обновление встанет после неё');
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
  el('audio-save').addEventListener('click', saveAudioSheet);
  el('enroll-start').addEventListener('click', onEnrollClick);
  el('enroll-forget').addEventListener('click', onForgetOwner);
  el('toast-close').addEventListener('click', hideToast);
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
      document.querySelectorAll('.tab').forEach((t) => t.classList.remove('is-active'));
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
    if (settings.window && settings.window.sidebar_width) {
      setSidebarWidth(settings.window.sidebar_width);
    }
  }

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
