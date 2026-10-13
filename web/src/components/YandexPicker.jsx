import { useEffect, useRef, useState } from 'react'
import { api, readToken } from '../useEdits.js'

// Вторая карта для «Указать точку вручную» — Яндекс: подробнее названия
// организаций в России, есть спутник (по снимку проще найти дом в деревне)
// и поиск. Грузится только когда админ на неё переключился.
//
// Плитки Яндекса нельзя подложить под MapLibre: у них своя проекция (на
// широте Москвы — сдвиг километров на двадцать) и условия запрещают брать
// плитки в обход API. Поэтому это отдельная карта JavaScript API 2.1: она
// сама переводит клик в широту и долготу.
//
// Ключ — VITE_YANDEX_MAPS_KEY в web/.env.local (в git не попадает). Он
// виден в коде страницы — у JS API иначе не бывает; в кабинете Яндекса
// ключ ограничен адресами стенда и localhost.
const KEY = import.meta.env.VITE_YANDEX_MAPS_KEY
let loading = null

// суточный лимит ключа считает сервер (server/index.js, /api/yandex-usage)
export const getYandexUsage = () => api('/yandex-usage', { token: readToken() })
export const countYandex = (body = { n: 1 }) =>
  api('/yandex-usage', { method: 'POST', token: readToken(), body }).catch(() => null)

function loadYmaps() {
  if (window.ymaps) return window.ymaps.ready().then(() => window.ymaps)
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const script = document.createElement('script')
      script.src = `https://api-maps.yandex.ru/2.1/?apikey=${KEY}&lang=ru_RU`
      script.onload = () => window.ymaps.ready(() => resolve(window.ymaps))
      script.onerror = () => {
        loading = null
        reject(new Error('Яндекс-карта не загрузилась'))
      }
      document.head.appendChild(script)
    })
  }
  return loading
}

export const yandexAvailable = Boolean(KEY)

// какая карта открыта — наша или Яндекс: общий выбор для формы точки и
// миникарт у координат, запоминается в браузере
const SOURCE_KEY = 'archimap.pickerMap.v1'
export function savedMapSource() {
  try {
    return localStorage.getItem(SOURCE_KEY) === 'yandex' ? 'yandex' : 'osm'
  } catch {
    return 'osm'
  }
}
export function saveMapSource(value) {
  try {
    localStorage.setItem(SOURCE_KEY, value)
  } catch {
    /* не запомнится — не страшно */
  }
}

// Переключатель «OpenStreetMap | Яндекс» с остатком суточного лимита.
// inline — внутри строки текста (миникарта у координат): только span
export function MapSourceToggle({ usage, yandexOn, onChoose, inline = false }) {
  const Box = inline ? 'span' : 'div'
  const exhausted = usage.used >= usage.limit
  return (
    <Box className="picker__source">
      <Box className="segmented">
        <button type="button" className={!yandexOn ? 'segmented__on' : ''} onClick={() => onChoose('osm')}>
          OpenStreetMap
        </button>
        <button type="button" className={yandexOn ? 'segmented__on' : ''} disabled={exhausted} onClick={() => onChoose('yandex')}>
          Яндекс
        </button>
      </Box>
      <span className="picker__hint">
        {exhausted
          ? `Лимит Яндекс-карт на сегодня исчерпан (${usage.used} из ${usage.limit}) — обновится в полночь по Москве`
          : `Яндекс: осталось ${usage.limit - usage.used} из ${usage.limit} на сегодня`}
      </span>
    </Box>
  )
}

// Выбор карты с лимитом: { usage, какая карта показана, выбрать }.
// usage === null — Яндекс недоступен. remember — запоминать выбор (форма
// точки); миникарты у координат всегда открываются нашей картой: иначе
// каждое раскрытие координат в модерации тратило бы запрос из лимита
export function useMapSource({ remember = true } = {}) {
  const [usage, setUsage] = useState(null)
  const [source, setSource] = useState(() => (remember ? savedMapSource() : 'osm'))
  useEffect(() => {
    if (!yandexAvailable || !readToken()) return
    getYandexUsage()
      .then(setUsage)
      .catch(() => setUsage(null))
  }, [])
  const yandexOn = source === 'yandex' && Boolean(usage) && usage.used < usage.limit
  const choose = (next) => {
    setSource(next)
    if (remember) saveMapSource(next)
  }
  return { usage, setUsage, yandexOn, choose }
}

