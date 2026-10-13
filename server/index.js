// Небольшой сервер для правок админа и предложений посетителей.
//
// Решения хранятся в data/edits.json на диске: файл можно открыть глазами,
// положить в git и скормить пайплайну.
//
// Запуск: npm run dev поднимает его вместе с Vite (см. web/package.json),
// отдельно — npm run dev:api. Настройки читаются из server/.env,
// образец — в .env.example.
import express from 'express'
import fs from 'node:fs/promises'
import path from 'node:path'
import crypto from 'node:crypto'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const DATA = path.resolve(__dirname, '..', 'data')
const STORE = path.join(DATA, 'edits.json')
// Посты посетителей — отдельным файлом: это не решения по разбору,
// а новое содержимое, и в git его класть не нужно.
const USER_STORE = path.join(DATA, 'user-posts.json')
const UPLOADS = path.join(DATA, 'uploads')
const PORT = Number(process.env.PORT ?? 5174)
const PRODUCTION = process.env.NODE_ENV === 'production'

// Пароль админа. Локально по умолчанию «dev», но выкладывать проект
// с этим паролем нельзя, поэтому в продакшене сервер без явно заданного
// ADMIN_PASSWORD просто не стартует.
const PASSWORD = process.env.ADMIN_PASSWORD ?? (PRODUCTION ? '' : 'dev')
if (!PASSWORD) {
  console.error('не задан ADMIN_PASSWORD — в продакшене это обязательно')
  process.exit(1)
}
if (PASSWORD === 'dev' && PRODUCTION) {
  console.error('пароль «dev» в продакшене недопустим')
  process.exit(1)
}

const TOKEN_TTL_MS = Number(process.env.TOKEN_TTL_HOURS ?? 12) * 3600_000
const MAX_SUGGESTIONS = Number(process.env.MAX_SUGGESTIONS ?? 5000)
// Сколько постов посетителей может ждать модерации одновременно: ручка
// открыта без входа, и без предела её можно завалить мусором.
const MAX_PENDING = Number(process.env.MAX_PENDING_POSTS ?? 300)
const MAX_PHOTOS = 4
const MAX_PHOTO_BYTES = 3 * 1024 * 1024

const app = express()
// За nginx настоящий адрес приходит в X-Forwarded-For. Доверять этому
// заголовку можно только когда перед нами действительно прокси, иначе
// ограничения обходятся подделкой заголовка.
if (process.env.TRUST_PROXY) app.set('trust proxy', process.env.TRUST_PROXY)
// Пост посетителя приходит с фотографиями в base64, поэтому для этой
// ручки предел больше; всем остальным хватает 64 КБ.
const bigJson = express.json({ limit: '16mb' })
const smallJson = express.json({ limit: '64kb' })
app.use((req, res, next) =>
  req.method === 'POST' && req.path === '/api/user-posts'
    ? bigJson(req, res, next)
    : smallJson(req, res, next)
)

// --- хранилище ---------------------------------------------------------

// Файл JSON на диске. Пишем через временный файл: если процесс упадёт
// на середине записи, прежняя версия останется целой.
function jsonStore(file, empty) {
  let writing = Promise.resolve()
  return {
    async load() {
      try {
        return { ...structuredClone(empty), ...JSON.parse(await fs.readFile(file, 'utf8')) }
      } catch (e) {
        if (e.code !== 'ENOENT') console.error(`не читается ${path.basename(file)}:`, e.message)
        return structuredClone(empty)
      }
    },
    save(state) {
      writing = writing.then(async () => {
        const tmp = file + '.tmp'
        await fs.mkdir(path.dirname(file), { recursive: true })
        await fs.writeFile(tmp, JSON.stringify(state, null, 2), 'utf8')
        await fs.rename(tmp, file)
      })
      return writing
    },
  }
}

const editsStore = jsonStore(STORE, { edits: {}, suggestions: [], bugs: {}, hidden: {}, flagged: {}, rules: [], reviewed: {} })
const load = () => editsStore.load()
const save = (state) => editsStore.save(state)

// next — счётчик номеров: номер поста входит в ключ «user-<номер>»,
// поэтому после удаления он не должен достаться другому посту.
const userStore = jsonStore(USER_STORE, { next: 1, posts: [] })

// --- ограничение частоты -----------------------------------------------

// Простое окно в памяти: для одного сервера этого достаточно, а лишней
// зависимости не нужно. Перезапуск счётчики обнуляет.
function rateLimit({ windowMs, max, message }) {
  const hits = new Map()
  return (req, res, next) => {
    const now = Date.now()
    const key = req.ip ?? 'unknown'
    const fresh = (hits.get(key) ?? []).filter((t) => now - t < windowMs)
    if (fresh.length >= max) {
      res.set('Retry-After', String(Math.ceil(windowMs / 1000)))
      return res.status(429).json({ error: message })
    }
    fresh.push(now)
    hits.set(key, fresh)
    // чтобы карта не росла бесконечно, изредка подчищаем остывшие записи
    if (hits.size > 5000) {
      for (const [k, times] of hits) {
        if (!times.some((t) => now - t < windowMs)) hits.delete(k)
      }
    }
    next()
  }
}

// Пароль общий и короткий, поэтому перебор ограничиваем жёстко.
const loginLimit = rateLimit({
  windowMs: 15 * 60_000,
  max: 10,
  message: 'слишком много попыток входа, попробуйте через четверть часа',
})

const suggestLimit = rateLimit({
  windowMs: 60 * 60_000,
  max: 20,
  message: 'слишком много уточнений за час',
})

const postLimit = rateLimit({
  windowMs: 60 * 60_000,
  max: 5,
  message: 'за час можно отправить не больше пяти постов',
})

// --- вход --------------------------------------------------------------

// Токены живут в памяти: перезапуск сервера разлогинивает, и это
// нормально для проекта такого размера.
const tokens = new Map() // token -> когда выдан

