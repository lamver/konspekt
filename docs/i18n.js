/* Тексты страницы на четырёх языках.
 *
 * Отдельным файлом, а не внутри index.html: там его правили бы вместе
 * с вёрсткой и однажды перевели бы половину страницы.
 *
 * Английский основной: страницу смотрят со всего мира, и он отсекает
 * меньше всего людей. Остальные три — языки, на которых говорит сам
 * интерфейс программы: обещать перевод и показывать страницу только
 * по-английски было бы странно.
 *
 * Это маркетинг, а не перевод строка в строку: формулировки под каждый
 * язык свои. Granola знают в англоязычной среде и почти не знают в
 * русской, поэтому сравнение с ней стоит только там, где оно понятно.
 */

const I18N = {
  en: {
    label: 'English',
    htmlLang: 'en',
    title: 'Konspekt — meeting notes that never leave your computer',
    tagline: 'A smart notepad for meetings. Konspekt records the conversation, transcribes it and turns it into notes — entirely on your own machine.',
    lead: 'A local, privacy-first alternative to Granola. Nothing is uploaded anywhere: not the audio, not the transcript, not your notes.',
    download: 'Download for Windows',
    downloadNote: 'Free. Installs in a minute and works offline afterwards.',
    sourceLink: 'Source code on GitHub',

    featuresTitle: 'What it does',
    features: [
      ['Records both sides', 'Your microphone and the system audio, so the other person in a call is captured too.'],
      ['Transcribes locally', 'Russian through GigaAM, other languages through Whisper. No cloud, no account, no API key.'],
      ['Tells speakers apart', 'Name someone once and Konspekt recognises their voice in later meetings.'],
      ['Writes the summary', 'Decisions, who does what, and what is still open — from the actual conversation.'],
      ['Answers questions', 'Ask who promised what or what was agreed, and get an answer from the transcript.'],
      ['Imports recordings', 'Drag in files from any source; each becomes a meeting with its own transcript.'],
    ],

    shotsTitle: 'What it looks like',
    shots: [
      ['summary', 'The summary', 'Decisions, owners and open questions, pulled out of the conversation.'],
      ['transcript', 'The transcript', 'Every line attributed to the person who said it.'],
      ['notes', 'Your own notes', 'Type your own points during the call; they sit next to the transcript.'],
      ['dictation', 'Dictation', 'Hold a hotkey, speak, release — the text lands wherever your cursor is, in any app.'],
    ],

    privacyTitle: 'Why local matters',
    privacyText: 'Meetings are where salaries, deals and disagreements get discussed. Sending that to someone else\'s server means trusting a company you have never met with the most sensitive half of your work. Konspekt keeps the audio, the transcript and the notes in a folder on your disk. Nothing leaves it unless you connect an external model yourself.',

    langsTitle: 'Speaks your language',
    langsText: 'The interface is available in English, Spanish, Serbian and Russian. Speech recognition handles Russian particularly well: it runs on GigaAM, a model built for it, rather than on a translation of an English-first tool.',

    licenseTitle: 'Open source',
    licenseText: 'Konspekt is distributed under FSL-1.1-MIT: read the code, change it, use it. The single restriction is repackaging it as a competing commercial product. Every release becomes plain MIT two years after it ships.',
    licenseLink: 'Read the license',

    verifyTitle: 'Verifying the download',
    verifyText: 'The installer ships with SHA256SUMS and a GitHub build provenance attestation, so you can confirm the file came from this repository and was built by CI rather than by someone on the internet.',
  },

  es: {
    label: 'Español',
    htmlLang: 'es',
    title: 'Konspekt — notas de reuniones que nunca salen de tu ordenador',
    tagline: 'Un cuaderno inteligente para reuniones. Konspekt graba la conversación, la transcribe y la convierte en notas, todo en tu propio ordenador.',
    lead: 'Una alternativa local y privada a Granola. No se sube nada a ningún sitio: ni el audio, ni la transcripción, ni tus notas.',
    download: 'Descargar para Windows',
    downloadNote: 'Gratis. Se instala en un minuto y luego funciona sin conexión.',
    sourceLink: 'Código fuente en GitHub',

    featuresTitle: 'Qué hace',
    features: [
      ['Graba ambos lados', 'Tu micrófono y el sonido del sistema, así también se registra a la otra persona en una llamada.'],
      ['Transcribe en local', 'Ruso con GigaAM, otros idiomas con Whisper. Sin nube, sin cuenta, sin clave de API.'],
      ['Distingue a quien habla', 'Pon un nombre una vez y Konspekt reconocerá esa voz en las siguientes reuniones.'],
      ['Escribe el resumen', 'Decisiones, quién hace qué y qué queda abierto, sacado de la conversación real.'],
      ['Responde preguntas', 'Pregunta quién prometió qué o en qué se quedó, y la respuesta sale de la transcripción.'],
      ['Importa grabaciones', 'Arrastra archivos de cualquier origen; cada uno se convierte en una reunión con su transcripción.'],
    ],

    shotsTitle: 'Cómo se ve',
    shots: [
      ['summary', 'El resumen', 'Decisiones, responsables y preguntas abiertas, extraídas de la conversación.'],
      ['transcript', 'La transcripción', 'Cada línea atribuida a quien la dijo.'],
      ['notes', 'Tus propias notas', 'Escribe tus apuntes durante la llamada; quedan junto a la transcripción.'],
      ['dictation', 'Dictado', 'Mantén una combinación de teclas, habla, suelta: el texto aparece donde esté el cursor, en cualquier aplicación.'],
    ],

    privacyTitle: 'Por qué importa que sea local',
    privacyText: 'En las reuniones se habla de sueldos, acuerdos y desacuerdos. Enviar eso al servidor de otra empresa significa confiar la mitad más delicada de tu trabajo a gente que no conoces. Konspekt guarda el audio, la transcripción y las notas en una carpeta de tu disco. Nada sale de ahí salvo que conectes tú mismo un modelo externo.',

    langsTitle: 'Habla tu idioma',
    langsText: 'La interfaz está disponible en inglés, español, serbio y ruso. El reconocimiento de voz funciona especialmente bien en ruso: usa GigaAM, un modelo creado para ese idioma, y no la adaptación de una herramienta pensada en inglés.',

    licenseTitle: 'Código abierto',
    licenseText: 'Konspekt se distribuye bajo FSL-1.1-MIT: puedes leer el código, modificarlo y usarlo. La única restricción es reempaquetarlo como producto comercial competidor. Cada versión pasa a ser MIT normal dos años después de publicarse.',
    licenseLink: 'Leer la licencia',

    verifyTitle: 'Cómo verificar la descarga',
    verifyText: 'El instalador viene con SHA256SUMS y con una firma de procedencia de GitHub, así puedes comprobar que el archivo salió de este repositorio y lo compiló el servidor, no una persona cualquiera.',
  },

  sr: {
    label: 'Srpski',
    htmlLang: 'sr-Latn',
    title: 'Konspekt — beleške sa sastanaka koje ne napuštaju tvoj računar',
    tagline: 'Pametna beležnica za sastanke. Konspekt snima razgovor, prepisuje ga i pretvara u beleške — sve na tvom računaru.',
    lead: 'Lokalna alternativa Granoli, sa privatnošću na prvom mestu. Ništa se nigde ne šalje: ni zvuk, ni transkript, ni tvoje beleške.',
    download: 'Preuzmi za Windows',
    downloadNote: 'Besplatno. Instalira se za minut i posle radi bez interneta.',
    sourceLink: 'Izvorni kod na GitHubu',

    featuresTitle: 'Šta radi',
    features: [
      ['Snima obe strane', 'Tvoj mikrofon i zvuk sistema, pa se sagovornik u pozivu takođe snima.'],
      ['Prepisuje lokalno', 'Ruski preko GigaAM-a, ostale jezike preko Whispera. Bez oblaka, bez naloga, bez API ključa.'],
      ['Razlikuje govornike', 'Imenuj nekoga jednom i Konspekt će prepoznati taj glas na sledećim sastancima.'],
      ['Piše sažetak', 'Odluke, ko šta radi i šta je ostalo otvoreno — iz stvarnog razgovora.'],
      ['Odgovara na pitanja', 'Pitaj ko je šta obećao ili šta je dogovoreno, a odgovor stiže iz transkripta.'],
      ['Uvozi snimke', 'Prevuci fajlove bilo kog porekla; svaki postaje sastanak sa svojim transkriptom.'],
    ],

    shotsTitle: 'Kako izgleda',
    shots: [
      ['summary', 'Sažetak', 'Odluke, zaduženja i otvorena pitanja, izvučeni iz razgovora.'],
      ['transcript', 'Transkript', 'Svaka replika pripisana onome ko ju je izgovorio.'],
      ['notes', 'Tvoje beleške', 'Zapisuj svoje tačke tokom poziva; stoje uz transkript.'],
      ['dictation', 'Diktiranje', 'Drži prečicu, govori, pusti — tekst se pojavi tamo gde je kursor, u bilo kojoj aplikaciji.'],
    ],

    privacyTitle: 'Zašto je važno da je lokalno',
    privacyText: 'Na sastancima se priča o platama, dogovorima i neslaganjima. Slati to na tuđi server znači poveriti najosetljiviju polovinu svog posla ljudima koje nikad nisi video. Konspekt drži zvuk, transkript i beleške u folderu na tvom disku. Odatle ništa ne izlazi osim ako sam ne priključiš spoljni model.',

    langsTitle: 'Govori tvoj jezik',
    langsText: 'Interfejs postoji na engleskom, španskom, srpskom i ruskom. Prepoznavanje govora posebno dobro radi na ruskom: koristi GigaAM, model napravljen za taj jezik, a ne prilagođenu alatku smišljenu na engleskom.',

    licenseTitle: 'Otvoren kod',
    licenseText: 'Konspekt se objavljuje pod FSL-1.1-MIT: kod možeš čitati, menjati i koristiti. Jedino ograničenje je pakovanje u konkurentski komercijalni proizvod. Svaka verzija dve godine nakon izdanja postaje obična MIT.',
    licenseLink: 'Pročitaj licencu',

    verifyTitle: 'Provera preuzetog fajla',
    verifyText: 'Uz instalaciju idu SHA256SUMS i GitHub potvrda o poreklu, pa možeš proveriti da fajl dolazi iz ovog repozitorijuma i da ga je sastavio server, a ne neko sa interneta.',
  },

  ru: {
    label: 'Русский',
    htmlLang: 'ru',
    title: 'Konspekt — заметки со встреч, которые остаются на вашем компьютере',
    tagline: 'Умный блокнот для встреч. Konspekt слушает разговор, расшифровывает его и превращает в заметки — целиком на вашем компьютере.',
    lead: 'Запись, расшифровка и заметки никуда не уходят: ни звук, ни текст, ни ваши тезисы.',
    download: 'Скачать для Windows',
    downloadNote: 'Бесплатно. Ставится за минуту, дальше работает без интернета.',
    sourceLink: 'Исходный код на GitHub',

    featuresTitle: 'Что умеет',
    features: [
      ['Пишет обе стороны', 'Микрофон и системный звук, поэтому собеседник в звонке тоже записывается.'],
      ['Расшифровывает на месте', 'Русский через GigaAM, другие языки через Whisper. Без облака, без учётной записи, без ключа.'],
      ['Различает голоса', 'Назовите человека один раз, и на следующих встречах он подпишется сам.'],
      ['Пишет саммари', 'Решения, кто что делает и что осталось нерешённым — из самого разговора.'],
      ['Отвечает на вопросы', 'Спросите, кто что обещал и о чём договорились, ответ придёт из расшифровки.'],
      ['Загружает записи', 'Бросьте файлы в окно: на каждую запись заведётся своя встреча с расшифровкой.'],
    ],

    shotsTitle: 'Как выглядит',
    shots: [
      ['summary', 'Саммари', 'Решения, ответственные и открытые вопросы, вынутые из разговора.'],
      ['transcript', 'Транскрипт', 'Каждая реплика подписана тем, кто её произнёс.'],
      ['notes', 'Свои заметки', 'Пишите тезисы прямо во время встречи, они лежат рядом с расшифровкой.'],
      ['dictation', 'Диктовка', 'Зажали клавиши, наговорили, отпустили — текст появился там, где стоит курсор, в любой программе.'],
    ],

    privacyTitle: 'Почему важно, что всё локально',
    privacyText: 'На встречах обсуждают зарплаты, сделки и разногласия. Отправить это на чужой сервер — значит доверить самую чувствительную половину своей работы людям, которых вы никогда не видели. Konspekt держит звук, расшифровку и заметки в папке на вашем диске. Оттуда ничего не уходит, пока вы сами не подключите внешнюю модель.',

    langsTitle: 'Говорит на вашем языке',
    langsText: 'Интерфейс есть на английском, испанском, сербском и русском. С русской речью программа справляется особенно хорошо: она работает на GigaAM, модели, сделанной для этого языка, а не на переделке англоязычного инструмента.',

    licenseTitle: 'Открытый код',
    licenseText: 'Konspekt распространяется по FSL-1.1-MIT: код можно читать, править и использовать. Нельзя одно — собрать из него конкурирующий продукт на продажу. Каждая версия через два года после выпуска становится обычной MIT.',
    licenseLink: 'Прочитать лицензию',

    verifyTitle: 'Как проверить скачанное',
    verifyText: 'Рядом с установщиком лежат SHA256SUMS и подпись происхождения от GitHub: можно убедиться, что файл собран из этого репозитория на сервере, а не кем-то в интернете.',
  },
};
