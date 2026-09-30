"""Мутации для разметки ответов.

Беда тут бывает двух видов: код развалился на строки (видно, но
раздражает) и HTML из ответа модели исполнился (не видно, и это хуже:
в ответ может попасть что угодно из расшифровки).
"""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКИ = [КОРЕНЬ / "разметка_edge_test.py"]
Ф = "web/app.js"
С = "web/styles.css"

МУТАЦИИ = [
    (Ф, "      out.push(`<pre class=\"md-code\">${подпись}<code>${esc(код.join('\\n'))}</code></pre>`);\n",
     "      out.push(`<pre class=\"md-code\">${подпись}<code>${код.join('\\n')}</code></pre>`);\n",
     "код вставляется как HTML"),
    (Ф, "    s = esc(s)\n", "    s = String(s)\n", "текст ответа вставляется как HTML"),
    (Ф, "    const ограда = line.match(/^```\\s*([\\w+#.-]*)/);\n", "    const ограда = null;\n",
     "блоки кода не разбираются"),
    (Ф, "      while (i < строки.length && !/^\\s*```\\s*$/.test(строки[i])) {\n",
     "      while (i < строки.length && !/^\\s*```\\s*$/.test(строки[i]) && строки[i].trim()) {\n",
     "пустая строка в коде рвёт блок"),
    (Ф, "    let s = String(raw).replace(/`([^`\\n]+)`/g, (m, код) => {\n",
     "    let s = String(raw).replace(/НЕ_НАЙДЁТСЯ/g, (m, код) => {\n",
     "`код` в строке не разбирается"),
    (Ф, "    const номер = line.match(/^(\\d+)[.)]\\s+(.*)$/);\n", "    const номер = null;\n",
     "списки с номерами не разбираются"),
    (Ф, "      .replace(/\\[([^\\]]+)\\]\\(([^)\\s]+)\\)/g, (m, надпись, адрес) => `<span class=\"md-link\" title=\"${адрес}\">${надпись}</span>`)\n",
     "      .replace(/\\[([^\\]]+)\\]\\(([^)\\s]+)\\)/g, (m, надпись, адрес) => `<a href=\"${адрес}\">${надпись}</a>`)\n",
     "ссылка уводит окно"),
    (Ф, "    if (line.includes('|') && i + 1 < строки.length", "    if (false && i + 1 < строки.length",
     "таблицы не разбираются"),
    (Ф, "    const цит = line.match(/^>\\s?(.*)$/);\n", "    const цит = null;\n", "цитаты не разбираются"),
    (Ф, "    node.innerHTML = renderMarkdown(text);\n    добавитьКопиюКода(node);\n",
     "    node.innerHTML = renderMarkdown(text);\n", "у кода нет кнопки копии"),
    (Ф, "      копировать(код ? код.textContent : '', 'copy.done_code');\n",
     "      копировать(блок.textContent, 'copy.done_code');\n", "в буфер уходит код с подписью и кнопкой"),
    (С, ".md-code code {\n  padding: 0;\n  background: none;\n  font-size: 11.5px;\n  white-space: pre;\n}\n",
     ".md-code code {\n  padding: 0;\n  background: none;\n  font-size: 11.5px;\n  white-space: normal;\n}\n",
     "отступы в коде схлопываются"),
    (С, "  overflow-x: auto;\n  white-space: pre;\n  line-height: 1.5;\n",
     "  white-space: pre;\n  line-height: 1.5;\n", "длинная строка кода распирает окно"),
]


def прогнать() -> int:
    for проверка in ПРОВЕРКИ:
        код = subprocess.run([sys.executable, str(проверка)], cwd=КОРЕНЬ, capture_output=True,
                             env={**os.environ, "PYTHONUTF8": "1"}, timeout=180).returncode
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
