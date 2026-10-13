# -*- coding: utf-8 -*-
"""Раскладывает посты на три группы по упоминанию мест.

  ru      — упомянут российский город и/или улица
  foreign — упомянут только зарубежный город
  none    — географии в тексте нет

Русский язык склоняет топонимы, поэтому и текст, и справочник приводятся
к начальной форме через pymorphy3, а сравнение идёт по цепочкам лемм
(«в Нижнем Новгороде» -> ('нижний', 'новгород')).
"""
import functools, json, math, os, re, sys
from collections import Counter

import pymorphy3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from places_extra import (HISTORICAL, COUNTRIES, AMBIGUOUS, ADJECTIVES,
                          REGIONS, ALIASES, STREET_TYPES, LANDMARKS,
                          FOREIGN_LANDMARKS, SPELLINGS, NAMED_PLACES, BARE_STREETS)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
MAX_NGRAM = 4

morph = pymorphy3.MorphAnalyzer()
_lemma_cache = {}


def lemma(word):
    """Начальная форма слова.

    Короткие частицы в составных названиях («Ко Лан», «Ла Пас») морфология
    приводит к однобуквенному предлогу — от такой леммы толку нет, поэтому
    для них оставляем само слово.
    """
    w = word.lower()
    if w not in _lemma_cache:
        form = morph.parse(w)[0].normal_form.replace("ё", "е")
        _lemma_cache[w] = form if len(form) > 1 else w
    return _lemma_cache[w]


TOKEN_RE = re.compile("[А-ЯЁа-яёA-Za-z]+(?:[-’'][А-ЯЁа-яёA-Za-z]+)*")


def tokenize(text):
    """Токены с исходной формой — заглавная буква нужна как признак имени собственного."""
    return [(m.group(0), m.start()) for m in TOKEN_RE.finditer(text)]


# --- географический контекст -------------------------------------------------

PREPOSITIONS = {"в", "во", "из", "под", "на", "к", "до", "о", "об", "за", "близ",
                "около", "возле", "у", "по", "через", "между", "над", "вокруг"}
# слова, после которых идёт название населённого пункта
PLACE_WORDS = {"город", "городок", "г", "гор", "посёлок", "поселок", "пгт", "село",
               "деревня", "станица", "станция", "слобода", "хутор", "аул", "область",
               "край", "район", "округ", "губерния", "уезд", "республика",
               "окрестность", "пригород", "житель", "уроженец", "центр",
               "остров", "полуостров", "озеро", "река", "гора", "провинция",
               "залив", "бухта", "мыс", "долина", "ущелье", "перевал"}
# слова, после которых идёт название улицы, а не города:
# «проспект Мира» — это улица, а не туркменский городок Мир
STREET_WORDS = {"улица", "проспект", "набережная", "площадь", "переулок", "шоссе",
                "бульвар", "проезд", "тупик", "аллея", "мост", "вокзал", "станция",
                "метро", "храм", "собор", "церковь", "монастырь", "музей", "театр",
                "завод", "фабрика", "дворец", "дом", "усадьба", "парк", "сад"}

STREET_RE = re.compile(
    r"(?:\bул\.|\bулиц[аыеуой]\w*|\bпросп\.|\bпр-?кт\b|\bпроспект\w*|"
    r"\bпереул[оки]\w*|\bнаб\.|\bнабережн\w+|"
    r"\bбульвар\w*|\bб-р\b|\bшоссе\b|\bплощад[ьияеу]\w*|"
    r"\bпроезд\w*|\bтупик\w*|\bаллея\b|\bаллеи\b|\bмагистрал\w+|"
    r"\bмикрорайон\w*|\bквартал\w*)", re.IGNORECASE)

# «ул. Пятницкая», «Пятницкая улица», «улица Большая Дмитровка»
STREET_NAME_RE = re.compile(
    r"(?:(?:ул\.|улиц[аыеуой]|просп\.|проспект[ае]?|переулок|переулк[ае]|наб\.|"
    r"набережн\w+|бульвар[ае]?|шоссе|площад[ьияеу]|проезд[ае]?)\s+"
    r"((?:[А-ЯЁ][а-яё]+[-\s]?){1,3})"
    r"|((?:[А-ЯЁ][а-яё]+[-\s]?){1,3})\s+"
    r"(?:улиц[аыеуой]|проспект[ае]?|переул[оки]\w*|набережн\w+|бульвар[ае]?|шоссе|площад[ьияеу]))")


class Gazetteer:
    """Индекс «цепочка лемм -> место»."""

    def __init__(self):
        self.index = {}
        # все места под ключом, не только крупнейшее: «Зеленогорск» — и
        # город в Красноярском крае, и район Петербурга
        self.alts = {}
        self.max_len = 1

    def add(self, key_words, entry):
        key = tuple(lemma(w) for w in key_words)
        if not key or any(len(k) < 2 for k in key):
            return
        self._put(key, entry)
        # «Касимов» морфология приводит к имени «Касим», а «в Касимове» —
        # к «касимов»: по одной лемме город не находится ни в одной из форм.
        # Поэтому название кладём ещё и как есть, в нижнем регистре
        surface = tuple(w.lower().replace("ё", "е") for w in key_words)
        if surface != key and surface not in self.index:
            self._put(surface, entry)
        # «Тверская область»: морфология знает «Тверскую» как улицу и даёт
        # лемму «тверская», а «в Тверской области» — «тверской». Без второго
        # ключа область не находилась, и «Тверской» доставалось городу Тверь
        if len(key_words) >= 2 and key[-1] in ("область", "край", "округ"):
            adj = next((p.normal_form.replace("ё", "е") for p in morph.parse(key_words[0].lower())
                        if "ADJF" in p.tag), None)
            if adj and adj != key[0]:
                self._put((adj,) + key[1:], entry)

    def _put(self, key, entry):
        self.max_len = max(self.max_len, len(key))
        self.alts.setdefault(key, []).append(entry)
        # приоритет: у кого больше население, тот и занимает ключ
        cur = self.index.get(key)
        if cur is None or entry["pop"] > cur["pop"]:
            self.index[key] = entry

    def add_place(self, name, scope, lat, lon, cc, pop, aliases=(), canon=None,
                  lower_ok=False, parent=None):
        entry = {"name": canon or name, "scope": scope, "lat": lat, "lon": lon,
                 "cc": cc, "pop": pop, "lc": lower_ok, "parent": parent,
                 "generic": parent is not None and is_generic_phrase(name)}
        for variant in [name] + list(aliases):
            words = TOKEN_RE.findall(variant)
            if 1 <= len(words) <= MAX_NGRAM:
                self.add(words, entry)


def load_gazetteer():
    global COUNTRY_LEMMAS
    COUNTRY_LEMMAS = set(load_countries())
    with open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8") as f:
        raw = json.load(f)
    g = Gazetteer()
    for w in raw["world"]:
        g.add_place(w["n"], "foreign", w["lat"], w["lon"], w["cc"], w["pop"], w["a"])
    # спорные имена (село Париж под Челябинском) достаются тому, кто крупнее,
    # но у российских мест фора: канал прежде всего про Россию
    for r in raw["ru"]:
        g.add_place(r["n"], "ru", r["lat"], r["lon"], "RU", r["pop"] * 50 + 2000, r["a"])
    for name, (lat, lon) in REGIONS.items():
        g.add_place(name, "ru", lat, lon, "RU", 10 ** 8)
    for r in raw.get("regions", []):
        g.add_place(r["n"], "ru", r["lat"], r["lon"], "RU", 10 ** 8)
    # Соловки, Кижи, Валаам — как город средней руки: ключ за ними
    for name, (lat, lon, forms) in NAMED_PLACES.items():
        g.add_place(name, "ru", lat, lon, "RU", 5 * 10 ** 6, forms)
    # районы и метро идут последними и с маленьким весом: если название
    # занято городом, город важнее. «Киевская» так и остаётся Киевом —
    # разделить станцию и прилагательное по одной лемме всё равно нельзя
    for d in load_districts():
        if is_usable_district(d["n"]):
            g.add_place(d["n"], "ru", d["lat"], d["lon"], "RU", 3000,
                        parent=d.get("parent"))
    for name, (lat, lon) in LANDMARKS.items():
        foreign = name in FOREIGN_LANDMARKS
        g.add_place(name, "foreign" if foreign else "ru", lat, lon,
                    "" if foreign else "RU", 10 ** 7)

    by_name = {}
    for e in g.index.values():
        by_name.setdefault(e["name"], e)

    def clone(src_name, variant, lower_ok=False):
        cur = by_name.get(src_name)
        if cur:
            g.add_place(variant, cur["scope"], cur["lat"], cur["lon"], cur["cc"],
                        cur["pop"], canon=cur["name"], lower_ok=lower_ok)

    for old, new in HISTORICAL.items():
        clone(new, old)
    for short, full in ALIASES.items():
        clone(full, short)
    for adj, city in ADJECTIVES.items():
        clone(city, adj, lower_ok=True)
    for city, adj in derived_adjectives(raw["ru"]):
        clone(city, adj, lower_ok=True)
    for variant, canon in SPELLINGS.items():
        clone(canon, variant)
    return g


COUNTRY_LEMMAS = set()


def is_usable_district(name):
    """Отсекает районы и станции с именами обычных слов.

    Среди 4 тысяч названий из OSM попадаются «Институт», «Российская»,
    «Спортивная». Частотный стоп-лист их не ловит: в текстах канала они
    и со строчной частые, и с заглавной. Поэтому спрашиваем морфологию:
    слово оставляем, если оно ей известно как топоним («Беляево», «Арбат»)
    либо неизвестно вовсе — то есть точно не обычное слово.
    """
    words = TOKEN_RE.findall(name)
    if tuple(lemma(x) for x in words) in COUNTRY_LEMMAS:
        return False  # деревня Тайвань не должна заслонять страну
    if len(words) != 1:
        return True  # составные названия защищает требование заглавной буквы
    w = lemma(words[0])
    if w in AMBIGUOUS_LEMMAS:
        return False
    if not morph.word_is_known(w):
        return True
    return sum(p.score for p in morph.parse(w) if "Geox" in p.tag.grammemes) >= 0.5


def is_generic_phrase(name):
    """Составлено ли название целиком из обычных слов.

    «Гостиный двор», «Речной вокзал», «Красные Ворота» — настоящие
    московские и петербургские ориентиры, но такими же словами описывают
    гостиный двор в Костроме и речной вокзал в Твери. Морфология знает
    каждое слово как обычное, без пометки Geox, — по этому и отличаем.
    """
    words = TOKEN_RE.findall(name)
    if len(words) < 2:
        return False
    for w in words:
        lem = lemma(w)
        if not morph.word_is_known(lem):
            return False
        if any("Geox" in pp.tag.grammemes for pp in morph.parse(lem)):
            return False
    return True


def load_districts():
    path = os.path.join(DATA, "districts.json")
    if not os.path.exists(path):
        print("districts.json не найден — запустите build_districts.py")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def derived_adjectives(ru_cities, min_pop=15000):
    """«Владикавказ» -> «владикавказский».

    Список ADJECTIVES покрывает крупные города, но канал пишет и про
    Сыктывкар с Новокузнецком. Формы, которые морфология знает как обычные
    слова («советский», «братский», «русский»), отбрасываем.
    """
    out = []
    for c in ru_cities:
        if c["pop"] < min_pop or " " in c["n"] or "-" in c["n"]:
            continue
        base = c["n"].lower()
        if base.endswith("ск"):
            forms = [base + "ий"]
        elif base[-1] in "ьяа":
            forms = [base[:-1] + "ский"]
        else:
            forms = [base + "ский"]
        for f in forms:
            if len(f) < 9:
                continue
            # Раньше отбрасывали всё, что морфология знает, — и вместе с
            # «советским» теряли «выборгский», «тобольский», «угличский»:
            # «памятник коту во дворе Выборгского замка» оставался без
            # места. Теперь отбрасываем только ловушки: обычные слова, моря
            # и народы, имена и фамилии («пушкинский музей» — не Пушкин),
            # посвящения («никольский» — не Никольск)
            if morph.word_is_known(f) and (f.replace("ё", "е") in COMMON_CITY_ADJ
                                           or could_be_person(c["n"])):
                continue
            out.append((c["n"], f))
    return out


# прилагательные от городов, которые чаще значат не город
COMMON_CITY_ADJ = {
    "советский", "пролетарский", "партизанский", "октябрьский", "красноармейский", "ленинский",
    "калининский", "кировский", "дзержинский", "куйбышевский", "каспийский", "балтийский",
    "байкальский", "енисейский", "приволжский", "армянский", "татарский", "черкесский",
    "карачаевский", "каменский", "островский", "северский", "павловский", "александровский",
    "николаевский", "михайловский", "петровский", "константиновский", "покровский", "никольский",
    "знаменский", "георгиевский", "воскресенский", "борисоглебский", "богородский", "семеновский",
    "даниловский", "марковский", "тихвинский", "парнасский", "белогорский", "горнякский",
    "первоуральский", "южноуральский", "среднеуральский", "североуральский", "красноуральский",
    "новоалтайский", "светлоградский", "зеленоградский", "приозерский",
    # пиво «Жигулёвское», «ноябрьский ветер»; «Архангельское» — усадьба
    # под Москвой, «Архангельский собор» — в честь архангела
    "жигулевский", "ноябрьский", "архангельский",
}


ADJECTIVE_ENDINGS = ("ский", "цкий", "ный", "ый", "ий", "ой")


def load_countries():
    """Отдельно помечаем прилагательные: «французский» и «китайский»
    говорят о стиле, а не о месте — «Китайский домик» стоит в Царском Селе,
    а «Белорусский вокзал» в Москве."""
    idx = {}
    for name, (cc, aliases) in COUNTRIES.items():
        for v in [name] + aliases:
            words = TOKEN_RE.findall(v)
            if not words:
                continue
            is_adj = v.lower().endswith(ADJECTIVE_ENDINGS)
            idx[tuple(lemma(w) for w in words)] = {"name": name, "cc": cc, "adj": is_adj}
    return idx


AMBIGUOUS_LEMMAS = {lemma(w) for w in AMBIGUOUS}
# части света — не города, хотя в GeoNames такие названия встречаются
CONTINENTS = {"азия", "европа", "африка", "америка", "антарктида", "австралия"}
_risky_cache = {}
BANNED = set()


def build_stoplist(posts):
    """Слова, которые в самом корпусе почти всегда пишутся со строчной.

    «Дом», «Мир», «Так» — настоящие города в GeoNames, но в текстах канала
    это обычные слова, и совпадения по ним — почти всегда ложные.
    """
    lower, upper = Counter(), Counter()
    for p in posts:
        for tok, _ in tokenize(p["text"]):
            (upper if tok[0].isupper() else lower)[lemma(tok)] += 1
    banned = {w for w, n in lower.items() if n >= 20 and n > 3 * upper[w]}
    return banned


def is_risky(word):
    """Слово опасно, если морфология не уверена, что это топоним.

    pymorphy взвешивает разборы: у «Твери» весь вес на Geox, а у «Дома»,
    «Луги» или «Пушкина» его перетягивают обычное слово и фамилия. Такие
    совпадения засчитываем только при явном географическом контексте.
    """
    if word not in _risky_cache:
        if word in AMBIGUOUS_LEMMAS:
            _risky_cache[word] = True
        elif not morph.word_is_known(word):
            # Слова, которого нет в словаре, не может быть обычным словом.
            # Раньше оно тоже считалось спорным — и «Паттайя» без предлога
            # рядом не засчитывалась, как и почти вся зарубежная география.
            _risky_cache[word] = False
        else:
            parses = morph.parse(word)
            geo = sum(p.score for p in parses if "Geox" in p.tag.grammemes)
            _risky_cache[word] = geo < 0.5
    return _risky_cache[word]


