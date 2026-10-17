import { useCallback, useEffect, useState } from 'react'
import { formatCoords, osmLink } from '../coords.js'
import { humanDate, humanDateTime, loadJSON, mapPlaces, photoURL, placeLabel, plural, postKey, USER_SRC } from '../data.js'
import { manualPoints, visibleTags } from '../useEdits.js'
import PointsEditor from './PointsEditor.jsx'
import NoteForm from './NoteForm.jsx'
import PhotoViewer from './PhotoViewer.jsx'
import PointMap from './PointMap.jsx'

// Сам пост прямо в карточке модерации: решать, куда ставить точку, без
// перехода в окно поста
function PostPreview({ post, places, edits, onPhoto }) {
  // «Сейчас на карте: Сент-Луис, Гатчина» — где именно, видно на миникарте
  // по кнопке, не уходя из модерации (16.10, просьба пользователя)
  const [mapOpen, setMapOpen] = useState(false)
  if (post === undefined) return <p className="review__meta">Загружаю пост…</p>
  if (!post) return null
  const { found } = visibleTags(post, places, edits)
  const onMap = mapPlaces(found).filter((pl) => pl.lat != null && pl.lon != null)
  return (
    <div className="preview">
      {post.p[0] && (
        <button
          className="preview__open"
          title={post.p.length > 1 ? `Открыть фото (${post.p.length})` : 'Открыть фото'}
          onClick={() =>
            onPhoto(post.p.map((name) => ({ src: photoURL(post, name, true), fallback: photoURL(post, name) })))
          }
        >
          <img className="preview__photo" src={photoURL(post, post.p[0])} alt="" loading="lazy" />
          {post.p.length > 1 && <span className="preview__count">{post.p.length}</span>}
        </button>
      )}
      <div className="preview__body">
        <p className="preview__meta">{humanDate(post.d)}</p>
        <p className="preview__text">{post.t || 'Без подписи'}</p>
        {/* Поиск похожих картинок в Яндексе по первому фото (16.10, ферма под
            Воронежем: район был только в подписи на снимке). Яндекс сам
            скачивает фото по адресу — работает на стенде; с localhost картинку
            ему не достать, поэтому так и пишем */}
        {post.p[0] && (
          <p className="preview__meta">
            <a
              className="preview__link"
              href={`https://yandex.ru/images/search?rpt=imageview&url=${encodeURIComponent(
                new URL(photoURL(post, post.p[0]), window.location.origin).href
              )}`}
              target="_blank"
              rel="noreferrer"
            >
              Найти по фото в Яндексе ↗
            </a>
            {/^(localhost|127\.|\[::1\])/.test(window.location.hostname) &&
              ' — работает только на стенде: с этого компьютера Яндекс фото не скачает'}
          </p>
        )}
        {found.length > 0 && (
          <p className="preview__meta">
            Сейчас на карте:{' '}
            {mapPlaces(found)
              .map((pl) => placeLabel(pl) + (pl.model ? ' (точка модели, ждёт решения)' : ''))
              .join(', ')}
            {onMap.length > 0 && (
              <>
                {' · '}
                <button className="coords-link" aria-pressed={mapOpen} onClick={() => setMapOpen((v) => !v)}>
                  {mapOpen ? 'Скрыть карту' : 'Показать на карте'}
                </button>
              </>
            )}
          </p>
        )}
      </div>
      {/* ручная точка — зелёная, точка модели — оранжевая, место из разбора —
          синее: видно, откуда что и насколько точки расходятся */}
      {mapOpen && onMap.length > 0 && (
        <div className="preview__map">
          <PointMap
            points={onMap.map((pl) => ({
              lat: pl.lat,
              lon: pl.lon,
              color: pl.model ? '#ff9500' : pl.manual ? '#4cd964' : '#007aff',
            }))}
          />
        </div>
      )}
    </div>
  )
}

// Координаты в карточке: по нажатию под строкой раскрывается миникарта с
// точкой, рядом — ссылка на OpenStreetMap в новой вкладке. others — другие
// точки того же поста (ручная рядом с точкой модели), видны на той же карте.
function Coords({ point, others = [] }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        className="coords-link"
        aria-expanded={open}
        title={open ? 'Скрыть карту' : 'Показать на карте'}
        onClick={() => setOpen(!open)}
      >
        {formatCoords(point)}
      </button>
      {' · '}
      <a href={osmLink(point)} target="_blank" rel="noreferrer">
        OSM ↗
      </a>
      {open && (
        <span className="coords-map">
          <PointMap points={[point, ...others]} />
        </span>
      )}
    </>
  )
}

