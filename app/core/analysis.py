"""Разборы встречи, которые считаются без модели.

Два разбора из научных методик анализа разговора, где всё можно
посчитать честно и мгновенно, по времени реплик и по словам.

«Разбор разговора» — по мотивам анализа разговора (Сакс, Щеглофф) и
исследований звонков продаж (Gong): кто сколько говорил, самый длинный
монолог, перебивания, паузы, вопросы, темп. Для продаж есть известный
ориентир: у лучших продавцов доля своей речи около 43%, и
разговор-монолог продаёт хуже диалога.

«Тон и слова» — по мотивам LIWC (Пеннебейкер): доля слов «я» против
«мы», слов сомнения и уверенности, эмоций, отрицаний. Словари русские и
короткие, по основам слов: это подсказка, а не диагноз.

Без модели потому, что маленькая модель считает плохо, а здесь нужен
счёт, а не пересказ. Зато такой разбор готов у каждой встречи сразу,
даже на слабом компьютере и без скачанной модели.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Пауза, после которой смена говорящего — уже не перебивание, а
# очередь: люди отвечают друг другу примерно через 0.2 секунды.
ПЕРЕБИЛ_ЗАХЛЁСТ = 0.3
# Пауза, которую человек замечает как тишину.
ДЛИННАЯ_ПАУЗА = 3.0
# Реплики одного голоса с разрывом меньше этого — один монолог.
СКЛЕЙКА = 1.5
# Голос, который говорил меньше этой доли встречи, в отдельную строку не
# выносим. На живой записи разделение по голосам даёт десятки «голосов»
# по секунде-две: это не люди, а шум, и список из ста строк «0%» прячет
# тех, кто действительно говорил.
МЕЛКИЙ_ГОЛОС = 0.02


@dataclass
class Реплика:
    who: str
    start: float
    end: float
    text: str


def _реплики(segments: list[dict[str, Any]]) -> list[Реплика]:
    out = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        start = float(s.get("start") or 0)
        end = float(s.get("end") or start)
        if end < start:
            end = start
        out.append(Реплика(str(s.get("who") or "?"), start, end, text))
    out.sort(key=lambda r: r.start)
    return out


def _слова(text: str) -> list[str]:
    return re.findall(r"[а-яёa-z]+", text.lower().replace("ё", "е"))


def _мин(секунды: float) -> str:
    с = int(round(секунды))
    return f"{с // 60}:{с % 60:02d}"


# --- подписи на языке встречи --------------------------------------------------

# Разбор пишется словами, и слова должны быть на языке встречи: в
# испанской встрече «Кто сколько говорил» выглядит как недоперевод.
# Языки те же, что у интерфейса; для остальных берётся язык интерфейса
# (см. core/язык_итогов.py).
ТЕКСТЫ: dict[str, dict[str, str]] = {
    "ru": {
        "who_talked": "**Кто сколько говорил**",
        "wpm": ", {n} слов в минуту",
        "others": "- Короткие реплики других голосов ({n}) — {share}% ({time})",
        "flow": "**Ход разговора**",
        "duration": "- Длительность {time}, заметных говорящих {n}",
        "turns": "- Смен говорящего: {n}",
        "turns_per_min": ", {n} в минуту",
        "longest": "- Самый длинный монолог: {who}, {time} (с {at})",
        "questions": "- Вопросов: {n}",
        "interruptions": "- Перебиваний: {n}",
        "pauses": "- Пауз дольше {sec} секунд: {n}",
        "notice": "**На что обратить внимание**",
        "monologue_share": "{who} говорил {share}% времени: это скорее монолог, чем разговор.",
        "even": "Время разделено почти поровну: настоящий диалог.",
        "long_monologue": "Монолог дольше трёх минут ({time}): собеседника в это время почти наверняка потеряли.",
        "no_questions": "За всю встречу ни одного вопроса.",
        "many_interruptions": "Перебивали друг друга {n} раз.",
        "tone_only_ru": "Тон по словам пока считается только для русской речи.",
        "tone_too_little": "Слишком мало речи, чтобы судить о тоне.",
    },
    "en": {
        "who_talked": "**Who talked how much**",
        "wpm": ", {n} words per minute",
        "others": "- Short remarks from other voices ({n}) — {share}% ({time})",
        "flow": "**How the conversation went**",
        "duration": "- Duration {time}, noticeable speakers {n}",
        "turns": "- Speaker changes: {n}",
        "turns_per_min": ", {n} per minute",
        "longest": "- Longest monologue: {who}, {time} (from {at})",
        "questions": "- Questions: {n}",
        "interruptions": "- Interruptions: {n}",
        "pauses": "- Pauses longer than {sec} seconds: {n}",
        "notice": "**Worth noticing**",
        "monologue_share": "{who} talked {share}% of the time: more of a monologue than a conversation.",
        "even": "Time was split almost evenly: a real dialogue.",
        "long_monologue": "A monologue longer than three minutes ({time}): the listener was most likely lost by then.",
        "no_questions": "Not a single question in the whole meeting.",
        "many_interruptions": "People interrupted each other {n} times.",
        "tone_only_ru": "Tone by words is only counted for Russian speech so far.",
        "tone_too_little": "Too little speech to judge the tone.",
    },
    "es": {
        "who_talked": "**Quién habló cuánto**",
        "wpm": ", {n} palabras por minuto",
        "others": "- Intervenciones breves de otras voces ({n}) — {share}% ({time})",
        "flow": "**Cómo fue la conversación**",
        "duration": "- Duración {time}, hablantes destacados {n}",
        "turns": "- Cambios de hablante: {n}",
        "turns_per_min": ", {n} por minuto",
        "longest": "- Monólogo más largo: {who}, {time} (desde {at})",
        "questions": "- Preguntas: {n}",
        "interruptions": "- Interrupciones: {n}",
        "pauses": "- Pausas de más de {sec} segundos: {n}",
        "notice": "**A tener en cuenta**",
        "monologue_share": "{who} habló el {share}% del tiempo: más un monólogo que una conversación.",
        "even": "El tiempo se repartió casi por igual: un diálogo de verdad.",
        "long_monologue": "Un monólogo de más de tres minutos ({time}): para entonces el interlocutor casi seguro se había perdido.",
        "no_questions": "Ni una sola pregunta en toda la reunión.",
        "many_interruptions": "Se interrumpieron {n} veces.",
        "tone_only_ru": "Por ahora el tono por palabras solo se calcula para el habla en ruso.",
        "tone_too_little": "Hay muy poca habla para juzgar el tono.",
    },
    "sr": {
        "who_talked": "**Ko je koliko govorio**",
        "wpm": ", {n} reči u minutu",
        "others": "- Kratke replike drugih glasova ({n}) — {share}% ({time})",
        "flow": "**Tok razgovora**",
        "duration": "- Trajanje {time}, primetnih govornika {n}",
        "turns": "- Smena govornika: {n}",
        "turns_per_min": ", {n} u minutu",
        "longest": "- Najduži monolog: {who}, {time} (od {at})",
        "questions": "- Pitanja: {n}",
        "interruptions": "- Upadanja u reč: {n}",
        "pauses": "- Pauza dužih od {sec} sekundi: {n}",
        "notice": "**Na šta obratiti pažnju**",
        "monologue_share": "{who} je govorio {share}% vremena: to je pre monolog nego razgovor.",
        "even": "Vreme je podeljeno skoro podjednako: pravi dijalog.",
        "long_monologue": "Monolog duži od tri minuta ({time}): sagovornik je tada skoro sigurno izgubljen.",
        "no_questions": "Na celom sastanku nijedno pitanje.",
        "many_interruptions": "Upadali su jedni drugima u reč {n} puta.",
        "tone_only_ru": "Ton po rečima se za sada računa samo za govor na ruskom.",
        "tone_too_little": "Premalo govora da bi se procenio ton.",
    },
}


def _т(язык: str, ключ: str, **vars: object) -> str:
    return ТЕКСТЫ.get(язык, ТЕКСТЫ["ru"])[ключ].format(**vars)



# --- разбор разговора ----------------------------------------------------------


def разговор(segments: list[dict[str, Any]], язык: str = "ru") -> dict[str, Any]:
    """Цифры по времени и очерёдности реплик.

    Возвращает словарь: `people` — по каждому говорящему, `totals` —
    общие цифры, `markdown` — готовый текст для карточки.
    """
    реплики = _реплики(segments)
    if not реплики:
        return {"people": [], "totals": {}, "markdown": ""}

    люди: dict[str, dict[str, Any]] = {}

    def человек(who: str) -> dict[str, Any]:
        return люди.setdefault(who, {
            "who": who, "seconds": 0.0, "words": 0, "turns": 0,
            "questions": 0, "interruptions": 0, "longest": 0.0,
        })

    # Монологи: подряд идущие реплики одного голоса без заметной паузы.
    монолог_кто, монолог_с, монолог_по = None, 0.0, 0.0
    самый_длинный = (0.0, "", 0.0)
    паузы = 0
    смен = 0
    прошлый: Реплика | None = None

    def закрыть_монолог() -> None:
        nonlocal самый_длинный
        if монолог_кто is None:
            return
        длина = монолог_по - монолог_с
        ч = человек(монолог_кто)
        ч["longest"] = max(ч["longest"], длина)
        if длина > самый_длинный[0]:
            самый_длинный = (длина, монолог_кто, монолог_с)

    for р in реплики:
        ч = человек(р.who)
        ч["seconds"] += р.end - р.start
        ч["words"] += len(_слова(р.text))
        ч["questions"] += р.text.count("?")
        if прошлый is not None:
            if р.who != прошлый.who:
                смен += 1
            if р.who != прошлый.who and р.start < прошлый.end - ПЕРЕБИЛ_ЗАХЛЁСТ:
                ч["interruptions"] += 1
            if р.start - прошлый.end >= ДЛИННАЯ_ПАУЗА:
                паузы += 1
        if р.who == монолог_кто and р.start - монолог_по < СКЛЕЙКА:
            монолог_по = max(монолог_по, р.end)
        else:
            закрыть_монолог()
            монолог_кто, монолог_с, монолог_по = р.who, р.start, р.end
            ч["turns"] += 1
        прошлый = р
    закрыть_монолог()

    всего_речи = sum(ч["seconds"] for ч in люди.values()) or 1.0
    длительность = max(р.end for р in реплики) - реплики[0].start
    for ч in люди.values():
        ч["share"] = round(100 * ч["seconds"] / всего_речи)
        минут = ч["seconds"] / 60
        ч["wpm"] = round(ч["words"] / минут) if минут >= 0.5 else 0
    по_доле = sorted(люди.values(), key=lambda ч: -ч["seconds"])
    # Вопросы видны только по знаку «?». Старые записи распознавались без
    # пунктуации, и «ни одного вопроса» там было бы враньём, а не цифрой.
    с_пунктуацией = any(ch in р.text for р in реплики for ch in ".,?!")
    итоги = {
        "duration": длительность,
        "speakers": len(люди),
        "turn_changes": смен,
        "turns_per_min": round(смен / (длительность / 60), 1) if длительность >= 60 else 0,
        "questions": sum(ч["questions"] for ч in люди.values()) if с_пунктуацией else None,
        "interruptions": sum(ч["interruptions"] for ч in люди.values()),
        "long_pauses": паузы,
        "longest": {"seconds": самый_длинный[0], "who": самый_длинный[1], "at": самый_длинный[2]},
    }

    заметные = [ч for ч in по_доле if ч["seconds"] >= МЕЛКИЙ_ГОЛОС * всего_речи]
    прочие = [ч for ч in по_доле if ч not in заметные]
    итоги["speakers"] = len(заметные)
    т = lambda ключ, **vars: _т(язык, ключ, **vars)  # noqa: E731
    строки = [т("who_talked")]
    for ч in заметные:
        темп = т("wpm", n=ч["wpm"]) if ч["wpm"] else ""
        строки.append(f"- {ч['who']} — {ч['share']}% ({_мин(ч['seconds'])}){темп}")
    if прочие:
        сек = sum(ч["seconds"] for ч in прочие)
        строки.append(т("others", n=len(прочие), share=round(100 * сек / всего_речи), time=_мин(сек)))
    строки += ["", т("flow")]
    строки.append(т("duration", time=_мин(длительность), n=len(заметные)))
    строки.append(т("turns", n=итоги["turn_changes"])
                  + (т("turns_per_min", n=итоги["turns_per_min"]) if итоги["turns_per_min"] else ""))
    строки.append(т("longest", who=самый_длинный[1], time=_мин(самый_длинный[0]),
                    at=_мин(самый_длинный[2])))
    if итоги["questions"] is not None:
        строки.append(т("questions", n=итоги["questions"]))
    # Перебивание видно только по наложению двух дорожек: у записи из
    # одного файла реплики идут строго по очереди, и «ноль перебиваний»
    # там был бы враньём.
    дорожки = {str(seg.get("track") or "") for seg in segments}
    if len(дорожки - {""}) >= 2:
        строки.append(т("interruptions", n=итоги["interruptions"]))
    else:
        итоги["interruptions"] = None
    строки.append(т("pauses", sec=int(ДЛИННАЯ_ПАУЗА), n=паузы))

    замечания = []
    if len(заметные) >= 2 and по_доле[0]["share"] >= 65:
        замечания.append(т("monologue_share", who=по_доле[0]["who"], share=по_доле[0]["share"]))
    if len(заметные) == 2 and all(35 <= ч["share"] <= 65 for ч in заметные):
        замечания.append(т("even"))
    # Монолог при одном заметном голосе — это диктовка или лекция, а не
    # потерянный собеседник.
    if самый_длинный[0] >= 180 and len(заметные) >= 2:
        замечания.append(т("long_monologue", time=_мин(самый_длинный[0])))
    if итоги["questions"] == 0 and длительность >= 300:
        замечания.append(т("no_questions"))
    if итоги["interruptions"] and итоги["interruptions"] >= 10:
        замечания.append(т("many_interruptions", n=итоги["interruptions"]))
    if замечания:
        строки += ["", т("notice")] + [f"- {з}" for з in замечания]
    return {"people": по_доле, "totals": итоги, "markdown": "\n".join(строки)}


# --- тон и слова ---------------------------------------------------------------

# Словари по основам. Короткие намеренно: длинный словарь из учебника
# ловит в живой речи что угодно («прав» в «правительство»).
# Слово со знаком «$» на конце считается только целиком: основа «рад»
# ловила бы «ради» и «радио», а «класс» — «классификацию».
СЛОВАРИ: dict[str, tuple[str, ...]] = {
    "я": ("я", "меня", "мне", "мной", "мой", "моя", "мое", "мои", "моего", "моей", "моих", "моим"),
    "мы": ("мы", "нас", "нам", "нами", "наш", "наша", "наше", "наши", "нашего", "нашей", "наших", "нашим"),
    "сомнение": ("наверно", "может", "возможно", "кажется", "вроде", "примерно", "пожалуй",
                 "видимо", "неуверен", "сомнева", "непонятно", "затрудня"),
    "уверенность": ("точно", "конечно", "обязательно", "уверен", "безусловно", "однозначно",
                    "гарантир", "несомненно", "решили", "договорились"),
    "позитив": ("отлично", "хорошо", "здорово", "супер$", "классно", "нравит", "рад$", "рада$",
                "рады$", "радует", "спасибо",
                "удобно", "прекрасно", "успех", "получилось"),
    "негатив": ("плохо", "проблем", "ужас", "не работает", "сломал", "ошибк", "беда", "жаль",
                "раздража", "устал", "сложно", "трудно", "неудобно", "провал"),
    "отрицание": ("нет", "не", "никак", "никогда", "ничего", "нельзя"),
}
НАЗВАНИЯ = {
    "я": "«я, мне, мой»", "мы": "«мы, нам, наш»", "сомнение": "сомнение",
    "уверенность": "уверенность", "позитив": "одобрение", "негатив": "недовольство",
    "отрицание": "отрицания",
}


def _счёт(текст: str) -> tuple[int, dict[str, int]]:
    слова = _слова(текст)
    счёт = {к: 0 for к in СЛОВАРИ}
    низ = " " + " ".join(слова) + " "
    for к, основы in СЛОВАРИ.items():
        for о in основы:
            if " " in о:
                счёт[к] += низ.count(" " + о + " ")
            elif о.endswith("$"):
                счёт[к] += sum(1 for с in слова if с == о[:-1])
            elif к in ("я", "мы", "отрицание"):
                счёт[к] += sum(1 for с in слова if с == о)
            else:
                счёт[к] += sum(1 for с in слова if с.startswith(о))
    return len(слова), счёт


def тон(segments: list[dict[str, Any]], язык: str = "ru") -> dict[str, Any]:
    """Доли слов из словарей по каждому говорящему и в целом, на 100 слов.

    Словари русские, поэтому нерусскую речь честно не считаем: в
    английской речи русские основы не найдут ничего, и разбор вышел бы
    уверенным «ноль сомнений, ноль недовольства». Русская ли речь, решаем
    по буквам самого текста, а не по языку подписей: подписи могут быть
    английскими у русской встречи и наоборот.
    """
    реплики = _реплики(segments)
    if not реплики:
        return {"people": [], "markdown": ""}
    буквы = [ch for р in реплики for ch in р.text if ch.isalpha()]
    кириллица = sum(1 for ch in буквы if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    if буквы and кириллица * 2 < len(буквы):
        return {"people": [], "markdown": _т(язык, "tone_only_ru")}
    по_людям: dict[str, str] = {}
    for р in реплики:
        по_людям[р.who] = по_людям.get(р.who, "") + " " + р.text
    люди = []
    for кто, текст in по_людям.items():
        n, счёт = _счёт(текст)
        if n < 100:
            continue  # на полусотне слов одно «я» даёт два процента, доли врут
        люди.append({"who": кто, "words": n,
                     "per100": {к: round(100 * v / n, 1) for к, v in счёт.items()}})
    люди.sort(key=lambda ч: -ч["words"])
    if not люди:
        return {"people": [], "markdown": _т(язык, "tone_too_little")}

    строки = ["**На 100 слов**"]
    for ч in люди:
        д = ч["per100"]
        строки.append(
            f"- {ч['who']}: я {д['я']}, мы {д['мы']}, сомнение {д['сомнение']}, "
            f"уверенность {д['уверенность']}, одобрение {д['позитив']}, недовольство {д['негатив']}"
        )
    замечания = []
    for ч in люди:
        д = ч["per100"]
        if д["я"] >= 2 * max(д["мы"], 0.5) and д["я"] >= 3:
            замечания.append(f"{ч['who']} говорит больше о себе, чем о команде (я {д['я']} против мы {д['мы']}).")
        elif д["мы"] >= 2 * max(д["я"], 0.5) and д["мы"] >= 2:
            замечания.append(f"{ч['who']} говорит от лица команды (мы {д['мы']} против я {д['я']}).")
        if д["сомнение"] >= 2 * max(д["уверенность"], 0.3) and д["сомнение"] >= 1.5:
            замечания.append(f"{ч['who']} часто сомневается: «наверно», «может», «кажется».")
        if д["негатив"] >= 2 * max(д["позитив"], 0.3) and д["негатив"] >= 1:
            замечания.append(f"У {ч['who']} недовольства заметно больше, чем одобрения.")
    if замечания:
        строки += ["", "**Что заметно**"] + [f"- {з}" for з in замечания]
    строки += ["", "_Подсказка по словам, а не оценка человека: словари короткие и не видят иронии._"]
    return {"people": люди, "markdown": "\n".join(строки)}
