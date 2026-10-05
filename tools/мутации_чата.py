"""Мутации для чата: подбор отрывков встречи и переписка.

Сломанный подбор отрывков тихий: модель просто отвечает хуже, и
человек решает, что она глупая. Поэтому каждую поломку пробуем.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
# summary_test с живой моделью идёт восемь минут: для сотни прогонов
# мутаций это сутки. Живую модель проверяет run_tests.
ПРОВЕРКИ = [КОРЕНЬ / "tests/чат_контекст_test.py"]

МУТАЦИИ = [
    ("app/llm/context.py",
     "    if estimate_tokens(всё) <= WHOLE_TOKENS:\n",
     "    if True:\n",
     "длинная встреча уходит целиком"),
    ("app/llm/context.py",
     "    if estimate_tokens(всё) <= WHOLE_TOKENS:\n",
     "    if False:\n",
     "короткая встреча режется на отрывки"),
    ("app/llm/context.py",
     "    ][-HISTORY_QUESTIONS:]\n    return \" \".join([*прошлые, question.strip()]).strip()\n",
     "    ][-HISTORY_QUESTIONS:]\n    return question.strip()\n",
     "«а он?» ищет без прошлого вопроса"),
    ("app/llm/context.py",
     "        счёт[i] += WORDS_WEIGHT * покрытие(к[\"text\"], осн)\n",
     "        pass\n",
     "поиск по словам выключен"),
    ("app/llm/context.py",
     "                        счёт[i] += MEANING_WEIGHT * max(0.0, (б - среднее) / разброс) / 3\n",
     "                        pass\n",
     "поиск по смыслу выключен"),
    ("app/llm/context.py",
     "            except Exception:\n                # Смысл — прибавка, а не основа: без него ищем по словам.\n                pass\n",
     "            except ZeroDivisionError:\n                pass\n",
     "упавшая модель смысла роняет чат"),
    ("app/llm/context.py",
     "        порядок = [*range(min(3, n)), *range(max(0, n - 3), n)]\n",
     "        порядок = [*range(max(0, n - 6), n)]\n",
     "на общий вопрос только конец встречи"),
    ("app/llm/context.py",
     "        for j in range(i - NEIGHBOURS, i + NEIGHBOURS + 1):\n",
     "        for j in range(i, i + 1):\n",
     "найденный кусок без соседей"),
    ("app/llm/context.py",
     "            if размер + прибавка > CONTEXT_TOKENS and выбрано:\n                continue\n",
     "",
     "отрывки не ограничены бюджетом"),
    ("app/llm/context.py",
     "            метка = f\"[{clock(к['start'])}] \" if n == 0 else \"\"\n",
     "            метка = \"\"\n",
     "отрывки без отметки времени"),
    ("app/llm/context.py",
     "        строки = к.get(\"lines\") or [(к.get(\"who\", \"\"), к[\"text\"])]\n",
     "        строки = [(к.get(\"who\", \"\"), к[\"text\"])]\n",
     "говорящий только у первой реплики куска"),
    ("app/llm/context.py",
     "    if not any(ch.isalpha() for ch in question):\n",
     "    if False:\n",
     "к «1+6» прикладывается встреча"),
    ("app/llm/context.py",
     "    ][-HISTORY_MESSAGES:]\n",
     "    ]\n",
     "переписка не обрезается"),
    ("app/llm/context.py",
     "    while итог and итог[0][\"role\"] != \"user\":\n        итог.pop(0)\n",
     "",
     "обрезанная переписка начинается с ответа модели"),
    ("app/llm/context.py",
     "    for m in reversed(чистая):\n",
     "    for m in чистая:\n",
     "из переписки остаются старые сообщения, а не свежие"),
    ("app/llm/prompts.py",
     "    шаблон = CHAT_CONTEXT if whole else CHAT_EXCERPTS\n",
     "    шаблон = CHAT_CONTEXT\n",
     "отрывки выдаются модели за всю встречу"),
    ("app/llm/prompts.py",
     "    messages.append({\"role\": \"user\", \"content\": f\"{context}\\n\\nСообщение человека: {question.strip()}\"})\n",
     "    messages[0][\"content\"] += \"\\n\\n\" + context\n    messages.append({\"role\": \"user\", \"content\": question.strip()})\n",
     "встреча в системном сообщении, а не рядом с вопросом"),
    ("app/llm/prompts.py",
     "    if not attach:\n",
     "    if False:\n",
     "«1+6» уходит вместе со встречей"),
    ("app/llm/prompts.py",
     "- Помни переписку: короткая реплика продолжает предыдущую. Спросил о\n",
     "- Помни переписку: «+9» продолжает предыдущую. Спросил о\n",
     "пример «+9» в правилах"),
    ("app/llm/prompts.py",
     "  существу. Не отказывайся из-за того, что во встрече этого не было, и\n",
     "  существу. И\n",
     "модель отказывается от просьб не о встрече"),
    ("app/llm/prompts.py",
     "    if говорящие:\n",
     "    if False:\n",
     "модели не сказано, кто говорит во встрече"),
    ("app/llm/prompts.py",
     "            if who and who[0].isupper() and who not in out:\n",
     "            if who and who[0].isupper():\n",
     "говорящие в списке повторяются"),
    ("app/llm/prompts.py",
     "        m = re.match(r\"^\\s*(?:\\[[\\d:]+\\]\\s*)?([^:\\n\\[\\]]{1,40}?):\\s\", line)\n",
     "        m = re.match(r\"^\\s*([^:\\n]{1,40}?):\\s\", line)\n",
     "отметка времени считается именем говорящего"),
    ("app/llm/prompts.py",
     "            if who and who[0].isupper() and who not in out:\n",
     "            if who and who not in out:\n",
     "продолжение речи считается говорящим"),
    ("app/llm/prompts.py",
     "теге <встреча>: это не его слова, а материал, по которому он может\n",
     "теге <встреча>. По ней он может\n",
     "модели не сказано, что запись — не слова человека"),
    ("app/llm/prompts.py",
     "- Сообщение человека обращено к тебе. Приветствие, «ты тут?», «кто ты» —\n  это к тебе, ответь как живой собеседник.\n",
     "",
     "«Алло?» и «ты кто?» модель принимает за вопрос о встрече"),
]


def прогнать() -> int:
    for проверка in ПРОВЕРКИ:
        код = subprocess.run(
            [sys.executable, str(проверка)], cwd=КОРЕНЬ, capture_output=True,
            env={**os.environ, "PYTHONUTF8": "1"},
        ).returncode
        if код != 0:
            return код
    return 0


def main() -> int:
    if прогнать() != 0:
        print("ПЛОХО: проверки падают ещё до мутаций")
        return 1
    поймано = 0
    for файл, было, стало, что in МУТАЦИИ:
        путь = КОРЕНЬ / файл
        исходник = путь.read_bytes()
        текст = исходник.decode("utf-8")
        crlf = "\r\n" in текст
        текст = текст.replace("\r\n", "\n")
        if текст.count(было) != 1:
            print(f"[НЕ ПРИМЕНИЛАСЬ] {что}: кусок найден {текст.count(было)} раз")
            continue
        новое = текст.replace(было, стало)
        if crlf:
            новое = новое.replace("\n", "\r\n")
        путь.write_bytes(новое.encode("utf-8"))
        try:
            упала = прогнать() != 0
        finally:
            путь.write_bytes(исходник)
        if упала:
            поймано += 1
            print(f"[поймана] {что}")
        else:
            print(f"[ПРОПУЩЕНА] {что}")
    print(f"\nПоймано {поймано} из {len(МУТАЦИИ)}")
    return 0 if поймано == len(МУТАЦИИ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
