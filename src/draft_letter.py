"""
draft_letter.py — writes the weekly letter using the Gemini API.

News comes from data/news.md (built by fetch_news.py from public RSS
feeds) rather than from search grounding, which is not available on the
Gemini free tier.

The "Article of the Week" section is manual: put your pick in
input/article.md and it gets appended verbatim.

Requires: GEMINI_API_KEY in the environment.
Output: letters/YYYY-MM-DD.md
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from google import genai

MODEL = os.environ.get("LETTER_MODEL", "models/gemini-3.6-flash")

CONTEXT = Path("data/context.md")
SNAPSHOT = Path("data/latest.json")
NEWS = Path("data/news.md")
BRIEF = Path("prompts/letter_template.md")
ARTICLE = Path("input/article.md")
OUT_DIR = Path("letters")

PLACEHOLDER = """## 4. Article of the Week

_To be added._
"""


def build_input(brief: str, week_label: str, context: str, news: str) -> str:
    """The brief, the news briefing and the market data go in one input."""
    return f"""{brief}

---

Draft the weekly market letter for the week of {week_label}.

Below are headlines collected from public news feeds covering this period,
followed by the market data. Use the headlines to identify what actually
drove markets and what is scheduled for the coming week. Treat them as leads,
not verified facts: state something as fact only if several headlines agree,
and frame anything uncertain as an expectation. Prioritise European coverage.

{news}

---

Market data. Section 2 must use these figures and only these:

{context}

---

Each line in section 2 is one sentence: the figure, then what moved it.
Vary the verbs across lines. Do not append a comment about the 52-week
range unless the writing rules above require it.

Write sections 1, 2 and 3. Stop after section 3 - the Article of the Week is
added separately. Return only the letter in Markdown, with no preamble."""


def extract_text(interaction) -> str:
    """
    Pull the letter out of the response. The SDK's response shape varies
    between versions, so try the known accessors in order of preference.
    """
    for attr in ("output_text", "text"):
        value = getattr(interaction, attr, None)
        if isinstance(value, str) and value.strip():
            return value

    steps = getattr(interaction, "steps", None) or []
    chunks: list[str] = []
    for step in steps:
        text = getattr(step, "text", None)
        if isinstance(text, str) and text.strip():
            chunks.append(text)
            continue
        content = getattr(step, "content", None)
        if isinstance(content, str) and content.strip():
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and part_text.strip():
                    chunks.append(part_text)

    return "\n".join(chunks).strip()


def main() -> None:
    if not NEWS.exists():
        print("  ! data/news.md missing - run fetch_news.py for better output")

    for path in (CONTEXT, SNAPSHOT, BRIEF):
        if not path.exists():
            sys.exit(f"Missing {path} — run the earlier pipeline steps first.")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY is not set.")

    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    week_label = snapshot["week_label"]
    context = CONTEXT.read_text(encoding="utf-8")
    news = NEWS.read_text(encoding="utf-8") if NEWS.exists() else "(No news briefing available.)"
    brief = BRIEF.read_text(encoding="utf-8").replace("{WEEK_LABEL}", week_label)

    client = genai.Client(api_key=api_key)
    print(f"Drafting letter for week of {week_label} using {MODEL}...")

    interaction = None
    last_error = None
    for attempt in range(1, 4):
        try:
            interaction = client.interactions.create(
                model=MODEL,
                input=build_input(brief, week_label, context, news),
                generation_config={
                    "temperature": 0.7,
                    "max_output_tokens": 8000,
                    "top_p": 0.95,
                },
            )
            break
        except Exception as exc:
            last_error = exc
            print(f"  attempt {attempt}/3 failed: {type(exc).__name__}")
            if attempt < 3:
                time.sleep(20 * attempt)

    if interaction is None:
        sys.exit(f"Gemini API call failed after 3 attempts: {last_error}")

    letter = extract_text(interaction)

    if not letter:
        # Response shape wasn't what we expected — dump it so we can adapt.
        Path("data/raw_response.txt").write_text(repr(interaction), encoding="utf-8")
        sys.exit(
            "Could not extract text from the response. "
            "Raw object written to data/raw_response.txt — inspect it and "
            "adjust extract_text()."
        )

    # Strip stray code fences if the model wraps its output
    if letter.startswith("```"):
        letter = letter.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    # Manual section
    article_text = ARTICLE.read_text(encoding="utf-8").strip() if ARTICLE.exists() else ""
    if article_text:
        letter += "\n\n" + article_text + "\n"

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / f"{snapshot['week_end']}.md"
    out_path.write_text(letter, encoding="utf-8")

    print(f"Wrote {out_path} — {len(letter.split())} words")


if __name__ == "__main__":
    main()