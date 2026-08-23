import type { Citation, ToolCall } from '@/features/chat/model/types'

/** Ответы Агента для тестов ленты и карточек Evidence.
 *
 * Числа и имена настоящие — заглушки «Lorem» и «Feature 1» запрещены (§3): фикстура должна
 * выглядеть ровно так, как выглядит продукт, иначе вёрстка проверяется не на том. Здесь же
 * лежат разнородные результаты Инструментов (таблица, динамика, перцентили, цитата),
 * которых поток спринта 1 ещё не присылает: рейка Evidence обязана их держать к спринтам
 * 2-3, и проверяется это на них.
 *
 * Фикстура выбирается по ключевым словам вопроса. Вопрос без фикстуры — не «пусто», а
 * ошибка потока: так состояние ошибки достижимо без искусственного тумблера.
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

/** Длинный ответ на четырёх Инструментах: таблица, динамика по сезонам, перцентили и
 * цитата из корпуса. Он же — проверка того, что рейка Evidence держит разнородные
 * результаты подряд, а лента не разъезжается на длинном тексте. */
const SHOOTING_COMPARISON: AnswerFixture = {
  keywords: ['curry', 'lillard', 'shooter', 'shooting'],
  suggestion: 'Compare Curry and Lillard as shooters since 2021',
  answer:
    'Both are volume shooters off the dribble, but the last four seasons separated them on efficiency rather than on volume. Curry has taken more threes in every season of the span and converted at a higher clip in three of the four[1]. The gap is widest where it matters most for a defense: pull-up threes, where Curry stayed above league-average efficiency in each season while Lillard dipped below it twice[2].\n\nThe shapes of the two curves differ as well. Curry’s attempt rate is flat and his percentage moves; Lillard’s attempt rate fell after 2022-23 while his percentage recovered, which reads as a player taking fewer of the hardest looks rather than as one who got better at them[2]. Against qualified guards, Curry sits in the 99th percentile on three-point volume and the 96th on efficiency, with the weakest part of his profile being rim finishing at the 34th[3].\n\nThe scouting read is that neither is declining as a shooter, but Lillard’s creation load has shifted toward the mid-range and to the free-throw line, and his gravity beyond the arc is now closer to elite-plus than to unique[4].',
  toolCalls: [
    {
      id: 'call_stats_shooters',
      name: 'get_player_stats',
      source: 'Curry, Lillard · 4 seasons · per game',
      durationMs: 243,
      result: {
        kind: 'table',
        columns: ['Player', 'Season', 'GP', '3PA', '3P%', 'TS%', 'Pts'],
        rows: [
          ['S. Curry', '2021-22', '64', '11.7', '38.0', '59.3', '25.5'],
          ['S. Curry', '2022-23', '56', '11.4', '42.7', '65.6', '29.4'],
          ['S. Curry', '2023-24', '74', '11.8', '40.8', '62.4', '26.4'],
          ['S. Curry', '2024-25', '70', '9.9', '39.7', '61.0', '24.5'],
          ['D. Lillard', '2021-22', '29', '9.7', '32.4', '54.0', '24.0'],
          ['D. Lillard', '2022-23', '58', '11.3', '37.1', '64.5', '32.2'],
          ['D. Lillard', '2023-24', '73', '8.4', '35.4', '58.6', '24.3'],
          ['D. Lillard', '2024-25', '58', '8.6', '37.6', '60.1', '24.9'],
        ],
      },
    },
    {
      id: 'call_series_shooters',
      name: 'compare_players',
      source: 'three-point attempts · per game',
      durationMs: 318,
      result: {
        kind: 'series',
        metric: 'Three-point attempts',
        unit: 'per game',
        series: [
          {
            name: 'Curry',
            points: [
              { x: '2021-22', y: 11.7 },
              { x: '2022-23', y: 11.4 },
              { x: '2023-24', y: 11.8 },
              { x: '2024-25', y: 9.9 },
            ],
          },
          {
            name: 'Lillard',
            points: [
              { x: '2021-22', y: 9.7 },
              { x: '2022-23', y: 11.3 },
              { x: '2023-24', y: 8.4 },
              { x: '2024-25', y: 8.6 },
            ],
          },
        ],
      },
    },
    {
      id: 'call_percentiles_shooters',
      name: 'get_player_percentiles',
      source: 'Stephen Curry · 84 qualified guards',
      durationMs: 104,
      result: {
        kind: 'bars',
        bars: [
          { label: '3P volume', percentile: 99 },
          { label: '3P efficiency', percentile: 96 },
          { label: 'Pull-up 3P', percentile: 98 },
          { label: 'Free throws', percentile: 71 },
          { label: 'Rim finishing', percentile: 34 },
        ],
      },
    },
    {
      id: 'call_corpus_shooters',
      name: 'search_corpus',
      source: 'Scouting notes · chunk 41',
      durationMs: 167,
      result: {
        kind: 'quote',
        text: '“…defenses still pick up Curry near half court, while Lillard is increasingly met at the arc — the difference in coverage says more about respected range than any percentage does.”',
      },
    },
  ],
  citations: [
    { index: 1, toolCallId: 'call_stats_shooters' },
    { index: 2, toolCallId: 'call_series_shooters' },
    { index: 3, toolCallId: 'call_percentiles_shooters' },
    { index: 4, toolCallId: 'call_corpus_shooters' },
  ],
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
  SHOOTING_COMPARISON,
]

export function findFixture(question: string): AnswerFixture | undefined {
  const normalized = question.toLowerCase()
  return ANSWER_FIXTURES.find((fixture) =>
    fixture.keywords.some((keyword) => normalized.includes(keyword)),
  )
}
