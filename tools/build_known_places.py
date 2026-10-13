"""Словарь мест, уже найденных для одного поста, — для остальных постов.

Места повторяются: Севкабель назван в 16 постах, Музей одной картины — в трёх,
а точку для них искали один раз. Здесь собираются проверенные точки:
ручные точки админа с подписью (data/edits.json), находки агентов, которые
применял Claude (`manual_lookup` в llm_places.json), и точки модели из
Викидаты. Из подписи «Музей одной картины имени Г. В. Мясникова · Пенза»
получаются варианты названия («Музей одной картины имени…», «Музей одной
картины»), и разбор (classify.find_known_places) ставит точку в каждом посте,
где вариант встречается и назван тот же город.

Вариант берём, только если он отличает место: «Успенский собор» или «Дом
культуры» есть в десятках городов. Проверяем по корпусу: среди постов, где
вариант встречается и назван какой-нибудь город, доля постов с городом этой
точки должна быть не ниже DISTINCT_SHARE.

    python tools/build_known_places.py      -> data/known_places.json
"""
import json, os, re, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import classify as c

DATA = c.DATA
OUT = os.path.join(DATA, "known_places.json")
DISTINCT_SHARE = 0.7

# слова, из которых одних название не складывается: «Дом культуры»,
# «Успенский собор», «Музей истории города»
GENERIC = {
    "дом", "дворец", "музей", "церковь", "храм", "собор", "часовня", "монастырь",
    "усадьба", "особняк", "здание", "корпус", "станция", "метро", "вокзал", "парк",
    "сад", "сквер", "площадь", "улица", "проспект", "переулок", "набережная",
    "театр", "кинотеатр", "библиотека", "школа", "гимназия", "больница", "завод",
    "фабрика", "рынок", "мост", "башня", "памятник", "гостиница", "отель", "клуб",
    "центр", "культура", "история", "город", "городской", "краеведческий",
    "областной", "главный", "старый", "новый", "бывший", "большой", "малый",
    "имя", "святой", "св", "икона", "божий", "богородица", "пресвятой",
    "троицкий", "успенский", "никольский", "покровский", "воскресенский",
    "вознесенский", "преображенский", "богоявленский", "благовещенский",
    "рождественский", "спасский", "ильинский", "петропавловский", "казанский",
    "смоленский", "владимирский", "тихвинский", "введенский", "сретенский",
    "архангельский", "михайловский", "георгиевский", "знаменский", "троица",
    "дк", "жилой", "доходный", "купеческий", "торговый", "ряд", "водонапорный",
    "железнодорожный", "речной", "морской", "один", "два", "три",
    "метрополитен", "район", "округ", "микрорайон",
}
# «город Семёнов», «Басманный район» — уже есть в справочнике мест
PLACE_PREFIX = {"город", "район", "посёлок", "поселок", "село", "деревня", "станица", "микрорайон"}
TYPE_HEAD_RE = re.compile(r"(?i)^(?:улица|проспект|переулок|площадь|набережная|бульвар|шоссе|проезд|аллея)\b"
                          r"|\b(?:улица|проспект|переулок|площадь|набережная|бульвар|шоссе|проезд)$")
WORD_RE = re.compile(r"[А-ЯЁа-яёA-Za-z0-9]+(?:-[А-ЯЁа-яёA-Za-z0-9]+)*")


def variants(obj):
    """«Кинотеатр «Мир» (Империя грез)» -> «Кинотеатр Мир», «Империя грез»…"""
    out = []
    base = re.sub(r"\s*\([^)]*\)", "", obj).strip()
    out.append(base)
    out += re.findall(r"\(([^)]*)\)", obj)
    for v in list(out):
        out.append(v.split(",")[0])
        out.append(re.split(r"\s(?:имени|им\.)\s", v)[0])
    seen, res = set(), []
    for v in out:
        v = re.sub(r"[«»\"“”]", "", v).strip(" .,")
        if v and v.lower() not in seen:
            seen.add(v.lower())
            res.append(v)
    return res


