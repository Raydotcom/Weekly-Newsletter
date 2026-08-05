"""
draft_letter.py — writes the weekly letter.

Feeds the market snapshot plus the drafting brief to the model, with web
search enabled so it can research the week's actual events rather than
relying on training data.

The "Article of the Week" section is manual: put your pick in
input/article.md and it gets appended verbatim. If that file is absent,
a placeholder is written so the letter is still complete in structure.

Requires: ANTHROPIC_API_KEY in the environment.
Output: letters/YYYY-MM-DD.md
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

from anthropic import Anthropic

MODEL = os.environ.get("LETTER_MODEL", "claude-sonnet-5")
MAX_TOKENS = 8000
SEARCH_BUDGET = 8  # max web searches per run

CONTEXT = Path("data/context.md")
SNAPSHOT = Path("data/latest.json")
BRIEF = Path("prompts/letter_template.md")
ARTICLE = Path("input/article.md")
OUT_DIR = Path("letters")

PLACEHOLDER = """## 4. Article of the Week

_To be added._
"""


def build_user_message(week_label: str, context: str) -> str:
    return f"""Draft the weekly market letter for the week of {week_label}.

Before writing, research what actually happened in markets during this week.
Search for: European equity market drivers, ECB policy and euro area rates,
major US large-cap and AI-sector developments, and the economic calendar for
the coming week. Prioritise European coverage.

Here is the market data. Section 2 must use these figures and only these:

{context}

Write sections 1, 2 and 3. Stop after section 3 — the Article of the Week is
added separately."""


def main() -> None:
    for path in (CONTEXT, SNAPSHOT, BRIEF):
        if not path.exists():
            sys.exit(f"Missing {path} — run the earlier pipeline steps first.")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set.")

    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    week_label = snapshot["week_label"]
    context = CONTEXT.read_text(encoding="utf-8")
    brief = BRIEF.read_text(encoding="utf-8").replace("{WEEK_LABEL}", week_label)

    client = Anthropic()
    print(f"Drafting letter for week of {week_label} using {MODEL}...")

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=brief,
        messages=[{"role": "user", "content": build_user_message(week_label, context)}],
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": SEARCH_BUDGET,
        }],
    )

    letter = "\n".join(
        block.text for block in response.content if block.type == "text"
    ).strip()

    if not letter:
        sys.exit("Model returned no text. Check the API response.")

    # Manual section
    if ARTICLE.exists() and ARTICLE.read_text(encoding="utf-8").strip():
        letter += "\n\n" + ARTICLE.read_text(encoding="utf-8").strip() + "\n"
    else:
        letter += "\n\n" + PLACEHOLDER
        print("  ! input/article.md is empty or missing — placeholder inserted")

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / f"{date.today().isoformat()}.md"
    out_path.write_text(letter, encoding="utf-8")

    searches = sum(
        1 for b in response.content if getattr(b, "type", "") == "server_tool_use"
    )
    print(f"Wrote {out_path} — {len(letter.split())} words, {searches} searches used")


if __name__ == "__main__":
    main()