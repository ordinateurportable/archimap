import { FOOD_FILTER, FOOD_TAG, humanDate, photoURL, placeLabel, postKey, streetLabel } from '../data.js'
import { visibleTags } from '../useEdits.js'

export default function PostCard({
  post,
  places,
  sources,
  edits,
  admin,
  bugged,
  onHideTag,
  onToggleBug,
  onToggleHidden,
  hidden,
  onToggleFlagged,
  flagged,
  onPlaceFilter,
  onOpen,
}) {
  const photo = post.p[0]
  // у поста посетителя вместо канала — ник автора
  const channel = post.a ?? sources?.[post.src]?.short
  const { found, streets } = visibleTags(post, places, edits)

  // в режиме админа клик по тегу убирает его, поэтому карточку не открываем
  const removeTag = (e, kind, name) => {
    e.stopPropagation()
    onHideTag(post, kind, name)
  }

  return (
    <article
      className={`card ${photo ? '' : 'card--text'} ${admin && flagged?.[postKey(post)] ? 'card--flagged' : ''}`}
      onClick={() => onOpen(post)}
    >
      {/* у поста без фотографий пустого серого блока нет — только текст */}
      {photo && <img className="card__photo" src={photoURL(post, photo)} loading="lazy" alt="" />}

      <div className="card__body">
        <div className="card__meta">
          <span>{humanDate(post.d)}</span>
          {post.np > 1 && <span>{post.np} фото</span>}
          {post.r > 0 && <span>♥ {post.r}</span>}
          {channel && <span className="card__src">{channel}</span>}
        </div>

        <p className="card__text">{post.t || <em>Без подписи</em>}</p>

        <div className="chips">
          {found.map((pl) => (
            <span
              key={pl.key ?? pl.name}
              className={`chip chip--link chip--${pl.name.includes(' · ') ? 'street' : pl.scope}`}
              title={`Показать все посты: ${placeLabel(pl)}`}
              role="button"
              tabIndex={0}
              // тег выглядит как кнопка — пусть и работает: показывает все
              // посты этого места
              onClick={(e) => {
                e.stopPropagation()
                onPlaceFilter?.(pl.name)
              }}
            >
              {placeLabel(pl)}
              {admin && !pl.manual && (
                <button
                  className="chip__x"
                  onClick={(e) => removeTag(e, 'places', pl.name)}
                  title="Убрать тег"
                >
                  ×
                </button>
              )}
            </span>
          ))}
          {post.f && (
            <span
              className="chip chip--link chip--food"
              title="Показать все посты про еду"
              role="button"
              tabIndex={0}
              onClick={(e) => {
                e.stopPropagation()
                onPlaceFilter?.(FOOD_FILTER)
              }}
            >
              {FOOD_TAG}
            </span>
          )}
          {streets.map((street) => (
            // улица не нажимается (фильтра по улицам нет) — и не выглядит
            // нажимаемой, как тег места
            <span key={street} className="chip chip--street chip--static">
              {streetLabel(street)}
              {admin && (
                <button
                  className="chip__x"
                  onClick={(e) => removeTag(e, 'streets', street)}
                  title="Убрать тег"
                >
                  ×
                </button>
              )}
            </span>
          ))}
        </div>

        {admin && (
          <div className="card__actions">
            <button
              className={`row-action ${bugged ? 'row-action--on' : ''}`}
              onClick={(e) => {
                e.stopPropagation()
                onToggleBug(post, found.map((x) => x.name))
              }}
              title="Отметить пост, у которого разбор сработал неправильно"
            >
              {bugged ? '✓ В баг-репорте' : 'Добавить в баг-репорт'}
            </button>
            <button
              className={`row-action ${flagged?.[postKey(post)] ? 'row-action--on' : ''}`}
              onClick={(e) => {
                e.stopPropagation()
                onToggleFlagged(post)
              }}
              title="Отметить пост, с которым пока непонятно, что делать"
            >
              {flagged?.[postKey(post)] ? '★ Отмечен' : 'Отметить'}
            </button>
            {post.ad ? (
              <span className="card__ad" title="Скрыт автоматически: в посте есть маркировка рекламы">
                Реклама
              </span>
            ) : (
              <button
                className="row-action row-action--danger"
                onClick={(e) => {
                  e.stopPropagation()
                  onToggleHidden(post)
                }}
                title="Убрать пост из списка и с карты для всех посетителей"
              >
                {/* «для всех» — словами, не только в подсказке: скрытие видят
                    все посетители, а не один админ */}
                {hidden?.[postKey(post)] ? 'Вернуть на сайт' : 'Скрыть с сайта'}
              </button>
            )}
          </div>
        )}
      </div>
    </article>
  )
}
