"""Поиск места по названию в локальных справочниках — без сети и лимитов.

Два справочника:
- data/heritage.json — объекты культурного наследия из Викидаты (номер
  реестра, tools/fetch_heritage.py): «дом купца Чернухина», «особняк
  Слепцова», храмы сёл;
- data/osm/names.sqlite — всё именованное из выгрузки OpenStreetMap
  (tools/build_osm_names.py): музеи, театры, ДК, здания с названием. Overpass
  неделями не отвечал (504, 500), а Geoapify ищет по-своему и с лимитом.

Названия сравниваются по значимым словам в начальной форме: общие слова
(«дом», «церковь», «купца») не считаются, зато все значимые слова запроса
должны быть в названии объекта — «дом Чернухина» находит «Особняк купца
Чернухина», но не «Чернухинскую улицу». Тип при этом должен быть совместим:
«церковь» не подходит к «дому».

    import local_places; local_places.search("Дом Чернухина", (53.15, 48.47), 20)
"""
import functools, json, math, os, re, sqlite3

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
HERITAGE = os.path.join(DATA, "heritage.json")
EGRKN = os.path.join(DATA, "egrkn.json")
OSM_DB = os.path.join(DATA, "osm", "names.sqlite")

WORD_RE = re.compile(r"[А-ЯЁа-яёA-Za-z0-9]+")

# слова, из которых одних название не складывается: тип, чин, эпитет
GENERIC = {
    "дом", "дворец", "музей", "церковь", "храм", "собор", "часовня", "монастырь",
    "усадьба", "особняк", "здание", "корпус", "флигель", "станция", "метро", "вокзал",
    "парк", "сад", "сквер", "площадь", "улица", "проспект", "переулок", "набережная",
    "театр", "кинотеатр", "библиотека", "школа", "гимназия", "училище", "больница",
    "завод", "фабрика", "рынок", "мост", "башня", "памятник", "гостиница", "отель",
    "клуб", "центр", "культура", "история", "город", "городской", "краеведческий",
    "областной", "главный", "старый", "новый", "бывший", "большой", "малый",
    "имя", "им", "святой", "св", "икона", "божий", "богородица", "пресвятой",
    "дк", "жилой", "доходный", "купеческий", "торговый", "ряд", "водонапорный",
    "железнодорожный", "речной", "морской", "один", "два", "три",
    "метрополитен", "район", "округ", "микрорайон", "комплекс", "ансамбль",
    "купец", "купчиха", "мещанин", "граф", "князь", "генерал", "барон",
    "фабрикант", "промышленник", "помещик", "врач", "доктор", "инженер",
    "архитектор", "палата", "дача", "постройка", "сооружение", "объект",
    "мемориальный", "государственный", "российский", "русский", "советский",
    "федеральный", "муниципальный", "бюджетный", "учреждение", "культурный",
    "мать", "матерь", "образ", "господень", "господний", "господь", "христов", "христовый",
    "пресвятая", "богородицын", "животворящий", "нерукотворный",
    # «парк-мемориал Свирская победа» в OSM — просто «Свирская Победа»
    "мемориал", "мемориальный", "заповедник", "музей-заповедник", "лесопарк",
}

