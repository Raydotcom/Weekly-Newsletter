"""
render_html.py — converts the latest Markdown letter into a standalone,
styled HTML page.

Markdown stays the source of truth; this is a rendered view for sharing
(LinkedIn, email, GitHub Pages).

Output: docs/YYYY-MM-DD.html and docs/index.html
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import markdown

LETTERS = Path("letters")
OUT_DIR = Path("docs")

STYLE = """
:root {
  --ink: #1a1a1a;
  --muted: #6b6b6b;
  --rule: #e2e2e2;
  --accent: #1f4e79;
  --bg: #ffffff;
}
* { box-sizing: border-box; }
body {
  font-family: Georgia, 'Times New Roman', serif;
  line-height: 1.65;
  color: var(--ink);
  background: var(--bg);
  max-width: 720px;
  margin: 0 auto;
  padding: 3rem 1.5rem 5rem;
}
h1 {
  font-size: 2rem;
  letter-spacing: -0.01em;
  margin: 0 0 0.25rem;
  border-bottom: 2px solid var(--accent);
  padding-bottom: 0.6rem;
}
h2 {
  font-size: 1.25rem;
  margin: 2.5rem 0 0.75rem;
  color: var(--accent);
}
h3 {
  font-size: 1rem;
  font-weight: normal;
  color: var(--muted);
  margin: 0 0 2rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
p { margin: 0 0 1rem; }
ol, ul { padding-left: 1.3rem; }
li { margin-bottom: 0.85rem; }
strong { color: var(--ink); }
a { color: var(--accent); }
table {
  border-collapse: collapse;
  width: 100%;
  font-family: -apple-system, 'Segoe UI', sans-serif;
  font-size: 0.85rem;
  margin: 1.5rem 0;
}
th, td {
  border-bottom: 1px solid var(--rule);
  padding: 0.45rem 0.6rem;
  text-align: right;
}
th:first-child, td:first-child { text-align: left; }
th { background: #f7f7f7; font-weight: 600; }
footer {
  margin-top: 4rem;
  padding-top: 1rem;
  border-top: 1px solid var(--rule);
  color: var(--muted);
  font-size: 0.8rem;
  font-family: -apple-system, 'Segoe UI', sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ink: #e8e8e8; --muted: #9a9a9a; --rule: #333;
    --accent: #7ab3e0; --bg: #141414;
  }
  th { background: #1f1f1f; }
}
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{style}</style>
</head>
<body>
{body}
<footer>Generated automatically from market data. Written by Rayan.</footer>
</body>
</html>
"""


def latest_letter() -> Path:
    files = sorted(LETTERS.glob("*.md"))
    if not files:
        sys.exit("No letters found in letters/")
    return files[-1]


def main() -> None:
    src = latest_letter()
    text = src.read_text(encoding="utf-8")

    body = markdown.markdown(text, extensions=["tables", "sane_lists"])
    title = f"Weekly Market Letter — {src.stem}"
    html = PAGE.format(title=title, style=STYLE, body=body)

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"{src.stem}.html").write_text(html, encoding="utf-8")
    (OUT_DIR / "index.html").write_text(html, encoding="utf-8")

    print(f"Rendered {src.name} -> docs/{src.stem}.html and docs/index.html")


if __name__ == "__main__":
    main()