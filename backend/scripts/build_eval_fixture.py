"""Сборка фикстур ретривал-eval: `backend/evals/fixture_corpus.jsonl` и `golden.yaml`.

Живых эмбеддингов у CI нет, поэтому векторы синтетические, но детерминированные: мешок
слов через hashing trick в 768 измерений (размерность боевой колонки) с L2-нормировкой.
Запрос и отвечающий ему чанк делят словарь — их косинус выше, чем у запроса с чужими
чанками, и eval действительно ранжирует, а не раздаёт всем одинаковые баллы. Одна и та же
функция считает векторы и чанкам, и золотым запросам — иначе они жили бы в разных
пространствах.

Запускается вручную при правке корпуса или запросов:

    uv run python scripts/build_eval_fixture.py

Результат коммитится в git: backend/evals/.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Final

import yaml

BACKEND_ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = BACKEND_ROOT / "evals"

DIMENSIONS: Final = 768
# float32 держит ~7 значащих цифр — больше знаков в фикстуре было бы ложной точностью.
ROUND_DIGITS: Final = 6

RULEBOOK_TITLE: Final = "NBA Official Playing Rules"
RULEBOOK_URL: Final = "https://official.nba.com/rulebook/"


def embed_text(text: str) -> list[float]:
    """Hashing trick: каждый токен даёт ±1 в координату из своего sha256; сумма нормируется.

    Схема детерминирована и не требует словаря: одинаковые слова запроса и чанка попадают
    в одни и те же координаты с одним знаком — общий словарь и есть косинусная близость.
    """
    vector = [0.0] * DIMENSIONS
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4]) % DIMENSIONS
        sign = 1.0 if digest[4] % 2 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        raise ValueError(f"text has no tokens: {text!r}")
    return [round(value / norm, ROUND_DIGITS) for value in vector]


# Чанки rulebook: локатор в боевом формате ("Rule N — Sec. X") + текст в духе оригинала.
# Секции без золотого запроса — намеренные дистракторы: им тоже достаётся место в топе.
RULEBOOK_CHUNKS: Final[list[tuple[str, str]]] = [
    (
        "Rule 1 — Sec. I",
        "The playing court shall be measured 94 feet in length by 50 feet in width. The "
        "three-point field goal line is 23 feet 9 inches from the center of the basket, "
        "22 feet in the corners. The free throw line is 15 feet from the backboard.",
    ),
    (
        "Rule 3 — Sec. I",
        "Each team shall consist of five players on the court. No team may be reduced to "
        "fewer than five players; if a player receives his sixth personal foul and no "
        "substitute is available, he shall remain in the game.",
    ),
    (
        "Rule 4 — Sec. X",
        "A legal screen is set when the screener remains stationary with both feet on the "
        "floor before contact occurs. A moving screen, where the screener leans or shifts "
        "into the defender, is an illegal screen and an offensive foul.",
    ),
    (
        "Rule 5 — Sec. II",
        "Each team is entitled to seven charged timeouts during regulation play. Each "
        "timeout lasts 75 seconds. No team may be granted more than four timeouts in the "
        "fourth period and no more than two in the last three minutes.",
    ),
    (
        "Rule 5 — Sec. VII",
        "If the score is tied at the end of the fourth period, play shall resume with an "
        "overtime period of five minutes. As many overtime periods as necessary shall be "
        "played until a winner is decided.",
    ),
    (
        "Rule 6 — Sec. I",
        "The game shall be started with a jump ball in the center circle between any two "
        "opponents. The jump ball is tossed by the referee, and jumpers may tap the ball "
        "only after it reaches its highest point.",
    ),
    (
        "Rule 7 — Sec. II",
        "The shot clock shall be 24 seconds. The offensive team must attempt a field goal "
        "that strikes the rim before the shot clock expires. After an offensive rebound "
        "the shot clock is reset to 14 seconds rather than 24.",
    ),
    (
        "Rule 8 — Sec. II",
        "The ball is out of bounds when it touches a player who is out of bounds, the "
        "floor, or any object on or outside a boundary line. The ball is awarded to the "
        "opposing team at the nearest spot of the violation.",
    ),
    (
        "Rule 8 — Sec. III",
        "On a throw-in, the inbounding player must release the ball within five seconds "
        "of receiving it from the official. A five-second inbound violation results in a "
        "turnover and the ball awarded to the opponent at the same spot.",
    ),
    (
        "Rule 8 — Sec. X",
        "A team in possession in its backcourt must advance the ball across the midcourt "
        "line within eight seconds. Failure to advance past midcourt in eight seconds is "
        "a violation and the ball is awarded to the opposing team.",
    ),
    (
        "Rule 9 — Sec. I",
        "A free throw is the privilege given a player to score one point by an "
        "unhindered attempt from the free throw line. The shooter must release the ball "
        "within ten seconds and may not cross the line until the ball touches the rim.",
    ),
    (
        "Rule 10 — Sec. II",
        "A player shall not dribble a second time after his first dribble has ended, "
        "unless he loses control of the ball. Dribbling again after gathering the ball is "
        "a double dribble violation, also called an illegal dribble.",
    ),
    (
        "Rule 10 — Sec. IV",
        "A player shall not intentionally strike the ball with any part of the leg or "
        "foot. Kicking the ball intentionally is a kicked ball violation; the shot clock "
        "is reset to 14 seconds if fewer than 14 remain.",
    ),
    (
        "Rule 10 — Sec. VI",
        "An offensive player shall not remain in the free throw lane, known as the key or "
        "the paint, for more than three seconds while his team controls the ball. This "
        "offensive three-second violation results in a turnover.",
    ),
    (
        "Rule 10 — Sec. VII",
        "A defensive player shall not remain in the lane for more than three seconds "
        "unless he is actively guarding an opponent. The penalty for defensive three "
        "seconds is a technical foul free throw and continued possession.",
    ),
    (
        "Rule 10 — Sec. IX",
        "A player of the team in control in the frontcourt may not cause the ball to go "
        "into the backcourt and be the first to touch it. This over-and-back backcourt "
        "violation awards the ball to the opposing team.",
    ),
    (
        "Rule 10 — Sec. XIII",
        "A player who receives the ball while progressing or upon completion of a dribble "
        "may take two steps in coming to a stop, passing or shooting the ball. Taking "
        "more than two steps without dribbling is traveling.",
    ),
    (
        "Rule 11 — Sec. I",
        "Goaltending occurs when a player touches the ball during a field goal attempt "
        "while the ball is on its downward flight above the rim level. Touching the ball "
        "or the basket while the ball is on the rim is basket interference.",
    ),
    (
        "Rule 12 — Sec. I",
        "A technical foul is assessed for unsportsmanlike conduct, including "
        "disrespectfully addressing an official, taunting an opponent, or delay of game. "
        "Two technical fouls result in ejection from the game.",
    ),
    (
        "Rule 12 — Sec. II",
        "A flagrant foul is unnecessary contact committed against an opponent. Flagrant "
        "one is unnecessary contact; flagrant two is unnecessary and excessive contact "
        "and results in immediate ejection.",
    ),
    (
        "Rule 12 — Sec. III",
        "A clear path foul occurs when a personal foul is committed on an offensive "
        "player with the ball ahead of the defense in transition, denying a clear path to "
        "the basket. The penalty is two free throws and possession.",
    ),
    (
        "Rule 12 — Sec. VIII",
        "A player who commits his sixth personal foul shall be disqualified and must "
        "leave the game immediately. Six personal fouls is the disqualification limit for "
        "an individual player in regulation and overtime combined.",
    ),
    (
        "Rule 13 — Sec. I",
        "Instant replay review shall be triggered for made baskets at the end of a "
        "period, flagrant foul penalty determination, and out of bounds calls in the last "
        "two minutes. Officials review the play at the replay center.",
    ),
    (
        "Rule 14 — Sec. I",
        "Each team is entitled to one coach's challenge per game, with a second challenge "
        "awarded if the first is successful. A challenge may contest a called foul, an "
        "out of bounds violation, or goaltending.",
    ),
]

# ~20 wiki-статей: биографии и эпохи, по одному чанку-интро на статью. Локатор — "Intro",
# в цитате wiki-чанка он второстепенен: попадание в документ уже считается сигналом.
WIKI_ARTICLES: Final[list[tuple[str, str]]] = [
    (
        "Michael Jordan",
        "Michael Jordan is an American former professional basketball player who won six "
        "NBA championships with the Chicago Bulls, earning six Finals MVP awards. Widely "
        "regarded as the greatest player of all time, he won five regular season MVP "
        "awards and ten scoring titles as a shooting guard.",
    ),
    (
        "LeBron James",
        "LeBron James is an American professional basketball player who became the NBA "
        "all-time leading scorer, surpassing Kareem Abdul-Jabbar's career points record "
        "in 2023. A four-time champion with the Cavaliers, Heat and Lakers, he is a "
        "four-time MVP known for longevity and all-around play as a forward.",
    ),
    (
        "Kareem Abdul-Jabbar",
        "Kareem Abdul-Jabbar is an American former center famous for his unstoppable "
        "skyhook shot. He held the NBA all-time scoring record for nearly four decades, "
        "won six championships with the Milwaukee Bucks and Los Angeles Lakers, and a "
        "record six regular season MVP awards.",
    ),
    (
        "Stephen Curry",
        "Stephen Curry is an American point guard for the Golden State Warriors who "
        "revolutionized basketball with his three-point shooting range. The greatest "
        "shooter in NBA history, he holds the record for career three-pointers made, won "
        "four championships and was the first unanimous MVP.",
    ),
    (
        "Magic Johnson",
        "Magic Johnson is an American former point guard who led the Los Angeles Lakers "
        "to five championships during the Showtime era. As a rookie in 1980 he won "
        "Finals MVP playing center in the deciding game, and his no-look passing made "
        "him one of the greatest playmakers ever.",
    ),
    (
        "Larry Bird",
        "Larry Bird is an American former forward who won three championships with the "
        "Boston Celtics and three consecutive regular season MVP awards from 1984 to "
        "1986. Nicknamed the Hick from French Lick, his rivalry with Magic Johnson "
        "revived the league in the 1980s.",
    ),
    (
        "Bill Russell",
        "Bill Russell was an American center who won eleven NBA championships with the "
        "Boston Celtics, the most titles of any player in league history. A defensive "
        "anchor and shot blocker, he won five MVP awards and became the first Black head "
        "coach in major American professional sports.",
    ),
    (
        "Wilt Chamberlain",
        "Wilt Chamberlain was an American center who scored 100 points in a single game "
        "in 1962, the highest single game total in NBA history. He averaged 50 points "
        "per game over an entire season and holds dozens of scoring and rebounding "
        "records that still stand.",
    ),
    (
        "Shaquille O'Neal",
        "Shaquille O'Neal is an American former center, one of the most dominant players "
        "ever, who won three consecutive championships with the Los Angeles Lakers from "
        "2000 to 2002 alongside Kobe Bryant, earning three straight Finals MVP awards, "
        "and a fourth title with the Miami Heat.",
    ),
    (
        "Tim Duncan",
        "Tim Duncan is a former power forward who spent his entire career with the San "
        "Antonio Spurs, winning five championships. Nicknamed the Big Fundamental for "
        "his understated bank shot and footwork, he is widely considered the greatest "
        "power forward in NBA history.",
    ),
    (
        "Kobe Bryant",
        "Kobe Bryant was an American shooting guard who spent twenty seasons with the "
        "Los Angeles Lakers, winning five championships. Nicknamed the Black Mamba, he "
        "scored 81 points against the Toronto Raptors in 2006, the second highest single "
        "game total in NBA history.",
    ),
    (
        "Hakeem Olajuwon",
        "Hakeem Olajuwon is a Nigerian-American former center who led the Houston "
        "Rockets to back-to-back championships in 1994 and 1995. His footwork in the "
        "post, known as the Dream Shake, made him nearly unguardable, and he remains the "
        "NBA all-time leader in blocked shots.",
    ),
    (
        "Dirk Nowitzki",
        "Dirk Nowitzki is a German former power forward who spent 21 seasons with the "
        "Dallas Mavericks and led them to the 2011 championship over the Miami Heat. His "
        "signature one-legged fadeaway jumper made him the highest scoring European "
        "player in NBA history.",
    ),
    (
        "Giannis Antetokounmpo",
        "Giannis Antetokounmpo is a Greek professional forward for the Milwaukee Bucks, "
        "nicknamed the Greek Freak for his rare combination of size and speed. A "
        "two-time regular season MVP, he led the Bucks to the 2021 championship with a "
        "50-point closeout game in the Finals.",
    ),
    (
        "Nikola Jokic",
        "Nikola Jokic is a Serbian center for the Denver Nuggets, a three-time regular "
        "season MVP renowned for his passing and triple-doubles from the center "
        "position. Drafted 41st overall, he led Denver to its first championship in 2023 "
        "and won Finals MVP.",
    ),
    (
        "1998 NBA Finals",
        "The 1998 NBA Finals were won by the Chicago Bulls, who defeated the Utah Jazz "
        "four games to two for their sixth championship. Michael Jordan hit the "
        "series-winning shot over Bryon Russell in Game 6 and was named Finals MVP, "
        "completing the Bulls' second three-peat.",
    ),
    (
        "Showtime (basketball)",
        "Showtime was the nickname for the fast-break style of the Los Angeles Lakers "
        "during the 1980s, orchestrated by Magic Johnson and coached by Pat Riley. The "
        "up-tempo era produced five championships and made the Forum the entertainment "
        "capital of basketball.",
    ),
    (
        "Chicago Bulls",
        "The Chicago Bulls are an American professional basketball team that dominated "
        "the 1990s, winning six championships in two three-peats behind Michael Jordan "
        "and Scottie Pippen. Their 72-win season in 1995-96 stood as the league record "
        "for two decades.",
    ),
    (
        "Celtics-Lakers rivalry",
        "The Celtics-Lakers rivalry is the most storied in the NBA: the Boston Celtics "
        "and Los Angeles Lakers have met in the Finals twelve times, more than any other "
        "pair of teams. The rivalry peaked in the 1960s and again in the 1980s with Bird "
        "against Magic.",
    ),
    (
        "Three-point field goal",
        "The three-point field goal was adopted by the NBA for the 1979-80 season after "
        "originating in the ABA. Initially a novelty attempted a few times per game, the "
        "three-pointer became central to modern offenses, with league-wide attempts "
        "rising every decade since its introduction.",
    ),
]

# Золотые запросы: (запрос, источник-метка, ожидаемый документ, ожидаемый локатор).
# У rulebook-запросов локатор обязателен: документ там один, попадание в него тривиально.
# У wiki достаточно документа (см. комментарий к GoldenQuery в corpus/eval.py).
GOLDEN: Final[list[tuple[str, str, str, str | None]]] = [
    # --- rulebook: правила и нарушения ---
    ("what is goaltending", "rulebook", RULEBOOK_TITLE, "Rule 11 — Sec. I"),
    ("clear path foul definition", "rulebook", RULEBOOK_TITLE, "Rule 12 — Sec. III"),
    ("how long is the shot clock", "rulebook", RULEBOOK_TITLE, "Rule 7 — Sec. II"),
    (
        "when is the shot clock reset to 14 seconds after an offensive rebound",
        "rulebook",
        RULEBOOK_TITLE,
        "Rule 7 — Sec. II",
    ),
    (
        "how many seconds does a team have to advance the ball past midcourt",
        "rulebook",
        RULEBOOK_TITLE,
        "Rule 8 — Sec. X",
    ),
    ("what counts as traveling", "rulebook", RULEBOOK_TITLE, "Rule 10 — Sec. XIII"),
    ("double dribble violation rule", "rulebook", RULEBOOK_TITLE, "Rule 10 — Sec. II"),
    (
        "offensive three second violation in the lane",
        "rulebook",
        RULEBOOK_TITLE,
        "Rule 10 — Sec. VI",
    ),
    (
        "penalty for defensive three seconds",
        "rulebook",
        RULEBOOK_TITLE,
        "Rule 10 — Sec. VII",
    ),
    (
        "over and back backcourt violation",
        "rulebook",
        RULEBOOK_TITLE,
        "Rule 10 — Sec. IX",
    ),
    ("kicked ball violation", "rulebook", RULEBOOK_TITLE, "Rule 10 — Sec. IV"),
    ("what is a flagrant foul", "rulebook", RULEBOOK_TITLE, "Rule 12 — Sec. II"),
    (
        "technical foul for unsportsmanlike conduct",
        "rulebook",
        RULEBOOK_TITLE,
        "Rule 12 — Sec. I",
    ),
    (
        "how many personal fouls before a player is disqualified",
        "rulebook",
        RULEBOOK_TITLE,
        "Rule 12 — Sec. VIII",
    ),
    (
        "five seconds to release the ball on a throw-in",
        "rulebook",
        RULEBOOK_TITLE,
        "Rule 8 — Sec. III",
    ),
    ("what makes a screen legal", "rulebook", RULEBOOK_TITLE, "Rule 4 — Sec. X"),
    (
        "how many challenges does a coach get",
        "rulebook",
        RULEBOOK_TITLE,
        "Rule 14 — Sec. I",
    ),
    (
        "when do officials use instant replay review",
        "rulebook",
        RULEBOOK_TITLE,
        "Rule 13 — Sec. I",
    ),
    ("how long is an overtime period", "rulebook", RULEBOOK_TITLE, "Rule 5 — Sec. VII"),
    (
        "how many timeouts is each team entitled to",
        "rulebook",
        RULEBOOK_TITLE,
        "Rule 5 — Sec. II",
    ),
    # --- wikipedia: биографии и эпохи ---
    ("who won the 1998 finals", "wikipedia", "1998 NBA Finals", None),
    ("how many championships did Michael Jordan win", "wikipedia", "Michael Jordan", None),
    ("who is the NBA all-time leading scorer", "wikipedia", "LeBron James", None),
    (
        "which center was famous for the skyhook shot",
        "wikipedia",
        "Kareem Abdul-Jabbar",
        None,
    ),
    (
        "who revolutionized basketball with three-point shooting range",
        "wikipedia",
        "Stephen Curry",
        None,
    ),
    (
        "which rookie won Finals MVP playing center in 1980",
        "wikipedia",
        "Magic Johnson",
        None,
    ),
    (
        "who won three consecutive MVP awards with the Boston Celtics",
        "wikipedia",
        "Larry Bird",
        None,
    ),
    (
        "which player won eleven NBA championships",
        "wikipedia",
        "Bill Russell",
        None,
    ),
    ("who scored 100 points in a single game", "wikipedia", "Wilt Chamberlain", None),
    (
        "which dominant center won three straight Finals MVP awards from 2000 to 2002",
        "wikipedia",
        "Shaquille O'Neal",
        None,
    ),
    (
        "who is considered the greatest power forward with the San Antonio Spurs",
        "wikipedia",
        "Tim Duncan",
        None,
    ),
    (
        "which player nicknamed the Black Mamba scored 81 points",
        "wikipedia",
        "Kobe Bryant",
        None,
    ),
    (
        "whose post footwork was called the Dream Shake",
        "wikipedia",
        "Hakeem Olajuwon",
        None,
    ),
    (
        "which German forward with a one-legged fadeaway led the Mavericks to the 2011 title",
        "wikipedia",
        "Dirk Nowitzki",
        None,
    ),
    (
        "who is nicknamed the Greek Freak",
        "wikipedia",
        "Giannis Antetokounmpo",
        None,
    ),
    (
        "which Serbian center led the Denver Nuggets to their first championship",
        "wikipedia",
        "Nikola Jokic",
        None,
    ),
    (
        "what was the Showtime era of the Lakers",
        "wikipedia",
        "Showtime (basketball)",
        None,
    ),
    (
        "which team won 72 games in the 1995-96 season",
        "wikipedia",
        "Chicago Bulls",
        None,
    ),
    (
        "how many times have the Celtics and Lakers met in the Finals",
        "wikipedia",
        "Celtics-Lakers rivalry",
        None,
    ),
    (
        "when did the NBA adopt the three-point field goal",
        "wikipedia",
        "Three-point field goal",
        None,
    ),
]


def build_corpus_lines() -> list[dict[str, object]]:
    lines: list[dict[str, object]] = [
        {
            "source_type": "rulebook",
            "title": RULEBOOK_TITLE,
            "url": RULEBOOK_URL,
            "locator": locator,
            "text": text,
            "embedding": embed_text(text),
        }
        for locator, text in RULEBOOK_CHUNKS
    ]
    lines.extend(
        {
            "source_type": "wikipedia",
            "title": title,
            "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
            "locator": "Intro",
            "text": text,
            "embedding": embed_text(text),
        }
        for title, text in WIKI_ARTICLES
    )
    return lines


def build_golden_entries() -> list[dict[str, object]]:
    return [
        {
            "query": query,
            "source": source,
            "expect_document": document,
            "expect_locator": locator,
            "embedding": embed_text(query),
        }
        for query, source, document, locator in GOLDEN
    ]


def main() -> None:
    EVALS_DIR.mkdir(parents=True, exist_ok=True)

    corpus_lines = build_corpus_lines()
    corpus_path = EVALS_DIR / "fixture_corpus.jsonl"
    with corpus_path.open("w", encoding="utf-8") as handle:
        for line in corpus_lines:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")

    golden_entries = build_golden_entries()
    golden_path = EVALS_DIR / "golden.yaml"
    header = (
        "# Золотые запросы ретривал-eval. Файл генерируется скриптом\n"
        "# scripts/build_eval_fixture.py — править запросы там, не здесь.\n"
        "# embedding — синтетический вектор той же hashing-trick схемы, что и у чанков\n"
        "# fixture_corpus.jsonl; локальный режим (--eval) его игнорирует и считает живой.\n"
    )
    with golden_path.open("w", encoding="utf-8") as handle:
        handle.write(header)
        yaml.safe_dump(golden_entries, handle, allow_unicode=True, sort_keys=False, width=100)

    print(f"wrote {corpus_path} ({len(corpus_lines)} chunks)")
    print(f"wrote {golden_path} ({len(golden_entries)} queries)")


if __name__ == "__main__":
    main()
