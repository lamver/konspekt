"""Мутации для пробного периода.

Ошибка в одну сторону — человек платит за то, что должно быть
бесплатным, или теряет доступ к своим записям. В другую — пробный
период не кончается никогда. Обе тихие.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКИ = [КОРЕНЬ / "пробный_период_test.py", КОРЕНЬ / "пробный_период_edge_test.py"]
Б = "app/storage/db.py"
С = "app/core/service.py"
Ф = "web/app.js"

МУТАЦИИ = [
    (Б, "                   HAVING SUM(MAX(end_s - start_s, 0)) >= ?\n",
     "                   HAVING SUM(MAX(end_s - start_s, 0)) >= ? * 0\n", "короткая запись съедает пробную встречу"),
    (Б, "                   WHERE meeting_id NOT IN (SELECT meeting_id FROM trial_legacy)\n", "",
     "старые встречи съедают пробу в день обновления"),
    (Б, "            return int(self._conn.execute(\"SELECT COUNT(*) FROM trial_counted\").fetchone()[0])\n",
     "            return int(self._conn.execute(\"SELECT COUNT(*) FROM trial_counted WHERE meeting_id IN (SELECT id FROM meetings)\").fetchone()[0])\n",
     "удаление встречи возвращает пробу"),
    (Б, "        if self._conn.execute(\"SELECT 1 FROM trial_state WHERE key='started'\").fetchone():\n            return\n",
     "", "каждый запуск записывает новые встречи в старые"),
    (Б, "                (now(), float(min_speech), limit - было),\n",
     "                (now(), float(min_speech), -1),\n", "две встречи разом обе становятся пробными"),
    (С, "        if not self._can_record(meeting_id):\n", "        if False:\n",
     "запись после пробы идёт"),
    (С, "        return bool(meeting_id) and self.store.trial_counts(meeting_id)\n",
     "        return False\n", "десятую встречу нельзя дописать"),
    (С, "        if свободно is not None and свободно < len(paths):\n", "        if False:\n",
     "загрузка файлов после пробы идёт"),
    (С, "            paths = list(paths)[:свободно]\n", "            paths = []\n",
     "при остатке 1 из пачки не грузится ничего"),
    (С, "        return self.store.trial_counts(meeting_id) or self.store.trial_legacy(meeting_id)\n",
     "        return False\n", "пробные и старые встречи теряют модель"),
    (С, "        return self.store.trial_counts(meeting_id) or self.store.trial_legacy(meeting_id)\n",
     "        return True\n", "модель открыта и сверх пробы"),
    (С, "        if not self._can_use_model(meeting_id) and calc.ответ(question, self._last_answer(meeting_id)) is None:\n",
     "        if not self._can_use_model(meeting_id):\n", "калькулятор закрыт вместе с моделью"),
    (С, "        if not self._can_use_model(meeting_id):\n            return {\"ok\": False, \"error\": self._msg(\"python.trial.llm\"), \"trial\": True}\n        if not self.llm.enabled:\n            return {\"ok\": False, \"error\": self._msg(\"python.summary.disabled\")}\n        with self._llm_lock:\n            if self._llm_busy:\n                return {\"ok\": False, \"error\": self._msg(\"python.summary.busy\")}\n            self._llm_busy = True\n            self._llm_cancel = False\n\n",
     "        if not self.llm.enabled:\n            return {\"ok\": False, \"error\": self._msg(\"python.summary.disabled\")}\n        with self._llm_lock:\n            if self._llm_busy:\n                return {\"ok\": False, \"error\": self._msg(\"python.summary.busy\")}\n            self._llm_busy = True\n            self._llm_cancel = False\n\n",
     "заметки сверх пробы делаются"),
    (С, "        if self._licensed():\n            return None\n", "",
     "с лицензией идёт счёт"),
    (С, "            license_mod.parse(key)\n        except license_mod.LicenseError:\n            return False\n        return True\n",
     "            pass\n        except license_mod.LicenseError:\n            return False\n        return True\n",
     "поддельный ключ снимает пробу"),
    (Ф, "    ui.licenseBar.hidden = licensed || (!!state.licenseBarHidden && !over);\n",
     "    ui.licenseBar.hidden = licensed || !!state.licenseBarHidden;\n", "после пробы плашку прячут крестиком"),
    (Ф, "      showToast(payload.message || t('license.trial_over'));\n      refreshLicense();\n", "      refreshLicense();\n",
     "«Запись» после пробы молчит"),
    (Ф, "    ui.chatText.value = text;\n    resizeChatInput();\n", "", "не ушедший вопрос пропадает из поля"),
    (Ф, "        n: trial.left, limit: trial.limit, word: tPlural('license.trial_word', trial.left),\n",
     "        n: trial.left, limit: trial.limit, word: tPlural('license.trial_word', 5),\n", "«осталось 1 встреч»"),
]


def прогнать() -> int:
    for проверка in ПРОВЕРКИ:
        код = subprocess.run([sys.executable, str(проверка)], cwd=КОРЕНЬ, capture_output=True,
                             env={**os.environ, "PYTHONUTF8": "1"}, timeout=300).returncode
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
