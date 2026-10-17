# -*- coding: utf-8 -*-
"""Поиск точки объекта «вручную» — для постов, где модель назвала здание
верно, а геокодер его не нашёл (другое название на карте, нет в OSM).

    python tools/lookup.py "Сретенский собор" --near "Никольск" [--region "Вологодская область"]
    python tools/lookup.py "Дом Губина" --at 52.61,39.60
    python tools/lookup.py --nearby --at 52.46,104.24 --radius 2   # что рядом: храмы, памятники, Викидата

Ищет сразу в нескольких местах и печатает кандидатов с расстоянием до центра:
  okn/osm — свои справочники (реестры наследия, выгрузка OSM);
  wv  — списки наследия Викигида: с точкой или адресом (адрес — искать следом);
  wd  — Викидата: подпись, описание, координаты;
  ph, nom — OpenStreetMap через Photon и Nominatim;
  wiki — поиск по тексту русской Википедии: статьи с координатами и отрывки
        (в статье об улице бывает номер дома нужного здания);
  osm — OpenStreetMap через Overpass: объекты с таким словом в названии
        в радиусе --radius км (по умолчанию 25);
  ga  — Geoapify (ключ tools/.geocoder-key; в вывод не попадает).
Решение — какую точку взять — за тем, кто ищет: скрипт только показывает.
"""
import argparse, json, os, re, sys, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geocode_streets as g
import local_places

# Викидата просит в User-Agent контакт — без него быстрее получаешь 429
UA = {"User-Agent": "archimap-lookup/0.2 (+https://archimap.194.226.142.99.sslip.io)"}

# Общая очередь запросов на все процессы: lookup.py запускают сразу
# несколько агентов, и пять агентов давали пачки запросов — Викидата и
# Overpass отвечали 429 почти на всё (29.09–01.10), а там недалеко и до
# бана адреса. Не чаще одного запроса к сервису раз в GAP секунд — для
# всех процессов вместе (время следующего разрешённого запроса лежит в
# файле под блокировкой); на 429 сервис отдыхает COOLDOWN секунд у всех.
GAP = 1.5
COOLDOWN = 60
THROTTLE = os.path.join(g.DATA, "lookup-throttle.json")
# Overpass целиком (все зеркала) не ответил — до этого времени его не спрашиваем
OVERPASS_OFF = "overpass-off"
OVERPASS_REST = 30 * 60


