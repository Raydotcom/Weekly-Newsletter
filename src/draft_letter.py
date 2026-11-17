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
# Optional second model tried when the main one is rate-limited/overloaded.
FALLBACK_MODEL = os.environ.get("LETTER_FALLBACK_MODEL", "").strip()

MAX_ATTEMPTS = 5
BACKOFF_SECONDS = [30, 90, 180, 300]   # waits between attempts
MIN_WORDS = 500                        # a full letter is ~850-1000 words

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

Section 2 uses this exact line format, one markdown bullet per instrument:

- Instrument name: +0.00% — what moved it.

Keep the leading hyphen, the instrument name exactly as written in the
tables above, the colon straight after the name, and the figure
immediately after the colon. One sentence per line, and vary the verbs
across lines. Do not append a comment about the 52-week range unless the
writing rules above require it.

Write sections 1, 2 and 3. Stop after section 3 - the Article of the Week is
added separately. Return only the letter in Markdown, with no preamble."""


def _get(obj, key):
    """Read a field from either an SDK object or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def extract_text(interaction) -> str:
    """
    Pull the full letter out of the response.

    The SDK's `output_text` only keeps the *trailing* block of model text.
    Since late August 2026, gemini-3.6-flash interleaves thought steps
    between its output chunks, so `output_text` came back with only the
    last section (~220 words, zero figures) and validation failed every
    week. We therefore stitch together every model_output text block
    ourselves and only fall back to `output_text` if that yields nothing.
    """
    steps = _get(interaction, "steps") or []
    chunks: list[str] = []
    for step in steps:
        if _get(step, "type") != "model_output":
            continue  # skip user_input, thought, tool steps
        content = _get(step, "content")
        if isinstance(content, str) and content.strip():
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                if _get(part, "type") not in (None, "text"):
                    continue
                text = _get(part, "text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text)

    stitched = "".join(chunks).strip()
    if stitched:
        return stitched

    for attr in ("output_text", "text"):
        value = _get(interaction, attr)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def looks_complete(letter: str) -> bool:
    """Sections 1-3 must all be there and the letter must have real length."""
    headings = [l for l in letter.splitlines() if l.lstrip().startswith("## ")]
    return len(letter.split()) >= MIN_WORDS and len(headings) >= 3


def is_retryable(exc: Exception) -> bool:
    msg = f"{type(exc).__name__} {exc}"
    return any(k in msg for k in ("429", "503", "500", "RateLimit",
                                   "InternalServer", "ServiceUnavailable",
                                   "high demand", "timed out", "Timeout"))


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
    prompt = build_input(brief, week_label, context, news)
    models = [MODEL] + ([FALLBACK_MODEL] if FALLBACK_MODEL else [])

    letter = ""
    interaction = None
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        # Switch to the fallback model for the second half of the attempts
        model = models[-1] if attempt > (MAX_ATTEMPTS + 1) // 2 else models[0]
        print(f"Drafting letter for week of {week_label} using {model} "
              f"(attempt {attempt}/{MAX_ATTEMPTS})...")
        try:
            interaction = client.interactions.create(
                model=model,
                input=prompt,
                generation_config={
                    "temperature": 0.7,
                    "max_output_tokens": 16000,  # thinking tokens count too
                    "top_p": 0.95,
                },
            )
            letter = extract_text(interaction)
            if looks_complete(letter):
                break
            print(f"  incomplete draft ({len(letter.split())} words) — retrying")
            last_error = RuntimeError(f"incomplete draft: {len(letter.split())} words")
        except Exception as exc:
            last_error = exc
            print(f"  failed: {type(exc).__name__}: {str(exc)[:200]}")
            if not is_retryable(exc):
                break
        if attempt < MAX_ATTEMPTS:
            time.sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])

    if interaction is not None:
        # Always keep the raw response for debugging (uploaded on failure).
        Path("data/raw_response.txt").write_text(repr(interaction), encoding="utf-8")

    if not looks_complete(letter):
        if letter:
            OUT_DIR.mkdir(exist_ok=True)
            (OUT_DIR / f"{snapshot['week_end']}.md").write_text(letter, encoding="utf-8")
        sys.exit(f"No complete letter after {MAX_ATTEMPTS} attempts: {last_error}")

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
