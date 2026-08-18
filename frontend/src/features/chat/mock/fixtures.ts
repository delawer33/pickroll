import type { Citation, ToolCall } from '@/features/chat/model/types'

/** Мок-ответы Агента до появления настоящего SSE (#8).
 *
 * Числа и имена настоящие — заглушки «Lorem» и «Feature 1» запрещены (§3): мок должен
 * выглядеть ровно так, как будет выглядеть продукт, иначе вёрстка проверяется не на том.
 *
 * Фикстура выбирается по ключевым словам вопроса. Вопрос без фикстуры — не «пусто», а
 * ошибка потока: так состояние ошибки достижимо руками, без искусственного тумблера.
 */

export interface AnswerFixture {
  /** Ключевые слова в нижнем регистре; совпадение любого выбирает фикстуру. */
  keywords: string[]
  /** Вопрос, который предлагается в пустом состоянии. */
  suggestion: string
  toolCalls: ToolCall[]
  /** Текст ответа: разбивается на токены самим потоком. */
  answer: string
  citations: Citation[]
}

export const ANSWER_FIXTURES: AnswerFixture[] = [
  {
    keywords: ['jokic', 'jokić', 'passing'],
    suggestion: 'How has Jokić’s passing changed since 2023?',
    answer:
      'Volume is up, but the sharper change is in the risk profile. Assists per 100 rose from 13.7 to 15.9 while turnovers fell to 4.4 — the best assist-to-turnover ratio of his career[1]. The pass also got earlier: potential assists climbed to 25.8, so more of his creation happens before the defense is set[2].',
    toolCalls: [
      {
        id: 'call_stats_jokic',
        name: 'get_player_stats',
        source: 'Nikola Jokić · 3 seasons · per 100',
        durationMs: 211,
        result: {
          kind: 'table',
          columns: ['Season', 'Ast', 'Tov', 'Ast/Tov'],
          rows: [
            ['2022-23', '13.7', '5.1', '2.69'],
            ['2023-24', '14.4', '4.6', '3.13'],
            ['2024-25', '15.9', '4.4', '3.61'],
          ],
        },
      },
      {
        id: 'call_corpus_jokic',
        name: 'search_corpus',
        source: 'Basketball Reference · chunk 14',
        durationMs: 148,
        result: {
          kind: 'quote',
          text: '…increasingly initiated offense from the perimeter rather than the post, shortening the time between catch and pass.',
        },
      },
    ],
    citations: [
      { index: 1, toolCallId: 'call_stats_jokic' },
      { index: 2, toolCallId: 'call_corpus_jokic' },
    ],
  },
  {
    keywords: ['lebron', '2012-13'],
    suggestion: 'What did LeBron average in 2012-13?',
    answer:
      'LeBron averaged 26.8 points, 8.0 rebounds and 7.3 assists in 76 games for Miami, on 64.0 TS% — the most efficient scoring season of his career[1]. He shot 56.5 percent from the field, a mark he has not matched since.',
    toolCalls: [
      {
        id: 'call_stats_lebron',
        name: 'get_player_stats',
        source: 'LeBron James · 2012-13 · per game',
        durationMs: 174,
        result: {
          kind: 'table',
          columns: ['Season', 'GP', 'Pts', 'Reb', 'Ast', 'TS%'],
          rows: [['2012-13', '76', '26.8', '8.0', '7.3', '64.0']],
        },
      },
    ],
    citations: [{ index: 1, toolCallId: 'call_stats_lebron' }],
  },
  {
    keywords: ['wembanyama', 'rim protection'],
    suggestion: 'How good is Wembanyama’s rim protection?',
    answer:
      'Elite by volume and by result. He blocked 5.1 shots per 100 possessions, first among qualified centers, and opponents shot 8.4 points per 100 worse at the rim with him on the floor[1]. The weaker half of the profile is foul rate: 4.6 per 100 puts him in the bottom third[2].',
    toolCalls: [
      {
        id: 'call_percentiles_wemby',
        name: 'get_player_percentiles',
        source: '62 qualified centers · 2024-25',
        durationMs: 96,
        result: {
          kind: 'bars',
          bars: [
            { label: 'Blocks', percentile: 99 },
            { label: 'Rim defense', percentile: 94 },
            { label: 'Defensive reb', percentile: 78 },
            { label: 'Foul rate', percentile: 29 },
          ],
        },
      },
      {
        id: 'call_stats_wemby',
        name: 'get_player_stats',
        source: 'Victor Wembanyama · 2024-25 · per 100',
        durationMs: 133,
        result: {
          kind: 'table',
          columns: ['Season', 'Blk', 'Fouls', 'Def Reb'],
          rows: [['2024-25', '5.1', '4.6', '11.9']],
        },
      },
    ],
    citations: [
      { index: 1, toolCallId: 'call_percentiles_wemby' },
      { index: 2, toolCallId: 'call_stats_wemby' },
    ],
  },
]

export function findFixture(question: string): AnswerFixture | undefined {
  const normalized = question.toLowerCase()
  return ANSWER_FIXTURES.find((fixture) =>
    fixture.keywords.some((keyword) => normalized.includes(keyword)),
  )
}

/** Диалоги в левой рейке: до персистентности (#8) история тоже мок. */
export interface ConversationSummary {
  id: string
  title: string
  when: string
}

export const MOCK_CONVERSATIONS: ConversationSummary[] = [
  { id: 'jokic-passing', title: 'Jokić’s passing since 2023', when: 'Today' },
  { id: 'lebron-2012-13', title: 'LeBron in 2012-13', when: 'Yesterday' },
  { id: 'wemby-rim', title: 'Wembanyama at the rim', when: 'Mar 20' },
]

/** Вопрос, с которого начинался сохранённый Диалог. */
export const CONVERSATION_QUESTIONS: Record<string, string> = {
  'jokic-passing': 'How has Jokić’s passing changed since 2023?',
  'lebron-2012-13': 'What did LeBron average in 2012-13?',
  'wemby-rim': 'How good is Wembanyama’s rim protection?',
}
