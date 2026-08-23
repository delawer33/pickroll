/** Вопросы, предлагаемые на пустом экране.
 *
 * Не украшение: они задают планку того, на что Агент отвечает сегодня — сезонные цифры из
 * Box Score с 2005-06. Вопрос вне этих данных Агент честно отклоняет, и предлагать такой
 * на пустом экране значит учить пользователя не тому.
 */
export const SUGGESTED_QUESTIONS = [
  'How many points did LeBron James average in 2012-13?',
  "What were Stephen Curry's numbers in 2015-16?",
  'Compare LeBron James and Kevin Durant in 2013-14',
] as const
