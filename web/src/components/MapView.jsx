import { useEffect, useMemo, useRef, useState } from 'react'
import { Map as MapLibreMap, NavigationControl, LngLatBounds } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { FONT, STYLES, applyStyle, mapStatus, onMapStatus, preferredStyle, rememberStyle } from '../mapStyle.js'
import { formatCount, humanDate, mapPlaces, photoURL, plural, postKey, USER_SRC } from '../data.js'
import { visibleTags } from '../useEdits.js'

const SOURCE = 'places'
const THUMB = 52 // сторона превью в пикселях
const ICONS_PER_IDLE = 30 // сколько превью подгружаем за один проход

// Координаты у нас пока городские, поэтому на карту идёт не каждый пост
// отдельной точкой, а одна точка на место с количеством постов. Так карта
// не тонет в 17 тысячах меток и останется рабочей, когда появятся адреса:
// поменяется только то, по какому ключу группируем.
// Точку на карте даёт место своей вкладки или собственная точка поста
// посетителя.
function onMap(place, group) {
  return place.scope === group || place.scope === USER_SRC || place.scope === 'manual'
}

// Насколько точна точка поста — для статистики админа. Пост считается по
// самой точной своей точке: «Курский вокзал · Москва» рядом с «Москвой»
// идёт в «здание», а не в «город». Город одной Москвы почти бесполезен —
// его считаем отдельно, чтобы было видно, сколько ещё уточнять.
const PRECISION = [
  ['manual', 'Точка поставлена вручную'],
  ['model', 'Точка модели, ждёт решения'],
  ['object', 'Здание, объект, адрес с домом'],
  ['street', 'Улица без номера дома'],
  ['village', 'Село, посёлок'],
  ['city', 'Только город'],
  ['region', 'Только область, край, республика'],
  ['user', 'Посты посетителей'],
]
const STREET_RE = /(^|\s)(улица|переулок|проспект|шоссе|площадь|набережная|бульвар|проезд|тупик|аллея|линия|спуск|тракт)(\s|$)/i
const VILLAGE_RE = /^(с|пос|д|х|ст|рп|пгт|дер|п|н\. п)\.\s/
const REGION_RE = /(^|\s)(область|край|республика|округ)(\s|$)/i
const CAPITALS = new Set(['Москва', 'Санкт-Петербург'])

function precisionOf(place) {
  if (place.scope === USER_SRC) return 'user'
  if (place.model) return 'model'
  if (place.manual) return 'manual'
  const name = place.name.split(' · ')[0]
  if (place.parent) {
    if (VILLAGE_RE.test(name)) return 'village'
    if (STREET_RE.test(name) && !/,\s*\d/.test(name)) return 'street'
    return 'object'
  }
  return place.region || REGION_RE.test(name) ? 'region' : 'city'
}

// Сколько постов вкладки с какой точностью стоят на карте, и сколько без точки.
function precisionStats(posts, places, group, edits) {
  const rank = Object.fromEntries(PRECISION.map(([k], i) => [k, i]))
  const counts = { none: 0, capitals: 0 }
  for (const post of posts) {
    const onTab = mapPlaces(visibleTags(post, places, edits).found).filter((p) => onMap(p, group))
    if (!onTab.length) {
      counts.none++
      continue
    }
    let best = null
    for (const p of onTab) {
      const k = precisionOf(p)
      if (best === null || rank[k] < rank[best]) best = k
    }
    counts[best] = (counts[best] ?? 0) + 1
    if (best === 'city' && onTab.every((p) => CAPITALS.has(p.name))) counts.capitals++
  }
  return counts
}

function buildGeoJSON(posts, places, group, edits) {
  const byPlace = new Map()
  for (const post of posts) {
    // теги, убранные админом, исчезают и с карты
    for (const place of mapPlaces(visibleTags(post, places, edits).found)) {
      // часть постов упоминает и российский, и зарубежный город. На вкладке
      // «Россия» зарубежные точки показывать не надо — иначе карта
      // разъезжается на весь мир из-за пары упоминаний Парижа
      if (!onMap(place, group)) continue
      // у мест из справочника ключ — название, у точек посетителей свой
      const key = place.key ?? place.name
      let bucket = byPlace.get(key)
      if (!bucket) byPlace.set(key, (bucket = { place, posts: [] }))
      bucket.posts.push(post)
    }
  }

  const features = []
  for (const [key, { place, posts: list }] of byPlace) {
    const props = { key, scope: place.scope, count: list.length }
    // у одиночной точки вместо кружка показываем саму фотографию
    if (list.length === 1 && list[0].p[0]) {
      props.icon = 'ph-' + postKey(list[0])
      props.photo = photoURL(list[0], list[0].p[0])
    }
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [place.lon, place.lat] },
      properties: props,
    })
  }
  features.sort((a, b) => b.properties.count - a.properties.count)
  return { geojson: { type: 'FeatureCollection', features }, byPlace }
}