# тип объекта OSM — из тега, если его нет в названии: «Свирская Победа» —
# leisure=park, то есть парк
TAG_KINDS = {
    "leisure=park": "парк", "leisure=garden": "сад", "leisure=stadium": "стадион",
    "tourism=museum": "музей", "tourism=hotel": "гостиница", "amenity=theatre": "театр",
    "amenity=cinema": "кинотеатр", "amenity=place_of_worship": "храм",
    "amenity=library": "библиотека", "amenity=school": "школа", "amenity=hospital": "больница",
    "amenity=university": "университет", "amenity=college": "училище",
    "historic=monument": "памятник", "historic=memorial": "памятник",
    "historic=castle": "замок", "historic=manor": "усадьба", "historic=monastery": "монастырь",
    "building=church": "церковь", "building=cathedral": "собор", "building=chapel": "часовня",
    "building=mosque": "мечеть", "building=synagogue": "синагога", "building=house": "дом",
    "building=apartments": "дом", "building=residential": "дом", "man_made=lighthouse": "маяк",
    "man_made=water_tower": "башня", "man_made=tower": "башня", "station": "станция",
    "amenity=community_centre": "дк", "amenity=arts_centre": "центр",
}
# совместимые типы: «дом Чернухина» = «особняк купца Чернухина»
FAMILIES = [
    {"дом", "особняк", "усадьба", "здание", "дача", "палата", "флигель", "корпус"},
    # «храм» — и церковь, и собор; а церковь и собор — разное: Троицкий
    # собор Архангельска снесён, «Свято-Троицкая церковь» — другое здание
    {"церковь", "храм", "кирха", "костел"},
    {"собор", "храм"},
    {"парк", "сад", "сквер"},
    {"гостиница", "отель"},
    {"дк", "дворец", "клуб"},
    # «Ленинский мемориал» — «музей-мемориал В. И. Ленина», «парк-мемориал
    # Свирская победа» — парк; но музей и парк друг другу не родня
    {"мемориал", "музей"}, {"мемориал", "парк"}, {"мемориал", "памятник"},
]
CHURCHES = {"церковь", "храм", "собор", "часовня", "монастырь", "кирха", "костел"}
# здания, у которых имя из одних прилагательных — обрывок разбора: «ДК
# Московский», «Дом Калужского» (у храма прилагательное — посвящение)
SECULAR = {"дом", "особняк", "усадьба", "здание", "дача", "палата", "флигель", "корпус",
           "дк", "дворец", "клуб", "театр", "кинотеатр", "гостиница", "отель", "школа",
           "институт", "училище", "университет", "гимназия", "центр", "музей", "библиотека",
           "больница", "вокзал", "станция", "академия", "завод", "фабрика"}
# тип объекта: «Дом Советов» не «Парк Советов»
TYPES = set().union(*FAMILIES) | {
    "музей", "монастырь", "станция", "вокзал", "площадь", "библиотека", "школа",
    "гимназия", "училище", "больница", "завод", "фабрика", "рынок", "мост", "башня",
    "памятник", "центр", "мечеть", "синагога", "замок", "кремль", "маяк", "стадион",
    "университет", "институт", "академия", "галерея", "пассаж", "универмаг", "цирк",
    "планетарий", "элеватор", "водокачка", "мельница", "кладбище", "пруд", "озеро", "остров",
    "часовня", "колокольня", "набережная", "улица", "переулок", "проспект", "луг", "роща",
    "село", "деревня", "посёлок", "поселок", "автогараж", "гараж", "филармония", "магазин",
    "мемориал",
}


@functools.lru_cache(maxsize=None)
def _morph():
    import pymorphy3
    return pymorphy3.MorphAnalyzer()


@functools.lru_cache(maxsize=300_000)
def lemma(word):
    w = word.lower().replace("ё", "е")
    if w.isdigit() or len(w) == 1:
        return w
    return _morph().parse(w)[0].normal_form.replace("ё", "е")


@functools.lru_cache(maxsize=300_000)
def pos(lem):
    return _morph().parse(lem)[0].tag.POS


def lemmas(name):
    # инициалы («В. Н. Слепцова») — не слова названия; скобки в реестре —
    # уточнение, где стоит («Казанская церковь (Бороздино)»), а не имя
    name = re.sub(r"\([^)]*\)", " ", name or "")
    return [lemma(w) for w in WORD_RE.findall(name) if len(w) > 1 or w.isdigit()]


# «Я был здесь»: глагол, наречие, местоимение — не слова названия
NOT_NAME_POS = {"VERB", "INFN", "ADVB", "NPRO", "PREP", "CONJ", "PRCL", "PRED", "INTJ", "GRND"}


def distinctive(lems):
    # тип («посёлок», «башня») — не имя: «поселок Лух» в Лухе — сам Лух
    return {l for l in lems if l not in GENERIC and l not in TYPES and len(l) > 2
            and not l.isdigit() and pos(l) not in NOT_NAME_POS}


@functools.lru_cache(maxsize=300_000)
def is_adj(lem):
    # «успенский» первым разбором — фамилия, но это и прилагательное
    return any(p.tag.POS in ("ADJF", "PRTF") for p in _morph().parse(lem))


def adjectives_only(want):
    return bool(want) and all(is_adj(l) for l in want)


def kind_of(lems):
    """Слово-тип названия: «музей», «церковь», «дом». «Здание Успенского
    храма» — храм: здание при другом типе только обёртка."""
    kinds = [l for l in lems if l in TYPES]
    # только «здание»: «Дом причта Успенской церкви» — дом, а не церковь
    if len(kinds) > 1 and kinds[0] == "здание":
        return kinds[1]
    return kinds[0] if kinds else None


