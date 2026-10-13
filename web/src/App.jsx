import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { useMeta, usePosts } from './usePosts.js'
import { manualPoints, modelAdKeys, readToken, useWorkspace, visibleTags } from './useEdits.js'
import {
  postKey,
  groupsFor,
  GROUPS,
  FILE_GROUPS,
  loadJSON,
  USER_SRC,
  USER_SOURCE,
  FOOD_FILTER,
  plural,
  queryStems,
  matchesQuery,
  formatCount,
} from './data.js'
import { buildURL, closedOnly, onlyTyping, readURL } from './urlState.js'
import Toolbar from './components/Toolbar.jsx'
import PostCard from './components/PostCard.jsx'
import PostModal from './components/PostModal.jsx'
import MapView from './components/MapView.jsx'
import LoginDialog from './components/LoginDialog.jsx'
import AddPostDialog from './components/AddPostDialog.jsx'
import ModerationDialog from './components/ModerationDialog.jsx'
import Toasts from './components/Toast.jsx'

const PAGE = 60 // сколько карточек добавляем за один шаг прокрутки
// Сами подгружаются только первые столько карточек, дальше — кнопкой: в
// ленте 17 тысяч постов, и при бесконечной подгрузке до подвала со входом
// для админа было не долистать (30.09, телефон)
const AUTO_LIMIT = 300
const RANDOM = 8 // размер блока случайных постов
// в этих вкладках нет координат или посты разбирают по одному — только список
const LIST_ONLY = ['none', 'hidden', 'flagged']