function sameSecret(a, b) {
  // Сравнение постоянного времени: иначе по задержке ответа можно
  // подбирать пароль посимвольно.
  const ab = Buffer.from(String(a))
  const bb = Buffer.from(String(b))
  if (ab.length !== bb.length) return false
  return crypto.timingSafeEqual(ab, bb)
}

function requireAdmin(req, res, next) {
  const token = (req.headers.authorization ?? '').replace(/^Bearer /, '')
  const issued = tokens.get(token)
  if (!issued) return res.status(401).json({ error: 'нужен вход в режим админа' })
  if (Date.now() - issued > TOKEN_TTL_MS) {
    tokens.delete(token)
    return res.status(401).json({ error: 'сессия истекла, войдите заново' })
  }
  next()
}

app.post('/api/login', loginLimit, (req, res) => {
  if (!sameSecret(req.body?.password ?? '', PASSWORD)) {
    return res.status(403).json({ error: 'неверный пароль' })
  }
  const token = crypto.randomUUID()
  tokens.set(token, Date.now())
  res.json({ token })
})

app.post('/api/logout', requireAdmin, (req, res) => {
  tokens.delete((req.headers.authorization ?? '').replace(/^Bearer /, ''))
  res.json({ ok: true })
})

// --- состояние ---------------------------------------------------------

app.get('/api/state', async (_req, res) => {
  res.json(await load())
})

const KEY_RE = /^[a-z0-9]+-\d+$/ // «канал-номер поста»

// Спрятать тег.
app.post('/api/edits/:key/hide', requireAdmin, async (req, res) => {
  const { key } = req.params
  const { kind, name } = req.body ?? {}
  if (!KEY_RE.test(key)) return res.status(400).json({ error: 'неверный ключ поста' })
  if (kind !== 'places' && kind !== 'streets') {
    return res.status(400).json({ error: 'kind: places или streets' })
  }
  if (typeof name !== 'string' || !name || name.length > 200) {
    return res.status(400).json({ error: 'нужно имя тега' })
  }
  const state = await load()
  const cur = state.edits[key] ?? { places: [], streets: [] }
  if (!cur[kind].includes(name)) cur[kind] = [...cur[kind], name]
  state.edits[key] = cur
  await save(state)
  res.json(state.edits)
})

// Вернуть посту все убранные теги.
app.delete('/api/edits/:key', requireAdmin, async (req, res) => {
  if (!KEY_RE.test(req.params.key)) {
    return res.status(400).json({ error: 'неверный ключ поста' })
  }
  const state = await load()
  // ручная точка — отдельное решение, «вернуть теги» её не снимает
  const { location, extra, related } = state.edits[req.params.key] ?? {}
  if (location)
    state.edits[req.params.key] = {
      places: [],
      streets: [],
      location,
      ...(extra ? { extra } : {}),
      ...(related ? { related } : {}),
    }
  else delete state.edits[req.params.key]
  await save(state)
  res.json(state.edits)
})

// Ручная точка поста: админ ставит место сам, если разбор и геокодер
// промахнулись. На карте она заменяет все найденные места поста.
// Точек может быть несколько — пост про два здания (ДК «Подмосковье» в
// Красногорске и его прообраз, ДК АЗЛК в Москве): тело { points: [...] },
// первая — location, остальные — extra. Одна точка — { lat, lon, name }.
// Точка с related: true — связанное место, а не место поста: пост про
// Староярмарочный собор в Нижнем Новгороде сравнивает его с Исаакиевским.
// Она хранится в related и на карте поста не стоит.
const MAX_POINTS = 5
app.post('/api/edits/:key/location', requireAdmin, async (req, res) => {
  const { key } = req.params
  if (!KEY_RE.test(key)) return res.status(400).json({ error: 'неверный ключ поста' })
  const raw = Array.isArray(req.body?.points) ? req.body.points.slice(0, MAX_POINTS * 2) : [req.body]
  const at = new Date().toISOString()
  const points = []
  const related = []
  for (const p of raw) {
    const point = readPoint(p)
    if (!point) return res.status(400).json({ error: 'нужны координаты' })
    const row = { ...point, name: String(p?.name ?? '').trim().slice(0, 120), at }
    if (p?.related) related.push(row)
    else points.push(row)
  }
  if (!points.length) return res.status(400).json({ error: 'нужна хотя бы одна точка самого поста' })
  if (points.length > MAX_POINTS || related.length > MAX_POINTS) {
    return res.status(400).json({ error: `не больше ${MAX_POINTS} точек поста и ${MAX_POINTS} связанных мест` })
  }
  const state = await load()
  const cur = state.edits[key] ?? { places: [], streets: [] }
  cur.location = points[0]
  if (points.length > 1) cur.extra = points.slice(1)
  else delete cur.extra
  if (related.length) cur.related = related
  else delete cur.related
  state.edits[key] = cur
  await save(state)
  res.json(state.edits)
})

app.delete('/api/edits/:key/location', requireAdmin, async (req, res) => {
  const { key } = req.params
  if (!KEY_RE.test(key)) return res.status(400).json({ error: 'неверный ключ поста' })
  const state = await load()
  const cur = state.edits[key]
  if (cur) {
    delete cur.location
    delete cur.extra
    delete cur.related
    if (!cur.places.length && !cur.streets.length) delete state.edits[key]
  }
  await save(state)
  res.json(state.edits)
})

// Предложение посетителя — вход не нужен, поэтому и ограничения строже.
// Координаты из формы: оба числа в допустимых пределах или ничего.
function readPoint(body) {
  const lat = Number(body?.lat)
  const lon = Number(body?.lon)
  if (body?.lat == null || body?.lon == null) return null
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return undefined
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return undefined
  // шесть знаков — это десяток сантиметров, точнее не бывает и у GPS
  return { lat: Math.round(lat * 1e6) / 1e6, lon: Math.round(lon * 1e6) / 1e6 }
}

