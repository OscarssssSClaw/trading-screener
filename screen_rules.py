"""Codable approximations of the locked VCP, Qullamaggie, and High Tight Flag rules.

These are screen rules, not a promise that a discretionary chart review would agree.

Distance to the 52-week high is the near-high gate. All-time high is not.
High Tight Flag means a pole plus a short flag, not a higher-time-frame filter.
There is no 6-month performance ceiling.
"""

from __future__ import annotations

import math

import pandas as pd


class ScanError(RuntimeError):
    """The scan cannot support a board that should be published."""


ADR_MIN_PCT = 4.0
MIN_PRICE = 5.0
ADV_MIN_DOLLARS = 3_000_000
MIN_LIQUID_UNIVERSE = 200
MIN_BOARD_SIZE = 10

# VCP is the Minervini trend template plus contraction / volume dry-up.
# Earnings, EPS, and institutional sponsorship (the rest of SEPA) are out of v1.
VCP_MAX_DIST_52W = 25.0
VCP_MIN_ABOVE_LOW_PCT = 30.0
VCP_MIN_RS_PERCENTILE = 70.0
VCP_DRYUP_RATIO = 0.80
VCP_RANGE_TIGHTEN = 0.85
SMA200_SLOPE_SESSIONS = 21

# Qullamaggie leadership is a universe percentile, not a fixed percent return.
QL_MAX_DIST_52W = 15.0
QL_TOP_PERCENT = 5.0
QL_PERCENTILE_MIN = 100.0 - QL_TOP_PERCENT
QL_NEAR_MA_PCT = 0.05
QL_MAX_RANGE_15D = 15.0
QL_RANGE_WIDEN = 1.15

# High Tight Flag geometry. The pole floor is 80%, softer than the classical double.
HTF_POLE_MIN_PCT = 80.0
HTF_POLE_MIN_DAYS = 20
HTF_POLE_MAX_DAYS = 40
HTF_FLAG_MIN_DAYS = 10
HTF_FLAG_MAX_DAYS = 30
HTF_MAX_DRAWDOWN_PCT = 25.0
HTF_MAX_DIST_52W = 30.0
HTF_MAX_FLAG_VOL_RATIO = 0.85
HTF_NEAR_MA_PCT = 0.08
# Loose retrieval floors so history is not downloaded for the whole market.
# They are not the pattern definition and they have no upper bound.
HTF_COARSE_MIN_PERF_1M = 15.0
HTF_COARSE_MIN_PERF_3M = 25.0
HTF_COARSE_MIN_PERF_6M = 40.0

NEAR_52W_MAX_DIST = 0.1

# IBD-style weights approximated with the horizons TradingView actually returns.
# 9-month performance is not a scanner field, so 12-month stands in for the two oldest quarters.
RS_WEIGHTS = (0.20, 0.40, 0.20, 0.20)  # 1M, 3M, 6M, 12M
QL_WEIGHTS = (0.45, 0.35, 0.20)  # 1M, 3M, 6M


def num(value, default=None):
    try:
        if value is None:
            return default
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return out


def adv_dollars(close, average_share_volume):
    price = num(close)
    shares = num(average_share_volume)
    if price is None or shares is None or price <= 0 or shares <= 0:
        return None
    return price * shares


def passes_liquidity(close, average_share_volume):
    price = num(close)
    dollar = adv_dollars(close, average_share_volume)
    return price is not None and price >= MIN_PRICE and dollar is not None and dollar >= ADV_MIN_DOLLARS


def dist_from_high_pct(close, high):
    price = num(close)
    peak = num(high)
    if price is None or peak is None or price <= 0 or peak <= 0:
        return None
    return (peak - price) / peak * 100.0


def is_near_52w_high(close, high_52w, max_dist=NEAR_52W_MAX_DIST):
    dist = dist_from_high_pct(close, high_52w)
    return dist is not None and dist <= max_dist


