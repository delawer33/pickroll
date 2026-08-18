import { Skeleton } from '@/shared/ui/Skeleton'

/** Загрузка сохранённого Диалога: скелетон повторяет форму Worktable — реплика справа,
 * ответ колонкой слева, карточка Evidence в рейке (§7.5). */
export function ConversationSkeleton() {
  return (
    <div
      role="status"
      aria-label="Loading conversation"
      className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[1fr_auto] shell:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] shell:grid-rows-1"
    >
      <div className="flex min-h-0 flex-col gap-4 p-4">
        <Skeleton className="h-8 w-[60%] self-end" />
        <div className="flex max-w-read flex-col gap-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-[80%]" />
        </div>
      </div>

      <div className="flex flex-col gap-2 border-t border-line bg-paper p-4 shell:border-t-0 shell:border-l">
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    </div>
  )
}
