"""Выдуманные данные для снимков страницы-инструкции, на четырёх языках.

Дополняет tools/встреча_для_снимков.py: там планёрка команды, здесь всё
остальное, что нужно кадрам инструкции, — звонок клиенту с разбором по
SPIN, вопросы к планёрке, идущая запись, папки, Telegram.

Ничего настоящего: компании, люди, телефоны и почты выдуманы. Телефоны
вида +7 900 000-00-00, почты на example.com, номер карты тестовый
(4111 1111 1111 1111 — его публикуют платёжные системы для проверок).
"""

from __future__ import annotations

ДАННЫЕ: dict[str, dict[str, object]] = {}


ДАННЫЕ["ru"] = {
    "папка_команда": "Команда",
    "папка_продажи": "Звонки отдела продаж",
    "звонок": {
        "название": "Звонок: «Северный ветер», логистика",
        "реплики": [
            ("me", "Андрей", "Ирина, расскажите, как у вас сейчас устроена работа с записями звонков?", 0.0, 5.2),
            ("them", "Ирина", "У нас двенадцать операторов, АТС пишет все разговоры, но их почти никто не слушает. Руководитель выборочно открывает пару в неделю.", 5.8, 14.1),
            ("me", "Андрей", "А что из-за этого теряете?", 14.6, 16.9),
            ("them", "Ирина", "Жалобы узнаём, когда клиент уже ушёл. В прошлом квартале потеряли двух крупных заказчиков, а в записях всё было слышно заранее.", 17.4, 26.0),
            ("me", "Андрей", "Если бы по каждому звонку были итоги и пометка о недовольстве, это бы помогло?", 26.5, 31.8),
            ("them", "Ирина", "Да, именно. Но записи у нас не должны уходить в чужое облако, это требование службы безопасности.", 32.3, 39.0),
            ("me", "Андрей", "Конспект всё делает на вашем компьютере, звук никуда не отправляется. Кто у вас принимает решение о покупке?", 39.5, 46.7),
            ("them", "Ирина", "Решает директор, Олег Викторович, бюджет до сорока тысяч в этом месяце. Пришлите предложение на irina@example.com, мой телефон +7 900 000-00-00. Я Ирина Сергеевна Белова, так и укажите.", 47.2, 59.4),
            ("me", "Андрей", "Договорились. Покажу всё в четверг в одиннадцать, подключим вашу АТС по SFTP прямо на встрече.", 60.0, 66.8),
        ],
        "итоги": """**Решения**

- Показ в четверг в 11:00, АТС подключаем по SFTP прямо на встрече.

**Кто что делает**

- **Андрей** — присылает предложение Ирине на почту.

**Открытые вопросы**

- Согласует ли директор бюджет до 40 000 ₽ в этом месяце.
""",
        "спин": """**Ситуация клиента**
- 12 операторов, АТС записывает все разговоры
- Записи почти не слушают: руководитель открывает пару в неделю

**Проблемы и боли**
- «Жалобы узнаём, когда клиент уже ушёл»

**Последствия**
- В прошлом квартале потеряны два крупных заказчика, хотя недовольство было слышно в записях заранее

**Выгода, которую клиент увидел**
- Итоги по каждому звонку и пометка о недовольстве

**Бюджет**
- До 40 000 ₽ в этом месяце

**Кто принимает решение**
- Директор, Олег Викторович; Ирина влияет на выбор

**Возражения**
- Записи не должны уходить в чужое облако. Ответ: всё делается на компьютере клиента

**Следующий шаг**
- Показ в четверг в 11:00, подключение АТС по SFTP на встрече
- Андрей присылает предложение на почту Ирины
""",
    },
    "вопрос": "Кто что обещал?",
    "ответ": """- **Дмитрий** проверит работу без интернета на планшетах: полдня, если ничего не всплывёт, полтора, если сломается синхронизация.
- **Марина** доделает пустой экран в знакомстве.
- **Алексей** напишет список изменений человеческим языком до выпуска и пришлёт на прочтение.

Срок у всех один: выпуск в пятницу.""",
    "запись": {
        "название": "Созвон с подрядчиком по складу",
        "реплики": [
            ("me", "Я", "Добрый день! Давайте сверим сроки по второй очереди склада.", 0.0, 4.4),
            ("them", "Собеседник", "Добрый. Монтаж стеллажей закончим к двадцатому, проводку начнём сразу после.", 5.0, 11.2),
            ("me", "Я", "А что с пожарной сигнализацией? Её нужно сдать до открытия.", 11.8, 16.0),
        ],
        "черновик": "С сигнализацией вопрос к проектировщику, я сегодня ему",
    },
    "поиск": "планшеты",
    "ссылка": "https://video.example.com/webinar/vnedrenie-crm",
    "телеграм": {
        "бот": "Конспект отдела продаж",
        "хозяин": "Андрей Волков",
        "люди": [("Ирина Белова", "irina_sales", True), ("Пётр Орлов", "porlov", True), ("Ольга Ким", "", False)],
    },
    "сфтп": {"host": "pbx.example.ru", "port": "22", "user": "konspekt", "dir": "/var/spool/asterisk/monitor"},
}


