// Три группы, на которые разложены посты.
// На время теста посетителю показываем только российские места: по ним
// разбор точнее всего, а зарубежные и беспривязочные посты нужны прежде
// всего для проверки самого разбора.
export const GROUPS = [
  { key: 'ru', title: 'Россия', hint: 'город или улица в России' },
  { key: 'foreign', title: 'Другие страны', hint: 'только зарубежный город', adminOnly: true },
  { key: 'none', title: 'Без географии', hint: 'город и улица не названы', adminOnly: true },
  // не файл с постами, а посты всех групп, которые скрыл админ
  { key: 'hidden', title: 'Скрытые', hint: 'посты, скрытые админом', adminOnly: true, virtual: true },
  { key: 'flagged', title: 'Отмеченные', hint: 'посты, которые админ отметил на потом', adminOnly: true, virtual: true },
]

// Группы, у которых есть свой файл в public/data
export const FILE_GROUPS = GROUPS.filter((g) => !g.virtual).map((g) => g.key)

export function groupsFor(admin) {
  return admin ? GROUPS : GROUPS.filter((g) => !g.adminOnly)
}

// Файлы лежат в public/data, поэтому доступны по обычному fetch.
const cache = new Map()

export async function loadJSON(name) {
  if (!cache.has(name)) {
    cache.set(
      name,
      fetch(`/data/${name}.json`).then((r) => {
        if (!r.ok) throw new Error(`не удалось загрузить ${name}.json`)
        return r.json()
      })
    )
  }
  return cache.get(name)
}

// meta.places — массив вида [название, scope, lat, lon, код страны] и,
// у точных точек, шестым полем город, который они уточняют («*» — любой; «@» —
// точка самого автора: координаты или ссылка на карту, вытесняет все места).
// Точные точки — улицы, найденные геокодером, и координаты из текста
// поста (см. tools/build_web_data.py). Разворачиваем в объекты.
export function expandPlaces(rawPlaces) {
  return rawPlaces.map(([name, scope, lat, lon, cc, parent, region], i) => ({
    name,
    scope,
    lat,
    lon,
    cc,
    // у точных точек бывают одинаковые подписи с разными координатами,
    // поэтому на карте их различаем по номеру, а не по названию
    ...(parent ? { parent, key: `x${i}` } : {}),
    // седьмое поле — область, край, республика (tools/build_web_data.py)
    ...(region ? { region: true } : {}),
  }))
}

// «Шишмаревский переулок · Санкт-Петербург» — в теге хватает улицы
export function placeLabel(place) {
  return place.name.split(' · ')[0]
}

// Точка на карте для поста: если есть точные, они вытесняют уточняемые
// ими города — «Шишмаревский переулок» вместо всего Петербурга.
export function mapPlaces(found) {
  // точки, поставленные админом вручную, ничто не перебивает
  const manual = found.filter((p) => p.manual && !p.model)
  if (manual.length) return manual
  // точка, которую дал сам автор, надёжнее догадки модели, ждущей модерации
  const authors = found.filter((p) => p.parent === '@')
  if (authors.length) return authors
  const model = found.filter((p) => p.model)
  if (model.length) return model
  const exact = found.filter((p) => p.parent)
  if (!exact.length) return withoutRegions(found)
  // координаты из текста и ссылки на точку на карте — место, которое назвал
  // сам автор: на карте только они. «Пионерлагерь «Космонавт», координаты:
  // 59.31, 29.93» — завод «Красный Треугольник», которому лагерь
  // принадлежал, остаётся тегом, но второй точкой в Петербурге не встаёт
  const own = exact.filter((p) => p.parent === '@')
  if (own.length) return own
  const replaced = new Set(exact.map((p) => p.parent))
  if (replaced.has('*')) return exact
  return withoutRegions(found.filter((p) => p.parent || !replaced.has(p.name)))
}

// «Севск Брянской области»: область только уточняет, где город, — точка в
// Брянске рядом с точкой в Севске была бы лишней. Точку области убираем,
// если город поста — в ней (ближе REGION_KM от её точки); тег остаётся.
// Город далеко — область, наоборот, подсказка: «замок Нойхаузен (Гурьевск)»
// с Калининградской областью, а разбор взял Гурьевск кемеровский
const REGION_KM = 600

function km(a, b) {
  const rad = Math.PI / 180
  const h =
    Math.sin(((b.lat - a.lat) * rad) / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(((b.lon - a.lon) * rad) / 2) ** 2
  return 6371 * 2 * Math.asin(Math.sqrt(h))
}

function withoutRegions(places) {
  const rest = places.filter((p) => !p.region)
  return places.filter((p) => !p.region || !rest.some((c) => km(p, c) <= REGION_KM))
}

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
  'августа', 'сентября', 'октября', 'ноября', 'декабря']

// «2025-07-27 07:30» — для программ; людям — «27 июля 2025» или
// «27 июля 2025, 7:30»
export function humanDate(d, withTime = false) {
  const [date, time] = String(d ?? '').split(' ')
  const [y, m, day] = date.split('-').map(Number)
  if (!y || !m || !day) return d ?? ''
  let s = `${day} ${MONTHS[m - 1]} ${y}`
  if (withTime && time) s += `, ${time.replace(/^0(\d)/, '$1')}`
  return s
}

