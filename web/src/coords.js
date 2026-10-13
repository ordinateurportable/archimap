// Координаты из поля ввода. Люди копируют их откуда угодно, поэтому
// понимаем самые частые записи:
//   55.7558, 37.6173        — Яндекс и Google Карты
//   55.7558 37.6173
//   55,7558, 37,6173        — десятичная запятая
//   55.7558° N, 37.6173° E  — с градусами и сторонами света
// Возвращает { lat, lon } или null, если разобрать не удалось.
export function parseCoords(input) {
  const s = String(input ?? '').trim()
  if (!s) return null

  // Если в строке есть точка, она и есть десятичный разделитель, а запятые
  // разделяют числа. Если точек нет, запятая, за которой сразу идут цифры,
  // — десятичная: «55,75, 37,61» даёт два числа, а не четыре.
  const numbers = s.includes('.')
    ? s.match(/-?\d+(?:\.\d+)?/g)
    : s.match(/-?\d+(?:,\d+)?/g)
  if (numbers?.length !== 2) return null
  let [lat, lon] = numbers.map((n) => Number(n.replace(',', '.')))

  // стороны света: S и W меняют знак, а «37.6 E, 55.7 N» — перепутанный
  // порядок, который буквы всё равно объясняют
  const sides = s.toUpperCase().match(/\b[NSEW]\b/g) ?? []
  if (sides.length === 2) {
    if (sides[0] === 'S') lat = -lat
    if (sides[1] === 'W') lon = -lon
    if (sides[0] === 'W') lat = -lat
    if (sides[1] === 'S') lon = -lon
    if (/[EW]/.test(sides[0])) [lat, lon] = [lon, lat]
  }

  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null
  return { lat, lon }
}

export function formatCoords(point, digits = 5) {
  if (!point) return ''
  return `${point.lat.toFixed(digits)}, ${point.lon.toFixed(digits)}`
}

// Ссылка, по которой админ может проверить присланную точку.
export function osmLink(point) {
  const { lat, lon } = point
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=17/${lat}/${lon}`
}
