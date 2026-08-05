"""
fetch_data.py — Weekly Market Letter data pipeline

Pulls the full tracking universe (European core, US satellite, FX,
rates, commodities, crypto) and writes a single JSON snapshot that
the drafting step consumes.

Sources
-------
yfinance      : equities, indices, FX, commodities, crypto  (no key)
FRED          : US Treasury yields                          (no key, CSV endpoint)
ECB Data API  : euro area yield curve                       (no key)

Output
------
data/YYYY-MM-DD.json  and  data/latest.json
"""

from __future__ import annotations

import io
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

# --------------------------------------------------------------------------
# Tracking universe
# --------------------------------------------------------------------------

UNIVERSE: dict[str, list[tuple[str, str]]] = {
    "European equities": [
        ("^STOXX",     "Stoxx Europe 600"),
        ("^STOXX50E",  "Euro Stoxx 50"),
        ("^FCHI",      "CAC 40"),
        ("^GDAXI",     "DAX"),
        ("^FTSE",      "FTSE 100"),
        ("EXV1.DE",    "Euro Stoxx Banks (ETF proxy)"),
        ("ASML.AS",    "ASML"),
        ("SAP.DE",     "SAP"),
        ("MC.PA",      "LVMH"),
        ("TTE.PA",     "TotalEnergies"),
        ("NOVO-B.CO",  "Novo Nordisk"),
        ("BNP.PA",     "BNP Paribas"),
    ],
    "US equities": [
        ("^GSPC",  "S&P 500"),
        ("^NDX",   "Nasdaq 100"),
        ("RSP",    "S&P 500 Equal Weight"),
        ("NVDA",   "Nvidia"),
        ("MSFT",   "Microsoft"),
        ("GOOGL",  "Alphabet"),
        ("META",   "Meta"),
        ("AMZN",   "Amazon"),
    ],
    "FX": [
        ("EURUSD=X",   "EUR/USD"),
        ("EURGBP=X",   "EUR/GBP"),
        ("EURCHF=X",   "EUR/CHF"),
        ("JPY=X",      "USD/JPY"),
        ("DX-Y.NYB",   "Dollar Index (DXY)"),
    ],
    "Commodities": [
        ("BZ=F",  "Brent crude"),
        ("GC=F",  "Gold"),
    ],
    "Crypto": [
        ("BTC-USD",  "Bitcoin"),
    ],
}

# FRED series (daily, CSV endpoint, no API key required)
FRED_SERIES = {
    "DGS10": "US 10Y Treasury",
    "DGS2":  "US 2Y Treasury",
}

