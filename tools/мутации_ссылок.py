"""Мутации для записи по ссылке: каждая ломает одно обещание, проверка обязана заметить."""
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
ПРОВЕРКИ = [КОРЕНЬ / "tests/ссылки_test.py"]
Л = "app/core/link.py"
О = "app/core/importer.py"
С = "app/core/service.py"
УСЛОВИЕ = "        if беда.code != \"network\" or стоп.is_set() or not _системный_прокси():\n"

МУТАЦИИ = [
    (Л, "(?:[/?#]\\S*)?$\", re.I)", "(?:[/?#].*)?$\", re.I)", "текст с пробелами принят за ссылку"),
    (Л, "    t = (text or \"\").strip().strip(\"<>\\\"'«»\")\n", "    t = (text or \"\").strip()\n",
     "ссылка в кавычках не принимается"),
    (Л, "        t = \"https://\" + t\n", "        pass\n", "ссылка без схемы не дополняется"),
    (Л, "        return \"unsupported\"\n", "        return \"failed\"\n",
     "неподдерживаемый сайт выглядит как непонятный сбой"),
    (Л, "        return \"private\"\n", "        return \"login\"\n",
     "закрытое видео выдаётся за просьбу войти"),
    (Л, УСЛОВИЕ, УСЛОВИЕ.replace(" or стоп.is_set()", ""), "после отмены качаем ещё раз"),
    (Л, УСЛОВИЕ, УСЛОВИЕ.replace("беда.code != \"network\" or ", ""),
     "закрытое видео перезапрашивается напрямую"),
    (Л, УСЛОВИЕ, УСЛОВИЕ.replace(" or not _системный_прокси()", ""),
     "без прокси всё равно вторая попытка"),
    (Л, "    return _скачать(url, папка, прогресс, стоп, {\"proxy\": \"\"})\n",
     "    return _скачать(url, папка, прогресс, стоп, {})\n",
     "вторая попытка снова через прокси"),
    (Л, "        if f.is_file():\n            f.unlink(missing_ok=True)\n", "        pass\n",
     "прямая попытка дописывает недокачанное через прокси"),
    (Л, "    return any(k in (\"http\", \"https\", \"all\") for k in urllib.request.getproxies())\n",
     "    return False\n", "прокси системы не замечается"),
    (Л, "        \"ffmpeg_location\": str(папка / \"без-ffmpeg\"),\n", "",
     "yt-dlp запускает ffmpeg человека"),
    (Л, "        \"js_runtimes\": {},\n", "", "yt-dlp ищет deno"),
    (Л, "    FFmpegPostProcessor._ffmpeg_location.set(опции[\"ffmpeg_location\"])\n", "",
     "загрузчик трансляций запускает ffmpeg человека"),
    (Л, "        \"external_downloader\": {\"default\": \"native\"},\n",
     "        \"external_downloader\": {\"m3u8\": \"ffmpeg\"},\n", "трансляции качаются через ffmpeg"),
    (О, "                стоп.set()\n", "                pass\n", "отмена ждёт конца загрузки"),
    (О, "self._create_meeting(task.title, task.url, task.folder_id)",
     "self._create_meeting(task.title, task.url, None)", "ссылка в папке кладёт встречу в корень"),
    (О, "self._create_meeting(task.title, task.url, task.folder_id)",
     "self._create_meeting(task.title, \"\", task.folder_id)", "ссылка теряется из пометок"),
    (О, "        if звук.title:\n            task.title = звук.title\n", "",
     "встреча называется ссылкой, а не видео"),
    (О, "                cleanup(папка_ссылки)\n", "                pass\n",
     "скачанный звук остаётся во временной папке"),
    (О, "            task.error = exc.code\n", "            task.error = \"failed\"\n",
     "ошибка сайта без понятной причины"),
    (О, "            \"name\": (self.title or self.url) if self.url else self.path.name,\n",
     "            \"name\": self.path.name,\n", "в очереди голое имя файла"),
    (С, "        if url is None:\n            return {\"ok\": False, \"error\": self._msg(\"python.link.not_link\")}\n",
     "", "текст вместо ссылки уходит в очередь"),
    (С, "            return {\"ok\": False, \"error\": сообщение, \"trial\": True}\n        task = self.importer.add_link(",
     "        task = self.importer.add_link(", "после пробного периода ссылка всё равно принимается"),
    (С, "            meeting.notes = url\n", "            pass\n", "ссылка не сохраняется во встрече"),
    (С, "            self.store.set_meeting_folder(meeting.id, folder_id)\n", "            pass\n",
     "встреча по ссылке не попадает в папку"),
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
        try:
            путь.write_bytes(новое.encode("utf-8"))
            код = прогнать()
        finally:
            путь.write_bytes(исходник)
        if код != 0:
            поймано += 1
            print(f"[поймана] {что}")
        else:
            print(f"[ПРОПУЩЕНА] {что}")
    print(f"\nПоймано {поймано} из {len(МУТАЦИИ)}")
    return 0 if поймано == len(МУТАЦИИ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
