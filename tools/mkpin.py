# -*- coding: utf-8 -*-
"""Задания искателю (Sonnet): посты, где модель назвала объект, а точка
осталась на городе, или точки нет вовсе (объект при одной области).

    python tools/mkpin.py 255,256,257 489     # ответы answer-255…257 -> pin-in-489, 490, …
    python tools/mkpin.py all 600             # все предложения модели

Запускать из корня проекта. Не берёт посты с ручной точкой, скрытые, принятые
в отчётах («Принять: точнее не найти») и уже бывшие в pin-in (кроме «на
доработку»). По 6 постов на задание; агенту — «прочитай pin_prompt.md, N = …».
"""
import json, glob, re, sys
sys.path.insert(0, 'tools')
from llm_places import key_of
from edits_store import load_edits
# answers — «225,226,…» или «all» (все предложения модели)
answers, start = sys.argv[1], int(sys.argv[2])
d = json.load(open('data/llm_places.json', encoding='utf-8'))
if answers == 'all':
    keys = set(d)
else:
    keys = set()
    for n in [int(x) for x in answers.split(',')]:
        keys |= {a['key'] for a in json.load(open(f'data/llm/answer-{n:02d}.json', encoding='utf-8'))}
tried = set()
_e = load_edits()
# пост, которому админ уже поставил точку (локально или на стенде) или скрыл, искателю не нужен
tried |= {k for k, v in _e.get('edits', {}).items() if (v or {}).get('location')} | set(_e.get('hidden', {}))
# «Принять» в отчётах — админ решил, что точнее не найти; искателю не отдаём
tried |= set(_e.get('reviewed') or {})
for f in glob.glob('data/llm/pin-in-*.json'):
    tried |= {p['key'] for p in json.load(open(f, encoding='utf-8'))}
# пост «на доработку» искатель смотрит снова, даже если уже был в задании:
# у модератора новое пояснение («найти набережную реки Миасс», 15.10)
tried -= {x.get('key') for x in _e.get('suggestions', []) if x.get('status') == 'rework'}
posts = {key_of(p): p for p in json.load(open('data/posts_classified.json', encoding='utf-8'))}

# Пост без точки, но с объектом и областью («Мастрюкова гора · Самарская
# область», 16.10) тоже идёт искателю: геокодер по одной области объект не
# нашёл, и точки не осталось вовсе. point_now — центр области
REGION_WORDS = re.compile(r'\b(область|край|республика|автономный округ|ао)\b', re.I)
regions = {}
for r in json.load(open('data/gazetteer.json', encoding='utf-8')).get('regions', []):
    regions[REGION_WORDS.sub('', r['n']).strip().lower()] = {'lat': r['lat'], 'lon': r['lon']}


def region_point(name):
    return regions.get(REGION_WORDS.sub('', name or '').strip().lower())


rows = []
for k in sorted(keys):
    r = d.get(k)
    if not r or k in tried or k not in posts: continue
    if not r.get('candidates') or r.get('digest') or r.get('ad'): continue
    point = r.get('point')
    if point and ' · ' in (r.get('label') or ''): continue  # уже точная
    if all(c.strip().lower() in ((r.get('city') or '').lower(), (r.get('region') or '').lower()) for c in r['candidates']): continue
    if not point:
        point = region_point(r.get('region'))
    rows.append({'key': k, 'city': r.get('city', ''), 'region': r.get('region', ''), 'locality': r.get('locality', ''),
                 'candidates': r['candidates'], 'reason': r.get('reason', ''), 'point_now': point, 'text': posts[k]['text'][:3000]})
for i in range(0, len(rows), 6):
    json.dump(rows[i:i+6], open(f'data/llm/pin-in-{start + i//6}.json', 'w', encoding='utf-8', newline=chr(10)), ensure_ascii=False, indent=1)
print(len(rows), 'постов ->', f'pin-in-{start} ..', start + (len(rows) - 1) // 6 if rows else '-')
