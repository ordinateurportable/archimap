# -*- coding: utf-8 -*-
"""Координаты улиц: «Шишмаревский переулок» + «Санкт-Петербург» -> точка.

Разбор (classify.py) находит улицы, но координат у них нет — только у
городов. Здесь пары «город + улица» из data/posts_classified.json
отправляются в геокодер Photon (photon.komoot.io, данные OpenStreetMap),
а ответы копятся в data/street_geocode.json. Кэш переживает повторные
запуски: спрашиваем только то, чего в нём ещё нет, поэтому прерванный
прогон можно просто запустить снова.

Геокодеров два. Если рядом лежит файл tools/.geocoder-key с ключом
Geoapify (geoapify.com, Geocoding API), запросы идут туда: бесплатного
тарифа хватает на несколько тысяч запросов в день. Без ключа — в Photon:
он бесплатен и без регистрации, но на тысячах запросов подряд начинает
отказывать (403), поэтому запросы к нему идут раз в две секунды. Оба
работают на данных OpenStreetMap. На отказ (403, 429) скрипт пять минут
ждёт и пробует снова. Файл с ключом в git не попадает (.gitignore).

Ответ принимается, только если название улицы совпало (без учёта «ё»
и регистра) и точка лежит в названном городе или рядом с ним: «улица
Ленина» есть почти везде, и без проверки она уехала бы в соседний посёлок.

Запуск: python tools/geocode_streets.py, затем classify.py и build_web_data.py.
  --retry-missing      спросить заново пары, которые раньше не нашлись
  --max-requests N     не больше N запросов за запуск (по умолчанию 2500)
"""
import json, math, os, re, sys, time, urllib.error, urllib.parse, urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
CACHE = os.path.join(DATA, "street_geocode.json")
URL = "https://photon.komoot.io/api/"
GEOAPIFY_URL = "https://api.geoapify.com/v1/geocode/search"
KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".geocoder-key")
GEOAPIFY_KEY = (open(KEY_FILE, encoding="utf-8").read().strip()
                if os.path.exists(KEY_FILE) else "")
UA = "archimap/0.1 (+https://archimap.194.226.142.99.sslip.io)"
# секунд между запросами. При 1.1 секунды Photon на втором часу работы
# начал отвечать 403 — две секунды держит без отказов
# Geoapify разрешает несколько запросов в секунду — берём с запасом
PAUSE = 0.4 if GEOAPIFY_KEY else 2.0
BACKOFF = 300  # сколько ждать после отказа, прежде чем спросить снова
NET_PAUSE = 20  # пауза после обрыва соединения
RETRIES = 3
MAX_CITIES = 2  # сколько городов поста пробуем для одной улицы

# регионы — не города: «улица Ленина» в Рязанской области ничего не значит
REGION_WORDS = ("область", "край", "республика", "округ", "губерния", "Россия")


def norm(s):
    # «Лахта-центр» и «Лахта Центр» — одно и то же
    s = (s or "").lower().replace("ё", "е").replace("-", " ")
    s = " ".join(s.replace("«", "").replace("»", "").replace('"', "").split())
    # «ДК „Невский“» в тексте — «Дом Культуры „Невский“» в OSM (под «ДК
    # Невский» там только остановки); «церковь Петра и Павла» — «Лютеранская
    # церковь Святых Петра и Павла»: слово «святых» перед именами не мешает
    s = re.sub(r"\b(?:дом|дворец) культуры\b", "дк", s)
    s = re.sub(r"\b(?:святых|святого|святителя|св\.?)\s+(?=[а-я])", "", s)
    return s


def key(city, street):
    return f"{city}|{street}"


