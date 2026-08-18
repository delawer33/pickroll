import { ArrowUp } from 'lucide-react'
import { useState, type KeyboardEvent } from 'react'

import { IconButton } from '@/shared/ui/IconButton'
import { TextArea } from '@/shared/ui/TextArea'
import { Tooltip } from '@/shared/ui/Tooltip'

export interface ComposerProps {
  onSubmit: (question: string) => void
  /** Пока идёт ответ, отправка заблокирована: Диалог линеен. */
  disabled?: boolean
}

/** Поле ввода вопроса. Enter отправляет, Shift+Enter переносит строку — продукт
 * клавиатурный, и уводить руку на мышь ради отправки не нужно. */
export function Composer({ onSubmit, disabled = false }: ComposerProps) {
  const [value, setValue] = useState('')
  const empty = value.trim() === ''

  function submit() {
    if (empty || disabled) {
      return
    }
    onSubmit(value.trim())
    setValue('')
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div className="flex items-end gap-2 border border-line bg-surface p-1">
      <TextArea
        value={value}
        onChange={(event) => {
          setValue(event.target.value)
        }}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        aria-label="Ask a question"
        placeholder={disabled ? 'Waiting for the answer…' : 'Ask a follow-up…'}
      />
      <Tooltip content="Send · Enter" side="top">
        <IconButton
          label="Send"
          icon={<ArrowUp aria-hidden="true" size={16} />}
          onClick={submit}
          disabled={empty || disabled}
          className="mb-1 shrink-0 border-line text-ink disabled:cursor-not-allowed disabled:text-ink-muted"
        />
      </Tooltip>
    </div>
  )
}
