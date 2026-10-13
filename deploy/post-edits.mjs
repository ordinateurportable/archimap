// Перенести на стенд решения, принятые в локальной модерации, пока стенд
// был недоступен: ручные точки (со связанными местами), скрытые и
// отмеченные посты, убранные теги, заявки на правила, «на доработку» и
// «не реклама» у предложений модели. Локальный data/edits.json и стендовый —
// разные файлы, поэтому всё, что на стенде уже решено, не трогаем: правка,
// сделанная на стенде, важнее локальной.
//   node --env-file=/srv/archimap/server/.env post-edits.mjs local-edits.json
// local-edits.json — копия локального data/edits.json. Предложения модели
// сначала отправляет post-suggestions.mjs: «на доработку» ставится на них.
import fs from 'node:fs'

const API = process.env.ARCHIMAP_API ?? `http://127.0.0.1:${process.env.PORT ?? 5174}/api`
const local = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))

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
const stand = await call('/state')
const done = { points: 0, tags: 0, hidden: 0, flagged: 0, reviewed: 0, rules: 0, rework: 0, notAd: 0 }
const kept = []

for (const [key, e] of Object.entries(local.edits ?? {})) {
  // ручные точки: главная, остальные и связанные места
  if (e.location) {
    if (stand.edits[key]?.location) kept.push(key)
    else {
      const points = [e.location, ...(e.extra ?? []), ...(e.related ?? []).map((r) => ({ ...r, related: true }))]
      await call(
        `/edits/${key}/location`,
        { points: points.map((p) => ({ lat: p.lat, lon: p.lon, name: p.name ?? '', ...(p.related ? { related: true } : {}) })) },
        token
      )
      done.points++
    }
  }
  // убранные теги
  for (const kind of ['places', 'streets']) {
    for (const name of e[kind] ?? []) {
      if (stand.edits[key]?.[kind]?.includes(name)) continue
      await call(`/edits/${key}/hide`, { kind, name }, token)
      done.tags++
    }
  }
}

for (const [key, v] of Object.entries(local.hidden ?? {})) {
  if (stand.hidden[key]) continue
  await call(`/hidden/${key}`, { text: v.text ?? '' }, token)
  done.hidden++
}
for (const [key, v] of Object.entries(local.flagged ?? {})) {
  if (stand.flagged[key]) continue
  await call(`/flagged/${key}`, { text: v.text ?? '' }, token)
  done.flagged++
}

// «Принять: места нет» в отчётах (модерация → «Отчёты»)
for (const [key, v] of Object.entries(local.reviewed ?? {})) {
  if (stand.reviewed?.[key]) continue
  await call(`/reviewed/${key}`, { text: v.text ?? '' }, token)
  done.reviewed++
}
const standRules = new Set((stand.rules ?? []).map((r) => r.key))
for (const r of local.rules ?? []) {
  if (standRules.has(r.key)) continue
  const { key, expected, note, text, lat, lon } = r
  await call('/rules', { key, expected, note, text, ...(lat != null ? { lat, lon } : {}) }, token)
  done.rules++
}

// «на доработку» и «не реклама» — у предложения модели того же поста на стенде
const fresh = await call('/state')
const modelOnStand = new Map(fresh.suggestions.filter((s) => s.from === 'model').map((s) => [s.key, s]))
for (const s of (local.suggestions ?? []).filter((x) => x.from === 'model')) {
  const there = modelOnStand.get(s.key)
  if (!there) continue
  if (s.status === 'rework' && there.status !== 'rework') {
    await call(`/suggestions/${there.id}/rework`, { note: s.note ?? '' }, token)
    done.rework++
  }
  if (s.notAd && !there.notAd) {
    await call(`/suggestions/${there.id}/not-ad`, {}, token)
    done.notAd++
  }
}

console.log(
  `перенесено: точек ${done.points}, убранных тегов ${done.tags}, скрытых ${done.hidden}, ` +
    `отмеченных ${done.flagged}, решённых в отчётах ${done.reviewed}, заявок на правила ${done.rules}, на доработку ${done.rework}, «не реклама» ${done.notAd}`
)
if (kept.length) console.log('на стенде уже есть своя точка, не тронуты:', kept.join(', '))
