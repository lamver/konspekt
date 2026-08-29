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
  pinned: true,
};

const el = (id) => document.getElementById(id);
const ui = {};

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
  renderMeetingList();
  toggleEmptyState();
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

/* --- Плашка сообщений --------------------------------------------------- */

let toastTimer = null;

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
  ui.audioSheet.hidden = false;
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
  });

  el('btn-new').addEventListener('click', createMeeting);
  el('btn-empty-new').addEventListener('click', createMeeting);
  ui.record.addEventListener('click', toggleRecording);

  el('btn-hide').addEventListener('click', () => api.hide_window());
  el('btn-minimize').addEventListener('click', () => api.minimize_window());

  el('btn-audio').addEventListener('click', openAudioSheet);
  el('audio-close').addEventListener('click', () => { ui.audioSheet.hidden = true; });
  el('audio-save').addEventListener('click', saveAudioSheet);
  el('toast-close').addEventListener('click', hideToast);
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

  const settings = await api.get_settings();
  if (settings) {
    state.pinned = Boolean(settings.always_on_top);
    ui.pin.setAttribute('aria-pressed', String(state.pinned));
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
