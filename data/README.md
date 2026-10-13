# data/

Рабочие данные конвейера. В репозиторий не входят (см. `.gitignore`):
справочники весят сотни мегабайт, разобранные посты содержат тексты
авторов каналов, кэши геокодеров и правки админа живут только локально
или на сервере. Здесь — откуда что берётся.

## Скачать вручную

| файл | откуда |
| --- | --- |
| `cities500.txt`, `cities15000.txt`, `alternateNamesV2.zip` (распаковать в `alternateNamesV2.txt`), `iso-languagecodes.txt` | GeoNames, https://download.geonames.org/export/dump/ (CC BY 4.0) |
| `russian-cities.json` | список городов России с населением и субъектом (формат `{name, subject, district, population, coords}`) |
| `egrkn/*.json` | единый реестр объектов культурного наследия, opendata.mkrf.ru, набор `7705851331-egrkn` (открывается только с российского адреса, ~1,3 ГБ) |
| `osm/russia.osm.pbf` | Geofabrik, ~4 ГБ; быстрее в несколько потоков — `tools/download_parallel.py` |

Запросы к Overpass для районов и метро лежат здесь же, `overpass_*.txt`;
их ответы сохраняются как `osm_districts_nodes.json`,
`osm_districts_admin.json`, `osm_metro.json`.

## Собирается скриптами

| файл | чем |
| --- | --- |
| `posts.json` | `tools/extract_posts.py` — из выгрузок каналов (папки из `tools/sources.py`) |
| `gazetteer.json` | `tools/build_gazetteer.py` |
| `districts.json` | `tools/build_districts.py` |
| `heritage.json` | `tools/fetch_heritage.py` — реестр наследия из Викидаты (CC0) |
| `egrkn.json` | `tools/import_egrkn.py` — из `egrkn/` |
| `osm/names.sqlite` | `tools/build_osm_names.py` — из `osm/russia.osm.pbf` |
| `maplinks.json` | `tools/resolve_maplinks.py` — короткие ссылки на карты |
| `street_geocode.json`, `reverse_state.json` | кэши `tools/geocode_streets.py` |
| `posts_classified.json` | `tools/classify.py` |
| `known_places.json` | `tools/build_known_places.py` |
| `llm_places.json`, `llm/` | `tools/llm_places.py` — ответы моделей и уточнённые точки |
| `reports.json` | `tools/make_report.py` |

Порядок запуска — в README.md проекта («Как устроен разбор»).

## Пишет сервер

`edits.json` (правки админа, скрытые посты, предложения моделей),
`user-posts.json` и `uploads/` (посты посетителей), `yandex-usage.json`,
`yandex-geocoder-usage.json` — `server/index.js`. На сервере и локально это
разные файлы.

## Только для чтения у себя

`bureau/` — советы Бюро Горбунова (`tools/fetch_bureau.py`), авторские
тексты, не публикуются.