def is_abbreviation(word):
    """Две заглавные подряд — признак сокращения: БАМ, ВДНХ, ЛОНИИС."""
    ups = sum(1 for ch in word if ch.isupper())
    return ups >= 2


_modifier_cache = {}


def is_modifier(word, next_word, next_lemma):
    """Прилагательное ли это при следующем существительном.

    «Рыбацкая деревня в Рязанской области» цеплялась за петербургское
    Рыбацкое: по лемме «рыбацкий» они неотличимы. Отличает грамматика —
    в описании прилагательное согласовано с существительным по роду,
    числу и падежу, а название города рядом с существительным не
    согласуется: «Коломенское — усадьба» (средний род против женского).
    """
    key = (word.lower(), next_word.lower())
    if key in _modifier_cache:
        return _modifier_cache[key]

    result = False
    # «Васильевского острова в Петербурге»: «в» морфология знает и как
    # существительное (сокращение «век») — предлоги и короткие служебные
    # слова описанием быть не могут
    if len(key[1]) <= 2 or is_function_word(key[1]):
        _modifier_cache[key] = False
        return False
    adj_parses = [p for p in morph.parse(key[0]) if p.tag.POS == "ADJF"]
    if adj_parses:
        # список слов вроде «шоссе» и «кольцо» оставлен запасным
        # вариантом: у несклоняемых существительных падежа не узнать
        if next_lemma in STREET_TYPES:
            result = True
        else:
            for pa in adj_parses:
                for pn in morph.parse(key[1]):
                    if pn.tag.POS != "NOUN":
                        continue
                    if pa.tag.case != pn.tag.case or pa.tag.number != pn.tag.number:
                        continue
                    if pa.tag.number == "sing" and pa.tag.gender != pn.tag.gender:
                        continue
                    result = True
                    break
                if result:
                    break
    _modifier_cache[key] = result
    return result


_person_cache = {}


def is_person_name(word):
    """«Александр», «Пётр» — прежде всего имена людей, а не города."""
    if word not in _person_cache:
        parses = morph.parse(word)
        score = sum(p.score for p in parses
                    if {"Name", "Patr"} & set(p.tag.grammemes))
        _person_cache[word] = score >= 0.5
    return _person_cache[word]


def standalone_in_title(text, tokens, i, size):
    """Слово в заголовке стоит отдельно: «Новостройка — Пересвет», «Грязовец».
    Для слов, которые бывают фамилиями, этого мало, чтобы считать заголовок
    местом: «Сходили в гости к самому интересному Ленину в Москве»."""
    if not in_title_line(text, tokens[i][1]):
        return False
    first = text.split("\n", 1)[0]
    name = " ".join(t for t, _ in tokens[i:i + size])
    parts = [x.strip() for x in re.split(r"[—–,.:;()!?]| - ", first)]
    return name in parts


def person_city_context(text, tokens, lemmas, i, size):
    """Контекст для слова, которое бывает фамилией: «в Гагарине», «город
    Гагарин», «в подмосковном Королёве» — в пределах одного предложения:
    в «…Куйбышевской области. Гагарин здесь отдыхал» область — не про него."""
    def joined(j):  # между токеном j и следующим нет конца предложения
        return not re.search(r"[.!?\n]", text[tokens[j][1] + len(tokens[j][0]):tokens[j + 1][1]])
    if i > 0 and joined(i - 1):
        left = lemmas[i - 1]
        if left in PREPOSITIONS or left in PLACE_WORDS:
            return True
        # «в подмосковном Королёве»: между предлогом и городом прилагательное
        if (i > 1 and joined(i - 2) and lemmas[i - 2] in PREPOSITIONS
                and not tokens[i - 1][0][0].isupper()
                and ADJ_FORM_RE.search(tokens[i - 1][0].lower())):
            return True
    if i + size < len(tokens) and joined(i + size - 1):
        return lemmas[i + size] in PLACE_WORDS
    return False


def in_title_line(text, pos):
    """Первая строка поста, если она короткая, — заголовок: «Новостройка —
    Пересвет», «Луга». Название в заголовке — это место поста."""
    end = text.find("\n")
    if end == -1 or end > 80:
        return False
    return pos < end


def standalone_one_line(text, tokens, i, size, is_place):
    """Пост из одной короткой строки, где название стоит отдельной частью:
    «Медгородок — Тольятти» (09.10, проверка «без географии» моделью). Вся
    строка как заголовок — слишком щедро: «Ленин, указующий путь к
    коворкингу», «Ван Гог и Дали убирают листья» — не места, поэтому для
    фамилий это правило не действует (см. find_places). И подпись через
    запятую — только если рядом тоже место («Мирный, Якутия, 1970»), а не
    «Калачи, видимо»."""
    line = text.strip()
    if "\n" in line or len(line) > 80:
        return False
    name = " ".join(t for t, _ in tokens[i:i + size])
    if re.search(r"\s[—–-]\s", line):
        parts = [x.strip() for x in re.split(r"\s[—–-]\s|[.!?]", line)]
        return name in parts and len([p for p in parts if p]) > 1
    parts = [x.strip() for x in re.split(r"[,.:;()!?]", line)]
    return name in parts and any(is_place(x) for x in parts if x and x != name)


def has_geo_context(lemmas, i, size):
    """Слева предлог или слово вроде «город», справа «область»/«район».

    Слово «улица» или «музей» слева, наоборот, отменяет совпадение:
    дальше идёт название объекта, а не города.
    """
    left = lemmas[i - 1] if i > 0 else ""
    right = lemmas[i + size] if i + size < len(lemmas) else ""
    if left in STREET_WORDS:
        return False
    return left in PREPOSITIONS or left in PLACE_WORDS or right in PLACE_WORDS


def from_place(text, tokens, lemmas, i):
    """«Начнём с Мурома»: «с» + родительный падеж топонима — откуда, то есть
    место. «С» нет в PREPOSITIONS: «с Пушкиным», «с Королёвым» — люди, но это
    творительный падеж, его здесь не берём (09.10, «Муром» морфология знает и
    как фамилию)."""
    if i == 0 or lemmas[i - 1] not in ("с", "со"):
        return False
    # «с. Казаково» — сокращение «село», а не предлог
    if "." in text[tokens[i - 1][1] + len(tokens[i - 1][0]):tokens[i][1]]:
        return False
    return any("gent" in p.tag and "Geox" in p.tag for p in morph.parse(tokens[i][0].lower()))


_alts_cache = {}


def lemma_alts(word):
    """Другие начальные формы слова, кроме первой: «Сергиевом» — «сергиево»
    первым разбором, а в справочнике «Сергиев Посад»; «Лиепае» — «лиепай»
    вместо «лиепая». Пробуем, только если по первой форме ничего не нашлось.

    Только разборы-топонимы (Geox), а в составном названии ещё и
    прилагательные («Сергиевом» → «сергиев»): иначе любой разбор обычного
    слова находил село — «Дворец» → Дворцы, «Они» → Ним, «Данные» → Дали."""
    w = word.lower()
    if w not in _alts_cache:
        first = lemma(word)
        geo, adj = [], []
        for p in morph.parse(w):
            f = p.normal_form.replace("ё", "е")
            if f == first or len(f) < 2:
                continue
            if "Geox" in p.tag:
                geo.append(f)
            elif p.tag.POS == "ADJF":
                adj.append(f)
        # у одного слова запасная форма — только если и первый разбор
        # топоним («Лиепае»): у «Они» тоже есть разбор-топоним (Ним), у
        # «Данные» — Дали, у «Еду», «План», «Рекорд» — свои сёла
        if "Geox" not in morph.parse(w)[0].tag:
            geo = []
        _alts_cache[w] = (list(dict.fromkeys(geo)), list(dict.fromkeys(adj)))
    return _alts_cache[w]


# Слова, после которых место названо для сравнения, а не как место поста:
# «застройка напоминает архангельские бараки Соломбалы», «похож на
# петербургский доходный дом». Сравнение действует в пределах нескольких
# слов и одного предложения.
COMPARE_VERBS = {"напоминать", "похожий", "похоже", "походить", "наподобие",
                 "словно", "будто", "сродни", "подражание", "двойник", "копия",
                 "аналог", "вроде", "назвать", "называть", "прозвать",
                 "окрестить", "величать", "именовать", "прозвище",
                 # «догнать и перегнать Москву» — соперничество, не место
                 "догнать", "перегнать", "соперничать", "конкурировать", "тягаться",
                 # «изначально город назывался Городец Мещерский» — старое имя
                 "называться", "именоваться", "переименовать", "прежде"}
ORIGIN_VERBS = {"привезти", "привозить", "прислать", "присылать", "приехать",
                "приезжать", "переехать", "выписать", "доставить", "доставлять",
                "пригласить", "приглашать", "перевезти", "вернуться", "уехать", "бежать"}
WRITTEN_VERBS = {"написать", "нарисовать", "сочинить"}
ORIGIN_NOUNS = {"архитектор", "мастер", "специалист", "инженер", "художник",
                "скульптор", "гость", "проект", "уроженец", "выходец", "купец",
                "переселенец", "эмигрант", "делегация", "бригада"}
# «под боком у Москвы», «рядом с Новосибирском», «в 20 км от Твери» —
# город назван как ориентир по соседству, а место поста другое
NEAR_WORDS = {"рядом", "недалеко", "неподалеку", "неподалёку", "бок", "боком", "км",
              "километр", "вблизи", "поблизости", "окрестность", "пригород"}
# «русский Марбург», «северная Венеция», «сибирские Афины» — прозвище:
# город назван, чтобы сравнить с ним другой
# после прилагательного от города — посвящение храма: «Казанский собор»,
# «Смоленская церковь», «Владимирская икона». Только для имён чудотворных
# икон и святых: «Калининградский собор», «калужский кафедральный собор»,
# «Миланский собор» — это соборы самих городов
DEDICATION_WORDS = {"собор", "церковь", "храм", "икона", "часовня", "монастырь",
                    "пустынь", "придел", "колокольня", "образ"}
DEDICATION_ADJ = {"казанский", "смоленский", "владимирский", "тихвинский", "иверский",
                  "феодоровский", "федоровский", "донской", "почаевский", "толгский",
                  "курский", "ченстоховский", "грузинский", "иерусалимский",
                  # Архангельский собор — в честь архангела Михаила
                  "архангельский"}
# «им» не берём: «перед Казанским собором» — единственное число
REGIONAL_ADJ = {"подмосковный", "сибирский", "уральский", "волжский"}
# инициалы перед фамилией: «С. В. Шервуда», «А.Г. Гагарину»
INITIAL_BEFORE_RE = re.compile(r"(?<![А-ЯЁа-яё])[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.\s?)?$")
PLURAL_ADJ_RE = re.compile(r"(?:ие|ые|их|ых|ими|ыми)$")
NICKNAME_ADJ = {"русский", "российский", "северный", "южный", "восточный",
                "сибирский", "уральский", "волжский", "подмосковный", "местный",
                "наш", "свой", "второй", "маленький"}
# «в духе московского ампира», «на петербургский манер» — только вплотную:
# в «особняке в стиле модерн в Самаре» Самара — настоящее место
COMPARE_NEAR = {"дух", "манер", "манера", "стиль", "характер", "образец"}
COMPARE_WINDOW = 7
LOCALITY_WORDS = {"посёлок", "поселок", "село", "деревня", "хутор", "станица",
                  "слобода", "микрорайон", "остров"}
REGION_WORDS = {"губерния", "область", "обл", "край", "уезд", "район", "округ",
                "епархия", "земля"}
# кавычки оборот не обрывают: «назвал его „русским Марбургом“»
SENTENCE_END_RE = re.compile(r"[.!?;:\n()]")


# Слова, после которых в кавычках идёт название произведения или
# заведения, а не место: фильм «Инкогнито из Петербурга», кафе «Москва».
# Без такого слова кавычки — это цитата или топоним («Воронежское море»),
# и место в них настоящее.
TITLE_WORDS = {"фильм", "кинофильм", "картина", "сериал", "книга", "роман",
               "повесть", "рассказ", "поэма", "стихотворение", "песня", "альбом",
               "спектакль", "опера", "балет", "пьеса", "журнал", "газета",
               "кафе", "ресторан", "бар", "магазин", "гостиница", "отель",
               "кинотеатр", "выставка", "фестиваль", "проект", "конкурс",
               "бренд", "марка", "сорт", "торт", "конфета", "папироса",
               "поезд", "теплоход", "корабль", "самолёт", "самолет", "клуб",
               # СНТ «Ленинградский Петрозавод» — садоводство, а не Петербург
               "снт", "днт", "садоводство", "товарищество", "колхоз", "совхоз",
               "завод", "комбинат", "предприятие", "жк"}
TITLE_RE = re.compile(r"([А-ЯЁа-яё]+)[  ]*[«\"][^»\"]*$")


def in_title(text, pos):
    """Внутри ли названия в кавычках: «фильм „Инкогнито из Петербурга“»."""
    before = text[max(0, pos - 200):pos]
    opened = before.count("«") > before.count("»") or before.count('"') % 2 == 1
    if not opened:
        return False
    m = TITLE_RE.search(before)
    return bool(m) and lemma(m.group(1)) in TITLE_WORDS


def is_city_form(word, city):
    """«с Новосибирском»: морфология первым разбором видит здесь
    прилагательное «новосибирский», хотя это падежная форма самого города.
    Если среди разборов есть форма названия, слово — существительное."""
    target = city.lower().replace("ё", "е")
    return any(pa.normal_form.replace("ё", "е") == target
               for pa in morph.parse(word.lower()))


# «В Санкт-Петербурге есть несколько аналогичных домов» — маркер сравнения
# стоит после названия; ищем его вперёд до конца предложения
COMPARE_AFTER = {"аналогичный", "подобный", "похожий", "идентичный", "близнец",
                 "двойник", "копия", "повторять", "напоминать"}


def compared_after(text, tokens, lemmas, i):
    end = tokens[i][1] + len(tokens[i][0])
    for j in range(i + 1, min(i + 8, len(tokens))):
        if SENTENCE_END_RE.search(text[end:tokens[j][1]]):
            return False
        if lemmas[j] in COMPARE_AFTER:
            return True
    return False


def in_quotes_not_first(text, pos):
    before = text[max(0, pos - 200):pos]
    opened = max(before.rfind("«"), -1)
    if opened == -1 or before.rfind("»") > opened:
        return False
    # между открывающей кавычкой и местом есть слова
    return bool(TOKEN_RE.search(before[opened + 1:]))


def written_in(lemmas, i):
    """«Картина написана в блокадном Ленинграде» — где создано произведение,
    а не где оно теперь: пост про пензенский Музей одной картины. Между «в»
    и городом допускаем одно слово."""
    k = i - 1 if i > 0 and lemmas[i - 1] in ("в", "во") else i - 2
    return k > 0 and lemmas[k] in ("в", "во") and lemmas[k - 1] in WRITTEN_VERBS


