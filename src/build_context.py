"""
build_context.py — turns data/latest.json into a compact markdown block
that the drafting step feeds to the model.

Keeps the payload small and readable: tables instead of raw JSON, and a
short "notable moves" section that pre-identifies the outliers so the
model doesn't have to hunt for them.

Output: data/context.md
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path("data/latest.json")
OUT = Path("data/context.md")

# A move beyond these thresholds gets flagged as notable
EQUITY_THRESHOLD = 2.0   # percent, 1 week
FX_THRESHOLD = 1.0
YIELD_THRESHOLD = 10.0   # basis points, 1 week


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.2f}%"


def fmt_bps(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.1f} bps"


def price_table(rows: list[dict]) -> str:
    lines = [
        "| Instrument | Last | 1W | 1M | YTD | 52w range pos |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        pos = "n/a" if r["range_52w_pos"] is None else f"{r['range_52w_pos']:.0f}%"
        lines.append(
            f"| {r['name']} | {r['last']:,.2f} | {fmt_pct(r['chg_1w_pct'])} | "
            f"{fmt_pct(r['chg_1m_pct'])} | {fmt_pct(r['chg_ytd_pct'])} | {pos} |"
        )
    return "\n".join(lines)


def yield_table(rows: list[dict]) -> str:
    lines = [
        "| Series | Level | 1W | 1M | YTD |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['last_pct']:.2f}% | {fmt_bps(r['chg_1w_bps'])} | "
            f"{fmt_bps(r['chg_1m_bps'])} | {fmt_bps(r['chg_ytd_bps'])} |"
        )
    return "\n".join(lines)


def notable_moves(snap: dict) -> list[str]:
    """Pre-flag the outliers so the model anchors on what actually moved."""
    flags: list[str] = []

    for group, rows in snap["prices"].items():
        threshold = FX_THRESHOLD if group == "FX" else EQUITY_THRESHOLD
        for r in rows:
            v = r["chg_1w_pct"]
            if v is None:
                continue
            if abs(v) >= threshold:
                direction = "rose" if v > 0 else "fell"
                flags.append(f"- {r['name']} {direction} {abs(v):.2f}% over the week")
            # extremes of the annual range are worth a mention
            pos = r["range_52w_pos"]
            if pos is not None and pos >= 97:
                flags.append(f"- {r['name']} is at the top of its 52-week range")
            elif pos is not None and pos <= 3:
                flags.append(f"- {r['name']} is at the bottom of its 52-week range")

    for _, rows in snap["yields"].items():
        for r in rows:
            v = r["chg_1w_bps"]
            if v is not None and abs(v) >= YIELD_THRESHOLD:
                direction = "rose" if v > 0 else "fell"
                flags.append(f"- {r['name']} {direction} {abs(v):.1f} bps over the week")

    return flags


def main() -> None:
    snap = json.loads(DATA.read_text(encoding="utf-8"))

    parts = [
        f"# Market data — week of {snap['week_label']}",
        f"_Snapshot generated {snap['generated_at']}. "
        f"Week covered: {snap['week_start']} to {snap['week_end']}._",
        "",
    ]

    for group, rows in snap["prices"].items():
        if rows:
            parts += [f"## {group}", price_table(rows), ""]

    for group, rows in snap["yields"].items():
        if rows:
            parts += [f"## Rates — {group}", yield_table(rows), ""]

    flags = notable_moves(snap)
    if flags:
        parts += ["## Notable moves (auto-flagged)", *flags, ""]

    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {OUT} — {len(flags)} notable moves flagged")


if __name__ == "__main__":
    main()