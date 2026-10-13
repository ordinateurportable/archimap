# -*- coding: utf-8 -*-
"""Сверка правил разбора с точками, которые поставил или одобрил админ.

Точка админа (ручная или применённая из предложения модели) — правильный
ответ. Для каждого такого поста смотрим, что показал бы на карте сам
разбор (web/public/data — то, что собрал build_web_data.py), и относим
пост к одной из групп:

    точно     — разбор сам поставил точку там же (ближе 300 м)
    частично  — у поста несколько точек админа, разбор нашёл не все
    рядом     — точка разбора в том же городе, но не на месте: город
                верный, а здание или улицу правила не увидели
    неверно   — разбор поставил точку далеко (другой город): хуже всего,
                это нарушает главный принцип «лучше никакой, чем не та»
    пусто     — разбор точки не поставил

Отдельно — «лишние»: точки разбора далеко от точек админа, даже если одна
из них совпала (пост про Тверь, а тег Петербурга остался).

    python tools/check_rules.py [--edits data/edits-stand.json]
        [--save data/rules-check.json] [--compare data/rules-check.json]

--save пишет группы постов, --compare показывает, что поменялось с
прошлого снимка: так проверяем, что новое правило ничего не сломало.
Отчёт — data/rules-check.md.
"""
import argparse, json, math, os, re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
WEB = os.path.join(BASE, "web", "public", "data")
GROUPS_ORDER = ["неверно", "пусто", "рядом", "частично", "точно"]
EXACT_KM = 0.3
CITY_KM = 30


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def map_places(found):
    """Как mapPlaces в web/src/data.js: точные места вытесняют свой город."""
    exact = [p for p in found if p["parent"]]
    if not exact:
        return without_regions(found)
    own = [p for p in exact if p["parent"] == "@"]
    if own:
        return own
    replaced = {p["parent"] for p in exact}
    if "*" in replaced:
        return exact
    return without_regions([p for p in found if p["parent"] or p["name"] not in replaced])


def without_regions(places):
    """Как withoutRegions в data.js: точка области убирается, если город
    поста лежит ближе 600 км от неё (похоже, что в ней)."""
    rest = [p for p in places if not p.get("region")]
    return [p for p in places
            if not p.get("region") or not any(km((p["lat"], p["lon"]), (c["lat"], c["lon"])) <= 600 for c in rest)]