ДАННЫЕ["en"] = {
    "папка_команда": "Team",
    "папка_продажи": "Sales calls",
    "звонок": {
        "название": "Call: Bluefin Logistics",
        "реплики": [
            ("me", "Alex", "Jordan, how do you handle call recordings today?", 0.0, 4.6),
            ("them", "Jordan", "We have twelve agents and the phone system records every call, but almost nobody listens. The team lead opens a couple a week.", 5.2, 13.8),
            ("me", "Alex", "What does that cost you?", 14.3, 16.1),
            ("them", "Jordan", "We hear about complaints after the customer is gone. Last quarter we lost two big accounts, and you could hear it coming in the recordings.", 16.6, 25.2),
            ("me", "Alex", "Would a summary of every call with a flag for unhappy customers help?", 25.7, 30.4),
            ("them", "Jordan", "Exactly that. But the recordings can't go to someone else's cloud, our security team won't allow it.", 30.9, 37.5),
            ("me", "Alex", "Konspekt runs on your own computer, the audio never leaves it. Who makes the buying decision?", 38.0, 44.6),
            ("them", "Jordan", "Our director, Sam. Budget is up to five hundred dollars this month. Send the proposal to jordan@example.com, my number is +1 555 010 0199.", 45.1, 55.0),
            ("me", "Alex", "Deal. I'll walk you through it Thursday at eleven and we'll connect your phone system over SFTP right in the call.", 55.6, 62.9),
        ],
        "итоги": """**Decisions**

- Demo on Thursday at 11:00; the phone system is connected over SFTP during the call.

**Who does what**

- **Alex** — sends the proposal to Jordan by email.

**Open questions**

- Whether the director approves the budget of up to $500 this month.
""",
        "спин": """**Customer situation**
- 12 agents, the phone system records every call
- Recordings are rarely heard: the team lead opens a couple a week

**Problems and pains**
- "We hear about complaints after the customer is gone"

**Implications**
- Two big accounts lost last quarter, though the discontent was audible in the recordings

**Benefit the customer saw**
- A summary of every call with a flag for unhappy customers

**Budget**
- Up to $500 this month

**Decision maker**
- Director, Sam; Jordan influences the choice

**Objections**
- Recordings must not go to a third-party cloud. Answer: everything runs on the customer's computer

**Next step**
- Demo on Thursday at 11:00, connect the phone system over SFTP during the call
- Alex sends the proposal to Jordan by email
""",
    },
    "вопрос": "Who promised what?",
    "ответ": """- **Daniel** will test offline mode on tablets: half a day if nothing turns up, a day and a half if sync breaks.
- **Maria** will finish the empty state in onboarding.
- **Alex** will write a plain-language changelog before the release and send it for a read.

Everyone works to the same date: release on Friday.""",
    "запись": {
        "название": "Call with the warehouse contractor",
        "реплики": [
            ("me", "Me", "Hi! Let's check the dates for the second phase of the warehouse.", 0.0, 4.4),
            ("them", "Contractor", "Hi. Racking will be done by the twentieth, wiring starts right after.", 5.0, 10.8),
            ("me", "Me", "And the fire alarm? It has to be signed off before opening.", 11.4, 15.6),
        ],
        "черновик": "The alarm is a question for the designer, I'll call him today",
    },
    "поиск": "tablets",
    "ссылка": "https://video.example.com/webinar/crm-rollout",
    "телеграм": {
        "бот": "Sales team Konspekt",
        "хозяин": "Alex Turner",
        "люди": [("Jordan Reed", "jordan_sales", True), ("Priya Shah", "priya_s", True), ("Tom Baker", "", False)],
    },
    "сфтп": {"host": "pbx.example.com", "port": "22", "user": "konspekt", "dir": "/var/spool/asterisk/monitor"},
}


