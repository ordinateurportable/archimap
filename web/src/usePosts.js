import { useEffect, useState } from 'react'
import { loadJSON, expandPlaces, FILE_GROUPS } from './data.js'

// Загружает справочник мест один раз на всё приложение.
export function useMeta() {
  const [meta, setMeta] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    loadJSON('meta')
      .then((m) => setMeta({ ...m, places: expandPlaces(m.places) }))
      .catch(setError)
  }, [])

  return { meta, error }
}

// Загружает посты выбранной группы. Файл группы качается один раз и
// остаётся в кэше, поэтому переключение вкладок потом мгновенное.
export function usePosts(group) {
  const [posts, setPosts] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setPosts(null)
    setError(null)
    // «Скрытые» собираются из всех групп: скрыть можно пост откуда угодно
    const load =
      group === 'hidden' || group === 'flagged'
        ? Promise.all(FILE_GROUPS.map(loadJSON)).then((parts) =>
            parts.flat().sort((a, b) => (a.d < b.d ? -1 : a.d > b.d ? 1 : 0))
          )
        : loadJSON(group)
    load
      .then((rows) => {
        if (!cancelled) setPosts(rows)
      })
      .catch((e) => {
        if (!cancelled) setError(e)
      })
    // если вкладку переключили до окончания загрузки, результат не нужен
    return () => {
      cancelled = true
    }
  }, [group])

  return { posts, error }
}
