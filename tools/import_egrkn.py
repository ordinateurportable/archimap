"""Единый реестр объектов культурного наследия (выгрузка Минкультуры) ->
data/egrkn.json — второй справочник наследия для tools/local_places.py.

Выгрузка — с opendata.mkrf.ru (набор 7705851331-egrkn, «data-69-structure-16»),
сайт открывается только с российского адреса, поэтому её скачивает
пользователь: 17 файлов JSON (~1,3 ГБ) в data/egrkn/. Объектов 163 тыс., но
точка на карте (general.address.mapPosition, [долгота, широта]) — только у
~61 тыс.; у остальных разве что адрес, а геокодировать сто тысяч адресов —
месяц лимита Geoapify. Берём объекты с точкой; те, что уже есть в
data/heritage.json (Викидата, тот же номер реестра P1483), не дублируем.

    python tools/import_egrkn.py
"""
import glob, json, os, re

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
SRC = os.path.join(DATA, "egrkn")
OUT = os.path.join(DATA, "egrkn.json")


def point(general):
    mp = ((general.get("address") or {}).get("mapPosition")) or {}
    c = mp.get("coordinates") if isinstance(mp, dict) else None
    if not (isinstance(c, list) and len(c) == 2 and all(isinstance(x, (int, float)) for x in c)):
        return None
    lon, lat = c
    # Россия: широта 41–82, долгота 19–180 (и Чукотка за 180-м меридианом)
    if not (41 <= lat <= 82 and (19 <= lon <= 180 or -180 <= lon <= -168)):
        return None
    return round(lat, 6), round(lon, 6)


def main():
    known = set()
    if os.path.exists(os.path.join(DATA, "heritage.json")):
        known = {r["id"] for r in json.load(open(os.path.join(DATA, "heritage.json"), encoding="utf-8"))}
    out, total, with_point, dup = [], 0, 0, 0
    files = sorted(glob.glob(os.path.join(SRC, "*.json")), key=lambda f: int(re.sub(r"\D", "", os.path.basename(f)) or 0))
    for f in files:
        for r in json.load(open(f, encoding="utf-8")):
            total += 1
            g = (r.get("data") or {}).get("general") or {}
            pt = point(g)
            if not pt:
                continue
            with_point += 1
            reg = str(g.get("regNumber") or "")
            if reg in known:
                dup += 1
                continue
            name = (g.get("name") or r.get("nativeName") or "").strip()
            if not name:
                continue
            region = g.get("region")
            out.append({"q": "egrkn-" + str(r.get("nativeId") or g.get("id")), "id": reg, "name": name, "alt": [],
                        "addr": ((g.get("address") or {}).get("fullAddress") or "")[:200],
                        "region": region.get("value", "") if isinstance(region, dict) else str(region or ""),
                        "lat": pt[0], "lon": pt[1]})
    json.dump(out, open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False)
    print(f"записей {total}, с точкой {with_point}, уже в Викидате {dup}, новых {len(out)} -> {OUT}")


if __name__ == "__main__":
    main()
