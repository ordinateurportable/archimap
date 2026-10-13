import { useEffect, useState } from 'react'
import { FOOD_FILTER, FOOD_TAG, humanDate, photoURL, postURL, streetLabel, USER_SRC } from '../data.js'
import { manualPoints, visibleTags } from '../useEdits.js'
import { useDraft } from '../drafts.js'
import { formatCoords } from '../coords.js'
import LocationPicker from './LocationPicker.jsx'
import PointsEditor from './PointsEditor.jsx'
import PointMap from './PointMap.jsx'
import StreetCheck from './StreetCheck.jsx'

export default function PostModal({
  post,
  places,
  sources,
  edits,
  admin,
  bugged,
  onHideTag,
  onToggleBug,
  isHidden,
  onToggleHidden,
  isFlagged,
  onToggleFlagged,
  onSetPoints,
  onClearLocation,
  onResetPost,
  onSuggest,
  onRemovePost,
  onPlaceFilter,
  onClose,
}) {
  // уточнение посетителя — черновик: закрыл окно, не отправив, — текст и
  // точка остаются до следующего раза
  const draftId = post ? `${post.src}-${post.i}` : 'none'
  const [draft, setDraft, clearText] = useDraft(`suggest:${draftId}`)
  const [point, setPoint, clearPoint] = useDraft(`suggest-point:${draftId}`, null)
  const [picking, setPicking] = useState(false)
  const [sent, setSent] = useState(false)
  const [sendError, setSendError] = useState('')
  const [busy, setBusy] = useState(false)
  // ручные точки админа: черновик в PointsEditor до нажатия «Сохранить»
  const [editing, setEditing] = useState(false)
  // какая миникарта раскрыта: «place:…» — точка места, «street:…» — проверка
  // улицы через Яндекс. Одна за раз: каждая карта — отдельный WebGL-контекст
  const [mapOpen, setMapOpen] = useState(null)
  const toggleMap = (id) => setMapOpen((cur) => (cur === id ? null : id))

  // Esc закрывает окно — привычное поведение для просмотрщика.
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!post) return null
  const channel = sources?.[post.src]
  const link = postURL(post, sources)
  const { found, streets, related } = visibleTags(post, places, edits)
  // убранные теги — не то же самое, что точка модели в ожидании модерации
  const edit = edits[`${post.src}-${post.i}`]
  const edited = Boolean(edit && (edit.places.length || edit.streets.length))
  const fromUser = post.src === USER_SRC
  const canSend = Boolean(draft.trim() || point)

  const submit = async () => {
    if (busy) return
    // кнопку не блокируем: нажали раньше времени — объясняем, чего не хватает
    if (!canSend) {
      setSendError('Напишите уточнение или укажите точку на карте')
      return
    }
    setBusy(true)
    setSendError('')
    try {
      await onSuggest(post, draft.trim(), point)
      clearText()
      clearPoint()
      setPicking(false)
      setSent(true)
    } catch (e) {
      setSendError(e.message || 'не получилось отправить')
    } finally {
      setBusy(false)
    }
  }

  // ручные точки вместе со связанными местами — их правит один редактор
  const current = manualPoints(edits[`${post.src}-${post.i}`], true)

  const savePoints = async (points) => {
    await onSetPoints(`${post.src}-${post.i}`, points)
    setEditing(false)
  }

  // без «вы уверены?»: после удаления внизу появится «Вернуть»
  const removeUserPost = async () => {
    onClose()
    await onRemovePost(post.i)
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div
        className={`modal ${post.p.length ? '' : 'modal--text'}`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* панель навигации модального экрана: заголовок по центру,
            подтверждающая кнопка справа */}
        <div className="modal__bar">
          <span className="modal__spacer" />
          <h3>{humanDate(post.d, true)}</h3>
          <button className="modal__done" onClick={onClose}>
            Закрыть
          </button>
        </div>

        {/* у текстовых постов колонки с фото нет вовсе, иначе остаётся
            пустая чёрная половина окна */}
        {post.p.length > 0 && (
          <div className="modal__photos">
            {/* крупная версия есть только у первого фото поста,
                см. tools/build_photos.py */}
            {post.p.map((name, i) => (
              <img
                key={name}
                src={photoURL(post, name, i === 0)}
                alt=""
                // крупной версии на сервере может не быть — места мало, и
                // для части постов нарезано только превью
                onError={(e) => {
                  const small = photoURL(post, name)
                  if (i === 0 && !e.currentTarget.src.endsWith(small)) e.currentTarget.src = small
                }}
              />
            ))}
          </div>
        )}

        <div className="modal__side">
          <h4>{fromUser ? `Пост посетителя · ${post.a}` : channel ? channel.title : 'Пост'}</h4>
          <p className="modal__text">{post.t || <em>Без подписи</em>}</p>

          {post.np > post.p.length && (
            <>
              <h4>Фотографии</h4>
              <p className="modal__section">
                показаны {post.p.length} из {post.np}
              </p>
            </>
          )}

          {found.length > 0 && (
            <section>
              <h4>Найденные места</h4>
              <ul className="places">
                {found.map((pl) => (
                  <li key={pl.key ?? pl.name}>
                    <button
                      className={`chip chip--link chip--${pl.scope}`}
                      onClick={() => onPlaceFilter?.(pl.name)}
                      title="Показать все посты этого места"
                    >
                      {pl.name}
                    </button>
                    <span className="places__right">
                      {/* координаты — кнопка: посмотреть точку на карте */}
                      <button
                        className="coords-link"
                        aria-pressed={mapOpen === `place:${pl.key ?? pl.name}`}
                        onClick={() => toggleMap(`place:${pl.key ?? pl.name}`)}
                        title="Показать точку на карте"
                      >
                        {pl.lat.toFixed(3)}, {pl.lon.toFixed(3)}
                      </button>
                      {admin && !pl.manual && (
                        <button
                          className="row-action row-action--danger"
                          onClick={() => onHideTag(post, 'places', pl.name)}
                        >
                          Убрать
                        </button>
                      )}
                    </span>
                    {mapOpen === `place:${pl.key ?? pl.name}` && (
                      <div className="places__map">
                        <PointMap points={[{ lat: pl.lat, lon: pl.lon }]} />
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* не место поста, а то, с чем пост его связывает: «похожесть
              зданий видна невооружённым глазом» — Исаакиевский собор в посте
              про Староярмарочный. На карте поста их нет */}
          {related.length > 0 && (
            <section>
              <h4>Связанные места</h4>
              <ul className="places">
                {related.map((pl) => (
                  <li key={pl.key}>
                    <span className="chip chip--related chip--static" title={pl.model ? 'Предложено моделью, ждёт решения' : undefined}>
                      {pl.name}
                    </span>
                    <span className="places__right">
                      <code>
                        {pl.lat.toFixed(3)}, {pl.lon.toFixed(3)}
                      </code>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {post.f && (
            <section>
              <h4>Темы</h4>
              <ul className="places">
                <li>
                  <button
                    className="chip chip--link chip--food"
                    onClick={() => onPlaceFilter?.(FOOD_FILTER)}
                    title="Показать все посты про еду"
                  >
                    {FOOD_TAG}
                  </button>
                </li>
              </ul>
            </section>
          )}

          {streets.length > 0 && (
            <section>
              <h4>Улицы и местности</h4>
              <ul className="places">
                {streets.map((s) => (
                  <li key={s}>
                    {admin ? (
                      // точки у улицы нет — админ может спросить Яндекс, где она
                      <button
                        className="chip chip--street chip--link"
                        aria-pressed={mapOpen === `street:${s}`}
                        onClick={() => toggleMap(`street:${s}`)}
                        title="Проверить по Яндекс-геокодеру, где это"
                      >
                        {streetLabel(s)}
                      </button>
                    ) : (
                      <span className="chip chip--street chip--static">{streetLabel(s)}</span>
                    )}
                    {admin && (
                      <button
                        className="row-action row-action--danger"
                        onClick={() => onHideTag(post, 'streets', s)}
                      >
                        Убрать
                      </button>
                    )}
                    {mapOpen === `street:${s}` && (
                      <div className="places__map">
                        <StreetCheck query={streetQuery(s, found)} />
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {admin && (
            <>
              <h4>Место на карте</h4>
              <div className="modal__section suggest">
                {editing ? (
                  <PointsEditor
                    draftKey={`points:${post.src}-${post.i}`}
                    initial={current}
                    around={found.find((p) => !p.manual)}
                    onSave={savePoints}
                    onCancel={() => setEditing(false)}
                  />
                ) : (
                  <>
                    {current.map((c, i) => (
                      <p key={i} className="suggest__point">
                        {c.related
                          ? 'Связанное место'
                          : current.length > 1
                            ? `Точка ${i + 1} указана вручную`
                            : 'Точка указана вручную'}
                        :{' '}
                        {c.name ? `${c.name} · ` : ''}
                        {formatCoords(c)}
                      </p>
                    ))}
                    <div className="review__actions">
                      <button className="row-action" onClick={() => setEditing(true)}>
                        {current.length ? (current.length > 1 ? 'Изменить точки' : 'Изменить точку') : 'Указать точку вручную'}
                      </button>
                      {current.length > 0 && (
                        <button
                          className="row-action row-action--danger"
                          onClick={() => onClearLocation(`${post.src}-${post.i}`)}
                        >
                          {current.length > 1 ? 'Убрать ручные точки' : 'Убрать ручную точку'}
                        </button>
                      )}
                    </div>
                  </>
                )}
              </div>

              <h4>Отметки и видимость</h4>
              <div className="modal__section modal__section--rows">
                <button
                  className={`row-action ${isFlagged ? 'row-action--on' : ''}`}
                  onClick={() => onToggleFlagged(post)}
                >
                  {isFlagged ? '★ Отмечен — снять отметку' : 'Отметить'}
                </button>
                {!isFlagged && (
                  <p className="suggest__point">
                    Чтобы вернуться к посту позже: он появится во вкладке «Отмеченные».
                  </p>
                )}
                {post.ad ? (
                  <p className="suggest__point">
                    Реклама: пост скрыт автоматически, в нём есть маркировка (erid).
                  </p>
                ) : (
                  <button
                    className={`row-action ${isHidden ? '' : 'row-action--danger'}`}
                    onClick={() => onToggleHidden(post)}
                  >
                    {isHidden ? 'Вернуть пост на сайт' : 'Скрыть пост с сайта'}
                  </button>
                )}
              </div>
            </>
          )}

          {admin && fromUser && (
            <>
              <h4>Пост посетителя</h4>
              <div className="modal__section modal__section--rows">
                <button className="row-action row-action--danger" onClick={removeUserPost}>
                  Удалить пост
                </button>
              </div>
            </>
          )}

          {admin && !fromUser && (
            <>
              <h4>Правки</h4>
              <div className="modal__section modal__section--rows">
                <button
                  className={`row-action ${bugged ? 'row-action--on' : ''}`}
                  onClick={() => onToggleBug(post, found.map((x) => x.name))}
                >
                  {bugged ? '✓ В баг-репорте' : 'Добавить в баг-репорт'}
                </button>
                {edited && (
                  <button className="row-action" onClick={() => onResetPost(post)}>
                    Вернуть убранные теги
                  </button>
                )}
              </div>
            </>
          )}

          {/* посетитель не правит карточку сам, а оставляет замечание:
              словами, точкой на карте или координатами */}
          {!admin && !fromUser && (
            <>
              <h4>Уточнить место</h4>
              <div className="modal__section suggest">
                {sent ? (
                  <p className="suggest__done">
                    Уточнение сохранено — админ увидит его в модерации.
                  </p>
                ) : (
                  <>
                    <textarea
                      className="suggest__input"
                      rows={3}
                      placeholder="Например: это не Рыбацкое в Петербурге, а деревня в Рязанской области"
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                    />
                    {picking ? (
                      <LocationPicker value={point} onChange={setPoint} around={found[0]} />
                    ) : (
                      <button className="row-action" onClick={() => setPicking(true)}>
                        Указать точку на карте или координаты
                      </button>
                    )}
                    {point && (
                      <p className="suggest__point">Точка: {formatCoords(point)}</p>
                    )}
                    {sendError && <p className="alert__error">{sendError}</p>}
                    <button className="row-action" onClick={submit}>
                      {busy ? 'Отправляю…' : 'Отправить'}
                    </button>
                  </>
                )}
              </div>
            </>
          )}

          {link && (
            <a className="modal__link" href={link} target="_blank" rel="noreferrer">
              Открыть в Telegram
            </a>
          )}
        </div>
      </div>
    </div>
  )
}

// «с. Великая Тополь» ищем в области поста (её подсказка надёжнее города),
// улицу — в городе: «ул. Ленина, Брянск»
const LOCALITY_PREFIX = /^(?:с|д|пос|п|х|ст|рп|пгт|дер|н\. п)\.\s/

function streetQuery(name, found) {
  const region = found.find((p) => p.region)?.name
  const city = found.find((p) => !p.region && !p.model)?.name.split(' · ').pop()
  const where = LOCALITY_PREFIX.test(name) ? region ?? city : city ?? region
  const label = LOCALITY_PREFIX.test(name) ? name : streetLabel(name)
  return where ? `${label}, ${where}` : label
}
