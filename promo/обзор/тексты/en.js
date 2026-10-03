/* Слова английской версии обзорного ролика.

   Чтобы сделать ролик на другом языке: скопировать файл в тексты/<язык>.js,
   перевести значения (ключи не трогать), положить озвучку в
   promo/голос/<язык>/vo-NN.wav и запустить собрать.py <язык>.

   «голос» — текст реплики: по нему заказывают озвучку, а пока озвучки
   нет, собрать.py прикидывает по нему длину сцены.
   В заголовках *звёздочки* выделяют слово цветом.
   Между маркерами только JSON: его читает и собрать.py.
*/
window.ТЕКСТЫ = /*JSON*/{
  "язык": "en",
  "слов_в_секунду": 2.6,
  "сцены": {
    "intro":      {"голос": "You talk. Who's taking notes?",
                   "строки": ["You talk.", "*Who's taking notes?*"]},
    "record":     {"голос": "Konspekt records any call — Zoom, Teams, Meet, or the phone. No bot joins.",
                   "заголовок": "Records *any call*", "подпись": "Zoom · Teams · Meet · phone. No bot joins."},
    "transcript": {"голос": "Every word, with every name.",
                   "заголовок": "Every word, *every name*", "подпись": "Replay any line in one click"},
    "summary":    {"голос": "Then it writes the notes for you: decisions, tasks, open questions.",
                   "заголовок": "Notes write *themselves*", "подпись": "Decisions · tasks · open questions"},
    "chat":       {"голос": "Ask your meeting anything. Who promised what?",
                   "заголовок": "Ask your *meeting*", "подпись": "“Who promised what?”"},
    "analysis":   {"голос": "Break a call down with SPIN, STAR, or the Harvard method — and see who talked how much.",
                   "заголовок": "Sales, hiring, *negotiations*", "подпись": "SPIN · STAR · Harvard method · who talked how much"},
    "search":     {"голос": "Search every meeting at once.",
                   "заголовок": "Search *every meeting*", "подпись": "Folders keep projects apart"},
    "sources":    {"голос": "Calls from your phone system and your own Telegram bot arrive by themselves.",
                   "заголовок": "Calls arrive *by themselves*", "подпись": "Phone systems · CRM · your own Telegram bot",
                   "метки": {"sftp": "Phone system (SFTP)", "bitrix": "Bitrix24", "telegram": "Telegram bot"}},
    "dictation":  {"голос": "Hold a key and dictate into any app.",
                   "заголовок": "Dictate *anywhere*", "подпись": "Hold a key, speak, the text lands at your cursor"},
    "languages":  {"голос": "It speaks your language: English, Spanish, Serbian, and Russian.",
                   "заголовок": "Speaks *your language*", "подпись": "Interface, transcripts and notes",
                   "языки": {"en": "English", "es": "Español", "sr": "Srpski", "ru": "Русский"}},
    "privacy":    {"голос": "It all runs on your computer. Nothing leaves it.",
                   "заголовок": "Runs on *your PC*", "подпись": "Personal data flagged before you share"},
    "final":      {"голос": "Konspekt. Your meetings, written up on your own computer.",
                   "имя": "Konspekt", "слоган": "Your meetings, written up on your own computer",
                   "бренд": "AI.TECH", "платформа": "Windows 10 & 11"}
  }
}/*/JSON*/;
