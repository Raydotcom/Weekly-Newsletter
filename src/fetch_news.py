"""
fetch_news.py — collects the week's financial news headlines via RSS.

Replaces search grounding: free, no API key, no quota. Pulls topic-specific
queries from Google News RSS plus the ECB's own press feed, filters to the
letter's week window, dedupes, and writes a compact briefing the drafting
step feeds to the model.

Output: data/news.md
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

import requests

SNAPSHOT = Path("data/latest.json")
OUT = Path("data/news.md")

HEADERS = {"User-Agent": "Mozilla/5.0 (weekly-newsletter-bot)"}
MAX_PER_TOPIC = 8

# Google News RSS accepts a search query. `when:14d` keeps the window tight
# so we aren't parsing months of irrelevant history.
GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=en-GB&gl=GB&ceid=GB:en"

TOPICS: dict[str, str] = {
    "European equities": "European stock markets Stoxx DAX CAC when:14d",
    "ECB and euro area rates": "ECB monetary policy euro area inflation when:14d",
    "European banks": "European banks earnings BNP Deutsche Bank when:14d",
    "US markets": "US stock market S&P 500 Federal Reserve when:14d",
    "AI and big tech": "Nvidia Microsoft Alphabet Amazon AI earnings when:14d",
    "Commodities and FX": "oil price Brent gold euro dollar when:14d",
    "Week ahead": "market week ahead economic calendar earnings preview",
}

# The ECB's own feed — primary source, worth having separately
ECB_FEED = "https://www.ecb.europa.eu/rss/press.html"


def clean(text: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(url: str, limit: int) -> list[dict]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
    except Exception as exc:
        print(f"  ! feed failed ({url[:60]}...): {exc}", file=sys.stderr)
        return []

    items = []
    # Handles both RSS <item> and Atom <entry>
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue

        title = source = published = ""
        for child in node:
            ctag = child.tag.split("}")[-1]
            if ctag == "title":
                title = clean(child.text or "")
            elif ctag == "source":
                source = clean(child.text or "")
            elif ctag in ("pubDate", "published", "date"):
                published = (child.text or "").strip()

        if not title:
            continue

        when = None
        if published:
            try:
                when = parsedate_to_datetime(published)
            except Exception:
                try:
                    when = datetime.fromisoformat(published.replace("Z", "+00:00"))
                except Exception:
                    when = None
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)

        items.append({"title": title, "source": source, "when": when})
        if len(items) >= limit * 3:   # over-collect, filtered below
            break

    return items


def in_window(item: dict, start: datetime, end: datetime) -> bool:
    if item["when"] is None:
        return True          # undated items are kept; the model can judge
    return start <= item["when"] <= end


def main() -> None:
    if not SNAPSHOT.exists():
        sys.exit("Missing data/latest.json — run fetch_data.py first.")

    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    week_start = datetime.fromisoformat(snap["week_start"]).replace(tzinfo=timezone.utc)
    week_end = datetime.fromisoformat(snap["week_end"]).replace(tzinfo=timezone.utc)

    # The letter also looks forward, so allow headlines up to today
    collect_from = week_start - timedelta(days=1)
    collect_to = datetime.now(timezone.utc)

    parts = [
        f"# News briefing — week of {snap['week_label']}",
        "_Headlines collected from public RSS feeds. Verify anything you state "
        "as fact; headlines can be misleading in isolation._",
        "",
    ]

    seen: set[str] = set()
    total = 0

    for topic, query in TOPICS.items():
        url = GOOGLE_NEWS.format(q=urllib.parse.quote(query))
        items = parse_feed(url, MAX_PER_TOPIC)
        rows = []
        for item in items:
            key = item["title"].lower()[:70]
            if key in seen:
                continue
            if not in_window(item, collect_from, collect_to):
                continue
            seen.add(key)
            stamp = item["when"].strftime("%d %b") if item["when"] else "undated"
            src = f" ({item['source']})" if item["source"] else ""
            rows.append(f"- [{stamp}] {item['title']}{src}")
            if len(rows) >= MAX_PER_TOPIC:
                break

        if rows:
            parts += [f"## {topic}", *rows, ""]
            total += len(rows)

    ecb_items = parse_feed(ECB_FEED, 10)
    ecb_rows = []
    for item in ecb_items:
        if not in_window(item, collect_from, collect_to):
            continue
        stamp = item["when"].strftime("%d %b") if item["when"] else "undated"
        ecb_rows.append(f"- [{stamp}] {item['title']}")
        if len(ecb_rows) >= 10:
            break
    if ecb_rows:
        parts += ["## ECB press releases (primary source)", *ecb_rows, ""]
        total += len(ecb_rows)

    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {OUT} — {total} headlines across {len(TOPICS) + 1} topics")

    if total == 0:
        print("  ! no headlines collected — check network access", file=sys.stderr)


if __name__ == "__main__":
    main()