// то же для ISO-времени с сервера («2026-09-24T12:59:01Z»)
export function humanDateTime(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return humanDate(
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${d.getHours()}:${pad(d.getMinutes())}`,
    true
  )
}

export function formatCount(n) {
  return n.toLocaleString('ru-RU')
}

// «1 пост», «3 поста», «5 постов»
export function plural(n, one, few, many) {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few
  return many
}

// Размеры фотографий на сервере. То же правило зашито в
// tools/build_photos.py: первое фото поста получает обе версии,
// остальные — только превью. Менять надо в обоих местах.
export const PHOTO_SMALL = 400
export const PHOTO_LARGE = 800

// Каналов несколько, поэтому и фотографии, и ссылки на посты зависят от
// того, откуда пост. Раскладка адресов задана в web/vite.config.js.
export function photoURL(post, name, large = false) {
  // фотографии посетителей лежат на сервере правок и нарезки не проходят
  if (post.src === USER_SRC) return `/api/uploads/${name}`
  const size = large ? PHOTO_LARGE : PHOTO_SMALL
  return `/photos/${size}/${post.src}/${name}`
}

export function postURL(post, sources) {
  const channel = sources?.[post.src]?.channel
  return channel ? `https://t.me/${channel}/${post.i}` : null
}

// В списке улиц поста лежат и местности вроде «пос. Остров» или «Ямская
// слобода» (см. find_localities в tools/classify.py) — у них тип уже в
// названии, а к голому названию улицы добавляем «ул.»
const LOCALITY_RE = /^(?:пос\.|с\.|дер\.|х\.|ст-ца|мкр|сл\.|о\.|н\. п\.) |слобода$/

// тип улицы уже в названии: «Шишмаревский переулок», «набережная реки
// Фонтанки, 132Б» — «ул.» добавляем только к голому названию
const STREET_TYPE_RE = /(^|[ -])(улица|проспект|переулок|набережная|площадь|шоссе|бульвар|проезд|тупик|аллея|линия|тракт|спуск|въезд)([ ,]|$)/i

export function streetLabel(name) {
  if (LOCALITY_RE.test(name) || STREET_TYPE_RE.test(name)) return name
  return `ул. ${name}`
}

// ключ для React: номера сообщений в разных каналах совпадают
export function postKey(post) {
  return `${post.src}-${post.i}`
}

// --- поиск по тексту -----------------------------------------------------

// Поиск искал точную подстроку: «Казань» не находил «в Казани», «соборы» —
// «собор» (13.10, справочник Бирмана: поиск «принимает слова в разной
// форме»). Морфологии в браузере нет, поэтому срезаем окончание: у слова
// длиннее четырёх букв — самое длинное из списка, если остаётся хотя бы
// четыре буквы («казань» → «казан», «шухов» не трогаем). Слова запроса
// ищутся по отдельности, все должны быть в тексте. Опечаток не прощаем.
const ENDINGS = [
  'иями', 'ями', 'ами', 'ого', 'его', 'ому', 'ему', 'ыми', 'ими', 'ией',
  'ой', 'ей', 'ий', 'ый', 'ая', 'яя', 'ое', 'ее', 'ую', 'юю', 'ом', 'ем',
  'ам', 'ям', 'ах', 'ях', 'ов', 'ев', 'ью', 'ия', 'ие', 'ии',
  'а', 'я', 'ы', 'и', 'у', 'ю', 'о', 'е', 'ь', 'й',
]
const norm = (s) => s.toLowerCase().replaceAll('ё', 'е')

function stem(word) {
  if (word.length < 5) return word
  for (const end of ENDINGS) {
    if (word.endsWith(end) && word.length - end.length >= 4) return word.slice(0, -end.length)
  }
  return word
}

// запрос → основы слов; пустой запрос — пустой список
export function queryStems(query) {
  return norm(query)
    .split(/[^\p{L}\p{N}-]+/u)
    .filter(Boolean)
    .map(stem)
}

// текст поста в нижнем регистре и с «е» вместо «ё» — считаем один раз
const normed = new WeakMap()
export function matchesQuery(post, stems) {
  if (!stems.length) return true
  let text = normed.get(post)
  if (text === undefined) {
    text = norm(post.t ?? '')
    normed.set(post, text)
  }
  return stems.every((s) => text.includes(s))
}

// --- посты посетителей ---------------------------------------------------

// Посты посетителей живут на сервере (server/index.js) и приходят в своём
// виде. Здесь они превращаются в такие же записи, как посты каналов, —
// тогда карточки, окно поста, поиск и карта работают с ними без особых
// случаев. Отличий два: автор (поле a) и собственная точка (поле loc)
// вместо ссылки на справочник мест.
export const USER_SRC = 'user'

// Тег «еда» — не место, а тема: его ставит модель при разборе (поле f в
// данных, tools/build_web_data.py). В фильтре мест он живёт под значением,
// которое не совпадёт ни с одним названием места
export const FOOD_TAG = 'еда'
export const FOOD_FILTER = '#еда'

export const USER_SOURCE = {
  title: 'Посты посетителей',
  short: 'посетитель',
}

const pad = (n) => String(n).padStart(2, '0')

// та же запись даты, что у постов каналов: «2024-05-01 12:30»
function formatDate(iso) {
  const d = new Date(iso)
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  )
}

export function userPostToPost(u) {
  return {
    i: u.id,
    src: USER_SRC,
    d: formatDate(u.at),
    t: u.text,
    p: u.photos,
    np: u.photos.length,
    g: [],
    s: [],
    c: [],
    r: 0,
    a: u.nick,
    pending: u.status === 'pending',
    loc: {
      // ключ точки на карте: у каждого поста посетителя она своя, даже
      // если место названо так же, как город из справочника
      key: `${USER_SRC}-${u.id}`,
      name: u.place || 'Точка на карте',
      scope: USER_SRC,
      lat: u.lat,
      lon: u.lon,
    },
  }
}
