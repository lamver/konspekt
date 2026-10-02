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
    # Ответы своего бота в Telegram (core/telegram_bot.py).
    "python.telegram.paired": {
        "ru": "Готово, бот привязан к этому чату. Пересылайте голосовые, кружки или аудио — пришлю расшифровку, а если в Конспекте есть модель для заметок, то и итоги. Ссылку на видео тоже можно прислать.",
        "en": "Done, the bot is linked to this chat. Forward voice messages, video notes or audio — I'll send back the transcript, and the summary too if Konspekt has a notes model. You can also send a video link.",
        "es": "Listo, el bot está vinculado a este chat. Reenvía mensajes de voz, videonotas o audio: te enviaré la transcripción, y el resumen si Konspekt tiene un modelo de notas. También puedes enviar un enlace de vídeo.",
        "sr": "Gotovo, bot je povezan sa ovim četom. Prosleđujte glasovne poruke, kružiće ili audio — poslaću transkript, a ako Konspekt ima model za beleške, i rezime. Možete poslati i link na video.",
    },
    "python.telegram.need_code": {
        "ru": "Это бот Конспекта. Чтобы привязать его, пришлите код из программы: Настройки → Telegram.",
        "en": "This is a Konspekt bot. To link it, send the code from the app: Settings → Telegram.",
        "es": "Este es un bot de Konspekt. Para vincularlo, envía el código de la aplicación: Ajustes → Telegram.",
        "sr": "Ovo je Konspekt bot. Da biste ga povezali, pošaljite kod iz programa: Podešavanja → Telegram.",
    },
    "python.telegram.foreign": {
        "ru": "Этот бот работает только для своего владельца.",
        "en": "This bot only works for its owner.",
        "es": "Este bot solo funciona para su propietario.",
        "sr": "Ovaj bot radi samo za svog vlasnika.",
    },
    "python.telegram.help": {
        "ru": "Пришлите голосовое, кружок, аудио или видео (до 20 МБ) либо ссылку на запись — расшифрую на компьютере и пришлю текст.",
        "en": "Send a voice message, video note, audio or video (up to 20 MB) or a link to a recording — I'll transcribe it on the computer and send the text.",
        "es": "Envía un mensaje de voz, videonota, audio o vídeo (hasta 20 MB) o un enlace a una grabación: lo transcribiré en el ordenador y te enviaré el texto.",
        "sr": "Pošaljite glasovnu poruku, kružić, audio ili video (do 20 MB) ili link na snimak — prepisaću ga na računaru i poslati tekst.",
    },
    "python.telegram.too_big": {
        "ru": "Файл больше 20 МБ: такие Telegram ботам не отдаёт. Перетащите запись в окно Конспекта или пришлите ссылку.",
        "en": "The file is over 20 MB: Telegram doesn't give such files to bots. Drop the recording into the Konspekt window or send a link.",
        "es": "El archivo supera los 20 MB: Telegram no entrega archivos así a los bots. Arrastra la grabación a la ventana de Konspekt o envía un enlace.",
        "sr": "Fajl je veći od 20 MB: Telegram takve fajlove ne daje botovima. Prevucite snimak u prozor Konspekta ili pošaljite link.",
    },
    "python.telegram.download_failed": {
        "ru": "Не получилось скачать файл из Telegram. Попробуйте прислать ещё раз.",
        "en": "Couldn't download the file from Telegram. Please try sending it again.",
        "es": "No se pudo descargar el archivo de Telegram. Intenta enviarlo de nuevo.",
        "sr": "Fajl nije mogao da se preuzme iz Telegrama. Pokušajte da ga pošaljete ponovo.",
    },
    "python.telegram.trial_over": {
        "ru": "Пробные встречи закончились: новые записи расшифровываются с лицензией. Купить: https://aisearch.ru/pricing/license/konspekt",
        "en": "The trial meetings are used up: new recordings are transcribed with a license. Buy: https://aisearch.ru/pricing/license/konspekt",
        "es": "Las reuniones de prueba se han agotado: las grabaciones nuevas se transcriben con licencia. Comprar: https://aisearch.ru/pricing/license/konspekt",
        "sr": "Probni sastanci su potrošeni: novi snimci se prepisuju uz licencu. Kupi: https://aisearch.ru/pricing/license/konspekt",
    },
    "python.telegram.not_audio": {
        "ru": "В этом файле не нашлось звука.",
        "en": "No audio found in this file.",
        "es": "No se encontró audio en este archivo.",
        "sr": "U ovom fajlu nije pronađen zvuk.",
    },
    "python.telegram.accepted": {
        "ru": "Принял, расшифровываю.",
        "en": "Got it, transcribing.",
        "es": "Recibido, transcribiendo.",
        "sr": "Primljeno, prepisujem.",
    },
    "python.telegram.accepted_link": {
        "ru": "Принял ссылку: скачаю звук и расшифрую.",
        "en": "Got the link: I'll download the audio and transcribe it.",
        "es": "Enlace recibido: descargaré el audio y lo transcribiré.",
        "sr": "Link primljen: preuzeću zvuk i prepisati ga.",
    },
    "python.telegram.failed": {
        "ru": "Не получилось расшифровать. Подробности — в окне Конспекта, в очереди разбора.",
        "en": "Couldn't transcribe it. Details are in the Konspekt window, in the import queue.",
        "es": "No se pudo transcribir. Los detalles están en la ventana de Konspekt, en la cola de importación.",
        "sr": "Prepis nije uspeo. Detalji su u prozoru Konspekta, u redu za obradu.",
    },
    "python.telegram.empty": {
        "ru": "Речи в записи не нашлось.",
        "en": "No speech found in the recording.",
        "es": "No se encontró voz en la grabación.",
        "sr": "U snimku nije pronađen govor.",
    },
    "python.telegram.transcript": {
        "ru": "Расшифровка:",
        "en": "Transcript:",
        "es": "Transcripción:",
        "sr": "Transkript:",
    },
    "python.telegram.summary": {
        "ru": "Итоги:",
        "en": "Summary:",
        "es": "Resumen:",
        "sr": "Rezime:",
    },
    "python.telegram.no_model": {
        "ru": "Итоги делает модель для заметок, а её в Конспекте пока нет. Скачайте её в Настройки → Заметки, и итоги будут приходить сами.",
        "en": "Summaries are written by the notes model, which Konspekt doesn't have yet. Download it in Settings → Notes, and summaries will arrive automatically.",
        "es": "Los resúmenes los escribe el modelo de notas, que Konspekt aún no tiene. Descárgalo en Ajustes → Notas y los resúmenes llegarán solos.",
        "sr": "Rezime piše model za beleške, a Konspekt ga još nema. Preuzmite ga u Podešavanja → Beleške i rezimei će stizati sami.",
    },
    "python.telegram.summary_later": {
        "ru": "Итоги сейчас не сделать: модель занята или недоступна. Их можно сделать позже в окне Конспекта.",
        "en": "Can't make a summary right now: the model is busy or unavailable. You can make it later in the Konspekt window.",
        "es": "Ahora no se puede hacer el resumen: el modelo está ocupado o no disponible. Puedes hacerlo luego en la ventana de Konspekt.",
        "sr": "Rezime sada ne može: model je zauzet ili nedostupan. Možete ga napraviti kasnije u prozoru Konspekta.",
    },
    # Название в заголовке окна и на панели задач. По-русски по-русски:
    # человек с русским интерфейсом видит «Конспект», а не латиницу.
    "python.app.name": {
        "ru": "Конспект",
        "en": "Konspekt",
        "es": "Konspekt",
        "sr": "Konspekt",
    },
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
    "python.link.not_link": {
        "ru": "Это не похоже на ссылку. Скопируйте адрес страницы с записью целиком.",
        "en": "This doesn't look like a link. Copy the full address of the page with the recording.",
        "es": "Esto no parece un enlace. Copia la dirección completa de la página con la grabación.",
        "sr": "Ovo ne liči na link. Kopirajte celu adresu stranice sa snimkom.",
    },
    "python.folder.new": {
        "ru": "Новая папка",
        "en": "New folder",
        "es": "Carpeta nueva",
        "sr": "Nova fascikla",
    },
    "python.trial.record": {
        "ru": "Пробный период закончился: {limit} встреч позади. Все записи на месте, их можно смотреть, искать и копировать. Чтобы записывать дальше, купите лицензию.",
        "en": "The trial is over: {limit} meetings recorded. Everything you recorded stays here to view, search and copy. Buy a license to keep recording.",
        "es": "La prueba ha terminado: {limit} reuniones grabadas. Todo lo grabado sigue aquí para ver, buscar y copiar. Compra una licencia para seguir grabando.",
        "sr": "Probni period je istekao: {limit} sastanaka je snimljeno. Sve snimljeno ostaje ovde za pregled, pretragu i kopiranje. Kupite licencu da biste nastavili snimanje.",
    },
    "python.trial.import_part": {
        "ru": "Загружено {n} из {total}: на остальные пробных встреч не хватило. Купите лицензию, чтобы загружать дальше.",
        "en": "Imported {n} of {total}: the trial has no meetings left for the rest. Buy a license to keep importing.",
        "es": "Se importaron {n} de {total}: la prueba no tiene reuniones para el resto. Compra una licencia para seguir importando.",
        "sr": "Učitano {n} od {total}: probni period nema više sastanaka za ostale. Kupite licencu da biste nastavili.",
    },
    "python.trial.llm": {
        "ru": "Пробный период закончился. Вопросы к встрече, разборы и пересборка заметок работают с лицензией. Готовые заметки остаются с вами.",
        "en": "The trial is over. Questions, analyses and rebuilding notes need a license. Your finished notes stay with you.",
        "es": "La prueba ha terminado. Las preguntas, los análisis y rehacer notas requieren licencia. Tus notas terminadas siguen contigo.",
        "sr": "Probni period je istekao. Pitanja, analize i ponovna izrada beleški rade uz licencu. Gotove beleške ostaju vama.",
    },
    "python.analysis.unknown": {
        "ru": "Такого разбора нет",
        "en": "No such analysis",
        "es": "No existe ese análisis",
        "sr": "Takva analiza ne postoji",
    },
    "python.analysis.part": {
        "ru": "Встреча длинная, читаем часть {i} из {n}…",
        "en": "Long meeting, reading part {i} of {n}…",
        "es": "Reunión larga, leyendo la parte {i} de {n}…",
        "sr": "Sastanak je dug, čitamo deo {i} od {n}…",
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
