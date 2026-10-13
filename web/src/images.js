// Фотографии посетителей ужимаем прямо в браузере: снимок с телефона весит
// 3–8 МБ, а на карте и в окне поста больше 1600 пикселей не нужно. Заодно
// всё приводится к JPEG — сервер другие форматы не принимает.
const MAX_SIDE = 1600
const QUALITY = 0.85

export async function compressImage(file) {
  if (!file.type.startsWith('image/')) throw new Error('это не изображение')
  const url = URL.createObjectURL(file)
  try {
    const img = await new Promise((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error('браузер не смог открыть фотографию'))
      el.src = url
    })
    const k = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(img.naturalWidth * k)
    canvas.height = Math.round(img.naturalHeight * k)
    const ctx = canvas.getContext('2d')
    // у PNG с прозрачностью фон иначе станет чёрным
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    // поворот по EXIF современные браузеры применяют сами
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    return canvas.toDataURL('image/jpeg', QUALITY)
  } finally {
    URL.revokeObjectURL(url)
  }
}