def distance_km(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    k = math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(lat2 - lat1, (lon2 - lon1) * k) * 111


def radius_km(city):
    # у Москвы и Петербурга улицы разбегаются на десятки километров
    return 45 if city in ("Москва", "Санкт-Петербург") else 20


def is_city(place):
    return place["scope"] == "ru" and not any(w in place["name"] for w in REGION_WORDS)


LOCALITY_PREFIXES = ("пос. ", "с. ", "дер. ", "х. ", "ст-ца ", "мкр ", "о. ", "н. п. ")


REGION_KIND_RE = re.compile(r"\b(?:республика|область|край|автономный округ|автономная область|ао)\b")


TWIN_REGIONS = [{"москва", "московская"}, {"санкт", "петербург", "ленинградская"}, {"севастополь", "крым"}]


def same_region(state, region):
    """«Республика Мордовия» — это «Мордовия», «Чувашская Республика» —
    «Чувашия», «Республика Саха (Якутия)» — «Якутия». Сравнение дословно
    не находило Краснослободск в Мордовии, и точка уезжала в волгоградский."""
    a = REGION_KIND_RE.sub(" ", norm(state)).replace("(", " ").replace(")", " ").split()
    b = REGION_KIND_RE.sub(" ", norm(region)).replace("(", " ").replace(")", " ").split()
    if not a or not b:
        return False
    if a == b or set(b) <= set(a):
        return True
    # «Подмосковье» бывает юридически Москвой: Сосенки, Коммунарка — Новая
    # Москва (16.10, «деревня Сосенки в Подмосковье» не находилась в
    # Московской области); так же Пушкин и Петергоф — Петербург, а не
    # Ленобласть, Севастополь — не Республика Крым
    if any(set(a) <= pair and set(b) <= pair and a != b for pair in TWIN_REGIONS):
        return True
    # «чувашская» и «чувашия», «удмуртская» и «удмуртия» — общее начало
    return len(a) == len(b) == 1 and a[0][:5] == b[0][:5]


def is_street(tag):
    """В списке улиц лежат и местности («пос. Остров») — их не геокодируем."""
    return not tag.startswith(LOCALITY_PREFIXES) and not tag.endswith(" слобода")


def city_coords():
    with open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8") as f:
        raw = json.load(f)
    out = {c["n"]: (c["lat"], c["lon"]) for c in raw["ru"]}
    # село Крым под Ростовом (4,5 тыс. жителей) перебивало республику: всё
    # «в Крыму» искалось у Ростова-на-Дону (16.10, «Дружба · Крым» встала там)
    for r in raw.get("regions", []):
        if r["n"] in out and distance_km(out[r["n"]], (r["lat"], r["lon"])) > 50:
            out[r["n"]] = (r["lat"], r["lon"])
    return out


_region_names = None
# города-регионы — это города: в них ищем как в городе
CITY_REGIONS = {"Москва", "Санкт-Петербург", "Севастополь"}


def is_region(name):
    """«Московская область», «Республика Карелия» — по словам; «Крым»,
    «Татарстан», «Алтай» — по справочнику регионов: раньше их искали как
    город, в радиусе в пару десятков километров от центра."""
    global _region_names
    if any(w in (name or "") for w in REGION_WORDS[:-1]):
        return True
    if _region_names is None:
        with open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8") as f:
            _region_names = {r["n"] for r in json.load(f).get("regions", [])} - CITY_REGIONS
    return name in _region_names


_regions = {}
# область из текста поста проверяем всегда; область, которую модель
# додумала сама («Оптина Пустынь · Тульская область» — она в Калужской), —
# нет: точка верная, ошибся ответ (llm_places.locate выключает проверку)
REGION_CHECK = True


def region_ok(region, point):
    """Лежит ли точка в названной области. Границ у нас нет, только центры:
    точка должна быть ближе к центру своей области, чем к соседним (с запасом
    на большие регионы), или совсем рядом с ним. «Усадьба Костино · Рязанская
    область»: Викидата нашла усадьбу Костино в Королёве, в 200 км, — она
    ближе к Москве, чем к Рязани (жалоба 08.10)."""
    if not _regions:
        with open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8") as f:
            _regions.update({r["n"]: (r["lat"], r["lon"]) for r in json.load(f).get("regions", [])})
    own = _regions.get(region)
    if not own:
        return True
    d_own = distance_km(own, point)
    if d_own <= 100:
        return True
    nearest = min(distance_km(c, point) for c in _regions.values())
    if d_own <= nearest * 1.05:
        return True
    # центры обманывают у вытянутых областей (Пугачёв ближе к Самаре, чем к
    # Саратову; восток Ленобласти — к Петрозаводску): спросим, где точка
    state = reverse_state(point)
    if state is None:
        return d_own <= nearest * 1.25
    return same_region(state, region)


REVERSE_CACHE = os.path.join(DATA, "reverse_state.json")
_reverse = None


def reverse_state(point):
    """Область точки по обратному геокодированию Geoapify (с кэшем на диске)."""
    global _reverse
    if not GEOAPIFY_KEY:
        return None
    if _reverse is None:
        _reverse = json.load(open(REVERSE_CACHE, encoding="utf-8")) if os.path.exists(REVERSE_CACHE) else {}
    k = f"{point[0]:.3f},{point[1]:.3f}"
    if k not in _reverse:
        try:
            data = get_json("https://api.geoapify.com/v1/geocode/reverse",
                            {"lat": point[0], "lon": point[1], "lang": "ru", "apiKey": GEOAPIFY_KEY})
        except Exception:
            return None
        feats = data.get("features") or []
        _reverse[k] = (feats[0]["properties"].get("state") or "") if feats else ""
        json.dump(_reverse, open(REVERSE_CACHE, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=0)
    return _reverse[k] or None


_population = {}


def population(city):
    if not _population:
        with open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8") as f:
            _population.update((c["n"], c["pop"]) for c in json.load(f)["ru"])
    return _population.get(city, 0)


# Храмы и монастыри, названные по посвящению: «Троицкий собор», «Никольская
# церковь», «Свято-Троицкая церковь». В небольшом городе такой храм обычно
# один, а в миллионнике и тем более в области их десятки, и геокодер
# отдаёт первый попавшийся — пост про «Кулич и Пасху» встал бы к другой
# Троицкой церкви. Там такие объекты не ищем.
DEDICATION_RE = re.compile(
    r"^(?:Свято-)?(?:Троицк|Успенск|Никольск|Воскресенск|Покровск|Преображенск|"
    r"Благовещенск|Казанск|Спасск|Рождественск|Вознесенск|Ильинск|Богоявленск|"
    r"Георгиевск|Петропавловск|Введенск|Сретенск|Знаменск|Михайловск|Смоленск|"
    r"Владимирск|Тихвинск|Иверск|Архангельск|Борисоглебск|Всехсвятск|Никитск|"
    r"Пятницк|Дмитриевск|Александро-Невск|Петров|Покрово|Спасо|Троице)\w*[ -]"
    r"(?:собор|церковь|храм|монастырь|часовня|колокольня)$")
BIG_CITY = 1_000_000


def ambiguous_dedication(city, item):
    if not item.startswith("obj:") or not DEDICATION_RE.search(item[4:]):
        return False
    in_region = is_region(city)
    return in_region or population(city) >= BIG_CITY or population(city) == 0


def collect_pairs(posts):
    """Пары (город, улица) и (город, «obj:объект»).

    Города берём из поля cities, которое готовит classify.py: туда входят
    и города, названные прилагательным, и города ориентиров и районов."""
    coords = city_coords()
    pairs = set()
    for p in posts:
        cities = [c for c in p.get("cities", []) if c in coords][:MAX_CITIES]
        regions = p.get("regions", {})
        for name, center in regions.items():
            coords.setdefault(name, tuple(center))
        for tag in p["streets"]:
            if is_street(tag):
                for city in cities:
                    pairs.add((city, tag))
                continue
            # местность ищем по названию без типа («дер. Черная Грязь») —
            # рядом с городом поста или в названном регионе
            import classify
            item = "loc:" + classify.locality_name(tag)
            for where in cities + list(regions):
                pairs.add((where, item))
        for obj in p.get("objects", []):
            for where in cities + list(regions):
                pairs.add((where, "obj:" + obj))
    return pairs, coords


FOOD = {"cafe", "restaurant", "fast_food", "bar", "pub", "food_court", "canteen"}
NAMED_AFTER = {"столовая", "кафе", "ресторан", "бар", "магазин", "остановка", "кофейня",
               "пиццерия", "гостиница", "отель", "хостел", "аптека", "салон", "клуб"}

# у населённых пунктов берём самый крупный из подходящих: в Нижегородской
# области пять Починок, и только одна — посёлок городского типа
PLACE_RANK = {"city": 5, "town": 4, "village": 3, "hamlet": 2}


def photon(params):
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": UA})
    global REQUESTS
    for attempt in range(RETRIES + 1):
        REQUESTS += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r).get("features", [])
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            # обрыв соединения или таймаут — разовый сбой сети: ждём и повторяем
            if not isinstance(e, urllib.error.HTTPError):
                if attempt == RETRIES:
                    raise
                print(f"  сеть: {e}, повтор через {NET_PAUSE} с", file=sys.stderr, flush=True)
                time.sleep(NET_PAUSE)
                continue
            # 403 и 429 — сервис просит притормозить: ждём и пробуем снова
            if e.code not in (403, 429) or attempt == RETRIES:
                raise
            print(f"  Photon ответил {e.code}, ждём {BACKOFF // 60} мин", file=sys.stderr, flush=True)
            time.sleep(BACKOFF)


# Ответ Geoapify приводим к виду Photon, чтобы проверки в ask() были общими
GEOAPIFY_PLACE = {"city", "suburb", "district", "locality", "postcode"}


def geoapify(text, center, kind, limit, bias=True):
    params = {"text": text, "lang": "ru", "limit": limit, "format": "json",
              "filter": "countrycode:ru", "apiKey": GEOAPIFY_KEY}
    # привязка к точке помогает внутри города, но в области мешает: из пяти
    # Починок Нижегородской области она выбирала ближайшую к центру деревню
    if bias:
        params["bias"] = f"proximity:{center[1]},{center[0]}"
    if kind == "street":
        params["type"] = "street"
    req = urllib.request.Request(GEOAPIFY_URL + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": UA})
    global REQUESTS
    for attempt in range(RETRIES + 1):
        REQUESTS += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                results = json.load(r).get("results", [])
            break
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            # обрыв соединения или таймаут — разовый сбой сети: ждём и повторяем
            if not isinstance(e, urllib.error.HTTPError):
                if attempt == RETRIES:
                    raise
                print(f"  сеть: {e}, повтор через {NET_PAUSE} с", file=sys.stderr, flush=True)
                time.sleep(NET_PAUSE)
                continue
            if e.code not in (403, 429) or attempt == RETRIES:
                raise
            print(f"  Geoapify ответил {e.code}, ждём {BACKOFF // 60} мин", file=sys.stderr, flush=True)
            time.sleep(BACKOFF)
    out = []
    for r in results:
        rtype, cat = r.get("result_type"), r.get("category") or ""
        if rtype == "street":
            osm_key, osm_value = "highway", "street"
        elif rtype in GEOAPIFY_PLACE:
            # настоящий населённый пункт — «populated_place», урочища и
            # районы идут ниже при выборе из одноимённых
            osm_key, osm_value = "place", "village" if cat == "populated_place" else "locality"
        elif cat.startswith("catering"):
            osm_key, osm_value = "amenity", "cafe"
        elif cat.startswith("commercial"):
            osm_key, osm_value = "shop", cat
        elif cat.startswith("public_transport"):
            # остановка «Лахта Центр» — не сам небоскрёб
            osm_key, osm_value = "highway", "bus_stop"
        else:
            osm_key, osm_value = rtype or "amenity", cat
        if kind == "street" and osm_key != "highway":
            continue
        if kind == "loc" and osm_key != "place":
            continue
        out.append({"properties": {"name": r.get("name"), "city": r.get("city"),
                                   "state": r.get("state"), "osm_key": osm_key,
                                   "osm_value": osm_value, "street": r.get("street"),
                                   "housenumber": r.get("housenumber")},
                    "geometry": {"coordinates": [r["lon"], r["lat"]]}})
    return out


def yo_variants(name, limit=3):
    """«Черная Грязь» -> «Чёрная Грязь»: в текстах «ё» почти не пишут, а
    поиск Photon по сёлам без неё иногда промахивается."""
    out = []
    for i, ch in enumerate(name):
        if ch in "еЕ":
            out.append(name[:i] + ("ё" if ch == "е" else "Ё") + name[i + 1:])
    return out[:limit]


ADDRESS_RE = re.compile(r"^(?P<street>.+), (?P<num>\d{1,4})(?P<rest>[А-Я]?(?:/\d+)?)$")


def ask_address(city, street, center):
    """«набережная реки Фонтанки, 132Б» → точка дома. Литеры и корпуса в
    OSM записаны по-разному («132 лит. З»), поэтому сверяем улицу и номер
    дома без литеры. Не нашли дом — точка улицы."""
    m = ADDRESS_RE.match(street)
    base, num = m.group("street"), m.group("num")
    if GEOAPIFY_KEY:
        for f in geoapify(f"{base} {num}{m.group('rest')}, {city}", center, "obj", 5):
            props = f["properties"]
            house = re.match(r"\d+", props.get("housenumber") or "")
            if norm(props.get("street")) != norm(base) or not house or house.group(0) != num:
                continue
            lon, lat = f["geometry"]["coordinates"]
            if norm(props.get("city")) == norm(city) or distance_km(center, (lat, lon)) <= radius_km(city):
                return {"lat": round(lat, 6), "lon": round(lon, 6)}
        time.sleep(PAUSE)
    return ask_osm(city, base, center)


def ask_station(city, station, center):
    """Станция метро. В OSM у Geoapify находятся не сами вестибюли, а
    остановки у выходов — «станция метро „Юго-Западная“». Они в десятках
    метров от станции: если все найденные лежат рядом, ставим точку
    посередине."""
    if not GEOAPIFY_KEY:
        return None
    want = norm(station)
    params = {"text": f"станция метро {station}, {city}", "lang": "ru", "limit": 10,
              "format": "json", "filter": "countrycode:ru", "apiKey": GEOAPIFY_KEY,
              "bias": f"proximity:{center[1]},{center[0]}"}
    data = get_json(GEOAPIFY_URL, params)
    points = []
    for r in data.get("results", []):
        name = norm(r.get("name"))
        if "метро" not in name.split() or f" {want} " not in f" {name} ":
            continue
        if distance_km(center, (r["lat"], r["lon"])) > radius_km(city):
            continue
        points.append((r["lat"], r["lon"]))
    if not points:
        return None
    lat = sum(p[0] for p in points) / len(points)
    lon = sum(p[1] for p in points) / len(points)
    # разбросаны по городу — значит, это разные места, не угадываем
    if any(distance_km((lat, lon), p) > 0.8 for p in points):
        return None
    return {"lat": round(lat, 6), "lon": round(lon, 6)}


def ask_osm(city, street, center):
    if street.startswith("obj:метро "):
        return ask_station(city, street[len("obj:метро "):], center)
    if not street.startswith(("obj:", "loc:")) and ADDRESS_RE.match(street):
        return ask_address(city, street, center)
    # объект («obj:Лахта-центр») ищем среди всего, местность («loc:Черная
    # Грязь») — среди населённых пунктов, улицу — только среди дорог
    is_obj = street.startswith("obj:")
    is_loc = street.startswith("loc:")
    name = street[4:] if is_obj or is_loc else street
    in_region = is_region(city)
    want = norm(name)

    # «тупик Свободы» в тексте — «1-й … 5-й тупик Свободы» на карте: серия
    # пронумерованных улиц, а дословно такой нет
    numbered = re.compile(r"^\d+ (?:[а-я]{1,2} )?" + re.escape(want) + "$")

    def accept(features):
        hits, series = [], []
        for f in features:
            props = f["properties"]
            got = norm(props.get("name"))
            if not is_obj and not is_loc and got != want and numbered.match(got):
                lon, lat = f["geometry"]["coordinates"]
                if norm(props.get("city")) == norm(city) or distance_km(center, (lat, lon)) <= radius_km(city):
                    series.append((lat, lon))
                continue
            # у объекта в OSM к названию часто добавлен тип: «небоскрёб
            # Лахта Центр», «ЖК Ренессанс» — достаточно совпадения по словам
            if got != want and not (is_obj and f" {want} " in f" {got} "):
                continue
            # «Столовая „Красная нить“», остановка «Лахта Центр» — заведение
            # или остановка, названные в честь объекта, а не он сам
            if got != want and any(w in got.split() for w in NAMED_AFTER):
                continue
            # объект — это здание или сооружение, а не населённый пункт и не
            # улица: иначе «кинотеатр „Мир“» нашёлся бы как посёлок Мир
            if is_obj and props.get("osm_key") in ("place", "highway", "boundary", "landuse"):
                continue
            # и не заведение, названное в честь объекта: «Столовая „Красная
            # нить“» — не фабрика «Красная нить», хоть и стоит где-то рядом
            if is_obj and (props.get("osm_key") == "shop" or props.get("osm_value") in FOOD):
                continue
            lon, lat = f["geometry"]["coordinates"]
            if in_region:
                # «село Починки» + «Нижегородская область»: в регионе — где
                # угодно, у Photon регион приходит в поле state
                ok = same_region(props.get("state"), city)
            else:
                # у города местность ищем рядом: названия вроде «Остров»
                # повторяются по всей стране, и в двухстах километрах
                # найдётся чужая деревня
                limit = 50 if is_loc else radius_km(city)
                ok = (norm(props.get("city")) == norm(city)
                      or distance_km(center, (lat, lon)) <= limit)
            if ok:
                hits.append((PLACE_RANK.get(props.get("osm_value"), 1), lat, lon))
        if not hits and series:
            # серия рядом — точка в её середине («пять тупиков Свободы»);
            # разбросана по городу — это разные улицы, не угадываем
            lat = sum(p[0] for p in series) / len(series)
            lon = sum(p[1] for p in series) / len(series)
            if all(distance_km((lat, lon), p) <= 1.5 for p in series):
                return {"lat": round(lat, 6), "lon": round(lon, 6)}
            return None
        if not hits:
            return None
        # «Невский» в Петербурге — заведения на улице Калинина и на Народной:
        # два разных объекта с одним названием, первый попавшийся встал
        # вместо ДК «Невский» (08.10). Разбросаны дальше километра — не угадываем
        if is_obj and any(distance_km((hits[0][1], hits[0][2]), (h[1], h[2])) > 1.0 for h in hits[1:]):
            return None
        if not is_loc:
            _, lat, lon = hits[0]
        else:
            hits.sort(reverse=True)
            # две одинаково крупные деревни — не угадываем
            if len(hits) > 1 and hits[0][0] == hits[1][0] and in_region:
                return None
            _, lat, lon = hits[0]
        return {"lat": round(lat, 6), "lon": round(lon, 6)}

    kind = "loc" if is_loc else "obj" if is_obj else "street"
    if GEOAPIFY_KEY:
        # Geoapify сам подставляет «ё»: «Черная Грязь» находит «Чёрную»
        text = f"{name}, {city}" if in_region or not is_loc else name
        hit = accept(geoapify(text, center, kind, 10 if is_loc or kind == "street" else 5, bias=not in_region))
        # «Сосенки, Московская область» — Geoapify отдаёт одни СНТ, а деревня
        # Сосенки числится в Москве и в выдачу не попадает: ещё раз без
        # области, а что в паре регионов — проверит same_region
        if hit is None and in_region and any(set(norm(city).split()) & pair for pair in TWIN_REGIONS):
            time.sleep(PAUSE)
            hit = accept(geoapify(name, center, kind, 10, bias=True))
        # «Дворцовая площадь» в Казани записана не улицей, а площадью-сквером:
        # второй запрос без фильтра, но только с точным совпадением названия
        if hit is None and kind == "street":
            time.sleep(PAUSE)
            exact = [f for f in geoapify(text, center, "obj", 5)
                     if norm(f["properties"]["name"]) == want
                     and f["properties"]["osm_key"] not in ("place", "shop")
                     and f["properties"]["osm_value"] not in FOOD | {"bus_stop"}]
            hit = accept(exact)
        return hit

    base = {"limit": 10 if is_loc else 5, "lat": center[0], "lon": center[1],
            "lang": "default"}
    if is_loc:
        base["osm_tag"] = "place"
    elif not is_obj:
        base["osm_tag"] = "highway"
    queries = [name] + (yo_variants(name) if is_loc else [])
    for q in queries:
        hit = accept(photon(dict(base, q=q if is_loc else f"{q}, {city}")))
        if hit:
            return hit
        if q is not queries[-1]:
            time.sleep(PAUSE)
    return None


REQUESTS = 0  # сколько запросов отправлено за запуск — для дневного лимита


# --- Wikidata и Викимапия -------------------------------------------------
#
# Второй и третий источник — только для объектов и местностей, которых нет
# в OpenStreetMap: неформальные названия, заброшенные лагеря, рабочие
# городки. Wikidata — CC0, без ключа. Викимапия — CC BY-SA, нужен ключ в
# tools/.wikimapia-key (wikimapia.org/api?action=my_keys); без него шаг
# пропускается.

WIKIDATA_URL = "https://www.wikidata.org/w/api.php"
WIKIMAPIA_URL = "http://api.wikimapia.org/"
WIKIMAPIA_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".wikimapia-key")
WIKIMAPIA_KEY = (open(WIKIMAPIA_KEY_FILE, encoding="utf-8").read().strip()
                 if os.path.exists(WIKIMAPIA_KEY_FILE) else "")
WIKI_PAUSE = 1.0  # оба сервиса просят не частить


def get_json(url, params):
    global REQUESTS
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": UA})
    for attempt in range(RETRIES + 1):
        REQUESTS += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            # обрыв соединения или таймаут — разовый сбой сети: ждём и повторяем
            if not isinstance(e, urllib.error.HTTPError):
                if attempt == RETRIES:
                    raise
                print(f"  сеть: {e}, повтор через {NET_PAUSE} с", file=sys.stderr, flush=True)
                time.sleep(NET_PAUSE)
                continue
            if e.code not in (403, 429) or attempt == RETRIES:
                raise
            print(f"  {url} ответил {e.code}, ждём {BACKOFF // 60} мин", file=sys.stderr, flush=True)
            time.sleep(BACKOFF)


def names_match(want, got, is_obj):
    """«Лахта-центр» = «Лахта Центр»; у объекта допускаем уточнение вокруг:
    «Троицкая церковь „Кулич и Пасха“» подходит к «Кулич и Пасха»."""
    got = norm(got)
    if not got:
        return False
    if got == want:
        return True
    if is_obj and f" {want} " in f" {got} " and not any(w in got.split() for w in NAMED_AFTER):
        return True
    return False


def pick(candidates, city, center, is_loc):
    """Из найденных — единственный в городе поста (или в названной области).
    Два разных места с одним названием рядом — не угадываем."""
    in_region = is_region(city)
    limit = 300 if in_region else (50 if is_loc else radius_km(city))
    near = [(lat, lon) for lat, lon in candidates if distance_km(center, (lat, lon)) <= limit
            and (not (in_region and REGION_CHECK) or region_ok(city, (lat, lon)))]
    if not near:
        return None
    first = near[0]
    if any(distance_km(first, other) > 1.0 for other in near[1:]):
        return None
    return {"lat": round(first[0], 6), "lon": round(first[1], 6)}


def ask_wikidata(city, name, center, is_obj, is_loc):
    want = norm(name)
    found = get_json(WIKIDATA_URL, {"action": "wbsearchentities", "search": name, "language": "ru",
                                    "uselang": "ru", "limit": 10, "format": "json"})
    ids = [x["id"] for x in found.get("search", [])]
    if not ids:
        return None
    time.sleep(WIKI_PAUSE)
    ents = get_json(WIKIDATA_URL, {"action": "wbgetentities", "ids": "|".join(ids),
                                   "props": "labels|aliases|claims", "languages": "ru",
                                   "format": "json"}).get("entities", {})
    candidates = []
    for i in ids:
        ent = ents.get(i, {})
        labels = [ent.get("labels", {}).get("ru", {}).get("value", "")]
        labels += [a["value"] for a in ent.get("aliases", {}).get("ru", [])]
        if not any(names_match(want, lab, is_obj) for lab in labels):
            continue
        claim = (ent.get("claims", {}).get("P625") or [{}])[0]
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        if value:
            candidates.append((value["latitude"], value["longitude"]))
    return pick(candidates, city, center, is_loc)


def ask_wikimapia(city, name, center, is_obj, is_loc):
    """Поиск по названию. У нового API он отключён (place.search отвечает
    пустым списком на любой запрос), работает прежняя функция search —
    она ищет рядом с точкой и отдаёт список в поле folder."""
    if not WIKIMAPIA_KEY:
        return None
    want = norm(name)
    data = get_json(WIKIMAPIA_URL, {"function": "search", "key": WIKIMAPIA_KEY, "q": name,
                                    "lat": center[0], "lon": center[1], "format": "json",
                                    "language": "ru", "count": 50})
    if isinstance(data, dict) and "debug" in data:  # неверный ключ или превышен лимит
        raise RuntimeError(f"Викимапия: {data['debug'].get('message')}")
    candidates = []
    for place in (data.get("folder") if isinstance(data, dict) else None) or []:
        title = place.get("name") or place.get("title") or ""
        # «Жилой комплекс „Гаванский рабочий городок“» подходит к
        # «Гаванский рабочий городок»: кавычки norm() убирает
        if not names_match(want, title, is_obj):
            continue
        loc = place.get("location") or {}
        if loc.get("lat") is not None and loc.get("lon") is not None:
            candidates.append((float(loc["lat"]), float(loc["lon"])))
    return pick(candidates, city, center, is_loc)


# --- Яндекс (HTTP Геокодер) ------------------------------------------------
#
# Последний источник: улицы садовых товариществ, новые дома и места,
# которых нет в OpenStreetMap («улица Пределов Мечты» в Орехово-Зуеве).
# Ключ — tools/.yandex-geocoder-key (в git не попадает). Суточный лимит
# небольшой, поэтому счётчик общий с сервером (поиск в «Указать точку
# вручную») — data/yandex-geocoder-usage.json, сутки по Москве.
YANDEX_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".yandex-geocoder-key")
YANDEX_KEY = (open(YANDEX_KEY_FILE, encoding="utf-8").read().strip()
              if os.path.exists(YANDEX_KEY_FILE) else "")