export default function App() {
  const { meta, error: metaError } = useMeta()
  // вкладка, место, канал, поиск, порядок, карта/список и открытый пост —
  // из адреса страницы (urlState.js): ссылкой можно поделиться, обновление
  // страницы ничего не сбрасывает
  const [fromURL] = useState(readURL)
  const [group, setGroup] = useState(fromURL.group)
  const { posts: channelPosts, error: postsError } = usePosts(group)

  const [query, setQuery] = useState(fromURL.query)
  const [placeFilter, setPlaceFilter] = useState(fromURL.place)
  const [srcFilter, setSrcFilter] = useState(fromURL.src)
  const [sort, setSort] = useState(fromURL.sort)
  const [limit, setLimit] = useState(PAGE)
  const [opened, setOpened] = useState(null)
  // пост из адреса (#arkh-12345) — откроется, когда загрузятся посты
  const [wantedPost, setWantedPost] = useState(fromURL.post)
  // карта интереснее списка, с неё и начинаем
  const [view, setView] = useState(LIST_ONLY.includes(fromURL.group) ? 'list' : fromURL.view)
  const [shuffle, setShuffle] = useState(0) // счётчик пересборки случайной выборки

  const [loginOpen, setLoginOpen] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  // модерация: false — закрыта, true — открыта, 'away' — спрятана, пока админ
  // смотрит место из тега поста (открыл пост из модерации, нажал «Магаданская
  // область»). Спрятанная не размонтируется: вкладка, прокрутка и открытые
  // формы ждут на месте, вернуться — «Вернуться в модерацию» у заголовка места
  // открыта ли она — тоже в адресе (?mod=1): «Назад» её закрывает, обновление
  // страницы не закрывает
  const [moderationOpen, setModerationOpen] = useState(() => Boolean(fromURL.mod && readToken()))
  // плашки «что произошло» — несколько сразу, у каждой своё «Вернуть»
  const [toasts, setToasts] = useState([])
  // Пока открыто окно (пост, модерация, вход, новый пост), страница под ним
  // не прокручивается: колесо мыши над затемнением листало ленту (11.10).
  // Спрятанная модерация ('away') ленту не держит — её как раз смотрят
  const modalOpen = Boolean(opened) || loginOpen || addOpen || moderationOpen === true
  useEffect(() => {
    document.documentElement.classList.toggle('scroll-locked', modalOpen)
    return () => document.documentElement.classList.remove('scroll-locked')
  }, [modalOpen])
  // посты всех вкладок по ключу — для превью в модерации (без лишнего клика)
  const [postIndex, setPostIndex] = useState(null)
  const closeToast = useCallback((id) => setToasts((list) => list.filter((t) => t.id !== id)), [])
  const toastId = useRef(0)
  const say = useCallback(
    (text, undo) => setToasts((list) => [...list, { text, undo, id: ++toastId.current }].slice(-3)),
    []
  )
  const {
    admin,
    offline,
    login,
    logout,
    edits,
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
  } = useWorkspace()

  // Действия с отменой: сразу делаем и показываем плашку «Вернуть» —
  // вместо вопросов «вы уверены?» и молча исчезающих карточек.
  const fail = (e) => say(e?.message ? `Не получилось: ${e.message}` : 'Не получилось')
  // в плашке — начало поста: скрыли два подряд — видно, какой вернуть
  const postName = (post) => {
    const t = (post.t || '').replace(/\s+/g, ' ').trim()
    return t ? `«${t.length > 40 ? `${t.slice(0, 40).trimEnd()}…` : t}»` : ''
  }
  const hidePost = (post, on) =>
    setPostHidden(post, on)
      .then(() =>
        say(on ? `Пост ${postName(post)} скрыт с сайта` : `Пост ${postName(post)} вернулся на сайт`, () =>
          setPostHidden(post, !on).catch(fail)
        )
      )
      .catch(fail)
  const removeTag = (post, kind, name) =>
    hideTag(post, kind, name)
      .then(() => say(`Тег «${name.split(' · ')[0]}» убран`, () => unhideTag(post, kind, name).catch(fail)))
      .catch(fail)
  const deleteUserPost = (id) =>
    removePost(id)
      .then(() => say('Пост удалён', () => restorePost(id).catch(fail)))
      .catch(fail)
  const dropSuggestion = (s) =>
    deleteSuggestion(s.id)
      .then(() => say('Уточнение удалено', () => restoreSuggestion(s).catch(fail)))
      .catch(fail)
  // прежние ручные точки поста — для «Вернуть»
  const pointsBefore = (key) =>
    manualPoints(edits[key], true).map(({ lat, lon, name, related }) => ({
      lat,
      lon,
      name: name ?? '',
      ...(related ? { related: true } : {}),
    }))
  const restorePoints = (key, prev) => (prev.length ? setPoints(key, prev) : clearLocation(key)).catch(fail)
  const dropLocation = (key) => {
    const prev = pointsBefore(key)
    return clearLocation(key)
      .then(() => say(prev.length > 1 ? 'Ручные точки убраны' : 'Ручная точка убрана', () => restorePoints(key, prev)))
      .catch(fail)
  }
  // точки [{ lat, lon, name }] из модерации или окна поста. Карточка сразу
  // уходит из «От модели», поэтому плашка — с «Вернуть» (прежние точки)
  const placePoints = (key, points, text) => {
    const prev = pointsBefore(key)
    return setPoints(key, points).then(() => say(text, () => restorePoints(key, prev)))
  }
  const showView = (next) => {
    // карту в этих вкладках не показываем — но и кнопку не прячем: говорим почему
    if (next === 'map' && LIST_ONLY.includes(group)) {
      say(
        group === 'none'
          ? 'У постов без географии нет координат — их можно посмотреть только списком'
          : 'Эти посты разбираются по одному — списком'
      )
      return
    }
    setView(next)
  }

  // Посты посетителей ставим в общий ряд с постами каналов. Точку им
  // выбирают на карте, а не по разбору текста, так что это вкладка
  // «Россия» — единственная, которую видит посетитель.
  // модель сочла пост рекламой — скрыт сразу, пока админ не решит иначе
  const adKeys = useMemo(() => modelAdKeys(suggestions), [suggestions])
  const posts = useMemo(() => {
    if (!channelPosts) return channelPosts
    // во вкладке «Скрытые» — только скрытые, в остальных — все, кроме них
    if (group === 'hidden' || group === 'flagged') {
      const pick = group === 'hidden' ? hidden : flagged
      return [...channelPosts, ...userPostRows]
        .filter((p) => pick[postKey(p)] || (group === 'hidden' && (p.ad || adKeys.has(postKey(p)))))
        .sort((a, b) => (a.d < b.d ? -1 : a.d > b.d ? 1 : 0))
    }
    // реклама (метка ad из tools/build_web_data.py) скрыта всегда
    const visible = channelPosts.filter((p) => !hidden[postKey(p)] && !p.ad && !adKeys.has(postKey(p)))
    if (group !== 'ru' || !userPostRows.length) return visible
    const users = userPostRows.filter((p) => !hidden[postKey(p)])
    // порядок по дате нужен сортировке «сначала новые/старые»
    return [...visible, ...users].sort((a, b) => (a.d < b.d ? -1 : a.d > b.d ? 1 : 0))
  }, [channelPosts, userPostRows, group, hidden, flagged, adKeys])

  // Переключатель режима: в админа пускаем только по паролю.
  const changeMode = (next) => {
    if (next === 'admin') {
      if (!admin) setLoginOpen(true)
    } else if (admin) {
      logout()
    }
  }

  // Пока пользователь печатает, React успевает отрисовать поле ввода
  // со старым списком — фильтрация 17 тысяч постов идёт «вторым приоритетом».
  const deferredQuery = useDeferredValue(query)

  const places = meta?.places ?? []
  // посетители — ещё один «канал» в фильтре по источнику
  const sources = useMemo(
    () => (meta && userPostRows.length ? { ...meta.sources, [USER_SRC]: USER_SOURCE } : meta?.sources),
    [meta, userPostRows.length]
  )
  const bySource = useMemo(
    () => (meta ? { ...meta.bySource, [USER_SRC]: userPostRows.length } : undefined),
    [meta, userPostRows.length]
  )

  useEffect(() => {
    if (!moderationOpen || postIndex) return
    Promise.all(FILE_GROUPS.map((g) => loadJSON(g).catch(() => [])))
      .then((parts) => setPostIndex(new Map(parts.flat().map((p) => [postKey(p), p]))))
  }, [moderationOpen, postIndex])

  // Уточнение из панели модерации ссылается на пост по ключу, а пост
  // может лежать в любой вкладке — ищем по всем, файлы уже в кэше.
  const openByKey = async (key) => {
    const own = userPostRows.find((p) => postKey(p) === key)
    if (own) return setOpened(own)
    for (const g of GROUPS) {
      if (g.virtual) continue
      const rows = await loadJSON(g.key).catch(() => [])
      const found = rows.find((p) => postKey(p) === key)
      if (found) return setOpened(found)
    }
    say('Такого поста больше нет: при пересборке у него мог смениться номер')
  }

  const loaded = Boolean(meta && posts)
  // Пост из адреса страницы (#arkh-12345) — открываем, когда посты на месте.
  // Сначала ищем в открытой вкладке, потом во всех файлах
  useEffect(() => {
    if (!wantedPost) return
    if (opened && postKey(opened) === wantedPost) return setWantedPost('')
    if (!loaded) return
    const here = posts.find((p) => postKey(p) === wantedPost)
    if (here) {
      setOpened(here)
      setWantedPost('')
      return
    }
    let gone = false
    openByKey(wantedPost).finally(() => !gone && setWantedPost(''))
    return () => {
      gone = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantedPost, opened, loaded])

  // Список мест для выпадающего фильтра — считаем по загруженной группе.
  const topPlaces = useMemo(() => {
    if (!posts || !places.length) return []
    const counts = new Map()
    for (const post of posts) {
      for (const pl of visibleTags(post, places, edits).found) {
        counts.set(pl.name, (counts.get(pl.name) ?? 0) + 1)
      }
    }
    return [...counts.entries()]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 50)
  }, [posts, places, edits])

  // постов вкладки с тегом «еда» — для пункта в фильтре мест
  const foodCount = useMemo(() => (posts ? posts.filter((p) => p.f).length : 0), [posts])

  const stems = useMemo(() => queryStems(deferredQuery), [deferredQuery])
  const filtered = useMemo(() => {
    if (!posts) return []
    return posts.filter((post) => {
      if (srcFilter && post.src !== srcFilter) return false
      if (!matchesQuery(post, stems)) return false
      if (placeFilter === FOOD_FILTER) {
        if (!post.f) return false
      } else if (placeFilter) {
        const { found } = visibleTags(post, places, edits)
        if (!found.some((p) => p.name === placeFilter)) return false
      }
      return true
    })
  }, [posts, stems, placeFilter, srcFilter, places, edits])

  // По запросу в выбранном месте или канале пусто — а без них? Тогда
  // предлагаем снять фильтр, а не голое «Ничего не нашлось» (справочник:
  // «пользу пустых результатов повышают, предлагая альтернативы»)
  const widerCount = useMemo(() => {
    if (!posts || filtered.length || !stems.length || !(placeFilter || srcFilter)) return 0
    return posts.filter((post) => matchesQuery(post, stems)).length
  }, [posts, filtered.length, stems, placeFilter, srcFilter])

  // Порядок списка. Исходные данные отсортированы по дате, поэтому
  // «сначала старые» — это сам массив, без лишнего копирования.
  const ordered = useMemo(() => {
    if (sort === 'old') return filtered
    const copy = [...filtered]
    if (sort === 'new') copy.reverse()
    else if (sort === 'liked') copy.sort((a, b) => b.r - a.r)
    else if (sort === 'photos') copy.sort((a, b) => b.np - a.np)
    return copy
  }, [filtered, sort])

  // Случайная выборка пересобирается по кнопке «Другие» и при смене
  // фильтров, но не при прокрутке — иначе блок прыгал бы.
  const randomPosts = useMemo(() => {
    if (filtered.length <= RANDOM) return filtered
    const picked = new Map()
    while (picked.size < RANDOM) {
      const i = Math.floor(Math.random() * filtered.length)
      picked.set(i, filtered[i])
    }
    return [...picked.values()]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtered, shuffle])

  // При смене группы или фильтра начинаем показ заново.
  useEffect(() => {
    setLimit(PAGE)
  }, [group, deferredQuery, placeFilter, srcFilter, sort])

  // Смена вкладки сбрасывает место: у другой вкладки свой список мест. Раньше
  // это делал эффект на смену вкладки — он же стирал место, пришедшее из
  // адреса страницы, и при «Назад» по истории
  const changeGroup = (next) => {
    setGroup(next)
    setPlaceFilter('')
    // у постов без географии координат нет — карту там показывать нечем,
    // а скрытые разбирают по одному, списком
    if (LIST_ONLY.includes(next)) setView('list')
  }

  const goHome = () => {
    changeGroup('ru')
    setSrcFilter('')
    setQuery('')
    setSort('new')
    setView('map')
    setOpened(null)
    setWantedPost('')
    setModerationOpen(false)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  // Выход из админа (или чужая ссылка на админскую вкладку) — на
  // единственную доступную посетителю вкладку.
  useEffect(() => {
    if (groupsFor(admin).every((g) => g.key !== group)) changeGroup('ru')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [admin, group])

  // --- адрес страницы ----------------------------------------------------
  // Состояние → адрес. Переход (вкладка, место, канал, карта/список, пост) —
  // новая запись в истории, по ней работает «Назад»; набор запроса и
  // порядок — правка текущей записи (urlState.js)
  const openedKey = opened ? postKey(opened) : wantedPost
  const modKey = admin && moderationOpen === true ? '1' : ''
  const lastURL = useRef(null)
  useEffect(() => {
    const state = { group, place: placeFilter, src: srcFilter, query, sort, view, mod: modKey, post: openedKey }
    const url = buildURL(state)
    const prev = lastURL.current
    if (url === prev?.url) {
      // пост из адреса нашёлся и открылся — адрес тот же, но теперь это
      // показанный пост: его закрытие — шаг назад по истории
      if (opened) prev.shown = true
      return
    }
    lastURL.current = { url, state, shown: Boolean(opened) }
    if (!prev) return window.history.replaceState(window.history.state, '', url)
    // Окно (пост, модерацию) открыли с сайта, а теперь закрыли — это шаг
    // назад по истории, а не новый переход: иначе «Назад» открывал бы только
    // что закрытое. Запись, сделанную нами при открытии, узнаём по её state.
    // Пост из адреса, который так и не нашёлся (shown — false), — не шаг назад
    const here = window.history.state
    const closedPost = closedOnly(prev.state, state, 'post')
    const closedMod = closedOnly(prev.state, state, 'mod')
    if (closedPost && prev.shown && here?.post === prev.state.post) return window.history.back()
    if (closedMod && here?.mod && !here?.post) return window.history.back()
    if (onlyTyping(prev.state, state) || closedPost || closedMod) window.history.replaceState(here, '', url)
    else window.history.pushState({ post: state.post || null, mod: state.mod || null }, '', url)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group, placeFilter, srcFilter, query, sort, view, modKey, openedKey, Boolean(opened)])

  // вышел из админа — модерация закрывается (иначе держала бы прокрутку)
  useEffect(() => {
    if (!admin) setModerationOpen(false)
  }, [admin])

  // Адрес → состояние: «Назад» и «Вперёд» в браузере
  useEffect(() => {
    const onPop = () => {
      const next = readURL()
      lastURL.current = { url: buildURL(next), state: next }
      setGroup(next.group)
      setPlaceFilter(next.place)
      setSrcFilter(next.src)
      setQuery(next.query)
      setSort(next.sort)
      setView(LIST_ONLY.includes(next.group) ? 'list' : next.view)
      setOpened((cur) => (cur && postKey(cur) === next.post ? cur : null))
      setWantedPost(next.post)
      // в адресе нет модерации — закрыта; спрятанная (смотрим место из тега)
      // остаётся спрятанной, «Вернуться в модерацию» на месте
      setModerationOpen((cur) => (next.mod && readToken() ? true : cur === 'away' ? 'away' : false))
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  // Бесконечная прокрутка: следим за «маячком» в конце списка.
  const sentinel = useRef(null)
  useEffect(() => {
    const node = sentinel.current
    if (!node) return
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) setLimit((n) => (n < AUTO_LIMIT ? n + PAGE : n))
    })
    io.observe(node)
    return () => io.disconnect()
  }, [ordered.length, view])

  const error = metaError || postsError
  // meta.json и файл группы грузятся параллельно, а карточкам нужен
  // справочник мест — поэтому ждём оба
  const ready = Boolean(meta && posts)

  // Пусто — говорим, что именно не нашлось и что с этим сделать: по запросу
  // в выбранном месте пусто, а во всех местах есть — кнопка туда
  const scopeName = [placeFilter === FOOD_FILTER ? 'Еда' : placeFilter, srcFilter && sources?.[srcFilter]?.title]
    .filter(Boolean)
    .join(' · ')
  const emptyHead = [
    stems.length ? `по запросу «${deferredQuery.trim()}»` : '',
    scopeName ? `с фильтром «${scopeName}»` : '',
    'ничего не нашлось.',
  ]
    .filter(Boolean)
    .join(' ')
  const emptyNote = (
    <p className="state">
      {emptyHead[0].toUpperCase() + emptyHead.slice(1)}
      {widerCount > 0 && (
        <>
          {' '}
          <button
            className="row-action"
            onClick={() => {
              setPlaceFilter('')
              setSrcFilter('')
            }}
          >
            Показать {formatCount(widerCount)} {plural(widerCount, 'пост', 'поста', 'постов')} во всех местах
          </button>
        </>
      )}
    </p>
  )
  const visible = ready ? ordered.slice(0, limit) : []

  const cardProps = {
    places,
    sources,
    edits,
    admin,
    onHideTag: removeTag,
    onToggleBug: toggleBug,
    onToggleHidden: (post) => hidePost(post, !hidden[postKey(post)]),
    onPlaceFilter: (name) => {
      setPlaceFilter(name)
      window.scrollTo({ top: 0, behavior: 'smooth' })
    },
    hidden,
    onToggleFlagged: toggleFlagged,
    flagged,
    onOpen: setOpened,
  }

  return (
    <div className={`app ${view === 'map' ? 'app--map' : ''}`}>
      <header className="navbar">
        <div className="navbar__inner">
          <div className="navbar__side">
            {/* на телефоне карта во весь экран и подвала со входом нет —
                вход здесь; показывается только там (styles.css, .app--map) */}
            {!admin && (
              <button className="navbar__button navbar__login" onClick={() => changeMode('admin')}>
                Вход
              </button>
            )}
            {admin && (
              <button className="navbar__button" onClick={() => setModerationOpen(true)}>
                Модерация
                {pending.length > 0 && <span className="badge">{pending.length}</span>}
              </button>
            )}
          </div>
          {/* заголовок — «домой»: вся Россия на карте, без фильтров и поиска
              (справочник Бирмана, «Дом»); ссылка — можно открыть в новой вкладке */}
          <h1 className="navbar__title">
            <a
              className="navbar__home"
              href="/"
              onClick={(e) => {
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
                e.preventDefault()
                goHome()
              }}
            >
              Карта постов
            </a>
          </h1>
          <div className="navbar__side navbar__side--right">
            <button
              className="navbar__button"
              onClick={() =>
                offline
                  ? say('Сейчас не получается связаться с сервером, поэтому отправить пост не выйдет. Попробуйте чуть позже')
                  : setAddOpen(true)
              }
              title="Добавить свой пост на карту"
            >
              <span className="navbar__plus">＋</span>
              <span className="navbar__label">Добавить пост</span>
            </button>
          </div>
        </div>
      </header>

      {meta && (
        <Toolbar
          group={group}
          counts={{
            ...meta.counts,
            // у открытой вкладки считаем по тому, что на самом деле видно:
            // без скрытых админом постов и с постами посетителей
            ...(posts && !['hidden', 'flagged'].includes(group) ? { [group]: posts.length } : {}),
            hidden: Object.keys(hidden).length + (meta.ads ?? 0),
            flagged: Object.keys(flagged).length,
          }}
          onGroup={changeGroup}
          query={query}
          onQuery={setQuery}
          placeFilter={placeFilter}
          onPlaceFilter={setPlaceFilter}
          onBackToModeration={moderationOpen === 'away' ? () => setModerationOpen(true) : null}
          topPlaces={topPlaces}
          foodCount={foodCount}
          shown={visible.length}
          total={ordered.length}
          view={view}
          onView={showView}
          sources={sources}
          bySource={bySource}
          srcFilter={srcFilter}
          onSrcFilter={setSrcFilter}
          sort={sort}
          onSort={setSort}
          admin={admin}
          offline={offline}
          groups={groupsFor(admin)}
          editedCount={editedCount}
          bugCount={bugCount}
          suggestionCount={suggestions.length}
          onExport={exportEdits}
        />
      )}

      {/* не голое «Ошибка: …», а что не получилось и что с этим сделать */}
      {error && (
        <p className="state state--error">
          Посты не загрузились ({String(error.message || error)}).{' '}
          <button className="row-action" onClick={() => window.location.reload()}>
            Загрузить заново
          </button>
        </p>
      )}
      {/* на карте о загрузке говорит сама карта — она видна сразу */}
      {!error && !ready && view === 'list' && <p className="state">Загружаю посты…</p>}

      {ready && view === 'map' && ordered.length === 0 && emptyNote}
      {!error && view === 'map' && (
        <MapView
          loading={!ready}
          posts={ordered}
          places={places}
          sources={sources}
          edits={edits}
          group={group}
          admin={admin}
          fitKey={`${group}|${placeFilter}`}
          onOpenPost={setOpened}
        />
      )}

      {view === 'list' && (
        <>
          {ready && randomPosts.length > 0 && (
            <>
              <p className="section-note section-note--row">
                <span>Случайные посты</span>
                <button className="row-action" onClick={() => setShuffle((n) => n + 1)}>
                  Показать другие
                </button>
              </p>
              <main className="grid">
                {randomPosts.map((post) => (
                  <PostCard
                key={'r-' + postKey(post)}
                post={post}
                bugged={Boolean(bugs[postKey(post)])}
                {...cardProps}
              />
                ))}
              </main>
            </>
          )}

          <p className="section-note">
            {ready ? `Все посты — ${ordered.length.toLocaleString('ru-RU')}` : ''}
          </p>
          <main className="grid">
            {visible.map((post) => (
              <PostCard
                key={postKey(post)}
                post={post}
                bugged={Boolean(bugs[postKey(post)])}
                {...cardProps}
              />
            ))}
          </main>
          {ready && ordered.length === 0 && emptyNote}
          {ready && limit >= AUTO_LIMIT && visible.length < ordered.length && (
            <p className="more">
              <button className="row-action" onClick={() => setLimit((n) => n + PAGE)}>
                Показать ещё {Math.min(PAGE, ordered.length - visible.length)}{' '}
                {plural(Math.min(PAGE, ordered.length - visible.length), 'пост', 'поста', 'постов')}
              </button>
            </p>
          )}
          <div ref={sentinel} className="sentinel" />
        </>
      )}

      <footer className="credits">
        Посты из телеграм-каналов, права на тексты и фотографии принадлежат их
        авторам. Координаты мест —{' '}
        <a href="https://www.geonames.org/" target="_blank" rel="noreferrer">
          GeoNames
        </a>{' '}
        (CC BY 4.0) и{' '}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
          OpenStreetMap
        </a>{' '}
        (ODbL). Подложка карты —{' '}
        <a href="https://openfreemap.org/" target="_blank" rel="noreferrer">
          OpenFreeMap
        </a>{' '}
        (если она недоступна — тайлы OpenStreetMap), точки улиц и зданий —{' '}
        <a href="https://www.geoapify.com/" target="_blank" rel="noreferrer">
          Geoapify
        </a>{' '}
        и{' '}
        <a href="https://photon.komoot.io/" target="_blank" rel="noreferrer">
          Photon
        </a>
        , неформальные места —{' '}
        <a href="https://www.wikidata.org/" target="_blank" rel="noreferrer">
          Wikidata
        </a>{' '}
        (CC0) и{' '}
        <a href="https://wikimapia.org/" target="_blank" rel="noreferrer">
          Викимапия
        </a>{' '}
        (CC BY-SA).
        <p className="credits__admin">
          {/* вход для админа — ссылкой, а не переключателем в панели:
              посетителю он не нужен */}
          <button className="credits__link" onClick={() => changeMode(admin ? 'guest' : 'admin')}>
            {admin ? 'Выйти из режима админа' : 'Вход для админа'}
          </button>
        </p>
      </footer>

      <Toasts toasts={toasts} onClose={closeToast} />

      {loginOpen && (
        <LoginDialog onLogin={login} onClose={() => setLoginOpen(false)} />
      )}

      {addOpen && <AddPostDialog onSubmit={submitPost} onClose={() => setAddOpen(false)} />}

      {admin && moderationOpen && (
        <ModerationDialog
          away={moderationOpen === 'away'}
          pending={pending}
          published={userPosts}
          suggestions={suggestions}
          onApprove={approvePost}
          onRemove={deleteUserPost}
          onDeleteSuggestion={dropSuggestion}
          onApplySuggestion={(s) =>
            placePoints(
              s.key,
              [
                { lat: s.lat, lon: s.lon, name: (s.label || s.text || '').slice(0, 120) },
                ...(s.extra ?? []).map((e) => ({ lat: e.lat, lon: e.lon, name: e.label ?? '' })),
                // связанные места — тоже, но с пометкой: на карту поста не встанут
                ...(s.related ?? []).map((e) => ({ lat: e.lat, lon: e.lon, name: e.label ?? '', related: true })),
              ],
              s.extra?.length || s.related?.length ? 'Точки применены' : 'Точка применена'
            )
          }
          appliedKeys={Object.fromEntries(
            Object.entries(edits)
              .filter(([, e]) => e.location)
              .map(([k, e]) => [k, e.location])
          )}
          hidden={hidden}
          onHidePost={(post) => hidePost(post, true)}
          onNotAd={(s) =>
            setNotAd(s.id, true)
              .then(() =>
                say('Не реклама — пост вернулся на сайт и ждёт точки в «От модели»', () =>
                  setNotAd(s.id, false).catch(fail)
                )
              )
              .catch(fail)
          }
          rules={rules}
          findPost={(key) => postIndex?.get(key) ?? userPostRows.find((p) => postKey(p) === key)}
          places={places}
          edits={edits}
          onRequestRule={(body) =>
            requestRule(body).then(() => say('Случай записан — правило придумаем по списку «Правила»'))
          }
          onRework={(s, note) =>
            reworkSuggestion(s.id, note)
              .then(() =>
                say(
                  note === null
                    ? 'Снято с доработки'
                    : 'Отправлено на доработку — модель разберёт пост заново с вашим пояснением',
                  note === null ? undefined : () => reworkSuggestion(s.id, null).catch(fail)
                )
              )
              // ошибку — и форме: не отправилось, значит черновик не стираем
              .catch((e) => {
                if (note === null) return fail(e)
                throw e
              })
          }
          // своя точка модератора — она же пример для следующих разборов модели
          onManualPoint={(s, points) =>
            placePoints(
              s.key,
              points,
              `${points.length > 1 ? 'Точки поставлены' : 'Точка поставлена'} — модель увидит это как пример`
            )
          }
          onDeleteRule={(r) =>
            deleteRule(r.id).then(() =>
              say('Заявка удалена', () => requestRule({ ...r, expected: r.expected }).catch(() => {}))
            )
          }
          reviewed={reviewed}
          // «Принять: места нет» в отчёте — без «вы уверены?», с «Вернуть»
          onReviewed={(key, value, text) =>
            setReviewedKey(key, value, text)
              .then(() =>
                value
                  ? say('Принято: у поста нет места — он во вкладке «Решено»', () =>
                      setReviewedKey(key, false).catch(fail)
                    )
                  : say('Пост вернулся в список отчёта')
              )
              .catch(fail)
          }
          onOpenPost={openByKey}
          onClose={() => setModerationOpen(false)}
        />
      )}

      {opened && (
        <PostModal
          key={postKey(opened)}
          post={opened}
          places={places}
          sources={sources}
          edits={edits}
          admin={admin}
          bugged={Boolean(bugs[postKey(opened)])}
          onHideTag={removeTag}
          onToggleBug={toggleBug}
          isHidden={Boolean(hidden[postKey(opened)])}
          onToggleHidden={(post) => hidePost(post, !hidden[postKey(post)])}
          onPlaceFilter={(name) => {
            setPlaceFilter(name)
            setOpened(null)
            window.scrollTo({ top: 0, behavior: 'smooth' })
            // иначе место выбралось бы под окном модерации — его не видно
            if (moderationOpen) setModerationOpen('away')
          }}
          isFlagged={Boolean(flagged[postKey(opened)])}
          onToggleFlagged={toggleFlagged}
          onSetPoints={(key, points) => placePoints(key, points, points.length > 1 ? 'Точки сохранены' : 'Точка сохранена')}
          onClearLocation={dropLocation}
          onResetPost={resetPost}
          onSuggest={addSuggestion}
          onRemovePost={deleteUserPost}
          onClose={() => setOpened(null)}
        />
      )}
    </div>
  )
}