def is_dedication(name):
    """«Казанская церковь», «Никольский собор»: посвящение без имени места —
    таких храмов в округе много, ищем только в самом городке."""
    q = lemmas(name)
    want = distinctive(q)
    return adjectives_only(want) and kind_of(q) in CHURCHES


def compatible(a, b):
    if a is None or b is None or a == b:
        return True
    return any(a in fam and b in fam for fam in FAMILIES)


def km(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(a[0] - b[0], (a[1] - b[1]) * k) * 111.2


def matches(query_lems, want, name_lems, tag_kind=None):
    """Все значимые слова запроса есть в названии, лишних значимых — не
    больше двух, тип совместим. Возвращает число лишних слов или None.
    tag_kind — тип по тегу OSM, если в названии его нет."""
    have = distinctive(name_lems)
    if not want <= have or len(have - want) > 2:
        return None
    kq, kn = kind_of(query_lems), kind_of(name_lems) or TAG_KINDS.get(tag_kind)
    # «Введенская церковь» — не село Введенское, «Петровский театр» — не
    # Петровская набережная: у найденного должен быть тип, раз он есть в запросе
    if kq and not kn:
        return None
    # без типа запрос из одного слова — обрывок разбора («Виталий»,
    # «Островский»), а «Кунст и Альберс», «Осиновая Роща» — имя
    if not kq and len(want) < 2:
        return None
    if not compatible(kq, kn):
        return None
    # «Дом Калужского», «Здание Калининградского областного»: у дома из
    # своих слов одни прилагательные — это обрывок, а не имя
    if kq in SECULAR and adjectives_only(want):
        return None
    # родственный, но другой тип — уже не полное совпадение: «Церковь Петра
    # и Павла» (Петрикирхе) — не Петропавловский собор
    return len(have - want) + (1 if kq and kn and kq != kn else 0)


@functools.lru_cache(maxsize=None)
def heritage_index():
    """Значимая лемма -> [(запись, леммы варианта названия)]. Два списка
    наследия: из Викидаты (data/heritage.json) и выгрузка реестра Минкультуры
    (data/egrkn.json, tools/import_egrkn.py). Один памятник может быть в
    обоих — точки рядом, pick считает их одним местом."""
    index = {}
    for path in (HERITAGE, EGRKN):
        if not os.path.exists(path):
            continue
        for rec in json.load(open(path, encoding="utf-8")):
            for name in [rec["name"]] + rec.get("alt", []):
                lems = lemmas(name)
                for l in distinctive(lems):
                    index.setdefault(l, []).append((rec, lems))
    return index


def _osm_db():
    if not os.path.exists(OSM_DB):
        return None
    return sqlite3.connect(f"file:{OSM_DB}?mode=ro", uri=True)


# запрос без типа не находит ЖК, отель, магазин: «Мещерский лес» — не ЖК
# «Мещёрский лес», «Китовый берег» — не «Арктик-отель Китовый берег»
COMMERCIAL_RE = re.compile(r"(?i)\b(?:жк|жилой комплекс|отель|гостиница|апарт|магазин|тц|торговый центр)\b")
COMMERCIAL_TAGS = ("tourism=hotel", "tourism=guest_house", "tourism=hostel", "shop=", "amenity=language_school",
                   "amenity=restaurant", "amenity=cafe", "office=", "amenity=payment_terminal",
                   "amenity=atm", "amenity=bank", "amenity=vending", "amenity=parcel_locker",
                   # «Крутыми Ключами» (район) — не жилой дом «крутые ключи»
                   "building=apartments", "building=residential", "place=neighbourhood")
# «Волгоградского проспекта» — улица, её ищет геокодер улиц
STREET_KINDS = {"улица", "проспект", "переулок", "набережная", "шоссе", "бульвар", "проезд"}


def search(name, center, radius_km=30, city=""):
    """Кандидаты рядом с center: [{"name", "lat", "lon", "src", "km", "id"}],
    ближние первыми. src — «okn» (реестр наследия) или «osm» (выгрузка)."""
    # станции ищет геокодер (метро …): «метро Площадь Ленина» — не площадь
    if re.match(r"(?i)\s*(?:метро|станция метро)\b", name or ""):
        return []
    q = lemmas(name)
    if kind_of(q) in STREET_KINDS:
        return []
    want = distinctive(q)
    # «Здание Саратова» в Саратове (и в Саратовской области) — сам город
    city_l = lemmas(city)
    if not want or all(any(c[:5] == w[:5] for c in city_l) for w in want):
        return []
    # «Казанский храм» — посвящение: других значимых слов у храма быть не
    # должно, иначе это «церковь Спаса Казанского монастыря»
    dedication = adjectives_only(want) and kind_of(q) in CHURCHES
    out = []
    # реестр: пересечение по самой редкой значимой лемме
    index = heritage_index()
    rarest = min(want, key=lambda l: len(index.get(l, ())))
    seen = set()
    best = {}
    for rec, lems in index.get(rarest, ()):
        extra = matches(q, want, lems)
        if extra is None:
            continue
        d = km(center, (rec["lat"], rec["lon"]))
        if d <= radius_km and (rec["q"] not in best or extra < best[rec["q"]]["extra"]):
            best[rec["q"]] = {"name": rec["name"], "lat": rec["lat"], "lon": rec["lon"],
                              "src": "okn", "km": round(d, 1), "id": rec["q"],
                              "addr": rec.get("addr", ""), "extra": extra}
    out += best.values()
    db = _osm_db()
    if db:
        dlat = radius_km / 111.2
        dlon = dlat / max(0.1, math.cos(math.radians(center[0])))
        rows = db.execute(
            "SELECT name, lat, lon, kind, osm FROM names WHERE names MATCH ? "
            "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            ("lemmas : (" + " AND ".join(f'"{l}"' for l in sorted(want)) + ")",
             center[0] - dlat, center[0] + dlat, center[1] - dlon, center[1] + dlon)).fetchall()
        for nm, lat, lon, kind, osm in rows:
            extra = matches(q, want, lemmas(nm), kind)
            if extra is None:
                continue
            d = km(center, (lat, lon))
            if d <= radius_km:
                out.append({"name": nm, "lat": lat, "lon": lon, "src": "osm",
                            "km": round(d, 1), "id": osm, "kind": kind, "extra": extra})
        db.close()
    if dedication:
        out = [h for h in out if h["extra"] <= 1]
        out = [h for h in out if not (distinctive(lemmas(h["name"])) - want)]
        # тип — тот же: «храм» подходит и к церкви, и к собору, а собор к
        # церкви — нет (Троицкий собор Архангельска снесён, «Свято-Троицкий
        # храм» — другое здание)
        kq = kind_of(q)
        out = [h for h in out
               if (kn := kind_of(lemmas(h["name"])) or TAG_KINDS.get(h.get("kind"))) in (None, kq)
               or kq == "храм" or (kn == "храм" and kq != "собор")]
    # «Центр МИРА» — не «Центр „Мир“»: аббревиатура должна быть и в названии
    abbr = [w for w in WORD_RE.findall(name) if len(w) >= 3 and w.isupper()]
    if abbr:
        out = [h for h in out if all(a in h["name"] for a in abbr)]
    if not kind_of(q) and not COMMERCIAL_RE.search(name):
        out = [h for h in out if not COMMERCIAL_RE.search(h["name"])
               and not str(h.get("kind", "")).startswith(COMMERCIAL_TAGS)]
    out.sort(key=lambda h: h["km"])
    return out