// readOnly — только посмотреть (миникарта у координат): без клика и
// перетаскивания; inline — внутри строки текста, контейнер — span
// search — сразу найти на карте то, что назвала модель («улица Пределов
// Мечты, Орехово-Зуево»): улиц из садовых товариществ нет в OpenStreetMap,
// а Яндекс их знает
// focus — { lat, lon } найденного строкой «Найти место»: карта переезжает туда
export default function YandexPicker({ value, onChange, around, onUsage, readOnly = false, inline = false, search, focus }) {
  const container = useRef(null)
  const mapRef = useRef(null)
  const markRef = useRef(null)
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const [error, setError] = useState('')
  // итог поиска того, что назвала модель: что нашлось или почему нет
  const [found, setFound] = useState('')

  useEffect(() => {
    let cancelled = false
    loadYmaps()
      .then((ymaps) => {
        if (cancelled || !container.current) return
        const start = value ?? around
        const map = new ymaps.Map(container.current, {
          center: start ? [start.lat, start.lon] : [57, 45],
          zoom: value ? 16 : around ? 12 : 4,
          // спутник и гибрид — в переключателе слоёв. Встроенного поиска нет:
          // он ищет по организациям — платный API, с нашим ключом отвечал
          // «Сервер не смог обработать запрос» (09.10, «памятник Тысячелетию
          // России»); вместо него строка «Найти место» над картой (LocationPicker)
          controls: ['zoomControl', 'typeSelector'],
        }, {
          // значки мест Яндекса (памятники, кафе) иначе забирают нажатие себе,
          // и точку на памятнике было не поставить
          yandexMapDisablePoiInteractivity: true,
        })
        // загрузка карты — запрос к лимиту
        countYandex().then((u) => u && onUsage?.(u))
        if (!readOnly)
          map.events.add('click', (e) => {
            const [lat, lon] = e.get('coords')
            onChangeRef.current({ lat, lon })
          })
        mapRef.current = map
        // точка уже стоит — показываем её
        if (value) placeMark(ymaps, value)
        // точки нет или она на городе — ищем названное моделью (ещё запрос)
        // названное моделью ищем через наш сервер: у ключа карты геокодера
        // нет, а ключ геокодера в страницу не отдаём (server: /api/yandex-geocode)
        if (search) {
          api(`/yandex-geocode?q=${encodeURIComponent(search)}`, { token: readToken() })
            .then(({ items }) => {
              // Яндекс, не найдя улицы, отдаёт сам город — такой ответ не
              // находка, карта и так на городе
              const hit = items.find((i) => !['locality', 'province', 'country', 'area'].includes(i.kind))
              if (cancelled) return
              if (!hit) return setFound(`Яндекс не нашёл «${search}» точнее, чем город`)
              map.setCenter([hit.lat, hit.lon], hit.kind === 'street' ? 16 : 17)
              setFound(`Нашлось: ${hit.text} — нажмите на карту, чтобы поставить точку`)
            })
            .catch((e) => !cancelled && setFound(`Поиск не удался: ${e.message}`))
        }
      })
      .catch((e) => {
        if (cancelled) return
        // Яндекс отказал — скорее всего, кончился суточный лимит ключа
        setError(e.message)
        countYandex({ exhausted: true }).then((u) => u && onUsage?.(u))
      })
    return () => {
      cancelled = true
      mapRef.current?.destroy()
      mapRef.current = null
      markRef.current = null
    }
    // карта создаётся один раз; метку двигает эффект ниже
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function placeMark(ymaps, point) {
    const map = mapRef.current
    if (!map) return
    if (!markRef.current) {
      const mark = new ymaps.Placemark([point.lat, point.lon], {}, { draggable: !readOnly, preset: 'islands#orangeDotIcon' })
      mark.events.add('dragend', () => {
        const [lat, lon] = mark.geometry.getCoordinates()
        onChangeRef.current({ lat, lon })
      })
      map.geoObjects.add(mark)
      markRef.current = mark
    } else {
      markRef.current.geometry.setCoordinates([point.lat, point.lon])
    }
  }

  useEffect(() => {
    if (focus && mapRef.current) mapRef.current.setCenter([focus.lat, focus.lon], 17)
  }, [focus])

  // метка следует за значением, откуда бы оно ни пришло (поле, другая карта)
  useEffect(() => {
    if (!mapRef.current || !window.ymaps) return
    if (!value) {
      if (markRef.current) mapRef.current.geoObjects.remove(markRef.current)
      markRef.current = null
      return
    }
    placeMark(window.ymaps, value)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  if (error) return <p className="picker__hint picker__hint--bad">{error}</p>
  const Box = inline ? 'span' : 'div'
  return (
    <>
      {found && <p className="picker__hint">{found}</p>}
      <Box ref={container} className={inline ? 'picker__map point-map' : 'picker__map'} />
    </>
  )
}
