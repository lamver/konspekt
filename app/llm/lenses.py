"""Разрезы разбора встречи: какие бывают и как их просить у модели.

Каждый разрез — отдельная карточка на вкладке «Саммари». Результат
хранится в базе отдельно (таблица meeting_analyses), поэтому разбор по
SPIN не затирает итоги встречи, а итоги не затирают разбор по STAR.

Разрезы двух видов:
- «счёт» — считаются без модели по времени реплик и словам
  (core/analysis.py). Готовы сразу у любой встречи;
- «модель» — модель заполняет известную структуру методики. Структура
  задана жёстко, с заголовками разделов: маленькая модель заполняет
  готовые клетки заметно лучше, чем сочиняет разбор сама.

Методики, на которые опираемся:
- итоги: речевые акты (Остин, Сёрл) — решение, обязательство, вопрос;
- продажи: SPIN (Рэкхем) и BANT;
- собеседование: STAR;
- один на один: обратная связь и развитие;
- приём у врача: SOAP;
- переговоры: гарвардский метод (интересы, позиции, варианты, критерии);
- статус команды: сделано, в работе, мешает.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Разрез:
    kind: str
    # «счёт» или «модель»
    engine: str
    # Делать сразу после записи, не дожидаясь щелчка.
    auto: bool
    # Для модели: чем она занята и в каком виде отвечать.
    system: str = ""
    form: str = ""


ОБЩЕЕ = """Правила:
- Только то, что было сказано во встрече. Ничего не додумывай.
- Если для раздела ничего нет, напиши под ним «— не обсуждалось».
- Коротко, по пунктам, каждый пункт с новой строки с дефиса.
- Имена и сроки дословно, как прозвучали.
- Отвечай на русском языке. Без вступлений и заключений."""

РАЗРЕЗЫ: dict[str, Разрез] = {
    "talk": Разрез("talk", "счёт", True),
    "tone": Разрез("tone", "счёт", True),
    "summary": Разрез("summary", "модель", True),
    "sales": Разрез(
        "sales", "модель", False,
        system="Ты разбираешь разговор с клиентом по методикам SPIN и BANT.\n\n" + ОБЩЕЕ,
        form="""**Ситуация клиента**
- что есть у клиента сейчас

**Проблемы и боли**
- что клиента не устраивает, его словами

**Последствия**
- к чему приводят проблемы, если их не решить

**Выгода, которую клиент увидел**
- что клиент сам назвал полезным

**Бюджет**
- названные суммы и ограничения

**Кто принимает решение**
- кто решает, кто влияет

**Сроки**
- когда клиенту нужно решение

**Возражения**
- что клиента смущает и как на это ответили

**Следующий шаг**
- о чём договорились, кто и когда""",
    ),
    "interview": Разрез(
        "interview", "модель", False,
        system="Ты разбираешь собеседование по методике STAR.\n\n" + ОБЩЕЕ,
        form="""**Опыт кандидата**
- где и кем работал, чем занимался

**Примеры по STAR**
- Ситуация — задача — что сделал сам — результат (по примеру в пункте)

**Сильные стороны**
- что подтверждено примерами

**Сомнения и пробелы**
- где ответ был общим или без примера

**Вопросы кандидата**
- о чём спрашивал сам кандидат

**Договорённости**
- следующий этап, сроки, ожидания по деньгам, если звучали""",
    ),
    "one_on_one": Разрез(
        "one_on_one", "модель", False,
        system="Ты разбираешь встречу один на один руководителя с сотрудником.\n\n" + ОБЩЕЕ,
        form="""**Как дела у сотрудника**
- настроение, нагрузка, что радует

**Что беспокоит**
- проблемы и опасения его словами

**Обратная связь**
- что сказал руководитель, что сказал сотрудник

**Развитие**
- цели, чему хочет научиться, куда расти

**Договорённости**
- кто что сделает и к какому сроку""",
    ),
    "medical": Разрез(
        "medical", "модель", False,
        system=("Ты составляешь запись приёма по схеме SOAP. Это помощь врачу, а "
                "не диагноз: не ставь диагнозы и не назначай лечение сам, пиши "
                "только прозвучавшее на приёме.\n\n" + ОБЩЕЕ),
        form="""**S — жалобы**
- жалобы пациента его словами, с какого времени

**O — объективно**
- что врач увидел, измерил, какие результаты обсуждали

**A — оценка**
- как врач сам оценил состояние, если сказал вслух

**P — план**
- назначения, обследования, повторный приём, как их назвал врач

**Что пациент понял и о чём спросил**
- вопросы пациента и ответы на них""",
    ),
    "negotiation": Разрез(
        "negotiation", "модель", False,
        system="Ты разбираешь переговоры по гарвардскому методу.\n\n" + ОБЩЕЕ,
        form="""**Позиции сторон**
- что каждая сторона требует

**Интересы за позициями**
- зачем это каждой стороне на самом деле

**Варианты**
- какие решения предлагались

**Объективные критерии**
- на что ссылались: рынок, цифры, правила

**Уступки**
- кто на что согласился

**Итог**
- о чём договорились и что осталось открытым""",
    ),
    "standup": Разрез(
        "standup", "модель", False,
        system="Ты разбираешь статус-встречу команды.\n\n" + ОБЩЕЕ,
        form="""**Сделано**
- Имя — что сделал

**В работе**
- Имя — чем занят сейчас

**Мешает**
- Имя — что блокирует и кто может помочь

