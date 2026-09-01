"""Экран настроек распознавания: язык и модель для чужой речи.

Проверяем не «есть ли такие строки в файле», а поведение: что программа
отдаёт UI, что принимает обратно, что видит человек и, главное, чего он
не сможет натворить. Настройки распознавания опасны тем, что ошибка тут
тихая: речь просто перестаёт распознаваться, и человек узнаёт об этом
через неделю, разбирая расшифровку.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import testenv  # noqa: F401

БЕДЫ: list[str] = []


def проверить(ок: bool, что: str) -> None:
    print(("[ok] " if ок else "[FAIL] ") + что)
    if not ок:
        БЕДЫ.append(что)


# --- сторона программы ----------------------------------------------------

def проверить_службу() -> None:
    import os
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-asrset-"))
    os.environ["KONSPEKT_DATA_DIR"] = str(tmp)

    from app.core import service as service_mod
    from app.core import settings as settings_mod

    s = settings_mod.Settings()
    зовы: list[int] = []

    class Заглушка:
        settings = s
        _whisper_dir = service_mod.AppService._whisper_dir
        asr_settings = service_mod.AppService.asr_settings
        save_asr_settings = service_mod.AppService.save_asr_settings

        class capture:
            is_recording = False

        def _build_transcriber(self):
            зовы.append(1)
            return object()

    служба = Заглушка()
    служба.transcriber = None

    было = служба.asr_settings()
    проверить(было["language"] == "ru", "язык по умолчанию уезжает в UI")
    проверить({x["code"] for x in было["sizes"]} == {"base", "small"},
              "UI получает все размеры модели")
    проверить(all("downloaded" in x for x in было["sizes"]),
              "про каждый размер сказано, скачан ли он")

    # Смена языка применяется и пересобирает движок.
    стало = служба.save_asr_settings(language="en")
    проверить(s.asr.language == "en", "выбранный язык сохранён")
    проверить(стало["language"] == "en", "UI получает новое значение назад")
    проверить(len(зовы) == 1, "движок пересобран, а не ждёт перезапуска")

    # Пустое или мусорное значение не должно ломать распознавание.
    служба.save_asr_settings(language="   ")
    проверить(s.asr.language == "en", "пустой язык не затирает выбранный")
    служба.save_asr_settings(whisper_size="огромная")
    проверить(s.asr.whisper_size == "small", "незнакомый размер не принимается")

    # Повтор того же не должен дёргать движок: перезагрузка весов дорогая.
    зовы.clear()
    служба.save_asr_settings(language="en")
    проверить(not зовы, "повтор тех же настроек не перезагружает модель")

    # Посреди записи движок не меняем.
    зовы.clear()
    Заглушка.capture.is_recording = True
    служба.save_asr_settings(language="de")
    проверить(s.asr.language == "de", "во время записи настройка всё же сохраняется")
    проверить(not зовы, "во время записи движок не подменяется на ходу")
    Заглушка.capture.is_recording = False

    # Выключение чужих языков.
    служба.save_asr_settings(detect_language=False)
    проверить(s.asr.detect_language is False, "чужие языки можно выключить")

    shutil.rmtree(tmp, ignore_errors=True)


# --- сторона окна ---------------------------------------------------------

СЦЕНАРИЙ = r"""
const fs = require('fs');
const path = require('path');

