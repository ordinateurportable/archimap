import { FOOD_FILTER, formatCount, plural } from '../data.js'

export default function Toolbar({
  group,
  counts,
  onGroup,
  query,
  onQuery,
  placeFilter,
  onPlaceFilter,
  onBackToModeration,
  topPlaces,
  foodCount,
  shown,
  total,
  view,
  onView,
  sources,
  bySource,
  srcFilter,
  onSrcFilter,
  sort,
  onSort,
  admin,
  groups,
  editedCount,
  bugCount,
  suggestionCount,
  onExport,
  offline,
}) {
  const channels = Object.entries(sources ?? {})
  // место из тега в карточке («Магаданская область») может не входить в
  // список частых — тогда список показывал «Все места», хотя фильтр включён
  const placeTitle = placeFilter === FOOD_FILTER ? 'Посты про еду' : placeFilter
  const placeInList = !placeFilter || placeFilter === FOOD_FILTER || topPlaces.some((p) => p.name === placeFilter)
  const channelTitle = srcFilter ? sources?.[srcFilter]?.title ?? srcFilter : ''
  const searched = query.trim()
  return (
    <div className="toolbar">
      <div className="toolbar__inner">
        <div className="tabs">
          {/* вкладки — только у админа: у посетителя одна «Россия», а одна
              вкладка — не выбор, а шум (число постов есть в счётчике) */}
          {groups.length > 1 ? (
            <div className="tabbar" role="tablist">
              {groups.map((g) => (
                <button
                  key={g.key}
                  role="tab"
                  aria-selected={g.key === group}
                  className={g.key === group ? 'tabbar__on' : ''}
                  onClick={() => onGroup(g.key)}
                >
                  <span>{g.title}</span>
                  <span className="tab__count">{formatCount(counts[g.key] ?? 0)}</span>
                </button>
              ))}
            </div>
          ) : (
            <span />
          )}

          <div className="segmented views">
            {[
              ['list', 'Список'],
              ['map', 'Карта'],
            ].map(([key, title]) => (
              <button
                key={key}
                className={view === key ? 'segmented__on' : ''}
                onClick={() => onView(key)}
              >
                {title}
              </button>
            ))}
          </div>
        </div>

        {groups.length > 1 && (
          <p className="tabs__hint">
            {(() => {
              const g = groups.find((x) => x.key === group)
              return g ? `${g.title}: ${g.hint}` : ''
            })()}
          </p>
        )}

        <div className="filters">
          <div className="searchbar">
            <span className="searchbar__icon">⌕</span>
            <input
              className="search"
              type="search"
              placeholder="Поиск"
              value={query}
              onChange={(e) => onQuery(e.target.value)}
            />
          </div>

          {channels.length > 1 && (
            <select
              className={srcFilter ? 'select select--on' : 'select'}
              value={srcFilter}
              onChange={(e) => onSrcFilter(e.target.value)}
            >
              <option value="">Все каналы</option>
              {channels.map(([key, ch]) => (
                <option key={key} value={key}>
                  {ch.title} ({formatCount(bySource?.[key] ?? 0)})
                </option>
              ))}
            </select>
          )}

          <select
            className={placeFilter ? 'select select--on' : 'select'}
            value={placeFilter}
            onChange={(e) => onPlaceFilter(e.target.value)}
          >
            <option value="">Все места</option>
            {!placeInList && <option value={placeFilter}>{placeFilter}</option>}
            {foodCount > 0 && (
              <option value={FOOD_FILTER}>
                Еда ({formatCount(foodCount)})
              </option>
            )}
            {topPlaces.map(({ name, count }) => (
              <option key={name} value={name}>
                {name} ({count})
              </option>
            ))}
          </select>

          {view === 'list' && (
            <select
              className="select"
              value={sort}
              onChange={(e) => onSort(e.target.value)}
            >
              <option value="new">Сначала новые</option>
              <option value="old">Сначала старые</option>
              <option value="liked">Больше реакций</option>
              <option value="photos">Больше фотографий</option>
            </select>
          )}

          <span className="counter">
            {view === 'map'
              ? `${formatCount(total)} ${plural(total, 'пост', 'поста', 'постов')} на карте`
              : `показано ${formatCount(shown)} из ${formatCount(total)}`}
          </span>

          {offline && (
            <span className="counter counter--warn">
              Не удаётся связаться с сервером: смотреть можно, отправлять пока нельзя
            </span>
          )}

          {admin && (editedCount > 0 || bugCount > 0 || suggestionCount > 0) && (
            // не машинное «Выгрузить: правок 3», а фраза: «Скачать 3 правки и 1 баг»
            // (bureau.ru/soviet/20160405)
            <button className="row-action" onClick={onExport} title="Файл JSON">
              Скачать{' '}
              {[
                editedCount && `${formatCount(editedCount)} ${plural(editedCount, 'правку', 'правки', 'правок')}`,
                bugCount && `${formatCount(bugCount)} ${plural(bugCount, 'баг', 'бага', 'багов')}`,
                suggestionCount &&
                  `${formatCount(suggestionCount)} ${plural(suggestionCount, 'уточнение', 'уточнения', 'уточнений')}`,
              ]
                .filter(Boolean)
                .join(', ')
                .replace(/, ([^,]*)$/, ' и $1')}
            </button>
          )}
        </div>

        {/* На каждом экране понятно, о чём он и как выйти: после нажатия на
            тег места в карточке — заголовок с местом и кнопка назад ко всем */}
        {(placeFilter || srcFilter || searched || onBackToModeration) && (
          <div className="scope">
            <h2 className="scope__title">
              {[searched && `«${searched}»`, placeTitle, channelTitle].filter(Boolean).join(' · ') || 'Все места'}
              <span className="scope__count">
                {formatCount(total)} {plural(total, 'пост', 'поста', 'постов')}
              </span>
            </h2>
            {placeFilter && (
              <button className="row-action" onClick={() => onPlaceFilter('')}>
                Показать все места
              </button>
            )}
            {srcFilter && (
              <button className="row-action" onClick={() => onSrcFilter('')}>
                Показать все каналы
              </button>
            )}
            {/* поиск — такой же фильтр: виден в заголовке и снимается рядом */}
            {searched && (
              <button className="row-action" onClick={() => onQuery('')}>
                Показать без поиска
              </button>
            )}
            {/* пришёл сюда из модерации (тег места в посте) — путь обратно
                рядом с названием места, модерация откроется там же */}
            {onBackToModeration && (
              <button className="row-action row-action--primary" onClick={onBackToModeration}>
                Вернуться в модерацию
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
