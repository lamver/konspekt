"""Мутации для загрузки весов с нескольких источников.

Поломка тут видна только когда Hugging Face уже заблокировали, а это
худший момент её узнать.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКИ = [КОРЕНЬ / "зеркала_test.py", КОРЕНЬ / "resume_test.py"]
Ф = "app/asr/download.py"

МУТАЦИИ = [
    (Ф, "    for основа in (os.environ.get(ENV_BASE, \"\"), CDN_BASE):\n",
     "    for основа in (os.environ.get(ENV_BASE, \"\"),):\n",
     "свой CDN не используется"),
    (Ф, "    итог.append(HF_BASE)\n    return итог\n",
     "    итог.insert(0, HF_BASE)\n    return итог\n",
     "Hugging Face идёт раньше CDN"),
    (Ф, "    итог.append(HF_BASE)\n    return итог\n",
     "    return итог or [HF_BASE]\n",
     "при живом CDN нет запасного Hugging Face"),
    (Ф, "            шаблон = основа if \"{repo}\" in основа else основа + \"/{repo}/{name}\"\n",
     "            шаблон = основа if \"{repo}\" in основа else основа + \"/{name}\"\n",
     "путь на CDN без репозитория"),
    (Ф, "                except MirrorFailed as exc:\n                    last_error = exc\n                    log.warning(\"Источник %d не отдаёт %s: %s\", номер + 1, name, exc)\n                    break\n",
     "",
     "на 404 долбим источник повторами"),
    (Ф, "            if exc.code in (401, 403, 404, 410, 451):\n",
     "            if exc.code in (401, 403, 404, 410):\n",
     "блокировка 451 не ведёт к другому источнику сразу"),
    (Ф, "                    if впустую >= 3:\n",
     "                    if впустую >= 3 and False:\n",
     "застрявший источник не бросаем"),
    (Ф, "        url = (шаблон or HF_BASE).format(repo=self.repo, name=name)\n",
     "        url = HF_BASE.format(repo=self.repo, name=name)\n",
     "источник выбран, но качаем всё равно с Hugging Face"),
]


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