def pick(hits, spread_km=1.0):
    """Одно место: все найденные рядом друг с другом (корпуса одной усадьбы,
    узел и здание OSM). Два разных места с одним именем — не угадываем."""
    if not hits:
        return None

    def one_place(group):
        first = group[0]
        if any(km((first["lat"], first["lon"]), (h["lat"], h["lon"])) > spread_km for h in group[1:]):
            return None
        # реестр точнее: у него точка — сам памятник
        okn = [h for h in group if h["src"] == "okn"]
        return okn[0] if okn else first

    # название совпало целиком — оно и есть: «Дом Пашкова», а не «Усадьба
    # С. И. Пашкова (Басманный)»; «Торговый дом Гвардейского экономического
    # общества» (второе имя ДЛТ), а не его магазин в Пушкине
    # реестр составлен вручную, в OSM у одного места бывает несколько записей
    # (корпуса, филиалы, дубли): одно место в реестре — оно и есть
    okn = [h for h in hits if h["src"] == "okn"]
    for group in ([h for h in okn if h.get("extra") == 0], okn):
        if group:
            found = one_place(group)
            if found:
                return found
            if len(group) > 1:
                return None
    exact = [h for h in hits if h.get("extra") == 0]
    if exact:
        found = one_place(exact)
        if found or len(exact) > 1:
            return found
    return one_place(hits)