// Россия переходит через 180-й меридиан: Чукотка лежит на +179, а соседние
// с ней точки — на -179. Обычный LngLatBounds растягивается тогда на весь
// глобус, поэтому считаем охват дважды — как есть и со сдвигом западного
// полушария за 180 — и берём тот, что уже.
function boundsOf(features) {
  const make = (shift) => {
    const b = new LngLatBounds()
    for (const f of features) {
      const [lon, lat] = f.geometry.coordinates
      b.extend([shift && lon < 0 ? lon + 360 : lon, lat])
    }
    return b
  }
  const plain = make(false)
  const shifted = make(true)
  const span = (b) => b.getEast() - b.getWest()
  return span(shifted) < span(plain) ? shifted : plain
}

// Квадратное превью со скруглением и светлой рамкой — значок на карте.
function makeThumb(url) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = THUMB
      const ctx = canvas.getContext('2d')
      ctx.save()
      ctx.beginPath()
      ctx.roundRect(0, 0, THUMB, THUMB, 9)
      ctx.clip()
      // кроп «по большей стороне», чтобы картинка не сплющивалась
      const k = Math.max(THUMB / img.width, THUMB / img.height)
      const w = img.width * k
      const h = img.height * k
      ctx.drawImage(img, (THUMB - w) / 2, (THUMB - h) / 2, w, h)
      ctx.restore()
      ctx.beginPath()
      ctx.roundRect(0.75, 0.75, THUMB - 1.5, THUMB - 1.5, 9)
      ctx.strokeStyle = 'rgba(255,255,255,0.85)'
      ctx.lineWidth = 1.5
      ctx.stroke()
      resolve(ctx.getImageData(0, 0, THUMB, THUMB))
    }
    img.onerror = reject
    img.src = url
  })
}

// Только для админа: видно, сколько постов ещё стоит «просто на городе».
function PrecisionStats({ stats, total }) {
  // 64 поста из 17 тысяч — не «0 %»
  const pct = (n) => {
    const p = total ? (n / total) * 100 : 0
    return p > 0 && p < 1 ? '<1 %' : `${Math.round(p)} %`
  }
  const row = (key, title, n, sub) => (
    <tr key={key} className={sub ? 'map-stats__sub' : undefined}>
      <th>{title}</th>
      <td>{formatCount(n)}</td>
      <td>{pct(n)}</td>
    </tr>
  )
  const rows = []
  for (const [k, title] of PRECISION) {
    if (!stats[k]) continue
    rows.push(row(k, title, stats[k]))
    if (k === 'city' && stats.capitals) rows.push(row('capitals', 'из них Москва или Петербург', stats.capitals, true))
  }
  if (stats.none) rows.push(row('none', 'Без точки', stats.none))
  return (
    <section className="map-stats">
      <h4>Точность точек · {formatCount(total)} {plural(total, 'пост', 'поста', 'постов')}</h4>
      <table>
        <tbody>{rows}</tbody>
      </table>
    </section>
  )
}

