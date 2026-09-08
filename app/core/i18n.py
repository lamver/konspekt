"""Локализация пользовательских сообщений на стороне Python.

Сюда попадают только строки, которые видит человек: ошибки распознавания,
саммари, чата, эталона голоса и диалога выбора файлов. Логи (`log.info`,
`log.exception` и т.п.) остаются русскими навсегда — их читают
разработчики, а не пользователи, см. docs/tasks/i18n-instructions.md.

Язык интерфейса живёт в Settings.language и передаётся сюда явно, а не
читается глобально: сервис уже хранит настройки в self.settings, и это
единственный источник правды.
"""

from __future__ import annotations

MESSAGES: dict[str, dict[str, str]] = {
    "python.model.other_instance": {
        "ru": "Модель уже качает другое окно Konspekt. Закройте лишнее окно и попробуйте снова.",
        "en": "Another Konspekt window is already downloading the model. Close the extra window and try again.",
        "es": "Otra ventana de Konspekt ya está descargando el modelo. Cierra la ventana sobrante e inténtalo de nuevo.",
        "sr": "Model već preuzima drugi prozor Konspekta. Zatvorite suvišni prozor i pokušajte ponovo.",
    },
    "python.model.incomplete": {
        "ru": "Модель скачалась не полностью, попробуйте ещё раз",
        "en": "The model didn't download completely, please try again",
        "es": "El modelo no se descargó por completo, inténtalo de nuevo",
        "sr": "Model nije preuzet do kraja, pokušajte ponovo",
    },
    "python.model.repairing": {
        "ru": "Файлы модели оказались повреждены, качаем заново",
        "en": "The model files turned out to be corrupted, downloading again",
        "es": "Los archivos del modelo estaban dañados, descargando de nuevo",
        "sr": "Fajlovi modela su oštećeni, preuzimamo ponovo",
    },
    "python.model.corrupted": {
        "ru": "Модель распознавания повреждена и не чинится перезакачкой. Напишите нам, приложив журнал.",
        "en": "The speech model is corrupted and re-downloading won't fix it. Please contact us and attach the log.",
        "es": "El modelo de reconocimiento está dañado y volver a descargarlo no lo arregla. Escríbenos y adjunta el registro.",
        "sr": "Model za prepoznavanje je oštećen i ponovno preuzimanje to ne popravlja. Pišite nam i priložite zapisnik.",
    },
    "python.summary.disabled": {
        "ru": "Синтез выключен в настройках",
        "en": "Note generation is turned off in settings",
        "es": "La generación de notas está desactivada en los ajustes",
        "sr": "Izrada beleški je isključena u podešavanjima",
    },
    "python.summary.busy": {
        "ru": "Модель уже занята",
        "en": "The model is already busy",
        "es": "El modelo ya está ocupado",
        "sr": "Model je već zauzet",
    },
    "python.summary.meeting_not_found": {
        "ru": "Встреча не найдена",
        "en": "Meeting not found",
        "es": "Reunión no encontrada",
        "sr": "Sastanak nije pronađen",
    },
    "python.summary.nothing": {
        "ru": "Нечего обрабатывать: нет ни расшифровки, ни заметок",
        "en": "Nothing to process: no transcript and no notes",
        "es": "No hay nada que procesar: no hay transcripción ni notas",
        "sr": "Nema šta da se obradi: nema ni transkripta ni beleški",
    },
    "python.summary.error": {
        "ru": "Не удалось сделать заметки",
        "en": "Couldn't generate notes",
        "es": "No se pudieron generar las notas",
        "sr": "Beleške nisu mogle da se naprave",
    },
    "python.chat.empty_question": {
        "ru": "Пустой вопрос",
        "en": "Empty question",
        "es": "Pregunta vacía",
        "sr": "Prazno pitanje",
    },
    "python.chat.disabled": {
        "ru": "Синтез выключен в настройках",
        "en": "Note generation is turned off in settings",
        "es": "La generación de notas está desactivada en los ajustes",
        "sr": "Izrada beleški je isključena u podešavanjima",
    },
    "python.chat.busy": {
        "ru": "Модель уже занята",
        "en": "The model is already busy",
        "es": "El modelo ya está ocupado",
        "sr": "Model je već zauzet",
    },
    "python.chat.meeting_not_found": {
        "ru": "Встреча не найдена",
        "en": "Meeting not found",
        "es": "Reunión no encontrada",
        "sr": "Sastanak nije pronađen",
    },
    "python.chat.error": {
        "ru": "Не удалось получить ответ",
        "en": "Couldn't get an answer",
        "es": "No se pudo obtener una respuesta",
        "sr": "Odgovor nije mogao da se dobije",
    },
    "python.version_check.error": {
        "ru": "Не удалось связаться с сервером обновлений",
        "en": "Couldn't reach the update server",
        "es": "No se pudo contactar con el servidor de actualizaciones",
        "sr": "Server za ažuriranja nije mogao da se kontaktira",
    },
    "python.enroll.recording_conflict": {
        "ru": "Нельзя записывать голос во время встречи",
        "en": "Can't record a voice while a meeting is recording",
        "es": "No se puede grabar una voz mientras se graba una reunión",
        "sr": "Glas ne može da se snima tokom snimanja sastanka",
    },
    "python.enroll.model_unavailable": {
        "ru": "Модель распознавания голоса недоступна: {exc}",
        "en": "The voice recognition model is unavailable: {exc}",
        "es": "El modelo de reconocimiento de voz no está disponible: {exc}",
        "sr": "Model za prepoznavanje glasa nije dostupan: {exc}",
    },
    "python.enroll.too_little": {
        "ru": "Речи слишком мало: нужно хотя бы {min} секунд",
        "en": "Too little speech: at least {min} seconds is needed",
        "es": "Hay muy poco habla: se necesitan al menos {min} segundos",
        "sr": "Govora je premalo: potrebno je bar {min} sekundi",
    },
    "python.enroll.not_started": {
        "ru": "Запись голоса не начиналась",
        "en": "Voice recording hasn't started",
        "es": "La grabación de voz no ha comenzado",
        "sr": "Snimanje glasa nije počelo",
    },
    "python.file_dialog.audio_filter": {
        "ru": "Аудио и видео (*.mp3;*.wav;*.m4a;*.ogg;*.opus;*.flac;*.aac;*.wma;*.mp4;*.mkv;*.mov;*.webm;*.avi)",
        "en": "Audio and video (*.mp3;*.wav;*.m4a;*.ogg;*.opus;*.flac;*.aac;*.wma;*.mp4;*.mkv;*.mov;*.webm;*.avi)",
        "es": "Audio y vídeo (*.mp3;*.wav;*.m4a;*.ogg;*.opus;*.flac;*.aac;*.wma;*.mp4;*.mkv;*.mov;*.webm;*.avi)",
        "sr": "Audio i video (*.mp3;*.wav;*.m4a;*.ogg;*.opus;*.flac;*.aac;*.wma;*.mp4;*.mkv;*.mov;*.webm;*.avi)",
    },
    "python.file_dialog.all_files": {
        "ru": "Все файлы (*.*)",
        "en": "All files (*.*)",
        "es": "Todos los archivos (*.*)",
        "sr": "Svi fajlovi (*.*)",
    },
}


def t(key: str, language: str = "ru", **vars: object) -> str:
    """Перевести ключ сообщения. Неизвестный язык откатывается на ru."""
    entry = MESSAGES.get(key)
    if not entry:
        return key
    text = entry.get(language) or entry.get("ru") or key
    if vars:
        try:
            return text.format(**vars)
        except (KeyError, IndexError):
            return text
    return text