def label(name):
    return name.split(" · ")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edits", default=os.path.join(DATA, "edits.json"))
    ap.add_argument("--save")
    ap.add_argument("--compare")
    args = ap.parse_args()

    meta = json.load(open(os.path.join(WEB, "meta.json"), encoding="utf-8"))
    places = [{"name": p[0], "scope": p[1], "lat": p[2], "lon": p[3], "parent": p[5] if len(p) > 5 else None,
               "region": len(p) > 6 and bool(p[6])}
              for p in meta["places"]]
    posts = {}
    for g in ("ru", "foreign", "none"):
        path = os.path.join(WEB, f"{g}.json")
        if os.path.exists(path):
            for p in json.load(open(path, encoding="utf-8")):
                posts[f"{p['src']}-{p['i']}"] = p

    e = json.load(open(args.edits, encoding="utf-8"))
    by_key = {s["key"]: s for s in e.get("suggestions", []) if s.get("from") == "model"}
    rows = []
    for key, edit in e.get("edits", {}).items():
        if not edit.get("location") or key not in posts:
            continue
        gold = [edit["location"], *edit.get("extra", [])]
        sug = by_key.get(key)
        source = "модель" if sug and sug.get("lat") == gold[0]["lat"] and sug.get("lon") == gold[0]["lon"] else "вручную"
        post = posts[key]
        # теги, которые админ убрал, разбор всё равно поставил бы — считаем их
        found = [places[i] for i in post["g"] if i < len(places)]
        shown = map_places(found)
        pts = [(p["lat"], p["lon"]) for p in shown]
        near = [min((km((g["lat"], g["lon"]), q) for q in pts), default=math.inf) for g in gold]
        # нужна точка на здании («Манеж · Нижний Новгород»), а у разбора только
        # город, чей центр случайно в двухстах метрах, — это не совпадение
        # а если нужен сам город («Черноголовка»), а разбор нашёл в нём
        # объекты поста, — это тоже совпадение, даже точнее
        def hits(g):
            want_exact = " · " in (g.get("name") or "")
            return any(km((g["lat"], g["lon"]), (p["lat"], p["lon"])) <= EXACT_KM and (p["parent"] or not want_exact)
                       or (not want_exact and p["parent"] and p["parent"] == g.get("name"))
                       for p in shown)
        hit = sum(hits(g) for g in gold)
        if not pts:
            group = "пусто"
        elif hit == len(gold):
            group = "точно"
        elif hit:
            group = "частично"
        elif min(near) <= CITY_KM:
            group = "рядом"
        else:
            group = "неверно"
        extra = [p["name"] for p in shown
                 if min(km((p["lat"], p["lon"]), (g["lat"], g["lon"])) for g in gold) > CITY_KM]
        rows.append({"key": key, "group": group, "source": source, "gold": [g.get("name") or "" for g in gold],
                     "rules": [p["name"] for p in shown], "extra": extra, "km": round(min(near), 1),
                     "text": post["t"][:300].replace("\n", " ")})

    # справочник: одобренные названия, которые встречаются и в других постах
    texts = {k: p["t"].lower() for k, p in posts.items()}
    for r in rows:
        names = [label(n) for n in r["gold"] if n and len(label(n)) >= 6]
        r["elsewhere"] = {}
        for n in names:
            # только само название, без «улица»: «Дом Губина», «Манеж в Нижегородском кремле»
            needle = re.sub(r"[«»\"]", "", n.lower())
            cnt = sum(1 for k, t in texts.items() if k != r["key"] and needle in re.sub(r"[«»\"]", "", t))
            if cnt:
                r["elsewhere"][n] = cnt

    rows.sort(key=lambda r: (GROUPS_ORDER.index(r["group"]), r["key"]))
    counts = {g: sum(r["group"] == g for r in rows) for g in GROUPS_ORDER}
    print(f"постов с точкой админа: {len(rows)}")
    for g in GROUPS_ORDER:
        print(f"  {g:9} {counts[g]}")
    print("  с лишними точками:", sum(bool(r["extra"]) for r in rows))

    if args.compare and os.path.exists(args.compare):
        prev = json.load(open(args.compare, encoding="utf-8"))
        changed = [(r["key"], prev.get(r["key"]), r["group"]) for r in rows if prev.get(r["key"]) not in (None, r["group"])]
        worse = [c for c in changed if GROUPS_ORDER.index(c[2]) < GROUPS_ORDER.index(c[1])]
        print(f"изменилось с прошлого снимка: {len(changed)}, из них хуже: {len(worse)}")
        for k, a, b in changed:
            print(f"  {k}: {a} → {b}{'   ← ХУЖЕ' if (k, a, b) in worse else ''}")
    if args.save:
        json.dump({r["key"]: r["group"] for r in rows}, open(args.save, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    out = ["# Сверка правил с точками админа", "",
           " · ".join(f"{g}: {counts[g]}" for g in GROUPS_ORDER), ""]
    for g in GROUPS_ORDER:
        part = [r for r in rows if r["group"] == g]
        if not part:
            continue
        out += [f"## {g} ({len(part)})", ""]
        for r in part:
            out.append(f"- **{r['key']}** ({r['source']}) — нужно: {'; '.join(r['gold']) or '—'}")
            out.append(f"  - разбор: {'; '.join(r['rules']) or '—'}" + (f" (до точки админа {r['km']} км)" if r['km'] != math.inf else ""))
            if r["extra"]:
                out.append(f"  - лишние: {'; '.join(r['extra'])}")
            if r["elsewhere"]:
                out.append("  - встречается и в других постах: " + ", ".join(f"«{n}» — {c}" for n, c in r["elsewhere"].items()))
            out.append(f"  - {r['text']}")
        out.append("")
    open(os.path.join(DATA, "rules-check.md"), "w", encoding="utf-8").write("\n".join(out))
    print("отчёт:", os.path.join(DATA, "rules-check.md"))


if __name__ == "__main__":
    main()
