// Подложка для MapLibre — OpenFreeMap: полностью бесплатные векторные
// тайлы OpenStreetMap, без ключа, регистрации и лимитов на запросы.
//
// Раньше стояла только «dark»: на ней почти не видно улиц, а подписи
// шли латиницей через пробел с оригиналом. Теперь по умолчанию светлая
// positron, а выбор посетителя запоминается в браузере.
const OFM = 'https://tiles.openfreemap.org'
export const STYLES = [
  { key: 'light', title: 'Светлая', url: `${OFM}/styles/positron` },
  { key: 'detailed', title: 'Подробная', url: `${OFM}/styles/liberty` },
  { key: 'dark', title: 'Тёмная', url: `${OFM}/styles/dark` },
]

// Тайлы — через наш сервер: из России без VPN OpenFreeMap отвечает 200 и
// виснет, а сервер на Рег.ру забирает тот же тайл за 0,1 с (проверка
// deploy/check-map.sh, 30.09). nginx кэширует их (deploy/nginx.conf,
// /ofm/ и /osm/), в деве так же проксирует Vite (vite.config.js)
const PROXY = `${location.origin}/ofm`
const viaProxy = (url) => (typeof url === 'string' ? url.replace(OFM, PROXY) : url)

const STYLE_KEY = 'archimap.mapStyle.v1'

export function preferredStyle() {
  try {
    const saved = localStorage.getItem(STYLE_KEY)
    if (STYLES.some((s) => s.key === saved)) return saved
  } catch {
    /* приватный режим — берём стиль по умолчанию */
  }
  return STYLES[0].key
}

export function rememberStyle(key) {
  try {
    localStorage.setItem(STYLE_KEY, key)
  } catch {
    /* не смертельно */
  }
}

// Шрифт для подписей берём из этого же стиля, поэтому отдельный
// glyph-сервер больше не нужен.
export const FONT = ['Noto Sans Regular']

// В тайлах OpenMapTiles у каждого объекта есть name (как в OSM, для
// России — по-русски) и переводы name:xx. Стили OpenFreeMap собирают из
// них «Tverskaya ulitsa Тверская улица» — берём русское название, а если
// его нет, исходное.
const RU_NAME = ['coalesce', ['get', 'name:ru'], ['get', 'name']]

// Подписи улиц в стилях появляются поздно: второстепенные — только с 15-го
// масштаба, когда видно пару кварталов. Показываем их чуть раньше.
const LABEL_MINZOOM = {
  'highway-name-major': 11,
  'highway-name-minor': 13.5,
}

function usesName(expr) {
  return JSON.stringify(expr).includes('"name')
}

function localize(style, key) {
  for (const layer of style.layers) {
    const layout = layer.layout
    if (layer.type !== 'symbol' || !layout?.['text-field']) continue
    // номера трасс (["get","ref"]) не трогаем — это не названия
    if (usesName(layout['text-field'])) layout['text-field'] = RU_NAME
    const minzoom = LABEL_MINZOOM[layer.id]
    if (minzoom != null && layer.minzoom > minzoom) layer.minzoom = minzoom
    // в тёмной теме подписи улиц тёмно-серые на чёрном — осветляем
    if (key === 'dark' && layer['source-layer'] === 'transportation_name') {
      layer.paint = { ...layer.paint, 'text-color': '#c8c8cc', 'text-halo-color': '#111' }
    }
  }
  return style
}

// --- запасная подложка ------------------------------------------------
//
// 26.09.2026 у посетителей из России без VPN карта перестала грузиться,
// хотя сайт открывался: OpenFreeMap, видимо, недоступен. Запасная —
// растровые тайлы самого OpenStreetMap: без ключа, подписи на языке
// местности (у нас по-русски). Шрифт для чисел на кружках лежит у нас
// (public/fonts), иначе без OpenFreeMap пропали бы и они.
// тоже через наш сервер: tile.openstreetmap.org из России без VPN не отвечает
const FALLBACK_TILES = `${location.origin}/osm/{z}/{x}/{y}.png`
// сколько ждать стиль и первые тайлы OpenFreeMap, прежде чем сдаться. Из
// России без VPN запрос к OpenFreeMap не отказывает, а висит — при 8 с
// посетитель успевал решить, что карта сломана (29.09)
const STYLE_WAIT_MS = 3000
const TILES_WAIT_MS = 5000
// и потом: три ошибки основной подложки за 20 с или карта, не догрузившая
// тайлы за 10 с после сдвига, — тоже запасная
const ERROR_WINDOW_MS = 20000
const STALL_MS = 10000