app.post('/api/suggestions', suggestLimit, async (req, res) => {
  const { key, src, id } = req.body ?? {}
  const text = typeof req.body?.text === 'string' ? req.body.text.trim() : ''
  const point = readPoint(req.body)
  if (point === undefined) return res.status(400).json({ error: 'неверные координаты' })
  // уточнение места может состоять из одной точки на карте, без текста
  if (!text && !point) {
    return res.status(400).json({ error: 'пустое уточнение' })
  }
  if (key != null && !KEY_RE.test(String(key))) {
    return res.status(400).json({ error: 'неверный ключ поста' })
  }
  const state = await load()
  if (state.suggestions.length >= MAX_SUGGESTIONS) {
    return res.status(429).json({ error: 'накопилось слишком много уточнений' })
  }
  state.suggestions.unshift({
    id: crypto.randomUUID(),
    key,
    src: String(src ?? '').slice(0, 32),
    post: Number(id) || null,
    text: text.slice(0, 2000),
    ...(point ?? {}),
    at: new Date().toISOString(),
  })
  await save(state)
  res.json({ ok: true, count: state.suggestions.length })
})

// --- баг-репорт --------------------------------------------------------

// Админ помечает пост, у которого разбор сработал неправильно. Вместе с
// пометкой храним подпись и найденные места: тогда data/edits.json можно
// читать как список проблем, не открывая интерфейс.
app.post('/api/bugs/:key', requireAdmin, async (req, res) => {
  const { key } = req.params
  if (!KEY_RE.test(key)) return res.status(400).json({ error: 'неверный ключ поста' })
  const { src, id, date, text, places, note } = req.body ?? {}
  const state = await load()
  state.bugs[key] = {
    src: String(src ?? '').slice(0, 32),
    post: Number(id) || null,
    date: String(date ?? '').slice(0, 32),
    text: String(text ?? '').slice(0, 300),
    places: Array.isArray(places) ? places.slice(0, 12).map((x) => String(x).slice(0, 120)) : [],
    note: String(note ?? '').slice(0, 500),
    at: new Date().toISOString(),
  }
  await save(state)
  res.json(state.bugs)
})

app.delete('/api/bugs/:key', requireAdmin, async (req, res) => {
  if (!KEY_RE.test(req.params.key)) {
    return res.status(400).json({ error: 'неверный ключ поста' })
  }
  const state = await load()
  delete state.bugs[req.params.key]
  await save(state)
  res.json(state.bugs)
})

// --- скрытые посты -----------------------------------------------------

// Админ убирает пост из списка и с карты для всех. Пост не удаляется:
// его видно во вкладке «Скрытые», откуда его можно вернуть. Подпись
// храним, чтобы data/edits.json читался без интерфейса.
app.post('/api/hidden/:key', requireAdmin, async (req, res) => {
  const { key } = req.params
  if (!KEY_RE.test(key)) return res.status(400).json({ error: 'неверный ключ поста' })
  const state = await load()
  state.hidden[key] = {
    text: String(req.body?.text ?? '').slice(0, 200),
    at: new Date().toISOString(),
  }
  await save(state)
  res.json(state.hidden)
})

app.delete('/api/hidden/:key', requireAdmin, async (req, res) => {
  if (!KEY_RE.test(req.params.key)) {
    return res.status(400).json({ error: 'неверный ключ поста' })
  }
  const state = await load()
  delete state.hidden[req.params.key]
  await save(state)
  res.json(state.hidden)
})

// --- отмеченные посты ---------------------------------------------------

// Закладка админа: пост, с которым пока непонятно, что делать. Посетители
// видят его как обычно, админ — с меткой и во вкладке «Отмеченные».
app.post('/api/flagged/:key', requireAdmin, async (req, res) => {
  const { key } = req.params
  if (!KEY_RE.test(key)) return res.status(400).json({ error: 'неверный ключ поста' })
  const state = await load()
  state.flagged[key] = {
    text: String(req.body?.text ?? '').slice(0, 200),
    at: new Date().toISOString(),
  }
  await save(state)
  res.json(state.flagged)
})

app.delete('/api/flagged/:key', requireAdmin, async (req, res) => {
  if (!KEY_RE.test(req.params.key)) {
    return res.status(400).json({ error: 'неверный ключ поста' })
  }
  const state = await load()
  delete state.flagged[req.params.key]
  await save(state)
  res.json(state.flagged)
})

// --- решения по отчётам ----------------------------------------------------

// «Принять: места нет» в отчёте о круге разбора (модерация → «Отчёты»):
// модель не назвала места или искатель не нашёл объект, и админ согласен —
// пост уходит из списков ошибок во вкладку «Решено» (10.10, просьба
// пользователя). На сайте ничего не меняет.
app.post('/api/reviewed/:key', requireAdmin, async (req, res) => {
  const { key } = req.params
  if (!KEY_RE.test(key)) return res.status(400).json({ error: 'неверный ключ поста' })
  const state = await load()
  state.reviewed ??= {}
  state.reviewed[key] = {
    text: String(req.body?.text ?? '').slice(0, 200),
    at: new Date().toISOString(),
  }
  await save(state)
  res.json(state.reviewed)
})

app.delete('/api/reviewed/:key', requireAdmin, async (req, res) => {
  if (!KEY_RE.test(req.params.key)) {
    return res.status(400).json({ error: 'неверный ключ поста' })
  }
  const state = await load()
  state.reviewed ??= {}
  delete state.reviewed[req.params.key]
  await save(state)
  res.json(state.reviewed)
})

