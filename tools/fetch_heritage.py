"""Объекты культурного наследия России из Викидаты -> data/heritage.json.

Реестр Минкультуры (opendata.mkrf.ru) через зарубежный VPN не открывается,
а в Викидате ~88 тыс. объектов с номером реестра (P1483) и координатами —
«дом купца Чернухина», «особняк Слепцова», торговые дома, храмы сёл. Это
справочник для геокодера и tools/lookup.py (tools/local_places.py): названия
ищутся в нём локально, без запросов и лимитов.

Номер реестра начинается с кода региона (78… — Петербург), по нему и режем
запросы, чтобы SPARQL укладывался в таймаут. Крупные регионы — ещё и по
третьей цифре.

    python tools/fetch_heritage.py
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUT = os.path.join(DATA, "heritage.json")
# QLever — независимый SPARQL по той же Викидате: 08.10 WDQS из-за сбоя
# пускал раз в минуту и всё равно отвечал 429, QLever посчитал все 88 тыс.
# за 0,2 с. Префиксы QLever не подставляет сам — объявляем
SPARQL = "https://qlever.dev/api/wikidata"
PREFIXES = """PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""
UA = "archimap/1.0 (heritage list for a Telegram post map)"

QUERY = """
SELECT ?i ?id ?label (GROUP_CONCAT(DISTINCT ?alt; separator="|") AS ?alts) ?addr ?c WHERE {
  ?i wdt:P1483 ?id ; wdt:P625 ?c .
  FILTER(STRSTARTS(?id, "%s"))
  OPTIONAL { ?i rdfs:label ?label FILTER(lang(?label) = "ru") }
  OPTIONAL { ?i skos:altLabel ?alt FILTER(lang(?alt) = "ru") }
  OPTIONAL { ?i wdt:P6375 ?addr FILTER(lang(?addr) = "ru") }
} GROUP BY ?i ?id ?label ?addr ?c
"""


def ask(prefix):
    url = SPARQL + "?" + urllib.parse.urlencode({"query": PREFIXES + QUERY % prefix})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=70) as r:
        return json.load(r)["results"]["bindings"]


def point(wkt):
    # «Point(37.6 55.7)» — долгота, широта
    lon, lat = wkt[wkt.index("(") + 1:wkt.index(")")].split()
    return round(float(lat), 6), round(float(lon), 6)


PAUSE = 2


def fetch(prefix, out, depth=0):
    tries = 0
    while True:
        try:
            rows = ask(prefix)
            break
        except urllib.error.HTTPError as e:
            # 429 — не наша ошибка, ждём и повторяем тот же запрос
            if e.code == 429:
                print(f"  {prefix}: 429, жду", flush=True)
                time.sleep(PAUSE)
                continue
            print(f"  {prefix}: {e}", flush=True)
        except Exception as e:
            print(f"  {prefix}: {e}", flush=True)
        tries += 1
        time.sleep(PAUSE)
        if tries >= 2:
            rows = None
            break
    if rows is None:
        if depth < 2:
            for d in "0123456789":
                fetch(prefix + d, out, depth + 1)
        else:
            print(f"  {prefix}: пропущен", flush=True)
        return
    time.sleep(PAUSE)
    for r in rows:
        q = r["i"]["value"].rsplit("/", 1)[-1]
        lat, lon = point(r["c"]["value"])
        name = r.get("label", {}).get("value", "")
        alts = [a for a in r.get("alts", {}).get("value", "").split("|") if a]
        if not name and not alts:
            continue
        rec = out.setdefault(q, {"q": q, "id": r["id"]["value"], "name": name or alts[0],
                                 "alt": alts, "addr": r.get("addr", {}).get("value", ""),
                                 "lat": lat, "lon": lon})
        for a in alts:
            if a not in rec["alt"]:
                rec["alt"].append(a)


def main():
    out = {}
    for code in range(1, 100):
        prefix = f"{code:02d}"
        before = len(out)
        fetch(prefix, out)
        print(f"{prefix}: +{len(out) - before} (всего {len(out)})", flush=True)
        # после каждого региона — на диск: сбой на середине не теряет скачанное
        json.dump(sorted(out.values(), key=lambda r: r["id"]), open(OUT, "w", encoding="utf-8", newline="\n"),
                  ensure_ascii=False)
    json.dump(sorted(out.values(), key=lambda r: r["id"]), open(OUT, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False)
    print("объектов:", len(out), "->", OUT)


if __name__ == "__main__":
    main()
