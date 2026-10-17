# -*- coding: utf-8 -*-
"""Списки «Культурное наследие России» из Викигида → data/wikivoyage_heritage.json.

    python tools/fetch_wikivoyage.py

Почти полный реестр памятников, с региональными и местными, — и с адресами:
пасторат в Астрахани («Здание Пастората… Казанская улица, 102-А», 16.10) был
только здесь, ни в реестре Викидаты, ни в выгрузке Минкультуры с точками.
Координаты есть у части записей (у Астрахани — у 43 из 226), адрес — почти у
всех: точку по адресу находит геокодер (local_places.search_addressed).

~3000 страниц, по 50 за запрос — минуты, без ключей.
"""
import json, os, re, time, urllib.parse, urllib.request

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUT = os.path.join(DATA, "wikivoyage_heritage.json")
API = "https://ru.wikivoyage.org/w/api.php"
UA = {"User-Agent": "archimap/0.1 (architecture posts map; one-off heritage list export)"}
PREFIX = "Культурное наследие России/"

FIELDS = ("name", "address", "municipality", "district", "region", "lat", "long", "precise",
          "knid", "complex", "type", "year", "wdid", "munid", "image")


def get(params):
    params = {**params, "format": "json"}
    for attempt in range(5):
        try:
            req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers=UA)
            return json.load(urllib.request.urlopen(req, timeout=60))
        except OSError as e:
            print("  повтор:", e)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("Викигид не отвечает")


def page_ids():
    ids, cont = [], {}
    while True:
        r = get({"action": "query", "list": "allpages", "apprefix": PREFIX, "aplimit": "500", **cont})
        ids += [p["pageid"] for p in r["query"]["allpages"]]
        if "continue" not in r:
            return ids
        cont = r["continue"]
        time.sleep(0.5)


# {{monument |name= … |address= … }}: поля через «|», значение до следующего «|поле=»
FIELD_RE = re.compile(r"\|\s*([a-z_]+)\s*=([^|]*)")


def clean(v):
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)   # [[ссылка|текст]] → текст
    v = re.sub(r"<[^>]+>|\{\{[^}]*\}\}|'''?", "", v)
    return " ".join(v.split())


def monuments(wikitext):
    for block in re.findall(r"\{\{\s*[Mm]onument(.*?)\n\}\}", wikitext, re.S):
        rec = {}
        for k, v in FIELD_RE.findall(block):
            if k in FIELDS:
                rec[k] = clean(v)
        if not rec.get("name"):
            continue
        try:
            lat, lon = float(rec.pop("lat", "") or "x"), float(rec.pop("long", "") or "x")
            rec["lat"], rec["lon"] = round(lat, 6), round(lon, 6)
        except ValueError:
            rec.pop("lat", None), rec.pop("long", None)
        yield {k: v for k, v in rec.items() if v not in ("", None)}


def main():
    ids = page_ids()
    print("страниц:", len(ids))
    out = []
    for i in range(0, len(ids), 50):
        r = get({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                 "pageids": "|".join(map(str, ids[i:i + 50]))})
        for p in r["query"]["pages"].values():
            text = p["revisions"][0]["slots"]["main"]["*"]
            out += list(monuments(text))
        print(f"  {min(i + 50, len(ids))}/{len(ids)}, записей {len(out)}")
        time.sleep(1)
    json.dump(out, open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False)
    with_point = sum("lat" in r for r in out)
    with_addr = sum(bool(r.get("address")) for r in out)
    print(f"записей {len(out)}, с точкой {with_point}, с адресом {with_addr} -> {OUT}")


if __name__ == "__main__":
    main()