def in_comparison(text, tokens, lemmas, i, scope=None):
    """Названо ли место i внутри оборота сравнения, прозвища или кавычек."""
    start = tokens[i][1]
    if compared_after(text, tokens, lemmas, i):
        return True
    # «русский Марбург», «подмосковная Швейцария» — прозвище. Но «в
    # подмосковном Электрогорске», «в сибирском Томске» — просто место:
    # прилагательное края перед российским городом
    if i > 0 and lemmas[i - 1] in NICKNAME_ADJ and not (scope == "ru" and lemmas[i - 1] in REGIONAL_ADJ):
        return True
    if i > 0 and lemmas[i - 1] in NEAR_WORDS:
        return True
    # откуда, а не где: «Из Ленинграда привозят проект», «архитектор из
    # Москвы», «приехал из Петербурга»
    if i > 0 and lemmas[i - 1] == "из":
        if i > 1 and lemmas[i - 2] in ORIGIN_NOUNS | ORIGIN_VERBS:
            return True
        for j in range(i + 1, min(i + 3, len(tokens))):
            if lemmas[j] in ORIGIN_VERBS:
                return True
    # «Немецкая слобода, а не Петербург», «не в Москве» — отрицание
    if i > 0 and lemmas[i - 1] == "не":
        return True
    if i > 1 and lemmas[i - 2] == "не" and lemmas[i - 1] in PREPOSITIONS:
        return True
    if i > 1 and lemmas[i - 1] in ("с", "от", "у", "со") and lemmas[i - 2] in NEAR_WORDS:
        return True
    # «дорога между Санкт-Петербургом и Москвой» — отрезок пути, а не место:
    # слабы оба города, и тот, что сразу после «между», и тот, что после «и»
    if i > 0 and lemmas[i - 1] == "между":
        return True
    if i > 1 and lemmas[i - 1] == "и" and "между" in lemmas[max(0, i - 6):i - 1]:
        return True
    # «по пути в Петербург», «по дороге на Москву» — направление, не место
    if i > 1 and lemmas[i - 1] in ("в", "во", "на", "до") and lemmas[i - 2] in (
            "путь", "дорога", "направление", "трасса", "поезд", "рейс"):
        return True
    if written_in(lemmas, i):
        return True
    if in_title(text, start):
        return True
    # «Правительство Ленинградской области», «Дни Башкортостана в
    # Санкт-Петербурге» на баннере: место в кавычках, но не первым словом —
    # часть надписи или названия. Место с самого начала кавычек
    # («Воронежское море») остаётся местом
    if in_quotes_not_first(text, start):
        return True
    for back in range(1, COMPARE_WINDOW + 1):
        j = i - back
        if j < 0:
            break
        # граница предложения обрывает оборот
        if SENTENCE_END_RE.search(text[tokens[j][1] + len(tokens[j][0]):start]):
            break
        w = lemmas[j]
        if w in COMPARE_VERBS:
            return True
        if w in COMPARE_NEAR and back <= 2:
            return True
        # «как в Петербурге» — сравнение, а «как в Москве, так и в
        # Петербурге» — перечисление
        if w == "как" and back == 2 and lemmas[i - 1] in PREPOSITIONS:
            tail = text[start:start + 80].lower()
            return "так и" not in tail
    return False


def find_places(text, gaz, countries):
    tokens = tokenize(text)
    lemmas = [lemma(t) for t, _ in tokens]
    caps = [t[0].isupper() for t, _ in tokens]
    n = len(tokens)
    places, found_countries = [], []
    # «творение С. В. Шервуда… Младший Шервуд, как и отец»: фамилия, раз
    # названная с инициалами, — фамилия и дальше, а не английский Шервуд
    # соседняя часть подписи — место: «Мирный, Якутия», «Родники, Ивановская область»
    def is_place(part):
        words = TOKEN_RE.findall(part)
        key = tuple(lemma(w) for w in words)
        return bool(words) and words[0][0].isupper() and (
            key in gaz.index or key in countries or any(k in REGION_WORDS for k in key))
    surnames = {lemmas[j] for j in range(2, n)
                if caps[j] and INITIAL_BEFORE_RE.search(text[max(0, tokens[j][1] - 8):tokens[j][1]])}
    i = 0
    while i < n:
        hit = None
        for size in range(min(gaz.max_len, n - i), 0, -1):
            key = tuple(lemmas[i:i + size])
            # слово в тексте — ровно название из справочника, а по лемме нашёлся
            # бы другой город: точное важнее. «Печоры, Псковская область»:
            # лемма «печора» — Печора в Коми. Только при таком споре: без него
            # точное слово находило «Руки», «Осаки», «Гори» (09.10). И только
            # для названий во множественном числе («Сокольники», «Текстильщики»
            # — лемма сводит их к селу Сокольник), российских (иначе финская
            # Кеми вместо Кеми): у «Красное», «Казанское», «Смоленская»
            # лемма-прилагательное — так и надо, там своя проверка
            if size == 1 and key in gaz.index and caps[i]:
                exact = (tokens[i][0].lower().replace("ё", "е"),)
                alt = gaz.index.get(exact)
                # по лемме — то же место («Кресты» под «крест» и «кресты» —
                # одна деревня): не подменяем, иначе обходим фильтры, после
                # которых находилась тюрьма «Кресты» в Петербурге
                # «Печоры», «Вачи» — это и родительный падеж от Печоры, Вачи:
                # «в 4 километрах от Вачи» — Вача. Множественное берём, только
                # где слово стоит само: в начале, после запятой или тире,
                # после «село», «в», «на» («Печоры, Псковская область»)
                gap = text[tokens[i - 1][1] + len(tokens[i - 1][0]):tokens[i][1]] if i > 0 else ""
                alone = (i == 0 or re.search(r"[,.;:—–(\n]", gap)
                         or lemmas[i - 1] in PLACE_WORDS or lemmas[i - 1] in ("в", "во", "на"))
                if (alone and exact != key and alt and alt is not gaz.index[key]
                        and alt["scope"] == "ru" and not alt["lc"]
                        and re.search(r"[иы]$", exact[0])
                        and alt["name"].lower().replace("ё", "е") == exact[0]):
                    key = exact
            entry = gaz.index.get(key)
            # «в Иванове», «в Кемерове»: морфология читает фамилию Иванов, и
            # город не находится. Пробуем название на -ово/-ино, если такой
            # город есть — пост про кинотеатр «Центральный» оставался без места
            if (entry is None and size == 1 and caps[i] and i > 0 and lemmas[i - 1] in ("в", "во", "о", "об")
                    and re.search(r"(?:ов|ев|ин|ын)е$", tokens[i][0].lower())):
                alt = gaz.index.get((tokens[i][0].lower().replace("ё", "е")[:-1] + "о",))
                if alt and alt["scope"] == "ru" and not alt["lc"]:
                    entry = alt
            # «в Сергиевом Посаде», «в Лиепае»: первая начальная форма не та,
            # что в справочнике, — пробуем другие разборы слов с заглавной
            if entry is None and caps[i]:
                for j in range(i, i + size):
                    if not caps[j]:
                        continue
                    geo_forms, adj_forms = lemma_alts(tokens[j][0])
                    for alt_form in geo_forms + (adj_forms if size > 1 else []):
                        alt_key = key[:j - i] + (alt_form,) + key[j - i + 1:]
                        if alt_key in gaz.index:
                            key, entry = alt_key, gaz.index[alt_key]
                            break
                    if entry is not None:
                        break
            if entry is None:
                continue
            # но «в Королёве… проходная корпорации имени С. П. Королёва» —
            # город: рядом география
            if size == 1 and key[0] in surnames and not person_city_context(text, tokens, lemmas, i, size):
                continue
            # топоним в русском тексте пишется с заглавной; исключение —
            # прилагательные вроде «московский»
            if not caps[i] and not entry["lc"]:
                continue
            # «Рыбацкая деревня», «Садовое кольцо», «Тверской бульвар» —
            # это описания, а не топонимы: прилагательное с согласованным
            # существительным. У названий городов такой формы нет —
            # «Москва, улица Пятницкая» — два существительных подряд,
            # и совпадение остаётся в силе.
            if i + size < n:
                nxt_word, nxt_lemma = tokens[i + size][0], lemmas[i + size]
                if entry["lc"]:
                    # прилагательное-город («московский дворик») только так
                    # и выглядит, для него проверяем лишь узкий список:
                    # «Тверской бульвар» — улица, а не город Тверь
                    if nxt_lemma in STREET_TYPES:
                        continue
                    # «Казанский собор», «церковь Казанской иконы», «Смоленский
                    # кафедральный собор» — посвящение, а не город: в
                    # Шамордино Казанский собор ставил пост в Казань
                    # множественное число — не посвящение: «ростовские храмы»
                    if size == 1 and key[0] in DEDICATION_ADJ and not PLURAL_ADJ_RE.search(tokens[i][0].lower()) and (
                            nxt_lemma in DEDICATION_WORDS or (
                                i + size + 1 < n and ADJ_FORM_RE.search(nxt_word.lower())
                                and lemmas[i + size + 1] in DEDICATION_WORDS)):
                        continue
                # только у однословных: у «Краснодарского края» первое слово
                # определяет «края», а не следующий за названием «Хутор», —
                # проверка отбрасывала край и ставила город Краснодар; а у
                # «Бирюлёво Восточное» последнее слово — часть имени
                elif size == 1 and is_modifier(tokens[i][0], nxt_word, nxt_lemma):
                    continue
            # «Коммунистическую и Октябрьский проспект»: город-прилагательное
            # мужского рода («Коммунистический», «Октябрьский») в тексте
            # склоняется в мужском роде — «в Октябрьском». Женская форма —
            # это улица или станция, а не город
            if (size == 1 and not entry["lc"] and re.search(r"(?:ий|ый|ой)$", entry["name"])
                    and re.search(r"(?:ая|ую|яя|юю)$", tokens[i][0].lower())):
                continue
            # «Анны Монс», «Ивана Фомина»: имя, за которым идёт фамилия с
            # заглавной, — человек, а не город Анна
            # («город Анна Воронежской области» — это город: перед ним слово
            # «город», а после — область)
            if (i + size < n and caps[i + size] and is_person_name(key[0])
                    and not text[tokens[i][1] + len(tokens[i][0]):tokens[i + size][1]].strip()
                    and not (i > 0 and lemmas[i - 1] in PLACE_WORDS)
                    and not (i + size + 1 < n and lemmas[i + size + 1] in REGION_WORDS)):
                continue
            # «Юрий Гагарин», «князю А.Г. Гагарину», «Сергей Королёв» — перед
            # фамилией имя или инициалы, значит это человек, а не город
            # Область фамилией не бывает: «село Великая Топаль Брянской
            # области» — морфология знает «Топаль» как имя («Топаля»), и
            # «Брянской области» терялась, а «Брянской» уходила в Брянск
            if i > 0 and not entry["lc"] and not any(w in REGION_WORDS for w in key) and (
                    (len(tokens[i - 1][0]) == 1 and tokens[i - 1][0].isupper()
                     and text[tokens[i - 1][1] + 1:tokens[i - 1][1] + 2] == ".")
                    # имя вплотную, без знаков между: «памятник князю
                    # Владимиру, Киев» — Киев, а не человек (09.10)
                    or (caps[i - 1] and is_person_name(lemmas[i - 1])
                        and not text[tokens[i - 1][1] + len(tokens[i - 1][0]):tokens[i][1]].strip())):
                continue
            # «улица Петербургская», «метро Киевская» — это название улицы
            # или станции, а не город. Но «на Тверицкой набережной Ярославля»
            # у набережной уже есть имя, и дальше идёт город
            if i > 0 and lemmas[i - 1] in STREET_TYPES and not (
                    i > 1 and caps[i - 2] and ADJ_FORM_RE.search(tokens[i - 2][0].lower())):
                continue
            # «село Семёновское» — это само село, а не Мурманск, у которого
            # в GeoNames записан старый вариант «Семёновский». После слова
            # «село» или «посёлок» засчитываем только основное имя места
            if (i > 0 and lemmas[i - 1] in LOCALITY_WORDS
                    and not text[tokens[i - 1][1] + len(tokens[i - 1][0]):tokens[i][1]].strip()):
                if (tuple(lemma(w) for w in TOKEN_RE.findall(entry["name"])) != key
                        and entry["name"].lower().replace("ё", "е") != " ".join(key)):
                    continue
            if size == 1 and not entry["lc"]:
                # стоп-лист — частые обычные слова, но «в городе Светлый»,
                # «город Зима» прямо названы городом. После «села», «деревни»
                # не берём: их ловит LOCALITY_RE тегом с областью («д. Слобода ·
                # Кировская область»), а справочник дал бы одноимённый город
                # Только вплотную: «…исторического города. Они украшают» —
                # уже другое предложение (Ним), «1945 г. Нового» — год
                if key[0] in CONTINENTS or (key[0] in BANNED and not (
                        i > 0 and lemmas[i - 1] in ("город", "городок")
                        and not text[tokens[i - 1][1] + len(tokens[i - 1][0]):tokens[i][1]].strip())):
                    continue
                # «путаться в Зеленоградсках, Зеленоградах, Зеленодольсках» —
                # название во множественном числе: сравнение, а не место.
                # У городов с именем во множественном («в Химках») так и надо
                if (re.search(r"(?:ах|ях|ами|ями)$", tokens[i][0].lower())
                        and not re.search(r"[иы]$", entry["name"])
                        and tokens[i][0].lower() != entry["name"].lower()):
                    continue
                # «БАМа» — это магистраль, а не нигерийский город Бама.
                # Аббревиатуру выдаёт вторая заглавная подряд, которой в
                # самом названии нет
                if is_abbreviation(tokens[i][0]) and not is_abbreviation(entry["name"]):
                    continue
                # мелкий зарубежный городок с чужим именем почти всегда
                # оказывается фамилией: «Теофиль Готье», «Фрэнк Гери»
                if (entry["scope"] == "foreign" and entry["pop"] < 30000
                        and not has_geo_context(lemmas, i, size)):
                    continue
                if is_risky(key[0]):
                    # мелкий зарубежный городок с именем-ловушкой почти всегда
                    # оказывается обычным словом: «Александр», «Дом», «Так»
                    if entry["scope"] == "foreign" and entry["pop"] < 100000:
                        continue
                    # «Пересвет»: город, но и имя. Имя засчитываем городом
                    # только после «в»/«из» или в заголовке: «в Пересвет не
                    # обязательно ехать» — город, «памятник Пересвету» — нет
                    if is_person_name(key[0]):
                        if not (standalone_in_title(text, tokens, i, size)
                                or (i > 0 and lemmas[i - 1] in ("в", "во", "из"))
                                or (i > 0 and lemmas[i - 1] in PLACE_WORDS)):
                            continue
                    elif could_be_person(tokens[i][0]):
                        if not (person_city_context(text, tokens, lemmas, i, size)
                                or standalone_in_title(text, tokens, i, size)):
                            continue
                    elif not (has_geo_context(lemmas, i, size)
                              or from_place(text, tokens, lemmas, i)
                              or in_title_line(text, tokens[i][1])
                              or standalone_one_line(text, tokens, i, size, is_place)):
                        continue
                # «Гагарин здесь отдыхал»: слово бывает фамилией — городом
                # считаем только в географическом контексте или в заголовке.
                # Ориентиры из ручного списка не фамилии: «в пространстве
                # Севкабель Порт» терялся — морфология знает «Севкабель» и как имя
                elif not (entry["name"] in LANDMARKS and entry["scope"] == "ru") and could_be_person(tokens[i][0]) and not (
                        person_city_context(text, tokens, lemmas, i, size)
                        or standalone_in_title(text, tokens, i, size)):
                    continue
            hit = (entry, size)
            break
        if hit:
            entry, size = hit
            places.append({"name": entry["name"], "scope": entry["scope"],
                           "lat": entry["lat"], "lon": entry["lon"], "cc": entry["cc"],
                           "parent": entry["parent"], "generic": entry["generic"],
                           # «Костромской губернии», «Владимирская область» —
                           # это регион, то есть место, а не стиль
                           # но «Гатчинского района (Ленинградской области)» —
                           # район области, а не сама Гатчина: слабое
                           # упоминание, запасной вариант. А «в московском
                           # районе Перово» — район города: за «районом» его
                           # собственное имя, и город — настоящее место
                           "adj": entry["lc"] and not (
                               i + size < n and lemmas[i + size] in REGION_WORDS
                               and not (lemmas[i + size] in ("район", "округ", "уезд")
                                        and not (i + size + 1 < n and caps[i + size + 1]
                                                 and not (i + size + 2 < n
                                                          and lemmas[i + size + 2] in REGION_WORDS))))
                           # «в омском театре» — прилагательное, «с Омском» —
                           # город: у города всегда заглавная
                           and not (caps[i] and is_city_form(tokens[i][0], entry["name"])),
                           "cmp": in_comparison(text, tokens, lemmas, i, entry["scope"]),
                           "made": written_in(lemmas, i),
                           "pos": tokens[i][1],
                           "key": key,
                           "raw": " ".join(t for t, _ in tokens[i:i + size])})
            i += size
            continue

        for size in range(min(3, n - i), 0, -1):
            c = countries.get(tuple(lemmas[i:i + size]))
            if c:
                # «в Монголии» — это место; «монгольский орнамент» — нет
                # прилагательное говорит о стиле, а не о месте
                # («Китайский домик» стоит в Царском Селе), а существительное
                # засчитываем и без предлога: подпись «Камбоджа» — это место
                found_countries.append(dict(c, located=not c["adj"]))
                i += size - 1
                break
        i += 1
    return places, found_countries


