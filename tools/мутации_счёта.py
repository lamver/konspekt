"""Мутации для счёта в чате: «-9» после «3» обязано дать -6."""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКИ = [КОРЕНЬ / "tests/чат_счёт_test.py"]
К = "app/llm/calc.py"
С = "app/core/service.py"

МУТАЦИИ = [
    (К, "            в = f\"({показать(прошлое).replace(',', '.')}){в}\"\n", "            pass\n",
     "«-9» не продолжает прошлый счёт"),
    (К, "    if not any(ч in _ДЕЙСТВИЯ for ч in в.lstrip(\"+-−( \")):\n        return None\n", "",
     "«2024» отвечается эхом"),
    (К, "        return float(узел.value)\n", "        return узел.value\n",
     "счёт в целых: 9^9^9 вешает программу"),
    (К, "    except ZeroDivisionError:\n        return \"На ноль делить нельзя.\"\n", "",
     "деление на ноль роняет чат"),
    (К, "    if abs(x - round(x)) < 1e-9:\n", "    if False:\n", "целые показываются как 3.0"),
    (К, "    return f\"{x:.10g}\".replace(\".\", \",\")\n", "    return str(x)\n",
     "0,1+0,2 = 0.30000000000000004"),
    (К, "    if re.fullmatch(r\"[-−]?\\d+(?:\\.\\d+)?\", t):\n", "    if re.fullmatch(r\"\\d+\", t):\n",
     "после отрицательного ответа счёт рвётся"),
    (К, "    if not в or not _ЗНАКИ.match(в) or not re.search(r\"\\d\", в):\n",
     "    if not в or not re.search(r\"\\d\", в):\n", "в калькулятор пускаются буквы"),
    (С, "            посчитано = calc.ответ(question, прошлый)\n",
     "            посчитано = None\n", "пример уходит в модель"),
    (С, "                return\n            ctx = self._chat_context(meeting_id, question, history)\n",
     "            ctx = self._chat_context(meeting_id, question, history)\n",
     "после счёта модель всё равно зовётся"),
    (К, "    в = _слова_в_знаки((вопрос or \"\").strip()).rstrip(\"=?\").strip()\n",
     "    в = (вопрос or \"\").strip().rstrip(\"=?\").strip()\n", "«минус 400» словами не считается"),
    (К, "    т = текст.lower().replace(\"ё\", \"е\")\n", "    т = текст.replace(\"ё\", \"е\")\n",
     "«Минус 400» с большой буквы не считается"),
]


def прогнать() -> int:
    for проверка in ПРОВЕРКИ:
        код = subprocess.run([sys.executable, str(проверка)], cwd=КОРЕНЬ, capture_output=True,
                             env={**os.environ, "PYTHONUTF8": "1"}, timeout=120).returncode
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
            try:
                упала = прогнать() != 0
            except subprocess.TimeoutExpired:
                упала = True
        finally:
            путь.write_bytes(исходник)
        print(f"[{'поймана' if упала else 'ПРОПУЩЕНА'}] {что}")
        поймано += упала
    print(f"\nПоймано {поймано} из {len(МУТАЦИИ)}")
    return 0 if поймано == len(МУТАЦИИ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