// Предложение модели (tools/llm_places.py): место поста, определённое по
// смыслу текста. Не применяется само — ждёт решения админа в модерации,
// как уточнение посетителя, но с пометкой from: "model". Повторная
// отправка для того же поста заменяет прежнее предложение модели.
app.post('/api/suggestions/model', requireAdmin, async (req, res) => {
  const s = req.body ?? {}
  if (!KEY_RE.test(String(s.key ?? ''))) return res.status(400).json({ error: 'неверный ключ поста' })
  const point = readPoint(s)
  if (point === undefined) return res.status(400).json({ error: 'неверные координаты' })
  const state = await load()
  state.suggestions = state.suggestions.filter((x) => !(x.from === 'model' && x.key === s.key))
  state.suggestions.unshift({
    id: crypto.randomUUID(),
    key: s.key,
    src: String(s.src ?? '').slice(0, 32),
    post: Number(s.post) || null,
    text: String(s.text ?? '').slice(0, 2000),
    ...(point ?? {}),
    // подпись точки, если админ её применит: «Музей Арктики · Санкт-Петербург»
    label: String(s.label ?? '').slice(0, 120),
    // ещё точки, если пост про несколько мест: [{ lat, lon, label }]
    ...(Array.isArray(s.extra) && s.extra.length
      ? {
          extra: s.extra
            .slice(0, MAX_POINTS - 1)
            .map((e) => ({ ...readPoint(e), label: String(e?.label ?? '').slice(0, 120) }))
            .filter((e) => e.lat != null),
        }
      : {}),
    // связанные места — сравнение, прообраз, другая работа того же архитектора:
    // не место поста, на карту поста не ставятся
    ...(Array.isArray(s.related) && s.related.length
      ? {
          related: s.related
            .slice(0, MAX_POINTS)
            .map((e) => ({ ...readPoint(e), label: String(e?.label ?? '').slice(0, 120) }))
            .filter((e) => e.lat != null),
        }
      : {}),
    // что искать на Яндекс-карте при ручной точке: «улица Пределов Мечты, Орехово-Зуево»
    ...(s.query ? { query: String(s.query).slice(0, 150) } : {}),
    // кто разбирал пост: «Claude Haiku 4.5», «Qwen 3.8 27B (Groq)»
    ...(s.model ? { model: String(s.model).slice(0, 80) } : {}),
    // модель считает пост рекламой: почему — админ решит, скрыть ли его
    ...(s.ad ? { ad: String(s.ad).slice(0, 300) } : {}),
    from: 'model',
    at: new Date().toISOString(),
  })
  await save(state)
  res.json({ ok: true })
})

// --- заявки на правила ------------------------------------------------

// «Придумать правило» в модерации: админ отмечает случай, который правила
// разбора (tools/classify.py) решают неверно, и что должно получиться.
// Сами правила пишутся потом, в сессии Claude Code по этому списку.
app.post('/api/rules', requireAdmin, async (req, res) => {
  const r = req.body ?? {}
  if (!KEY_RE.test(String(r.key ?? ''))) return res.status(400).json({ error: 'неверный ключ поста' })
  const point = readPoint(r)
  if (point === undefined) return res.status(400).json({ error: 'неверные координаты' })
  const state = await load()
  state.rules = state.rules.filter((x) => x.key !== r.key)
  state.rules.unshift({
    id: crypto.randomUUID(),
    key: r.key,
    // что должно получиться: подпись точки и сама точка, если есть
    expected: String(r.expected ?? '').slice(0, 300),
    ...(point ?? {}),
    note: String(r.note ?? '').slice(0, 1000),
    text: String(r.text ?? '').slice(0, 300),
    status: 'open',
    at: new Date().toISOString(),
  })
  await save(state)
  res.json(state.rules)
})

app.delete('/api/rules/:id', requireAdmin, async (req, res) => {
  const state = await load()
  state.rules = state.rules.filter((x) => x.id !== req.params.id)
  await save(state)
  res.json(state.rules)
})

// «Отправить на доработку»: предложение модели остаётся в списке с пометкой
// и пояснением модератора — следующий разбор (tools/llm_places.py select)
// отдаст этот пост модели первым вместе с пояснением. note: null — снять
app.post('/api/suggestions/:id/rework', requireAdmin, async (req, res) => {
  const state = await load()
  const s = state.suggestions.find((x) => x.id === req.params.id)
  if (!s) return res.status(404).json({ error: 'предложение не найдено' })
  if (req.body?.note === null) {
    delete s.status
    delete s.note
  } else {
    s.status = 'rework'
    s.note = String(req.body?.note ?? '').slice(0, 1000)
  }
  await save(state)
  res.json(state.suggestions)
})

// --- лимит Яндекс-карт -------------------------------------------------
//
// Вторая карта в «Указать точку вручную» — Яндекс (JavaScript API). У
// бесплатного ключа суточный лимит запросов, а остатка API не сообщает,
// поэтому считаем сами: загрузка карты и поиск на ней — по запросу. Счётчик
// общий для всех админов, сутки — по Москве (так Яндекс и обнуляет).
// Кончился — кнопка «Яндекс» в выборе точки становится неактивной.
const YANDEX_LIMIT = Number(process.env.YANDEX_DAILY_LIMIT ?? 100)
const yandexStore = jsonStore(path.join(DATA, 'yandex-usage.json'), { day: '', used: 0 })
const moscowDay = () => new Date(Date.now() + 3 * 3600_000).toISOString().slice(0, 10)

async function yandexUsage() {
  const u = await yandexStore.load()
  return u.day === moscowDay() ? u : { day: moscowDay(), used: 0 }
}

app.get('/api/yandex-usage', requireAdmin, async (_req, res) => {
  const u = await yandexUsage()
  res.json({ used: u.used, limit: YANDEX_LIMIT })
})

// { n: 1 } — ещё запрос; { exhausted: true } — Яндекс сам ответил отказом
app.post('/api/yandex-usage', requireAdmin, async (req, res) => {
  const u = await yandexUsage()
  u.used = req.body?.exhausted ? Math.max(u.used, YANDEX_LIMIT) : u.used + Math.max(0, Math.min(5, Number(req.body?.n) || 1))
  await yandexStore.save(u)
  res.json({ used: u.used, limit: YANDEX_LIMIT })
})