YANDEX_URL = "https://geocode-maps.yandex.ru/1.x/"
YANDEX_USAGE = os.path.join(DATA, "yandex-geocoder-usage.json")
YANDEX_LIMIT = int(os.environ.get("YANDEX_GEOCODER_LIMIT", 1000))
YANDEX_OFF = False
# какие пары Яндекс уже спрашивали и когда: «--only yandex --retry-missing»
# каждый вечер (tools/groq_daily.py) иначе спрашивал бы одни и те же первые
# сотни ненайденных пар («Абакан|…»). Спрошенные в последние 30 дней
# такой прогон пропускает — лимит идёт на новые
YANDEX_ASKED = os.path.join(DATA, "yandex-asked.json")
YANDEX_RECHECK_DAYS = 30
# дни, когда запускали геокодер с Яндексом, — вечерний прогон смотрит сюда
GEOCODER_RUNS = os.path.join(DATA, "geocoder-runs.json")
_yandex_asked = None


def yandex_asked():
    global _yandex_asked
    if _yandex_asked is None:
        try:
            _yandex_asked = json.load(open(YANDEX_ASKED, encoding="utf-8"))
        except (OSError, ValueError):
            _yandex_asked = {}
    return _yandex_asked


def yandex_budget():
    """Взять один запрос из суточного лимита; False — лимит исчерпан."""
    import datetime
    day = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)).strftime("%Y-%m-%d")
    try:
        u = json.load(open(YANDEX_USAGE, encoding="utf-8"))
    except (OSError, ValueError):
        u = {}
    if u.get("day") != day:
        u = {"day": day, "used": 0}
    if u["used"] >= YANDEX_LIMIT:
        return False
    u["used"] += 1
    json.dump(u, open(YANDEX_USAGE, "w", encoding="utf-8"))
    return True


