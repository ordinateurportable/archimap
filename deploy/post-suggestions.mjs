// Отправить предложения модели (data/llm/suggestions.json из
// tools/llm_places.py merge) в модерацию сервера на этой машине.
// Пароль берётся из server/.env, в командную строку он не попадает:
//   node --env-file=/srv/archimap/server/.env post-suggestions.mjs suggestions.json
import fs from 'node:fs'

const API = process.env.ARCHIMAP_API ?? `http://127.0.0.1:${process.env.PORT ?? 5174}/api`
const file = process.argv[2]
const items = JSON.parse(fs.readFileSync(file, 'utf8'))

const call = async (path, body, token) => {
  const r = await fetch(API + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`)
  return r.json()
}

const { token } = await call('/login', { password: process.env.ADMIN_PASSWORD })
// предложение, по которому модератор на стенде уже решил («на доработку»,
// «не реклама»), не перезаписываем: новое предложение стёрло бы решение
const state = await (await fetch(API + '/state')).json()
const decided = new Set(
  state.suggestions.filter((s) => s.from === 'model' && (s.status === 'rework' || s.notAd)).map((s) => s.key)
)
// одно отвергнутое предложение не должно останавливать остальные: 03.10
// выкладка сайта прошла, а из 693 предложений на стенд не попало ни одного
let ok = 0
const kept = []
const failed = []
for (const [i, s] of items.entries()) {
  if (decided.has(s.key)) {
    kept.push(s.key)
    continue
  }
  try {
    await call('/suggestions/model', s, token)
    ok++
  } catch (e) {
    failed.push(`${s.key}: ${e.message}`)
  }
  if ((i + 1) % 200 === 0) console.log(`   ${i + 1} из ${items.length}…`)
}
console.log(`отправлено в модерацию: ${ok} из ${items.length}`)
if (kept.length) console.log('на стенде по ним уже решено, не тронуты:', kept.join(', '))
if (failed.length) {
  console.log(`!! не приняты (${failed.length}):`)
  for (const f of failed.slice(0, 20)) console.log('   ' + f)
  process.exitCode = 1
}