**Договорённости**
- кто что сделает и к какому сроку""",
    ),
}

# Порядок карточек на экране: сначала то, что готово у всех.
ПОРЯДОК = ("summary", "talk", "tone", "sales", "interview", "one_on_one",
           "negotiation", "standup", "medical")


# Разбор встречи не по-русски. Русская форма с русскими подсказками
# тянет маленькую модель в русский: проба на Qwen3-4B (03.10) дала
# сербскому звонку разбор полу-русской кириллицей под русскими
# заголовками. Английская форма — нейтральная середина: её модель знает
# лучше всех и переводит заголовки на нужный язык охотнее, чем с русского.
# Последняя строка запроса — просьба на языке самой встречи
# (prompts.ПРОСЬБА_НА_ЯЗЫКЕ).
ОБЩЕЕ_EN = """Rules:
- Only what was said in the meeting. Do not make anything up.
- If there is nothing for a section, write under it that it was not discussed.
- Short bullet points, each on a new line starting with a dash.
- Names and dates exactly as they were said.
- No introductions or conclusions."""

ПО_АНГЛИЙСКИ: dict[str, tuple[str, str]] = {
    "sales": (
        "You analyse a conversation with a customer using SPIN and BANT.",
        """**Customer situation**
- what the customer has now

**Problems and pains**
- what the customer is unhappy with, in their words

**Implications**
- what the problems lead to if left unsolved

**Benefit the customer saw**
- what the customer named as useful

**Budget**
- amounts and limits mentioned

**Decision maker**
- who decides, who influences

**Timeline**
- when the customer needs a solution

**Objections**
- what worries the customer and how it was answered

**Next step**
- what was agreed, who and when""",
    ),
    "interview": (
        "You analyse a job interview using the STAR method.",
        """**Candidate experience**
- where and as whom they worked, what they did

**STAR examples**
- Situation — task — what they did themselves — result (one example per bullet)

**Strengths**
- what is backed by examples

**Doubts and gaps**
- where the answer was general or had no example

**Candidate's questions**
- what the candidate asked

**Agreements**
- next stage, dates, salary expectations if mentioned""",
    ),
    "one_on_one": (
        "You analyse a one-on-one meeting between a manager and an employee.",
        """**How the employee is doing**
- mood, workload, what makes them happy

**Concerns**
- problems and worries in their words

**Feedback**
- what the manager said, what the employee said

**Growth**
- goals, what they want to learn, where to grow

**Agreements**
- who does what and by when""",
    ),
    "medical": (
        "You write up a medical appointment using SOAP. This helps the doctor, it is "
        "not a diagnosis: do not diagnose or prescribe yourself, write only what was "
        "said at the appointment.",
        """**S — complaints**
- the patient's complaints in their words, since when

**O — objective**
- what the doctor saw or measured, which results were discussed

**A — assessment**
- how the doctor assessed the condition, if said aloud

**P — plan**
- prescriptions, tests, follow-up visit, as the doctor named them

**What the patient understood and asked**
- the patient's questions and the answers""",
    ),
    "negotiation": (
        "You analyse a negotiation using the Harvard method.",
        """**Positions**
- what each side demands

**Interests behind the positions**
- why each side really needs it

**Options**
- which solutions were proposed

**Objective criteria**
- what they referred to: market, numbers, rules

**Concessions**
- who agreed to what

**Outcome**
- what was agreed and what remains open""",
    ),
    "standup": (
        "You analyse a team status meeting.",
        """**Done**
- Name — what they did

**In progress**
- Name — what they are working on

**Blockers**
- Name — what blocks them and who can help

**Agreements**
- who does what and by when""",
    ),
}


def messages(kind: str, title: str, transcript: str, notes: str = "",
             lang: str = "ru") -> list[dict[str, str]]:
    """Запрос к модели для разреза вида «модель».

    Русская встреча — русская форма, как всегда. Встреча на другом языке —
    английская форма с переводом заголовков и просьба на языке встречи.
    """
    from ..core.язык_итогов import код
    from .prompts import ПРОСЬБА_НА_ЯЗЫКЕ

    р = РАЗРЕЗЫ[kind]
    if код(lang) in ("", "ru") or kind not in ПО_АНГЛИЙСКИ:
        пометки = f"Мои пометки во время встречи:\n{notes.strip()}\n\n" if notes.strip() else ""
        заголовок = f"Название встречи: {title.strip()}\n\n" if title.strip() else ""
        user = (f"{заголовок}{пометки}Расшифровка встречи:\n\n{transcript.strip() or '(расшифровки нет)'}\n\n"
                f"Заполни строго по этим разделам, заголовки оставь как есть:\n\n{р.form}")
        return [{"role": "system", "content": р.system}, {"role": "user", "content": user}]

    роль, форма = ПО_АНГЛИЙСКИ[kind]
    просьба = ПРОСЬБА_НА_ЯЗЫКЕ.get(
        код(lang), f"Write the whole answer in the language with code «{код(lang)}», including the headings.")
    system = f"{роль}\n\n{ОБЩЕЕ_EN}"
    пометки = f"My notes during the meeting:\n{notes.strip()}\n\n" if notes.strip() else ""
    заголовок = f"Meeting title: {title.strip()}\n\n" if title.strip() else ""
    user = (f"{заголовок}{пометки}Meeting transcript:\n\n{transcript.strip() or '(no transcript)'}\n\n"
            "Fill in strictly by these sections, keep their order and translate the headings "
            f"into the language of the meeting:\n\n{форма}\n\n{просьба}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
