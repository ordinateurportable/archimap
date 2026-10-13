import { useCallback, useEffect, useMemo, useState } from 'react'
import { postKey, userPostToPost } from './data.js'

// Правки админа и предложения посетителей живут на сервере (см. server/),
// а в браузере остаётся только токен входа. Раньше всё лежало
// в localStorage и не выходило за пределы одного браузера.
const TOKEN_KEY = 'archimap.token.v1'

function readToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? ''
  } catch {
    return '' // приватный режим или заблокированное хранилище
  }
}

function writeToken(value) {
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* не смертельно: придётся войти заново */
  }
}

async function api(path, { method = 'GET', body, token } = {}) {
  const res = await fetch('/api' + path, {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    const error = new Error(data.error ?? `сервер ответил ${res.status}`)
    error.status = res.status
    throw error
  }
  return res.json()
}

// для компонентов вне useWorkspace: выбор точки спрашивает лимит Яндекс-карт
export { api, readToken }

export function useWorkspace() {
  const [token, setToken] = useState(readToken)
  const [edits, setEdits] = useState({})
  const [bugs, setBugs] = useState({})
  const [hidden, setHidden] = useState({})
  const [flagged, setFlagged] = useState({})
  // «Принять: места нет» в отчётах о кругах разбора (модерация → «Отчёты»)
  const [reviewed, setReviewed] = useState({})
  const [suggestions, setSuggestions] = useState([])
  const [rules, setRules] = useState([]) // заявки «придумать правило»
  const [offline, setOffline] = useState(false)
  const [userPosts, setUserPosts] = useState([]) // одобренные, видны всем
  const [pending, setPending] = useState([]) // очередь модерации, только админу

  const admin = Boolean(token)

  // Состояние забираем один раз при загрузке.
  useEffect(() => {
    api('/state')
      .then((s) => {
        setEdits(s.edits ?? {})
        setBugs(s.bugs ?? {})
        setHidden(s.hidden ?? {})
        setFlagged(s.flagged ?? {})
        setReviewed(s.reviewed ?? {})
        setSuggestions(s.suggestions ?? [])
        setRules(s.rules ?? [])
        setOffline(false)
      })
      .catch(() => setOffline(true)) // сервер не запущен — работаем только на чтение
    api('/user-posts')
      .then(setUserPosts)
      .catch(() => {})
  }, [])

  // Очередь модерации запрашиваем только у админа и заново после входа.
  useEffect(() => {
    if (!token) {
      setPending([])
      return
    }
    api('/user-posts/pending', { token })
      .then(setPending)
      .catch((e) => {
        if (e.status === 401 || e.status === 403) {
          writeToken('')
          setToken('')
        }
      })
  }, [token])

  const login = useCallback(async (password) => {
    const { token: fresh } = await api('/login', { method: 'POST', body: { password } })
    writeToken(fresh)
    setToken(fresh)
  }, [])

  const logout = useCallback(() => {
    api('/logout', { method: 'POST', token }).catch(() => {})
    writeToken('')
    setToken('')
  }, [token])

  // Если сервер разлогинил (например, его перезапустили), выходим тихо,
  // но говорим об этом вызывающему коду.
  const withAuth = useCallback(
    async (run) => {
      try {
        return await run()
      } catch (e) {
        if (e.status === 401 || e.status === 403) {
          writeToken('')
          setToken('')
        }
        throw e
      }
    },
    []
  )

  const hideTag = useCallback(
    (post, kind, name) =>
      withAuth(async () => {
        const next = await api(`/edits/${postKey(post)}/hide`, {
          method: 'POST',
          body: { kind, name },
          token,
        })
        setEdits(next)
      }),
    [token, withAuth]
  )

  const unhideTag = useCallback(
    (post, kind, name) =>
      withAuth(async () => {
        setEdits(
          await api(`/edits/${postKey(post)}/unhide`, {
            method: 'POST',
            body: { kind, name },
            token,
          })
        )
      }),
    [token, withAuth]
  )

  const resetPost = useCallback(
    (post) =>
      withAuth(async () => {
        const next = await api(`/edits/${postKey(post)}`, { method: 'DELETE', token })
        setEdits(next)
      }),
    [token, withAuth]
  )

  // Пометка «разбор сработал неправильно». Вместе с ключом отправляем
  // подпись и найденные места, чтобы файл с правками читался как список
  // проблем, без открытия интерфейса.
  const toggleBug = useCallback(
    (post, placeNames) =>
      withAuth(async () => {
        const key = postKey(post)
        const next = bugs[key]
          ? await api(`/bugs/${key}`, { method: 'DELETE', token })
          : await api(`/bugs/${key}`, {
              method: 'POST',
              token,
              body: {
                src: post.src,
                id: post.i,
                date: post.d,
                text: post.t.slice(0, 300),
                places: placeNames,
              },
            })
        setBugs(next)
      }),
    [bugs, token, withAuth]
  )

  // Уточнение посетителя: текст, точка на карте или и то и другое.
  // явное «скрыть/вернуть», а не переключатель: кнопке «Вернуть» на
  // плашке нужно действие, которое не зависит от текущего состояния
  const setPostHidden = useCallback(
    (post, on) =>
      withAuth(async () => {
        const key = postKey(post)
        const next = !on
          ? await api(`/hidden/${key}`, { method: 'DELETE', token })
          : await api(`/hidden/${key}`, {
              method: 'POST',
              token,
              body: { text: post.t.slice(0, 200) },
            })
        setHidden(next)
      }),
    [token, withAuth]
  )

  const toggleHidden = useCallback(
    (post) =>
      withAuth(async () => {
        const key = postKey(post)
        const next = hidden[key]
          ? await api(`/hidden/${key}`, { method: 'DELETE', token })
          : await api(`/hidden/${key}`, {
              method: 'POST',
              token,
              body: { text: post.t.slice(0, 200) },
            })
        setHidden(next)
      }),
    [hidden, token, withAuth]
  )

  const toggleFlagged = useCallback(
    (post) =>
      withAuth(async () => {
        const key = postKey(post)
        const next = flagged[key]
          ? await api(`/flagged/${key}`, { method: 'DELETE', token })
          : await api(`/flagged/${key}`, {
              method: 'POST',
              token,
              body: { text: post.t.slice(0, 200) },
            })
        setFlagged(next)
      }),
    [flagged, token, withAuth]
  )

  // решение по посту из отчёта: value — принять (true) или вернуть в список
  const setReviewedKey = useCallback(
    (key, value, text = '') =>
      withAuth(async () => {
        const next = value
          ? await api(`/reviewed/${key}`, { method: 'POST', token, body: { text: text.slice(0, 200) } })
          : await api(`/reviewed/${key}`, { method: 'DELETE', token })
        setReviewed(next)
      }),
    [token, withAuth]
  )

  const setLocation = useCallback(
    (key, point, name) =>
      withAuth(async () => {
        setEdits(
          await api(`/edits/${key}/location`, {
            method: 'POST',
            token,
            body: { ...point, name },
          })
        )
      }),
    [token, withAuth]
  )

  // несколько ручных точек сразу: [{ lat, lon, name }] — первая главная
  const setPoints = useCallback(
    (key, points) =>
      withAuth(async () => {
        setEdits(await api(`/edits/${key}/location`, { method: 'POST', token, body: { points } }))
      }),
    [token, withAuth]
  )

  const clearLocation = useCallback(
    (key) =>
      withAuth(async () => {
        setEdits(await api(`/edits/${key}/location`, { method: 'DELETE', token }))
      }),
    [token, withAuth]
  )

  const addSuggestion = useCallback(async (post, text, point) => {
    const body = { key: postKey(post), src: post.src, id: post.i, text, ...(point ?? {}) }
    await api('/suggestions', { method: 'POST', body })
    setSuggestions((prev) => [
      { id: 'local-' + Date.now(), ...body, at: new Date().toISOString() },
      ...prev,
    ])
  }, [])

  const requestRule = useCallback(
    (body) =>
      withAuth(async () => {
        setRules(await api('/rules', { method: 'POST', token, body }))
      }),
    [token, withAuth]
  )

  const deleteRule = useCallback(
    (id) =>
      withAuth(async () => {
        setRules(await api(`/rules/${id}`, { method: 'DELETE', token }))
      }),
    [token, withAuth]
  )

  const deleteSuggestion = useCallback(
    (id) =>
      withAuth(async () => {
        setSuggestions(await api(`/suggestions/${id}`, { method: 'DELETE', token }))
      }),
    [token, withAuth]
  )

  // «Отправить на доработку»: note — пояснение модератора, null — снять
  const reworkSuggestion = useCallback(
    (id, note) =>
      withAuth(async () => {
        setSuggestions(await api(`/suggestions/${id}/rework`, { method: 'POST', token, body: { note } }))
      }),
    [token, withAuth]
  )

  // «Не реклама»: value true — пост возвращается на сайт, false — отмена
  const setNotAd = useCallback(
    (id, value) =>
      withAuth(async () => {
        setSuggestions(await api(`/suggestions/${id}/not-ad`, { method: 'POST', token, body: { value } }))
      }),
    [token, withAuth]
  )

  const restoreSuggestion = useCallback(
    (s) =>
      withAuth(async () => {
        setSuggestions(await api('/suggestions/restore', { method: 'POST', token, body: s }))
      }),
    [token, withAuth]
  )

  // --- посты посетителей ---

  const submitPost = useCallback(async (form) => {
    await api('/user-posts', { method: 'POST', body: form })
  }, [])

  const approvePost = useCallback(
    (id) =>
      withAuth(async () => {
        const post = await api(`/user-posts/${id}/approve`, { method: 'POST', token })
        setPending((prev) => prev.filter((p) => p.id !== id))
        setUserPosts((prev) => [...prev.filter((p) => p.id !== id), post])
      }),
    [token, withAuth]
  )

  // и отклонение на модерации, и снятие опубликованного поста
  const removePost = useCallback(
    (id) =>
      withAuth(async () => {
        await api(`/user-posts/${id}`, { method: 'DELETE', token })
        setPending((prev) => prev.filter((p) => p.id !== id))
        setUserPosts((prev) => prev.filter((p) => p.id !== id))
      }),
    [token, withAuth]
  )

  const restorePost = useCallback(
    (id) =>
      withAuth(async () => {
        const post = await api(`/user-posts/${id}/restore`, { method: 'POST', token })
        if (post.status === 'approved') setUserPosts((prev) => [...prev, post])
        else setPending((prev) => [...prev, post])
      }),
    [token, withAuth]
  )

  // Опубликованные посты в том же виде, что посты каналов (см. data.js).
  const userPostRows = useMemo(() => userPosts.map(userPostToPost), [userPosts])

  const exportEdits = useCallback(() => {
    const blob = new Blob([JSON.stringify({ edits, bugs, suggestions }, null, 2)], {
      type: 'application/json',
    })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'archimap-edits.json'
    a.click()
    URL.revokeObjectURL(a.href)
  }, [edits, bugs, suggestions])

  const editedCount = useMemo(() => Object.keys(edits).length, [edits])
  const bugCount = useMemo(() => Object.keys(bugs).length, [bugs])
  // правки вместе с точками модели, которые ждут модерации: пост встаёт на
  // точку сразу, одобрение её только закрепляет
  const shownEdits = useMemo(() => withModelPoints(edits, suggestions), [edits, suggestions])

  return {
    admin,
    offline,
    login,
    logout,
    edits: shownEdits,
    hideTag,
    resetPost,
    bugs,
    toggleBug,
    bugCount,
    hidden,
    toggleHidden,
    setPostHidden,
    unhideTag,
    restoreSuggestion,
    restorePost,
    flagged,
    toggleFlagged,
    reviewed,
    setReviewedKey,
    setLocation,
    setPoints,
    clearLocation,
    suggestions,
    rules,
    reworkSuggestion,
    setNotAd,
    requestRule,
    deleteRule,
    addSuggestion,
    deleteSuggestion,
    exportEdits,
    editedCount,
    userPosts,
    userPostRows,
    pending,
    submitPost,
    approvePost,
    removePost,
  }
}

// Ручные точки поста: главная (location) и остальные (extra) — пост
// бывает про два здания сразу. withRelated — вместе со связанными местами
// (с пометкой related): так их правит и сохраняет редактор точек
export function manualPoints(edit, withRelated = false) {
  if (!edit?.location) return []
  const own = [edit.location, ...(edit.extra ?? [])]
  return withRelated ? [...own, ...(edit.related ?? []).map((r) => ({ ...r, related: true }))] : own
}

// Посты, которые модель сочла рекламой и админ ещё не сказал «не реклама»:
// они скрыты сразу, для всех, и ждут решения во вкладке модерации «Реклама»
export function modelAdKeys(suggestions) {
  return new Set(suggestions.filter((s) => s.from === 'model' && s.ad && !s.notAd).map((s) => s.key))
}

// Точки из предложений модели, ещё не одобренные: в правку поста полем
// model. Только точные — «просто Москва» ничего не добавляет к тому, что
// нашёл разбор; и не те, что отправлены на доработку — их модель переделает.
// Связанные места модели (modelRelated) — отдельно: на карту поста они не
// ставятся, их видно в окне поста.
function withModelPoints(edits, suggestions) {
  let out = edits
  for (const s of suggestions) {
    if (s.from !== 'model' || s.status === 'rework') continue
    const exact = s.lat != null && s.label?.includes(' · ')
    if (!exact && !s.related?.length) continue
    if (out === edits) out = { ...edits }
    const points = [
      { lat: s.lat, lon: s.lon, name: s.label },
      ...(s.extra ?? []).map((e) => ({ lat: e.lat, lon: e.lon, name: e.label ?? '' })),
    ]
    out[s.key] = {
      places: [],
      streets: [],
      ...out[s.key],
      ...(exact ? { model: points, modelBy: s.model } : {}),
      ...(s.related?.length ? { modelRelated: s.related.map((e) => ({ lat: e.lat, lon: e.lon, name: e.label ?? '' })) } : {}),
    }
  }
  return out
}

// Места и улицы поста за вычетом того, что убрал админ.
export function visibleTags(post, places, edits) {
  const hidden = edits[postKey(post)]
  const found = post.g.map((i) => places[i]).filter(Boolean)
  // у поста посетителя точка своя, а не из справочника
  if (post.loc) found.push(post.loc)
  if (!hidden) return { found, streets: post.s, related: [] }
  const kept = found.filter((p) => !hidden.places.includes(p.name))
  // ручные точки админа — первыми: на карте они заменяют все остальные.
  // Нет ручных — так же ведут себя точки модели, ждущие модерации
  const own = manualPoints(hidden)
  const manual = (own.length ? own : hidden.model ?? []).map((m, i) => ({
    name: m.name || 'Точка, указанная вручную',
    scope: 'manual',
    lat: m.lat,
    lon: m.lon,
    manual: true,
    // какая модель нашла — админ видит это в карточке точки на карте
    ...(own.length ? {} : { model: true, by: hidden.modelBy }),
    key: `${own.length ? 'manual' : 'model'}-${postKey(post)}-${i}`,
  }))
  // связанные места: свои у админа, если он ставил точки, иначе — модели
  const related = (own.length ? hidden.related ?? [] : hidden.modelRelated ?? []).map((m, i) => ({
    name: m.name || 'Связанное место',
    lat: m.lat,
    lon: m.lon,
    ...(own.length ? {} : { model: true }),
    key: `related-${postKey(post)}-${i}`,
  }))
  return {
    found: [...manual, ...kept],
    streets: post.s.filter((s) => !hidden.streets.includes(s)),
    related,
  }
}