// loading — посты ещё грузятся: карту показываем сразу, пустой (справочник
// Бирмана, «Воспринимаемая производительность»: 14 МБ постов — несколько
// секунд, и всё это время вместо карты была одна строка «Загружаю посты…»)
export default function MapView({ posts, places, sources, edits, group, admin, fitKey, onOpenPost, loading }) {
  const container = useRef(null)
  const mapRef = useRef(null)
  const [ready, setReady] = useState(false)
  const [selected, setSelected] = useState(null)
  const lastFit = useRef(null)
  const requested = useRef(new Set())
  const [styleKey, setStyleKey] = useState(preferredStyle)
  // основная подложка грузится или вместо неё запасная — пишем об этом на
  // карте, чтобы пустой прямоугольник не выглядел поломкой
  const [baseStatus, setBaseStatus] = useState(mapStatus)
  useEffect(() => onMapStatus(setBaseStatus), [])
  const geojsonRef = useRef(null)

  const { geojson, byPlace } = useMemo(
    () => buildGeoJSON(posts, places, group, edits),
    [posts, places, group, edits]
  )

  // посты, для которых точки не нашлось: названа только улица или страна
  const stats = useMemo(
    () => precisionStats(posts, places, group, edits),
    [posts, places, edits, group]
  )
  const withoutPoint = stats.none

  // Карта создаётся один раз за всё время жизни компонента. Стиль
  // подложки меняется потом через setStyle, см. эффект ниже.
  useEffect(() => {
    const map = new MapLibreMap({
      container: container.current,
      style: { version: 8, sources: {}, layers: [] },
      center: [55, 58],
      zoom: 2.6,
      attributionControl: { compact: true },
    })
    map.addControl(new NavigationControl(), 'top-right')

    // При смене подложки MapLibre выбрасывает и наши слои, и картинки
    // превью — поэтому всё своё добавляем заново на каждый style.load.
    map.on('style.load', () => {
      if (!map.getStyle().glyphs) return // пустой стиль до загрузки настоящего
      requested.current.clear()
      map.addSource(SOURCE, {
        type: 'geojson',
        data: geojsonRef.current ?? { type: 'FeatureCollection', features: [] },
        // координаты мест взяты из GeoNames и OpenStreetMap, обе лицензии
        // требуют указания источника — MapLibre покажет это в углу карты
        attribution:
          'Места: <a href="https://www.geonames.org/">GeoNames</a> (CC BY 4.0), ' +
          '<a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> (ODbL)',
      })

      map.addLayer({
        id: 'places-circle',
        type: 'circle',
        source: SOURCE,
        paint: {
          // площадь кружка пропорциональна числу постов, поэтому корень
          'circle-radius': ['interpolate', ['linear'], ['sqrt', ['get', 'count']], 1, 5, 60, 40],
          // посты посетителей — оранжевые, чтобы их было видно среди точек каналов
          'circle-color': [
            'match',
            ['get', 'scope'],
            'ru',
            '#34c759',
            'manual',
            '#34c759',
            USER_SRC,
            '#ff9500',
            '#007aff',
          ],
          'circle-opacity': 0.7,
          'circle-stroke-width': 1.2,
          'circle-stroke-color': '#ffffff',
          'circle-stroke-opacity': 0.9,
        },
      })

      // Превью одиночных точек. Картинка подгружается лениво, а выражение
      // ['image', ...] просто ничего не рисует, пока её нет.
      map.addLayer({
        id: 'places-photo',
        type: 'symbol',
        source: SOURCE,
        minzoom: 5,
        filter: ['all', ['==', ['get', 'count'], 1], ['has', 'icon']],
        layout: {
          'icon-image': ['image', ['get', 'icon']],
          'icon-size': 0.7,
          'icon-allow-overlap': false,
        },
      })

      map.addLayer({
        id: 'places-count',
        type: 'symbol',
        source: SOURCE,
        // подпись только у заметных мест: иначе на мелком масштабе
        // получается каша из двузначных чисел
        filter: ['>=', ['get', 'count'], 15],
        layout: {
          'text-field': ['to-string', ['get', 'count']],
          'text-font': FONT,
          'text-size': 12,
          'text-allow-overlap': false,
          // при нехватке места подпись достаётся тому, у кого постов больше
          'symbol-sort-key': ['-', 0, ['get', 'count']],
        },
        paint: { 'text-color': '#ffffff', 'text-halo-color': '#131315', 'text-halo-width': 1.2 },
      })

      setReady(true)
    })

    // Обработчики по id слоя переживают смену стиля, поэтому вешаем их
    // один раз, а не на каждый style.load.
    for (const layer of ['places-circle', 'places-photo']) {
      map.on('click', layer, (e) => setSelected(e.features[0].properties.key))
      map.on('mouseenter', layer, () => {
        map.getCanvas().style.cursor = 'pointer'
      })
      map.on('mouseleave', layer, () => {
        map.getCanvas().style.cursor = ''
      })
    }

    // «idle» срабатывает, когда карта закончила рисовать: самое время
    // догрузить превью для того, что попало в кадр
    map.on('idle', () => loadVisibleThumbs(map, requested.current))

    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
      setReady(false)
    }
  }, [])

  // подложка OpenFreeMap, а если она не грузится — запасная (mapStyle.js)
  useEffect(() => (mapRef.current ? applyStyle(mapRef.current, styleKey) : undefined), [styleKey])

  const chooseStyle = (key) => {
    rememberStyle(key)
    setStyleKey(key)
  }

  // Данные обновляются при каждой смене фильтров.
  useEffect(() => {
    geojsonRef.current = geojson
    const map = mapRef.current
    if (!map || !ready) return
    map.getSource(SOURCE)?.setData(geojson)

    // Подгоняем охват при смене группы и фильтра по месту, но не на
    // каждую букву в поиске — иначе карта дёргалась бы при наборе.
    if (lastFit.current !== fitKey && geojson.features.length) {
      const first = lastFit.current === null
      lastFit.current = fitKey
      map.fitBounds(boundsOf(geojson.features), {
        padding: 60,
        maxZoom: 12,
        duration: first ? 0 : 600,
      })
    }
  }, [geojson, ready, fitKey])

  useEffect(() => setSelected(null), [group])

  const bucket = selected ? byPlace.get(selected) : null
  const selectedPlace = bucket?.place
  const selectedPosts = bucket?.posts ?? []

  return (
    <div className={`map-wrap ${selectedPlace ? 'map-wrap--open' : ''}`}>
      <div className="map-box">
        <div ref={container} className="map" />
        {baseStatus === 'loading' && (
          <p className="map-status map-status--center">
            Загружаю карту… Если основная не ответит, через пару секунд покажу запасную
          </p>
        )}
        {baseStatus !== 'loading' && loading && (
          <p className="map-status map-status--center">Загружаю посты…</p>
        )}
        {baseStatus === 'fallback' && (
          <p className="map-status">
            Основная карта сейчас недоступна — показана запасная (OpenStreetMap)
          </p>
        )}
        <div className="segmented map-styles">
          {STYLES.map((st) => (
            <button
              key={st.key}
              className={st.key === styleKey ? 'segmented__on' : ''}
              onClick={() => chooseStyle(st.key)}
            >
              {st.title}
            </button>
          ))}
        </div>
      </div>

      <aside className="map-panel">
        {selectedPlace ? (
          <>
            <header className="map-panel__head">
              <h3>{selectedPlace.name}</h3>
              <button onClick={() => setSelected(null)}>Закрыть</button>
            </header>
            <p className="map-panel__meta">
              {selectedPosts.length} {plural(selectedPosts.length, 'пост', 'поста', 'постов')} ·{' '}
              {selectedPlace.lat.toFixed(3)},{' '}
              {selectedPlace.lon.toFixed(3)}
            </p>
            {/* откуда точка — админу, чтобы не принять догадку модели за
                проверенное место */}
            {admin && selectedPlace.manual && (
              <p className={`map-panel__source ${selectedPlace.model ? 'map-panel__source--model' : ''}`}>
                {selectedPlace.model
                  ? `Точка от модели${selectedPlace.by ? ` (${selectedPlace.by})` : ''}, ждёт решения в модерации`
                  : 'Точка поставлена вручную'}
              </p>
            )}
            <div className="map-panel__list">
              {selectedPosts.slice(0, 200).map((post) => (
                <button key={postKey(post)} className="mini" onClick={() => onOpenPost(post)}>
                  {post.p[0] && <img src={photoURL(post, post.p[0])} loading="lazy" alt="" />}
                  <span>
                    <em>
                      {humanDate(post.d)}
                      {post.a
                        ? ` · ${post.a}`
                        : sources?.[post.src]
                          ? ` · ${sources[post.src].title}`
                          : ''}
                    </em>
                    {post.t.slice(0, 120) || 'без подписи'}
                  </span>
                </button>
              ))}
            </div>
          </>
        ) : loading ? (
          <p className="map-panel__hint">Загружаю посты — места появятся на карте через несколько секунд.</p>
        ) : (
          <p className="map-panel__hint">
            {formatCount(geojson.features.length)}{' '}
            {plural(geojson.features.length, 'место', 'места', 'мест')} на карте. Размер кружка — сколько постов
            привязано к месту, у одиночных точек вместо кружка фотография. Оранжевые
            точки — посты посетителей. Нажмите, чтобы посмотреть посты.
            {withoutPoint > 0 && (
              <>
                <br />
                <br />
                Ещё {formatCount(withoutPoint)} {plural(withoutPoint, 'пост', 'поста', 'постов')} на
                карту не {withoutPoint % 10 === 1 && withoutPoint % 100 !== 11 ? 'попал' : 'попали'}: в них названа
                только улица или только страна, координат для точки не хватает.
              </>
            )}
          </p>
        )}
        {!selectedPlace && admin && !loading && <PrecisionStats stats={stats} total={posts.length} />}
      </aside>
    </div>
  )
}

// Превью грузим только для того, что видно сейчас, и порциями: иначе
// на первой же отрисовке браузер полезет за тысячей фотографий.
function loadVisibleThumbs(map, requested) {
  if (map.getZoom() < 5) return
  let features
  try {
    features = map.queryRenderedFeatures({ layers: ['places-circle'] })
  } catch {
    return // слой ещё не готов
  }
  let budget = ICONS_PER_IDLE
  for (const f of features) {
    const { icon, photo } = f.properties
    if (!icon || !photo || requested.has(icon)) continue
    if (budget-- <= 0) break
    requested.add(icon)
    makeThumb(photo)
      .then((data) => {
        if (hasStyle(map) && !map.hasImage(icon)) map.addImage(icon, data)
      })
      .catch(() => {
        /* битая или недокачанная фотография — точка останется кружком */
      })
  }
}

function hasStyle(map) {
  try {
    return Boolean(map.getStyle())
  } catch {
    return false // карту уже размонтировали
  }
}
