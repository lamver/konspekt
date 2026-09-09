"""Содержимое выдуманной встречи для снимков экрана, на четырёх языках.

Отдельным файлом, потому что текста тут больше, чем самой логики съёмки.

Почему переведено, а не оставлено на английском: страница показывает
снимок рядом с текстом на языке читателя. Английский разговор на
испанской странице выглядит как признание, что перевод — витрина, а
внутри всё равно английский. Программа же переводит интерфейс целиком,
и снимки обязаны это показывать.

Ничего настоящего: вымышленная компания, вымышленные люди, вымышленный
проект. Имена подобраны привычные для каждого языка, иначе кадр
выглядит переводом с чужого.

Реплики короткие и по делу: на снимке читают две-три строки, а не всю
встречу.
"""

from __future__ import annotations

# Кто говорит: дорожка ("me" — микрофон, "them" — собеседники), имя,
# текст, начало и конец в секундах.
ВСТРЕЧИ: dict[str, dict[str, object]] = {}


ВСТРЕЧИ["en"] = {
    "название": "Weekly sync — mobile app",
    "соседние": ["Design review", "1:1 with Maria", "Support digest"],
    "реплики": [
        ("me", "Alex", "Let's start with the release. Are we still aiming for Thursday?", 0.0, 4.6),
        ("them", "Maria", "Thursday works. The onboarding screens are done, only the empty state is left.", 5.1, 11.2),
        ("them", "Daniel", "I'd rather move it to Friday. We haven't tested offline mode on tablets.", 11.9, 18.4),
        ("me", "Alex", "How long does the tablet pass take?", 19.0, 21.8),
        ("them", "Daniel", "Half a day if nothing turns up. A day and a half if sync breaks again.", 22.4, 28.1),
        ("them", "Maria", "Then let's say Friday and announce it once, instead of moving it twice.", 28.8, 34.9),
        ("me", "Alex", "Agreed, Friday. Daniel takes the tablets, Maria finishes onboarding.", 35.5, 41.7),
        ("them", "Maria", "One more thing: support asked for a changelog in plain language, not ticket numbers.", 42.3, 49.0),
        ("me", "Alex", "Fair. I'll write it before the release and send it over for a read.", 49.6, 54.8),
    ],
    "заметки": """Release moved to Friday — tablets not tested yet
Daniel: offline mode on tablets, half a day
Maria: onboarding empty state
Changelog in plain language for support (me)
""",
    "саммари": """**Decisions**

- **The release moves to Friday.** Thursday was on the table, but offline mode has not been tested on tablets, and announcing one date once is better than moving it twice.
- The changelog for support is written in plain language, without ticket numbers.

**Who does what**

- **Daniel** — tests offline mode on tablets. Half a day if nothing turns up, a day and a half if sync breaks again.
- **Maria** — finishes the empty state on the onboarding screens.
- **Alex** — writes the changelog before the release and sends it for a read.

**Open questions**

- Sync on tablets has broken before. If it repeats, the Friday date is at risk.
""",
}


ВСТРЕЧИ["es"] = {
    "название": "Reunión semanal — app móvil",
    "соседние": ["Revisión de diseño", "1:1 con Marta", "Resumen de soporte"],
    "реплики": [
        ("me", "Alejandro", "Empecemos por la publicación. ¿Seguimos apuntando al jueves?", 0.0, 4.6),
        ("them", "Marta", "El jueves me sirve. Las pantallas de bienvenida están listas, falta el estado vacío.", 5.1, 11.2),
        ("them", "Daniel", "Yo lo movería al viernes. No hemos probado el modo sin conexión en tabletas.", 11.9, 18.4),
        ("me", "Alejandro", "¿Cuánto lleva probarlo en tabletas?", 19.0, 21.8),
        ("them", "Daniel", "Medio día si no aparece nada. Día y medio si la sincronización vuelve a fallar.", 22.4, 28.1),
        ("them", "Marta", "Entonces digamos viernes y lo anunciamos una vez, en lugar de moverlo dos veces.", 28.8, 34.9),
        ("me", "Alejandro", "De acuerdo, viernes. Daniel se encarga de las tabletas, Marta termina el onboarding.", 35.5, 41.7),
        ("them", "Marta", "Una cosa más: soporte pidió el registro de cambios en lenguaje claro, sin números de ticket.", 42.3, 49.0),
        ("me", "Alejandro", "Justo. Lo escribo antes de publicar y os lo paso para que lo leáis.", 49.6, 54.8),
    ],
    "заметки": """Publicación movida al viernes — faltan pruebas en tabletas
Daniel: modo sin conexión en tabletas, medio día
Marta: estado vacío del onboarding
Registro de cambios en lenguaje claro para soporte (yo)
""",
    "саммари": """**Decisiones**

- **La publicación se mueve al viernes.** El jueves estaba sobre la mesa, pero el modo sin conexión no se ha probado en tabletas, y anunciar una fecha una vez es mejor que moverla dos veces.
- El registro de cambios para soporte se escribe en lenguaje claro, sin números de ticket.

**Quién hace qué**

- **Daniel** — prueba el modo sin conexión en tabletas. Medio día si no aparece nada, día y medio si la sincronización vuelve a fallar.
- **Marta** — termina el estado vacío de las pantallas de bienvenida.
- **Alejandro** — escribe el registro de cambios antes de publicar y lo pasa para revisión.

**Preguntas abiertas**

- La sincronización en tabletas ya ha fallado antes. Si se repite, la fecha del viernes está en riesgo.
""",
}


