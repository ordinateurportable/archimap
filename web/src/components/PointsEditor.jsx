import { useState } from 'react'
import { formatCoords } from '../coords.js'
import { clearDraft, getDraft, setDraft } from '../drafts.js'
import LocationPicker from './LocationPicker.jsx'

// столько принимает сервер (server/index.js): 5 точек поста и 5 связанных мест
const MAX_POINTS = 10

// Ручные точки поста — одна или несколько: пост бывает про два здания
// сразу (ДК «Подмосковье» в Красногорске и его прообраз — ДК АЗЛК в
// Москве). Карта одна, на ней правится выбранная точка; остальные — строками
// с подписью, по нажатию на строку карта переключается на неё.
//
// Точка с пометкой «Связанное место» — не место поста, а то, с чем пост его
// связывает: Староярмарочный собор в Нижнем Новгороде похож на Исаакиевский.
// На карту поста такая точка не ставится, её видно в окне поста.
//
// initial — [{ lat, lon, name, related? }]; onSave(points) — промис: ошибку покажем
// у кнопки. around — с чего начать карту, если точек ещё нет.
// draftKey — ключ черновика: закрыли редактор, не сохранив, — при следующем
// открытии поставленные точки и подписи на месте
const fromInitial = (initial) =>
  initial.length
    ? initial.map((p) => ({ point: { lat: p.lat, lon: p.lon }, name: p.name ?? '', related: Boolean(p.related) }))
    : [{ point: null, name: '' }]

export default function PointsEditor({ initial = [], around, onSave, onCancel, placeholder, draftKey, search }) {
  const [points, setPointsState] = useState(() => (draftKey && getDraft(draftKey)) || fromInitial(initial))
  const [drafted, setDrafted] = useState(() => Boolean(draftKey && getDraft(draftKey)))
  // любая правка — сразу в черновик
  const setPoints = (update) => {
    const next = typeof update === 'function' ? update(points) : update
    setPointsState(next)
    if (draftKey) {
      setDraft(draftKey, next)
      setDrafted(true)
    }
  }
  const discard = () => {
    if (draftKey) clearDraft(draftKey)
    setPointsState(fromInitial(initial))
    setDrafted(false)
    setActive(0)
  }
  const [active, setActive] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const change = (i, patch) => {
    setPoints((list) => list.map((p, j) => (j === i ? { ...p, ...patch } : p)))
    setError('')
  }
  const add = () => {
    if (points.length >= MAX_POINTS) return setError(`Больше ${MAX_POINTS} точек вместе со связанными местами не бывает`)
    setPoints((list) => [...list, { point: null, name: '' }])
    setActive(points.length)
    setError('')
  }
  const remove = (i) => {
    setPoints((list) => list.filter((_, j) => j !== i))
    setActive((a) => (a > i ? a - 1 : a === i ? Math.max(0, i - 1) : a))
  }
  const save = async () => {
    // кнопку не блокируем: нажали раньше времени — объясняем, чего не хватает
    const empty = points.findIndex((p) => !p.point)
    if (empty >= 0) {
      setActive(empty)
      return setError(
        points.length > 1
          ? `У точки ${empty + 1} нет координат — поставьте её на карте или уберите`
          : 'Поставьте точку на карте или вставьте координаты'
      )
    }
    if (points.every((p) => p.related)) {
      return setError('Хотя бы одна точка должна быть местом самого поста — снимите у неё «Связанное место»')
    }
    setBusy(true)
    try {
      await onSave(points.map((p) => ({ ...p.point, name: p.name.trim(), ...(p.related ? { related: true } : {}) })))
      if (draftKey) clearDraft(draftKey)
    } catch (e) {
      setError(e.message || 'не получилось сохранить')
    } finally {
      setBusy(false)
    }
  }

  const many = points.length > 1
  const current = points[active]
  // новая точка начинается с того места, где стоит предыдущая
  const start = current.point ?? points.find((p) => p.point)?.point ?? around

  return (
    <div className="points">
      {points.map((p, i) => (
        <div key={i} className={`points__row ${i === active ? 'points__row--on' : ''}`}>
          {many && (
            <button className="points__num" onClick={() => setActive(i)} title="Править эту точку на карте">
              {i + 1}
            </button>
          )}
          <input
            className="field"
            placeholder={placeholder ?? 'Подпись точки, например «Санаторий „Белые ночи“»'}
            maxLength={120}
            value={p.name}
            onFocus={() => setActive(i)}
            onChange={(e) => change(i, { name: e.target.value })}
          />
          {many && (
            <button className="points__coords" onClick={() => setActive(i)}>
              {p.point ? formatCoords(p.point) : 'нет точки'}
            </button>
          )}
          {/* «Связанное место» — состояние, а не команда: галочка, а не кнопка
              (справочник Бирмана: чекбокс — для «включено/выключено») */}
          {many && (
            <label className="points__related">
              <input type="checkbox" checked={p.related} onChange={(e) => change(i, { related: e.target.checked })} />
              Связанное место
            </label>
          )}
          {many && (
            <button className="row-action row-action--danger" onClick={() => remove(i)}>
              Убрать
            </button>
          )}
        </div>
      ))}
      {/* что значит галочка — на виду, а не во всплывающей подсказке: на
          телефоне подсказок нет */}
      {many && (
        <p className="picker__hint">
          Связанное место — не место поста, а то, с чем пост его связывает: прообраз, похожее здание, другая
          работа архитектора. На карту поста оно не ставится, его видно в окне поста.
        </p>
      )}
      {/* key: поле координат у выбора точки своё — при смене точки начинаем заново */}
      <LocationPicker
        key={`${active}-${points.length}`}
        value={current.point}
        onChange={(point) => change(active, { point })}
        around={start}
        search={search}
      />
      {error && <p className="field-error">{error}</p>}
      <div className="review__actions">
        <button className="row-action row-action--primary" disabled={busy} onClick={save}>
          {many ? 'Сохранить точки' : 'Сохранить точку'}
        </button>
        <button className="row-action" onClick={add}>
          Добавить ещё точку
        </button>
        {/* черновик можно выбросить только явно — закрытие его не трогает */}
        {drafted && (
          <button className="row-action" onClick={discard}>
            Вернуть как было
          </button>
        )}
        {onCancel && (
          <button className="row-action" onClick={onCancel}>
            Закрыть
          </button>
        )}
      </div>
    </div>
  )
}
