import { useEffect, useRef, useState } from 'react'
import { Map as MapLibreMap, Marker, NavigationControl } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { applyStyle, preferredStyle } from '../mapStyle.js'
import { formatCoords, parseCoords } from '../coords.js'
import YandexPicker, { MapSourceToggle, useMapSource } from './YandexPicker.jsx'
import { api, readToken } from '../useEdits.js'

// «Найти место» у админа — над обеими картами. Поиск через наш сервер
// (/api/place-search: OpenStreetMap — Photon и Nominatim, адреса — Яндекс),
// рядом с точкой поста. Встроенный поиск Яндекс-карты не годится: он ищет
// по организациям, а этот API нашим ключом не оплачен (09.10, «памятник
// Тысячелетию России»). Находка — кнопка: нажал — точка там.
function PlaceSearch({ initial, near, onPick }) {
  const [q, setQ] = useState(initial ?? '')
  const [state, setState] = useState({ busy: false, items: null, error: '', asked: '' })
  const run = () => {
    const query = q.trim()
    if (!query) return setState({ busy: false, items: null, error: 'Напишите, что искать: название или адрес', asked: '' })
    setState((s) => ({ ...s, busy: true, error: '' }))
    const params = new URLSearchParams({ q: query })
    if (near) {
      params.set('lat', near.lat)
      params.set('lon', near.lon)
    }
    api(`/place-search?${params}`, { token: readToken() })
      .then(({ items }) => setState({ busy: false, items, error: '', asked: query }))
      .catch((e) => setState({ busy: false, items: null, error: e.message, asked: query }))
  }
  return (
    <div className="place-search">
      <div className="picker__row">
        <input
          className="field"
          type="search"
          placeholder="Найти место: название или адрес"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              run()
            }
          }}
        />
        <button type="button" className="row-action" onClick={run}>
          {state.busy ? 'Ищу…' : 'Найти'}
        </button>
      </div>
      {state.error && <p className="picker__hint picker__hint--bad">{state.error}</p>}
      {state.items && state.items.length === 0 && (
        <p className="picker__hint">
          По «{state.asked}» ничего не нашлось. Попробуйте одно название, без «памятник» или «церковь», или
          поставьте точку на карте.
        </p>
      )}
      {state.items?.length > 0 && (
        <ul className="place-search__list">
          {state.items.map((x) => (
            <li key={`${x.lat},${x.lon}`}>
              <button type="button" className="place-search__item" onClick={() => onPick(x)}>
                <span className="place-search__name">{x.name}</span>
                <span className="place-search__meta">
                  {x.text !== x.name ? x.text.replace(`${x.name}, `, '') : ''}
                  {x.km != null && ` · ${String(x.km).replace('.', ',')} км`}
                  {x.src === 'yandex' ? ' · Яндекс' : x.src === 'wikidata' ? ' · Викидата' : ' · OSM'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// Выбор точки: координаты можно вставить в поле, а можно ткнуть в карту
// или перетащить метку. Поле и карта всегда показывают одно и то же.
//
// value — { lat, lon } или null; around — с чего начать показ карты,
// если точки ещё нет (например, город, найденный в посте).
// search — что искать на Яндекс-карте сразу при открытии (у админа)
export default function LocationPicker({ value, onChange, around, search }) {
  const container = useRef(null)
  const mapRef = useRef(null)
  const markerRef = useRef(null)
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange

  const [text, setText] = useState(() => formatCoords(value))
  const [bad, setBad] = useState(false)
  // «Не понимаю координаты» — не с первой цифры, а когда человек закончил:
  // ушёл из поля или перестал печатать на секунду (справочник Бирмана,
  // «Валидация»; 13.10)
  const [badShown, setBadShown] = useState(false)
  const badTimer = useRef(null)
  useEffect(() => () => clearTimeout(badTimer.current), [])
  const [locating, setLocating] = useState(false)
  const [geoError, setGeoError] = useState('')
  // Яндекс-карта — только админу (ключ и суточный лимит — на сервере);
  // usage — { used, limit } за сегодня, null — Яндекс недоступен
  const { usage, setUsage, yandexOn: showYandex, choose: chooseSource } = useMapSource()

  // точка с Яндекс-карты: то же, что клик по нашей — и поле, и метка
  const pickFromYandex = (point) => {
    setText(formatCoords(point))
    setBad(false)
    onChangeRef.current(point)
  }
  // находка строки «Найти место»: точка там, обе карты едут к ней
  const [focus, setFocus] = useState(null)
  const pickFound = (x) => {
    const point = { lat: x.lat, lon: x.lon }
    setText(formatCoords(point))
    setBad(false)
    onChangeRef.current(point)
    setFocus(point)
    mapRef.current?.flyTo({ center: [point.lon, point.lat], zoom: 17, duration: 700 })
  }

  useEffect(() => {
    const start = value ?? around
    const map = new MapLibreMap({
      container: container.current,
      style: { version: 8, sources: {}, layers: [] }, // настоящий стиль подгрузится ниже
      center: start ? [start.lon, start.lat] : [45, 57],
      zoom: value ? 15 : around ? 11 : 2.6,
      attributionControl: { compact: true },
    })
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    const unstyle = applyStyle(map, preferredStyle())

    map.on('click', (e) => {
      const point = { lat: e.lngLat.lat, lon: e.lngLat.lng }
      setText(formatCoords(point))
      setBad(false)
      onChangeRef.current(point)
    })
    mapRef.current = map
    return () => {
      unstyle()
      map.remove()
      mapRef.current = null
      markerRef.current = null
    }
    // карта создаётся один раз, дальше её двигают эффекты ниже
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // пока была открыта Яндекс-карта, наша стояла скрытой — пересчитать размер
  useEffect(() => {
    if (!showYandex) mapRef.current?.resize()
  }, [showYandex])

  // Метка следует за значением, откуда бы оно ни пришло.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (!value) {
      markerRef.current?.remove()
      markerRef.current = null
      return
    }
    if (!markerRef.current) {
      const marker = new Marker({ color: '#ff9500', draggable: true })
      marker.on('dragend', () => {
        const { lat, lng } = marker.getLngLat()
        const point = { lat, lon: lng }
        setText(formatCoords(point))
        setBad(false)
        onChangeRef.current(point)
      })
      markerRef.current = marker
    }
    markerRef.current.setLngLat([value.lon, value.lat]).addTo(map)
  }, [value])

  const fly = (point) =>
    mapRef.current?.flyTo({
      center: [point.lon, point.lat],
      zoom: Math.max(mapRef.current.getZoom(), 15),
      duration: 700,
    })

  const typed = (next) => {
    setText(next)
    if (!next.trim()) {
      setBad(false)
      onChange(null)
      return
    }
    const point = parseCoords(next)
    setBad(!point)
    clearTimeout(badTimer.current)
    setBadShown(false)
    if (!point) badTimer.current = setTimeout(() => setBadShown(true), 1200)
    if (point) {
      onChange(point)
      fly(point)
    }
  }

  const locate = () => {
    if (!navigator.geolocation) {
      setGeoError('браузер не умеет определять местоположение')
      return
    }
    setLocating(true)
    setGeoError('')
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false)
        const point = { lat: pos.coords.latitude, lon: pos.coords.longitude }
        setText(formatCoords(point))
        setBad(false)
        onChange(point)
        fly(point)
      },
      (err) => {
        setLocating(false)
        setGeoError(
          err.code === err.PERMISSION_DENIED
            ? 'нет доступа к местоположению — разрешите его в браузере'
            : 'не получилось определить местоположение'
        )
      },
      { enableHighAccuracy: true, timeout: 15000 }
    )
  }

  return (
    <div className="picker">
      <div className="picker__row">
        <input
          className={`field ${bad && badShown ? 'field--bad' : ''}`}
          placeholder="Координаты"
          value={text}
          onChange={(e) => typed(e.target.value)}
          onBlur={() => bad && setBadShown(true)}
        />
        <button type="button" className="row-action" onClick={locate} disabled={locating}>
          {locating ? 'Ищу…' : 'Где я'}
        </button>
      </div>
      {bad && badShown && <p className="picker__hint picker__hint--bad">Не понимаю координаты: нужны широта и долгота</p>}
      {geoError && <p className="picker__hint picker__hint--bad">{geoError}</p>}
      {readToken() && <PlaceSearch initial={search} near={value ?? around} onPick={pickFound} />}
      {usage && <MapSourceToggle usage={usage} yandexOn={showYandex} onChoose={chooseSource} />}
      {usage && search && !showYandex && usage.used < usage.limit && (
        <p className="picker__hint">
          Яндекс-карта сразу поищет «{search}» — у Яндекса есть улицы садовых товариществ и новые дома, которых нет у нас.{' '}
          <button type="button" className="row-action" onClick={() => chooseSource('yandex')}>
            Искать на Яндексе
          </button>
        </p>
      )}
      {/* наша карта не пропадает, пока открыт Яндекс: метка на ней следует
          за точкой, и при возврате не надо ждать загрузки */}
      <div ref={container} className="picker__map" style={showYandex ? { display: 'none' } : undefined} />
      {showYandex && (
        <YandexPicker value={value} onChange={pickFromYandex} around={around} onUsage={setUsage} search={search} focus={focus} />
      )}
      <p className="picker__hint">
        Координаты можно вставить из Яндекс- или Google-карт, например 55.7558, 37.6173.{' '}
        Нажмите на карту, чтобы поставить точку, или перетащите метку.
      </p>
    </div>
  )
}
