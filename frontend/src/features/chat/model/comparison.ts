import type { ComparisonPlayer } from '@/features/chat/model/types'

/** Подпись колонки и серии radar: имя и сезон — один игрок в двух сезонах это две колонки. */
export function playerHeading(player: ComparisonPlayer): string {
  return `${player.name} ${player.season}`
}
