// Перенести на стенд точки, которые админ поставил в локальной модерации
// («Применить точку», «Указать точку вручную»): локальный data/edits.json
// и стендовый — разные файлы. Точку, которая на стенде уже есть, не трогаем —
// правка, сделанная на стенде, важнее локальной; такие посты перечисляются.
//   node --env-file=/srv/archimap/server/.env post-locations.mjs locations.json
// locations.json — { "arkh-50209": [{ lat, lon, name }, …], … }: у поста
// бывает несколько точек, первая — главная; связанные места — с related: true
import fs from 'node:fs'

const API = process.env.ARCHIMAP_API ?? `http://127.0.0.1:${process.env.PORT ?? 5174}/api`
const items = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))

const call = async (path, body, token) => {
  const r = await fetch(API + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`)
  return r.json()
}

const { token } = await call('/login', { password: process.env.ADMIN_PASSWORD })
const { edits } = await call('/state')
let ok = 0
const kept = []
for (const [key, value] of Object.entries(items)) {
  if (edits[key]?.location) {
    kept.push(key)
    continue
  }
  // прежний формат файла — одна точка объектом
  // related: true — связанное место (сравнение, прообраз), не точка поста
  const points = (Array.isArray(value) ? value : [value]).map((p) => ({
    lat: p.lat,
    lon: p.lon,
    name: p.name ?? '',
    ...(p.related ? { related: true } : {}),
  }))
  await call(`/edits/${key}/location`, { points }, token)
  ok++
}
console.log(`поставлено точек у постов: ${ok} из ${Object.keys(items).length}`)
if (kept.length) console.log('на стенде уже есть своя точка, не тронуты:', kept.join(', '))