ДАННЫЕ["es"] = {
    "папка_команда": "Equipo",
    "папка_продажи": "Llamadas de ventas",
    "звонок": {
        "название": "Llamada: Logística Brisa",
        "реплики": [
            ("me", "Alejandro", "Lucía, ¿cómo gestionáis hoy las grabaciones de llamadas?", 0.0, 4.8),
            ("them", "Lucía", "Tenemos doce agentes y la centralita graba todo, pero casi nadie las escucha. La jefa de equipo abre un par a la semana.", 5.3, 13.9),
            ("me", "Alejandro", "¿Y qué os cuesta eso?", 14.4, 16.0),
            ("them", "Lucía", "Nos enteramos de las quejas cuando el cliente ya se ha ido. El trimestre pasado perdimos dos cuentas grandes, y en las grabaciones ya se oía.", 16.5, 25.3),
            ("me", "Alejandro", "¿Os ayudaría un resumen de cada llamada con un aviso de cliente descontento?", 25.8, 30.6),
            ("them", "Lucía", "Justo eso. Pero las grabaciones no pueden ir a la nube de otro, seguridad no lo permite.", 31.1, 37.4),
            ("me", "Alejandro", "Konspekt funciona en vuestro ordenador, el audio no sale de ahí. ¿Quién decide la compra?", 37.9, 44.2),
            ("them", "Lucía", "Decide el director, Javier. Presupuesto hasta quinientos euros este mes. Manda la propuesta a lucia@example.com; si hace falta, la cuenta es ES91 2100 0418 4502 0005 1332.", 44.7, 56.0),
            ("me", "Alejandro", "Hecho. Os lo enseño el jueves a las once y conectamos la centralita por SFTP en la misma llamada.", 56.5, 63.4),
        ],
        "итоги": """**Decisiones**

- Demostración el jueves a las 11:00; la centralita se conecta por SFTP en la misma llamada.

**Quién hace qué**

- **Alejandro** — envía la propuesta a Lucía por correo.

**Preguntas abiertas**

- Si el director aprueba el presupuesto de hasta 500 € este mes.
""",
        "спин": """**Situación del cliente**
- 12 agentes, la centralita graba todas las llamadas
- Casi nadie escucha las grabaciones: la jefa de equipo abre un par a la semana

**Problemas y dolores**
- «Nos enteramos de las quejas cuando el cliente ya se ha ido»

**Implicaciones**
- Dos cuentas grandes perdidas el trimestre pasado, aunque el descontento ya se oía en las grabaciones

**Beneficio que vio el cliente**
- Un resumen de cada llamada con aviso de cliente descontento

**Presupuesto**
- Hasta 500 € este mes

**Quién decide**
- El director, Javier; Lucía influye en la elección

**Objeciones**
- Las grabaciones no pueden ir a una nube ajena. Respuesta: todo se hace en el ordenador del cliente

**Siguiente paso**
- Demostración el jueves a las 11:00, conexión de la centralita por SFTP en la llamada
- Alejandro envía la propuesta a Lucía por correo
""",
    },
    "вопрос": "¿Quién prometió qué?",
    "ответ": """- **Daniel** probará el modo sin conexión en tabletas: medio día si no aparece nada, día y medio si falla la sincronización.
- **Marta** terminará el estado vacío del onboarding.
- **Alejandro** escribirá el registro de cambios en lenguaje claro antes de publicar y lo pasará para leerlo.

Todos trabajan para la misma fecha: publicación el viernes.""",
    "запись": {
        "название": "Llamada con el contratista del almacén",
        "реплики": [
            ("me", "Yo", "¡Hola! Revisemos los plazos de la segunda fase del almacén.", 0.0, 4.4),
            ("them", "Contratista", "Hola. Las estanterías estarán montadas el día veinte, el cableado empieza justo después.", 5.0, 11.0),
            ("me", "Yo", "¿Y la alarma contra incendios? Hay que certificarla antes de abrir.", 11.6, 15.8),
        ],
        "черновик": "Lo de la alarma es cosa del proyectista, hoy mismo le llamo",
    },
    "поиск": "tabletas",
    "ссылка": "https://video.example.com/webinar/implantar-crm",
    "телеграм": {
        "бот": "Konspekt de ventas",
        "хозяин": "Alejandro Ruiz",
        "люди": [("Lucía Romero", "lucia_ventas", True), ("Pablo Ortega", "portega", True), ("Elena Vidal", "", False)],
    },
    "сфтп": {"host": "pbx.example.es", "port": "22", "user": "konspekt", "dir": "/var/spool/asterisk/monitor"},
}


