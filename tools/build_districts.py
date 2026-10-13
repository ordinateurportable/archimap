# -*- coding: utf-8 -*-
"""Собирает районы, микрорайоны и станции метро из OpenStreetMap.

Канал постоянно пишет «в Беляеве» или «на Таганке», а в справочнике
городов таких названий нет — посты уходили в группу «без географии».
Беляево при этом даже не официальный район Москвы, а название станции
метро и исторической местности, поэтому метро тоже идёт в справочник.

Исходники качаются через Overpass API (см. data/overpass_*.txt):
  osm_districts_nodes.json — place=suburb|quarter по всей России
  osm_districts_admin.json — границы районов Москвы и Петербурга
  osm_metro.json           — станции метро

На выходе data/districts.json: [{"n", "lat", "lon", "kind", "parent"}]
"""
import json, math, os, re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")

# «район Чертаново Северное» -> «Чертаново Северное»
PREFIX_RE = re.compile(
    r"^(?:район|округ|муниципальный округ|городской округ|посёлок|поселок|"
    r"поселение|микрорайон|деревня|село)\s+", re.IGNORECASE)

NAME_RE = re.compile(r"^[А-ЯЁ][А-Яа-яЁё0-9\-\s\.]{2,}$")


def load(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        print("нет файла", name, "— пропускаю")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)["elements"]


def coords(el):
    if "lat" in el:
        return el["lat"], el["lon"]
    center = el.get("center")
    return (center["lat"], center["lon"]) if center else (None, None)


def clean(name):
    name = PREFIX_RE.sub("", name).strip()
    return name if NAME_RE.match(name) else None


def nearest_city(lat, lon, cities, max_km=60):
    """Ближайший город — нужен, чтобы понимать, чей это район."""
    best, best_d = None, max_km
    for c in cities:
        dlat = (c["lat"] - lat) * 111.0
        dlon = (c["lon"] - lon) * 111.0 * math.cos(math.radians(lat))
        d = math.hypot(dlat, dlon)
        if d < best_d:
            best, best_d = c, d
    return best["n"] if best else None


def main():
    with open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8") as f:
        cities = [c for c in json.load(f)["ru"] if c["pop"] >= 50000]

    sources = [
        ("osm_districts_nodes.json", "suburb"),
        ("osm_districts_admin.json", "admin"),
        ("osm_metro.json", "metro"),
    ]

    by_name = {}
    for filename, kind in sources:
        for el in load(filename):
            lat, lon = coords(el)
            if lat is None:
                continue
            name = clean(el.get("tags", {}).get("name", ""))
            if not name:
                continue
            key = name.lower().replace("ё", "е")
            if key in by_name:
                continue  # первый источник важнее: границы точнее метро
            by_name[key] = {"n": name, "lat": round(lat, 5), "lon": round(lon, 5),
                            "kind": kind}

    districts = sorted(by_name.values(), key=lambda x: x["n"])
    for d in districts:
        d["parent"] = nearest_city(d["lat"], d["lon"], cities)

    with open(os.path.join(DATA, "districts.json"), "w", encoding="utf-8") as f:
        json.dump(districts, f, ensure_ascii=False)

    kinds = {}
    for d in districts:
        kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
    print("районов и станций:", len(districts), kinds)
    print("с привязкой к городу:", sum(1 for d in districts if d["parent"]))
    for probe in ("Беляево", "Купчино", "Чертаново Северное", "Уралмаш"):
        hit = by_name.get(probe.lower().replace("ё", "е"))
        print("  %-20s %s" % (probe, hit["kind"] if hit else "НЕТ"))


if __name__ == "__main__":
    main()
