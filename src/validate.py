"""
validate.py — checks the drafted letter against the market data.

Verifies that:
  1. Every figure quoted in section 2 matches data/latest.json exactly.
  2. Every index, FX pair, commodity, yield and crypto appears in the letter.
  3. Any single stock that moved more than 5% appears.
  4. No "52-week high/low" claim is made unless the range position supports it.

Rates may be quoted either as a level in percent or as a change in basis
points; both are checked against the matching field.

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

# Must match the thresholds used in build_context.range_label()
HIGH_THRESHOLD = 99.5
LOW_THRESHOLD = 0.5

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


def parse_letter_lines(text: str) -> dict[str, dict[str, float]]:
    """
    Pull 'Name: <figures>' lines out of the letter.

    The leading markdown bullet is optional: the model sometimes drops it,
    and a line is identifiable by the 'Name: figure' shape alone. Bold
    markers around the name are tolerated too.

    Returns {name: {"pct": value, "bps": value}} — a line may carry both,
    e.g. 'US 10Y Treasury: 4.69% (+1.0 bps)'.
    """
    found: dict[str, dict[str, float]] = {}
    line_pattern = re.compile(
        r"^\s*(?:[-*+]\s*)?(?P<name>[^:|#]{2,60}?):\s*(?P<rest>.*[+-]?\d.*)$"
    )
    fig_pattern = re.compile(r"(?P<val>[+-]?\d+(?:[.,]\d+)?)\s*(?P<unit>%|bps)")

    for line in text.splitlines():
        stripped = line.strip()
        # skip headings and table rows
        if stripped.startswith("#") or stripped.startswith("|"):
            continue
        m = line_pattern.match(line)
        if not m:
            continue
        name = m.group("name").strip().strip("*_").strip()
        figures: dict[str, float] = {}
        for f in fig_pattern.finditer(m.group("rest")):
            unit = "pct" if f.group("unit") == "%" else "bps"
            # keep the first figure of each unit on the line
            figures.setdefault(unit, float(f.group("val").replace(",", ".")))
        if figures:
            found[name] = figures
    return found


def expected_values(snap: dict) -> dict[str, dict]:
    """
    Build {name: {...}} from the snapshot.

    Prices carry a 'pct' weekly change. Yields carry both a 'pct' level
    and a 'bps' weekly change, so either may legitimately be quoted.
    """
    out: dict[str, dict] = {}

    for group, rows in snap["prices"].items():
        if rows is None:
            continue
        for row in rows:
            if row.get("chg_1w_pct") is None:
                continue
            out[row["name"]] = {
                "units": {"pct": row["chg_1w_pct"]},
                "primary_unit": "pct",
                "primary": row["chg_1w_pct"],
                "row": {**row, "_group": group},
                "kind": "price",
            }

    for group, rows in snap["yields"].items():
        if rows is None:
            continue
        for row in rows:
            units: dict[str, float] = {}
            if row.get("last_pct") is not None:
                units["pct"] = row["last_pct"]
            if row.get("chg_1w_bps") is not None:
                units["bps"] = row["chg_1w_bps"]
            if not units:
                continue
            out[row["name"]] = {
                "units": units,
                "primary_unit": "bps" if "bps" in units else "pct",
                "primary": units.get("bps", units.get("pct")),
                "row": {**row, "_group": group},
                "kind": "yield",
            }

    return out


def names_in_line(line: str, names: list[str]) -> list[str]:
    """
    Return the instrument names mentioned in a line, ignoring any name
    that is only a substring of a longer name also present.

    Without this, 'S&P 500' matches a line about 'S&P 500 Equal Weight'.
    """
    hits = [n for n in names if n in line]
    return [n for n in hits if not any(o != n and n in o for o in hits)]


def check_range_claims(text: str, expected: dict) -> list[str]:
    """Flag 52-week high/low claims where the range position doesn't support it."""
    problems: list[str] = []
    names = sorted(expected.keys(), key=len, reverse=True)

    for line in text.splitlines():
        if not re.search(r"52[- ]week (high|low)", line, re.IGNORECASE):
            continue
        claims_high = bool(re.search(r"52[- ]week high", line, re.IGNORECASE))

        for name in names_in_line(line, names):
            pos = expected[name]["row"].get("range_52w_pos")
            if pos is None:
                continue
            if claims_high and pos < HIGH_THRESHOLD:
                problems.append(
                    f"'{name}' is described as a 52-week high but sits at "
                    f"{pos}% of its range"
                )
            elif not claims_high and pos > LOW_THRESHOLD:
                problems.append(
                    f"'{name}' is described as a 52-week low but sits at "
                    f"{pos}% of its range"
                )

    # the same claim can appear on several lines; report each once
    return list(dict.fromkeys(problems))


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
    for name, figures in quoted.items():
        if name not in expected:
            continue                     # not one of our tracked instruments
        exp = expected[name]
        matched_any = False

        for unit, value in figures.items():
            if unit not in exp["units"]:
                errors.append(
                    f"{name}: quoted in {unit}, data has no {unit} figure "
                    f"(available: {', '.join(exp['units'])})"
                )
                continue
            matched_any = True
            exp_value = exp["units"][unit]
            if abs(value - exp_value) > TOLERANCE:
                suffix = "%" if unit == "pct" else " bps"
                errors.append(
                    f"{name}: letter says {value:+g}{suffix}, "
                    f"data says {exp_value:+g}{suffix}"
                )
        if matched_any:
            checked += 1

    # 2 & 3. Required coverage
    for name, exp in expected.items():
        if name in quoted:
            continue
        group = exp["row"]["_group"]
        value = exp["primary"]
        if (
            group in MANDATORY_GROUPS
            or exp["kind"] == "yield"
            or "yield" in group.lower()
        ):
            errors.append(f"{name} is missing from the letter (required coverage)")
        elif name in INDEX_NAMES:
            errors.append(f"{name} is missing from the letter (index, required)")
        elif exp["primary_unit"] == "pct" and abs(value) >= BIG_MOVE:
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