# «Посёлок Остров в Рязани», «Ямская слобода», «деревня Кондратьево» —
# местности внутри города или рядом с ним. В справочниках их часто нет, а
# если есть, то под обычным словом («Остров»), которое отсеивают фильтры.
# Поэтому ловим их по слову-типу перед названием и показываем тегом рядом
# с улицами; точку на карте даёт город, названный в том же посте.
LOCALITY_RE = re.compile(
    r"(?<![А-ЯЁа-яё])(?P<kind>(?i:посёл(?:ок|ка|ке|ком)|посел(?:ок|ка|ке|ком)|"
    r"слобод(?:а|ы|е|у|ой)|микрорайон(?:а|е|у|ом)?|деревн(?:я|и|е|ю|ей)|"
    r"сел(?:о|а|е|у|ом)|хутор(?:а|е|у|ом)?|станиц(?:а|ы|е|у|ей)|"
    # «остров Проран», «на острове Кижи»: острова тоже бывают без своей
    # записи в справочниках, а в Самаре Проран — часть города
    r"остров(?:а|е|у|ом)?))[ \u00a0]+"
    # «деревня с названием Черная Грязь», «посёлок под названием Сокол»
    r"(?:(?:с|под) +названием +)?"
    r"(?P<name>[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]+)?(?: [А-ЯЁ][а-яё]+)?)"
    # «Ямская слобода»: у слобод название чаще стоит впереди
    r"|(?P<adj>[А-ЯЁ][а-яё]+(?:ая|ой|ую)) (?P<kind2>(?i:слобод)[а-я]*)")
# род слова-типа: по нему название-прилагательное ставится в начальную
# форму — «в хуторе Крымском» -> «Крымский», «в селе Коломенском» -> «Коломенское»
LOCALITY_GENDER = {"пос.": "masc", "мкр": "masc", "х.": "masc", "с.": "neut", "о.": "masc",
                   "дер.": "femn", "ст-ца": "femn"}
# «в селе Копосово Балахнинского уезда»: второе слово — уже регион
REGION_ADJ_RE = re.compile(r"(?:ского|ской|цкого|цкой|ного|ной|его|ей|ого|ой)$")
ADJ_END_RE = re.compile(r"(?:ая|ый|ий|ой|ое|ее|ую|ом|ем|ей|ые|ие|ых|их)$")
SITUATED_RE = re.compile(
    r"(?:^|[.!?\n]\s*)(?P<name>[А-ЯЁ][а-яё]{2,}(?:-[А-ЯЁа-яё][а-яё]+)?),\s+"
    r"(?:расположенн|находящ)[а-яё]*\s+(?:в|на|у)\s+[^.!?\n]{0,50}?"
    r"(?:област|кра[ея]|район|республик|губерни|уезд)")
LOCALITY_LABELS = {"посёл": "пос.", "посел": "пос.", "слобод": "сл.",
                   "микрор": "мкр", "деревн": "дер.", "сел": "с.",
                   "хутор": "х.", "станиц": "ст-ца", "остров": "о."}


def nominative(name, gender):
    """Название-прилагательное — в начальную форму нужного рода."""
    if not gender:
        return name
    for pa in morph.parse(name.lower()):
        if pa.tag.POS == "ADJF":
            form = pa.inflect({"nomn", "sing", gender})
            if form:
                return cap(form.word)
    return name


def nominative_pair(words):
    """«Красной Горке» -> «Красная Горка», «Малый Сундырь» — как есть."""
    adj, noun = words
    # уже в начальной форме — не трогаем: у редких названий морфология
    # угадывает начальную форму плохо («Дуброва» -> «Дубров»)
    if any(pa.tag.POS == "ADJF" and pa.tag.case == "nomn" for pa in morph.parse(adj.lower())):
        return None
    # род незнакомого слова морфология угадывает («Большом Мурашкине» ->
    # «Большая Мурашкина»), а выдумывать название хуже, чем оставить падеж
    if not morph.word_is_known(noun.lower()):
        return None
    for pn in morph.parse(noun.lower()):
        if pn.tag.POS != "NOUN" or not pn.tag.gender:
            continue
        for pa in morph.parse(adj.lower()):
            if pa.tag.POS != "ADJF" or pa.tag.case != pn.tag.case:
                continue
            form = pa.inflect({"nomn", "sing", pn.tag.gender})
            if form:
                return f"{cap(form.word)} {cap(pn.normal_form)}"
    return None


def find_localities(text, places):
    # «село Коломенское» уже нашлось как место — второй тег не нужен. Но
    # сверяем только с местом, найденным в той же позиции: в «село Питер…
    # Поехали в Питер?» Питер нашёлся как Петербург в другой строке
    known_at = {p["pos"]: p for p in places if "pos" in p}
    out = []
    for m in LOCALITY_RE.finditer(text):
        if m.group("adj"):
            # «в Ямской слободе» -> «Ямская слобода»
            adj = m.group("adj")
            form = morph.parse(adj.lower())[0].inflect({"nomn", "sing", "femn"})
            if form:
                adj = form.word.capitalize()
            tag = f"{adj} слобода"
            if tag not in out:
                out.append(tag)
            continue
        words = m.group("name").split()
        if len(words) == 2 and REGION_ADJ_RE.search(words[1]):
            words = words[:1]
        # «станицы Донского Войска» — за типом идёт не название, а
        # прилагательное в родительном падеже
        if re.search(r"(?:ого|его)$", words[0]):
            continue
        # «Подолец Юрьев-Польского района» — второе слово — половина
        # названия района, её отрезаем
        if text[m.start("name") + len(" ".join(words)):][:1] == "-":
            if len(words) == 1:
                continue
            words = words[:1]
        # «деревня Ромашковая долина»: прилагательное при своём
        # существительном — это описание, а не название
        after = text[m.start("name") + len(" ".join(words)):]
        nxt = re.match(r" ([а-яё]+)", after)
        # («остров Русский во Владивостоке»: за названием предлог — это не описание)
        if (len(words) == 1 and ADJ_END_RE.search(words[0]) and nxt
                and not is_function_word(nxt.group(1))):
            continue
        name = " ".join(words)
        same = known_at.get(m.start("name"))
        if same and same["name"].lower().startswith(name.lower()[:4]):
            continue
        # «она села Нина…» — глагол, а не село. Путаница возможна только у
        # формы «села»: «село Питер», «в селе Питер» — точно село
        if m.group("kind").lower() == "села" and is_person_name(lemma(words[0])):
            continue
        kind = m.group("kind").lower()
        label = next(v for k, v in LOCALITY_LABELS.items() if kind.startswith(k))
        if len(words) == 1:
            name = nominative(name, LOCALITY_GENDER.get(label))
        else:
            name = nominative_pair(words) or name
        tag = f"{label} {name}"
        if tag not in out:
            out.append(tag)
    # «Замытье, расположенное в Тверской области», «Курмыш, расположенный на
    # границе Нижегородской области и Чувашии» — населённый пункт без слова
    # «село»: его выдаёт оборот «расположенный в … области». Тип неизвестен —
    # тег «н. п.»
    for m in SITUATED_RE.finditer(text):
        name = m.group("name")
        if lemma(name) in BANNED or any(p.get("pos") == m.start("name") for p in places):
            continue
        tag = f"н. п. {name}"
        if tag not in out:
            out.append(tag)
    return out[:3]


# «Координаты: 60.050942, 31.085751», «🧭 61.634059, 33.413961». Не меньше
# четырёх знаков после точки — иначе это обычные числа из текста.
COORDS_RE = re.compile(r"(?<![\d.])(-?\d{1,2}\.\d{4,})\s*,\s*(-?\d{1,3}\.\d{4,})(?![\d.])")
LABEL_JUNK_RE = re.compile(r"(?i)координаты|coordinates|gps|[^\w\s«»\"'.,-]|^[\s,.:-]+|[\s,.:(-]+$")
_nearest = []


def nearest_city(lat, lon, min_pop=0):
    """Ближайшее место справочника — по нему решаем, Россия это или нет."""
    import math
    best, best_d = None, 1e9
    k = math.cos(math.radians(lat))
    for e in _nearest:
        if e["pop"] < min_pop:
            continue
        d = (e["lat"] - lat) ** 2 + ((e["lon"] - lon) * k) ** 2
        if d < best_d:
            best, best_d = e, d
    return best


def post_title(text):
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    first = LABEL_JUNK_RE.sub("", first).strip()
    return first if 0 < len(first) <= 60 else ""


# --- ссылки на карты ---------------------------------------------------
#
# «Улица Дарта Вейдера (https://yandex.ru/maps/-/CCUimCcPCD)»: точка — в
# ссылке. У коротких ссылок она появляется только после переадресации,
# поэтому их раскрывает tools/resolve_maplinks.py и кладёт в
# data/maplinks.json; здесь ссылки только читаются.
MAP_URL_RE = re.compile(
    r"https?://[^\s)»\"]*(?:yandex\.[a-z]+/maps|maps\.app\.goo\.gl|goo\.gl/maps|google\.[a-z.]+/maps|2gis\.ru)"
    r"[^\s)»\"]*")
MAPLINKS = {}


def load_maplinks():
    path = os.path.join(DATA, "maplinks.json")
    if os.path.exists(path):
        MAPLINKS.update(json.load(open(path, encoding="utf-8")))


def needs_resolving(url):
    """Короткая ссылка или страница организации без точки в адресе."""
    return bool(re.search(r"goo\.gl/|/maps/-/", url)) or ("/org/" in url and not map_point(url))


def map_point(url):
    """(широта, долгота) из адреса ссылки на карту или None. Списки мест —
    закладки Яндекса, «Мои карты» и сохранённые списки Google — не точка."""
    from urllib.parse import unquote
    u = unquote(unquote(url or ""))
    if "bookmarks" in u or "/maps/d/" in u or "!11m" in u:
        return None
    num = r"(-?\d{1,3}\.\d{3,})"
    pairs = []
    if "yandex." in u:
        # у Яндекса порядок «долгота,широта»; метка (pt, whatshere) точнее
        # центра карты (ll), а центр — точка, только если карта приближена
        for key in (r"whatshere\[point\]", r"pt"):
            m = re.search(key + r"=" + num + "," + num, u)
            if m:
                pairs.append((float(m.group(2)), float(m.group(1))))
                break
        m = re.search(r"[?&]ll=" + num + "," + num, u)
        z = re.search(r"[?&]z=(\d+)", u)
        if not pairs and m and (z and int(z.group(1)) >= 15 or re.search(r"/(house|org)/", u)):
            pairs.append((float(m.group(2)), float(m.group(1))))
    elif "google." in u:
        # «?q=55.47,36.35», «/place/…/@55.75,37.61,17z», «!3d55.75!4d37.61»
        m = (re.search(r"!3d" + num + r"!4d" + num, u)
             or re.search(r"[?&](?:q|query|ll)=" + num + "," + num, u)
             or re.search(r"/@" + num + "," + num + r",\d+(?:\.\d+)?z", u))
        if m:
            pairs.append((float(m.group(1)), float(m.group(2))))
    elif "2gis." in u:
        m = re.search(r"/geo/[^?]*?" + num + "," + num, u) or re.search(r"[?&]m=" + num + "," + num, u)
        if m:
            pairs.append((float(m.group(2)), float(m.group(1))))
    for lat, lon in pairs:
        if abs(lat) <= 90 and abs(lon) <= 180:
            return lat, lon
    return None


def link_points(text):
    """Точки из ссылок на карты: (начало ссылки, конец, широта, долгота)."""
    out = []
    for m in MAP_URL_RE.finditer(text):
        url = m.group(0)
        point = map_point(url) or (MAPLINKS.get(url) or {}).get("point")
        if point:
            out.append((m.start(), m.end(), float(point[0]), float(point[1])))
    return out