// полный поиск Яндекс Карт в новой вкладке — без API и без лимита
const yandexSearchLink = (query) => `https://yandex.ru/maps/?text=${encodeURIComponent(query)}`

// Точка поста совпадает с предложенной — значит, её применили из
// предложения; иначе админ поставил свою
const sameSpot = (a, b) => a != null && a.lat === b.lat && a.lon === b.lon

// Панель админа: посты посетителей на модерации, уже опубликованные посты
// и уточнения к постам каналов.
// Отчёт о круге разбора (data/reports.json, tools/make_report.py): сколько
// постов, где точки, и посты, где модель не назвала места или искатель не
// нашёл объект, с причиной. Каждый пост — карточкой с теми же действиями,
// что во вкладке «От модели» (renderPost, 10.10). Разобранное уходит из
// списков само, по живым правкам: ручная точка — во «Точка поставлена
// вручную», «Принять» или «Скрыть пост» — в «Решено»
function Report({ report, edits, reviewed, hidden, renderPost }) {
  const hasManual = (item) => manualPoints(edits[item.key]).length > 0
  const isSettled = (item) => Boolean(reviewed[item.key] || hidden[item.key])
  const open = (item) => !hasManual(item) && !isSettled(item)
  const noPlace = (report.noPlace ?? []).filter(open)
  const missed = (report.missed ?? []).filter(open)
  const once = (pred) => {
    const seen = new Set()
    return [...(report.noPlace ?? []).map((x) => [x, 'noPlace']), ...(report.missed ?? []).map((x) => [x, 'missed'])]
      .filter(([x]) => pred(x) && !seen.has(x.key) && seen.add(x.key))
  }
  const manual = once(hasManual)
  const settled = once((x) => !hasManual(x) && isSettled(x))
  const lists = [
    // «Без точки», а не «Модель не назвала места»: сюда попадают и посты, где
    // модель место назвала, а геокодер его не нашёл (Успенский собор в
    // Свияжске, 16.10) — что именно, видно в карточке
    ['noPlace', 'Без точки', noPlace.map((x) => [x, 'noPlace'])],
    ['missed', 'Искатель не нашёл объект', missed.map((x) => [x, 'missed'])],
    ['manual', 'Точка поставлена вручную', manual],
    ['settled', 'Решено', settled],
  ].filter(([, , items]) => items.length > 0)
  // плашки с числами тоже открывают свои посты (09.10, просьба пользователя):
  // tab — ключ списка или «stat:N»
  const statLists = (report.stats ?? []).map(([name, , items], i) => [
    `stat:${i}`,
    name,
    (items ?? []).map((x) => [x, 'stat']),
  ])
  const [tab, setTab] = useState(null)
  const current = [...lists, ...statLists].find(([key]) => key === tab) ?? lists[0]
  return (
    <article className="review report">
      <header className="review__head">
        <strong>{report.title}</strong>
        <span>{humanDate(report.date)}</span>
      </header>
      {report.models?.length > 0 && <p className="review__meta">{report.models.join(' · ')}</p>}
      <div className="report__stats">
        {(report.stats ?? []).map(([name, value, items], i) => (
          <div key={name}>
            {/* старые отчёты без списков — просто число */}
            {items?.length ? (
              <button
                className="report__stat"
                aria-pressed={current?.[0] === `stat:${i}`}
                onClick={() => setTab(current?.[0] === `stat:${i}` ? null : `stat:${i}`)}
              >
                <span className="report__stat-name">{name}</span>
                <span className="report__stat-value">{value}</span>
              </button>
            ) : (
              <>
                <span className="report__stat-name">{name}</span>
                <span className="report__stat-value">{value}</span>
              </>
            )}
          </div>
        ))}
      </div>
      {report.notes?.length > 0 && (
        <ul className="report__notes">
          {report.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
      {current && (
        <>
          <div className="tabbar report__tabs" role="tablist">
            {lists.map(([key, title, items]) => (
              <button
                key={key}
                role="tab"
                aria-selected={key === current[0]}
                className={key === current[0] ? 'tabbar__on' : ''}
                onClick={() => setTab(key)}
              >
                <span>{title}</span>
                <span className="tab__count">{items.length}</span>
              </button>
            ))}
          </div>
          <h4 className="report__head">
            {current[1]} — {current[2].length}
          </h4>
          <div className="report__cards">{current[2].map(([item, kind]) => renderPost(item, kind))}</div>
        </>
      )}
    </article>
  )
}

// пост, с которым пока непонятно, что делать («Кинотеатр «Орион»… «Ашхабад»»
// — перечень без города): отметка та же, что в карточке на сайте, — вкладка
// «Отмеченные»; из модерации пост не уходит
function FlagButton({ post, flagged, onToggle }) {
  const on = Boolean(flagged[postKey(post)])
  return (
    <button className={`row-action ${on ? 'row-action--on' : ''}`} aria-pressed={on} onClick={() => onToggle(post)}>
      {on ? '★ Отмечен' : 'Отметить'}
    </button>
  )
}

export default function ModerationDialog({
  pending,
  published,
  suggestions,
  onApprove,
  onRemove,
  onDeleteSuggestion,
  onApplySuggestion,
  appliedKeys = {},
  hidden = {},
  onHidePost,
  onNotAd,
  rules = [],
  findPost = () => undefined,
  places = [],
  edits = {},
  onRequestRule,
  onRework,
  onManualPoint,
  onDeleteRule,
  onOpenPost,
  onClose,
  away,
  reviewed = {},
  onReviewed,
  flagged = {},
  onToggleFlagged,
}) {
  const [tab, setTab] = useState('pending')
  // отчёты о кругах разбора — статичный файл сборки, без сервера
  const [reports, setReports] = useState([])
  useEffect(() => {
    loadJSON('reports').then(setReports).catch(() => setReports([]))
  }, [])
  // открытое во весь экран фото: { photos, start }
  const [viewer, setViewer] = useState(null)
  const showPhotos = (photos, start = 0) => setViewer({ photos, start })
  const closeViewer = useCallback(() => setViewer(null), [])
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState('')
  // открытая форма «придумать правило»: id уточнения и пояснение админа
  const [ruleFor, setRuleFor] = useState(null)
  const ruled = new Set(rules.map((r) => r.key))
  // раскрытая в карточке форма: 'point' — своя точка, 'rework' — на доработку
  const [formFor, setFormFor] = useState(null)
  // с чего начать правку: уже поставленные вручную точки — чтобы поправить
  // их, а не начать заново; иначе — точки модели
  const startPoints = (s) => {
    const own = manualPoints(edits[s.key], true)
    if (own.length) return own
    if (s.lat == null) return []
    return [
      { lat: s.lat, lon: s.lon, name: s.label ?? '' },
      ...(s.extra ?? []).map((e) => ({ lat: e.lat, lon: e.lon, name: e.label ?? '' })),
      ...(s.related ?? []).map((e) => ({ lat: e.lat, lon: e.lon, name: e.label ?? '', related: true })),
    ]
  }
  const openForm = (id, kind, s) => {
    const same = formFor?.id === id && formFor?.kind === kind
    setFormFor(same ? null : { id, kind })
  }
  const formOpen = (id, kind) => formFor?.id === id && formFor?.kind === kind

  useEffect(() => {
    if (away) return
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, away])

  const run = async (id, action) => {
    setBusy(id)
    setError('')
    try {
      await action()
    } catch (e) {
      setError(e.message || 'не получилось')
    } finally {
      setBusy(null)
    }
  }

  // предложения модели (tools/llm_places.py) — отдельной вкладкой: их
  // много, и разбирают их иначе, чем уточнения посетителей
  // пост, которому точку уже поставили (из предложения или вручную) или
  // который скрыли, из списка уходит: решение принято, остаются неразобранные
  const modelAll = suggestions.filter((s) => s.from === 'model')
  // «похоже на рекламу» — отдельной вкладкой: пост уже скрыт с сайта и ждёт
  // одного решения — скрыть навсегда или вернуть; точку для него не ищем
  const ads = modelAll.filter((s) => s.ad && !s.notAd && !hidden[s.key])
  const fromModel = modelAll.filter((s) => !appliedKeys[s.key] && !hidden[s.key] && !(s.ad && !s.notAd))
  const modelDone = modelAll.length - fromModel.length
  const fromPeople = suggestions.filter((s) => s.from !== 'model')
  const tabs = [
    // вкладка называет то, что за ней, — существительным (bureau.ru/soviet/20170516)
    ['pending', 'Новые', pending.length],
    ['published', 'Опубликованные', published.length],
    ['suggestions', 'Уточнения', fromPeople.length],
    ['model', 'От модели', fromModel.length],
    ['ads', 'Реклама', ads.length],
    ['rules', 'Правила', rules.length],
    ['reports', 'Отчёты', reports.length],
  ]
  const shown = tab === 'model' ? fromModel : fromPeople

  const posts = tab === 'pending' ? pending : [...published].reverse()

  // Пост из отчёта о круге разбора — той же карточкой, что в «От модели»,
  // если у поста есть предложение модели; иначе (модель не назвала места —
  // такие в модерацию не шлются) — пост, причина и те же действия, что
  // имеют смысл без предложения. kind — из какого списка отчёта пост
  const latestModel = new Map(modelAll.map((x) => [x.key, x]))
  const reportPost = (item, kind) => {
    const isDone = Boolean(reviewed[item.key])
    const why = item.reason || item.note || ''
    const named = kind === 'noPlace' && item.object
    const note = (why || item.object) && (
      <p className="review__report">
        {kind === 'missed'
          ? 'Искатель не нашёл'
          : named
            ? 'Модель назвала'
            : kind === 'noPlace'
              ? 'Модель не назвала места'
              : 'В отчёте'}
        {item.object ? ` «${item.object}»` : ''}
        {named ? ' — точку не нашли' : ''}
        {why ? `: ${why}` : ''}
      </p>
    )
    // Подпись — по тому, что сейчас на карте: модель назвала Успенский собор,
    // разбор его и поставил, а кнопка говорила «места нет» (16.10, жалоба).
    // Точное место на карте — «точка верна»; объект назван, но не найден —
    // «точнее не найти»; «места нет» — только когда его и правда нет
    const shown = findPost(item.key)
    const exactOnMap =
      shown && mapPlaces(visibleTags(shown, places, edits).found).some((p) => p.parent || p.manual || p.model)
    const acceptLabel = exactOnMap
      ? 'Принять: точка верна'
      : kind === 'missed' || item.object
        ? 'Принять: точнее не найти'
        : 'Принять: места нет'
    const accept = (
      <button
        key="accept"
        className={isDone ? 'row-action' : 'row-action row-action--primary'}
        onClick={() => onReviewed?.(item.key, !isDone, why)}
        title="Согласиться с тем, что сейчас на карте, — пост уйдёт во вкладку «Решено»"
      >
        {isDone ? 'Вернуть в список' : acceptLabel}
      </button>
    )
    const s = latestModel.get(item.key)
    if (s) return <div key={item.key}>{suggestionCard(s, { note, actions: kind === 'stat' ? null : accept })}</div>
    const id = `report:${item.key}`
    const post = findPost(item.key)
    return (
      <article key={item.key} className="review">
        <header className="review__head">
          <strong>{item.key}</strong>
        </header>
        <PostPreview post={post} places={places} edits={edits} onPhoto={showPhotos} />
        {note}
        {appliedKeys[item.key] && (
          <p className="review__manual">
            ✓ Точка указана вручную{appliedKeys[item.key].name ? `: ${appliedKeys[item.key].name}` : ''} ·{' '}
            <Coords point={{ ...appliedKeys[item.key], color: '#4cd964' }} />
          </p>
        )}
        <div className="review__actions">
          {kind !== 'stat' && accept}
          <button className="row-action" onClick={() => onOpenPost(item.key)}>
            Открыть пост
          </button>
          <button
            className="row-action row-action--toggle"
            aria-pressed={formOpen(id, 'point')}
            onClick={() => openForm(id, 'point')}
          >
            {appliedKeys[item.key] ? 'Изменить ручную точку' : 'Указать точку вручную'}
          </button>
          {ruled.has(item.key) ? (
            <span className="review__applied">✓ Ждёт правила</span>
          ) : (
            <button
              className="row-action row-action--toggle"
              aria-pressed={ruleFor === id}
              onClick={() => setRuleFor(ruleFor === id ? null : id)}
              title="Записать случай, чтобы правила разбора научились решать его сами"
            >
              Придумать правило
            </button>
          )}
          {post && (
            <button className="row-action" disabled={busy === id} onClick={() => run(id, () => onHidePost(post))}>
              Скрыть пост
            </button>
          )}
          {post && onToggleFlagged && <FlagButton post={post} flagged={flagged} onToggle={onToggleFlagged} />}
        </div>
        {formOpen(id, 'point') && (
          <div className="suggest">
            <PointsEditor
              draftKey={`points:${item.key}`}
              initial={manualPoints(edits[item.key], true)}
              placeholder="Подпись точки: «Мосфильмовская улица», «гостиница «Украина»»"
              onSave={async (points) => {
                await onManualPoint({ key: item.key }, points)
                setFormFor(null)
              }}
            />
          </div>
        )}
        {ruleFor === id && (
          <NoteForm
            draftKey={`rule:${item.key}`}
            placeholder="Что должно понять правило? Например: «общежитие фабрики» — не сама фабрика"
            submitLabel="Записать случай"
            onSubmit={async (text) => {
              await onRequestRule({ key: item.key, expected: '', note: text, text: why })
              setRuleFor(null)
            }}
          />
        )}
      </article>
    )
  }

  // Карточка предложения: и во вкладке «От модели», и в отчётах о кругах
  // разбора (10.10: «у постов в отчётах — функционал как в „От модели“»).
  // extra — что добавляет отчёт: причину и кнопку «Принять»
  const suggestionCard = (s, extra) => (
              <article key={s.id} className="review">
                <header className="review__head">
                  <strong>{s.key ?? 'без поста'}</strong>
                  <span>
                    {/* кто разбирал пост — чтобы сравнивать модели по качеству */}
                    {s.model && <span className="review__model">{s.model}</span>}
                    {s.at && <span className="review__date">{humanDateTime(s.at)}</span>}
                  </span>
                </header>
                {s.key && <PostPreview post={findPost(s.key)} places={places} edits={edits} onPhoto={showPhotos} />}
                {s.text && <p className="review__text review__text--answer">{s.text.replace(/\.\./g, '.')}</p>}
                {s.ad && <p className="review__ad">Похоже на рекламу: {s.ad}</p>}
                {s.status === 'rework' && (
                  <p className="review__rework">
                    На доработке{s.note ? `: ${s.note}` : ''}.{' '}
                    <button className="row-action" onClick={() => onRework(s, null)}>
                      Снять
                    </button>
                  </p>
                )}
                {/* Модель назвала объект, а геокодер его не нашёл — точка стоит
                    на центре города. Так прямо и пишем: иначе выглядело, будто
                    модель предложила город (30.09). Поиск Яндекс Карт знает
                    организации и исторические названия домов, которых нет ни
                    в OpenStreetMap, ни в Яндекс-геокодере по адресам */}
                {s.from === 'model' && s.query && s.lat != null && !s.label?.includes(' · ') &&
                  s.query.trim().toLowerCase() !== (s.label || '').trim().toLowerCase() && (
                    <p className="review__miss">
                      Геокодер не нашёл «{s.query}» — точка пока на городе
                      {s.label ? `: ${s.label}` : ''}.{' '}
                      <a href={yandexSearchLink(s.query)} target="_blank" rel="noreferrer">
                        Найти на Яндекс Картах ↗
                      </a>
                      : нажмите на дом, скопируйте координаты и вставьте их в «Указать точку вручную».
                    </p>
                  )}
                {s.lat != null && s.lon != null && (
                  <p className="review__meta">
                    {s.extra?.length ? `Точка 1${s.label ? ` — ${placeLabel({ name: s.label })}` : ''}` : 'Точка'}:{' '}
                    <Coords
                      point={s}
                      others={[
                        ...(s.extra ?? []),
                        ...(appliedKeys[s.key] && !sameSpot(appliedKeys[s.key], s)
                          ? [{ ...appliedKeys[s.key], color: '#4cd964' }]
                          : []),
                      ]}
                    />
                  </p>
                )}
                {/* пост про несколько мест: модель предлагает и остальные */}
                {(s.extra ?? []).map((e, i) => (
                  <p key={i} className="review__meta">
                    Точка {i + 2}
                    {e.label ? ` — ${placeLabel({ name: e.label })}` : ''}: <Coords point={e} others={[s]} />
                  </p>
                ))}
                {/* связанные места: с чем пост сравнивает своё место — на
                    карту поста не встанут */}
                {(s.related ?? []).map((e, i) => (
                  <p key={`r${i}`} className="review__meta">
                    Связанное место{e.label ? ` — ${placeLabel({ name: e.label })}` : ''}:{' '}
                    <Coords point={{ ...e, color: '#8e8e93' }} others={s.lat != null ? [s] : []} />
                  </p>
                ))}
                {s.key && appliedKeys[s.key] && !sameSpot(appliedKeys[s.key], s) && (
                  <p className="review__manual">
                    ✓ Точка указана вручную
                    {appliedKeys[s.key].name ? `: ${appliedKeys[s.key].name}` : ''} ·{' '}
                    <Coords
                      point={{ ...appliedKeys[s.key], color: '#4cd964' }}
                      others={s.lat != null ? [s] : []}
                    />
                  </p>
                )}
                {extra?.note}
                <div className="review__actions">
                  {extra?.actions}
                  {s.key && (
                    <button className="row-action" onClick={() => onOpenPost(s.key)}>
                      Открыть пост
                    </button>
                  )}
                  {s.key && s.lat != null && s.lon != null && (
                    sameSpot(appliedKeys[s.key], s) ? (
                      <span className="review__applied">✓ Точка применена</span>
                    ) : (
                      <button
                        className="row-action"
                        disabled={busy === s.id}
                        onClick={() => run(s.id, () => onApplySuggestion(s))}
                        title="Поставить посту эту точку вместо найденных мест"
                      >
                        {s.extra?.length || s.related?.length ? 'Применить точки' : 'Применить точку'}
                      </button>
                    )
                  )}
                  {s.key && (
                    <button
                      className="row-action row-action--toggle"
                      aria-pressed={formOpen(s.id, 'point')}
                      onClick={() => openForm(s.id, 'point', s)}
                    >
                      {appliedKeys[s.key] && !sameSpot(appliedKeys[s.key], s)
                        ? 'Изменить ручную точку'
                        : 'Указать точку вручную'}
                    </button>
                  )}
                  {s.from === 'model' && s.status !== 'rework' && (
                    <button
                      className="row-action row-action--toggle"
                      aria-pressed={formOpen(s.id, 'rework')}
                      onClick={() => openForm(s.id, 'rework', s)}
                    >
                      Отправить на доработку
                    </button>
                  )}
                  {s.key &&
                    (ruled.has(s.key) ? (
                      <span className="review__applied">✓ Ждёт правила</span>
                    ) : (
                      <button
                        className="row-action row-action--toggle"
                        aria-pressed={ruleFor === s.id}
                        onClick={() => setRuleFor(ruleFor === s.id ? null : s.id)}
                        title="Записать случай, чтобы правила разбора научились решать его сами"
                      >
                        Придумать правило
                      </button>
                    ))}
                  {s.key && findPost(s.key) && (
                    <button
                      className="row-action"
                      disabled={busy === s.id}
                      onClick={() => run(s.id, () => onHidePost(findPost(s.key)))}
                      title="Убрать пост из списка и с карты — например, неважный или рекламный"
                    >
                      Скрыть пост
                    </button>
                  )}
                  {s.key && findPost(s.key) && onToggleFlagged && (
                    <FlagButton post={findPost(s.key)} flagged={flagged} onToggle={onToggleFlagged} />
                  )}
                  {!String(s.id).startsWith('local') && (
                    <button
                      className="row-action row-action--danger"
                      disabled={busy === s.id}
                      onClick={() => run(s.id, () => onDeleteSuggestion(s))}
                    >
                      Удалить
                    </button>
                  )}
                </div>
                {formFor?.id === s.id && formFor.kind === 'point' && (
                  <div className="suggest">
                    <PointsEditor
                      draftKey={`points:${s.key}`}
                      search={s.query}
                      initial={startPoints(s)}
                      placeholder="Подпись точки: «Мосфильмовская улица», «гостиница «Украина»»"
                      onSave={async (points) => {
                        await onManualPoint(s, points)
                        setFormFor(null)
                      }}
                    />
                  </div>
                )}
                {formOpen(s.id, 'rework') && (
                  <NoteForm
                    draftKey={`rework:${s.key}`}
                    placeholder="Что не так? Например: «речь про Мосфильмовскую улицу — нужна точка на ней, а не центр Москвы»"
                    submitLabel="Отправить на доработку"
                    onSubmit={async (note) => {
                      await onRework(s, note)
                      setFormFor(null)
                    }}
                  />
                )}
                {ruleFor === s.id && (
                  <NoteForm
                    draftKey={`rule:${s.key}`}
                    placeholder="Что должно понять правило? Например: «общежитие фабрики» — не сама фабрика"
                    submitLabel="Записать случай"
                    onSubmit={async (note) => {
                      await onRequestRule({
                        key: s.key,
                        expected: s.label || s.text?.replace(/^Модель:\s*/, '').split('. ')[0] || '',
                        ...(s.lat != null ? { lat: s.lat, lon: s.lon } : {}),
                        note,
                        text: s.text ?? '',
                      })
                      setRuleFor(null)
                    }}
                  />
                )}
              </article>
  )


  return (
    // спрятана, пока админ смотрит место из тега, — не размонтируем (App.jsx)
    <div className="overlay" onClick={onClose} style={away ? { display: 'none' } : undefined}>
      <div className="sheet sheet--wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal__bar">
          <span className="modal__spacer" />
          <h3>Модерация</h3>
          <button className="modal__done" onClick={onClose}>
            Закрыть
          </button>
        </div>

        <div className="sheet__tabs">
          <div className="tabbar" role="tablist">
            {tabs.map(([key, title, count]) => (
              <button
                key={key}
                role="tab"
                aria-selected={tab === key}
                className={tab === key ? 'tabbar__on' : ''}
                onClick={() => setTab(key)}
              >
                <span>{title}</span>
                <span className="tab__count">{count}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="sheet__body">
          {error && <p className="sheet__error">{error}</p>}

          {(tab === 'pending' || tab === 'published') && posts.length === 0 && (
            <p className="sheet__note">
              {tab === 'pending' ? 'Новых постов нет.' : 'Опубликованных постов пока нет.'}
            </p>
          )}

          {tab === 'reports' && (
            <p className="sheet__note">
              {reports.length
                ? 'Круги разбора постов моделями: что найдено и где точку найти не удалось — с причиной. «Принять» — согласиться с тем, что сейчас на карте (точка верна, точнее не найти или места нет): пост уйдёт во вкладку «Решено».'
                : 'Отчётов пока нет.'}
            </p>
          )}
          {tab === 'reports' &&
            reports.map((r) => (
              <Report key={r.id} report={r} edits={edits} reviewed={reviewed} hidden={hidden} renderPost={reportPost} />
            ))}

          {/* посты посетителей — только в своих вкладках: раньше опубликованные
              показывались и под «Рекламой» и «Правилами» */}
          {(tab === 'pending' || tab === 'published') &&
            posts.map((p) => (
              <article key={p.id} className="review">
                <header className="review__head">
                  <strong>{p.nick}</strong>
                  <span>{humanDateTime(p.at)}</span>
                </header>
                <p className="review__text">{p.text}</p>
                <p className="review__meta">
                  {p.place ? `${p.place} · ` : ''}
                  <Coords point={p} />
                </p>
                {p.photos.length > 0 && (
                  <div className="thumbs">
                    {p.photos.map((name, i) => (
                      <button
                        key={name}
                        className="thumbs__item"
                        title="Открыть фото"
                        onClick={() =>
                          showPhotos(p.photos.map((n) => ({ src: photoURL({ src: USER_SRC }, n) })), i)
                        }
                      >
                        <img src={photoURL({ src: USER_SRC }, name)} alt="" loading="lazy" />
                      </button>
                    ))}
                  </div>
                )}
                <div className="review__actions">
                  {tab === 'pending' && (
                    <button
                      className="row-action"
                      disabled={busy === p.id}
                      onClick={() => run(p.id, () => onApprove(p.id))}
                    >
                      Одобрить
                    </button>
                  )}
                  <button
                    className="row-action row-action--danger"
                    disabled={busy === p.id}
                    // без «вы уверены?»: после удаления внизу появится «Вернуть»
                    onClick={() => run(p.id, () => onRemove(p.id))}
                  >
                    {tab === 'pending' ? 'Отклонить' : 'Удалить'}
                  </button>
                </div>
              </article>
            ))}

          {tab === 'ads' && (
            <p className="sheet__note">
              {ads.length
                ? 'Модель сочла эти посты рекламой, и они уже скрыты с сайта. Скройте навсегда или верните, если это не реклама.'
                : 'Постов, похожих на рекламу, нет.'}
            </p>
          )}
          {tab === 'ads' &&
            ads.map((s) => (
              <article key={s.id} className="review">
                <header className="review__head">
                  <strong>{s.key}</strong>
                  <span>
                    {s.model && <span className="review__model">{s.model}</span>}
                    {s.at && <span className="review__date">{humanDateTime(s.at)}</span>}
                  </span>
                </header>
                <PostPreview post={findPost(s.key)} places={places} edits={edits} onPhoto={showPhotos} />
                <p className="review__ad">Похоже на рекламу: {s.ad}</p>
                <div className="review__actions">
                  {findPost(s.key) && (
                    <button
                      className="row-action row-action--primary"
                      disabled={busy === s.id}
                      onClick={() => run(s.id, () => onHidePost(findPost(s.key)))}
                    >
                      Скрыть навсегда
                    </button>
                  )}
                  <button className="row-action" disabled={busy === s.id} onClick={() => run(s.id, () => onNotAd(s))}>
                    Не реклама
                  </button>
                  <button className="row-action" onClick={() => onOpenPost(s.key)}>
                    Открыть пост
                  </button>
                </div>
              </article>
            ))}

          {tab === 'rules' && (
            <p className="sheet__note">
              Случаи, которые правила разбора решают неверно. По этому списку правила пишутся
              в сессии Claude Code — скажите «придумай правила».
            </p>
          )}
          {tab === 'rules' &&
            rules.map((r) => (
              <article key={r.id} className="review">
                <header className="review__head">
                  <strong>{r.key}</strong>
                  <span>{r.at ? humanDateTime(r.at) : ''}</span>
                </header>
                <PostPreview post={findPost(r.key)} places={places} edits={edits} onPhoto={showPhotos} />
                {r.text && <p className="review__text review__text--answer">{r.text.replace(/\.\./g, '.')}</p>}
                <p className="review__meta">
                  Должно получиться: {r.expected || '—'}
                  {r.lat != null && (
                    <>
                      {' · '}
                      <Coords point={r} />
                    </>
                  )}
                </p>
                {r.note && <p className="review__meta">Пояснение: {r.note}</p>}
                <div className="review__actions">
                  <button className="row-action" onClick={() => onOpenPost(r.key)}>
                    Открыть пост
                  </button>
                  <button
                    className="row-action row-action--danger"
                    onClick={() => run(r.id, () => onDeleteRule(r))}
                  >
                    Удалить
                  </button>
                </div>
              </article>
            ))}

          {(tab === 'suggestions' || tab === 'model') && shown.length === 0 && (
            <p className="sheet__note">
              {tab === 'model' ? 'Неразобранных предложений модели нет.' : 'Уточнений нет.'}
            </p>
          )}
          {tab === 'model' && modelDone > 0 && (
            <p className="sheet__note">
              Ещё {modelDone} {plural(modelDone, 'пост', 'поста', 'постов')} с уже поставленной точкой или
              скрытых — убраны из списка.
            </p>
          )}

          {/* что делают кнопки карточек — на виду, один раз над списком, а не
              во всплывающих подсказках у каждой (на телефоне их нет) */}
          {(tab === 'suggestions' || tab === 'model') && shown.length > 0 && (
            <p className="sheet__note">
              «Применить точку» ставит посту точку из карточки вместо найденных мест. «Придумать правило»
              записывает случай, чтобы правила разбора научились решать его сами. «Скрыть пост» убирает его из
              списка и с карты для всех посетителей. «Отметить» — пост, с которым пока непонятно, что делать: он
              остаётся здесь и попадает во вкладку «Отмеченные» на сайте.
            </p>
          )}
          {(tab === 'suggestions' || tab === 'model') &&
            shown.map((s) => suggestionCard(s))}
        </div>
      </div>
      {viewer && <PhotoViewer photos={viewer.photos} start={viewer.start} onClose={closeViewer} />}
    </div>
  )
}
