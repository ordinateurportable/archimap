# -*- coding: utf-8 -*-
"""Собирает справочник топонимов из GeoNames и списка городов России.

Русские названия берутся строго из alternateNamesV2 по тегу языка ru:
в самом cities*.txt варианты свалены в одну колонку без языка, и оттуда
приезжают чеченские, белорусские и монгольские написания.

На выходе data/gazetteer.json:
  {"ru": [...], "world": [...]}
  запись: {"n": имя, "a": [варианты], "lat", "lon", "cc", "pop"}
"""
import json, os, re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")

RU_NAME = re.compile(r"^[А-ЯЁ][А-Яа-яЁё\- ]{2,}$")


def read_geonames(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 15:
                continue
            yield {
                "gid": p[0],
                "name": p[1],
                "lat": float(p[4]),
                "lon": float(p[5]),
                "fcode": p[7],
                "cc": p[8],
                "pop": int(p[14] or 0),
            }


def read_ru_names(gids):
    """geonameid -> [русские названия], предпочитаемое первым."""
    out = {}
    path = os.path.join(DATA, "alternateNamesV2.txt")
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 4 or p[2] != "ru":
                continue
            gid = p[1]
            if gid not in gids:
                continue
            name = p[3].strip()
            if not RU_NAME.match(name):
                continue
            preferred = len(p) > 4 and p[4] == "1"
            historic = len(p) > 7 and p[7] == "1"
            bucket = out.setdefault(gid, [])
            bucket.append((0 if preferred else (2 if historic else 1), name))
    return {g: [n for _, n in sorted(v)] for g, v in out.items()}


def key(name):
    return name.lower().replace("ё", "е")


def build_regions():
    """Субъекты РФ, привязанные к своему крупнейшему городу.

    «Церковь в Ивановской области» должна попадать в регион, а не в село
    Ивановское, поэтому регионы идут в справочник отдельными записями.
    """
    best = {}
    with open(os.path.join(DATA, "russian-cities.json"), encoding="utf-8") as f:
        for c in json.load(f):
            s = c["subject"]
            pop = c.get("population") or 0
            if s not in best or best[s]["pop"] < pop:
                best[s] = {"n": s, "pop": pop,
                           "lat": round(float(c["coords"]["lat"]), 5),
                           "lon": round(float(c["coords"]["lon"]), 5)}
    return sorted(best.values(), key=lambda x: x["n"])


def build():
    ru_rows = [r for r in read_geonames(os.path.join(DATA, "cities500.txt")) if r["cc"] == "RU"]
    world_rows = [r for r in read_geonames(os.path.join(DATA, "cities15000.txt")) if r["cc"] != "RU"]

    gids = {r["gid"] for r in ru_rows} | {r["gid"] for r in world_rows}
    print("geonames-записей:", len(gids), "— читаю alternateNamesV2 (785 МБ)...")
    names = read_ru_names(gids)
    print("из них с русским названием:", len(names))

    def collect(rows):
        by_key = {}
        for r in rows:
            variants = names.get(r["gid"])
            if not variants:
                continue
            canon = variants[0]
            k = key(canon)
            cur = by_key.get(k)
            if cur and cur["pop"] >= r["pop"]:
                continue
            by_key[k] = {"n": canon, "a": sorted(set(variants[1:])),
                         "lat": round(r["lat"], 5), "lon": round(r["lon"], 5),
                         "cc": r["cc"], "pop": r["pop"]}
        return sorted(by_key.values(), key=lambda x: -x["pop"])

    ru, world = collect(ru_rows), collect(world_rows)

    # официальный список городов РФ — добирает то, чего нет в GeoNames с ru-именем
    have = {key(r["n"]) for r in ru}
    with open(os.path.join(DATA, "russian-cities.json"), encoding="utf-8") as f:
        for c in json.load(f):
            if key(c["name"]) in have:
                continue
            ru.append({"n": c["name"], "a": [],
                       "lat": round(float(c["coords"]["lat"]), 5),
                       "lon": round(float(c["coords"]["lon"]), 5),
                       "cc": "RU", "pop": c.get("population") or 0})
            have.add(key(c["name"]))

    ru.sort(key=lambda x: -x["pop"])
    regions = build_regions()
    with open(os.path.join(DATA, "gazetteer.json"), "w", encoding="utf-8") as f:
        json.dump({"ru": ru, "world": world, "regions": regions}, f, ensure_ascii=False)
    print("РФ:", len(ru), "зарубеж:", len(world), "регионов:", len(regions))
    print("примеры РФ:", [r["n"] for r in ru[:10]])
    print("примеры зарубеж:", [w["n"] for w in world[:10]])


if __name__ == "__main__":
    build()