def yandex_refund():
    """Вернуть запрос в суточный лимит — он не дошёл до Яндекса."""
    try:
        u = json.load(open(YANDEX_USAGE, encoding="utf-8"))
    except (OSError, ValueError):
        return
    if u.get("used", 0) > 0:
        u["used"] -= 1
        json.dump(u, open(YANDEX_USAGE, "w", encoding="utf-8"))


YANDEX_NET_FAILS = 0


def ask_yandex(city, name, center, is_obj, is_loc, pair=None):
    """Яндекс отвечает на всё — не найдя улицы, отдаёт сам город. Поэтому
    берём только ответ нужного вида (улица для улицы, населённый пункт для
    местности) с совпавшим названием и рядом с городом или в его области."""
    global YANDEX_OFF, REQUESTS, YANDEX_NET_FAILS
    if not YANDEX_KEY or YANDEX_OFF:
        return None
    if not yandex_budget():
        print("  лимит Яндекс-геокодера на сегодня исчерпан", file=sys.stderr, flush=True)
        YANDEX_OFF = True
        return None
    # и в потолок --max-requests: иначе прогон съедает весь суточный лимит,
    # и Яндекс-карте в форме точки не остаётся ни одного поиска
    REQUESTS += 1
    in_region = is_region(city)
    params = {"apikey": YANDEX_KEY, "geocode": f"{city}, {name}", "format": "json", "lang": "ru_RU",
              "results": 5, "ll": f"{center[1]},{center[0]}", "spn": "1.0,1.0"}
    # свой запрос, не get_json: тот на 403 ждёт по пять минут, а у Яндекса
    # 403 — это «ключ не принят» или «лимит исчерпан», ждать бесполезно
    try:
        req = urllib.request.Request(YANDEX_URL + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        # 403 — ключ не принят или лимит исчерпан: до конца запуска не спрашиваем
        print(f"  Яндекс-геокодер: {e.code} — отключён до конца запуска", file=sys.stderr, flush=True)
        YANDEX_OFF = True
        return None
    except (urllib.error.URLError, TimeoutError, ValueError):
        # сбой сети — эту пару спросим в следующий раз. Запрос до Яндекса не
        # дошёл: возвращаем его в суточный счётчик. 05–06.10 вечерние прогоны
        # шли без сети (getaddrinfo failed) и «спросили» 600 пар впустую —
        # после нескольких сбоев подряд Яндекс выключается до конца запуска
        yandex_refund()
        YANDEX_NET_FAILS += 1
        if YANDEX_NET_FAILS >= 5:
            print("  Яндекс-геокодер: нет сети — отключён до конца запуска", file=sys.stderr, flush=True)
            YANDEX_OFF = True
        return None
    YANDEX_NET_FAILS = 0
    # спрошено — только когда Яндекс ответил: такую пару следующие 30 дней не
    # переспрашиваем
    if pair:
        yandex_asked()[pair] = time.strftime("%Y-%m-%d")
    want = norm(name)
    want_words = set(want.split())
    kinds = ({"locality", "district"} if is_loc else
             {"house", "other", "metro", "railway", "airport", "vegetation", "hydro"} if is_obj else
             {"street"})
    for m in data.get("response", {}).get("GeoObjectCollection", {}).get("featureMember", []):
        obj = m["GeoObject"]
        meta = obj["metaDataProperty"]["GeocoderMetaData"]
        if meta.get("kind") not in kinds:
            continue
        got = norm(obj.get("name"))
        # у улицы — то же название (слова в любом порядке: «улица Мечты» и
        # «Мечты улица») или пронумерованная серия; у объекта — все слова
        # названия из текста есть в ответе
        if not (got == want or set(got.split()) == want_words
                or (not is_obj and not is_loc and re.match(r"^\d+ (?:[а-я]{1,2} )?" + re.escape(want) + "$", got))
                or (is_obj and want_words <= set(got.split()))):
            continue
        lon, lat = map(float, obj["Point"]["pos"].split())
        if in_region:
            comps = meta.get("Address", {}).get("Components", [])
            ok = any(c.get("kind") == "province" and same_region(c.get("name"), city) for c in comps)
        else:
            ok = distance_km(center, (lat, lon)) <= (50 if is_loc else radius_km(city) + 15)
        if ok:
            return {"lat": round(lat, 6), "lon": round(lon, 6)}
    return None


SOURCES_ORDER = ("local", "osm", "wikidata", "wikimapia", "yandex")
WIKIMAPIA_OFF = False


def ask_local(city, name, center, radius=None):
    """Свои справочники (tools/local_places.py): реестр наследия из Викидаты
    и выгрузка OSM — без сети. Берём, только если в городе одно такое место.
    radius — для поиска по всей области (`llm_places.py local`, посты без
    точки): «Ангарская деревня» у Братска — в 470 км от Иркутска."""
    import local_places
    # «Варшавское шоссе, 141» — адрес, его ищет геокодер по дому
    if re.search(r"\d", name):
        return None
    in_region = is_region(city)
    # область — 150 км от центра: «Ильинские луга» Подмосковья иначе находились
    # под Суздалем (190 км), а границ областей в справочниках нет
    radius = radius or (150 if in_region else radius_km(city))
    # храм по посвящению — только в самом городке: в округе Старицы своя
    # Казанская церковь есть и в Бороздине
    if local_places.is_dedication(name):
        # в большом городе таких храмов несколько — «Здание Успенского храма»
        # встало бы на Успенский собор Кремля
        if in_region or population(city) > 300_000:
            return None
        radius = min(radius, 2.5)
    hits = local_places.search(name, center, radius, city)
    # широкий поиск по области — только с проверкой области: «Республика
    # Карелия», «Татарстан» слов из REGION_WORDS не содержат
    if (in_region or radius > 150) and REGION_CHECK:
        hits = [h for h in hits if region_ok(city, (h["lat"], h["lon"]))]
    hit = local_places.pick(hits)
    # прозвище в кавычках («Мавритания»), которого нет в реестре, — по одному
    # типу, если в городе такой объект один; по области — нет, их там много
    if not hit and not hits and not in_region and radius <= 150:
        hit = local_places.search_by_kind(name, center, radius)
    return {"lat": round(hit["lat"], 6), "lon": round(hit["lon"], 6)} if hit else None


def ask(city, street, center, only=None):
    """Точка для пары: сперва OpenStreetMap (Geoapify или Photon), для
    объектов и местностей — затем Wikidata и Викимапия. Возвращает
    {"lat", "lon"} и, если точка не из OSM, поле src."""
    if ambiguous_dedication(city, street):
        return None
    is_obj = street.startswith("obj:")
    is_loc = street.startswith("loc:")
    name = street[4:] if is_obj or is_loc else street
    for src in SOURCES_ORDER:
        if only and src != only:
            continue
        if src == "local":
            if not is_obj:
                continue
            hit = ask_local(city, name, center)
        elif src == "osm":
            hit = ask_osm(city, street, center)
        elif src == "yandex":
            # последним: и улицы, и объекты, и местности
            hit = ask_yandex(city, name, center, is_obj, is_loc, key(city, street))
        elif not (is_obj or is_loc):
            continue
        elif src == "wikidata":
            time.sleep(WIKI_PAUSE)
            hit = ask_wikidata(city, name, center, is_obj, is_loc)
        else:
            global WIKIMAPIA_OFF
            if not WIKIMAPIA_KEY or WIKIMAPIA_OFF:
                continue
            time.sleep(WIKI_PAUSE)
            try:
                hit = ask_wikimapia(city, name, center, is_obj, is_loc)
            except RuntimeError as e:
                # «IP address limit has been reached»: лимит Викимапии на
                # адрес — выключаем её до конца запуска, остальные источники
                # продолжают. Не спрошенное доберёт следующий запуск
                if only == "wikimapia":
                    raise
                print(f"  {e} — Викимапия отключена до конца запуска", file=sys.stderr, flush=True)
                WIKIMAPIA_OFF = True
                continue
        if hit:
            return hit if src == "osm" else dict(hit, src=src)
    # «Дом генерала Губина», «Дом провизора Вяжлинского» — на картах и в
    # Викидате без чина: «Дом Губина». Пробуем и так
    m = is_obj and RANK_RE.match(name)
    if m:
        return ask(city, f"obj:{m.group(1)} {m.group(2)}", center, only)
    # «Здание Речного вокзала» — на карте «Речной вокзал»: без слова
    # «здание» и в именительном падеже
    m = is_obj and re.match(r"(?i)^здани[ея]\s+(\S+)\s+(\S+)$", name)
    if m:
        import classify
        nom = classify.nominative_pair([m.group(1), m.group(2)])
        if nom:
            return ask(city, "obj:" + nom, center, only)
    return None


RANK_RE = re.compile(
    r"^(Дом|Дача|Усадьба|Особняк|Палаты|Доходный дом)\s+"
    r"(?:генерала|генеральши|купца|купцов|купчихи|провизора|графа|графини|графов|князя|княгини|князей|"
    r"барона|баронессы|фабриканта|фабрикантов|промышленника|мецената|помещика|помещицы|"
    r"губернатора|архитектора|инженера|доктора|врача|профессора|адмирала|полковника|"
    r"градоначальника|городского головы|купеческой вдовы|мещанина|мещан)\s+(.+)$")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    # пары, которые прежний геокодер не нашёл, спросить заново
    ap.add_argument("--retry-missing", action="store_true")
    # потолок запросов за запуск: бесплатный тариф Geoapify — около 3000 в день
    ap.add_argument("--max-requests", type=int, default=2500)
    # спросить только один источник: «--only wikidata --retry-missing»
    # доберёт то, чего не нашёл OSM, не тратя лимит Geoapify
    ap.add_argument("--only", choices=SOURCES_ORDER)
    # переспросить ненайденное только по шаблону — после нового правила
    # поиска, не тратя лимит на всё подряд: «--retry-match "^obj:Дом "»
    ap.add_argument("--retry-match")
    args = ap.parse_args()

    with open(os.path.join(DATA, "posts_classified.json"), encoding="utf-8") as f:
        posts = json.load(f)
    pairs, coords = collect_pairs(posts)
    cache = {}
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    # точки, которые новое правило про храмы считает ненадёжными, снимаем
    dropped = [k for k, v in cache.items() if v and ambiguous_dedication(*k.split("|", 1))]
    for k in dropped:
        cache[k] = None
    if dropped:
        print(f"снято ненадёжных точек храмов: {len(dropped)}")
    todo = sorted(p for p in pairs if key(*p) not in cache
                  or (args.retry_missing and cache[key(*p)] is None)
                  or (args.retry_match and cache[key(*p)] is None and re.search(args.retry_match, p[1])))
    # Wikidata и Викимапия ищут только объекты и местности: улицы при
    # «--only wikidata» не спрашиваем, иначе они записались бы в кэш как
    # ненайденные, хотя OSM их даже не видел
    if args.only in ("wikidata", "wikimapia"):
        todo = [p for p in todo if p[1].startswith(("obj:", "loc:"))]
    # свои справочники знают только объекты
    if args.only == "local":
        todo = [p for p in todo if p[1].startswith("obj:")]
    if args.only == "yandex":
        import datetime
        fresh = (datetime.date.today() - datetime.timedelta(days=YANDEX_RECHECK_DAYS)).isoformat()
        asked = yandex_asked()
        todo = [p for p in todo if asked.get(key(*p), "") < fresh]
    if YANDEX_KEY and args.only in (None, "yandex"):
        runs = json.load(open(GEOCODER_RUNS, encoding="utf-8")) if os.path.exists(GEOCODER_RUNS) else {}
        runs.setdefault(time.strftime("%Y-%m-%d"), []).append(time.strftime("%H:%M"))
        json.dump(runs, open(GEOCODER_RUNS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("геокодер:", "Geoapify" if GEOAPIFY_KEY else "Photon",
          "+ Wikidata" + (" + Викимапия" if WIKIMAPIA_KEY else " (Викимапия — нет ключа)"),
          f"| только {args.only}" if args.only else "")
    print(f"пар город+улица: {len(pairs)}, уже в кэше: {len(pairs) - len(todo)}, "
          f"спросить: {len(todo)} (~{len(todo) * PAUSE / 60:.0f} мин)")

    found = failures = 0
    for n, (city, street) in enumerate(todo, 1):
        if REQUESTS >= args.max_requests:
            print(f"достигнут потолок {args.max_requests} запросов — остальное в следующий запуск")
            break
        try:
            hit = ask(city, street, coords[city], args.only)
            failures = 0
        except OSError as e:
            # разрыв соединения (WinError 10054): 29.09 прогон встал на 225-й
            # паре из 3573. Пару пропускаем — не пишем в кэш, спросится в
            # следующий раз; встаём, только если сеть лежит подряд
            failures += 1
            if failures >= 10:
                print(f"остановились на {n}: сеть не отвечает — {e}", file=sys.stderr)
                break
            print(f"  {city}|{street}: {e} — пропуск", file=sys.stderr, flush=True)
            time.sleep(NET_PAUSE)
            continue
        except Exception as e:  # лимит и прочее — сохраняем что есть и выходим
            print(f"остановились на {n}: {e}", file=sys.stderr)
            break
        # «--only local»: не нашли у себя — ещё не значит «нет нигде»,
        # такую пару в кэш не пишем, её спросит обычный прогон
        if hit is not None or args.only != "local":
            cache[key(city, street)] = hit
        found += hit is not None
        if n % 50 == 0 or n == len(todo):
            with open(CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=0)
            if _yandex_asked is not None:
                json.dump(_yandex_asked, open(YANDEX_ASKED, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
            print(f"  {n}/{len(todo)}, найдено {found}", flush=True)
        if args.only != "local":
            time.sleep(PAUSE)

    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=0)
    if _yandex_asked is not None:
        json.dump(_yandex_asked, open(YANDEX_ASKED, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    total = sum(1 for v in cache.values() if v)
    print(f"запросов за запуск: {REQUESTS}")
    print(f"в кэше {len(cache)} пар, с координатами {total} -> {CACHE}")


if __name__ == "__main__":
    main()
