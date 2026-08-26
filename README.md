# Weekly Market Letters

A recurring series of one-page macro & market summaries, generated and
published automatically every Monday.

Each weekly letter covers:

1. **What moved markets**
2. **How major asset classes reacted**
3. **Why it matters for the week ahead**
4. **One article of the week** — selected manually, the one human editorial
   decision in the pipeline

The repository exists to build analytical discipline, develop a macro narrative
week after week, and serve as a public sample of research-style writing.

It is also an engineering project. Every Monday at 07:00 UTC, a GitHub Actions
workflow pulls market data, collects headlines, drafts the letter with an LLM,
**validates every number in it against the source data**, and publishes the
result. If a single figure is wrong, nothing gets published.

That last part is the point. Generating plausible market commentary is easy.
Generating market commentary you can put your name on is a different problem,
and it turns out to be mostly an engineering problem rather than a prompting
one.

---

## Why the validator exists

An early draft contained this line:

```
ASML is trading at a 52-week high after a strong week for European semiconductors.
```

ASML was at 87.4% of its 52-week range and had **fallen** 4.66% that week. The
sentence was fluent, confident, and completely wrong.

This is the failure mode that matters for a market letter. A typo is
embarrassing; a fabricated market claim destroys the credibility of everything
around it. So the pipeline treats the LLM as an untrusted component: it writes
prose, and a deterministic Python script checks every claim it makes against
`data/latest.json` before anything is published.

---

## Architecture

```
fetch_data.py     → data/latest.json        market data (yfinance, FRED, ECB)
fetch_news.py     → data/news.md            headlines from public RSS feeds
build_context.py  → data/context.md         compact prompt payload + writing rules
draft_letter.py   → letters/YYYY-MM-DD.md   LLM draft (Gemini)
validate.py       → exit 0 / exit 1         cross-checks every figure
render_html.py    → docs/                   published output
```

Each step is a standalone script that reads files and writes files. Nothing is
held in memory across steps, so any stage can be re-run in isolation while
debugging — which turned out to matter a lot.

`validate.py` sits between drafting and publishing and exits non-zero on any
discrepancy. The GitHub Actions job stops there. **A failed validation is the
system working, not the system breaking.**

---

## What it checks

| Check | Rule |
|---|---|
| Figure accuracy | Every quoted figure must match `latest.json` within a rounding tolerance |
| Unit correctness | Rate *levels* in percent, rate *changes* and spreads in basis points |
| Required coverage | FX, commodities, crypto and all rate series must appear |
| Index coverage | Every tracked index must appear |
| Material moves | Any single stock moving >5% must be covered |
| Range claims | No "52-week high/low" claim unless the position actually supports it |

A representative failure:

```
Validating 2026-08-14.md against latest.json
  20 figures cross-checked
18 PROBLEM(S) FOUND:
  x US 10Y Treasury: quoted in pct, data is in bps
  x EUR/USD is missing from the letter (required coverage)
  x 'ASML' is described as a 52-week high but sits at 87.4% of its range
  ...
Error: Process completed with exit code 1.
```

Three genuinely different classes of problem in one run: a unit bug in my own
code, a coverage gap in the prompt, and a hallucination. They needed three
different fixes.

---

## Problems hit, and what actually fixed them

### 1. Unit mismatch — a bug in the validator, not the model

The validator stored exactly one expected value per rate series: the weekly
change in basis points. So when the letter correctly wrote *"US 10Y Treasury:
4.69%"* — a level, quoted in the conventional unit — the validator compared a
percentage against a basis-point field and flagged it. Five errors, all from
one wrong assumption.

Rates legitimately carry two units. The fix was to expect both:

```python
units = {}
if row.get("last_pct") is not None:
    units["pct"] = row["last_pct"]        # level
if row.get("chg_1w_bps") is not None:
    units["bps"] = row["chg_1w_bps"]      # change
```

A line may quote either, or both, and each is checked against the matching
field.

**Takeaway:** when a validator fires on a whole category at once, suspect the
validator before the thing being validated.

### 2. Substring matching produced phantom duplicates

`"S&P 500" in line` is `True` for a line about **S&P 500 Equal Weight**. Every
range claim about the equal-weight index was reported twice — once correctly,
once against the wrong instrument.

Fixed by discarding any matched name that is a substring of another name also
present on the same line:

```python
hits = [n for n in names if n in line]
return [n for n in hits if not any(o != n and n in o for o in hits)]
```

**Takeaway:** financial instrument names are adversarial for naive string
matching. Ticker collisions and prefix names are the norm, not the exception.

### 3. Thresholds that disagreed across files

`build_context.py` labelled anything ≥ 99.5% of range as a 52-week high.
`validate.py` used 99.95%. A value between the two would be legitimately
generated and then rejected — an unfixable letter, from the model's point of
view.

Both now reference the same constant, with a comment saying so.

### 4. Hallucinated market characterisations

The ASML line above. Instructing a model not to exaggerate does not work
reliably. What works is removing the judgement call from the model entirely:
the descriptive claim is precomputed in Python and handed over as a string to
be copied verbatim.

