import { useEffect, useState } from 'react'

// Черновики: то, что админ или посетитель начал писать, но не отправил, —
// пояснение «на доработку», заявка на правило, ручные точки, уточнение к
// посту. Закрыл форму, окно или вкладку — написанное не пропадает: данные
// пользователя бесконечно важны (Бирман). Стираем, только когда отправлено.
//
// Лежат в браузере. Если хранилище недоступно (приватный режим), черновик
// живёт в памяти до перезагрузки страницы.
const STORE_KEY = 'archimap.drafts.v1'
let memory = null

function all() {
  if (memory) return memory
  try {
    memory = JSON.parse(localStorage.getItem(STORE_KEY)) || {}
  } catch {
    memory = {}
  }
  return memory
}

function persist() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(memory))
  } catch {
    /* места нет или хранилище закрыто — черновик останется в памяти */
  }
}

export function getDraft(key) {
  return all()[key]
}

// пустое значение — черновика нет
export function setDraft(key, value) {
  const drafts = all()
  const empty = value == null || value === '' || (Array.isArray(value) && value.length === 0)
  if (empty) delete drafts[key]
  else drafts[key] = value
  persist()
}

export const clearDraft = (key) => setDraft(key, null)

// Поле с черновиком: [значение, изменить, стереть после отправки]
export function useDraft(key, initial = '') {
  const [value, setValue] = useState(() => getDraft(key) ?? initial)
  // другой пост — другой черновик
  useEffect(() => {
    setValue(getDraft(key) ?? initial)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  const change = (next) => {
    setValue(next)
    setDraft(key, next)
  }
  const clear = () => {
    setValue(initial)
    clearDraft(key)
  }
  return [value, change, clear]
}
