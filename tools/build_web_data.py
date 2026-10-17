# -*- coding: utf-8 -*-
"""Готовит компактные файлы для фронтенда.

Целиком posts_classified.json — это 27 МБ, и грузить их в браузер разом
незачем. Здесь данные режутся по группам и ужимаются: места выносятся в
отдельный словарь, у фотографий остаются только имена файлов.

На выходе web/public/data/{ru,foreign,none}.json и meta.json.
"""
import json, math, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sources import SOURCES

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "web", "public", "data")
MAX_PHOTOS = 6


# Рекламные посты: маркировка erid (в тексте или в ссылке) либо строка
# «Реклама. Рекламодатель …» / «Реклама. ООО …, ИНН …». На сайте такие
# посты скрыты для всех и лежат во вкладке «Скрытые» с пометкой.
ERID_RE = re.compile(r"(?i)(?<![a-z])erid(?![a-z])")
AD_LINE_RE = re.compile(r"(?i)реклама[.:]\s*[^\n]{0,80}(?:рекламодатель|инн)")


def is_ad(text):
    return bool(ERID_RE.search(text) or AD_LINE_RE.search(text))


def refine(pl):
    """Ориентир («Дворцовая площадь») и район («Басманный») точнее своего
    города — его вычисляет classify.py в поле city. Помечаем их точными,
    чтобы на карте поста они заменили город."""
    if pl.get("city") and not pl.get("exact"):
        return dict(pl, exact=True, parent=pl["city"])
    return pl