def find_coordinates(text):
    """Точные точки, если автор указал координаты в тексте или дал ссылку
    на точку на карте."""
    out = []
    # ссылки на карты: подпись — текст ссылки перед «(https://…)»
    for start, end, lat, lon in link_points(text):
        # подпись — последний отрезок фразы перед ссылкой: «1. Дом Ярошенко
        # (…)» → «Дом Ярошенко», а не вся строка «Проживая в Лондоне, …»
        line_start = text.rfind("\n", 0, start) + 1
        head = re.sub(r"https?://\S+", "", text[line_start:start]).rstrip(" (")
        def clean(s):
            s = re.sub(r"^\s*\d+[.)]?\s*", "", LABEL_JUNK_RE.sub("", s)).strip(" ,-")
            if len(s) > 45:
                s = s[-45:].split(" ", 1)[-1]
            return re.sub(r"\s+", " ", s)

        # точка после инициала («имени В.А. Вольховского») — не конец фразы
        label = clean(re.split(r"(?<![А-ЯЁA-Z])[.!?]|[;:—–«»()]|,\s", head)[-1])
        # от отрезка осталось меньше двух слов («18с4-4А» из адреса) — берём
        # конец строки целиком: «Покровский бул., 16-18с4-4А»
        if len(re.findall(r"[А-ЯЁа-яёA-Za-z]{3,}", label)) < 2:
            label = clean(head) or label
        # от ссылки осталось одно служебное слово («официально») — берём заголовок
        if len(re.findall(r"[А-ЯЁа-яёA-Za-z]{3,}", label)) < 2 and not re.search(r"[А-ЯЁA-Z]", label):
            label = post_title(text)
        near = nearest_city(lat, lon)
        if not label:
            label = f"Точка у {near['name']}" if near else "Точка из поста"
        if any(abs(p["lat"] - lat) < 0.0005 and abs(p["lon"] - lon) < 0.0005 for p in out):
            continue
        out.append({"name": label, "scope": near["scope"] if near else "ru", "lat": lat, "lon": lon,
                    "cc": near["cc"] if near else "", "parent": None,
                    "generic": False, "adj": False, "cmp": False,
                    "exact": True, "own": True, "raw": text[start:end]})
    prev_end = 0
    links = [(m.start(), m.end()) for m in MAP_URL_RE.finditer(text)]
    for m in COORDS_RE.finditer(text):
        lat, lon = float(m.group(1)), float(m.group(2))
        if abs(lat) > 90 or abs(lon) > 180:
            continue
        # координаты внутри ссылки на карту («?q=55.47,36.35») уже взяты выше
        if any(a <= m.start() < b for a, b in links):
            continue
        # подпись — то, что стоит перед координатами в той же строке
        # («📍Atoneli 31 - 41.70, 44.80»), иначе заголовок поста
        # в списке точек подпись начинается после предыдущих координат
        line_start = max(text.rfind("\n", 0, m.start()) + 1, prev_end)
        prev_end = m.end()
        label =LABEL_JUNK_RE.sub("", text[line_start:m.start()]).strip()
        label = re.sub(r"\s+", " ", label)[:60] or post_title(text)
        near = nearest_city(lat, lon)
        scope = near["scope"] if near else "ru"
        if not label:
            label = f"Точка у {near['name']}" if near else "Точка из поста"
        out.append({"name": label, "scope": scope, "lat": lat, "lon": lon,
                    "cc": near["cc"] if near else "", "parent": None,
                    "generic": False, "adj": False, "cmp": False,
                    "exact": True, "own": True, "raw": m.group(0)})
        if len(out) >= 6:
            break
    return out


# Типы улиц: как пишутся в тексте -> начальная форма и род. Род нужен,
# чтобы поставить название в начальную форму: «в Шишмаревском переулке»
# -> «Шишмаревский переулок», «на Пятницкой улице» -> «Пятницкая улица».
STREET_KINDS = {
    "улица": ("улица", "femn"), "ул": ("улица", "femn"),
    "проспект": ("проспект", "masc"), "просп": ("проспект", "masc"),
    "пр-т": ("проспект", "masc"), "пр-кт": ("проспект", "masc"),
    "переулок": ("переулок", "masc"), "пер": ("переулок", "masc"),
    "набережная": ("набережная", "femn"), "наб": ("набережная", "femn"),
    "площадь": ("площадь", "femn"), "пл": ("площадь", "femn"),
    "шоссе": ("шоссе", "neut"), "бульвар": ("бульвар", "masc"),
    "б-р": ("бульвар", "masc"), "проезд": ("проезд", "masc"),
    "тупик": ("тупик", "masc"), "аллея": ("аллея", "femn"),
    "линия": ("линия", "femn"),
}
STREET_WORD = (r"(?i:улиц[аыеуой]\w*|ул\.|проспект\w*|просп\.|пр-к?т\b|переул[оки]\w*|"
               r"пер\.|набережн\w+|наб\.|площад[ьиеюя]\w*|пл\.|шоссе|бульвар\w*|б-р\b|"
               r"проезд\w*|тупик\w*|алле[яиеюй]\w*|лини[яиеюй]\w*)")
NAME_WORD = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]+)?"
# «улица Большая Дмитровка», «ул. Пятницкая», «проспект Мира»
STREET_AFTER_RE = re.compile(
    r"(?<![А-ЯЁа-яё])(?P<num>\d+-?[а-я]{1,2}[ ])?(?P<kind>" + STREET_WORD + r")[ \u00a0]+"
    # «набережная канала Грибоедова», «набережная реки Фонтанки», «проспект имени Газа»
    r"(?P<name>(?:\d+-?[а-я]{0,2}[ ])?(?:(?:канала|реки|р\.|озера|пруда|протоки|имени|им\.)[ ]+)?" + NAME_WORD + r"(?:[ ]" + NAME_WORD + r"){0,2})")
# «Шишмаревский переулок», «на Пятницкой улице», «2-я Тверская-Ямская улица»
STREET_BEFORE_RE = re.compile(
    r"(?P<name>(?:\d+-?[а-я]{0,2}[ ])?(?:" + NAME_WORD + r" +){1,2})"
    r"(?P<kind>" + STREET_WORD + r")(?![А-ЯЁа-яё])")


def street_kind(word):
    w = word.lower().rstrip(".")
    if w in STREET_KINDS:
        return STREET_KINDS[w]
    lem = lemma(w)
    return STREET_KINDS.get(lem)


ADJ_FORM_RE = re.compile(r"(?:ий|ый|ой|ая|яя|ое|ее|ого|его|ому|ему|ом|ем|ым|им|ую|юю|ей)$")


def cap(word):
    """«тверская-ямская» -> «Тверская-Ямская»."""
    return "-".join(part[:1].upper() + part[1:] for part in word.split("-"))


def _adj_part(part, gender):
    part = part.lower()
    # «Ленина», «Пушкина» — родительный падеж фамилии, а не прилагательное;
    # по окончанию это видно сразу
    if not ADJ_FORM_RE.search(part):
        return None
    for pa in morph.parse(part):
        # «эта», «наша» — местоимения, а не названия
        if pa.tag.POS == "ADJF" and "Apro" not in pa.tag.grammemes:
            form = pa.inflect({"nomn", "sing", gender})
            if form:
                return form.word
    # незнакомое морфологии «Шишмаревском»: окончание видно и так
    m = re.match(r"(.+?[сц]к)(?:ий|ом|ого|ому|им|ая|ой|ую|ое)$", part)
    if m:
        return m.group(1) + {"masc": "ий", "femn": "ая", "neut": "ое"}[gender]
    m = re.match(r"(.+?[нвр])(?:ый|ом|ого|ому|ым|ая|ой|ую|ое)$", part)
    if m:
        return m.group(1) + {"masc": "ый", "femn": "ая", "neut": "ое"}[gender]
    return None


def street_adjective(word, gender):
    """Прилагательное-название в начальную форму нужного рода, иначе None."""
    # «Нижне-Волжская», «Ново-Садовая»: первая часть — неизменяемая основа
    # на -о/-е, склоняется только последняя
    raw = word.split("-")
    parts = [x.lower() if k < len(raw) - 1 and re.search(r"[оеи]$", x.lower()) else _adj_part(x, gender)
             for k, x in enumerate(raw)]
    if any(x is None for x in parts):
        return None
    return cap("-".join(parts))


def street_pair(words):
    """«Большой Дмитровке» -> «Большая Дмитровка», «Ленина» не трогаем.

    Прилагательное и существительное должны быть согласованы — тогда это
    одно название в каком-то падеже, и его можно поставить в начальную
    форму. У улиц род второго слова морфология угадывает надёжно."""
    adj, noun = words
    if not ADJ_FORM_RE.search(adj.lower()):
        return None
    for pa in morph.parse(adj.lower()):
        if pa.tag.POS != "ADJF" or "Apro" in pa.tag.grammemes:
            continue
        for pn in morph.parse(noun.lower()):
            if pn.tag.POS != "NOUN" or pn.tag.case != pa.tag.case:
                continue
            if pn.tag.number != pa.tag.number or pn.tag.number != "sing":
                continue
            if pn.tag.gender != pa.tag.gender:
                continue
            if pa.tag.case == "nomn":
                return f"{cap(adj)} {cap(noun)}"
            form = pa.inflect({"nomn", "sing", pn.tag.gender})
            if form:
                return f"{cap(form.word)} {cap(pn.normal_form)}"
    return None


def normalize_street(kind_word, name):
    """(«переулке», «Шишмаревском») -> «Шишмаревский переулок»."""
    kind = street_kind(kind_word)
    if not kind:
        return None
    base, gender = kind
    words = name.split()
    # «ул. Площадь Ленина»: тип уже внутри названия — внешний отбрасываем
    if len(words) > 1 and street_kind(words[0]) and not words[0][0].isdigit():
        return normalize_street(words[0], " ".join(words[1:]))
    # номер впереди: «по 2-й Тверской-Ямской» -> «2-я», по роду улицы
    num = words[0] if words and words[0][0].isdigit() else ""
    if num:
        words = words[1:]
        digits = re.match(r"\d+", num).group(0)
        num = digits + {"masc": "-й", "femn": "-я", "neut": "-е"}[gender]
    if not words:
        return None
    # название из прилагательных: «Шишмаревский переулок», «Малый
    # Козихинский переулок» — тип после названия
    adjs = [street_adjective(w, gender) for w in words]
    if all(adjs):
        return " ".join(x for x in (num, *adjs, base) if x)
    # «улица Большая Дмитровка» — тип перед названием
    if len(words) == 2 and not num:
        pair = street_pair(words)
        if pair:
            return f"{base} {pair}"
    # «улица Ленина», «проспект Мира» — название в родительном, не трогаем
    return " ".join(x for x in (num, base, *words) if x)


def is_function_word(word):
    """Предлог, союз, частица или местоимение: «По», «Но», «Эта»."""
    return morph.parse(word.lower())[0].tag.POS in ("PREP", "CONJ", "PRCL", "NPRO", "INTJ")


# Номер дома сразу за улицей: «на набережной реки Фонтанки, 132Б»,
# «улица Ленина, д. 5», «Садовая улица, 39/2». С номером точка встаёт у
# самого дома, а не посреди длинной набережной.
HOUSE_RE = re.compile(r"^,?[ \u00a0]*(?:д\.|дом)?[ \u00a0]*(\d{1,4})(?:[ \u00a0]?([а-яА-Я])(?![а-яА-Я]))?(?:/(\d{1,4}))?(?!\d)")


def house_after(text, end):
    m = HOUSE_RE.match(text[end:end + 20])
    if not m:
        return ""
    # «улица Ленина, 1917 год» — год, а не дом
    rest = text[end + m.end():end + m.end() + 8]
    if re.match(r"[ \u00a0]*(?:г\.|год|гг|век|в\.|м\b|км|%|руб|тыс|млн)", rest):
        return ""
    num = m.group(1) + (m.group(2) or "").upper() + (f"/{m.group(3)}" if m.group(3) else "")
    return num


def find_streets(text):
    if not STREET_RE.search(text):
        return []
    out = []
    spans = []
    for rx in (STREET_BEFORE_RE, STREET_AFTER_RE):
        for m in rx.finditer(text):
            if any(a < m.end() and m.start() < b for a, b in spans):
                continue
            # «На Никольской улице» в начале предложения: предлог с
            # заглавной захватывается вместе с названием — отрезаем
            words = m.group("name").split()
            while words and is_function_word(words[0]):
                words = words[1:]
            name = " ".join(words)
            # слово с заглавной в начале предложения — ещё не название:
            # «Эта улица…», «Наша площадь…»
            first = next((w for w in name.split() if not w[0].isdigit()), "")
            if not first:
                continue

            if rx is STREET_BEFORE_RE and not street_adjective(first, "masc") and (
                    morph.word_is_known(first.lower())
                    and not any("Geox" in p.tag.grammemes or "Surn" in p.tag.grammemes
                                for p in morph.parse(first.lower()))):
                continue
            if "num" in rx.groupindex and m.group("num"):
                name = m.group("num").strip() + " " + name
            street = normalize_street(m.group("kind"), name)
            if street:
                house = house_after(text, m.end())
                if house:
                    street = f"{street}, {house}"
            if street and street not in out:
                spans.append((m.start(), m.end()))
                out.append(street)
    return out[:5]


@functools.lru_cache(maxsize=200_000)
def _forms(word):
    # «Большая» морфология сводит к «больший», «Большой» — к «большой»:
    # сравниваем все разборы, а не первый
    return frozenset(p.normal_form for p in morph.parse(word.lower()))


# Места, уже найденные для других постов (tools/build_known_places.py):
# «Музей одной картины · Пенза» нашли один раз — точка нужна всем трём
# постам о нём. Город -> {форма первого слова -> [(формы слов, запись)]}
KNOWN_PATH = os.path.join(DATA, "known_places.json")
_known = None


def known_index():
    global _known
    if _known is None:
        _known = {}
        if os.path.exists(KNOWN_PATH):
            for e in json.load(open(KNOWN_PATH, encoding="utf-8")):
                need = [_forms(w) for w in e["words"]]
                by_first = _known.setdefault(e["city"], {})
                for f in need[0]:
                    by_first.setdefault(f, []).append((need, e))
    return _known


KNOWN_PREPS = {"в", "во", "на", "у", "около", "возле", "напротив", "перед", "за",
               "под", "рядом", "близ", "внутри", "вокруг", "по"}


def known_as_place(text, spans, i, n):
    """«в Музее архитектуры открылась выставка», «Замок Шереметева в поселке
    Юрино» — место; «Фото: Музей архитектуры», «из архива Музея архитектуры»,
    «директора Музея архитектуры» — организация, точку не ставим.
    Место — после предлога места (между ним и названием можно одно
    прилагательное: «в московском Музее»), в начале предложения или перед
    «в <город>»."""
    words = [text[a:b] for a, b in spans]
    for back in (1, 2):
        j = i - back
        if j < 0:
            break
        if lemma(words[j]) in KNOWN_PREPS:
            return True
        if not ADJ_FORM_RE.search(words[j].lower()):
            break
    before = text[:spans[i][0]].rstrip("  «\"")
    if not before or before[-1] in ".!?\n" or before.endswith("—"):
        return True
    k = i + n
    if k + 1 < len(words) and words[k].lower() in ("в", "во") and words[k + 1][:1].isupper():
        return True
    return False


def find_known_places(text, cities):
    """Точки мест из словаря, названных в посте при своём городе."""
    index = known_index()
    wanted = [index[c] for c in cities if c in index]
    if not wanted:
        return []
    spans = [m.span() for m in TOKEN_RE.finditer(text)]
    tokens = [text[a:b] for a, b in spans]
    out, seen = [], set()
    for i, tok in enumerate(tokens):
        for by_first in wanted:
            for f in _forms(tok):
                for need, e in by_first.get(f, ()):
                    n = len(need)
                    if (e["city"], e["lat"], e["lon"]) in seen or i + n > len(tokens):
                        continue
                    if (all(need[k] & _forms(tokens[i + k]) for k in range(1, n))
                            and known_as_place(text, spans, i, n)):
                        seen.add((e["city"], e["lat"], e["lon"]))
                        out.append({"name": f"{e['name']} · {e['city']}", "scope": "ru",
                                    "lat": e["lat"], "lon": e["lon"], "cc": "RU",
                                    "parent": e["city"], "generic": False, "adj": False,
                                    "cmp": False, "exact": True, "raw": e["name"]})
    return out