class El {
  constructor(tag) {
    this.tagName = (tag || '').toUpperCase();
    this.children = [];
    this.options = this.children;
    this.dataset = {};
    this.classList = new Set();
    this._text = '';
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = '';
    this._handlers = {};
  }
  set textContent(v) { this._text = String(v); if (v === '') this.children.length = 0; }
  get textContent() { return this._text; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(n, f) { (this._handlers[n] ||= []).push(f); }
  async fire(n) { for (const f of this._handlers[n] || []) await f({ target: this }); }
}

global.document = { createElement: (t) => new El(t) };
global.window = global;

const ui = {
  asrLanguage: new El('select'),
  asrDetect: new El('input'),
  asrDetectHint: new El('p'),
  asrSize: new El('select'),
  asrSizeHint: new El('p'),
  asrForeign: new El('div'),
};
global.ui = ui;

let СОСТОЯНИЕ = {
  language: 'ru',
  detect_language: true,
  whisper_size: 'small',
  langid_ready: true,
  active_size: 'whisper-small',
  sizes: [
    { code: 'base', bytes: 79000000, downloaded: true },
    { code: 'small', bytes: 250000000, downloaded: false },
  ],
};
const СОХРАНЕНО = [];
global.api = {
  asr_settings: async () => СОСТОЯНИЕ,
  save_asr_settings: async (f) => { СОХРАНЕНО.push(f); return { ...СОСТОЯНИЕ, ...f }; },
};
global.fmtBytes = (b) => Math.round(b / 1024 / 1024) + ' МБ';

const src = fs.readFileSync(path.join(process.argv[2], 'web', 'app.js'), 'utf8');
const от = src.indexOf('const ASR_LANGS = [');
const до = src.indexOf('/** Человеческий размер');
if (от < 0 || до < 0) throw new Error('не нашёл блок настроек распознавания');
eval(src.slice(от, до));

// Привязка обработчиков живёт отдельно, в общем месте настройки окна.
// Берём её из файла, а не вешаем сами: иначе проверка не заметит, если
// строку привязки уберут, и «настройка не сохраняется» дойдёт до людей.
const привязки = src.split('\n')
  .filter((s) => /ui\.asr\w+\.addEventListener/.test(s));
if (привязки.length < 3) throw new Error('в app.js нет привязки настроек распознавания');
eval(привязки.join('\n'));

(async () => {
  const итог = [];
  await loadAsrSettings();

  итог.push(['языки показаны по-человечески',
             ui.asrLanguage.children[0].textContent === 'Русский']);
  итог.push(['выбран текущий язык программы', ui.asrLanguage.value === 'ru']);
  итог.push(['в списке моделей оба размера', ui.asrSize.children.length === 2]);
  итог.push(['нескачанная модель помечена',
             ui.asrSize.children[1].textContent.includes('не скачана')]);
  итог.push(['скачанная не помечена как отсутствующая',
             !ui.asrSize.children[0].textContent.includes('не скачана')]);
  итог.push(['виден размер на диске',
             ui.asrSize.children[1].textContent.includes('МБ')]);

  // Смена языка уходит в программу.
  ui.asrLanguage.value = 'en';
  await ui.asrLanguage.fire('change');
  итог.push(['выбор языка сохраняется',
             СОХРАНЕНО.length === 1 && СОХРАНЕНО[0].language === 'en']);

  // Выключение чужих языков прячет ненужное.
  ui.asrDetect.checked = false;
  await ui.asrDetect.fire('change');
  итог.push(['без чужих языков выбор модели спрятан', ui.asrForeign.hidden === true]);
  ui.asrDetect.checked = true;
  await ui.asrDetect.fire('change');
  итог.push(['включили обратно — выбор модели вернулся', ui.asrForeign.hidden === false]);

  // Нет определителя языка — галочка недоступна и объяснено почему.
  СОСТОЯНИЕ = { ...СОСТОЯНИЕ, langid_ready: false };
  await loadAsrSettings();
  итог.push(['без модели языка галочка недоступна', ui.asrDetect.disabled === true]);
  итог.push(['и человеку сказано почему',
             ui.asrDetectHint.textContent.includes('не скачана')]);

  // Повторное открытие не плодит дубли в списках.
  СОСТОЯНИЕ = { ...СОСТОЯНИЕ, langid_ready: true };
  await loadAsrSettings();
  await loadAsrSettings();
  итог.push(['список языков не удваивается при повторном открытии',
             ui.asrLanguage.children.length === 12]);
  итог.push(['список моделей не удваивается', ui.asrSize.children.length === 2]);

  console.log(JSON.stringify(итог));
})();
"""


def проверить_окно() -> None:
    node = shutil.which("node")
    if not node:
        print("[пропуск] нет node, поведение экрана не проверить")
        return
    tmp = Path(tempfile.mkdtemp(prefix="konspekt-asrui-"))
    файл = tmp / "проба.js"
    файл.write_text(СЦЕНАРИЙ, encoding="utf-8")
    готово = subprocess.run(
        [node, str(файл), str(Path(__file__).resolve().parent)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    if готово.returncode != 0:
        print(готово.stderr.strip()[:1200])
        проверить(False, "сценарий экрана отработал")
        return
    for что, ок in json.loads(готово.stdout.strip().splitlines()[-1]):
        проверить(bool(ок), что)


def main() -> int:
    проверить_службу()
    проверить_окно()
    if БЕДЫ:
        print(f"\nПровалено проверок: {len(БЕДЫ)}")
        return 1
    print("\nНастройки распознавания доходят до модели и обратно")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