ВСТРЕЧИ["sr"] = {
    "название": "Nedeljni sastanak — mobilna aplikacija",
    "соседние": ["Pregled dizajna", "1:1 sa Milicom", "Pregled podrške"],
    "реплики": [
        ("me", "Marko", "Počnimo od izdanja. Da li i dalje ciljamo na četvrtak?", 0.0, 4.6),
        ("them", "Milica", "Četvrtak mi odgovara. Ekrani za uvod su gotovi, ostalo je samo prazno stanje.", 5.1, 11.2),
        ("them", "Nikola", "Ja bih pomerio na petak. Nismo testirali rad bez interneta na tabletima.", 11.9, 18.4),
        ("me", "Marko", "Koliko traje provera na tabletima?", 19.0, 21.8),
        ("them", "Nikola", "Pola dana ako ništa ne iskrsne. Dan i po ako sinhronizacija opet pukne.", 22.4, 28.1),
        ("them", "Milica", "Onda recimo petak i najavimo jednom, umesto da pomeramo dva puta.", 28.8, 34.9),
        ("me", "Marko", "Slažem se, petak. Nikola uzima tablete, Milica završava uvodne ekrane.", 35.5, 41.7),
        ("them", "Milica", "Još nešto: podrška je tražila spisak izmena običnim jezikom, bez brojeva tiketa.", 42.3, 49.0),
        ("me", "Marko", "Pošteno. Napisaću ga pre izdanja i poslati vam da pročitate.", 49.6, 54.8),
    ],
    "заметки": """Izdanje pomereno na petak — tableti još nisu testirani
Nikola: rad bez interneta na tabletima, pola dana
Milica: prazno stanje uvodnih ekrana
Spisak izmena običnim jezikom za podršku (ja)
""",
    "саммари": """**Odluke**

- **Izdanje se pomera na petak.** Četvrtak je bio u igri, ali rad bez interneta nije testiran na tabletima, a bolje je najaviti jedan datum jednom nego ga pomerati dva puta.
- Spisak izmena za podršku piše se običnim jezikom, bez brojeva tiketa.

**Ko šta radi**

- **Nikola** — testira rad bez interneta na tabletima. Pola dana ako ništa ne iskrsne, dan i po ako sinhronizacija opet pukne.
- **Milica** — završava prazno stanje na uvodnim ekranima.
- **Marko** — piše spisak izmena pre izdanja i šalje ga na čitanje.

**Otvorena pitanja**

- Sinhronizacija na tabletima je već pucala. Ako se ponovi, petak je ugrožen.
""",
}


ВСТРЕЧИ["ru"] = {
    "название": "Планёрка — мобильное приложение",
    "соседние": ["Разбор макетов", "Один на один с Мариной", "Сводка поддержки"],
    "реплики": [
        ("me", "Алексей", "Начнём с выпуска. Мы всё ещё целимся в четверг?", 0.0, 4.6),
        ("them", "Марина", "Четверг подходит. Экраны знакомства готовы, остался только пустой экран.", 5.1, 11.2),
        ("them", "Дмитрий", "Я бы перенёс на пятницу. Мы не проверяли работу без интернета на планшетах.", 11.9, 18.4),
        ("me", "Алексей", "Сколько занимает проверка на планшетах?", 19.0, 21.8),
        ("them", "Дмитрий", "Полдня, если ничего не всплывёт. Полтора дня, если синхронизация опять сломается.", 22.4, 28.1),
        ("them", "Марина", "Тогда скажем пятница и объявим один раз, вместо того чтобы переносить дважды.", 28.8, 34.9),
        ("me", "Алексей", "Согласен, пятница. Дмитрий берёт планшеты, Марина доделывает знакомство.", 35.5, 41.7),
        ("them", "Марина", "Ещё поддержка просила список изменений человеческим языком, без номеров задач.", 42.3, 49.0),
        ("me", "Алексей", "Справедливо. Напишу до выпуска и пришлю вам на прочтение.", 49.6, 54.8),
    ],
    "заметки": """Выпуск перенесён на пятницу — планшеты ещё не проверены
Дмитрий: работа без интернета на планшетах, полдня
Марина: пустой экран в знакомстве
Список изменений человеческим языком для поддержки (я)
""",
    "саммари": """**Решения**

- **Выпуск переезжает на пятницу.** Четверг обсуждался, но работа без интернета не проверена на планшетах, а объявить одну дату один раз лучше, чем переносить её дважды.
- Список изменений для поддержки пишется человеческим языком, без номеров задач.

**Кто что делает**

- **Дмитрий** — проверяет работу без интернета на планшетах. Полдня, если ничего не всплывёт, полтора дня, если синхронизация снова сломается.
- **Марина** — доделывает пустой экран в знакомстве.
- **Алексей** — пишет список изменений до выпуска и отдаёт на прочтение.

**Открытые вопросы**

- Синхронизация на планшетах уже ломалась. Если повторится, пятница под угрозой.
""",
}
