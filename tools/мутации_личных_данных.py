"""Мутации для поиска личных данных.

Пропуск и ложная тревога здесь оба тихие: человек не узнает, что номер
карты ушёл дальше, или перестанет нажимать кнопку, которая подсвечивает
всё подряд.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКИ = [КОРЕНЬ / "личные_данные_test.py", КОРЕНЬ / "личные_данные_edge_test.py"]
П = "app/core/personal.py"
С = "app/core/service.py"
Ф = "web/app.js"

# Люди и адреса: строки взяты из исходника как есть.
МУТАЦИИ_ЛЮДИ_И_АДРЕСА = [
    (П, '_ОТЧЕСТВО = (r"[А-ЯЁ][а-яё]+(?:ович|евич|ич)(?:а|у|ем|е)?\\b"\n',
     '_ОТЧЕСТВО = (r"(?i:[а-яё]+)(?:ович|евич|ич)(?:а|у|ем|е)?\\b"\n',
     'отчество ищется и со строчной'),
    (П, '    re.compile(r"\\b(?i:" + _ИМЯ_СПИСОК + r")(?<=[А-ЯЁа-яё])\\s+" + _ФАМИЛИЯ),\n',
     '',
     'не ищем «Имя Фамилия»'),
    (П, '    "милан наталь натали нин оксан ольг полин раис светлан снежан софи софь тамар "\n',
     '    "милан наталь натали нин оксан ольг полин раис светлан снежан софи софь тамар вер лев "\n',
     'в списке имён слова «вера» и «лев»'),
    (П, '               r"(?:(?:" + _Р + _КОРПУС + r")+(?:" + _Р + _КВАРТИРА + r")?|" + _Р + _КВАРТИРА + r"))"),\n',
     '               r"(?:(?:" + _Р + _КОРПУС + r")*(?:" + _Р + _КВАРТИРА + r")?))"),\n',
     'одинокий «дом 2» считается адресом'),
    (П, '    re.compile(r"(?:" + _З + r"(?:\\s+" + _З + r")?" + _Р + r")?(?i:" + _ДОМ +\n',
     '    re.compile(r"(?i:" + _ДОМ +\n',
     'из адреса выпадает улица перед домом'),
    (П, '    re.compile(r"(?i:" + _УЛИЦА + r")\\s+" + _НАЗВАНИЕ + _Р + r"(?:" + _ДОМ + r"|\\d{1,4}[а-я]?\\b)"\n',
     '    re.compile(r"НЕ_НАЙДЁТСЯ"\n',
     'не ищем «улица Название, дом»'),
]

МУТАЦИИ = [
    (П, "        сумма += d\n    return сумма % 10 == 0\n", "        сумма += d\n    return True\n",
     "карта без проверки Луна"),
    (П, "        if i % 2:\n            d *= 2\n", "        if i % 2 == 0:\n            d *= 2\n",
     "Лун считает не те цифры"),
    (П, "        return к((2, 4, 10, 3, 5, 9, 4, 6, 8), 9) == int(цифры[9])\n",
     "        return True\n", "ИНН организации без контрольной цифры"),
    (П, "            and к((3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8), 11) == int(цифры[11])\n",
     "", "ИНН человека проверяется наполовину"),
    (П, "    return контроль == int(цифры[9:])\n", "    return True\n", "СНИЛС без контрольной суммы"),
    (П, "    return int(число) % 97 == 1\n", "    return True\n", "IBAN без mod 97"),
    (П, "        r\"(?i)паспорт\\w*[^\\d\\n]{0,40}?(\" + _Н + r\"\\d{2}[ ]?\\d{2}[ №]{0,3}\\d{6}\" + _К + r\")\"\n",
     "        r\"(\" + _Н + r\"\\d{2}[ ]?\\d{2}[ №]{0,3}\\d{6}\" + _К + r\")\"\n",
     "паспорт ищется без слова «паспорт»"),
    (П, "_Н = r\"(?<![\\d])\"\n", "_Н = r\"\"\n", "номер ищется внутри длинного числа"),
    (П, "ВИДЫ = (\"email\", \"card\", \"iban\", \"snils\", \"inn\", \"passport\", \"phone\", \"address\", \"person\")\n",
     "ВИДЫ = (\"email\", \"phone\", \"card\", \"iban\", \"snils\", \"inn\", \"passport\", \"address\", \"person\")\n",
     "телефон перебивает карту"),
    (П, "        if any(н.start < п.end and п.start < н.end for п in принято):\n            continue\n",
     "", "пересекающиеся находки обе заменяются"),
    (С, "        if self.llm.backend != \"remote\" or not self.settings.llm.mask_personal_remote:\n",
     "        if True:\n", "на свой сервер уходит всё как есть"),
    (С, "        if self.llm.backend != \"remote\" or not self.settings.llm.mask_personal_remote:\n",
     "        if not self.settings.llm.mask_personal_remote:\n", "своей модели тоже скрываем"),
    (С, "        return [{**m, \"content\": personal.mask(m.get(\"content\") or \"\")} for m in messages]\n",
     "        for m in messages:\n            m[\"content\"] = personal.mask(m.get(\"content\") or \"\")\n        return messages\n",
     "скрытие портит исходные сообщения"),
    (Ф, "  if (вид === 'masked') return копироватьБезЛичного(текст);\n", "",
     "«без личных данных» копирует как есть"),
    (Ф, "  if (typeof скрытый !== 'string') {\n    showToast(t('copy.failed'));\n    return false;\n  }\n",
     "  if (typeof скрытый !== 'string') скрытый = текст;\n",
     "при сбое в буфер уходит нескрытый текст"),
    (Ф, "      метка.textContent = текст.slice(н.start, н.end);\n",
     "      метка.innerHTML = текст.slice(н.start, н.end);\n", "находка вставляется как HTML"),
    (Ф, "    тело.appendChild(document.createTextNode(текст.slice(прошлый)));\n", "",
     "хвост реплики после находки теряется"),
    (Ф, "  if (state.personalShown) {\n    clearPersonalMarks();\n", "  if (state.personalShown) {\n",
     "подсветка не убирается"),
] + МУТАЦИИ_ЛЮДИ_И_АДРЕСА


def прогнать() -> int:
    for проверка in ПРОВЕРКИ:
        код = subprocess.run(
            [sys.executable, str(проверка)], cwd=КОРЕНЬ, capture_output=True,
            env={**os.environ, "PYTHONUTF8": "1"}, timeout=300,
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
            try:
                упала = прогнать() != 0
            except subprocess.TimeoutExpired:
                упала = True
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