def find_bare_streets(text, cities, spans=None):
    """«на Большой Конюшенной в Петербурге» — известная улица без слова
    «улица» (список BARE_STREETS). Только в городе, названном в посте.
    В spans кладёт отрезки текста, занятые улицами."""
    wanted = [full for city in cities for full in BARE_STREETS.get(city, ())]
    if not wanted:
        return []
    tokens = [(m.group(0), m.start()) for m in TOKEN_RE.finditer(text)]
    out = []
    for full in wanted:
        words = [w for w in full.split() if w not in ("улица", "проспект")]
        need = [_forms(w) for w in words]
        n = len(words)
        for i in range(1, len(tokens) - n + 1):
            if lemma(tokens[i - 1][0]) not in ("на", "по", "с", "со"):
                continue
            if not all(tokens[i + k][0][:1].isupper() and need[k] & _forms(tokens[i + k][0])
                       for k in range(n)):
                continue
            end = tokens[i + n - 1][1] + len(tokens[i + n - 1][0])
            # «на Тверской улице» нашёл find_streets, «Тверской области» — не улица
            if i + n < len(tokens) and not text[end:tokens[i + n][1]].strip():
                nxt = tokens[i + n][0]
                if nxt[:1].islower() and morph.parse(nxt.lower())[0].tag.POS == "NOUN":
                    continue
            house = house_after(text, end)
            street = f"{full}, {house}" if house else full
            if spans is not None:
                spans.append((tokens[i][1], end))
            if street not in out:
                out.append(street)
    return out


# Именованные объекты: небоскрёб «Лахта-центр», ДК «Каучук», гостиница
# «Москва». Их нет ни в GeoNames, ни в слое районов, зато есть в OSM —
# координаты им ищет tools/geocode_streets.py.
OBJECT_NOUNS = (r"небоскр[её]б\w*|здани\w+|дом[ау]?|особняк\w*|усадьб\w+|"
                r"ДК|[Дд]вор[ец]\w* культуры|кинотеатр\w*|гостиниц\w+|отел[ьяюе]\w*|"
                r"стадион\w*|вокзал\w*|башн\w+|комплекс\w*|ЖК|санатори\w+|"
                r"пансионат\w*|лагер[ьяюе]\w*|ДОЛ|театр\w*|музе[йяюе]\w*|"
                r"завод\w*|фабрик\w+|универмаг\w*|ТЦ|торгов\w+ центр\w*|"
                r"бизнес-центр\w*|БЦ|парк[аеу]?|сад[ау]?|мост[ау]?|"
                r"монастыр\w+|собор\w*|храм\w*|церк\w+|колонн\w+|памятник\w*|"
                r"СНТ|ДНТ|садоводств\w*|товариществ\w*|колхоз\w*|совхоз\w*|"
                # произведения на фасадах и в скверах: в OSM у них своя точка
                r"барельеф\w*|горельеф\w*|рельеф\w*|мозаик\w+|панно|скульптур\w+|"
                r"стел[аыеуой]|фреск\w+|роспис\w+|арт-объект\w*|инсталляци\w+|"
                # «НИИ „Гипроцемент“», «ЦКБ „Рубин“»
                r"ЦНИИ|НИИ|ЦКБ|ОКБ|СКБ|КБ|"
                # «тюрьмы „Кресты“», «рынка „Кузнечный“»
                r"тюрьм\w+|острог\w*|крепост\w+|казарм\w+|фабрик\w+-кухн\w+|бан[яиеюь]|бань|"
                r"рын(?:ок|ка|ке|ком)|пассаж\w*|универсам\w*|бассейн\w*|планетари\w+|цирк\w*|"
                r"маяк\w*|элеватор\w*|водонапорн\w+ башн\w+|ротонд\w+|павильон\w*")
OBJECT_QUOTED_RE = re.compile(
    # между типом и названием бывает уточнение: «музей современного
    # искусства „Эрарта“», «Дом культуры завода „Каучук“» — до трёх слов
    r"(?<![А-ЯЁа-яё])(?i:" + OBJECT_NOUNS + r")(?:[ \u00a0]+[а-яё]+){0,3}[ \u00a0]*[«\"]([^»\"\n]{2,40})[»\"]")
# «Александровская колонна», «Казанский собор», «Сухарева башня»
OBJECT_NAMED_RE = re.compile(
    # «Свято-Троицкая церковь»: прилагательное бывает составным
    r"(?<![А-ЯЁа-яё-])((?:[А-ЯЁ][а-яё]+-)?[А-ЯЁ][а-яё]+(?:ая|ый|ий|ой|ое|ее|ва|ов|ин|на|ого|его|ому|ему|ым|им|ую|ом|ем|ей))[ \u00a0]+"
    r"(колонн[аыеуой]|собор[аеу]?|башн[яиеюй]|двор[е]?ц[аеу]?|мост[аеу]?|монастыр[ьяюе]|"
    r"вокзал[аеу]?|театр[аеу]?|усадьб[аыеуой]|церк(?:овь|ви|овью)|храм[аеу]?|часовн[яиеюй])(?![А-ЯЁа-яё])")
OBJECT_BASE = {"колонн": ("колонна", "femn"), "собор": ("собор", "masc"),
               "башн": ("башня", "femn"), "двор": ("дворец", "masc"),
               "мост": ("мост", "masc"), "монастыр": ("монастырь", "masc"),
               "вокзал": ("вокзал", "masc"), "театр": ("театр", "masc"),
               "усадьб": ("усадьба", "femn"), "церк": ("церковь", "femn"),
               "храм": ("храм", "masc"), "часовн": ("часовня", "femn")}


# «в KGallery», «в Garage», «на Artplay»: латиница после предлога — чаще
# всего название заведения. Сервисы и соцсети исключаем — их упоминают
# постоянно, и геокодер нашёл бы офис с тем же именем
OBJECT_LATIN_RE = re.compile(
    r"(?<![А-ЯЁа-яё])(?:[Вв]о?|[Ии]з|[Нн]а)[  ]+([A-Z][A-Za-z0-9&'’.\-]*[A-Za-z0-9](?: [A-Z][A-Za-z0-9&'’\-]+){0,2})")
LATIN_SKIP = {"instagram", "telegram", "youtube", "facebook", "twitter", "google", "yandex",
              "vk", "tiktok", "whatsapp", "viber", "zoom", "wikipedia", "pinterest",
              "livejournal", "dzen", "rutube", "apple", "microsoft", "netflix", "spotify",
              "steam", "discord", "reddit", "threads", "linkedin", "flickr", "behance",
              "airbnb", "booking", "avito", "ozon", "wildberries", "ok", "x", "tg", "ai"}
# общие слова: «в Cafe», «на Max» — само по себе не название, геокодер нашёл
# бы любое заведение с таким словом. В составе названия («Design District
# DAA») они не мешают
LATIN_GENERIC = {"cafe", "bar", "max", "hotel", "park", "club", "home", "house", "city",
              "village", "art", "design", "studio", "loft", "space", "shop", "market",
              "gallery", "museum", "center", "centre", "plaza", "mall", "food", "coffee",
              "the", "new", "old", "best", "top", "pro", "one", "just"}


# «здание Пробирной палаты», «особняк Второва», «комплекс Бадаевского завода»:
# название без кавычек после типа постройки. Ищем фразу целиком, вместе
# со словом «здание»: по короткому «Пробирная палата» геокодер находит
# одноимённую организацию на другом конце города
OBJECT_BUILDING_RE = re.compile(
    r"(?<![А-ЯЁа-яё])(?P<kind>[Зз]дани[еяюи]|[Оо]собняк[аеу]?|[Кк]омплекс[аеу]?)[ ]+"
    r"(?P<name>[А-ЯЁ][а-яё]+(?:ой|ого|ых|их|ей|ова|ева|ина)(?:[ ]+[а-яё]+)?)(?![А-ЯЁа-яё])")
BUILDING_BASE = {"здани": "Здание", "особняк": "Особняк", "комплекс": "Комплекс"}

# «Музей ОБЭРИУ», «театр ТЮЗ», «галерея ГУМ-Red Line»: тип и название
# заглавными, без кавычек
OBJECT_CAPS_RE = re.compile(
    r"(?<![А-ЯЁа-яё])(?P<kind>[Мм]узе[йяюе]|[Тт]еатр[аеу]?|[Гг]алере[яиеюй]|[Цц]ентр[аеу]?|"
    r"[Кк]инотеатр[аеу]?|[Кк]луб[аеу]?|[Пп]ространств[оау]|ДК)[ ]+"
    r"(?P<name>[A-ZА-ЯЁ]{3,}(?:[-][A-ZА-ЯЁ0-9]+)?)(?![A-Za-zА-ЯЁа-яё])")
CAPS_BASE = {"музе": "Музей", "театр": "Театр", "галере": "Галерея", "центр": "Центр",
             "кинотеатр": "Кинотеатр", "клуб": "Клуб", "пространств": "Пространство", "дк": "ДК"}


# «станции „Юго-Западная“ и „Путиловская“», «метро „Беляево“»: станция
# метро в кавычках, часто перечнем. Новых станций в справочнике нет, а
# старые одноимённые («Юго-Западная» есть и в Москве) уехали бы в другой город
STATION_RE = re.compile(r"(?<![А-ЯЁа-яё])(?:[Сс]танци[яиеюй]\w*(?:[ \u00a0]+метро)?|[Мм]етро)[ \u00a0]*«")
STATION_NEXT_RE = re.compile(r"«([^»\n]{2,40})»(?:[ \u00a0]*(?:,|и)[ \u00a0]*)?")


def find_stations(text):
    out = []
    for m in STATION_RE.finditer(text):
        # «станция „Москва-Сортировочная“» — железнодорожная: без слова
        # «метро» станцию берём, только если метро упомянуто в том же
        # предложении («станции „…“ новой линии метро»)
        if "метро" not in m.group(0).lower():
            start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
            ends = [i for i in (text.find(".", m.end()), text.find("\n", m.end())) if i != -1]
            if "метро" not in text[start:min(ends) if ends else len(text)].lower():
                continue
        pos = m.end() - 1
        while True:
            n = STATION_NEXT_RE.match(text, pos)
            if not n:
                break
            name = n.group(1).strip()
            if name[:1].isupper() and f"метро {name}" not in out:
                out.append(f"метро {name}")
            pos = n.end()
    return out


# «Башня ЦНИИ робототехники», «здание НИИ точных приборов», «ОКБ Сухого»:
# НИИ и КБ по аббревиатуре и продолжению строчными (или фамилией)
INSTITUTE_RE = re.compile(
    r"(?<![А-ЯЁа-яё])(?P<abbr>ЦНИИ|НИИ|ВНИИ|ЛНИИ|ЦКБ|ОКБ|СКБ|ГСКБ|КБ|ЦАГИ|ГИПРО[А-ЯЁ]*)"
    r"(?P<rest>(?:[ \u00a0]+[а-яё]{3,}){1,3}|[ \u00a0]+[А-ЯЁ][а-яё]{2,}(?:ого|ова|ева|ина)?)(?=[ \u00a0]*[.,;:!?()\n«]|$)")


# «здания Музея Арктики и Антарктики», «в Музее истории Петербурга», «Дом
# культуры Капранова»: тип с заглавной и название в родительном падеже, без
# кавычек. Берём до четырёх слов, соединённых «и»; тип — в начальной форме
NAMED_GENITIVE_RE = re.compile(
    r"(?<![А-ЯЁа-яё])(?P<kind>Музе[йяюе]м?|Театр[аеу]?|Двор(?:ец|ца|це|цом)|Дом[аеу]?|Библиотек[аиеуой]|Галере[яиеюй]|Институт[аеу]?)"
    r"(?P<name>(?:[  ]+(?:и|[а-яё]+[  ]+)?[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё][а-яё]+)?){1,4})")
GENITIVE_BASE = {"музе": "Музей", "театр": "Театр", "двор": "Дворец", "дом": "Дом",
                 "библиотек": "Библиотека", "галере": "Галерея", "институт": "Институт"}
# «Дома Культуры имени X-летия Октября»: с заглавной написан только тип, а
# имя («X» латинская) правило не взяло — остался «Дом Культуры», и геокодер
# поставил точку на первый попавшийся дом культуры Петербурга. Таких домов и
# дворцов в любом городе десятки; «Дом Советов» — имя, его не трогаем
GENERIC_GENITIVE = {"культуры", "спорта", "творчества", "быта"}


# «общежитие фабрики „…“», «школа при заводе „…“», «жилой дом комбината „…“» —
# постройка, которая принадлежит предприятию, но стоит отдельно от него
ENTERPRISE_PART_RE = re.compile(
    r"(?:общежити\w*|жило\w* дом\w*|жилмассив\w*|посёл\w*|посел\w*|школ\w*|"
    r"детск\w+ сад\w*|больниц\w*|поликлиник\w*|санатори\w*)"
    r"(?:[  ]+[а-яё]+){0,2}[  ]+(?:фабрик\w*|завод\w*|комбинат\w*|мануфактур\w*)"
    r"[  ]*[«\"]?$")


def find_objects(text):
    out = find_stations(text)
    for m in NAMED_GENITIVE_RE.finditer(text):
        kind = m.group("kind").lower()
        base = next(v for k, v in GENITIVE_BASE.items() if kind.startswith(k))
        words = m.group("name").split()
        # «Дом Пашкова в Москве»: предлог — уже не название, режем перед ним
        for k, w in enumerate(words):
            if w != "и" and w.islower() and is_function_word(w):
                words = words[:k]
                break
        # хвост из служебных слов («… и») отбрасываем
        while words and (words[-1] == "и" or is_function_word(words[-1])):
            words = words[:-1]
        # «Дом Пашкова», «Дом Советов» тоже годятся: геокодер примет точку,
        # только если название в городе поста совпало целиком
        if not words or (len(words) == 1 and words[0].lower() in GENERIC_GENITIVE):
            continue
        name = f"{base} {' '.join(words)}"
        if name not in out:
            out.append(name)
    for m in INSTITUTE_RE.finditer(text):
        words = m.group("rest").split()
        # «НИИ в Москве»: предлог — не продолжение названия
        if is_function_word(words[0]):
            continue
        name = f"{m.group('abbr')} {' '.join(words)}"
        if name not in out:
            out.append(name)
    for m in OBJECT_CAPS_RE.finditer(text):
        kind = m.group("kind").lower()
        # «кинотеатр» раньше «театра» — иначе он станет «Театр»
        base = next(v for k, v in sorted(CAPS_BASE.items(), key=lambda kv: -len(kv[0])) if kind.startswith(k))
        name = f"{base} {m.group('name')}"
        if name not in out:
            out.append(name)
    for m in OBJECT_BUILDING_RE.finditer(text):
        base = next(v for k, v in BUILDING_BASE.items() if m.group("kind").lower().startswith(k))
        words = m.group("name").split()
        # «особняк Второва в Москве» — предлог за названием не берём
        if len(words) > 1 and (is_function_word(words[-1]) or len(words[-1]) <= 2):
            words = words[:-1]
        name = f"{base} {' '.join(words)}"
        if name not in out:
            out.append(name)
    for m in OBJECT_LATIN_RE.finditer(text):
        name = m.group(1).strip(" .")
        if name.split()[0].lower() in LATIN_SKIP or len(name) < 3:
            continue
        if " " not in name and name.lower() in LATIN_GENERIC:
            continue
        # «в XVIII веке» — римские цифры; «в RTVI», «в LEGO» — аббревиатуры
        # и бренды заглавными буквами, не заведения
        if re.fullmatch(r"[IVXLCDM]+(?:[-–][IVXLCDM]+)?", name) or (name.isupper() and len(name) <= 5):
            continue
        # ссылка сразу после названия («KGallery (https://…)») — признак
        # заведения, но и без неё берём: геокодер проверит точное совпадение
        if name not in out:
            out.append(name)
    for m in OBJECT_QUOTED_RE.finditer(text):
        name = m.group(1).strip()
        # «общежития текстильной фабрики „Красная Нить“»: речь о другом
        # здании предприятия — точка фабрики была бы не та
        if ENTERPRISE_PART_RE.search(text[max(0, m.start() - 60):m.start(1)]):
            continue
        # «ДК „Невский“»: в OSM дом культуры так и записан — «ДК «Невский»», а
        # голое «Невский» в Петербурге — десяток заведений, и геокодер взял
        # первое, на улице Калинина. Тип оставляем в названии
        if re.match(r"(?i)ДК\b|д(?:ом|вор\w*)[  ]+культуры", m.group(0)):
            name = f"ДК {name}"
        if name[:1].isupper() and name not in out:
            out.append(name)
    for m in OBJECT_NAMED_RE.finditer(text):
        word, noun = m.group(1), m.group(2).lower()
        # «Водонапорная башня», «Роскошный вокзал» — описание, а не имя:
        # слово, которое в текстах канала почти всегда пишется со строчной
        # (кроме прилагательных от городов: «Казанский собор»)
        if lemma(word) in BANNED and lemma(word) not in ADJECTIVES:
            continue
        base, gender = next(v for k, v in OBJECT_BASE.items() if noun.startswith(k))
        if noun == base:
            name = f"{word} {base}"  # уже в начальной форме
        else:
            # ставим в начальную форму только то, что морфология знает как
            # прилагательное: «Сухаревой башни» она путает с «Сухаревая»,
            # а притяжательные «Симонова монастыря» не склоняет вовсе
            form = None
            for pa in morph.parse(word.lower()):
                if pa.tag.POS == "ADJF" and "Poss" not in pa.tag.grammemes                         and morph.word_is_known(pa.normal_form) and not re.search(r"(?:ов|ев|ин)[аоуы]?$|(?:ов|ев|ин)ой$", word.lower()):
                    f = pa.inflect({"nomn", "sing", gender})
                    if f:
                        form = cap(f.word)
                        break
            if not form:
                continue
            name = f"{form} {base}"
        if name not in out:
            out.append(name)
    return out[:5]


