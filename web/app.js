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
  model: {},
  pinned: true,
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
      renderRecordingState();
      stopTimer();
      // Именно refreshMeta, а не selectMeeting: перезагрузка встречи
      // затёрла бы текст, который пользователь печатает прямо сейчас.
      if (state.currentId) refreshMeta(state.currentId);
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
  }
};

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
  const items = q
    ? state.meetings.filter((m) => (m.title || '').toLowerCase().includes(q)
        || (m.notes || '').toLowerCase().includes(q))
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

    node.append(title, meta);
    node.addEventListener('click', () => selectMeeting(m.id));
    ui.list.appendChild(node);
  }
}

async function selectMeeting(id) {
  // Не теряем несохранённые правки при переключении.
  flushNotes();

  const meeting = await api.get_meeting(id);
  if (!meeting) return;

  state.currentId = id;
  state.current = meeting;

  ui.title.value = meeting.title || '';
  ui.notes.value = meeting.notes || '';
  renderMeta(meeting);
  renderTranscript(meeting.segments || []);
  renderMeetingList();
  toggleEmptyState();
}

/* --- Транскрипт --------------------------------------------------------- */

/** Отрисовать транскрипт встречи целиком. */
function renderTranscript(segments) {
  ui.transcript.innerHTML = '';
  for (const seg of segments) appendSegment(seg, false);
  updateTranscriptEmpty();
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

    head.appendChild(who);
    head.appendChild(time);

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
  } else if (st.downloading) {
    ui.modelBar.classList.remove('is-ready');
    ui.modelAction.hidden = false;
    ui.modelAction.textContent = 'Отменить';
    ui.modelAction.dataset.act = 'cancel';
    setModelProgress(st.bytes, st.total_bytes);
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
    return;
  }
  if (payload.state === 'error') {
    showToast(payload.message || 'Не удалось скачать модель');
  }
  refreshModelStatus();
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
  ui.audioSheet.hidden = false;
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

/* --- Инициализация ------------------------------------------------------ */

function bindUi() {
  Object.assign(ui, {
    titlebar: el('titlebar'),
    list: el('meeting-list'),
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
  });

  el('btn-new').addEventListener('click', createMeeting);
  el('btn-empty-new').addEventListener('click', createMeeting);
  ui.record.addEventListener('click', toggleRecording);

  el('btn-hide').addEventListener('click', () => api.hide_window());
  el('btn-minimize').addEventListener('click', () => api.minimize_window());

  el('btn-audio').addEventListener('click', openAudioSheet);
  el('audio-close').addEventListener('click', () => { ui.audioSheet.hidden = true; });
  el('theme-switch').addEventListener('click', onThemeClick);
  el('audio-save').addEventListener('click', saveAudioSheet);
  el('enroll-start').addEventListener('click', onEnrollClick);
  el('enroll-forget').addEventListener('click', onForgetOwner);
  el('toast-close').addEventListener('click', hideToast);
  ui.modelAction.addEventListener('click', onModelAction);
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
      renderMeetingList();
    }
    if (e.key === 'Escape' && !ui.audioSheet.hidden) ui.audioSheet.hidden = true;
  });

  // Страховка от потери правок при закрытии окна.
  window.addEventListener('beforeunload', flushNotes);
}

async function init() {
  bindUi();
  setupDrag();
  setupResize();

  const settings = await api.get_settings();
  if (settings) {
    state.pinned = Boolean(settings.always_on_top);
    ui.pin.setAttribute('aria-pressed', String(state.pinned));
    applyTheme(settings.theme || 'system');
  }

  await loadMeetings();

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
