# -*- coding: utf-8 -*-
"""Место поста по смыслу — для трудных случаев, где правил classify.py мало.

Посты читает модель (Claude Haiku) в сессии Claude Code: этот скрипт
только готовит пачки и разбирает ответы, сам он модель не вызывает.
Ответ модели не применяется напрямую — он уходит в модерацию как
уточнение с пометкой «модель»: точку ставит админ кнопкой «Применить
точку» в панели модерации.

    python tools/llm_places.py select --limit 100 --batch 25 [--edits PATH]
        отбирает трудные посты с фотографиями и пишет пачки
        data/llm/batch-NN.json — их читают агенты по инструкции
        tools/llm_prompt.md. Первыми идут посты, которые модератор отправил
        на доработку (с прошлым ответом и его пояснением), а ручные точки
        модератора становятся примерами — data/llm/examples.json.
        --edits — edits.json со стенда (правки модератора живут там):
        ssh … cat /srv/archimap/data/edits.json > data/edits-stand.json
    python tools/llm_places.py merge
        читает ответы data/llm/answer-NN.json, ищет точки геокодером
        (с теми же проверками), складывает в data/llm_places.json и
        отправляет в модерацию локального сервера (нужен npm run dev)

Модель называет места, но не координаты: координаты ставит геокодер.
"""
import argparse, glob, json, os, re, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
LLM_DIR = os.path.join(DATA, "llm")
STORE = os.path.join(DATA, "llm_places.json")
EXPORT = os.path.join(LLM_DIR, "suggestions.json")
API = os.environ.get("ARCHIMAP_API", "http://localhost:5174/api")
PASSWORD = os.environ.get("ADMIN_PASSWORD", "dev")

REGION_WORDS = ("область", "край", "республика", "округ", "губерния", "Россия")
ABBR_RE = re.compile(r"^[А-ЯЁA-Z]{2,5}$")
# кандидат-станция от модели: «станция метро «Шоссе Энтузиастов»», «метро Киевская»
STATION_RE = re.compile(r"^(?:станци[яи]\s+)?(?:метро|МЦК|МЦД)\s+«?([^«»]+?)»?$", re.I)


def key_of(p):
    return f"{p['src']}-{p['id']}"


def hardness(p):
    """Чем больше спорных мест и неразобранных объектов, тем труднее пост."""
    places = p["places"]
    cities = {x["name"] for x in places if x["scope"] == "ru" and not x.get("exact")
              and not any(w in x["name"] for w in REGION_WORDS)}
    exact = [x for x in places if x.get("exact")]
    objects = p.get("objects", [])
    streets = [s for s in p["streets"] if not s.startswith(("пос. ", "с. ", "дер. ", "х. ", "ст-ца ", "мкр ", "о. ", "н. п. "))]
    score = 0
    score += 3 * len(objects)            # объект назван, а точки нет
    score += 2 * len(streets)            # улица без точки
    score += 2 * max(0, len(cities) - 1)  # несколько городов — какой главный?
    if not exact:
        score += 2                       # на карте только город
    if len(p["text"]) > 1500:
        score += 1                       # длинный текст, много упоминаний
    if not places:
        score += 1
    return score