# Кэш геокодера (tools/geocode_streets.py): «город|улица» и
# «город|obj:объект» -> координаты или null. Пока его нет, разбор просто
# работает без точек улиц.
GEOCACHE_PATH = os.path.join(DATA, "street_geocode.json")
GEOCACHE = {}


def load_geocache():
    GEOCACHE.clear()
    if os.path.exists(GEOCACHE_PATH):
        with open(GEOCACHE_PATH, encoding="utf-8") as f:
            GEOCACHE.update(json.load(f))


REGION_NAME_WORDS = ("область", "край", "республика", "округ", "губерния", "Россия")


_big_cities = []


def big_cities():
    """Города от 20 тысяч жителей — к ним привязываем ориентиры и районы."""
    if not _big_cities:
        with open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8") as f:
            raw = json.load(f)
        _big_cities.extend((c["n"], c["lat"], c["lon"], c["pop"])
                           for c in raw["ru"] if c["pop"] >= 20000)
    return _big_cities


_city_of_cache = {}


def city_of(place):
    """Город ориентира или района: крупнейший в радиусе 30 км.

    Не ближайший: GeoNames числит Лефортово и Тушино отдельными «городами»,
    и ближайшим к Басманному району оказывалось Лефортово, а не Москва."""
    import math
    lat, lon = place["lat"], place["lon"]
    if (lat, lon) not in _city_of_cache:
        k = math.cos(math.radians(lat))
        near = [c for c in big_cities()
                if math.hypot(c[1] - lat, (c[2] - lon) * k) * 111 <= 30]
        _city_of_cache[(lat, lon)] = max(near, key=lambda c: c[3])[0] if near else None
    return _city_of_cache[(lat, lon)]


def is_local(place):
    """Ориентир или район внутри города, а не сам город."""
    return place["scope"] == "ru" and (place["name"] in LANDMARKS or bool(place.get("parent")))


def context_cities(places):
    """Города, в которых стоит искать улицы и объекты поста.

    Сюда идут и слабые упоминания: в «петербургского небоскрёба
    „Лахта-центр“» город назван только прилагательным, но искать небоскрёб
    надо именно в Петербурге. У района и ориентира берём их город."""
    out = []
    for p in places:
        if p["scope"] != "ru" or p.get("exact"):
            continue
        name = p["name"]
        if any(w in name for w in REGION_NAME_WORDS):
            continue
        if is_local(p):
            name = city_of(p)
        if name and name not in out:
            out.append(name)
    return out[:3]


def locality_name(tag):
    """«дер. Черная Грязь» -> «Черная Грязь»; у слобод тип — часть названия."""
    if tag.startswith("н. п. "):
        return tag[len("н. п. "):]
    return tag if tag.endswith(" слобода") else tag.split(" ", 1)[1]


# как регион называется в OSM: по нему геокодер проверяет, что село лежит
# в нужной области
REGION_STATE = {"Подмосковье": "Московская область"}


def post_regions(places):
    """Регионы поста: в них ищем сёла, далёкие от областного центра."""
    out = []
    for p in places:
        name = REGION_STATE.get(p["name"], p["name"])
        if p["scope"] == "ru" and any(w in name for w in REGION_NAME_WORDS[:-1]):
            if name not in out:
                out.append(name)
    return out[:2]


def geocoded_points(streets, objects, cities, localities=(), regions=()):
    """Точки улиц, объектов и местностей из кэша геокодера."""
    points, found = [], set()
    todo = ([(s, s, cities) for s in streets]
            + [(o, "obj:" + o, list(cities) + list(regions)) for o in objects]
            + [(t, "loc:" + locality_name(t), list(cities) + list(regions))
               for t in localities])
    for label, key_part, where in todo:
        for city in where:
            pt = GEOCACHE.get(f"{city}|{key_part}")
            if pt:
                points.append({"name": f"{label} · {city}", "scope": "ru",
                               "lat": pt["lat"], "lon": pt["lon"], "cc": "RU",
                               "parent": city, "generic": False, "adj": False,
                               "cmp": False, "exact": True, "raw": label})
                found.add(label)
                break
    return points, found


LIST_GAP_RE = re.compile(r"^[\s,]*(?:и\s+)?$")


def inherit_in_lists(places, text):
    """«в пригородах Москвы, Петербурга, Казани и Тюмени»: пригород
    относится ко всему перечню, а не к одной Москве. Место, которое идёт
    в перечислении сразу за слабым, тоже слабое."""
    prev = None
    for p in places:
        if prev is not None and prev["cmp"] and "pos" in p and "pos" in prev:
            gap = text[prev["pos"] + len(prev["raw"]):p["pos"]]
            if LIST_GAP_RE.match(gap):
                p["cmp"] = True
        prev = p
    # «кейсы Москвы (https://…), Санкт-Петербурга (https://…), Кирова (…)»:
    # перечень городов, у каждого своя ссылка, — это ссылки на другие
    # посты, а не место этого. Одиночная ссылка после места («В Москве
    # (https://…) открылась выставка») — обычный источник, её не трогаем
    linked = [p for p in places if "pos" in p
              and re.match(r"[  ]*\(https?://", text[p["pos"] + len(p["raw"]):p["pos"] + len(p["raw"]) + 12])]
    if len(linked) >= 2:
        for p in linked:
            p["cmp"] = True
    return places


# фамилии, которых нет в словаре морфологии: «в стиле работ Билибина»
SURNAME_PLACES = {"билибино"}


def prefer_local_homonyms(places, gaz):
    """«Зеленогорск — курортный город в составе Петербурга»: ключ достаётся
    крупнейшему Зеленогорску (Красноярский край), но у названия есть и
    район Петербурга. Если город района в посте назван, берём район."""
    import math
    named = {p["name"] for p in places if p["scope"] == "ru" and not p.get("cmp")}

    def km(a, b):
        k = math.cos(math.radians(a["lat"]))
        return math.hypot(a["lat"] - b["lat"], (a["lon"] - b["lon"]) * k) * 111

    out = []
    for p in places:
        # трогаем, только если место оторвано от всех остальных в посте
        # (Зеленогорск в Красноярском крае при Петербурге). Иваново рядом с
        # Шуей, Кунцево при Москве — и так на месте
        others = [q for q in places if q["name"] != p["name"] and q["scope"] == "ru" and not q.get("cmp")]
        # зарубежное не трогаем: «София» в посте про Чухлому — скорее имя,
        # чем район Пушкина
        if p["scope"] != "ru" or not others or min(km(p, q) for q in others) < 300:
            out.append(p)
            continue
        for e in gaz.alts.get(p.get("key"), ()):
            if (not e["parent"] or e["name"].replace("ё", "е") != p["name"].replace("ё", "е")
                    or (e["lat"], e["lon"]) == (p["lat"], p["lon"])):
                continue
            # город района — ближайшее из названных в посте мест: Зеленогорск
            # в 45 км от центра Петербурга, city_of с его 30 км не дотягивался
            # из нескольких — крупнейший: «Некрасовка · Москва», а не «· Жулебино»
            near = [q for q in others if km(e, q) < 60 and not q.get("parent")]
            city = max(near, key=lambda q: (city_population(q["name"]), -km(e, q)))["name"] if near else None
            if city and city in named and city != p["name"]:
                # не «точное»: вес упоминания прежний, иначе мимоходом
                # названное Орехово переживало бы все фильтры
                p = dict(p, name=f"{e['name']} · {city}", lat=e["lat"], lon=e["lon"],
                         parent=city, homonym=True, was=p["name"])
                break
        out.append(p)
    return out


# «станции московского метро «Шоссе Энтузиастов»»: станцию разбор находит,
# но без города геокодер её не ищет — город задаёт «московское метро»
METRO_CITY = [
    (re.compile(r"(?i)московск\w*\s+метро|метро\s+Москвы|столичн\w*\s+метро"), "москва"),
    (re.compile(r"(?i)(?:петербургск|ленинградск)\w*\s+метро|метро\s+(?:Санкт-)?Петербурга|метро\s+Ленинграда"),
     "санкт-петербург"),
]


def metro_city(places, text, gaz):
    if not find_stations(text):
        return places
    for rx, key in METRO_CITY:
        m = rx.search(text)
        e = gaz.index.get((key,))
        if m and e and not any(p["name"] == e["name"] and not p.get("cmp") and not p.get("adj") for p in places):
            places = places + [{"name": e["name"], "scope": e["scope"], "lat": e["lat"], "lon": e["lon"],
                                "cc": e["cc"], "parent": None, "generic": False, "adj": False,
                                "cmp": False, "pos": m.start(), "key": (key,), "raw": m.group(0)}]
    return places


def could_be_person(word):
    """Может ли слово быть фамилией или именем: «Гагарин», «Королёв»."""
    if lemma(word) in SURNAME_PLACES:
        return True
    return any({"Surn", "Name", "Patr"} & set(p.tag.grammemes)
               for p in morph.parse(word.lower()))


def lead_end(text):
    """Начало поста — заголовок или первое предложение, не дальше 250 знаков."""
    m = re.search(r"[.!?\n]", text[20:])
    return min(250, 20 + m.start() if m else 250)


# с какого веса место считается главным (см. drop_incidental)
MAIN_MIN = 3


