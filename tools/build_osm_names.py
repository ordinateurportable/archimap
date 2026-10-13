"""Именованные объекты OpenStreetMap -> data/osm/names.sqlite (для local_places).

Overpass неделями не отвечал, поэтому OSM храним у себя: из выгрузки
Geofabrik (data/osm/russia.osm.pbf, ~4 ГБ) берём всё, что названо и похоже на
место — музеи, театры, ДК, храмы, памятники, станции, здания с названием.
Улицы не берём: их ищет геокодер. Точка линии или мультиполигона — среднее
нескольких его вершин: для здания этого хватает с запасом.

Отбор делает osmium в C++ (KeyFilter, IdFilter), Python видит только
нужное. Проходы: отношения -> линии -> точки с именем -> вершины линий.

    python tools/build_osm_names.py [data/osm/russia.osm.pbf]
"""
import os, sqlite3, sys, time

import osmium
import osmium.filter as F

sys.path.insert(0, os.path.dirname(__file__))
import local_places

DATA = local_places.DATA
PBF = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA, "osm", "russia.osm.pbf")
OUT = local_places.OSM_DB

SKIP_AMENITY = {"parking", "parking_space", "bench", "waste_basket", "toilets", "atm",
                "vending_machine", "bicycle_parking", "parking_entrance", "recycling",
                "waste_disposal", "drinking_water", "shelter", "post_box", "telephone",
                "charging_station", "fuel", "car_wash", "pharmacy", "bank", "fast_food",
                "cafe", "restaurant", "bar", "pub", "dentist", "clinic", "doctors"}
KEEP_SHOP = {"mall", "department_store"}


def kind(tags):
    """Тип объекта, если он похож на место из постов; иначе None."""
    if "highway" in tags and "building" not in tags:
        return None
    for k in ("tourism", "historic", "leisure", "man_made", "aeroway"):
        if k in tags:
            return f"{k}={tags[k]}"
    a = tags.get("amenity")
    if a and a not in SKIP_AMENITY:
        return f"amenity={a}"
    if tags.get("railway") in ("station", "halt") or tags.get("public_transport") == "station":
        return "station"
    if tags.get("shop") in KEEP_SHOP:
        return f"shop={tags['shop']}"
    if "building" in tags:
        return f"building={tags['building']}"
    if tags.get("place") in ("square", "quarter", "neighbourhood", "island", "islet"):
        return f"place={tags['place']}"
    return None


def name_of(tags):
    return tags.get("name:ru") or tags.get("name")


def sample(refs, n=6):
    """Несколько вершин по контуру — их среднее и есть точка."""
    if len(refs) <= n:
        return list(refs)
    step = len(refs) / n
    return [refs[int(i * step)] for i in range(n)]


def main():
    t0 = time.time()
    # 1. отношения-мультиполигоны с именем: их внешние линии
    rel_ways = {}  # way id -> индекс записи
    records = []   # [name, kind, osm id, [node ids] | (lat, lon)]
    for r in osmium.FileProcessor(PBF, osmium.osm.RELATION).with_filter(F.KeyFilter("name")):
        tags = {t.k: t.v for t in r.tags}
        if tags.get("type") not in ("multipolygon", "building"):
            continue
        k, nm = kind(tags), name_of(tags)
        if not k or not nm:
            continue
        outer = [m.ref for m in r.members if m.type == "w" and m.role in ("outer", "")]
        if outer:
            records.append([nm, k, f"r{r.id}", []])
            rel_ways[outer[0]] = len(records) - 1
    print(f"отношений: {len(rel_ways)}  ({time.time() - t0:.0f} с)", flush=True)

    # 2. линии с именем — и внешние линии отношений из шага 1
    need_nodes = set()
    for w in osmium.FileProcessor(PBF, osmium.osm.WAY).with_filter(F.KeyFilter("name")):
        tags = {t.k: t.v for t in w.tags}
        k, nm = kind(tags), name_of(tags)
        refs = [n.ref for n in w.nodes]
        if w.id in rel_ways:
            records[rel_ways.pop(w.id)][3] = sample(refs)
        if k and nm and refs:
            pick = sample(refs)
            records.append([nm, k, f"w{w.id}", pick])
    if rel_ways:
        for w in osmium.FileProcessor(PBF, osmium.osm.WAY).with_filter(F.IdFilter(list(rel_ways))):
            records[rel_ways[w.id]][3] = sample([n.ref for n in w.nodes])
    for rec in records:
        need_nodes.update(rec[3])
    print(f"линий и отношений: {len(records)}, вершин нужно: {len(need_nodes)}  ({time.time() - t0:.0f} с)", flush=True)

    # 3. точки с именем
    for n in osmium.FileProcessor(PBF, osmium.osm.NODE).with_filter(F.KeyFilter("name")):
        tags = {t.k: t.v for t in n.tags}
        k, nm = kind(tags), name_of(tags)
        if k and nm and n.location.valid():
            records.append([nm, k, f"n{n.id}", (n.location.lat, n.location.lon)])
    print(f"всего записей: {len(records)}  ({time.time() - t0:.0f} с)", flush=True)

    # 4. координаты вершин линий
    loc = {}
    for n in osmium.FileProcessor(PBF, osmium.osm.NODE).with_filter(F.IdFilter(list(need_nodes))):
        if n.location.valid():
            loc[n.id] = (n.location.lat, n.location.lon)
    print(f"вершин найдено: {len(loc)}  ({time.time() - t0:.0f} с)", flush=True)

    tmp = OUT + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.execute("CREATE VIRTUAL TABLE names USING fts5(name, lemmas, lat UNINDEXED, lon UNINDEXED, "
               "kind UNINDEXED, osm UNINDEXED)")
    rows = []
    for nm, k, osm, pts in records:
        if isinstance(pts, tuple):
            lat, lon = pts
        else:
            got = [loc[i] for i in pts if i in loc]
            if not got:
                continue
            lat = sum(p[0] for p in got) / len(got)
            lon = sum(p[1] for p in got) / len(got)
        rows.append((nm, " ".join(local_places.lemmas(nm)), round(lat, 6), round(lon, 6), k, osm))
        if len(rows) >= 50_000:
            db.executemany("INSERT INTO names VALUES (?,?,?,?,?,?)", rows)
            rows = []
    db.executemany("INSERT INTO names VALUES (?,?,?,?,?,?)", rows)
    db.commit()
    count = db.execute("SELECT count(*) FROM names").fetchone()[0]
    db.close()
    os.replace(tmp, OUT)
    print(f"в базе {count} мест -> {OUT}  ({time.time() - t0:.0f} с)")


if __name__ == "__main__":
    main()