def _mean(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _range_pct(bars):
    if not bars:
        return None
    close = num(bars[-1].get('close'))
    if close is None or close <= 0:
        return None
    highs = [num(bar.get('high')) for bar in bars]
    lows = [num(bar.get('low')) for bar in bars]
    if any(v is None for v in highs + lows):
        return None
    return (max(highs) - min(lows)) / close * 100.0


def sma200_is_rising(bars, lookback=SMA200_SLOPE_SESSIONS):
    """True when the 200-session average is higher than it was `lookback` sessions ago."""
    closes = [num(bar.get('close')) for bar in bars or []]
    if any(v is None for v in closes):
        closes = [v for v in closes if v is not None]
    if len(closes) < 200 + lookback:
        return None
    now = sum(closes[-200:]) / 200.0
    prev = sum(closes[-(200 + lookback):-lookback]) / 200.0
    return now > prev


def contraction_stats(bars):
    """Last three 10-session blocks. Passes only when range contracts and volume dries up."""
    empty = {
        'passes': False,
        'score': 0,
        'range_1': None,
        'range_2': None,
        'range_3': None,
        'dryup_ratio': None,
        'note': 'not enough daily bars for contraction',
    }
    if not bars or len(bars) < 30:
        return empty
    block_1 = bars[-30:-20]
    block_2 = bars[-20:-10]
    block_3 = bars[-10:]
    range_1 = _range_pct(block_1)
    range_2 = _range_pct(block_2)
    range_3 = _range_pct(block_3)
    recent_vol = _mean([num(bar.get('volume'), 0.0) for bar in block_3])
    prior_vol = _mean([num(bar.get('volume'), 0.0) for bar in bars[-30:-10]])
    dryup_ratio = (recent_vol / prior_vol) if prior_vol else None
    tighter = (
        range_1 is not None
        and range_3 is not None
        and range_1 > 0
        and range_3 <= (VCP_RANGE_TIGHTEN * range_1) + 1e-9
    )
    dry = dryup_ratio is not None and dryup_ratio <= VCP_DRYUP_RATIO
    score = 0
    if None not in (range_1, range_2, range_3) and range_1 > range_2 > range_3:
        score += 40
    elif tighter:
        score += 25
    if dry:
        score += 35
    high = max(num(bar.get('high'), 0.0) for bar in block_3)
    low = min(num(bar.get('low'), 0.0) for bar in block_3)
    last = num(block_3[-1].get('close'), 0.0)
    if high > low and last >= low + 0.5 * (high - low):
        score += 10
    passes = bool(tighter and dry)
    note = (
        f"10-session ranges {range_1:.1f}% / {range_2:.1f}% / {range_3:.1f}% (older to newer); "
        f"10d volume / prior 20d = {dryup_ratio:.2f}"
        if None not in (range_1, range_2, range_3, dryup_ratio)
        else 'contraction inputs missing'
    )
    return {
        'passes': passes,
        'score': score,
        'range_1': range_1,
        'range_2': range_2,
        'range_3': range_3,
        'dryup_ratio': dryup_ratio,
        'note': note,
    }


def _row_num(row, key):
    try:
        return num(row.get(key))
    except Exception:
        return None


def _flag(row, key):
    try:
        value = row.get(key)
    except Exception:
        return False
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    return bool(value)


def trend_template_checks(row):
    close = _row_num(row, 'close')
    sma50 = _row_num(row, 'SMA50')
    sma150 = _row_num(row, 'SMA150')
    sma200 = _row_num(row, 'SMA200')
    high_52 = _row_num(row, 'price_52_week_high')
    low_52 = _row_num(row, 'price_52_week_low')
    dist = _row_num(row, 'dist_52w')
    if dist is None:
        dist = dist_from_high_pct(close, high_52)
    rs_pct = _row_num(row, 'rs_pct')
    checks = {
        'sma_stack': None not in (close, sma50, sma150, sma200) and sma200 > 0 and close > sma50 > sma150 > sma200,
        'above_sma150': None not in (close, sma150) and close > sma150,
        'above_sma200': None not in (close, sma200) and close > sma200,
        'above_52w_low_30': (
            None not in (close, low_52) and low_52 > 0 and close >= low_52 * (1 + VCP_MIN_ABOVE_LOW_PCT / 100.0)
        ),
        'within_25_of_52w_high': dist is not None and dist <= VCP_MAX_DIST_52W,
        'rs_proxy_70': rs_pct is not None and rs_pct >= VCP_MIN_RS_PERCENTILE,
    }
    return checks


def evaluate_vcp(row, bars):
    checks = trend_template_checks(row)
    slope = sma200_is_rising(bars or [])
    checks['sma200_rising'] = slope is True
    contraction = contraction_stats(bars or [])
    passes = all(checks.values()) and contraction['passes']
    failed = [name for name, ok in checks.items() if not ok]
    if not contraction['passes']:
        failed.append('contraction_dryup')
    rs_pct = _row_num(row, 'rs_pct')
    dist = _row_num(row, 'dist_52w')
    note = (
        "VCP = trend template + contraction/dry-up, not SEPA earnings. "
        f"RS proxy {rs_pct:.0f}. Dist 52W {dist:.1f}%. {contraction['note']}. "
        "SMA200 slope uses daily bars."
        if rs_pct is not None and dist is not None
        else "VCP = trend template + contraction/dry-up, not SEPA earnings."
    )
    if not passes:
        note += " Failed: " + ", ".join(failed) + "."
    return {'passes': passes, 'note': note, 'checks': checks, 'score': contraction['score']}


def _sma(closes, end, window):
    if end < window:
        return None
    chunk = closes[end - window:end]
    if len(chunk) < window or any(v is None for v in chunk):
        return None
    return sum(chunk) / float(window)


def evaluate_ql(row, bars):
    dist = _row_num(row, 'dist_52w')
    ql_pct = _row_num(row, 'ql_pct')
    adr_ok = _flag(row, 'adr_ok')
    liquid = _flag(row, 'liquid')
    reasons = []
    if not liquid:
        reasons.append('liquidity')
    if not adr_ok:
        reasons.append('adr')
    if ql_pct is None or ql_pct < QL_PERCENTILE_MIN:
        reasons.append('leadership_percentile')
    if dist is None or dist > QL_MAX_DIST_52W:
        reasons.append('dist_52w')

    range_15 = None
    if not bars or len(bars) < 30:
        reasons.append('price_structure')
    else:
        closes = [num(bar.get('close')) for bar in bars]
        if any(v is None or v <= 0 for v in closes):
            reasons.append('price_structure')
        else:
            end = len(closes)
            sma10 = _sma(closes, end, 10)
            sma20 = _sma(closes, end, 20)
            sma10_then = _sma(closes, end - 10, 10)
            sma20_then = _sma(closes, end - 10, 20)
            last = closes[-1]
            near = False
            rising = False
            if None not in (sma10, sma20, sma10_then, sma20_then):
                near_10 = abs(last - sma10) / last <= QL_NEAR_MA_PCT
                near_20 = abs(last - sma20) / last <= QL_NEAR_MA_PCT
                above = last >= sma20 * 0.98 and last >= sma10 * 0.97
                near = (near_10 or near_20) and above
                rising = sma20 > sma20_then and sma10 >= sma10_then * 0.998
            last_15 = bars[-15:]
            prior_15 = bars[-30:-15]
            range_15 = _range_pct(last_15)
            prior_range = _range_pct(prior_15)
            tight = (
                range_15 is not None
                and prior_range is not None
                and range_15 <= QL_MAX_RANGE_15D
                and range_15 <= prior_range * QL_RANGE_WIDEN
            )
            if not (near and rising and tight):
                reasons.append('price_structure')

    passes = not reasons
    ql_text = f"{ql_pct:.0f}" if ql_pct is not None else "n/a"
    dist_text = f"{dist:.1f}%" if dist is not None else "n/a"
    range_text = f"{range_15:.1f}%" if range_15 is not None else "n/a"
    note = (
        f"Qullamaggie leadership percentile {ql_text} of the liquid universe "
        f"(top {QL_TOP_PERCENT:.0f}% = {QL_PERCENTILE_MIN:.0f}+). "
        f"Dist 52W {dist_text}. 15-session range {range_text}. "
        "Gate is the percentile blend of 1M/3M/6M, not a fixed 6-month return."
    )
    if reasons:
        note += " Failed: " + ", ".join(reasons) + "."
    return {'passes': passes, 'note': note, 'range_15': range_15}


def find_htf(bars):
    """Search the most recent bars for one pole (20–40 sessions) and a following flag (10–30)."""
    if not bars or len(bars) < HTF_POLE_MIN_DAYS + HTF_FLAG_MIN_DAYS:
        return None
    best = None
    last_index = len(bars)
    for flag_len in range(HTF_FLAG_MIN_DAYS, HTF_FLAG_MAX_DAYS + 1):
        flag_start = last_index - flag_len
        if flag_start < HTF_POLE_MIN_DAYS:
            continue
        flag = bars[flag_start:last_index]
        for pole_len in range(HTF_POLE_MIN_DAYS, HTF_POLE_MAX_DAYS + 1):
            pole_start = flag_start - pole_len
            if pole_start < 0:
                continue
            pole = bars[pole_start:flag_start]
            base_closes = [num(bar.get('close')) for bar in pole[:5]]
            pole_highs = [num(bar.get('high')) for bar in pole]
            flag_highs = [num(bar.get('high')) for bar in flag]
            flag_lows = [num(bar.get('low')) for bar in flag]
            if any(v is None or v <= 0 for v in base_closes + pole_highs + flag_highs + flag_lows):
                continue
            base = min(base_closes)
            pole_high = max(pole_highs)
            pole_pct = (pole_high / base - 1.0) * 100.0
            if pole_pct < HTF_POLE_MIN_PCT:
                continue
            pole_close = num(pole[-1].get('close'))
            if pole_close is None or pole_close < pole_high * 0.90:
                continue
            flag_high = max(flag_highs)
            flag_low = min(flag_lows)
            if flag_high > pole_high * 1.05:
                continue
            drawdown = (pole_high - flag_low) / pole_high * 100.0
            if drawdown > HTF_MAX_DRAWDOWN_PCT:
                continue
            pole_vol = _mean([num(bar.get('volume'), 0.0) for bar in pole]) or 0.0
            flag_vol = _mean([num(bar.get('volume'), 0.0) for bar in flag]) or 0.0
            if pole_vol <= 0 or flag_vol <= 0:
                continue
            vol_ratio = flag_vol / pole_vol
            if vol_ratio > HTF_MAX_FLAG_VOL_RATIO:
                continue
            closes = [num(bar.get('close')) for bar in bars[:last_index]]
            if any(v is None or v <= 0 for v in closes):
                continue
            sma10 = _sma(closes, last_index, 10)
            sma20 = _sma(closes, last_index, 20)
            last = closes[-1]
            if sma10 is None or sma20 is None:
                continue
            near = (
                (abs(last - sma10) / last <= HTF_NEAR_MA_PCT or abs(last - sma20) / last <= HTF_NEAR_MA_PCT)
                and last >= sma20 * 0.95
            )
            if not near:
                continue
            quality = (pole_pct - drawdown, -vol_ratio, -flag_len)
            candidate = {
                'pole_pct': pole_pct,
                'pole_days': pole_len,
                'flag_days': flag_len,
                'drawdown_pct': drawdown,
                'vol_ratio': vol_ratio,
                'quality': quality,
            }
            if best is None or quality > best['quality']:
                best = candidate
    return best


def evaluate_htf(row, bars):
    """High Tight Flag. Performance figures are not a ceiling; Perf.6M is ignored except for the note."""
    dist = _row_num(row, 'dist_52w')
    perf_6m = _row_num(row, 'Perf.6M')
    reasons = []
    if not _flag(row, 'liquid'):
        reasons.append('liquidity')
    if not _flag(row, 'adr_ok'):
        reasons.append('adr')
    if dist is None or dist > HTF_MAX_DIST_52W:
        reasons.append('dist_52w')
    setup = None if reasons else find_htf(bars or [])
    if setup is None:
        reasons.append('pole_flag')
    passes = bool(setup) and not any(name in ('liquidity', 'adr', 'dist_52w') for name in reasons)
    perf_text = f"{perf_6m:.0f}%" if perf_6m is not None else "n/a"
    if setup:
        note = (
            f"High Tight Flag (not a higher timeframe). Pole {setup['pole_pct']:.0f}% over {setup['pole_days']} sessions, "
            f"flag {setup['flag_days']}d, drawdown {setup['drawdown_pct']:.1f}%, "
            f"flag/pole volume {setup['vol_ratio']:.2f}. Dist 52W {dist:.1f}%. "
            f"6M performance {perf_text} is not capped."
        )
    else:
        note = (
            "High Tight Flag (not a higher timeframe): pole >= "
            f"{HTF_POLE_MIN_PCT:.0f}% over {HTF_POLE_MIN_DAYS}-{HTF_POLE_MAX_DAYS} sessions, "
            f"flag {HTF_FLAG_MIN_DAYS}-{HTF_FLAG_MAX_DAYS} sessions, drawdown <= {HTF_MAX_DRAWDOWN_PCT:.0f}%, "
            "volume dry-up, near the 10/20-day average, within "
            f"{HTF_MAX_DIST_52W:.0f}% of the 52-week high. 6M performance {perf_text} is not capped."
        )
        if reasons:
            note += " Failed: " + ", ".join(dict.fromkeys(reasons)) + "."
    return {'passes': passes, 'note': note, 'setup': setup}


def _both(series_a, series_b):
    return series_a.notna() & series_b.notna() & (series_a > series_b)


def vcp_coarse_mask(df):
    dist = df['dist_52w']
    rs_pct = df['rs_pct']
    low = df['price_52_week_low']
    close = df['close']
    stack = (
        _both(close, df['SMA50'])
        & _both(df['SMA50'], df['SMA150'])
        & _both(df['SMA150'], df['SMA200'])
        & df['SMA200'].notna()
        & (df['SMA200'] > 0)
    )
    above_low = low.notna() & (low > 0) & close.notna() & (close >= low * (1 + VCP_MIN_ABOVE_LOW_PCT / 100.0))
    near_high = dist.notna() & (dist <= VCP_MAX_DIST_52W)
    rs_ok = rs_pct.notna() & (rs_pct >= VCP_MIN_RS_PERCENTILE)
    return df['liquid'].fillna(False) & df['adr_ok'].fillna(False) & stack & above_low & near_high & rs_ok


def ql_coarse_mask(df):
    dist = df['dist_52w']
    ql_pct = df['ql_pct']
    return (
        df['liquid'].fillna(False)
        & df['adr_ok'].fillna(False)
        & ql_pct.notna()
        & (ql_pct >= QL_PERCENTILE_MIN)
        & dist.notna()
        & (dist <= QL_MAX_DIST_52W)
    )


def htf_coarse_mask(df):
    """Retrieval only. No 6-month ceiling. The geometric test is evaluate_htf."""
    dist = df['dist_52w']
    perf_hit = (
        (df['Perf.1M'].fillna(-1e9) >= HTF_COARSE_MIN_PERF_1M)
        | (df['Perf.3M'].fillna(-1e9) >= HTF_COARSE_MIN_PERF_3M)
        | (df['Perf.6M'].fillna(-1e9) >= HTF_COARSE_MIN_PERF_6M)
    )
    return (
        df['liquid'].fillna(False)
        & df['adr_ok'].fillna(False)
        & dist.notna()
        & (dist <= HTF_MAX_DIST_52W)
        & perf_hit
    )


def add_leadership_scores(df):
    """Percentile ranks inside the frame that is passed in. Pass the liquid universe, not a prefiltered subset."""
    out = df.copy()
    perf_1m = pd.to_numeric(out['Perf.1M'], errors='coerce')
    perf_3m = pd.to_numeric(out['Perf.3M'], errors='coerce')
    perf_6m = pd.to_numeric(out['Perf.6M'], errors='coerce')
    perf_y = pd.to_numeric(out['Perf.Y'], errors='coerce')
    w1, w3, w6, wy = RS_WEIGHTS
    out['rs_raw'] = w1 * perf_1m + w3 * perf_3m + w6 * perf_6m + wy * perf_y
    q1, q3, q6 = QL_WEIGHTS
    out['ql_raw'] = q1 * perf_1m + q3 * perf_3m + q6 * perf_6m
    out['rs_pct'] = pd.Series(pd.NA, index=out.index, dtype='Float64')
    out['ql_pct'] = pd.Series(pd.NA, index=out.index, dtype='Float64')
    rs_ok = out['rs_raw'].notna()
    ql_ok = out['ql_raw'].notna()
    if rs_ok.any():
        out.loc[rs_ok, 'rs_pct'] = out.loc[rs_ok, 'rs_raw'].rank(pct=True, method='average') * 100.0
    if ql_ok.any():
        out.loc[ql_ok, 'ql_pct'] = out.loc[ql_ok, 'ql_raw'].rank(pct=True, method='average') * 100.0

    def _rating(value):
        if pd.isna(value):
            return pd.NA
        return int(max(1, min(99, round(float(value)))))

    out['rs_rating'] = out['rs_pct'].apply(_rating).astype('Int64')
    return out


def classify_board(liquid, high52, load_bars):
    """Confirm coarse hits with daily bars. `load_bars(ticker)` returns chronological OHLC bars or None."""
    liquid = liquid.copy()
    vcp_ids = set(liquid.loc[vcp_coarse_mask(liquid), 'ticker'].astype(str))
    ql_ids = set(liquid.loc[ql_coarse_mask(liquid), 'ticker'].astype(str))
    htf_ids = set(liquid.loc[htf_coarse_mask(liquid), 'ticker'].astype(str))
    ordered = list(dict.fromkeys([*sorted(vcp_ids), *sorted(ql_ids), *sorted(htf_ids)]))
    print(
        f"Pattern candidates before daily-bar check: VCP {len(vcp_ids)}, "
        f"Qullamaggie {len(ql_ids)}, High Tight Flag {len(htf_ids)}, unique {len(ordered)}"
    )

    bars_by_ticker = {}
    misses = 0
    for index, ticker in enumerate(ordered, start=1):
        try:
            bars = load_bars(ticker)
        except Exception as exc:
            print(f"WARNING: history fetch failed for {ticker}: {exc}")
            bars = None
        if bars:
            bars_by_ticker[ticker] = bars
        else:
            misses += 1
        if index % 25 == 0 or index == len(ordered):
            print(f"  Pattern history {index}/{len(ordered)} (usable {len(bars_by_ticker)}, missed {misses})")
    if ordered and not bars_by_ticker:
        raise ScanError(
            "Price history failed for every VCP / Qullamaggie / High Tight Flag candidate. "
            "Refusing to publish a board that cannot verify those badges."
        )

    rows = liquid.to_dict('records')
    by_ticker = {str(row['ticker']): row for row in rows}
    vcp_pass = set()
    ql_pass = set()
    htf_pass = set()
    notes = {}
    for ticker in ordered:
        row = by_ticker.get(ticker)
        bars = bars_by_ticker.get(ticker)
        if row is None or not bars:
            continue
        if ticker in vcp_ids:
            result = evaluate_vcp(row, bars)
            notes.setdefault(ticker, {})['vcp_note'] = result['note']
            if result['passes']:
                vcp_pass.add(ticker)
        if ticker in ql_ids:
            result = evaluate_ql(row, bars)
            notes.setdefault(ticker, {})['ql_note'] = result['note']
            if result['passes']:
                ql_pass.add(ticker)
        if ticker in htf_ids:
            result = evaluate_htf(row, bars)
            notes.setdefault(ticker, {})['htf_note'] = result['note']
            if result['passes']:
                htf_pass.add(ticker)

    high52 = high52.copy() if high52 is not None else pd.DataFrame()
    if high52.empty or 'ticker' not in high52.columns:
        high_ids = set()
    else:
        near = high52['dist_52w'].notna() & (high52['dist_52w'] <= NEAR_52W_MAX_DIST)
        liquid_flag = high52['liquid'].fillna(False) if 'liquid' in high52.columns else False
        high_ids = set(high52.loc[near & liquid_flag, 'ticker'].astype(str))

    extra = pd.DataFrame()
    if high_ids:
        known = set(liquid['ticker'].astype(str))
        missing = high_ids - known
        if missing and not high52.empty:
            extra = high52.loc[high52['ticker'].astype(str).isin(missing)].copy()

    board = pd.concat([liquid, extra], ignore_index=True, sort=False)
    board['ticker'] = board['ticker'].astype(str)
    board = board.drop_duplicates('ticker', keep='first')
    board['is_vcp'] = board['ticker'].isin(vcp_pass)
    board['is_ql'] = board['ticker'].isin(ql_pass)
    board['is_htf'] = board['ticker'].isin(htf_pass)
    board['is_52w_high'] = board['ticker'].isin(high_ids)
    board['vcp_note'] = board['ticker'].map(lambda ticker: notes.get(ticker, {}).get('vcp_note', ''))
    board['ql_note'] = board['ticker'].map(lambda ticker: notes.get(ticker, {}).get('ql_note', ''))
    board['htf_note'] = board['ticker'].map(lambda ticker: notes.get(ticker, {}).get('htf_note', ''))
    board = board.loc[board['is_vcp'] | board['is_ql'] | board['is_htf'] | board['is_52w_high']].copy()

    board['symbol'] = board['ticker'].str.split(':').str[-1]
    if 'company' not in board.columns:
        board['company'] = ''
    company = board['company'].fillna('').astype(str).str.strip()
    company = company.mask(company.str.lower().isin(['', 'nan', 'none', '-']), '')
    board['company'] = company
    board['name'] = company.where(company != '', board['symbol'])
    if 'sector' not in board.columns:
        board['sector'] = '-'
    if 'industry' not in board.columns:
        board['industry'] = '-'
    board['dist_52w'] = pd.to_numeric(board['dist_52w'], errors='coerce')
    board['dist_high'] = board['dist_52w']
    for column in ('close', 'Perf.6M', 'ADR', 'volume', 'price_52_week_high', 'High.All', 'dist_52w', 'dist_high'):
        if column not in board.columns:
            board[column] = 0.0
        board[column] = pd.to_numeric(board[column], errors='coerce').fillna(0.0)
    if 'RS' not in board.columns:
        board['RS'] = board['rs_rating'] if 'rs_rating' in board.columns else pd.NA

    stats = {
        'vcp_coarse': len(vcp_ids),
        'ql_coarse': len(ql_ids),
        'htf_coarse': len(htf_ids),
        'history_ok': len(bars_by_ticker),
        'history_miss': misses,
        'vcp': int(board['is_vcp'].sum()) if len(board) else 0,
        'ql': int(board['is_ql'].sum()) if len(board) else 0,
        'htf': int(board['is_htf'].sum()) if len(board) else 0,
        'high52': int(board['is_52w_high'].sum()) if len(board) else 0,
        'board': len(board),
    }
    print(
        "Confirmed badges: "
        f"VCP {stats['vcp']}/{stats['vcp_coarse']}, "
        f"Qullamaggie {stats['ql']}/{stats['ql_coarse']}, "
        f"High Tight Flag {stats['htf']}/{stats['htf_coarse']}, "
        f"52W high {stats['high52']}, board {stats['board']}"
    )
    return board, stats


def assert_publishable(*, universe_total, fetched, liquid_count, scored_rs, board_count=None):
    if universe_total < MIN_LIQUID_UNIVERSE:
        raise ScanError(
            f"Universe scanner total {universe_total} is below {MIN_LIQUID_UNIVERSE}. "
            "Refusing to publish."
        )
    if fetched < universe_total:
        raise ScanError(
            f"Universe fetch truncated at {fetched} of {universe_total}. Refusing to publish a partial board."
        )
    if liquid_count < MIN_LIQUID_UNIVERSE:
        raise ScanError(
            f"Only {liquid_count} names passed ADV$ >= ${ADV_MIN_DOLLARS:,.0f}. Refusing to publish."
        )
    if scored_rs < MIN_LIQUID_UNIVERSE:
        raise ScanError(
            f"RS / leadership percentiles scored {scored_rs} names, below {MIN_LIQUID_UNIVERSE}. "
            "Refusing to publish."
        )
    if board_count is not None and board_count < MIN_BOARD_SIZE:
        raise ScanError(
            f"Board has {board_count} names, below {MIN_BOARD_SIZE}. "
            "Refusing to publish an empty or thin board."
        )


def sector_strength(sectors):
    counts = {}
    for value in sectors:
        label = str(value or '').strip()
        if not label or label.lower() in ('nan', 'none', '-'):
            continue
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def clip_text(text, limit=280):
    cleaned = ' '.join(str(text or '').split())
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit].rsplit(' ', 1)[0]
    return (cut or cleaned[:limit]).rstrip() + '…'