# ECB Data Portal — euro area government yield curve, 10Y spot rate.
# G_N_A = AAA-rated issuers only (Bund proxy)
# G_N_C = all euro area issuers (spread vs AAA = periphery/credit premium)
ECB_SERIES = {
    "YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y": "Euro area AAA 10Y",
    "YC/B.U2.EUR.4F.G_N_C.SV_C_YM.SR_10Y": "Euro area all-issuer 10Y",
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def to_naive(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Strip timezone so all comparisons happen on plain calendar dates."""
    return idx.tz_localize(None) if idx.tz is not None else idx


def value_at(series: pd.Series, when: pd.Timestamp) -> float | None:
    """Last observation on or before `when`."""
    window = series[series.index <= when]
    return None if window.empty else float(window.iloc[-1])


def pct_between(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    a, b = value_at(series, start), value_at(series, end)
    if a is None or b is None or a == 0:
        return None
    return round((b / a - 1) * 100, 2)


def bps_between(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    a, b = value_at(series, start), value_at(series, end)
    if a is None or b is None:
        return None
    return round((b - a) * 100, 1)


def range_position(series: pd.Series, anchor: pd.Timestamp) -> float | None:
    """Where the anchor-date price sits in its trailing 52-week range."""
    window = series[series.index <= anchor].tail(252)
    if window.empty:
        return None
    lo, hi = window.min(), window.max()
    if hi == lo:
        return None
    return round((window.iloc[-1] - lo) / (hi - lo) * 100, 1)


# --------------------------------------------------------------------------
# Price data
# --------------------------------------------------------------------------

def fetch_prices(week_start: pd.Timestamp, week_end: pd.Timestamp) -> dict:
    """Prices measured over the labeled week, not over 'the last 7 days'."""
    prior_close = week_start - pd.Timedelta(days=1)   # previous Friday's close
    month_ref = week_end - pd.Timedelta(days=30)
    year_ref = pd.Timestamp(date(week_end.year, 1, 1)) - pd.Timedelta(days=5)

    tickers = [t for group in UNIVERSE.values() for t, _ in group]
    raw = yf.download(
        tickers,
        period="2y",
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
    )

    out: dict[str, list[dict]] = {}
    for group, members in UNIVERSE.items():
        rows = []
        for ticker, label in members:
            try:
                closes = raw[ticker]["Close"].dropna()
            except (KeyError, TypeError):
                print(f"  ! no data for {ticker} ({label})", file=sys.stderr)
                continue
            if closes.empty:
                print(f"  ! empty series for {ticker} ({label})", file=sys.stderr)
                continue

            closes.index = to_naive(closes.index)
            last = value_at(closes, week_end)
            if last is None:
                print(f"  ! no observation by {week_end.date()} for {label}", file=sys.stderr)
                continue

            observed = closes[closes.index <= week_end].index[-1]
            rows.append({
                "ticker": ticker,
                "name": label,
                "last": round(last, 2),
                "chg_1w_pct": pct_between(closes, prior_close, week_end),
                "chg_1m_pct": pct_between(closes, month_ref, week_end),
                "chg_ytd_pct": pct_between(closes, year_ref, week_end),
                "range_52w_pos": range_position(closes, week_end),
                "as_of": observed.strftime("%Y-%m-%d"),
            })
        out[group] = rows
    return out


# --------------------------------------------------------------------------
# Yields
# --------------------------------------------------------------------------

def fetch_fred(week_start: pd.Timestamp, week_end: pd.Timestamp) -> list[dict]:
    prior = week_start - pd.Timedelta(days=1)
    month_ref = week_end - pd.Timedelta(days=30)
    year_ref = pd.Timestamp(date(week_end.year, 1, 1)) - pd.Timedelta(days=5)

    rows = []
    for series_id, label in FRED_SERIES.items():
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
        except Exception as exc:
            print(f"  ! FRED {series_id} failed: {exc}", file=sys.stderr)
            continue

        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        s = df.dropna().set_index("date")["value"].sort_index()
        level = value_at(s, week_end)
        if level is None:
            continue

        rows.append({
            "series": series_id,
            "name": label,
            "last_pct": round(level, 3),
            "chg_1w_bps": bps_between(s, prior, week_end),
            "chg_1m_bps": bps_between(s, month_ref, week_end),
            "chg_ytd_bps": bps_between(s, year_ref, week_end),
            "as_of": s[s.index <= week_end].index[-1].strftime("%Y-%m-%d"),
        })
    return rows


def fetch_ecb(week_start: pd.Timestamp, week_end: pd.Timestamp) -> list[dict]:
    prior = week_start - pd.Timedelta(days=1)
    month_ref = week_end - pd.Timedelta(days=30)
    year_ref = pd.Timestamp(date(week_end.year, 1, 1)) - pd.Timedelta(days=5)

    rows = []
    for key, label in ECB_SERIES.items():
        url = (
            f"https://data-api.ecb.europa.eu/service/data/{key}"
            "?format=csvdata&lastNObservations=400"
        )
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
        except Exception as exc:
            print(f"  ! ECB {key} failed: {exc}", file=sys.stderr)
            continue

        df["date"] = pd.to_datetime(df["TIME_PERIOD"])
        s = df.set_index("date")["OBS_VALUE"].sort_index().dropna()
        level = value_at(s, week_end)
        if level is None:
            continue

        rows.append({
            "series": key,
            "name": label,
            "last_pct": round(level, 3),
            "chg_1w_bps": bps_between(s, prior, week_end),
            "chg_1m_bps": bps_between(s, month_ref, week_end),
            "chg_ytd_bps": bps_between(s, year_ref, week_end),
            "as_of": s[s.index <= week_end].index[-1].strftime("%Y-%m-%d"),
        })

    # AAA vs all-issuer spread — a clean read on euro area credit conditions
    aaa = next((r for r in rows if "AAA" in r["name"]), None)
    allq = next((r for r in rows if "all-issuer" in r["name"]), None)
    if aaa and allq:
        rows.append({
            "series": "DERIVED",
            "name": "Euro area 10Y spread (all-issuer - AAA)",
            "last_pct": round(allq["last_pct"] - aaa["last_pct"], 3),
            "chg_1w_bps": None if (allq["chg_1w_bps"] is None or aaa["chg_1w_bps"] is None)
                          else round(allq["chg_1w_bps"] - aaa["chg_1w_bps"], 1),
            "chg_1m_bps": None if (allq["chg_1m_bps"] is None or aaa["chg_1m_bps"] is None)
                          else round(allq["chg_1m_bps"] - aaa["chg_1m_bps"], 1),
            "chg_ytd_bps": None if (allq["chg_ytd_bps"] is None or aaa["chg_ytd_bps"] is None)
                           else round(allq["chg_ytd_bps"] - aaa["chg_ytd_bps"], 1),
            "as_of": allq["as_of"],
        })
    return rows


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> None:
    """Anchor everything to the last complete Monday-Friday week."""
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    week_start = pd.Timestamp(this_monday - timedelta(days=7))   # Monday
    week_end = pd.Timestamp(this_monday - timedelta(days=3))     # Friday

    label = f"{week_start:%d}-{week_end:%d %B %Y}"
    print(f"Week covered: {week_start.date()} to {week_end.date()}")

    print("Fetching prices...")
    prices = fetch_prices(week_start, week_end)
    print("Fetching FRED yields...")
    fred = fetch_fred(week_start, week_end)
    print("Fetching ECB yields...")
    ecb = fetch_ecb(week_start, week_end)

    snapshot = {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "week_label": label,
        "week_start": week_start.date().isoformat(),
        "week_end": week_end.date().isoformat(),
        "prices": prices,
        "yields": {"US (FRED)": fred, "Euro area (ECB)": ecb},
    }

    out_dir = Path("data")
    out_dir.mkdir(exist_ok=True)
    payload = json.dumps(snapshot, indent=2, ensure_ascii=False)
    (out_dir / f"{week_end.date().isoformat()}.json").write_text(payload, encoding="utf-8")
    (out_dir / "latest.json").write_text(payload, encoding="utf-8")

    n = sum(len(v) for v in prices.values()) + len(fred) + len(ecb)
    print(f"Wrote data/latest.json - {n} series, week of {label}")


if __name__ == "__main__":
    main()