def select(args):
    posts = json.load(open(os.path.join(DATA, "posts_classified.json"), encoding="utf-8"))
    by_key = {key_of(p): p for p in posts}
    done = json.load(open(STORE, encoding="utf-8")) if os.path.exists(STORE) else {}
    edits, rework, examples = set(), {}, []
    edits_path = args.edits or os.path.join(DATA, "edits.json")
    if os.path.exists(edits_path):
        e = json.load(open(edits_path, encoding="utf-8"))
        # ручные точки: главная (location) и остальные (extra), если пост
        # про несколько зданий
        located = {k: [v["location"], *v.get("extra", [])]
                   for k, v in e.get("edits", {}).items() if v.get("location")}
        related = {k: v.get("related", []) for k, v in e.get("edits", {}).items() if v.get("location")}
        edits = set(located) | set(e.get("hidden", {}))
        # ручные точки модератора — примеры того, какой точности ждём, и
        # связанные места, если модератор их отметил
        for k, locs in list(located.items())[-args.examples:]:
            if k in by_key:
                examples.append({"text": by_key[k]["text"][:800],
                                 "places": [{"place": x.get("name", ""), "lat": x["lat"], "lon": x["lon"]}
                                            for x in locs],
                                 **({"related": [x.get("name", "") for x in related[k]]} if related[k] else {})})
        # «на доработку»: пост снова идёт модели — с прошлым ответом и пояснением
        for sug in e.get("suggestions", []):
            if sug.get("from") == "model" and sug.get("status") == "rework" and sug.get("key") in by_key:
                rework[sug["key"]] = {"previous": sug.get("text", ""), "admin_note": sug.get("note", "")}
        # отправил на доработку, а потом сам поставил точку или скрыл пост —
        # решение принято, модели он больше не нужен
        rework = {k: v for k, v in rework.items() if k not in edits}
    os.makedirs(LLM_DIR, exist_ok=True)
    with open(os.path.join(LLM_DIR, "examples.json"), "w", encoding="utf-8") as f:
        json.dump(examples, f, ensure_ascii=False, indent=1)
    print("примеров для модели:", len(examples), "| на доработку:", len(rework))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from build_web_data import is_ad

    # автор сам дал координаты или ссылку на точку («Координаты: 59.96,
    # 30.26» у ozzed) — модели там искать нечего, точнее не будет
    pool = [p for p in posts
            if p["group"] == "ru" and p["photos"] and p["text"].strip()
            and key_of(p) not in done and key_of(p) not in edits and not is_ad(p["text"])
            and not any(x.get("own") for x in p["places"])]
    pool.sort(key=lambda p: (-hardness(p), -len(p["text"])))
    picked = [by_key[k] for k in rework] + [p for p in pool if key_of(p) not in rework]
    picked = picked[:args.limit]

    os.makedirs(LLM_DIR, exist_ok=True)
    for f in glob.glob(os.path.join(LLM_DIR, "batch-*.json")):
        os.remove(f)
    for n in range(0, len(picked), args.batch):
        rows = []
        for p in picked[n:n + args.batch]:
            rows.append({
                "key": key_of(p),
                "text": p["text"][:3000],
                # что нашли правила — модели как подсказка, не как ответ
                "rules_places": [x["name"] for x in p["places"]],
                "rules_streets": p["streets"],
                "rules_objects": p.get("objects", []),
                **rework.get(key_of(p), {}),
            })
        path = os.path.join(LLM_DIR, f"batch-{n // args.batch + 1:02d}.json")
        json.dump(rows, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(path, len(rows))
    print("отобрано", len(picked), "из", len(pool), "| сложность от", hardness(picked[-1]) if picked else "-",
          "до", hardness(picked[0]) if picked else "-")


def api(path, body=None, token=None):
    req = urllib.request.Request(API + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def strip_city(c, city, classify):
    """«Речной вокзал Ростова-на-Дону», «вокзал, Москва», «музей в Самаре» →
    запасной вариант без города: геокодер ищет объект в городе сам и сверяет
    название дословно, а с городом внутри такой строки на карте нет. Сам
    ответ модели не меняем — вариант без города пробуется вторым."""
    if not city or not c:
        return c
    want = [classify.lemma(w) for w in city.split()]
    words = c.replace(",", " , ").split()
    n = len(want)
    if len(words) > n and [classify.lemma(w) for w in words[-n:]] == want:
        words = words[:-n]
        while words and words[-1].lower() in (",", "в", "во", "г.", "город", "города"):
            words = words[:-1]
    return " ".join(words).replace(" ,", ",").strip(" ,") or c


def candidates_of(answer):
    """Кандидаты от точного к общему. Улицы — через нормализатор разбора:
    модель пишет «улица Мосфильмовская», а на карте — «Мосфильмовская улица»,
    и геокодер, сверяющий название дословно, промахивался."""
    import classify
    raw = list(answer.get("candidates") or [])
    # ответы старого формата (пилот): object отдельно. Поле address теперь —
    # адрес по памяти модели, его проверяет locate_address, а не геокодер вслепую
    raw += [answer.get(f) for f in ("object",) if answer.get(f)]
    # кандидат модели остаётся как есть, а следом — он же без города в конце:
    # «Речной вокзал Ростова-на-Дону» на карте просто «Речной вокзал»
    expanded = []
    for c in raw:
        c = (c or "").strip()
        expanded.append(c)
        bare = strip_city(c, answer.get("city") or "", classify)
        if bare != c:
            expanded.append(bare)
    out = []
    for c in expanded:
        # голая аббревиатура — не место: «ЦДТ» нашёлся как центр детского
        # творчества у Тульской, а пост про Центральный дом туриста
        if not c or ABBR_RE.match(c):
            continue
        # станцию — раньше улиц: «станция метро «Шоссе Энтузиастов»» иначе
        # нашлась бы как само шоссе Энтузиастов
        station = STATION_RE.match(c)
        streets = [] if station else classify.find_streets(c)
        if station:
            item = "obj:метро " + station.group(1)
        else:
            item = streets[0] if streets else "obj:" + c
        if item not in out:
            out.append(item)
    if answer.get("locality"):
        out.append("loc:" + answer["locality"].strip())
    return out


ADDRESS_CHECK_KM = 0.25


def locate_address(answer, where, center):
    """Адрес, который модель назвала по памяти («Музей одной картины» —
    «улица Кирова, 11»). Геокодер находит дом, но верим ему, только если в
    ADDRESS_CHECK_KM от дома в своих справочниках (реестр наследия, OSM) есть
    объект с названием из кандидатов модели. Иначе адрес уходит в модерацию
    с пометкой «не проверен» (поле address_unverified) — точка на городе."""
    import classify
    import geocode_streets as g
    import local_places
    addr = (answer.get("address") or "").strip()
    if not addr:
        return None
    streets = classify.find_streets(addr)
    if not streets or not re.search(r",\s*\d", streets[0]):
        answer["address_unverified"] = addr
        return None
    point = g.ask(where, streets[0], center)
    if not point:
        answer["address_unverified"] = addr
        return None
    here = (point["lat"], point["lon"])
    for c in (answer.get("candidates") or [])[:3]:
        if not c or classify.find_streets(c):
            continue
        if local_places.search(c, here, ADDRESS_CHECK_KM):
            return ({"lat": point["lat"], "lon": point["lon"], "src": "address"},
                    f"{c} ({streets[0]}) · {where}")
    answer["address_unverified"] = addr
    return None


def region_in_text(region, text):
    """«Рязанская область» названа в посте («в Рязанской области»)?"""
    words = [w for w in re.findall(r"[А-ЯЁа-яё]+", region or "") if w[:1].isupper()]
    return bool(words) and all(w[:5].lower() in (text or "").lower() for w in words)


def locate(answer, coords, text=None):
    """Точка для ответа модели: первый кандидат, который нашёл геокодер,
    иначе город. Область проверяем, только если она есть в тексте поста."""
    import geocode_streets as g
    saved = g.REGION_CHECK
    g.REGION_CHECK = text is None or region_in_text(answer.get("region"), text)
    try:
        return _locate(answer, coords)
    finally:
        g.REGION_CHECK = saved


def _locate(answer, coords):
    import geocode_streets as g
    city = (answer.get("city") or "").strip()
    region = (answer.get("region") or "").strip()
    where = city if city in coords else (region if region in coords else None)
    if not where:
        return None, None
    center = coords[where]
    # «Никольск, Вологодская область»: в справочнике городов один Никольск —
    # пензенский, и точка уезжала за 900 км. Если модель назвала область,
    # город сначала ищем в ней; нашёлся не там, где справочный, — берём его
    home = None
    if city and region in coords and city != region:
        found = g.ask(region, "loc:" + city, coords[region])
        if found and (city not in coords or g.distance_km((found["lat"], found["lon"]), coords[city]) > 30):
            home = {"lat": found["lat"], "lon": found["lon"]}
            where, center = city, (found["lat"], found["lon"])
    # Село без города: «церковь Дмитрия Солунского, Щелейки · Ленинградская
    # область». Искать храм от центра области — найдётся одноимённый в
    # Петербурге. Сперва находим село, объект ищем в своих справочниках рядом
    # с ним (3 км); не нашёлся — точка на селе, а не на чужом храме
    locality = (answer.get("locality") or "").strip()
    # Село при городе — тоже сперва село: «церковь Рождества Богородицы,
    # Гимрека» модель записала при Петрозаводске, и храм искался бы в
    # Петрозаводске. Не нашлось у города — ищем в области поста (у also
    # область бывает не та: Гимрека в Ленобласти, а не в Карелии)
    if locality and locality != city:
        import local_places
        spot = g.ask(where, "loc:" + locality, center)
        parent = (answer.get("_post_region") or "").strip()
        if not spot and parent in coords and parent != where:
            spot = g.ask(parent, "loc:" + locality, coords[parent])
        if spot:
            here = (spot["lat"], spot["lon"])
            for item in candidates_of(answer):
                if not item.startswith("obj:") or item[4:] == locality:
                    continue
                hit = local_places.pick(local_places.search(item[4:], here, 3, locality))
                if hit:
                    return ({"lat": hit["lat"], "lon": hit["lon"], "src": "local"},
                            f"{item[4:]} · {locality}")
            return {"lat": spot["lat"], "lon": spot["lon"]}, f"{locality} · {region or where}"
        # («Подпорожск» модели в справочнике нет, а «Тверская область» в поле
        # города — та же область: поиск идёт по области.) Село не нашлось —
        # объект по всей области не ищем: найдётся одноимённый чужой
        if region and where == region:
            lat, lon = center
            return {"lat": lat, "lon": lon}, region
    for item in candidates_of(answer):
        # сам город среди кандидатов — не объект: «Никольск» в «Никольске»
        if item in ("obj:" + city, "loc:" + city):
            continue
        hit = g.ask(where, item, center)
        if hit:
            name = item.split(":", 1)[1] if item.startswith(("obj:", "loc:")) else item
            return hit, f"{name} · {where}"
    found = locate_address(answer, where, center)
    if found:
        return found
    if home:
        return home, f"{city} · {region}"
    if city in coords:
        lat, lon = coords[city]
        return {"lat": lat, "lon": lon}, city
    return None, None



def save_results(store, before, bodies, replace_export=False):
    """Записать в llm_places.json и файл для стенда только свои изменения —
    поверх свежей версии файлов. Раньше merge читал файл в начале и целиком
    перезаписывал в конце: pin, отработавший за эти полчаса, пропадал — так
    29.09 исчезли 54 точки, найденные Opus (arkh-18568 «Дом Рошке» снова
    встал на Пятигорск). before — снимок записей на старте (json-строки)."""
    changed = {k: v for k, v in store.items() if before.get(k) != json.dumps(v, ensure_ascii=False, sort_keys=True)}
    latest = json.load(open(STORE, encoding="utf-8")) if os.path.exists(STORE) else {}
    latest.update(changed)
    json.dump(latest, open(STORE, "w", encoding="utf-8", newline=chr(10)), ensure_ascii=False, indent=1)
    old = [] if replace_export or not os.path.exists(EXPORT) else json.load(open(EXPORT, encoding="utf-8"))
    fresh = {b["key"] for b in bodies}
    json.dump([b for b in old if b["key"] not in fresh] + bodies, open(EXPORT, "w", encoding="utf-8", newline=chr(10)),
              ensure_ascii=False, indent=1)
    return len(changed)


def snapshot(store):
    return {k: json.dumps(v, ensure_ascii=False, sort_keys=True) for k, v in store.items()}


def merge(args):
    import geocode_streets as g
    coords = g.city_coords()
    posts = {key_of(p): p for p in json.load(open(os.path.join(DATA, "posts_classified.json"), encoding="utf-8"))}
    for p in posts.values():
        for name, c in p.get("regions", {}).items():
            coords.setdefault(name, tuple(c))
    # центры всех областей — чтобы искать город в названной моделью области
    for r in json.load(open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8")).get("regions", []):
        coords.setdefault(r["n"], (r["lat"], r["lon"]))
    store = json.load(open(STORE, encoding="utf-8")) if os.path.exists(STORE) else {}
    before = snapshot(store)
    answers = []
    # после доработки — только новый файл, чтобы не переотправлять весь пилот
    for f in args.files or sorted(glob.glob(os.path.join(LLM_DIR, "answer-*.json"))):
        try:
            answers += json.load(open(f, encoding="utf-8"))
        except json.JSONDecodeError as e:
            print("не читается", f, e)
    try:
        token = api("/login", {"password": PASSWORD})["token"]
    except OSError:
        token = None  # локальный сервер не запущен — только файл для стенда
        print("локальный сервер не отвечает: предложения только в", EXPORT)
    sent = found = 0
    bodies = []
    for a in answers:
        k = a.get("key")
        # повторный ответ по посту «на доработке» заменяет прежний
        if k not in posts or (k in store and not args.redo):
            continue
        # подборка многих мест — одной точки у неё нет, в модерацию не шлём
        if a.get("digest") or not (a.get("city") or a.get("region")):
            store[k] = {**a, "point": None, "label": None, "skipped": "digest" if a.get("digest") else "unknown"}
            continue
        point, label = locate(a, coords, posts[k].get("text"))
        found += point is not None
        # ещё места поста («ДК «Подмосковье» повторяет ДК АЗЛК»): только
        # найденные точно — вторая точка «просто в Москве» ничего не даёт
        extra = []
        for other in (a.get("also") or [])[:3]:
            if not isinstance(other, dict):
                continue
            p2, l2 = locate({**other, "_post_region": a.get("region")}, coords, posts[k].get("text"))
            if p2 and " · " in (l2 or "") and p2 != point and all(p2 != e["point"] for e in extra):
                extra.append({"point": p2, "label": l2})
        # связанные места — сравнение, прообраз, другая работа архитектора
        # («Исаакиевский собор» в посте про Староярмарочный собор): не место
        # поста, на его карту не встают. Тоже только найденные точно
        related = []
        for other in (a.get("related") or [])[:3]:
            if not isinstance(other, dict):
                continue
            p2, l2 = locate({**other, "_post_region": a.get("region")}, coords, posts[k].get("text"))
            if (p2 and " · " in (l2 or "") and p2 != point
                    and all(p2 != e["point"] for e in extra + related)):
                related.append({"point": p2, "label": l2})
        store[k] = {**a, "point": point, "label": label, "extra": extra, "related": related}
        # в модерацию: текст — что решила модель и почему
        names = [label or a.get("city") or "место не определено"] + [e["label"] for e in extra]
        parts = [f"Модель: {'; '.join(names)}"]
        if related:
            parts.append("связанные места: " + "; ".join(e["label"] for e in related))
        if a.get("reason"):
            parts.append(a["reason"].rstrip(". "))
        if a.get("confidence"):
            parts.append(f"уверенность — {a['confidence']}")
        if a.get("address_unverified"):
            parts.append(f"адрес по памяти модели — «{a['address_unverified']}», по справочникам не проверен")
        body = {"key": k, "src": posts[k]["src"], "post": posts[k]["id"], "text": ". ".join(parts)[:1900],
                "from": "model", "label": label or "", **(point or {}),
                # кто разбирал: у ответов Groq модель записана в ответе, у
                # агентов в сессии — берётся из --model
                "model": model_title(a.get("model") or args.model),
                # что искать на Яндекс-карте в «Указать точку вручную», если
                # геокодер не нашёл: «улица Пределов Мечты, Орехово-Зуево»
                **({"query": search_query(a)} if search_query(a) else {}),
                **({"extra": [{**e["point"], "label": e["label"]} for e in extra]} if extra and point else {}),
                **({"related": [{**e["point"], "label": e["label"]} for e in related]} if related else {}),
                # «похоже на рекламу» — пометкой в карточке, скрыть решает админ
                **({"ad": a["ad"]} if isinstance(a.get("ad"), str) and a["ad"].strip() else {})}
        bodies.append(body)
        if token:
            api("/suggestions/model", body, token)
            sent += 1
    # те же предложения — файлом для стенда (см. deploy/post-suggestions.mjs);
    # новые ответы заменяют прежние по посту, остальные остаются в файле.
    # Без списка файлов (все answer-*.json) файл для стенда собирается заново
    save_results(store, before, bodies, replace_export=not args.files)
    print(f"ответов {len(answers)}, отправлено в модерацию {sent}, с точкой {found} -> {STORE}")


GENERIC_PLACES = {"исторический центр", "центр", "центр города", "старый город", "старая часть города",
                  "набережная", "главная площадь", "центральная площадь", "центральный парк"}


def local_retry(args):
    """Предложения модели с точкой на городе — кандидаты ещё раз, своими
    справочниками (tools/local_places.py: реестр наследия, выгрузка OSM), без
    сети. «парк-мемориал Свирская победа · Лодейное Поле» геокодер не нашёл,
    а в OSM парк записан как «Свирская Победа». Находки — файлом pin-out,
    применяются через `pin`."""
    import geocode_streets as g
    coords = g.city_coords()
    for r in json.load(open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8")).get("regions", []):
        coords.setdefault(r["n"], (r["lat"], r["lon"]))
    store = json.load(open(STORE, encoding="utf-8"))
    located = set()
    edits_path = os.path.join(DATA, "edits.json")
    if os.path.exists(edits_path):
        e = json.load(open(edits_path, encoding="utf-8"))
        located = {k for k, v in e.get("edits", {}).items() if v.get("location")} | set(e.get("hidden", {}))
    out = []

    def on_village(a):
        # «Березовый Рядок · Тверская область» — точка на селе, а храм в нём
        # («Церковь Спаса Преображения» в OSM) можно найти рядом
        loc = (a.get("locality") or "").strip()
        return loc and (a.get("label") or "").split(" · ")[0] == loc

    todo = [(k, a) for k, a in store.items()
            if a.get("point") and (" · " not in (a.get("label") or "") or on_village(a))
            and a.get("candidates") and not a.get("digest") and not a.get("ad") and k not in located]
    import local_places
    for k, a in todo:
        if on_village(a):
            here = (a["point"]["lat"], a["point"]["lon"])
            loc = a["locality"].strip()
            for item in candidates_of(a):
                if not item.startswith("obj:") or item[4:] == loc:
                    continue
                hit = local_places.pick(local_places.search(item[4:], here, 3, loc))
                if hit:
                    out.append({"key": k, "lat": hit["lat"], "lon": hit["lon"], "label": f"{item[4:]} · {loc}",
                                "source": "local", "confidence": "medium",
                                "note": f"свои справочники: {hit['name']} в 3 км от села"})
                    break
            continue
        city, region = (a.get("city") or "").strip(), (a.get("region") or "").strip()
        where = city if city in coords else (region if region in coords else None)
        if not where:
            continue
        for item in candidates_of(a):
            if not item.startswith("obj:") or item == "obj:" + city:
                continue
            name = item[4:]
            if g.ambiguous_dedication(where, item):
                continue
            hit = g.ask_local(where, name, coords[where])
            if hit and name.lower().replace("ё", "е") not in GENERIC_PLACES:
                out.append({"key": k, "lat": hit["lat"], "lon": hit["lon"], "label": f"{name} · {where}",
                            "source": "local", "confidence": "medium", "note": f"свои справочники: {name}"})
                break
    # Посты вовсе без точки, но с областью и объектом («музей под открытым
    # небом Малые Корелы · Архангельская область», «Ангарская деревня ·
    # Иркутская область»): геокодер искал в 150 км от центра области и не
    # дотягивался, а искатель Sonnet таких не получал — у них нет точки
    # (09.10, разбор ошибок отчётов). Ищем по всей области (800 км), но
    # точка обязана лежать в ней (region_ok).
    # только области из справочника регионов: для неизвестного имени
    # region_ok ничего не проверяет, а радиус — 800 км
    regions = {r["n"] for r in json.load(open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8")).get("regions", [])}
    bare = [(k, a) for k, a in store.items()
            if not a.get("point") and a.get("candidates") and not a.get("digest") and not a.get("ad")
            and k not in located and (a.get("region") or "").strip() in regions]
    g.REGION_CHECK = True
    for k, a in bare:
        region = a["region"].strip()
        for item in candidates_of(a):
            if not item.startswith("obj:"):
                continue
            # «Ангарская деревня · музей» — пометка после « · » мешает совпадению
            name = item[4:].split(" · ")[0].strip()
            if not name or name.lower().replace("ё", "е") in GENERIC_PLACES or name == region:
                continue
            if g.ambiguous_dedication(region, "obj:" + name):
                continue
            hit = g.ask_local(region, name, coords[region], radius=800)
            if hit:
                out.append({"key": k, "lat": hit["lat"], "lon": hit["lon"], "label": f"{name} · {region}",
                            "source": "local", "confidence": "medium",
                            "note": f"свои справочники: {name}, по всей области"})
                break
    json.dump(out, open(args.out, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print(f"предложений с точкой на городе: {len(todo)}, без точки с областью: {len(bare)}, "
          f"нашлось у себя: {len(out)} -> {args.out}")


def yandex_retry(args):
    """Предложения модели, где точка осталась на городе («Дом Штумпфа ·
    Омск» не нашёлся), — кандидаты модели ещё раз, одним Яндекс-геокодером:
    у Яндекса есть садовые товарищества, новые дома и организации, которых
    нет в OpenStreetMap. Находки — файлом того же вида, что у поиска Opus
    (pin-out), применяются через `pin`: с той же проверкой 60 км.
    Пары, которые Яндекс видел за 30 дней, не спрашиваем (yandex-asked.json)."""
    import datetime
    import geocode_streets as g
    coords = g.city_coords()
    for r in json.load(open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8")).get("regions", []):
        coords.setdefault(r["n"], (r["lat"], r["lon"]))
    store = json.load(open(STORE, encoding="utf-8"))
    located = set()
    edits_path = os.path.join(DATA, "edits.json")
    if os.path.exists(edits_path):
        e = json.load(open(edits_path, encoding="utf-8"))
        located = {k for k, v in e.get("edits", {}).items() if v.get("location")} | set(e.get("hidden", {}))
    fresh = (datetime.date.today() - datetime.timedelta(days=g.YANDEX_RECHECK_DAYS)).isoformat()
    asked = g.yandex_asked()
    out, requests = [], 0
    todo = [(k, a) for k, a in store.items()
            if a.get("point") and " · " not in (a.get("label") or "") and a.get("candidates")
            and not a.get("digest") and not a.get("ad") and k not in located]
    for k, a in todo:
        city, region = (a.get("city") or "").strip(), (a.get("region") or "").strip()
        where = city if city in coords else (region if region in coords else None)
        if not where:
            continue
        for item in candidates_of(a):
            if item in ("obj:" + city, "loc:" + city) or asked.get(g.key(where, item), "") >= fresh:
                continue
            if requests >= args.max:
                break
            is_obj, is_loc = item.startswith("obj:"), item.startswith("loc:")
            name = item[4:] if is_obj or is_loc else item
            hit = g.ask_yandex(where, name, coords[where], is_obj, is_loc, g.key(where, item))
            requests += 1
            if g.YANDEX_OFF:
                break
            # «исторический центр · Киров» — Яндекс знает такой «объект», но
            # это весь центр города: точнее города он не делает
            if hit and name.lower().replace("ё", "е") in GENERIC_PLACES:
                hit = None
            if hit:
                out.append({"key": k, "lat": hit["lat"], "lon": hit["lon"], "label": f"{name} · {where}",
                            "source": "yandex", "confidence": "medium", "note": f"Яндекс-геокодер: {item}"})
                print(f"  {k}: {name} · {where}", flush=True)
                break
        if g.YANDEX_OFF or requests >= args.max:
            break
    json.dump(asked, open(g.YANDEX_ASKED, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"постов с точкой на городе: {len(todo)}, запросов к Яндексу: {requests}, найдено: {len(out)} -> {args.out}")


# --- разбор через API Groq ---------------------------------------------
#
# Бесплатный тариф Groq: у каждой модели свой лимит — около 8 тыс. токенов
# в минуту и 200 тыс. в день. Поэтому посты шлём по несколько штук, а при
# отказе «rate limit» ждём, сколько просит сервер. Инструкция та же, что у
# агентов (tools/llm_prompt.md), только посты и примеры — в сообщении.
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# дни, когда посты уже уходили в Groq (см. tools/groq_daily.py)
GROQ_RUNS = os.path.join(LLM_DIR, "groq-runs.json")
GROQ_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".groq-key")

# куски инструкции для агента, которые в API читаются иначе: файлов нет
API_PROMPT_SWAPS = [
    ("Прочитай файл пачки `{batch}` (Read tool): JSON-массив постов",
     "В сообщении пользователя — `posts`: JSON-массив постов"),
    ("Прочитай и `{examples}` — примеры", "Там же — `examples`: примеры"),
    ("Запиши Write tool'ом в `{answer}` JSON-массив, по объекту на пост, в том же\nпорядке:",
     "Ответь JSON-объектом {\"answers\": [...]} — массив, по объекту на пост, в том же\nпорядке:"),
    ("- Никаких других файлов не создавай и не меняй. В конце ответь одной строкой:\n  сколько постов записано.",
     "- Отвечай только JSON, без пояснений вокруг."),
]


def api_prompt():
    text = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_prompt.md"), encoding="utf-8").read()
    for old, new in API_PROMPT_SWAPS:
        # инструкцию поменяли — поправь и замены, иначе модель станет искать файлы
        assert old in text, f"в llm_prompt.md нет куска: {old[:40]}…"
        text = text.replace(old, new)
    return text


def groq_call(model, system, user, key):
    body = {"model": model, "temperature": 0.2, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    while True:
        req = urllib.request.Request(GROQ_URL, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                                              "User-Agent": "archimap-llm"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.load(r)
            return json.loads(data["choices"][0]["message"]["content"]), data.get("usage", {})
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")
            if e.code != 429:
                raise RuntimeError(f"{e.code}: {msg[:300]}")
            # «Please try again in 7.5s» или «in 1m2.3s»; дневной лимит — часы
            m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s", msg)
            wait = (int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3))) if m else 30
            if wait > 900:
                raise RuntimeError(f"дневной лимит {model}: ждать {wait / 3600:.1f} ч")
            print(f"  лимит, жду {wait:.0f} с", flush=True)
            time.sleep(wait + 1)
        except (urllib.error.URLError, OSError) as e:
            # обрыв соединения (WinError 10054, таймаут): ночной прогон 28.09
            # упал на нём после 105 постов из 180 — ждём и спрашиваем снова
            fails = getattr(groq_call, "fails", 0) + 1
            groq_call.fails = fails
            if fails > 10:
                raise RuntimeError(f"сеть: {e}")
            print(f"  сеть: {e}, повтор через 30 с", flush=True)
            time.sleep(30)


def ask(args):
    key = open(GROQ_KEY_FILE, encoding="utf-8").read().strip()
    system = api_prompt()
    examples = json.load(open(os.path.join(LLM_DIR, "examples.json"), encoding="utf-8"))[-args.examples:]
    rows = []
    for f in args.batches:
        rows += json.load(open(f, encoding="utf-8"))
    # день отправки — для автозапуска (tools/groq_daily.py): если сегодня
    # посты в Groq уже уходили, вечером он их не шлёт
    runs = json.load(open(GROQ_RUNS, encoding="utf-8")) if os.path.exists(GROQ_RUNS) else {}
    runs.setdefault(time.strftime("%Y-%m-%d"), []).append({"posts": len(rows), "out": args.out, "at": time.strftime("%H:%M")})
    json.dump(runs, open(GROQ_RUNS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    out, tokens = [], 0
    for n in range(0, len(rows), args.chunk):
        part = rows[n:n + args.chunk]
        user = json.dumps({"examples": examples, "posts": part}, ensure_ascii=False)
        try:
            reply, usage = groq_call(args.model, system, user, key)
        except (RuntimeError, json.JSONDecodeError) as e:
            print(f"  посты {n + 1}–{n + len(part)}: {e}")
            if "дневной лимит" in str(e):
                break
            continue
        tokens += usage.get("total_tokens", 0)
        got = {a.get("key"): dict(a, model=args.model) for a in reply.get("answers", []) if isinstance(a, dict)}
        out += [got[r["key"]] for r in part if r["key"] in got]
        print(f"  {n + len(part)}/{len(rows)} · ответов {len(got)} · токенов {usage.get('total_tokens')}", flush=True)
        # ответ пишем после каждой пачки: оборвётся на лимите — сделанное останется
        json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{args.model}: ответов {len(out)} из {len(rows)}, токенов {tokens} -> {args.out}")
    # ни одного ответа (05–06.10: нет сети) — день не считается отправленным,
    # иначе вечерний прогон решит, что сегодня Groq уже работал
    if not out:
        runs = json.load(open(GROQ_RUNS, encoding="utf-8"))
        today = runs.get(time.strftime("%Y-%m-%d"), [])
        if today and today[-1].get("out") == args.out:
            today.pop()
            if not today:
                runs.pop(time.strftime("%Y-%m-%d"))
            json.dump(runs, open(GROQ_RUNS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def search_query(answer):
    """Первый кандидат модели и где искать — строка для поиска на Яндекс-карте.
    Непроверенный адрес модели — первым: по адресу Яндекс находит дом точно."""
    first = next((c for c in answer.get("candidates") or [] if (c or "").strip()), "").replace(" · ", ", ")
    if answer.get("address_unverified"):
        first = answer["address_unverified"]
    where = answer.get("locality") or answer.get("city") or answer.get("region") or ""
    if not first:
        return where
    return first if where and where.lower() in first.lower() else ", ".join(x for x in (first, where) if x)


def pin(args):
    """Точки, найденные вручную (Claude Opus через tools/lookup.py) для
    постов, где модель назвала объект верно, а геокодер нашёл только город.
    Файл — [{key, lat, lon, label, source, confidence, note}]. Точка дальше
    60 км от города ответа — подозрительна: такие не применяем, а печатаем."""
    import geocode_streets as g
    coords = g.city_coords()
    for r in json.load(open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8")).get("regions", []):
        coords.setdefault(r["n"], (r["lat"], r["lon"]))
    store = json.load(open(STORE, encoding="utf-8"))
    before = snapshot(store)
    bodies = []
    try:
        token = api("/login", {"password": PASSWORD})["token"]
    except OSError:
        # вечерний запуск без npm run dev: точки — в llm_places.json и файл
        # для стенда, в локальную модерацию уйдут при следующем merge/pin
        token = None
        print("локальный сервер не отвечает: точки только в", STORE, "и", EXPORT)
    done = 0
    for f in args.files:
        for p in json.load(open(f, encoding="utf-8")):
            k, a = p["key"], store.get(p["key"])
            if not a or p.get("lat") is None:
                continue
            point = {"lat": round(float(p["lat"]), 6), "lon": round(float(p["lon"]), 6)}
            # сверяем с городом ответа; если модель назвала только область,
            # село может быть далеко от её центра — не проверяем
            base = a.get("city") and ((a.get("point") and (a["point"]["lat"], a["point"]["lon"]))
                                      or coords.get(a["city"]))
            far = base and g.distance_km(base, (point["lat"], point["lon"])) > 60
            if far and not args.force:
                print(f"  {k}: {p['label']} — {g.distance_km(base, (point['lat'], point['lon'])):.0f} км от города, не беру")
                continue
            label = p["label"][:120]
            by = {"yandex": "yandex", "local": "local"}.get(p.get("source"), args.by)
            store[k] = {**a, "point": point, "label": label, "manual_lookup": True,
                        "pin_source": p.get("source"), "pinned_by": by}
            body = pin_body(k, store[k], args.model)
            if token:
                api("/suggestions/model", body, token)
            bodies = [b for b in bodies if b["key"] != k] + [body]
            done += 1
            print(f"  {k}: {label} ({p.get('source')}, {p.get('confidence')})")
    save_results(store, before, bodies)
    print(f"уточнено точек: {done}")


def pin_credit(by):
    """Кто нашёл точку — в подписи карточки: «Claude Haiku 4.5, точку
    уточнил Claude Sonnet 5.5»."""
    if by == "yandex":
        return "точку нашёл Яндекс-геокодер"
    if by == "local":
        return "точку нашли свои справочники (реестр наследия, OSM)"
    return f"точку уточнил {model_title(by or 'claude-sonnet')}"


def pin_body(k, a, default_model="claude-haiku"):
    """Предложение для модерации по уточнённой точке."""
    label, point = a["label"], {"lat": a["point"]["lat"], "lon": a["point"]["lon"]}
    parts = [f"Модель: {label}"]
    if a.get("reason"):
        parts.append(a["reason"].rstrip(". "))
    if a.get("confidence"):
        parts.append(f"уверенность — {a['confidence']}")
    return {"key": k, "src": k.split("-")[0], "post": int(k.split("-")[1]), "text": ". ".join(parts),
            "from": "model", "label": label, **point,
            "model": f"{model_title(a.get('model') or default_model)}, {pin_credit(a.get('pinned_by'))}",
            # вторые точки поста и связанные места — те же, что дал merge
            **({"extra": [{**e["point"], "label": e["label"]} for e in a["extra"]]} if a.get("extra") else {}),
            **({"related": [{**e["point"], "label": e["label"]} for e in a["related"]]} if a.get("related") else {}),
            **({"ad": a["ad"]} if isinstance(a.get("ad"), str) and a["ad"].strip() else {})}


def credits(args):
    """Подписать уже применённые точки: кто их нашёл. Раньше в карточке было
    «точку уточнил Claude» без модели. Восстанавливаем по файлам находок:
    задания pin-in-40…94 искал Opus, с 95-го — Sonnet; pin-manual и
    pin-region-fix — основная сессия (Opus); pin-local — свои справочники;
    pin-yandex — Яндекс-геокодер. Последний файл с ключом — главный."""
    store = json.load(open(STORE, encoding="utf-8"))
    before = snapshot(store)
    who = {}
    for f in sorted(glob.glob(os.path.join(LLM_DIR, "pin-*.json")), key=os.path.getmtime):
        name = os.path.basename(f)
        if name.startswith("pin-in-"):
            continue
        m = re.match(r"pin-(?:out|ok)-(\d+)", name)
        if m:
            by = "claude-opus" if int(m.group(1)) <= 94 else "claude-sonnet"
        elif name.startswith(("pin-manual", "pin-region")):
            by = "claude-opus"
        elif name.startswith("pin-local"):
            by = "local"
        elif name.startswith("pin-yandex"):
            by = "yandex"
        else:
            continue
        try:
            rows = json.load(open(f, encoding="utf-8"))
        except ValueError:
            continue
        for p in rows if isinstance(rows, list) else []:
            if isinstance(p, dict) and p.get("key"):
                who[p["key"]] = "yandex" if p.get("source") == "yandex" else by
    try:
        token = api("/login", {"password": PASSWORD})["token"]
    except OSError:
        token = None
    bodies, n = [], 0
    for k, a in store.items():
        if not a.get("manual_lookup") or not a.get("point") or not a.get("label"):
            continue
        by = who.get(k) or a.get("pinned_by") or "claude-sonnet"
        if a.get("pinned_by") == by:
            continue
        store[k] = {**a, "pinned_by": by}
        body = pin_body(k, store[k], args.model)
        if token:
            api("/suggestions/model", body, token)
        bodies.append(body)
        n += 1
    save_results(store, before, bodies)
    print(f"подписано точек: {n}")


# как модель подписана в карточке модерации
MODEL_TITLES = {
    "claude-haiku": "Claude Haiku 4.5",
    # агенты-искатели точек (tools/lookup.py) и основная сессия
    "claude-sonnet": "Claude Sonnet 5.5",
    # отдельные посты по жалобам админа разбирает основная сессия
    "claude-opus": "Claude Opus 5.5",
    "qwen/qwen3.8-27b": "Qwen 3.8 27B (Groq)",
    "openai/gpt-oss-120b": "GPT-OSS 120B (Groq)",
    "openai/gpt-oss-20b": "GPT-OSS 20B (Groq)",
}


def model_title(model):
    return MODEL_TITLES.get(model, model)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--batch", type=int, default=25)
    s.add_argument("--edits", help="edits.json (по умолчанию локальный data/edits.json)")
    s.add_argument("--examples", type=int, default=12, help="сколько ручных точек дать в примеры")
    m = sub.add_parser("merge")
    m.add_argument("--redo", action="store_true", help="заменить прежние ответы (после доработки)")
    m.add_argument("files", nargs="*", help="файлы ответов (по умолчанию все data/llm/answer-*.json)")
    m.add_argument("--model", default="claude-haiku",
                   help="кто писал ответы, если в них не указано (агенты в сессии — claude-haiku)")
    pn = sub.add_parser("pin", help="применить точки, найденные вручную (tools/lookup.py)")
    pn.add_argument("files", nargs="+")
    pn.add_argument("--model", default="claude-haiku")
    pn.add_argument("--force", action="store_true", help="брать и точки дальше 60 км от города")
    pn.add_argument("--by", default="claude-sonnet",
                    help="кто нашёл точки: claude-sonnet (агенты), claude-opus (основная сессия)")
    cr = sub.add_parser("credits", help="подписать применённые точки моделью, которая их нашла")
    cr.add_argument("--model", default="claude-haiku")
    a = sub.add_parser("ask", help="разобрать пачки через API Groq (ключ — tools/.groq-key)")
    a.add_argument("batches", nargs="+", help="data/llm/batch-NN.json")
    a.add_argument("--out", required=True, help="куда записать ответы: data/llm/answer-NN.json")
    a.add_argument("--model", default="openai/gpt-oss-120b")
    a.add_argument("--chunk", type=int, default=5, help="постов в одном запросе")
    a.add_argument("--examples", type=int, default=6, help="сколько примеров ручных точек слать")
    y = sub.add_parser("yandex", help="кандидаты предложений с точкой на городе — Яндекс-геокодером")
    y.add_argument("--max", type=int, default=250, help="не больше N запросов к Яндексу")
    y.add_argument("--out", default=os.path.join(LLM_DIR, "pin-yandex.json"))
    lc = sub.add_parser("local", help="кандидаты предложений с точкой на городе — своими справочниками")
    lc.add_argument("--out", default=os.path.join(LLM_DIR, "pin-local.json"))
    args = ap.parse_args()
    {"select": select, "merge": merge, "ask": ask, "pin": pin, "yandex": yandex_retry,
     "local": local_retry, "credits": credits}[args.cmd](args)


if __name__ == "__main__":
    main()
