import type { SimilarFeature, SimilarItem } from '@/features/chat/model/types'

export interface SimilarResultProps {
  caption: string
  items: SimilarItem[]
}

/** Похожие игроки: каждый — в своём самом близком сезоне, с сходством и признаками, по
 * которым профили совпадают и расходятся. z-score подписан обоими игроками (искомый →
 * похожий): без пары чисел «совпадает по передачам» остаётся словом, а не проверяемым
 * фактом (§7.2). */
export function SimilarResult({ caption, items }: SimilarResultProps) {
  return (
    <div className="grid gap-3">
      <p className="t-micro text-ink-muted">{caption}</p>
      <ol className="grid gap-3">
        {items.map((item) => (
          <li key={`${item.name}-${item.season}`} className="grid min-w-0 gap-1">
            <p className="flex min-w-0 items-baseline gap-2">
              <span className="t-body-sm min-w-0 flex-1 truncate text-ink">{item.name}</span>
              <span className="t-mono shrink-0 text-ink-muted">{item.season}</span>
              <span className="num t-body-sm shrink-0 text-accent-text">
                {item.similarity.toFixed(3)}
              </span>
            </p>
            <FeatureLine title="Matches" features={item.matching} />
            <FeatureLine title="Differs" features={item.diverging} />
          </li>
        ))}
      </ol>
    </div>
  )
}

function FeatureLine({ title, features }: { title: string; features: SimilarFeature[] }) {
  return (
    <p className="t-micro flex min-w-0 flex-wrap gap-x-3 text-ink-muted">
      <span className="shrink-0">{title}</span>
      {features.map((feature) => (
        <span key={feature.label} className="num">
          {feature.label} {signed(feature.target)} / {signed(feature.similar)}
        </span>
      ))}
    </p>
  )
}

/** z-score со знаком: «+1.8» читается как «выше лиги», «−0.4» — как «ниже». */
function signed(value: number): string {
  const rounded = value.toFixed(1)
  if (rounded === '0.0' || rounded === '-0.0') return '0.0'
  return value > 0 ? `+${rounded}` : rounded.replace('-', '−')
}