// Поиск места для «Указать точку вручную» — через Яндекс HTTP Геокодер.
// Ключ — на сервере (YANDEX_GEOCODER_KEY в server/.env, локально —
// tools/.yandex-geocoder-key), в страницу не попадает. Лимит суточный,
// счётчик общий с разбором (tools/geocode_streets.py) — один файл.
const GEOCODER_LIMIT = Number(process.env.YANDEX_GEOCODER_LIMIT ?? 1000)
const geocoderStore = jsonStore(path.join(DATA, 'yandex-geocoder-usage.json'), { day: '', used: 0 })
let geocoderKey = process.env.YANDEX_GEOCODER_KEY ?? null

async function readGeocoderKey() {
  if (geocoderKey === null) {
    geocoderKey = await fs
      .readFile(path.join(__dirname, '..', 'tools', '.yandex-geocoder-key'), 'utf8')
      .then((s) => s.trim())
      .catch(() => '')
  }
  return geocoderKey
}

app.get('/api/yandex-geocode', requireAdmin, async (req, res) => {
  const q = String(req.query.q ?? '').trim().slice(0, 200)
  if (!q) return res.status(400).json({ error: 'нужен запрос' })
  const key = await readGeocoderKey()
  if (!key) return res.status(503).json({ error: 'ключ Яндекс-геокодера не задан' })
  const u = await geocoderStore.load()
  if (u.day !== moscowDay()) Object.assign(u, { day: moscowDay(), used: 0 })
  if (u.used >= GEOCODER_LIMIT) {
    return res.status(429).json({ error: `лимит Яндекс-геокодера на сегодня исчерпан (${u.used} из ${GEOCODER_LIMIT})` })
  }
  u.used += 1
  await geocoderStore.save(u)
  const params = new URLSearchParams({ apikey: key, geocode: q, format: 'json', lang: 'ru_RU', results: '5' })
  try {
    const r = await fetch(`https://geocode-maps.yandex.ru/1.x/?${params}`)
    if (!r.ok) return res.status(502).json({ error: `Яндекс ответил ${r.status}` })
    const data = await r.json()
    const items = (data.response?.GeoObjectCollection?.featureMember ?? []).map(({ GeoObject: o }) => {
      const [lon, lat] = o.Point.pos.split(' ').map(Number)
      const meta = o.metaDataProperty.GeocoderMetaData
      return { text: meta.text, kind: meta.kind, precision: meta.precision, lat, lon }
    })
    res.json({ items, used: u.used, limit: GEOCODER_LIMIT })
  } catch (e) {
    res.status(502).json({ error: 'Яндекс-геокодер не ответил' })
  }
})

// Поиск места по названию для «Указать точку вручную» — строка над картой
// (09.10: «памятник Тысячелетию России» не искался). Встроенный поиск
// Яндекс-карты ищет по организациям — это отдельный платный API, наш ключ
// его не даёт («Сервер не смог обработать запрос»), а HTTP Геокодер знает
// только адреса («памятник Тысячелетию России» — «памятники природы» за
// тысячу километров). Названия памятников, храмов, усадеб есть в
// OpenStreetMap: Photon ищет нечётко и рядом с точкой поста (нашёл
// «Тысячелетие России» сразу), Nominatim — только по точному имени, без
// «памятник». Оба — не чаще раза в секунду, со своей подписью программы.
const NOMINATIM_UA = 'archimap/1.0 (admin point picker; https://archimap.194.226.142.99.sslip.io)'
let nominatimQueue = Promise.resolve()
let photonQueue = Promise.resolve()
const placeCache = new Map()

function photon(q, near) {
  const params = new URLSearchParams({ q, limit: '6' })
  if (near) {
    params.set('lat', near.lat)
    params.set('lon', near.lon)
  }
  const run = photonQueue.then(async () => {
    const r = await fetch(`https://photon.komoot.io/api/?${params}`, {
      headers: { 'User-Agent': NOMINATIM_UA },
      signal: AbortSignal.timeout(15000),
    })
    if (!r.ok) throw new Error(`Photon ответил ${r.status}`)
    return (await r.json()).features.map(({ properties: p, geometry: g }) => ({
      name: p.name || p.street || q,
      text: [p.name, p.street && [p.street, p.housenumber].filter(Boolean).join(' '), p.city || p.county, p.state]
        .filter(Boolean)
        .join(', '),
      kind: p.osm_value,
      lat: g.coordinates[1],
      lon: g.coordinates[0],
      src: 'osm',
    }))
  })
  photonQueue = run.catch(() => {}).then(() => new Promise((ok) => setTimeout(ok, 1100)))
  return run
}

function nominatim(q, near) {
  const params = new URLSearchParams({ q, format: 'jsonv2', limit: '6', 'accept-language': 'ru', addressdetails: '0' })
  if (near) {
    // рядом с постом — первыми, но и дальше ищем (bounded=0)
    const d = 0.7
    params.set('viewbox', [near.lon - d, near.lat + d, near.lon + d, near.lat - d].join(','))
  }
  const run = nominatimQueue.then(async () => {
    const r = await fetch(`https://nominatim.openstreetmap.org/search?${params}`, {
      headers: { 'User-Agent': NOMINATIM_UA },
      signal: AbortSignal.timeout(15000),
    })
    if (!r.ok) throw new Error(`OpenStreetMap ответил ${r.status}`)
    return (await r.json()).map((x) => ({
      text: x.display_name,
      name: x.name || x.display_name.split(',')[0],
      kind: x.type,
      lat: Number(x.lat),
      lon: Number(x.lon),
      src: 'osm',
    }))
  })
  // следующий запрос — не раньше чем через секунду после этого
  nominatimQueue = run.catch(() => {}).then(() => new Promise((ok) => setTimeout(ok, 1100)))
  return run
}