def usable(words):
    """Вариант отличает место, а не называет тип: есть хоть одно не-общее
    слово, а одно слово — только незнакомое морфологии («Севкабель»)."""
    lemmas = [c.lemma(w) for w in words]
    own = [w for w, l in zip(words, lemmas) if l not in GENERIC and len(l) > 2]
    if not own:
        return False
    # «Московский метрополитен», «фахверковый дом»: из своих слов одни
    # прилагательные — это описание, а не имя. Нужно существительное или
    # слово, которого морфология не знает («Кул-Шариф», «ВДНХ»)
    if all(c.morph.parse(w.lower())[0].tag.POS in ("ADJF", "PRTF") for w in own):
        return False
    if len(words) == 1:
        w = words[0]
        return len(w) >= 6 and w[:1].isupper() and not c.morph.word_is_known(w.lower())
    return True


def sources():
    """(название, город, lat, lon, откуда) из проверенных точек."""
    out = []
    store = json.load(open(os.path.join(DATA, "llm_places.json"), encoding="utf-8"))
    for k, r in store.items():
        lab, pt = r.get("label") or "", r.get("point")
        if " · " not in lab or not pt:
            continue
        if not (r.get("manual_lookup") or pt.get("src") == "wikidata"):
            continue
        obj, city = lab.split(" · ", 1)
        out.append((obj, r.get("city") or city, pt["lat"], pt["lon"], k))
    edits = json.load(open(os.path.join(DATA, "edits.json"), encoding="utf-8")).get("edits", {})
    for k, e in edits.items():
        loc = (e or {}).get("location") or {}
        name = loc.get("name") or ""
        if " · " in name and loc.get("lat") is not None:
            obj, city = name.split(" · ", 1)
            out.append((obj, city, loc["lat"], loc["lon"], k))
    return out


def main():
    gaz = c.load_gazetteer()
    cities_known = {e["name"] for e in gaz.index.values() if e["scope"] == "ru"}
    cand = {}  # (лемма-кортеж, город) -> запись
    for obj, city, lat, lon, key in sources():
        if city not in cities_known or TYPE_HEAD_RE.search(obj.strip()):
            continue
        for v in variants(obj):
            words = WORD_RE.findall(v)
            if not words or len(words) > 6 or not usable(words):
                continue
            lem = tuple(c.lemma(w) for w in words)
            # улицы ищет find_streets, города и районы — справочник мест
            if TYPE_HEAD_RE.search(v) or any(l in c.STREET_TYPES for l in lem):
                continue
            if gaz.index.get(tuple(l for l in lem if l not in PLACE_PREFIX)):
                continue
            cand.setdefault((lem, city), {"name": v, "city": city, "lat": lat, "lon": lon,
                                          "words": words, "from": key})
    print("вариантов-кандидатов:", len(cand))

    # где каждый вариант встречается в корпусе и какие города названы рядом
    by_first = defaultdict(list)
    for (lem, city), e in cand.items():
        by_first[lem[0]].append((lem, city))
    posts = json.load(open(os.path.join(DATA, "posts_classified.json"), encoding="utf-8"))
    seen_in = defaultdict(lambda: [0, 0])  # (lem, city) -> [постов с городом точки, постов с любым городом]
    for p in posts:
        text = p.get("text") or ""
        toks = [c.lemma(w) for w in c.TOKEN_RE.findall(text)]
        named = {x["name"] for x in p.get("places") or [] if x.get("scope") == "ru"}
        named = {n.split(" · ")[-1] for n in named}
        hit = set()
        for i, t in enumerate(toks):
            for lem, city in by_first.get(t, ()):
                if tuple(toks[i:i + len(lem)]) == lem:
                    hit.add((lem, city))
        for lem, city in hit:
            if named:
                seen_in[(lem, city)][1] += 1
                if city in named:
                    seen_in[(lem, city)][0] += 1
    out = []
    for k, e in cand.items():
        own, anyc = seen_in.get(k, [0, 0])
        if anyc and own / anyc < DISTINCT_SHARE:
            continue
        e["posts"] = own
        out.append(e)
    out.sort(key=lambda e: (e["city"], e["name"]))
    json.dump(out, open(OUT, "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)
    print("мест в словаре:", len(out), "| встречаются в постах своего города:",
          sum(1 for e in out if e["posts"] > 1), "->", OUT)


if __name__ == "__main__":
    main()
