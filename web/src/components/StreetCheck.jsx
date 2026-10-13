import { useEffect, useState } from 'react'
import { api, readToken } from '../useEdits.js'
import PointMap from './PointMap.jsx'

// Улица или местность без точки («с. Великая Тополь», «ул. Ленина»): по
// нажатию админ видит, что о ней знает Яндекс-геокодер, — проверить, мог
// ли разбор поставить точку и где. Один запрос к нашему серверу
// (/api/yandex-geocode, суточный лимит общий с разбором), поэтому только
// по нажатию, а не сразу при открытии поста.
export default function StreetCheck({ query }) {
  const [state, setState] = useState({ status: 'loading' })

  useEffect(() => {
    let cancelled = false
    api(`/yandex-geocode?q=${encodeURIComponent(query)}`, { token: readToken() })
      .then(({ items, used, limit }) => {
        if (cancelled) return
        // Яндекс, не найдя улицы, отдаёт сам город или область — это не находка
        const hit = items.find((i) => !['province', 'country', 'area'].includes(i.kind))
        setState({ status: 'done', hit, used, limit })
      })
      .catch((e) => !cancelled && setState({ status: 'error', message: e.message }))
    return () => {
      cancelled = true
    }
  }, [query])

  if (state.status === 'loading') return <p className="street-check">Спрашиваю Яндекс: «{query}»…</p>
  if (state.status === 'error') return <p className="street-check street-check--error">Не удалось: {state.message}</p>
  const { hit, used, limit } = state
  return (
    <div className="street-check">
      <p>
        {hit
          ? `Яндекс: ${hit.text}${hit.kind === 'locality' ? ' — только населённый пункт' : ''}`
          : `Яндекс не нашёл «${query}» точнее, чем регион`}
        <span className="street-check__usage"> · запросов сегодня {used} из {limit}</span>
      </p>
      {hit && <PointMap points={[{ lat: hit.lat, lon: hit.lon }]} />}
    </div>
  )
}
