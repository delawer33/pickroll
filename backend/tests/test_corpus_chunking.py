"""Структурный чанкинг: локаторы и границы чанков на фикстурных текстах источников.

Чистые юнит-тесты: rulebook представлен текстом страниц, как его извлекает pypdf,
Wikipedia — wikitext'ом, как его отдаёт REST API.
"""

from __future__ import annotations

from court_vision.corpus.chunking import (
    LEAD_LOCATOR,
    MAX_CHUNK_TOKENS,
    chunk_document,
)
from court_vision.corpus.sources import RawDocument

# Текст двух страниц rulebook в том виде, в котором его отдаёт pypdf: колонтитулы
# страниц («- 9 -», «Official Rules») перемешаны с содержимым.
RULEBOOK_TEXT = """\
- 8 -
Official Rules
TABLE OF CONTENTS
RULE NO. 1: COURT DIMENSIONS
- 9 -
Official Rules
RULE NO. 1—COURT DIMENSIONS—EQUIPMENT
Section I—Court and Dimensions
a. The playing court shall be measured and marked as shown in the court diagram.
b. A free throw lane shall be marked at each end of the court.
Section II—Equipment
a. The backboard shall be a rectangle measuring 6' horizontally.
- 10 -
Official Rules
RULE NO. 2—OFFICIALS AND THEIR DUTIES
Section I—The Officials
a. The game officials shall be a crew chief and two referees.
"""


def rulebook_document(text: str = RULEBOOK_TEXT) -> RawDocument:
    return RawDocument(source_type="rulebook", title="NBA Rules", url="https://x", text=text)


def wikipedia_document(text: str) -> RawDocument:
    return RawDocument(source_type="wikipedia", title="LeBron James", url="https://w", text=text)


class TestRulebookChunking:
    def test_locators_follow_rule_and_section(self) -> None:
        chunks = chunk_document(rulebook_document())

        assert [chunk.locator for chunk in chunks] == [
            "Rule 1 — Sec. I",
            "Rule 1 — Sec. II",
            "Rule 2 — Sec. I",
        ]

    def test_section_bodies_land_in_their_chunks(self) -> None:
        chunks = chunk_document(rulebook_document())

        assert "free throw lane" in chunks[0].text
        assert "backboard" in chunks[1].text
        assert "crew chief" in chunks[2].text

    def test_page_artifacts_and_front_matter_dropped(self) -> None:
        chunks = chunk_document(rulebook_document())

        joined = "\n".join(chunk.text for chunk in chunks)
        assert "Official Rules" not in joined
        assert "- 9 -" not in joined
        assert "TABLE OF CONTENTS" not in joined

    def test_embed_text_carries_title_and_locator(self) -> None:
        chunk = chunk_document(rulebook_document())[0]

        assert chunk.embed_text.startswith("NBA Rules > Rule 1 — Sec. I\n")
        assert chunk.text in chunk.embed_text

    def test_oversized_section_splits_at_line_boundaries(self) -> None:
        lines = [f"item {index}: " + " ".join(["word"] * 30) for index in range(40)]
        text = "RULE NO. 3—PLAYERS AND SUBSTITUTES\nSection I—Team\n" + "\n".join(lines) + "\n"
        budget = MAX_CHUNK_TOKENS * 4

        chunks = chunk_document(rulebook_document(text))

        assert len(chunks) > 1
        assert all(chunk.locator == "Rule 3 — Sec. I" for chunk in chunks)
        assert all(len(chunk.text) <= budget for chunk in chunks)
        # Ни одна строка не разрезана пополам: каждая уцелела в каком-то чанке.
        joined = "\n".join(chunk.text for chunk in chunks)
        assert all(line in joined for line in lines)


WIKITEXT = """\
{{Short description|American basketball player}}
{{Infobox basketball biography
| name = LeBron James
| height_ft = {{convert|6|ft|9|in}}
}}
'''LeBron Raymone James''' is an American professional [[basketball]] player for the
[[Los Angeles Lakers]] of the [[National Basketball Association|NBA]].
<ref name="espn">ESPN profile</ref>

== Playing career ==
James was selected first overall in the 2003 draft.

=== Cleveland Cavaliers ===
James spent his first seven seasons with the Cavaliers.
{| class="wikitable"
! Season !! PPG
|-
| 2003–04 || 20.9
|}

=== Chicago Bulls ===
A hypothetical stint used by the design doc as a locator example.

== Records ==
James is the all-time leading scorer in NBA history.<ref>NBA.com</ref>

== References ==
{{Reflist}}

== External links ==
* [https://nba.com official site]
"""


class TestWikipediaChunking:
    def test_locators_are_section_paths(self) -> None:
        chunks = chunk_document(wikipedia_document(WIKITEXT))

        assert [chunk.locator for chunk in chunks] == [
            LEAD_LOCATOR,
            "Playing career",
            "Playing career > Cleveland Cavaliers",
            "Playing career > Chicago Bulls",
            "Records",
        ]

    def test_infobox_tables_and_refs_dropped(self) -> None:
        chunks = chunk_document(wikipedia_document(WIKITEXT))

        joined = "\n".join(chunk.text for chunk in chunks)
        assert "Infobox" not in joined
        assert "height_ft" not in joined
        assert "wikitable" not in joined
        assert "20.9" not in joined
        assert "ESPN profile" not in joined

    def test_link_markup_resolves_to_labels(self) -> None:
        lead = chunk_document(wikipedia_document(WIKITEXT))[0]

        assert "LeBron Raymone James" in lead.text
        assert "the Los Angeles Lakers of the NBA" in " ".join(lead.text.split())
        assert "[[" not in lead.text
        assert "'''" not in lead.text

    def test_reference_sections_skipped(self) -> None:
        chunks = chunk_document(wikipedia_document(WIKITEXT))

        locators = [chunk.locator for chunk in chunks]
        assert "References" not in locators
        assert "External links" not in locators
        assert all("nba.com official site" not in chunk.text for chunk in chunks)