```python
def range_label(pos):
    if pos >= 99.5: return "at a 52-week high"
    if pos >= 95:   return "near the top of its 52-week range"
    if pos <= 0.5:  return "at a 52-week low"
    if pos <= 5:    return "near the bottom of its 52-week range"
    return "(not noteworthy — do not mention)"
```

This is emitted as a column in the data table the model receives. The
instruction not to characterise the range itself is embedded *in the data*
rather than sitting in a prompt paragraph the model may drift away from.

**Takeaway:** descriptive claims deserve the same treatment as numbers. If a
statement can be derived deterministically, derive it deterministically.

### 5. Dropped sections

An early draft omitted FX, commodities and crypto entirely — eight required
instruments, silently gone. The required-coverage list was buried mid-prompt
under a long data block.

Two changes: the list moved to the **end** of the prompt (recency helps in long
contexts), and the validator treats omission as a hard error rather than
trusting the instruction to hold.

### 6. Filler phrases masking a data gap

Four lines in one letter used *"without a clear catalyst in the week's
reporting"*. The worst offender:

```
Bitcoin: +24.70% — rallied sharply without a clear catalyst.
```

A 24.7% weekly move with no explanation is not a stylistic problem. It is the
RSS feed set failing to cover crypto, showing up as prose. The phrase is now
banned in the writing rules — but the real fix is on the roadmap, because
banning the phrase hides the gap rather than closing it.

**Takeaway:** LLM filler language is often a symptom. Read it as a diagnostic
signal about the inputs.

### 7. A parser too strict for a non-deterministic writer

Tightening the prompt changed the draft's formatting, and the validator's regex
— which required a leading markdown bullet — matched **zero** lines:

```
Validating 2026-08-21.md against latest.json
  0 figures cross-checked
23 PROBLEM(S) FOUND:
```

Every instrument reported missing, from a letter that covered all of them.

The parser now treats the bullet as optional and tolerates bold markers around
the instrument name. Validating the output of a non-deterministic system means
the parser has to be more forgiving than the prompt is strict.

### 8. The bug that cost the most time: `gitignore` vs `.gitignore`

The ignore file was named `gitignore` — no leading dot. Git ignored the ignore
file.

The consequence was subtle and expensive. `data/context.md` is a generated
intermediate artifact, and because it was being versioned, a stale copy
travelled between local and CI. Local runs validated against different data
than CI runs. The same commit passed on my machine and failed in Actions,
repeatedly, for reasons that looked like parser bugs, prompt bugs, and token
truncation in turn. I chased all three.

The fix was renaming one file. The lesson was expensive and worth writing down:

**When local and CI disagree on identical code, stop debugging the code. Debug
the inputs.**

---

## Debugging in CI, without flying blind

A failed run left nothing behind — the draft that failed validation was
generated in the runner and discarded when the job exited. Every failure had to
be reproduced blind.

```yaml
- name: Upload draft on failure
  if: failure()
  uses: actions/upload-artifact@v4
  with:
    name: failed-draft
    path: |
      letters/
      data/context.md
```

Trivial to add, and it changed the debugging loop from guesswork to inspection.
It should have been the first thing in the workflow rather than something added
after several opaque failures.

---

## Design principles that emerged

1. **Treat the LLM as an untrusted component.** It writes prose. It does not
   get the final word on any factual claim.
2. **Precompute anything derivable.** Numbers, and also descriptive
   characterisations. If Python can decide it, Python decides it.
3. **Put constraints in the data, not just the prompt.** A rule embedded in the
   table the model is reading survives long context better than a rule three
   paragraphs up.
4. **Fail loudly and stop.** Publishing a wrong number is far worse than
   publishing nothing.
5. **Strict prompt, forgiving parser.** The generator is non-deterministic; the
   validator must absorb reasonable variation without breaking.

---

## Running it locally

```bash
git clone https://github.com/Raydotcom/Weekly-Newsletter
cd Weekly-Newsletter
pip install -r requirements.txt

export GEMINI_API_KEY=...        # set GEMINI_API_KEY=... on Windows

python src/fetch_data.py
python src/fetch_news.py
python src/build_context.py
python src/draft_letter.py
python src/validate.py
python src/render_html.py
```

`validate.py` can be run on its own against any existing letter, which is the
fastest way to iterate on the checks themselves.

---

## Stack

Python 3.11 · yfinance · FRED and ECB data feeds · Google Gemini API ·
GitHub Actions · GitHub Pages

Zero infrastructure cost: everything runs on the Actions free tier and the
Gemini free tier.

---

## Roadmap

- **Critic–revise loop.** Feed validator output back to the model with the
  draft and let it self-correct, up to three iterations. Currently a validation
  failure means a dead workflow rather than an automatic repair.
- **Broader news coverage.** Add crypto and commodity sources so that moves
  like Bitcoin's 24.7% week come with an actual explanation.
- **Retrieval over past letters.** Give the drafting step access to previous
  editions so the letter can build on earlier calls instead of restarting from
  zero each week.

---

## 📬 Contact

Prepared by **Rayan Hobballah**
Master's in Economic and Political Analysis — Econometrics
University of Strasbourg
