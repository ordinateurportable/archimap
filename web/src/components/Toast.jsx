import { useEffect, useRef } from 'react'

// Плашки внизу экрана: что произошло и, если действие можно отменить,
// кнопка «Вернуть». Вместо вопросов «вы уверены?» — сразу делаем и даём
// отменить (см. раздел «Интерфейс» в README).
//
// Плашек может быть несколько: раньше новая вытесняла прежнюю, и скрыв два
// поста подряд, первый было уже не вернуть. Пока на плашку наведена мышь
// (или на неё нажали пальцем), она не исчезает — успеть нажать «Вернуть»
// (13.10, справочник Бирмана: «Анду»)
const MAX = 3

function Toast({ toast, onClose }) {
  const timer = useRef(null)
  const start = () => {
    clearTimeout(timer.current)
    timer.current = setTimeout(() => onClose(toast.id), toast.undo ? 7000 : 4500)
  }
  const stop = () => clearTimeout(timer.current)
  useEffect(() => {
    start()
    return stop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="toast" role="status" onMouseEnter={stop} onMouseLeave={start} onTouchStart={stop}>
      <span className="toast__text">{toast.text}</span>
      {toast.undo && (
        <button
          className="toast__undo"
          onClick={() => {
            onClose(toast.id)
            toast.undo()
          }}
        >
          Вернуть
        </button>
      )}
      <button className="toast__close" onClick={() => onClose(toast.id)} aria-label="Закрыть">
        ×
      </button>
    </div>
  )
}

export default function Toasts({ toasts, onClose }) {
  if (!toasts.length) return null
  return (
    <div className="toasts">
      {toasts.slice(-MAX).map((t) => (
        <Toast key={t.id} toast={t} onClose={onClose} />
      ))}
    </div>
  )
}
