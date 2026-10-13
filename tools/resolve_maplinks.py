# -*- coding: utf-8 -*-
"""Раскрыть короткие ссылки на карты из постов: «yandex.ru/maps/-/CCUim…»,
«goo.gl/maps/…», «maps.app.goo.gl/…», страницы организаций Яндекса.

В самой ссылке точки нет — она появляется после переадресации
(«…?whatshere[point]=37.585120,55.678984»). Разбор (classify.py) работает
без сети, поэтому раскрываем здесь, один раз, и кладём в
data/maplinks.json: {ссылка: {"url": куда ведёт, "point": [широта, долгота]}}.
Уже раскрытые не спрашиваем.

    python tools/resolve_maplinks.py
"""
import json, os, re, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify import MAP_URL_RE, map_point, needs_resolving

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(BASE, "data", "maplinks.json")
UA = {"User-Agent": "Mozilla/5.0 (archimap maplinks)"}
# на странице организации Яндекса точка — в данных страницы: первая пара
# «"coordinates":[долгота,широта]» — сама организация
BODY_COORDS_RE = re.compile(r'"coordinates":\[(\d+\.\d+),(\d+\.\d+)\]')


def main():
    posts = json.load(open(os.path.join(BASE, "data", "posts.json"), encoding="utf-8"))
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    urls = sorted({u for p in posts for u in MAP_URL_RE.findall(p["text"]) if needs_resolving(u)})
    todo = [u for u in urls if u not in cache]
    print(f"ссылок на карты, которые нужно раскрыть: {len(urls)}, новых: {len(todo)}")
    for n, u in enumerate(todo, 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25) as r:
                final = r.geturl()
                body = r.read(400_000).decode("utf-8", "replace") if "/org/" in final else ""
        except Exception as e:  # ссылка умерла — запомним, чтобы не спрашивать снова
            cache[u] = {"url": None, "point": None, "error": str(e)[:100]}
            continue
        point = map_point(final)
        if not point and body:
            m = BODY_COORDS_RE.search(body)
            if m:
                point = (float(m.group(2)), float(m.group(1)))
        cache[u] = {"url": final, "point": list(point) if point else None}
        if n % 10 == 0:
            json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"  {n}/{len(todo)}", flush=True)
        time.sleep(1)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("с точкой:", sum(1 for v in cache.values() if v.get("point")), "из", len(cache), "->", CACHE)


if __name__ == "__main__":
    main()