ДАННЫЕ["sr"] = {
    "папка_команда": "Tim",
    "папка_продажи": "Pozivi prodaje",
    "звонок": {
        "название": "Poziv: Severni vetar logistika",
        "реплики": [
            ("me", "Marko", "Jovana, kako danas radite sa snimcima poziva?", 0.0, 4.2),
            ("them", "Jovana", "Imamo dvanaest operatera, centrala snima sve razgovore, ali ih skoro niko ne sluša. Šefica tima otvori par nedeljno.", 4.8, 13.4),
            ("me", "Marko", "A šta zbog toga gubite?", 13.9, 15.6),
            ("them", "Jovana", "Za žalbe saznamo kad klijent već ode. Prošlog kvartala izgubili smo dva velika klijenta, a na snimcima se sve čulo unapred.", 16.1, 24.8),
            ("me", "Marko", "Da li bi pomogao rezime svakog poziva sa oznakom nezadovoljnog klijenta?", 25.3, 30.0),
            ("them", "Jovana", "Upravo to. Ali snimci ne smeju u tuđi oblak, bezbednost to ne dozvoljava.", 30.5, 36.2),
            ("me", "Marko", "Konspekt radi na vašem računaru, zvuk ne izlazi odatle. Ko donosi odluku o kupovini?", 36.7, 43.0),
            ("them", "Jovana", "Odlučuje direktor, Dragan. Budžet do pedeset hiljada dinara ovog meseca. Pošaljite ponudu na jovana@example.com, a za povraćaj koristite karticu 4111 1111 1111 1111.", 43.5, 54.6),
            ("me", "Marko", "Dogovoreno. Pokazaću vam sve u četvrtak u jedanaest i povezaćemo centralu preko SFTP-a odmah na sastanku.", 55.1, 62.3),
        ],
        "итоги": """**Odluke**

- Prikaz u četvrtak u 11:00; centrala se povezuje preko SFTP-a na samom sastanku.

**Ko šta radi**

- **Marko** — šalje ponudu Jovani mejlom.

**Otvorena pitanja**

- Da li će direktor odobriti budžet do 50.000 dinara ovog meseca.
""",
        "спин": """**Situacija klijenta**
- 12 operatera, centrala snima sve razgovore
- Snimci se skoro ne slušaju: šefica tima otvori par nedeljno

**Problemi**
- „Za žalbe saznamo kad klijent već ode“

**Posledice**
- Prošlog kvartala izgubljena dva velika klijenta, iako se nezadovoljstvo čulo na snimcima

**Korist koju je klijent video**
- Rezime svakog poziva sa oznakom nezadovoljnog klijenta

**Budžet**
- Do 50.000 dinara ovog meseca

**Ko donosi odluku**
- Direktor, Dragan; Jovana utiče na izbor

**Prigovori**
- Snimci ne smeju u tuđi oblak. Odgovor: sve se radi na računaru klijenta

**Sledeći korak**
- Prikaz u četvrtak u 11:00, povezivanje centrale preko SFTP-a na sastanku
- Marko šalje ponudu Jovani mejlom
""",
    },
    "вопрос": "Ko je šta obećao?",
    "ответ": """- **Nikola** će testirati rad bez interneta na tabletima: pola dana ako ništa ne iskrsne, dan i po ako sinhronizacija pukne.
- **Milica** će završiti prazno stanje uvodnih ekrana.
- **Marko** će pre izdanja napisati spisak izmena običnim jezikom i poslati ga na čitanje.

Svi rade za isti datum: izdanje u petak.""",
    "запись": {
        "название": "Razgovor sa izvođačem za magacin",
        "реплики": [
            ("me", "Ja", "Dobar dan! Hajde da proverimo rokove za drugu fazu magacina.", 0.0, 4.4),
            ("them", "Sagovornik", "Dobar dan. Police završavamo do dvadesetog, instalacije počinju odmah posle.", 5.0, 10.9),
            ("me", "Ja", "A protivpožarni alarm? Mora da se preda pre otvaranja.", 11.5, 15.4),
        ],
        "черновик": "Za alarm je pitanje za projektanta, danas ću ga zvati",
    },
    "поиск": "tabletima",
    "ссылка": "https://video.example.com/webinar/uvodjenje-crm",
    "телеграм": {
        "бот": "Konspekt prodaje",
        "хозяин": "Marko Jovanović",
        "люди": [("Jovana Petrović", "jovana_prodaja", True), ("Stefan Ilić", "silic", True), ("Ana Kovač", "", False)],
    },
    "сфтп": {"host": "pbx.example.rs", "port": "22", "user": "konspekt", "dir": "/var/spool/asterisk/monitor"},
}
