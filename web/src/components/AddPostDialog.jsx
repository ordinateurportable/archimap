import { useEffect, useRef, useState } from 'react'
import { compressImage } from '../images.js'
import LocationPicker from './LocationPicker.jsx'
import { useDraft } from '../drafts.js'

const MAX_PHOTOS = 4 // то же число проверяет server/index.js
const NICK_KEY = 'archimap.nick.v1'

function readNick() {
  try {
    return localStorage.getItem(NICK_KEY) ?? ''
  } catch {
    return ''
  }
}

function writeNick(nick) {
  try {
    localStorage.setItem(NICK_KEY, nick)
  } catch {
    /* ник просто не запомнится */
  }
}

// Пост от посетителя. Регистрации нет: достаточно ника. Всё присланное
// сначала уходит админу на модерацию и только потом появляется на карте.
export default function AddPostDialog({ onSubmit, onClose }) {
  const [nick, setNick] = useState(readNick)
  // закрыл форму, не отправив, — написанное ждёт в черновике (drafts.js);
  // стирается после отправки или кнопкой «Очистить форму»
  const [text, setText, clearText] = useDraft('add-post:text')
  const [place, setPlace, clearPlace] = useDraft('add-post:place')
  const [point, setPoint, clearPoint] = useDraft('add-post:point', null)
  const [photos, setPhotos, clearPhotos] = useDraft('add-post:photos', []) // data URL уже ужатых JPEG
  const clearForm = () => {
    clearText()
    clearPlace()
    clearPoint()
    clearPhotos()
  }
  const touched = Boolean(text || place || point || photos.length)
  const [website, setWebsite] = useState('') // ловушка для ботов, см. сервер
  const [busy, setBusy] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)
  const fileInput = useRef(null)

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const addFiles = async (files) => {
    const room = MAX_PHOTOS - photos.length
    const picked = [...files].slice(0, room)
    if (!picked.length) return
    setPreparing(true)
    setError('')
    try {
      const ready = await Promise.all(picked.map(compressImage))
      setPhotos([...photos, ...ready].slice(0, MAX_PHOTOS))
    } catch (e) {
      setError(e.message || 'не получилось подготовить фотографию')
    } finally {
      setPreparing(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const trimmedNick = nick.trim()
  // «Отправить» не блокируем: если чего-то не хватает, после нажатия
  // говорим об этом у самого поля
  const [tried, setTried] = useState(false)
  const problems = {
    nick: trimmedNick.length < 2 && 'Напишите ник — хотя бы две буквы',
    text: !text.trim() && 'Напишите, о чём пост',
    point: !point && 'Поставьте точку на карте или вставьте координаты',
  }
  const valid = !problems.nick && !problems.text && !problems.point
  const submit = async (e) => {
    e.preventDefault()
    if (busy || preparing) return
    if (!valid) {
      setTried(true)
      // сообщения появятся после перерисовки — прокручиваем к первому
      requestAnimationFrame(() =>
        document.querySelector('.sheet .field-error')?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      )
      return
    }
    setBusy(true)
    setError('')
    try {
      await onSubmit({
        nick: trimmedNick,
        text: text.trim(),
        place: place.trim(),
        lat: point.lat,
        lon: point.lon,
        photos,
        website,
      })
      writeNick(trimmedNick)
      clearForm()
      setDone(true)
    } catch (err) {
      setError(err.message || 'не получилось отправить')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <form className="sheet" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="modal__bar">
          <button type="button" className="modal__cancel" onClick={onClose}>
            {done ? '' : 'Закрыть'}
          </button>
          <h3>Новый пост</h3>
          {done ? (
            <button type="button" className="modal__done" onClick={onClose}>
              Закрыть
            </button>
          ) : (
            <button type="submit" className="modal__done">
              {busy ? 'Отправка…' : 'Отправить'}
            </button>
          )}
        </div>

        <div className="sheet__body">
          {done ? (
            <p className="sheet__done">
              Пост отправлен на модерацию и появится на карте, как только его одобрит
              админ.
            </p>
          ) : (
            <>
              <h4>Автор</h4>
              <div className="modal__section">
                <input
                  className="field"
                  placeholder="Ваш ник"
                  maxLength={40}
                  value={nick}
                  onChange={(e) => setNick(e.target.value)}
                  autoComplete="nickname"
                />
                {tried && problems.nick && <p className="field-error">{problems.nick}</p>}
              </div>

              <h4>Текст</h4>
              <div className="modal__section">
                <textarea
                  className="suggest__input"
                  rows={5}
                  maxLength={4000}
                  placeholder="Что это за здание, чем интересно, когда построено…"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                />
                {tried && problems.text && <p className="field-error">{problems.text}</p>}
              </div>

              <h4>Место</h4>
              <div className="modal__section suggest">
                <input
                  className="field"
                  placeholder="Название или адрес (необязательно)"
                  maxLength={120}
                  value={place}
                  onChange={(e) => setPlace(e.target.value)}
                />
                <LocationPicker value={point} onChange={setPoint} />
                {tried && problems.point && <p className="field-error">{problems.point}</p>}
              </div>

              <h4>
                Фотографии · {photos.length} из {MAX_PHOTOS}
              </h4>
              <div className="modal__section">
                <div className="thumbs">
                  {photos.map((src, i) => (
                    <div key={i} className="thumbs__item">
                      <img src={src} alt="" />
                      <button
                        type="button"
                        className="thumbs__x"
                        onClick={() => setPhotos(photos.filter((_, j) => j !== i))}
                        title="Убрать фотографию"
                      >
                        ×
                      </button>
                    </div>
                  ))}
                  {photos.length < MAX_PHOTOS && (
                    <button
                      type="button"
                      className="thumbs__add"
                      onClick={() => fileInput.current?.click()}
                      disabled={preparing}
                    >
                      {preparing ? '…' : '＋'}
                    </button>
                  )}
                </div>
                <input
                  ref={fileInput}
                  type="file"
                  accept="image/*"
                  multiple
                  hidden
                  onChange={(e) => addFiles(e.target.files)}
                />
              </div>

              {/* поле для ботов: человеку его не видно */}
              <input
                className="trap"
                tabIndex={-1}
                autoComplete="off"
                aria-hidden="true"
                value={website}
                onChange={(e) => setWebsite(e.target.value)}
                name="website"
              />

              {error && <p className="sheet__error">{error}</p>}
              <p className="sheet__note">
                Пост появится на карте после проверки модератором. Если закрыть форму,
                написанное сохранится до следующего раза.
              </p>
              {touched && (
                <p className="more">
                  <button type="button" className="row-action" onClick={clearForm}>
                    Очистить форму
                  </button>
                </p>
              )}
            </>
          )}
        </div>
      </form>
    </div>
  )
}
