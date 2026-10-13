// Состояние сайта — в адресе страницы. Раньше адрес не менялся никогда:
// ссылку на пост было не отправить, «Назад» в браузере при открытом посте
// уводило с сайта, а обновление страницы сбрасывало вкладку, место и поиск
// (13.10, по «Справочнику проектировщика интерфейса» Бирмана: «Адрес
// ресурса», «Постоянная ссылка», «Назад»).
//
// ?tab=foreign&place=Казань&channel=arkh&q=шухов&sort=old&view=list#arkh-12345
// Модерация у админа — тоже экран со своим адресом: ?mod=1
// Значения по умолчанию в адрес не пишем: у главной страницы адрес чистый.

const DEFAULTS = { group: 'ru', place: '', src: '', query: '', sort: 'new', view: 'map', mod: '' }
const PARAMS = { group: 'tab', place: 'place', src: 'channel', query: 'q', sort: 'sort', view: 'view', mod: 'mod' }
const SORTS = ['new', 'old', 'liked', 'photos']
const VIEWS = ['map', 'list']

export function readURL(location = window.location) {
  const params = new URLSearchParams(location.search)
  const state = {}
  for (const [field, name] of Object.entries(PARAMS)) state[field] = params.get(name) ?? DEFAULTS[field]
  if (!SORTS.includes(state.sort)) state.sort = DEFAULTS.sort
  if (!VIEWS.includes(state.view)) state.view = DEFAULTS.view
  state.post = decodeURIComponent(location.hash.replace(/^#/, '')) || ''
  return state
}

export function buildURL(state) {
  const params = new URLSearchParams()
  for (const [field, name] of Object.entries(PARAMS)) {
    const value = state[field] ?? DEFAULTS[field]
    if (value !== DEFAULTS[field]) params.set(name, value)
  }
  const search = params.toString()
  return `${window.location.pathname}${search ? `?${search}` : ''}${state.post ? `#${state.post}` : ''}`
}

// Поиск и порядок меняются по букве и по выбору в списке — их правим на
// месте, без новой записи в истории: иначе «Назад» стирало бы запрос по
// одной букве. Остальное (вкладка, место, канал, карта/список, модерация,
// пост) — переходы, по ним «Назад» и возвращает
const STEPS = ['group', 'place', 'src', 'view', 'mod', 'post']
export function onlyTyping(a, b) {
  return STEPS.every((f) => (a[f] ?? '') === (b[f] ?? ''))
}

// из a в b закрылось окно field (пост, модерация), а остальное то же
export function closedOnly(a, b, field) {
  return Boolean(a[field]) && !b[field] && onlyTyping({ ...a, [field]: '' }, b)
}