// OpenFreeMap не ответил — браузер помнит это сутки, и следующие открытия
// сайта сразу берут запасную подложку, без ожидания. Потом пробуем снова:
// вдруг доступ вернулся
// v2: с 30.09 основная идёт через наш сервер — прежняя пометка «недоступна»
// устарела, пусть браузер попробует снова
const MEMO_KEY = 'archimap.mapFallback.v2'
const MEMO_MS = 24 * 3600 * 1000

function rememberedFallback() {
  try {
    return Date.now() - Number(localStorage.getItem(MEMO_KEY) || 0) < MEMO_MS
  } catch {
    return false
  }
}

function rememberFallback(on) {
  try {
    if (on) localStorage.setItem(MEMO_KEY, String(Date.now()))
    else localStorage.removeItem(MEMO_KEY)
  } catch {
    /* не запомнится — в следующий раз снова подождём */
  }
}

// перешли на запасную — остальные карты страницы (выбор точки, миникарты)
// сразу берут её, не ожидая заново
let fallbackOn = rememberedFallback()

// Что с подложкой — для надписи на карте: пока ждём OpenFreeMap, посетитель
// видит пустой прямоугольник и думает, что сайт сломан (29.09). 'loading' —
// ждём основную, 'fallback' — показана запасная, 'ok' — основная пришла
let status = fallbackOn ? 'fallback' : 'loading'
const listeners = new Set()

function setStatus(next) {
  if (status === next) return
  status = next
  listeners.forEach((f) => f(next))
}

export function mapStatus() {
  return status
}

