"""
validate.py — checks the drafted letter against the market data.

Verifies that:
  1. Every figure quoted in section 2 matches data/latest.json exactly.
  2. Every index, FX pair, commodity, yield and crypto appears in the letter.
  3. Any single stock that moved more than 5% appears.
  4. No "52-week high/low" claim is made unless the range position is 100 or 0.

Exits with code 1 if anything fails, so GitHub Actions stops before
publishing a letter with a wrong number in it.

Usage: python src/validate.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SNAPSHOT = Path("data/latest.json")
LETTERS = Path("letters")

BIG_MOVE = 5.0          # single stocks beyond this must be covered
TOLERANCE = 0.005       # allowance for rounding when comparing figures

# Groups where every member must appear in the letter
MANDATORY_GROUPS = {"FX", "Commodities", "Crypto"}
# Within equities, these are indices rather than single stocks
INDEX_NAMES = {
    "Stoxx Europe 600", "Euro Stoxx 50", "CAC 40", "DAX", "FTSE 100",
    "Euro Stoxx Banks (ETF proxy)", "S&P 500", "Nasdaq 100",
    "S&P 500 Equal Weight",
}


def latest_letter() -> Path:
    files = sorted(LETTERS.glob("*.md"))
    if not files:
        sys.exit("No letters found in letters/")
    return files[-1]


def parse_letter_lines(text: str) -> dict[str, tuple[str, float]]:
    """
    Pull '- Name: +0.00% - clause' lines out of the letter.
    Returns {name: (unit, value)} where unit is 'pct' or 'bps'.
    """
    found: dict[str, tuple[str, float]] = {}
    pattern = re.compile(
        r"^\s*[-*]\s*(?P<name>[^:]+?):\s*(?P<val>[+-]?\d+(?:\.\d+)?)\s*(?P<unit>%|bps)",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        name = match.group("name").strip().strip("*")
        unit = "pct" if match.group("unit") == "%" else "bps"
        found[name] = (unit, float(match.group("val")))
    return found


def expected_values(snap: dict) -> dict[str, tuple[str, float, dict]]:
    """Build {name: (unit, expected_week_change, full_row)} from the snapshot."""
    out: dict[str, tuple[str, float, dict]] = {}
    for group, rows in snap["prices"].items():
        for row in rows:
            if row["chg_1w_pct"] is not None:
                out[row["name"]] = ("pct", row["chg_1w_pct"], {**row, "_group": group})
    for group, rows in snap["yields"].items():
        for row in rows:
            if row["chg_1w_bps"] is not None:
                out[row["name"]] = ("bps", row["chg_1w_bps"], {**row, "_group": group})
    return out


def check_range_claims(text: str, expected: dict) -> list[str]:
    """Flag 52-week high/low claims where the range position doesn't support it."""
    problems: list[str] = []
    for line in text.splitlines():
        if not re.search(r"52[- ]week (high|low)", line, re.IGNORECASE):
            continue
        for name, (_, _, row) in expected.items():
            if name not in line:
                continue
            pos = row.get("range_52w_pos")
            if pos is None:
                continue
            claims_high = bool(re.search(r"52[- ]week high", line, re.IGNORECASE))
            if claims_high and pos < 99.95:
                problems.append(
                    f"'{name}' is described as a 52-week high but sits at "
                    f"{pos}% of its range"
                )
            if not claims_high and pos > 0.05:
                problems.append(
                    f"'{name}' is described as a 52-week low but sits at "
                    f"{pos}% of its range"
                )
    return problems


def main() -> None:
    if not SNAPSHOT.exists():
        sys.exit("Missing data/latest.json")

    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    path = latest_letter()
    text = path.read_text(encoding="utf-8")

    expected = expected_values(snap)
    quoted = parse_letter_lines(text)

    errors: list[str] = []
    warnings: list[str] = []
    checked = 0

    # 1. Figures that appear must be right
    for name, (unit, value) in quoted.items():
        if name not in expected:
            continue                     # not one of our tracked instruments
        exp_unit, exp_value, _ = expected[name]
        if unit != exp_unit:
            errors.append(f"{name}: quoted in {unit}, data is in {exp_unit}")
            continue
        if abs(value - exp_value) > TOLERANCE:
            errors.append(
                f"{name}: letter says {value:+g}{'%' if unit == 'pct' else ' bps'}, "
                f"data says {exp_value:+g}{'%' if unit == 'pct' else ' bps'}"
            )
        checked += 1

    # 2 & 3. Required coverage
    for name, (unit, value, row) in expected.items():
        if name in quoted:
            continue
        group = row["_group"]
        if group in MANDATORY_GROUPS or "yield" in group.lower() or "Euro area" in group or "FRED" in group:
            errors.append(f"{name} is missing from the letter (required coverage)")
        elif name in INDEX_NAMES:
            errors.append(f"{name} is missing from the letter (index, required)")
        elif unit == "pct" and abs(value) >= BIG_MOVE:
            errors.append(
                f"{name} moved {value:+g}% but is missing from the letter "
                f"(single stocks beyond {BIG_MOVE}% must be covered)"
            )
        else:
            warnings.append(f"{name} not mentioned ({value:+g}%)")

    # 4. Range claims
    errors.extend(check_range_claims(text, expected))

    # Report
    print(f"Validating {path.name} against {SNAPSHOT.name}")
    print(f"  {checked} figures cross-checked")

    if warnings:
        print(f"  {len(warnings)} optional instruments omitted (fine):")
        for w in warnings[:10]:
            print(f"    - {w}")

    if errors:
        print(f"\n{len(errors)} PROBLEM(S) FOUND:")
        for e in errors:
            print(f"  x {e}")
        sys.exit(1)

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()