def drop_incidental(places, uniq, text):
    """Города, упомянутые мимоходом, в посте с явным главным местом.

    «Череповец на окраинах кажется монструозным… Отношения двух главных
    городов области — Вологды и Череповца — тянут на „терки“ Москвы и
    Петербурга… вернуться в Череповец»: пост про Череповец, остальные
    города — к слову. Вес места — число упоминаний всерьёз плюс два, если
    оно названо в начале поста. Если у главного места вес от четырёх,
    города с одним упоминанием не в начале из поста убираются. Регионы,
    ориентиры, улицы и точные точки остаются: они уточняют, а не спорят.
    Посты-перечни, где каждый город назван по разу, правило не трогает."""
    # Координаты в тексте — самое точное место поста. Города дальше 40 км
    # от них названы к слову: «лагерь принадлежал заводу „Красный
    # треугольник“ — одному из крупнейших предприятий Ленинграда»
    coords = [p for p in uniq if p.get("exact") and not p.get("parent")]
    if coords:
        import math

        def far(p):
            k = math.cos(math.radians(p["lat"]))
            return all(math.hypot(p["lat"] - c["lat"], (p["lon"] - c["lon"]) * k) * 111 > 40
                       for c in coords)
        uniq = [p for p in uniq
                if p.get("exact") or is_local(p)
                or any(w in p["name"] for w in REGION_NAME_WORDS) or not far(p)]

    head = lead_end(text)
    weight, in_lead = Counter(), set()
    for p in places:
        if p.get("adj") or p.get("cmp"):
            continue
        weight[p["name"]] += 1
        if p.get("pos", 10 ** 9) < head:
            in_lead.add(p["name"])
    # «где находится Свободный», «Свободный покажется Нью-Йорком»: без
    # предлога рядом разбор такие упоминания не засчитывает, но если город
    # уже найден в посте, все его упоминания с заглавной — его вес
    caps_count = Counter(lemma(t) for t, _ in tokenize(text) if t[:1].isupper())
    for name in list(weight):
        words = TOKEN_RE.findall(name)
        # «Гагарин», «Семёнов» — ещё и фамилии: «Юрий Гагарин провёл ночь»
        # не делает пост постом про город Гагарин
        if len(words) == 1 and not could_be_person(words[0]):
            weight[name] = max(weight[name], caps_count[lemma(words[0])],
                               caps_count[words[0].lower().replace("ё", "е")])
    score = {n: w + (2 if n in in_lead else 0) for n, w in weight.items()}
    # главным бывает только город: область не должна вытеснять свои же
    # города («Вичуга» в посте про Ивановскую область), а ориентир или
    # район — свой город
    by_name = {p["name"]: p for p in uniq}
    cities = {n: v for n, v in score.items()
              if n in by_name and by_name[n]["scope"] == "ru"
              and not is_local(by_name[n]) and not by_name[n].get("exact")
              and not any(w in n for w in REGION_NAME_WORDS)
              and n not in REGIONS}
    if not cities or max(cities.values()) < MAIN_MIN:
        return uniq
    main = max(cities, key=cities.get)
    # к слову — город с весом не больше трети главного: у Свободного вес
    # шесть, дважды названная Могоча рядом с ним — к слову
    minor = max(1, score[main] // 3)

    def incidental(p):
        if p["name"] == main or p.get("exact") or is_local(p):
            return False
        if any(w in p["name"] for w in REGION_NAME_WORDS):
            return False
        return score.get(p["name"], 0) <= minor and p["name"] not in in_lead
    return [p for p in uniq if not incidental(p)]


def drop_foreign_landmarks(uniq):
    """«Дворцовая площадь» в посте про Казань — казанская, а не петербургская.

    Ориентиры и районы из справочника привязаны к одному городу, но многие
    названия повторяются: Дворцовая площадь, Соборная, Театральная есть
    во многих городах. Если в посте прямо назван город, а город ориентира —
    нет, ориентир отбрасываем: одноимённое место в названном городе найдёт
    геокодер (tools/geocode_streets.py)."""
    named = {p["name"] for p in uniq
             if p["scope"] == "ru" and not is_local(p) and not p.get("exact")
             and not any(w in p["name"] for w in REGION_NAME_WORDS)}
    if not named:
        return uniq
    # у точки геокодера город известен — тот, с которым её искали: ДК
    # «Протон» в Протвино, хотя крупнейший город в 30 км от него — Серпухов
    # «в московской Некрасовке… сменил Жулебино и Бирюлёво»: Жулебино в
    # GeoNames — отдельный «город», но его город — Москва
    near = named | {city_of(q) for q in uniq if q["name"] in named} | {q["was"] for q in uniq if q.get("was")}
    # точка села в названной области («дер. Лампово · Ленинградская
    # область») — не ориентир какого-то города, её не трогаем
    return [p for p in uniq if not is_local(p) or city_of(p) in named
            or ((p.get("exact") or p.get("homonym")) and p.get("parent") in near)
            or (p.get("exact") and any(w in (p.get("parent") or "") for w in REGION_NAME_WORDS))]


_pop_by_name = {}


def city_population(name):
    if not _pop_by_name:
        with open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8") as f:
            _pop_by_name.update((c["n"], c["pop"]) for c in json.load(f)["ru"])
    return _pop_by_name.get(name, 0)


def attach_suburbs(uniq):
    """«Комарово (СПб)», «Сестрорецк под Петербургом», «Химки и Москва»:
    небольшой населённый пункт рядом с крупным городом или в его черте —
    точнее самого города. Помечаем его полем city, как ориентир, и на
    карте поста он заменит город (см. tools/build_web_data.py)."""
    import math
    cities = [p for p in uniq if p["scope"] == "ru" and not p.get("exact") and not is_local(p)
              and not any(w in p["name"] for w in REGION_NAME_WORDS) and p["name"] not in REGIONS]
    out = []
    for p in uniq:
        if p in cities and not p.get("city"):
            small = city_population(p["name"])
            for q in cities:
                if q is p:
                    continue
                big = city_population(q["name"])
                # город заметно крупнее, а посёлок — в пределах его агломерации
                if big < 300_000 or big < small * 5:
                    continue
                k = math.cos(math.radians(p["lat"]))
                d = math.hypot(p["lat"] - q["lat"], (p["lon"] - q["lon"]) * k) * 111
                if d <= (50 if big >= 1_000_000 else 25):
                    p = dict(p, city=q["name"])
                    break
        out.append(p)
    return out


def drop_weak_mentions(places, uniq):
    """Убирает места, названные не как место поста, а для сравнения.

    «У центра Твери совершенно петербургский характер», «московский модерн
    в Самаре» — прилагательное от города говорит о сходстве или стиле.
    «Застройка напоминает архангельские бараки Соломбалы» — сравнение.

    Такие упоминания отбрасываем, только если в посте есть место той же
    вкладки, названное всерьёз: иначе подпись «Питерский двор-колодец»
    осталась бы без Петербурга. Вкладку учитываем, чтобы «московский
    ресторан „Прага“» не уехал в Чехию.

    Упоминание засчитывается всерьёз, если хотя бы раз город назван
    существительным и не в сравнении: «В Твери… тверской кремль» — Тверь.
    Сравнение, прозвище и кавычки уступают любому настоящему месту, а
    прилагательное — только месту своей вкладки.
    """
    strong = {p["name"] for p in places if not p["adj"] and not p["cmp"]}
    strong_scopes = {p["scope"] for p in uniq if p["name"] in strong}
    compared = {p["name"] for p in places if p["cmp"]} - strong
    # «Хорошая пензенская задумка — музей одной картины… Картина написана в
    # блокадном Ленинграде»: прилагательное слабое, но «где написана» ещё
    # слабее — уступает и ему. Прочие сравнения прилагательному не уступают:
    # «в 15 километрах от псковского Порхова» — Порхов и есть место поста
    adjectives = {p["name"] for p in places if p["adj"] and not p["cmp"]} - strong
    made = ({p["name"] for p in places if p.get("made")}
            - {p["name"] for p in places if p["cmp"] and not p.get("made")} - strong - adjectives)
    out = []
    for p in uniq:
        if p["name"] in strong:
            out.append(p)
        elif p["name"] in made:
            if not strong and not adjectives:
                out.append(p)
        # сравнение и прозвище — не место поста, какой бы стране ни
        # принадлежал город: «Касимов — русский Марбург»
        elif p["name"] in compared:
            if not strong:
                out.append(p)
        # прилагательное — только своей вкладки: «московский ресторан
        # „Прага“» остаётся Москвой
        elif p["scope"] not in strong_scopes:
            out.append(p)
    return out


OWN_POINT_KM = 3


def drop_far_from_own(places):
    """Автор сам дал точку (координаты, ссылка на карту) — точные места
    дальше OWN_POINT_KM от всех его точек упомянуты мимоходом: «Пионерлагерь
    „Космонавт“… принадлежал заводу „Красный Треугольник“. Координаты:
    59.31, 29.93» — завод в 69 км, в Петербурге, это не место поста. Ближе —
    обычно то же самое здание («Дом Ярошенко» и точка у Дома Ярошенко) или
    соседнее, их оставляем."""
    own = [p for p in places if p.get("own")]
    if not own:
        return places

    def km(a, b):
        la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
        h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
        return 6371 * 2 * math.asin(math.sqrt(h))
    return [p for p in places
            if p.get("own") or not p.get("exact") or min(km(p, o) for o in own) <= OWN_POINT_KM]


def parts_of_city(places, text, gaz):
    """«Тула, Заречье.» — подпись строкой «город, место»: место — часть этого
    города. В справочнике районов тульского Заречья нет, и ключ доставался
    подмосковному в 170 км (09.10, жалоба). Если одноимённое место дальше
    80 км от названного рядом города — это не оно: тег «мкр Заречье» при
    городе, его ищет геокодер. Ближе — оставляем: «Тула, Венёв» (45 км) —
    сам Венёв. Только целая строка из двух названий, не перечень городов.
    Не трогаем республики («Алатырь, Чувашия» — у них нет населения в
    справочнике городов) и места, у которых есть одноимённый район этого
    города («Зеленогорск, Санкт-Петербург» — район берёт prefer_local_homonyms)."""
    import math

    def km(a, b):
        k = math.cos(math.radians(a["lat"]))
        return math.hypot(a["lat"] - b["lat"], (a["lon"] - b["lon"]) * k) * 111

    lines = {ln.strip().rstrip(".!").strip() for ln in text.split("\n") if ln.strip()}
    by_raw = {p["raw"]: p for p in places if p.get("raw") and p["scope"] == "ru"}
    drop, tags = set(), []

    # только небольшое место: город так в микрорайон не превращается
    # («мкр Тверь», «мкр Хвалынск» — первая попытка 09.10)
    def far_part(city, part):
        return (not part.get("parent")
                and 0 < city_population(part["name"]) < min(30_000, city_population(city["name"]))
                and not any(w in part["name"].lower() for w in REGION_WORDS)
                and not any(km(e, city) < 60 for e in gaz.alts.get(part.get("key"), ()))
                and km(city, part) > 80)

    # «церковь Сретения в районе Заречье» в посте про Вологду (09.10): после
    # «район»/«микрорайон» — часть города поста, а не одноимённое село за
    # 400 км. Город — крупнейший из названных (и прилагательным: «вологодский»)
    cities = [p for p in places if p["scope"] == "ru" and city_population(p["name"]) > 0]
    for part in places:
        # имя района — в именительном: «в районе Заречье», «района Кузнечиха»;
        # «в районе Миасса» — около Миасса, это не район
        if part.get("pos") is None or part["scope"] != "ru" or part.get("raw") != part["name"]:
            continue
        if not re.search(r"(?:микро)?район[еау]?\s+$", text[max(0, part["pos"] - 14):part["pos"]], re.I):
            continue
        # «в районе Лихой и Россоши» — около двух станций, перечень — не район
        after = text[part["pos"] + len(part["raw"]):part["pos"] + len(part["raw"]) + 30]
        if re.match(r"\s*(?:,|и\s)\s*[А-ЯЁ]", after):
            continue
        hosts = [c for c in cities if c is not part]
        if not hosts:
            continue
        city = max(hosts, key=lambda c: city_population(c["name"]))
        if far_part(city, part):
            drop.add(id(part))
            tags.append(f"мкр {part['name']}")
    for line in lines:
        parts = [x.strip() for x in re.split(r",|\s[—–-]\s", line)]
        if len(parts) != 2 or not all(x in by_raw for x in parts):
            continue
        a, b = by_raw[parts[0]], by_raw[parts[1]]
        for city, part in ((a, b), (b, a)):
            if id(part) not in drop and not city.get("adj") and far_part(city, part):
                drop.add(id(part))
                tags.append(f"мкр {part['name']}")
                break
    return [p for p in places if id(p) not in drop], tags


def classify(post, gaz, countries):
    text = post["text"]
    if not text:
        return {"group": "none", "places": [], "streets": [], "countries": [], "both": False}
    places, ctry = find_places(text, gaz, countries)
    places, city_parts = parts_of_city(places, text, gaz)
    places = metro_city(places, text, gaz)
    places = inherit_in_lists(places, text)
    # точные координаты надёжнее любого разбора — ставим их первыми
    places = find_coordinates(text) + places
    streets = find_streets(text)
    named_cities = {p["name"] for p in places if not p.get("cmp")}
    bare_spans = []
    streets += [s for s in find_bare_streets(text, named_cities, bare_spans)
                if s.split(",")[0] not in {x.split(",")[0] for x in streets}]
    # «на Малой Дмитровке» — улица, а не Дмитровск-Орловский
    if bare_spans:
        places = [p for p in places if "pos" not in p
                  or not any(a <= p["pos"] < b for a, b in bare_spans)]
    localities = find_localities(text, places) + [t for t in city_parts]
    # «село Питер… Поехали в Питер?»: раз в посте есть местность с таким
    # названием, то же слово в других местах — эта местность, а не
    # одноимённый город (Питер — прозвище Петербурга)
    loc_names = {lemma(t.split(" ", 1)[1].split()[0]) for t in localities if " " in t and not t.endswith("слобода")}
    if loc_names:
        places = [p for p in places
                  if not (p.get("raw") and lemma(p["raw"].split()[0]) in loc_names
                          and lemma(p["name"].split()[0]) not in loc_names)]
    # «гостиница „Россия“», «кинотеатр „Москва“»: название, совпавшее с
    # городом или страной, геокодер нашёл бы как сам город
    # и «в центре СССР» — не заведение: название после типа — страна
    # (только крупного города: «тюрьма „Кресты“» — не деревня Кресты)
    def big_place(o):
        e = gaz.index.get(tuple(lemma(w) for w in TOKEN_RE.findall(o)))
        return bool(e) and (e["scope"] != "ru" or e["pop"] >= 100_000 * 50)
    objects = [o for o in find_objects(text)
               if not big_place(o)
               and tuple(lemma(w) for w in TOKEN_RE.findall(o)) not in countries
               and (lemma(TOKEN_RE.findall(o)[-1]),) not in countries
               and TOKEN_RE.findall(o)[-1] not in ("СССР", "США", "РФ", "РСФСР", "СНГ", "ЕС", "ООН")]
    cities = context_cities(places)
    # улицы и объекты с координатами — самые точные места поста: ставим
    # их первыми и считаем упоминаниями всерьёз
    regions = post_regions(places)
    points, located = geocoded_points(
        [x for x in streets if x not in {p["name"] for p in places}], objects, cities,
        localities, regions)
    # места из словаря найденных — если геокодер то же место не нашёл
    near = lambda a, b: abs(a["lat"] - b["lat"]) < 0.002 and abs(a["lon"] - b["lon"]) < 0.004
    known = [k for k in find_known_places(text, cities)
             if not any(near(k, p) for p in points)]
    places = points + known + places
    # после геокодера: точки улиц и объектов уже подобраны к исходному
    # названию города («Павловский дворец · Павловск»)
    places = prefer_local_homonyms(places, gaz)
    streets = [x for x in streets if x not in located]
    localities = [x for x in localities if x not in located]

    uniq, seen = [], set()
    for p in places:
        if p["name"] not in seen:
            seen.add(p["name"])
            uniq.append(p)

    # «Гостиный двор» и «Речной вокзал» засчитываем, только если в посте
    # назван и сам город: иначе костромской гостиный двор уезжает в
    # Петербург, а тверской речной вокзал — в Москву
    named = {p["name"] for p in uniq}
    uniq = [p for p in uniq
            if not p.get("generic") or (p.get("parent") in named)]
    uniq = drop_weak_mentions(places, uniq)
    uniq = drop_foreign_landmarks(uniq)
    uniq = drop_incidental(places, uniq, text)
    # город ориентира или района — чтобы на карте ориентир заменил город
    uniq = [dict(p, city=city_of(p)) if is_local(p) else p for p in uniq]
    uniq = attach_suburbs(uniq)
    uniq = drop_far_from_own(uniq)

    has_ru = any(p["scope"] == "ru" for p in uniq)
    has_foreign = any(p["scope"] == "foreign" for p in uniq)
    # страна без города — сигнал слабый, поэтому засчитываем только оборот
    # «в <Стране>». Россия так не считается: город она не называет, а
    # «в России» встречается в каждом втором рассуждении канала
    abroad = any(c["located"] and c["name"] != "Россия" for c in ctry)

    # Местность («деревня Канфранк, Испания») — не обязательно в России,
    # поэтому во вкладку «Россия» она ведёт, только если другая страна
    # в посте не названа вовсе
    foreign_country = any(c["name"] != "Россия" for c in ctry)
    street_ru = bool(streets) and not has_foreign
    local_ru = bool(localities) and not has_foreign and not foreign_country

    if has_ru or street_ru or local_ru:
        group = "ru"
    elif has_foreign or abroad:
        group = "foreign"
    else:
        group = "none"
    # «Дворцовая площадь» уже нашлась как ориентир с координатами —
    # тег улицы с тем же названием не нужен
    named = {p["name"] for p in uniq}
    streets = [x for x in streets if x not in named]
    return {"group": group, "places": uniq[:8], "streets": streets + localities,
            "objects": [o for o in objects if o not in located], "cities": cities,
            "regions": {REGION_STATE.get(p["name"], p["name"]): [p["lat"], p["lon"]]
                        for p in places
                        if REGION_STATE.get(p["name"], p["name"]) in regions},
            "countries": [c["name"] for c in ctry][:3], "both": has_ru and has_foreign}


def prepare_nearest(gaz):
    """Города для nearest_city: только населённые пункты, без районов."""
    seen = set()
    _nearest.clear()
    for e in gaz.index.values():
        if e["name"] in seen or e["parent"] or e["pop"] < 5000:
            continue
        seen.add(e["name"])
        _nearest.append(e)


def main():
    global BANNED
    gaz, countries = load_gazetteer(), load_countries()
    print("ключей в справочнике:", len(gaz.index))
    prepare_nearest(gaz)
    load_geocache()
    load_maplinks()
    print("раскрытых ссылок на карты:", len(MAPLINKS))
    print("точек в кэше геокодера:", sum(1 for v in GEOCACHE.values() if v))
    with open(os.path.join(DATA, "posts.json"), encoding="utf-8") as f:
        posts = json.load(f)

    BANNED = build_stoplist(posts)
    print("в стоп-листе слов:", len(BANNED))

    stats = Counter()
    for i, p in enumerate(posts):
        res = classify(p, gaz, countries)
        p.update(res)
        stats[res["group"]] += 1
        if res["both"]:
            stats["_both"] += 1
        if i % 5000 == 0:
            print("  ...%d/%d" % (i, len(posts)), file=sys.stderr)

    out = os.path.join(DATA, "posts_classified.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False)
    total = len(posts)
    for k in ("ru", "foreign", "none", "_both"):
        print("%-8s %6d  %5.1f%%" % (k, stats[k], 100.0 * stats[k] / total))
    print("->", out)


if __name__ == "__main__":
    main()