def main():
    with open(os.path.join(DATA, "posts_classified.json"), encoding="utf-8") as f:
        posts = json.load(f)

    places, place_id = [], {}

    # Области, края и республики — по справочнику регионов и по слову в
    # названии («Подмосковье» тоже регион). Седьмым полем 1: на карте точка
    # области не ставится, если у поста есть город — «Севск Брянской
    # области» стоял двумя точками, в Севске и в Брянске
    gaz = json.load(open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8"))
    # Москва, Петербург и Севастополь — и регионы, и города: это города
    city_names = {c["n"] for c in gaz.get("ru", [])}
    region_names = ({r["n"] for r in gaz.get("regions", [])} - city_names) | {"Подмосковье"}

    def is_region(p):
        return p["scope"] == "ru" and not p.get("exact") and (
            p["name"] in region_names
            or any(w in p["name"].split() for w in ("область", "край", "республика", "Республика", "округ")))

    # Тег «еда» ставит модель при разборе (поле food в ответе, копится в
    # llm_places.json): «Кулич и Пасха», конфеты вологодской фабрики. Посты,
    # которые модель не разбирала, тега не получают
    store_path = os.path.join(DATA, "llm_places.json")
    llm = json.load(open(store_path, encoding="utf-8")) if os.path.exists(store_path) else {}
    food = {k for k, a in llm.items() if a.get("food") is True}

    # Точные точки — улицы и объекты с координатами геокодера, координаты
    # из текста поста, ориентиры и районы — несут шестым полем город,
    # который они уточняют («*» — любой): на карте поста вместо этого
    # города будет стоять только точная точка.
    def place_index(p):
        exact = p.get("exact")
        pid = (p["name"], p["lat"], p["lon"]) if exact else p["name"]
        if pid not in place_id:
            place_id[pid] = len(places)
            row = [p["name"], p["scope"], p["lat"], p["lon"], p["cc"]]
            if exact:
                # «@» — точка, которую дал сам автор (координаты, ссылка на
                # карту): на карте поста она вытесняет все остальные места
                row.append("@" if p.get("own") else p.get("parent") or "*")
            elif is_region(p):
                row += [None, 1]
            places.append(row)
        return place_id[pid]

    # Вкладку поста решает его точная точка — ручная админа или точка модели
    # (10.10: на карте «Россия» стоял Николаевский костёл в Киеве, Прага,
    # Тбилиси, Андижан — разбор отнёс посты к России по случайному
    # российскому названию, а точку модели карта ставит поверх). Страна
    # точки — по ближайшему городу справочника: российские города (вместе с
    # Крымом) против остального мира.
    # правки — локальные вместе со стендовыми (tools/edits_store.py): точка,
    # поставленная на стенде за границей, тоже переносит пост в «Другие страны»
    from edits_store import load_edits
    manual = load_edits().get("edits", {})
    buckets = {}
    for c in gaz["ru"]:
        buckets.setdefault((round(c["lat"]), round(c["lon"])), []).append((c["lat"], c["lon"], True))
    for c in gaz.get("world", []):
        if c.get("cc") != "RU":
            buckets.setdefault((round(c["lat"]), round(c["lon"])), []).append((c["lat"], c["lon"], False))

    def in_russia(lat, lon):
        k = math.cos(math.radians(lat))
        near = {True: None, False: None}
        for dlat in (-1, 0, 1):
            for dlon in (-2, -1, 0, 1, 2):
                for clat, clon, ru in buckets.get((round(lat) + dlat, round(lon) + dlon), ()):
                    d = math.hypot(clat - lat, (clon - lon) * k) * 111
                    if near[ru] is None or d < near[ru]:
                        near[ru] = d
        if near[True] is None and near[False] is None:
            return None
        # Ялта, Севастополь есть в справочнике и российскими, и в «мире» с
        # cc UA — одна точка на двух записях; как и разбор, считаем Россией
        if near[True] is not None and (near[False] is None or near[True] <= near[False] + 2):
            return True
        return False

    def point_group(key, group):
        loc = (manual.get(key) or {}).get("location")
        if not loc:
            a = llm.get(key) or {}
            # точная точка модели — с « · » в подписи; «просто город» не в счёт
            loc = a.get("point") if " · " in (a.get("label") or "") and not (a.get("ad") or "").strip() else None
        if not loc:
            return group
        ru = in_russia(loc["lat"], loc["lon"])
        if ru is None:
            return group
        return "ru" if ru else "foreign"

    moved = Counter()
    groups = {"ru": [], "foreign": [], "none": []}
    for p in posts:
        row = {
            "i": p["id"],
            "src": p["src"],
            "d": p["date"][:16].replace("T", " "),
            "t": p["text"],
            "p": p["photos"][:MAX_PHOTOS],
            "np": len(p["photos"]),
            "g": [place_index(refine(x)) for x in p["places"]],
            "s": p["streets"],
            "c": p["countries"],
            "r": p["reactions"],
        }
        if is_ad(p["text"]):
            row["ad"] = 1
        if f"{p['src']}-{p['id']}" in food:
            row["f"] = 1
        group = point_group(f"{p['src']}-{p['id']}", p["group"])
        if group != p["group"]:
            moved[(p["group"], group)] += 1
        groups[group].append(row)

    os.makedirs(OUT, exist_ok=True)
    meta = {
        "places": places,
        "counts": {},
        "total": len(posts),
        # фронтенд собирает из этого ссылки на посты и подписи каналов
        "sources": {s["key"]: {"title": s["title"], "channel": s["channel"],
                               "short": s.get("short", s["title"])}
                    for s in SOURCES},
        "bySource": dict(Counter(p["src"] for p in posts)),
        "ads": sum(1 for p in posts if is_ad(p["text"])),
        "food": len(food),
    }
    for name, rows in groups.items():
        path = os.path.join(OUT, name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
        # реклама на сайте скрыта — в счётчиках вкладок её нет
        meta["counts"][name] = sum(1 for r in rows if not r.get("ad"))
        print("%-8s %6d постов  %6.1f МБ" % (name, len(rows), os.path.getsize(path) / 1e6))

    # самые частые места — для быстрых фильтров в интерфейсе
    freq = Counter()
    for rows in groups.values():
        for r in rows:
            for g in r["g"]:
                freq[g] += 1
    meta["top"] = [[i, n] for i, n in freq.most_common(60)]

    with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, separators=(",", ":"))
    exact = sum(1 for row in places if len(row) > 5)
    if moved:
        print("вкладку поменяла точная точка:", ", ".join(f"{a} → {b}: {n}" for (a, b), n in moved.items()))
    print("мест в словаре:", len(places), "| из них точных:", exact, "->", OUT)
    # отчёты о кругах разбора (data/reports.json, их дописывает сессия Claude
    # после каждого круга) — для вкладки «Отчёты» в модерации
    reports = os.path.join(BASE, "data", "reports.json")
    if os.path.exists(reports):
        with open(reports, encoding="utf-8") as f:
            items = json.load(f)
        with open(os.path.join(OUT, "reports.json"), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
