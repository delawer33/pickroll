import { cn } from '@/shared/ui/cn'

/** Скелетон повторяет форму будущего контента, а не крутит спиннер по центру (§7.5).
 * Не мигает и не пульсирует: единственный бесконечный цикл в приложении — курсор
 * печати (§5.7). */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn('bg-fill', className)} />
}
