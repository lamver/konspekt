"""Мутации для выбора своей модели.

Сломанный выбор тихий: человек выбрал умную, а отвечает быстрая, и он
решает, что умная ничем не лучше.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКИ = [КОРЕНЬ / "tests/модели_выбор_test.py", КОРЕНЬ / "tests/llm_download_test.py", КОРЕНЬ / "tests/модели_edge_test.py"]
М = "app/llm/manager.py"
Л = "app/llm/local.py"
С = "app/core/service.py"
Ф = "web/app.js"

МУТАЦИИ = [
    (Л, "    if tier is not None:\n        путь = tier_path(tier)\n        return путь if путь.exists() else None\n",
     "", "ищется любая модель вместо выбранной"),
    (Л, "    return tier if tier in LOCAL_MODELS else DEFAULT_TIER\n",
     "    return tier or DEFAULT_TIER\n", "незнакомый выбор не откатывается"),
    (М, "        return tier_or_default(getattr(self._settings(), \"local_model\", None))\n",
     "        return \"fast\"\n", "выбор модели игнорируется"),
    (М, "            if self._server is not None and self._server.model_path != model:\n",
     "            if False:\n", "сервер с прежней моделью не гасится"),
    (М, "        if self.backend != \"local\" or find_model(self.tier) is not None:\n",
     "        if self.backend != \"local\" or find_model() is not None:\n",
     "выбранная модель не качается, раз лежит другая"),
    (М, "            \"model_ready\": model is not None,\n",
     "            \"model_ready\": find_model() is not None,\n",
     "«готово» по чужой модели"),
    (С, "        cfg.local_model = tier_or_default(cfg.local_model)\n",
     "", "мусор вместо модели записывается в настройки"),
    (С, "        return {**self.llm.status(), \"ram_gb\": total_ram_gb()}\n",
     "        return self.llm.status()\n", "окно не знает памяти компьютера"),
    (Ф, "    if (status.ram_gb && status.ram_gb + 0.5 < m.ram_gb) {\n",
     "    if (false) {\n", "нет предупреждения о нехватке памяти"),
    (Ф, "  ui.llmTiers.hidden = status.backend !== 'local' || !models.length;\n",
     "  ui.llmTiers.hidden = !models.length;\n", "карточки видны у своего сервера"),
    (Ф, "    card.addEventListener('click', () => chooseLlmTier(m.code));\n",
     "", "щелчок по карточке ничего не делает"),
    (Ф, "    card.className = 'llm-tier' + (m.code === status.local_model ? ' is-current' : '');\n",
     "    card.className = 'llm-tier';\n", "выбранная модель не выделена"),
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