def _locked(fn):
    """fn(state) -> state, под межпроцессной блокировкой файла."""
    import time
    lock_path = THROTTLE + ".lock"
    with open(lock_path, "a+b") as lock:
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                state = json.load(open(THROTTLE, encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            state = fn(state)
            json.dump(state, open(THROTTLE, "w", encoding="utf-8"))
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    return state


def _wait_turn(host):
    import time
    slot = {}

    def take(state):
        now = time.time()
        start = max(now, state.get(host, 0))
        state[host] = start + GAP
        slot["wait"] = start - now
        return state
    _locked(take)
    if slot["wait"] > 0:
        time.sleep(slot["wait"])


def _cool_down(host):
    import time
    _locked(lambda state: {**state, host: max(state.get(host, 0), time.time() + COOLDOWN)})


def get(url, data=None):
    host = urllib.parse.urlparse(url).netloc
    _wait_turn(host)
    req = urllib.request.Request(url, data=data, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            _cool_down(host)
        raise


def wikidata(name, center):
    q = urllib.parse.urlencode({"action": "wbsearchentities", "search": name, "language": "ru",
                                "uselang": "ru", "format": "json", "limit": 8})
    ids = [x["id"] for x in get("https://www.wikidata.org/w/api.php?" + q).get("search", [])]
    if not ids:
        return []
    q = urllib.parse.urlencode({"action": "wbgetentities", "ids": "|".join(ids), "props": "claims|labels|descriptions",
                                "languages": "ru", "format": "json"})
    ents = get("https://www.wikidata.org/w/api.php?" + q)["entities"]
    out = []
    for i in ids:
        e = ents[i]
        c = e.get("claims", {}).get("P625")
        c = c and c[0]["mainsnak"].get("datavalue", {}).get("value")
        if not c:
            continue
        out.append(("wd", f"{e.get('labels', {}).get('ru', {}).get('value', '?')} — "
                          f"{e.get('descriptions', {}).get('ru', {}).get('value', '')} ({i})",
                    c["latitude"], c["longitude"]))
    return out


def overpass(name, center, radius):
    # по самому значимому слову: «Сретенский собор» → «Сретенск»
    words = sorted(re.findall(r"[А-ЯЁа-яёA-Za-z]{4,}", name), key=len, reverse=True)
    if not words:
        return []
    stem = words[0][:-2] if len(words[0]) > 6 else words[0]
    # квадрат, а не around: around не ловит отношения (Манеж в кремле — relation)
    import math
    lat, lon = center
    dlat, dlon = radius / 111, radius / (111 * math.cos(math.radians(lat)))
    q = (f'[out:json][timeout:25];nwr["name"~"{stem}",i]({lat - dlat},{lon - dlon},{lat + dlat},{lon + dlon});'
         f"out center tags 30;")
    # Все три сервера не ответили — Overpass отдыхает полчаса у всех агентов.
    # 07.10 основной молчал по 25 с на запрос (соединение не устанавливалось),
    # зеркала сразу отдавали 500: каждый поиск ждал впустую, а если адрес
    # закрыли на экране сервера, лишние запросы ему только вредят
    import time
    off = _locked(lambda state: state).get(OVERPASS_OFF, 0)
    if off > time.time():
        raise RuntimeError(f"молчал при прошлом запросе, следующая попытка в {time.strftime('%H:%M', time.localtime(off))}")
    # основной сервер Overpass под нагрузкой отвечает 504 — пробуем зеркала
    data, err = None, None
    for url in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                "https://overpass.private.coffee/api/interpreter"):
        try:
            data = get(url, urllib.parse.urlencode({"data": q}).encode())
            break
        except Exception as e:
            err = e
    if data is None:
        _locked(lambda state: {**state, OVERPASS_OFF: time.time() + OVERPASS_REST})
        raise err
    out = []
    for e in data.get("elements", []):
        c = e.get("center") or {"lat": e.get("lat"), "lon": e.get("lon")}
        t = e.get("tags", {})
        kind = t.get("building") or t.get("amenity") or t.get("tourism") or t.get("historic") or t.get("railway") or ""
        addr = " ".join(x for x in (t.get("addr:street"), t.get("addr:housenumber")) if x)
        out.append(("osm", f"{t.get('name')} [{kind}] {addr}".strip(), c["lat"], c["lon"]))
    return out


def geoapify(name, where, center):
    if not g.GEOAPIFY_KEY:
        return []
    params = {"text": f"{name}, {where}" if where else name, "lang": "ru", "limit": 6, "format": "json",
              "filter": "countrycode:ru", "apiKey": g.GEOAPIFY_KEY}
    if center:
        params["bias"] = f"proximity:{center[1]},{center[0]}"
    out = []
    for r in g.get_json(g.GEOAPIFY_URL, params).get("results", []):
        addr = " ".join(x for x in (r.get("street"), r.get("housenumber")) if x)
        out.append(("ga", f"{r.get('name')} [{r.get('result_type')}] {addr} · {r.get('city') or ''}", r["lat"], r["lon"]))
    return out


def photon(name, center):
    """OpenStreetMap через Photon — тот же поиск, что в форме «Найти место» на
    сайте: Overpass неделями молчит, а Photon по OSM отвечает (метеостанцию
    Кара-Тюрек и дачу Петрова пользователь нашёл именно им, 16.10)."""
    params = {"q": name, "limit": 8, "lang": "default"}
    if center:
        params.update(lat=center[0], lon=center[1])
    out = []
    for f in get("https://photon.komoot.io/api/?" + urllib.parse.urlencode(params)).get("features", []):
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        addr = " ".join(x for x in (p.get("street"), p.get("housenumber")) if x)
        where = ", ".join(x for x in (p.get("city") or p.get("county"), p.get("state")) if x)
        out.append(("ph", f"{p.get('name')} [{p.get('osm_value', '')}] {addr} · {where}", lat, lon))
    return out


def nominatim(name, where):
    """OpenStreetMap через Nominatim — точное имя с городом или областью."""
    q = urllib.parse.urlencode({"q": f"{name}, {where}" if where else name, "format": "jsonv2", "limit": 6,
                                "accept-language": "ru"})
    out = []
    for r in get("https://nominatim.openstreetmap.org/search?" + q):
        out.append(("nom", f"{r.get('name') or r.get('display_name', '')[:60]} [{r.get('type', '')}] · "
                           f"{', '.join(r.get('display_name', '').split(', ')[1:4])}", float(r["lat"]), float(r["lon"])))
    return out


def ruwiki(name, where):
    """Поиск по тексту русской Википедии. Встроенный веб-поиск агента —
    зарубежный индекс, русские краеведческие сайты находит плохо (пасторат в
    Астрахани: «адрес нигде не найден»), а Википедия знает здание внутри
    статьи об улице: «Казанская улица (Астрахань)… дом 102А… Здание
    Пастората». Печатает статьи с отрывком (там бывает адрес) и, если у
    статьи есть координаты, — точку."""
    q = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": f"{name} {where}".strip(),
                                "srlimit": 5, "format": "json"})
    hits = get("https://ru.wikipedia.org/w/api.php?" + q).get("query", {}).get("search", [])
    if not hits:
        return [], []
    titles = "|".join(h["title"] for h in hits)
    q = urllib.parse.urlencode({"action": "query", "prop": "coordinates", "titles": titles, "format": "json"})
    pages = get("https://ru.wikipedia.org/w/api.php?" + q).get("query", {}).get("pages", {})
    coords = {p["title"]: p["coordinates"][0] for p in pages.values() if p.get("coordinates")}
    rows, notes = [], []
    for h in hits:
        snippet = re.sub(r"<[^>]+>", "", h.get("snippet", "")).replace("&quot;", "«").strip()
        c = coords.get(h["title"])
        if c:
            rows.append(("wiki", f"{h['title']} — {snippet[:90]}", c["lat"], c["lon"]))
        else:
            notes.append(f"wiki «{h['title']}»: …{snippet[:160]}…")
    return rows, notes