// Викидата — храмы, усадьбы, монастыри, которых нет в OpenStreetMap
// (10.10: дацан «Шедруб Линг» под Качканаром — в OSM только тропа с его
// именем). Ищет по названию: тип («обитель», «дацан») и область после
// запятой ей мешают — их отрезаем. Точка — свойство P625 (координаты)
const TYPE_WORDS =
  /^(?:обитель|монастырь|дацан|храм|церковь|собор|часовня|мечеть|костёл|костел|кирха|синагога|усадьба|дом|особняк|памятник|музей|дворец|замок|скит|погост)\s+/i
let wikidataQueue = Promise.resolve()

function wikidata(q) {
  const name = q.split(',')[0].trim().replace(TYPE_WORDS, '').replace(/[«»"]/g, '').trim()
  if (name.length < 3) return Promise.resolve([])
  const run = wikidataQueue.then(async () => {
    const opt = { headers: { 'User-Agent': NOMINATIM_UA }, signal: AbortSignal.timeout(15000) }
    const search = new URLSearchParams({ action: 'wbsearchentities', search: name, language: 'ru', uselang: 'ru', format: 'json', limit: '7', type: 'item' })
    const r = await fetch(`https://www.wikidata.org/w/api.php?${search}`, opt)
    if (!r.ok) throw new Error(`Викидата ответила ${r.status}`)
    const ids = (await r.json()).search?.map((x) => x.id) ?? []
    if (!ids.length) return []
    const get = new URLSearchParams({ action: 'wbgetentities', ids: ids.join('|'), props: 'labels|descriptions|claims', languages: 'ru|en', format: 'json' })
    const e = await fetch(`https://www.wikidata.org/w/api.php?${get}`, opt)
    if (!e.ok) throw new Error(`Викидата ответила ${e.status}`)
    const entities = (await e.json()).entities ?? {}
    return ids
      .map((id) => entities[id])
      .filter(Boolean)
      .map((x) => {
        const c = x.claims?.P625?.[0]?.mainsnak?.datavalue?.value
        if (!c) return null
        const label = x.labels?.ru?.value || x.labels?.en?.value || name
        const desc = x.descriptions?.ru?.value || x.descriptions?.en?.value || ''
        return { name: label, text: [label, desc, x.id].filter(Boolean).join(', '), kind: 'wikidata', lat: c.latitude, lon: c.longitude, src: 'wikidata' }
      })
      .filter(Boolean)
  })
  wikidataQueue = run.catch(() => {}).then(() => new Promise((ok) => setTimeout(ok, 500)))
  return run
}

async function yandexAddress(q) {
  const key = await readGeocoderKey()
  if (!key) return []
  const u = await geocoderStore.load()
  if (u.day !== moscowDay()) Object.assign(u, { day: moscowDay(), used: 0 })
  if (u.used >= GEOCODER_LIMIT) return []
  u.used += 1
  await geocoderStore.save(u)
  const params = new URLSearchParams({ apikey: key, geocode: q, format: 'json', lang: 'ru_RU', results: '5' })
  const r = await fetch(`https://geocode-maps.yandex.ru/1.x/?${params}`, { signal: AbortSignal.timeout(15000) })
  if (!r.ok) return []
  const data = await r.json()
  return (data.response?.GeoObjectCollection?.featureMember ?? [])
    .map(({ GeoObject: o }) => {
      const [lon, lat] = o.Point.pos.split(' ').map(Number)
      const meta = o.metaDataProperty.GeocoderMetaData
      return { text: meta.text, name: o.name, kind: meta.kind, lat, lon, src: 'yandex' }
    })
    // город, область, страна — не находка: Яндекс отдаёт их, не найдя дома
    .filter((x) => !['locality', 'province', 'country', 'area'].includes(x.kind))
}

const distKm = (a, b) => {
  const r = Math.PI / 180
  const x = (b.lon - a.lon) * r * Math.cos(((a.lat + b.lat) / 2) * r)
  const y = (b.lat - a.lat) * r
  return 6371 * Math.hypot(x, y)
}

app.get('/api/place-search', requireAdmin, async (req, res) => {
  const q = String(req.query.q ?? '').trim().slice(0, 200)
  if (!q) return res.status(400).json({ error: 'нужен запрос' })
  const lat = Number(req.query.lat)
  const lon = Number(req.query.lon)
  const near = Number.isFinite(lat) && Number.isFinite(lon) && req.query.lat !== undefined ? { lat, lon } : null
  const cacheKey = `${q.toLowerCase()}|${near ? `${lat.toFixed(2)},${lon.toFixed(2)}` : ''}`
  if (placeCache.has(cacheKey)) return res.json({ items: placeCache.get(cacheKey) })
  // Photon дважды: рядом с постом и без привязки — с привязкой он отдаёт
  // только местное («Памятник Рязанову» вместо «Тысячелетия России», если
  // пост в Нижнем, а точка ошибочно там)
  const found = await Promise.allSettled([
    photon(q, near),
    near ? photon(q, null) : Promise.resolve([]),
    nominatim(q, near),
    yandexAddress(q),
    wikidata(q),
  ])
  const [phNear, phAll, nom, ya, wd] = found.map((x) => x.value ?? [])
  const ph = [...phNear, ...phAll]
  // Яндекс — только адреса рядом с постом: по названиям он отдаёт что попало
  const yaNear = near ? ya.filter((x) => distKm(near, x) <= 50) : ya
  let items = []
  for (const x of [...ph, ...wd, ...nom, ...yaNear]) {
    // одно место из двух источников — оставляем первое
    if (items.some((y) => distKm(x, y) < 0.05)) continue
    items.push(near ? { ...x, km: Math.round(distKm(near, x) * 10) / 10 } : x)
  }
  // первыми — у кого в названии больше слов запроса (по началу слова:
  // «Тысячелетию» ~ «Тысячелетие»), при равенстве — рядом с постом (до
  // 50 км): иначе адрес Яндекса «улица Пределов Мечты» стоял за шестью
  // улицами OSM, а местный «Памятник Рязанову» — впереди «Тысячелетия России»
  const stems = q.toLowerCase().split(/[^а-яёa-z0-9]+/).filter((w) => w.length > 3).map((w) => w.slice(0, 5))
  // считаем по названию, а при равенстве выше короткое: в OSM бывает
  // «название» в целый абзац («…как археологический памятник России…»)
  const score = (x) => {
    const words = x.name.toLowerCase().split(/[^а-яёa-z0-9]+/).filter(Boolean)
    const hit = stems.filter((s) => words.some((w) => w.startsWith(s))).length
    return { hit, share: words.length ? hit / words.length : 0 }
  }
  const far = (x) => (near && x.km > 50 ? 1 : 0)
  // тропы и маршруты с местом в названии («Туристическая тропа Косья —
  // Шедруб Линг — скала Верблюд») — ниже настоящих мест
  const route = (x) => (/^(?:path|footway|track|hiking|route|cycleway|bridleway)$/.test(x.kind || '') ? 1 : 0)
  items = items
    .map((x, i) => ({ x, i, ...score(x) }))
    .sort((a, b) => route(a.x) - route(b.x) || b.hit - a.hit || b.share - a.share || far(a.x) - far(b.x) || a.i - b.i)
    .map(({ x }) => x)
    .slice(0, 8)
  if (!items.length && found.every((x) => x.status === 'rejected')) {
    return res.status(502).json({ error: 'поиск сейчас не отвечает — попробуйте ещё раз или поставьте точку на карте' })
  }
  if (placeCache.size > 300) placeCache.clear()
  placeCache.set(cacheKey, items)
  res.json({ items })
})

// «Похоже на рекламу» от модели — пост скрыт сразу, до решения админа.
// «Не реклама» ({ value: true }) возвращает пост на сайт; { value: false } —
// отмена этого решения (кнопка «Вернуть» на плашке)
app.post('/api/suggestions/:id/not-ad', requireAdmin, async (req, res) => {
  const state = await load()
  const s = state.suggestions.find((x) => x.id === req.params.id)
  if (!s) return res.status(404).json({ error: 'предложение не найдено' })
  if (req.body?.value === false) delete s.notAd
  else s.notAd = true
  await save(state)
  res.json(state.suggestions)
})

// Вернуть удалённое уточнение: так в панели модерации работает «Вернуть»
app.post('/api/suggestions/restore', requireAdmin, async (req, res) => {
  const s = req.body ?? {}
  if (typeof s.id !== 'string' || !s.id) return res.status(400).json({ error: 'нет уточнения' })
  const state = await load()
  if (!state.suggestions.some((x) => x.id === s.id)) {
    const point = readPoint(s)
    state.suggestions.unshift({
      id: s.id,
      key: s.key != null && KEY_RE.test(String(s.key)) ? s.key : null,
      src: String(s.src ?? '').slice(0, 32),
      post: Number(s.post) || null,
      text: String(s.text ?? '').slice(0, 2000),
      ...(point ?? {}),
      ...(s.from === 'model' ? { from: 'model' } : {}),
      at: String(s.at ?? new Date().toISOString()).slice(0, 40),
    })
    await save(state)
  }
  res.json(state.suggestions)
})

// Вернуть один убранный тег — для кнопки «Вернуть» после «Убрать»
app.post('/api/edits/:key/unhide', requireAdmin, async (req, res) => {
  const { key } = req.params
  const { kind, name } = req.body ?? {}
  if (!KEY_RE.test(key)) return res.status(400).json({ error: 'неверный ключ поста' })
  if (kind !== 'places' && kind !== 'streets') {
    return res.status(400).json({ error: 'kind: places или streets' })
  }
  const state = await load()
  const cur = state.edits[key]
  if (cur) {
    cur[kind] = cur[kind].filter((x) => x !== name)
    if (!cur.places.length && !cur.streets.length && !cur.location) delete state.edits[key]
  }
  await save(state)
  res.json(state.edits)
})

app.delete('/api/suggestions/:id', requireAdmin, async (req, res) => {
  const state = await load()
  state.suggestions = state.suggestions.filter((s) => s.id !== req.params.id)
  await save(state)
  res.json(state.suggestions)
})

// --- посты посетителей -------------------------------------------------

// Регистрации нет: посетитель указывает ник, текст, точку на карте и по
// желанию фотографии. Пост сначала попадает на модерацию и становится
// виден всем, только когда его одобрит админ.

// Имена загруженных файлов придумывает сервер, поэтому шаблон строгий:
// адрес /api/uploads/<имя> не должен вести никуда, кроме этой папки.
const UPLOAD_RE = /^[0-9a-f-]{36}\.jpg$/

// Фотографии приходят из браузера уже ужатыми в JPEG (см. web/src/images.js).
// Проверяем и заголовок data URL, и сигнатуру самого файла: полагаться
// на то, что прислал браузер, нельзя.
function decodePhoto(dataUrl) {
  const m = /^data:image\/jpeg;base64,([A-Za-z0-9+/=]+)$/.exec(String(dataUrl))
  if (!m) return null
  const buf = Buffer.from(m[1], 'base64')
  if (buf.length < 100 || buf.length > MAX_PHOTO_BYTES) return null
  if (buf[0] !== 0xff || buf[1] !== 0xd8 || buf[2] !== 0xff) return null
  return buf
}

async function removePhotos(names) {
  for (const name of names ?? []) {
    if (!UPLOAD_RE.test(name)) continue
    await fs.rm(path.join(UPLOADS, name), { force: true })
  }
}

// Что уходит наружу. Черновые поля хранилища здесь не нужны.
function publicPost(p) {
  return {
    id: p.id,
    nick: p.nick,
    text: p.text,
    place: p.place,
    lat: p.lat,
    lon: p.lon,
    photos: p.photos,
    status: p.status,
    at: p.at,
  }
}

app.get('/api/user-posts', async (_req, res) => {
  const { posts } = await userStore.load()
  res.json(posts.filter((p) => p.status === 'approved').map(publicPost))
})

// Очередь модерации видит только админ.
app.get('/api/user-posts/pending', requireAdmin, async (_req, res) => {
  const { posts } = await userStore.load()
  res.json(posts.filter((p) => p.status === 'pending').map(publicPost))
})

app.post('/api/user-posts', postLimit, async (req, res) => {
  const body = req.body ?? {}
  // скрытое поле формы: человек его не видит и не заполняет, а бот —
  // заполняет. Отвечаем как обычно, чтобы бот не понял, что его отсеяли.
  if (body.website) return res.json({ ok: true })

  const nick = String(body.nick ?? '').trim().replace(/\s+/g, ' ')
  const text = String(body.text ?? '').trim()
  const place = String(body.place ?? '').trim().replace(/\s+/g, ' ')
  const point = readPoint(body)
  if (nick.length < 2 || nick.length > 40) {
    return res.status(400).json({ error: 'ник — от 2 до 40 символов' })
  }
  if (!text || text.length > 4000) {
    return res.status(400).json({ error: 'текст поста — от 1 до 4000 символов' })
  }
  if (place.length > 120) return res.status(400).json({ error: 'слишком длинное название места' })
  if (!point) return res.status(400).json({ error: 'укажите точку на карте или координаты' })

  const raw = Array.isArray(body.photos) ? body.photos : []
  if (raw.length > MAX_PHOTOS) {
    return res.status(400).json({ error: `не больше ${MAX_PHOTOS} фотографий` })
  }
  const buffers = raw.map(decodePhoto)
  if (buffers.some((b) => !b)) {
    return res.status(400).json({ error: 'фотография не читается или слишком большая' })
  }

  const state = await userStore.load()
  if (state.posts.filter((p) => p.status === 'pending').length >= MAX_PENDING) {
    return res.status(429).json({ error: 'очередь модерации переполнена, попробуйте позже' })
  }

  const photos = []
  await fs.mkdir(UPLOADS, { recursive: true })
  for (const buf of buffers) {
    const name = `${crypto.randomUUID()}.jpg`
    await fs.writeFile(path.join(UPLOADS, name), buf)
    photos.push(name)
  }

  const post = {
    id: state.next,
    nick,
    text,
    place,
    ...point,
    photos,
    status: 'pending',
    at: new Date().toISOString(),
  }
  state.next += 1
  state.posts.push(post)
  await userStore.save(state)
  res.json({ ok: true, id: post.id })
})

app.post('/api/user-posts/:id/approve', requireAdmin, async (req, res) => {
  const state = await userStore.load()
  const post = state.posts.find((p) => p.id === Number(req.params.id))
  if (!post) return res.status(404).json({ error: 'пост не найден' })
  post.status = 'approved'
  post.approvedAt = new Date().toISOString()
  await userStore.save(state)
  res.json(publicPost(post))
})

// Отклонить на модерации и снять опубликованный — одно действие. Удаление
// мягкое: вместо «вы уверены?» админ получает кнопку «Вернуть», поэтому
// пост с фотографиями стирается окончательно только через неделю.
const PURGE_AFTER_MS = 7 * 24 * 3600_000

async function purgeDeleted(state) {
  const now = Date.now()
  const old = state.posts.filter(
    (p) => p.status === 'deleted' && now - Date.parse(p.deletedAt) > PURGE_AFTER_MS
  )
  if (!old.length) return
  state.posts = state.posts.filter((p) => !old.includes(p))
  await userStore.save(state)
  for (const p of old) await removePhotos(p.photos)
}

app.delete('/api/user-posts/:id', requireAdmin, async (req, res) => {
  const state = await userStore.load()
  const post = state.posts.find((p) => p.id === Number(req.params.id))
  if (!post) return res.status(404).json({ error: 'пост не найден' })
  if (post.status !== 'deleted') {
    post.prevStatus = post.status
    post.status = 'deleted'
    post.deletedAt = new Date().toISOString()
  }
  await userStore.save(state)
  await purgeDeleted(state)
  res.json({ ok: true })
})

app.post('/api/user-posts/:id/restore', requireAdmin, async (req, res) => {
  const state = await userStore.load()
  const post = state.posts.find((p) => p.id === Number(req.params.id))
  if (!post) return res.status(404).json({ error: 'пост уже удалён окончательно' })
  if (post.status === 'deleted') {
    post.status = post.prevStatus ?? 'pending'
    delete post.prevStatus
    delete post.deletedAt
    await userStore.save(state)
  }
  res.json(publicPost(post))
})

app.get('/api/uploads/:name', (req, res) => {
  const { name } = req.params
  if (!UPLOAD_RE.test(name)) return res.status(404).end()
  res.set('Cache-Control', 'public, max-age=2592000, immutable')
  res.set('X-Content-Type-Options', 'nosniff')
  res.type('image/jpeg')
  res.sendFile(path.join(UPLOADS, name), (err) => {
    if (err && !res.headersSent) res.status(404).end()
  })
})

app.listen(PORT, () => {
  const where = path.relative(process.cwd(), STORE)
  console.log(`api: http://localhost:${PORT} (правки в ${where})`)
  if (PASSWORD === 'dev') console.log('пароль админа: dev (значение по умолчанию)')
  if (!process.env.TRUST_PROXY) {
    console.log(
      'TRUST_PROXY не задан: если впереди стоит nginx, все посетители будут ' +
        'видны под одним адресом и попадут в общий счётчик ограничений'
    )
  }
})