export function onMapStatus(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function fallbackStyle(key) {
  return {
    version: 8,
    glyphs: `${location.origin}/fonts/{fontstack}/{range}.pbf`,
    sources: {
      osm: {
        type: 'raster',
        tiles: [FALLBACK_TILES],
        tileSize: 256,
        maxzoom: 19,
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      },
    },
    layers: [
      {
        id: 'osm',
        type: 'raster',
        source: 'osm',
        // тёмная тема из светлых тайлов: яркость наоборот, цвета приглушены
        paint:
          key === 'dark'
            ? { 'raster-brightness-min': 0.9, 'raster-brightness-max': 0.1, 'raster-saturation': -0.7 }
            : {},
      },
    ],
  }
}

const cache = new Map()

function fetchJSON(url) {
  return fetch(url, { signal: AbortSignal.timeout(STYLE_WAIT_MS) }).then((r) => {
    if (!r.ok) throw new Error(r.status)
    return r.json()
  })
}

// Все адреса OpenFreeMap в стиле — на наш прокси. Описание тайлов (source.url
// → /planet) внутри тоже держит прямые адреса, поэтому качаем его сами и
// вставляем в стиль готовым списком tiles
async function proxyStyle(style) {
  style.glyphs = viaProxy(style.glyphs)
  if (Array.isArray(style.sprite)) style.sprite = style.sprite.map((sp) => ({ ...sp, url: viaProxy(sp.url) }))
  else style.sprite = viaProxy(style.sprite)
  for (const src of Object.values(style.sources ?? {})) {
    if (src.url?.startsWith(OFM)) {
      const tj = await fetchJSON(viaProxy(src.url))
      delete src.url
      Object.assign(src, {
        tiles: tj.tiles,
        ...(tj.minzoom != null ? { minzoom: tj.minzoom } : {}),
        ...(tj.maxzoom != null ? { maxzoom: tj.maxzoom } : {}),
        ...(tj.bounds ? { bounds: tj.bounds } : {}),
        attribution: src.attribution ?? tj.attribution,
      })
    }
    if (src.tiles) src.tiles = src.tiles.map(viaProxy)
  }
  return style
}

// Стиль качаем сами, чтобы поправить подписи до того, как их увидит
// MapLibre. Не вышло за STYLE_WAIT_MS — запасная подложка.
export function loadStyle(key) {
  const def = STYLES.find((s) => s.key === key) ?? STYLES[0]
  if (fallbackOn) return Promise.resolve(fallbackStyle(def.key))
  if (!cache.has(def.key)) {
    cache.set(
      def.key,
      fetchJSON(viaProxy(def.url))
        .then(proxyStyle)
        .then((style) => localize(style, def.key))
        .catch(() => {
          cache.delete(def.key)
          fallbackOn = true
          rememberFallback(true)
          setStatus('fallback')
          return fallbackStyle(def.key)
        })
    )
  }
  return cache.get(def.key)
}

// Поставить карте подложку и проследить, что тайлы OpenFreeMap правда
// приходят: стиль может загрузиться, а тайлы — нет (или виснуть). Если за
// TILES_WAIT_MS не пришло ни одного тайла или посыпались ошибки — переключаемся
// на запасную. Возвращает функцию отмены — для очистки эффекта.
export function applyStyle(map, key) {
  let cancelled = false
  let timer = null
  let stall = null
  // время ошибок основной подложки за последние ERROR_WINDOW_MS
  const errors = []
  const giveUp = () => {
    if (cancelled || fallbackOn) return
    fallbackOn = true
    rememberFallback(true)
    setStatus('fallback')
    stop()
    map.setStyle(fallbackStyle(key), { diff: false })
  }
  const onData = (e) => {
    if (e.sourceId === 'openmaptiles' && e.tile) {
      clearTimeout(timer)
      rememberFallback(false)
      setStatus('ok')
    }
  }
  // Первый тайл — ещё не значит, что основная подложка работает: из России
  // без VPN часть запросов к OpenFreeMap проходит, часть виснет или
  // обрывается, и карта оставалась без улиц и подписей (29.09). Поэтому
  // следим всё время: ошибки подряд — запасная
  const onError = (e) => {
    const ours = e.sourceId === 'openmaptiles' || String(e.error?.message ?? e.error?.url ?? '').includes('openfreemap')
    if (!ours) return
    const now = Date.now()
    errors.push(now)
    while (errors.length && now - errors[0] > ERROR_WINDOW_MS) errors.shift()
    if (errors.length >= 3) giveUp()
  }
  // карту сдвинули, а тайлы так и не догрузились — висят: запасная
  const onMove = () => {
    clearTimeout(stall)
    stall = setTimeout(() => {
      let loaded = true
      try {
        loaded = map.isSourceLoaded('openmaptiles')
      } catch {
        /* источника ещё нет — стиль не встал, это ловит timer */
      }
      if (!loaded) giveUp()
    }, STALL_MS)
  }
  const onIdle = () => clearTimeout(stall)
  function stop() {
    clearTimeout(timer)
    clearTimeout(stall)
    map.off('sourcedata', onData)
    map.off('error', onError)
    map.off('moveend', onMove)
    map.off('idle', onIdle)
  }
  loadStyle(key).then((style) => {
    if (cancelled) return
    map.setStyle(style, { diff: false })
    if (fallbackOn) return
    map.on('sourcedata', onData)
    map.on('error', onError)
    map.on('moveend', onMove)
    map.on('idle', onIdle)
    timer = setTimeout(giveUp, TILES_WAIT_MS)
    onMove()
  })
  return () => {
    cancelled = true
    stop()
  }
}

// Стиль просим сразу при загрузке сайта, не дожидаясь карты: пока качаются
// данные постов (ru.json — 14 МБ), станет ясно, отвечает ли OpenFreeMap, и
// карта встанет сразу нужная — без трёх секунд ожидания при первом визите
if (!fallbackOn) loadStyle(preferredStyle())

// На телефоне карта во весь экран перехватывала любое движение пальцем, и
// страницу было не пролистать до подвала со входом для админа (30.09).
// «Совместные жесты» MapLibre: одним пальцем листается страница, карта —
// двумя, при попытке одним — подсказка. На компьютере не включаем: там это
// заставило бы приближать колесом только с Ctrl
export function touchOptions() {
  const touch = typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches
  return touch
    ? {
        cooperativeGestures: true,
        locale: { 'CooperativeGesturesHandler.MobileHelpText': 'Чтобы двигать карту, используйте два пальца' },
      }
    : {}
}
