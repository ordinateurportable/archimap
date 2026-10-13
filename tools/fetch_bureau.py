"""Советы Бюро Горбунова (bureau.ru/soviet) — к себе, для чтения в сессии.

Тексты авторские: сохраняем в data/bureau/ (в .gitignore, на стенд не
выкладывается) и берём оттуда правила своими словами со ссылкой на совет.

Сайт не пускает зарубежные адреса — запускать без VPN, со своего компьютера.
Качаем вежливо, как внимательный читатель: robots.txt соблюдаем, страницы по
одной, пауза 3–5 с, честная подпись программы; при отказе (403/429) или
подряд нескольких ошибках — останавливаемся. Повторный запуск докачивает
только недостающее.

    python tools/fetch_bureau.py                 # рубрики про интерфейс (Бирман, Мозговой)
    python tools/fetch_bureau.py --section ilyahov   # другая рубрика (текст — Ильяхов)
    python tools/fetch_bureau.py --list          # только найти ссылки, ничего не качать

Советы разложены по рубрикам авторов: /soviet/ilyabirman/ — «Интерфейс и
навигация», /soviet/mozgovoy/ — «Интерфейс», /soviet/ilyahov/ — «Текст» и
т. д. Общая лента /soviet/ — все 4882 совета подряд, её не качаем.

Если ссылок на советы не нашлось — стартовая страница лежит в
data/bureau/_start-<рубрика>.html: по ним разберём разметку и поправим парсер.
"""
import argparse, html, json, os, random, re, sys, time, urllib.error, urllib.parse, urllib.request
import urllib.robotparser
from html.parser import HTMLParser

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(BASE, "data", "bureau")
SITE = "https://bureau.ru"
UA = "archimap-reader/1.0 (personal reading copy; one page per few seconds)"
# совет — страница вида /soviet/20190305/ (номер — дата публикации)
ADVICE_RE = re.compile(r"^/soviet/\d{6,8}/?$")


class Page(HTMLParser):
    """Заголовок, ссылки и текст страницы — без сторонних библиотек."""

    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form"}
    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "blockquote", "pre", "figcaption", "dt", "dd", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.title, self.h1 = [], "", ""
        self.parts, self._skip, self._buf, self._in_title, self._in_h1 = [], 0, [], False, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self._skip += 1
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        if tag == "title":
            self._in_title = True
        if tag == "h1":
            self._in_h1 = True
        if tag in self.BLOCK or tag == "br":
            self._flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_h1 = False
        if tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._in_h1:
            self.h1 += data
        if not self._skip:
            self._buf.append(data)

    def _flush(self):
        text = " ".join("".join(self._buf).split())
        if text:
            self.parts.append(text)
        self._buf = []

    def text(self):
        self._flush()
        return "\n\n".join(self.parts)


# после самого совета на странице идут призыв дополнить, комментарии,
# «Рекомендуем другие советы» и подборки — это не совет, обрезаем
TAIL_RE = re.compile(r"\n\n(?:Уважаемые советчики!|Комментарии\n|Рекомендуем другие советы)")


def clean(text):
    text = TAIL_RE.split(text, maxsplit=1)[0]
    # «Редакторы: …» повторяется у каждой иллюстрации — оставляем первую
    seen, out = set(), []
    for block in text.split("\n\n"):
        if block.startswith("Редакторы:") and block in seen:
            continue
        seen.add(block)
        out.append(block)
    return "\n\n".join(out).strip()


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def parse(raw):
    p = Page()
    p.feed(raw)
    return p


def pause():
    time.sleep(3 + random.random() * 2)


# рубрики про интерфейс: «Интерфейс и навигация» (Илья Бирман) и
# «Интерфейс» (Константин Мозговой)
INTERFACE = ["ilyabirman", "mozgovoy"]


def collect(start, section, robots):
    """Ссылки на советы рубрики: её страница и страницы-продолжения списка."""
    seen, queue, found = set(), [start], []
    while queue and len(seen) < 60:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            raw = get(url)
        except urllib.error.HTTPError as e:
            print(f"{url}: {e.code} — стоп")
            break
        if len(seen) == 1:
            open(os.path.join(OUT, f"_start-{section}.html"), "w", encoding="utf-8", newline="\n").write(raw)
        for href in parse(raw).links:
            full_url = urllib.parse.urljoin(url, href)
            path = urllib.parse.urlparse(full_url).path
            if ADVICE_RE.match(path):
                if SITE + path not in found:
                    found.append(SITE + path)
            # продолжение списка той же рубрики: «?page=2», «/soviet/ilyabirman/2/»
            elif path.startswith(f"/soviet/{section}/") and (re.search(r"page", href) or re.search(r"/\d+/?$", path)):
                if full_url not in seen and full_url not in queue and robots.can_fetch(UA, full_url):
                    queue.append(full_url)
        print(f"   {section}, список {len(seen)}: советов {len(found)}", flush=True)
        pause()
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", action="append",
                    help="рубрика: ilyabirman, mozgovoy, ilyahov…; по умолчанию — интерфейс")
    ap.add_argument("--max", type=int, default=2000, help="не больше N советов за запуск")
    ap.add_argument("--list", action="store_true", help="только собрать ссылки")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    robots = urllib.robotparser.RobotFileParser(SITE + "/robots.txt")
    try:
        robots.read()
    except Exception as e:
        print("robots.txt не прочитался:", e, "— похоже, сайт недоступен (VPN включён?)")
        sys.exit(1)
    sections = args.section or INTERFACE
    advice = []  # [(ссылка, рубрика)]
    for section in sections:
        start = f"{SITE}/soviet/{section}/"
        if not robots.can_fetch(UA, start):
            print("robots.txt запрещает", start, "— пропускаем")
            continue
        found = collect(start, section, robots)
        print(f"рубрика {section}: советов {len(found)}", flush=True)
        advice += [(u, section) for u in found if u not in {a for a, _ in advice}]
    if not advice:
        print("ссылок на советы не нашлось — пришлите data/bureau/_start-*.html, поправлю парсер")
        sys.exit(1)
    json.dump(advice, open(os.path.join(OUT, "_links.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if args.list:
        print("ссылок:", len(advice), "->", os.path.join(OUT, "_links.json"))
        return

    # 2. сами советы, по одному
    done = errors = 0
    for url, section in advice:
        if done >= args.max:
            break
        name = re.search(r"/soviet/(\d+)", url).group(1)
        path = os.path.join(OUT, f"{name}.json")
        if os.path.exists(path):
            continue
        if not robots.can_fetch(UA, url):
            continue
        try:
            raw = get(url)
            errors = 0
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                print(f"{url}: {e.code} — сайт просит остановиться, стоп. Скачано {done}")
                break
            errors += 1
            print(f"{url}: {e.code}")
        except Exception as e:
            errors += 1
            print(f"{url}: {e}")
        else:
            page = parse(raw)
            title = html.unescape((page.h1 or page.title).strip())
            json.dump({"url": url, "section": section, "title": title, "text": clean(page.text())},
                      open(path, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
            done += 1
            print(f"{done}: {title[:70]}", flush=True)
        if errors >= 5:
            print("пять ошибок подряд — стоп")
            break
        pause()
    print(f"готово: скачано {done}, всего у себя {sum(1 for f in os.listdir(OUT) if f[0].isdigit())} -> {OUT}")


if __name__ == "__main__":
    main()