def nearby(center, radius):
    """Что есть рядом с точкой, без названия: храмы и памятники из Geoapify
    (по категории) и всё, что Викидата знает вокруг. Так нашёлся «Спасский
    храм» в Урике — по названию его не находил никто, а по месту — сразу."""
    lat, lon = center
    out = []
    if g.GEOAPIFY_KEY:
        cats = "religion.place_of_worship,tourism.sights,heritage,building.historic,entertainment.museum"
        data = g.get_json("https://api.geoapify.com/v2/places",
                          {"categories": cats, "filter": f"circle:{lon},{lat},{int(radius * 1000)}",
                           "limit": 30, "lang": "ru", "apiKey": g.GEOAPIFY_KEY})
        for f in data.get("features", []):
            p = f["properties"]
            if p.get("name"):
                addr = " ".join(x for x in (p.get("street"), p.get("housenumber")) if x)
                out.append(("ga", f"{p['name']} [{(p.get('categories') or [''])[0]}] {addr}", p["lat"], p["lon"]))
    q = urllib.parse.urlencode({"action": "query", "list": "geosearch", "gscoord": f"{lat}|{lon}",
                                "gsradius": int(min(radius, 10) * 1000), "gslimit": 30, "format": "json"})
    # Викидата часто не отвечает — найденное Geoapify из-за неё не теряем
    try:
        hits = get("https://www.wikidata.org/w/api.php?" + q).get("query", {}).get("geosearch", [])
    except Exception as e:
        print(f"  wd — Викидата: не ответила — {str(e)[:120]}")
        return out
    if hits:
        ids = "|".join(h["title"] for h in hits)
        q = urllib.parse.urlencode({"action": "wbgetentities", "ids": ids, "props": "labels|descriptions",
                                    "languages": "ru", "format": "json"})
        ents = get("https://www.wikidata.org/w/api.php?" + q)["entities"]
        for h in hits:
            e = ents.get(h["title"], {})
            out.append(("wd", f"{e.get('labels', {}).get('ru', {}).get('value', '?')} — "
                              f"{e.get('descriptions', {}).get('ru', {}).get('value', '')} ({h['title']})",
                        h["lat"], h["lon"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", default="", help="название; без него — только --nearby")
    ap.add_argument("--nearby", action="store_true",
                    help="показать храмы, памятники и объекты Викидаты вокруг центра поиска")
    ap.add_argument("--near", help="город или село")
    ap.add_argument("--region")
    ap.add_argument("--at", help="центр поиска «широта,долгота», если город в справочнике не тот")
    ap.add_argument("--radius", type=float, default=25)
    a = ap.parse_args()
    coords = g.city_coords()
    for r in json.load(open(os.path.join(g.DATA, "gazetteer.json"), encoding="utf-8")).get("regions", []):
        coords.setdefault(r["n"], (r["lat"], r["lon"]))
    center = tuple(map(float, a.at.split(","))) if a.at else coords.get(a.near) or coords.get(a.region)
    print("центр поиска:", center, "(из справочника)" if not a.at else "")
    rows = []
    # с подписью: «источник не ответил» без имени не говорит, чего в выдаче
    # не хватает — Викидата молчит или Overpass (а это разные выводы)
    sources = [("рядом (Geoapify Places, Викидата)", lambda: nearby(center, a.radius))] if a.nearby and center else []
    if a.name:
        # свои справочники первыми: реестр наследия и выгрузка OSM
        # (tools/local_places.py) отвечают сразу и без лимитов
        sources += [("свои справочники", lambda: [
            (h["src"], h["name"] + (f" [{h['kind']}]" if h.get("kind") else "")
             + (f" {h['addr']}" if h.get("addr") else ""), h["lat"], h["lon"])
            for h in local_places.search(a.name, center, a.radius)] if center else [])]
        where = ", ".join(x for x in (a.near, a.region) if x)
        sources += [("wv — Викигид (списки наследия)", lambda: wikivoyage_rows(a.name, a.near, center, a.radius)),
                    ("wd — Викидата", lambda: wikidata(a.name, center)),
                    ("osm — Overpass", lambda: overpass(a.name, center, a.radius) if center else []),
                    ("ph — Photon (OSM)", lambda: photon(f"{a.name} {a.near or ''}".strip(), center)),
                    ("nom — Nominatim (OSM)", lambda: nominatim(a.name, where)),
                    ("ga — Geoapify", lambda: geoapify(a.name, where, center))]
    notes = []
    for title, fn in sources:
        try:
            got = fn()
            if isinstance(got, tuple):      # ruwiki/Викигид: точки и записи без точки
                got, more = got
                notes += more
            rows += got
        except Exception as e:  # один источник упал — остальные показываем
            print(f"  {title}: не ответил — {str(e)[:120]}")
    if a.name:
        try:
            got, more = ruwiki(a.name, a.near or a.region or "")
            rows += got
            notes += more
        except Exception as e:
            print(f"  wiki — Википедия (поиск по тексту): не ответила — {str(e)[:120]}")
    for src, label, lat, lon in rows:
        d = f"{g.distance_km(center, (lat, lon)):.1f} км" if center else ""
        print(f"{src:4} {lat:.6f}, {lon:.6f}  {d:>9}  {label}")
    # без точки, но с адресом или отрывком текста: адрес — ищи следом
    # `lookup.py "<улица, дом>" --near <город>`
    for n in notes:
        print("     ", n)


def wikivoyage_rows(name, city, center, radius):
    """Списки наследия Викигида (data/wikivoyage_heritage.json): с точкой —
    строкой кандидата, без точки — адресом, который потом ищут геокодером."""
    rows, notes = [], []
    for h in local_places.search_wikivoyage(name, center, radius, city or ""):
        if "lat" in h:
            rows.append(("wv", f"{h['name']} · {h.get('address', '')}", h["lat"], h["lon"]))
        else:
            notes.append(f"wv  «{h['name']}» — адрес: {h.get('address') or '—'} ({h.get('municipality', '')})")
    return rows, notes


if __name__ == "__main__":
    main()
