import { useState } from 'react'
import { useDraft } from '../drafts.js'

// Поле с пояснением и кнопкой отправки: «на доработку», «придумать правило».
// Текст — черновик: форму закрыли, не отправив, — при следующем открытии
// он на месте. Стирается только после отправки.
export default function NoteForm({ draftKey, placeholder, submitLabel, onSubmit }) {
  const [note, setNote, clearNote] = useDraft(draftKey)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const send = async () => {
    setBusy(true)
    setError('')
    try {
      await onSubmit(note.trim())
      clearNote()
    } catch (e) {
      setError(e.message || 'не получилось отправить')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="suggest">
      <textarea
        className="suggest__input"
        rows={2}
        autoFocus
        placeholder={placeholder}
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      {error && <p className="field-error">{error}</p>}
      <button className="row-action row-action--primary" disabled={busy} onClick={send}>
        {submitLabel}
      </button>
    </div>
  )
}
