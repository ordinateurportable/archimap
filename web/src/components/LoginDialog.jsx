import { useEffect, useRef, useState } from 'react'

// Окно входа в духе системного диалога iOS: заголовок, поле и два
// действия внизу, разделённые волосяными линиями.
export default function LoginDialog({ onLogin, onClose }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [shown, setShown] = useState(false)
  const input = useRef(null)

  useEffect(() => {
    input.current?.focus()
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    if (!password) {
      setError('Введите пароль')
      input.current?.focus()
      return
    }
    setBusy(true)
    setError('')
    try {
      await onLogin(password)
      onClose()
    } catch (err) {
      setError(err.message || 'не получилось войти')
      // введённое не стираем (справочник Бирмана: данные пользователя
      // бесконечно важны) — выделяем: опечатался — видно с «Показать
      // пароль», набрать заново — просто начать печатать
      input.current?.focus()
      input.current?.select()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <form className="alert" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="alert__body">
          <h3>Режим админа</h3>
          <p>Введите пароль, чтобы править карточки.</p>
          {/* Имя и autocomplete — чтобы браузер предложил запомнить пароль и
              подставлял его сам: менеджеры паролей узнают форму входа по паре
              «имя + текущий пароль». Имя у админа одно, его не вводят */}
          <input type="text" name="username" autoComplete="username" value="admin" readOnly hidden />
          <input
            ref={input}
            className="alert__input"
            type={shown ? 'text' : 'password'}
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value)
              setError('')
            }}
            placeholder="Пароль"
          />
          {/* ошибка — сразу под полем, к которому относится */}
          {error && <p className="alert__error">{error[0].toUpperCase() + error.slice(1)}</p>}
          <label className="alert__check">
            <input type="checkbox" checked={shown} onChange={(e) => setShown(e.target.checked)} />
            Показать пароль
          </label>
        </div>
        <div className="alert__actions">
          <button type="button" onClick={onClose}>
            Закрыть
          </button>
          <button type="submit" className="alert__primary" disabled={busy}>
            {busy ? 'Проверяю…' : 'Войти'}
          </button>
        </div>
      </form>
    </div>
  )
}
