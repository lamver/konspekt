"""Мутации: ловит ли язык_в_разметке_test ту самую беду.

Беда была в том, что страница объявляла английский язык при любом
`?lang=`, и переводился только видимый текст после выполнения скриптов.
Каждая мутация возвращает кусочек этого поведения.
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

КОРЕНЬ = Path(__file__).resolve().parent.parent
СТРАНИЦА = КОРЕНЬ / "docs" / "index.html"
РУССКАЯ = КОРЕНЬ / "docs" / "ru" / "index.html"
КАРТА = КОРЕНЬ / "docs" / "sitemap.xml"
СЛОВАРИ = КОРЕНЬ / "docs" / "i18n.js"

МУТАЦИИ = [
    (
        "русская страница снова объявляет английский (ровно найденная беда)",
        РУССКАЯ, '<html lang="ru">', '<html lang="en">',
    ),
    (
        "заголовок русской страницы остался английским",
        РУССКАЯ,
        "<title>Konspekt — заметки со встреч, которые остаются на вашем компьютере</title>",
        "<title>Konspekt — meeting notes that never leave your computer</title>",
    ),
    (
        "описание не переведено: в выдаче английский текст под русским заголовком",
        РУССКАЯ,
        'name="description" content="Умный блокнот',
        'name="description" content="A smart notepad for meetings. Умный блокнот',
    ),
    (
        "og:locale пропал: мессенджер считает карточку английской",
        РУССКАЯ, '<meta property="og:locale" content="ru">', "",
    ),
    (
        "скрипт снова перебивает язык прошлым выбором человека",
        СТРАНИЦА,
        "  const fromPath = langFromPath();\n  if (LANGS.includes(fromPath)) return fromPath;",
        "",
    ),
    (
        "hreflang снова указывает на ?lang=",
        РУССКАЯ,
        '<link rel="alternate" hreflang="es" href="https://konspekt.aisearch.tech/es/">',
        '<link rel="alternate" hreflang="es" href="https://konspekt.aisearch.tech/?lang=es">',
    ),
    (
        "карта сайта снова обещает ?lang=",
        КАРТА,
        '<loc>https://konspekt.aisearch.tech/es/</loc>',
        '<loc>https://konspekt.aisearch.tech/?lang=es</loc>',
    ),
    (
        "исходник поправили, а страницы не пересобрали",
        СЛОВАРИ,
        "title: 'Konspekt — заметки со встреч, которые остаются на вашем компьютере'",
        "title: 'Konspekt — совершенно другое название'",
    ),
    (
        "карточки удвоились: русские вперемешку с английскими",
        РУССКАЯ,
        '<div class="features" id="features">',
        '<div class="features" id="features">\n'
        '      <div class="feature"><h3>Records both sides</h3>'
        '<p>Your microphone and the system audio.</p></div>',
    ),
]


def прогон() -> tuple[bool, str]:
    итог = subprocess.run([sys.executable, "язык_в_разметке_test.py"],
                          capture_output=True, text=True,
                          encoding="utf-8", cwd=КОРЕНЬ)
    return итог.returncode == 0, (итог.stdout or "") + (итог.stderr or "")


def main() -> int:
    файлы = {СТРАНИЦА, РУССКАЯ, КАРТА, СЛОВАРИ}
    запасы = {ф: ф.read_text(encoding="utf-8") for ф in файлы}

    поймано = 0
    try:
        прошла, вывод = прогон()
        if "[..]" in вывод and "jsdom" in вывод:
            print("[плохо] нет node/jsdom: часть проверки пропускается")
            return 1
        if not прошла:
            print("[плохо] проверка падает и без мутаций")
            return 1
        print("[ок] на целом коде проверка проходит")

        for имя, файл, было, стало in МУТАЦИИ:
            целое = запасы[файл]
            if было not in целое:
                print(f"[плохо] мутация неприменима: {имя}")
                return 1
            файл.write_text(целое.replace(было, стало, 1), encoding="utf-8")
            # Пересобираем только тогда, когда мутация в исходнике: иначе
            # правка не доехала бы до /ru/ и проверка честно ничего бы не
            # увидела. Мутации в самих собранных страницах пересборка,
            # наоборот, затёрла бы — а мутация «забыли пересобрать» на
            # том и держится, что пересборки не было.
            надо_пересобрать = файл in (СТРАНИЦА,) and "не пересобрали" not in имя
            if надо_пересобрать:
                subprocess.run(["node", "tools/собрать-языки.mjs"],
                               capture_output=True, cwd=КОРЕНЬ)
            прошла, _ = прогон()
            # Возвращаем всё: проверка сама пересобирает страницы, и
            # соседние файлы могли измениться.
            for ф, ц in запасы.items():
                ф.write_text(ц, encoding="utf-8")
            if прошла:
                print(f"[плохо] мутация прошла незамеченной: {имя}")
                return 1
            поймано += 1
            print(f"[ок] поймана: {имя}")
    finally:
        for ф, ц in запасы.items():
            ф.write_text(ц, encoding="utf-8")

    print(f"\nМутации: {поймано}/{len(МУТАЦИИ)}. Язык в разметке под присмотром.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
