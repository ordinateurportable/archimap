import { useEffect, useState } from 'react'

// Фотография во весь экран поверх модерации. Закрывается кликом, Escape
// или кнопкой; стрелки листают фото поста.
//
// photos — [{ src, fallback? }]: fallback — превью, если крупной версии на
// сервере нет (места мало, крупные нарезаны не для всех постов).
export default function PhotoViewer({ photos, start = 0, onClose }) {
  const [i, setI] = useState(start)
  const many = photos.length > 1
  const step = (d) => setI((n) => (n + d + photos.length) % photos.length)

  useEffect(() => {
    // в фазе перехвата: Escape закрывает только фото, а не всю модерацию
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight' && many) step(1)
      else if (e.key === 'ArrowLeft' && many) step(-1)
      else return
      e.stopPropagation()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onClose, many])

  const photo = photos[i]
  // клик не должен дойти до подложки модерации — она закрыла бы и её
  const close = (e) => {
    e.stopPropagation()
    onClose()
  }
  return (
    <div className="viewer" onClick={close}>
      <img
        key={photo.src}
        className="viewer__photo"
        src={photo.src}
        alt=""
        onError={(e) => {
          if (photo.fallback && !e.currentTarget.src.endsWith(photo.fallback)) e.currentTarget.src = photo.fallback
        }}
      />
      <button className="viewer__close" onClick={close}>
        Закрыть
      </button>
      {/* поиск похожих картинок в Яндексе по этому снимку — подпись с местом
          бывает не на первом фото (16.10). Превью, а не крупная версия: на
          стенде крупных нет, Яндекс скачивает фото сам по адресу */}
      <a
        className="viewer__search"
        href={`https://yandex.ru/images/search?rpt=imageview&url=${encodeURIComponent(
          new URL(photo.fallback || photo.src, window.location.origin).href
        )}`}
        target="_blank"
        rel="noreferrer"
        onClick={(e) => e.stopPropagation()}
      >
        Найти это фото в Яндексе ↗
      </a>
      {many && (
        <>
          <button
            className="viewer__nav viewer__nav--prev"
            aria-label="Предыдущее фото"
            onClick={(e) => {
              e.stopPropagation()
              step(-1)
            }}
          >
            ‹
          </button>
          <button
            className="viewer__nav viewer__nav--next"
            aria-label="Следующее фото"
            onClick={(e) => {
              e.stopPropagation()
              step(1)
            }}
          >
            ›
          </button>
          <span className="viewer__count">
            {i + 1} из {photos.length}
          </span>
        </>
      )}
    </div>
  )
}
