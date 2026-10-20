#!/usr/bin/env python3
"""TradingView Screener HTML Generator - Multi-strategy badges on merged stocks"""

import time
import datetime
import math
import urllib.parse
import yfinance as yf
import pandas as pd
import json
import html
from pathlib import Path

from market_scan import scan_liquid_universe, scan_near_52w
from screen_rules import (
    ADR_MIN_PCT,
    ADV_MIN_DOLLARS,
    HTF_FLAG_MAX_DAYS,
    HTF_FLAG_MIN_DAYS,
    HTF_MAX_DIST_52W,
    HTF_MAX_DRAWDOWN_PCT,
    HTF_MAX_FLAG_VOL_RATIO,
    HTF_POLE_MAX_DAYS,
    HTF_POLE_MIN_DAYS,
    HTF_POLE_MIN_PCT,
    MIN_PRICE,
    NEAR_52W_MAX_DIST,
    QL_MAX_DIST_52W,
    QL_MAX_RANGE_15D,
    QL_PERCENTILE_MIN,
    QL_TOP_PERCENT,
    ScanError,
    VCP_DRYUP_RATIO,
    VCP_MAX_DIST_52W,
    VCP_MIN_ABOVE_LOW_PCT,
    VCP_MIN_RS_PERCENTILE,
    VCP_RANGE_TIGHTEN,
    assert_publishable,
    classify_board,
    clip_text,
    sector_strength,
)

start = time.time()
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
last_updated = now.strftime("%Y-%m-%d %H:%M") + " HK"

GAMMA_MIN_VOLUME = 500
GAMMA_MIN_OI = 100
GAMMA_MIN_VOL_OI = 5
GAMMA_MIN_PREMIUM = 250_000
GAMMA_MAX_DTE = 30
GAMMA_MAX_EXPIRIES = 4
GAMMA_HISTORY_PATH = Path("data/gamma_contract_history.json")
GAMMA_WALL_MIN_OI = 20
GAMMA_WALL_MIN_SCORE = 55
GAMMA_WALL_MAX_DIST_PCT = 20
GAMMA_WALL_MIN_DIRECTIONAL_DIST_PCT = 2.0
GAMMA_WALL_SCORE_ADV_BPS = 30
GAMMA_WALL_ABS_GEX_FALLBACK = 3_000_000


def yfinance_symbol(ticker):
    if ':' not in ticker:
        return None
    symbol = ticker.split(':')[-1].strip()
    if not symbol or symbol.startswith('OTC') or '/' in symbol:
        return None
    return symbol


def contract_key(symbol, contract):
    return f"{symbol}|{contract}"


def load_gamma_history():
    if not GAMMA_HISTORY_PATH.exists():
        return {'contracts': {}}
    try:
        data = json.loads(GAMMA_HISTORY_PATH.read_text())
        if isinstance(data, dict) and isinstance(data.get('contracts'), dict):
            return data
    except Exception:
        pass
    return {'contracts': {}}


def verify_contract(symbol, candidate, history):
    prev = history.get('contracts', {}).get(contract_key(symbol, candidate.get('contract', '')))
    if not prev:
        candidate.update({
            'verification': 'Pending',
            'verification_note': 'First seen; need next OI update',
            'first_seen_at': last_updated,
            'first_seen_date': now.strftime('%Y-%m-%d'),
            'prev_oi': None,
            'oi_change': None,
            'oi_change_pct': None,
        })
        return candidate

    prev_seen = prev.get('seen_date', 'previous run')
    if prev_seen == now.strftime('%Y-%m-%d'):
        candidate.update({
            'verification': 'Pending',
            'verification_note': 'Seen earlier today; need next OI update',
            'first_seen_at': prev.get('first_seen_at') or prev.get('updated_at') or last_updated,
            'first_seen_date': prev.get('first_seen_date') or prev.get('seen_date'),
            'prev_oi': int(prev.get('openInterest') or 0),
            'oi_change': None,
            'oi_change_pct': None,
        })
        return candidate

    prev_oi = int(prev.get('openInterest') or 0)
    current_oi = int(candidate.get('openInterest') or 0)
    oi_change = current_oi - prev_oi
    oi_change_pct = (oi_change / prev_oi * 100) if prev_oi > 0 else None

    if oi_change >= max(100, prev_oi * 0.20):
        status = 'Confirmed OI ↑'
        note = f"OI increased vs {prev_seen}"
    elif oi_change > 0:
        status = 'Partial OI ↑'
        note = f"OI rose modestly vs {prev_seen}"
    elif oi_change == 0:
        status = 'Unconfirmed'
        note = f"OI unchanged vs {prev_seen}"
    else:
        status = 'Failed OI ↓'
        note = f"OI fell vs {prev_seen}; could be closing/spread/expired interest"

    candidate.update({
        'verification': status,
        'verification_note': note,
        'first_seen_at': prev.get('first_seen_at') or prev.get('updated_at') or last_updated,
        'first_seen_date': prev.get('first_seen_date') or prev.get('seen_date'),
        'prev_oi': prev_oi,
        'oi_change': oi_change,
        'oi_change_pct': round(oi_change_pct, 1) if oi_change_pct is not None else None,
    })
    return candidate


def save_gamma_history(gamma_data):
    GAMMA_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    contracts = {}
    for ticker, gamma in gamma_data.items():
        symbol = ticker.split(':')[-1]
        for c in gamma.get('contracts', []) or []:
            key = contract_key(symbol, c.get('contract', ''))
            contracts[key] = {
                'ticker': ticker,
                'symbol': symbol,
                'contract': c.get('contract'),
                'expiry': c.get('expiry'),
                'strike': c.get('strike'),
                'type': c.get('type', 'CALL'),
                'openInterest': c.get('openInterest'),
                'volume': c.get('volume'),
                'premium': c.get('premium'),
                'score': c.get('score'),
                'tags': c.get('tags'),
                'ask': c.get('ask'),
                'bid': c.get('bid'),
                'last': c.get('last'),
                'iv': c.get('iv'),
                'pct_otm': c.get('pct_otm'),
                'mid': c.get('mid'),
                'vol_oi': c.get('vol_oi'),
                'first_seen_at': c.get('first_seen_at') or last_updated,
                'first_seen_date': c.get('first_seen_date') or now.strftime('%Y-%m-%d'),
                'last_trade_time': c.get('last_trade_time'),
                'seen_date': now.strftime('%Y-%m-%d'),
                'updated_at': last_updated,
                'contract_call_volume': c.get('volume'),
                'call_share_equiv': c.get('call_share_equiv') or ((c.get('volume') or 0) * 100),
            }
    payload = {'updated_at': last_updated, 'contracts': contracts}
    GAMMA_HISTORY_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')

def safe_float(value, default=0.0):
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def bs_gamma(spot, strike, years_to_expiry, iv):
    if spot <= 0 or strike <= 0 or years_to_expiry <= 0 or iv <= 0:
        return 0.0
    try:
        d1 = (math.log(spot / strike) + (0.043 + 0.5 * iv * iv) * years_to_expiry) / (iv * math.sqrt(years_to_expiry))
        return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (spot * iv * math.sqrt(years_to_expiry))
    except Exception:
        return 0.0


def gamma_wall_score_class(score):
    if score >= 80:
        return "strong"
    if score >= 55:
        return "med"
    if score > 0:
        return "low"
    return "none"


def net_gex_class(net_gex_mn):
    if net_gex_mn > 0:
        return "positive"
    if net_gex_mn < 0:
        return "negative"
    return "none"


def fmt_gex_mn(net_gex_mn):
    if net_gex_mn is None:
        return "-"
    net_gex_mn = safe_float(net_gex_mn, 0.0)
    if abs(net_gex_mn) >= 100:
        return f"{net_gex_mn:+.0f}M"
    if abs(net_gex_mn) >= 10:
        return f"{net_gex_mn:+.1f}M"
    return f"{net_gex_mn:+.2f}M"


def average_dollar_volume(price_rows, lookback=20):
    if not price_rows:
        return None
    values = []
    for row in price_rows[-lookback:]:
        close = safe_float(row.get('close'), 0.0)
        volume = safe_float(row.get('volume'), 0.0)
        if close > 0 and volume > 0:
            values.append(close * volume)
    return sum(values) / len(values) if values else None


def summarize_gamma_walls(option_rows, stock_price, avg_dollar_vol=None):
    if not option_rows or stock_price <= 0:
        return {}

    by_strike = {}
    total_net = 0.0
    basis_counts = {}
    for row in option_rows:
        basis_counts[row.get('basis', 'OI')] = basis_counts.get(row.get('basis', 'OI'), 0) + 1
        strike = row['strike']
        level = by_strike.setdefault(strike, {
            'strike': strike,
            'abs_gex': 0.0,
            'net_gex': 0.0,
            'call_gex': 0.0,
            'put_gex': 0.0,
            'min_dte': row['dte'],
            'expiry': row['expiry'],
            'basis_counts': {},
        })
        abs_gex = abs(row['gex'])
        level['abs_gex'] += abs_gex
        level['net_gex'] += row['gex']
        total_net += row['gex']
        if row['type'] == 'CALL':
            level['call_gex'] += abs_gex
        else:
            level['put_gex'] += abs_gex
        row_basis = row.get('basis', 'OI')
        level['basis_counts'][row_basis] = level['basis_counts'].get(row_basis, 0) + 1
        if row['dte'] < level['min_dte']:
            level['min_dte'] = row['dte']
            level['expiry'] = row['expiry']

    levels = list(by_strike.values())
    if not levels:
        return {}

    max_abs = max(level['abs_gex'] for level in levels)
    if max_abs <= 0:
        return {}

    def level_basis(level):
        counts = level.get('basis_counts') or {}
        if not counts:
            return 'OI'
        if len(counts) == 1:
            return next(iter(counts.keys()))
        return 'MIXED'

    def basis_weight(basis):
        if basis == 'OI':
            return 1.0
        if basis == 'MIXED':
            return 0.9
        return 0.72

    def dte_weight(dte):
        if dte <= 7:
            return 1.0
        if dte <= 14:
            return 0.95
        return 0.9

    for level in levels:
        basis = level_basis(level)
        level['relative_score'] = int(round(min(100, level['abs_gex'] / max_abs * 100)))
        if avg_dollar_vol and avg_dollar_vol > 0:
            impact_ratio = level['abs_gex'] / avg_dollar_vol
            impact_bps = impact_ratio * 10_000
            raw_score = 100 * (1 - math.exp(-impact_bps / GAMMA_WALL_SCORE_ADV_BPS))
        else:
            impact_ratio = None
            impact_bps = None
            raw_score = 100 * (1 - math.exp(-level['abs_gex'] / GAMMA_WALL_ABS_GEX_FALLBACK))
        adjusted_score = raw_score * basis_weight(basis) * dte_weight(level['min_dte'])
        level['score'] = int(round(max(0, min(100, adjusted_score))))
        level['dist_pct'] = (level['strike'] / stock_price - 1) * 100
        level['distance'] = level['strike'] - stock_price
        level['abs_gex_mn'] = round(level['abs_gex'] / 1_000_000, 2)
        level['net_gex_mn'] = round(level['net_gex'] / 1_000_000, 2)
        level['impact_ratio'] = round(impact_ratio, 2) if impact_ratio is not None else None
        level['impact_bps'] = round(impact_bps, 1) if impact_bps is not None else None
        level['basis'] = basis
        level['role'] = 'call wall' if level['call_gex'] >= level['put_gex'] else 'put wall'
        distance_factor = max(0.35, 1 - min(abs(level['dist_pct']), GAMMA_WALL_MAX_DIST_PCT) / 30)
        level['rank_score'] = level['score'] * distance_factor

    def clean(level):
        if not level:
            return None
        return {
            'strike': round(level['strike'], 2),
            'score': level['score'],
            'dist_pct': round(level['dist_pct'], 1),
            'abs_gex_mn': level['abs_gex_mn'],
            'net_gex_mn': level['net_gex_mn'],
            'impact_ratio': level['impact_ratio'],
            'impact_bps': level['impact_bps'],
            'relative_score': level['relative_score'],
            'basis': level['basis'],
            'dte': int(level['min_dte']),
            'expiry': level['expiry'],
            'role': level['role'],
        }

    nearby = [l for l in levels if abs(l['dist_pct']) <= GAMMA_WALL_MAX_DIST_PCT]
    significant = [l for l in nearby if l['score'] >= GAMMA_WALL_MIN_SCORE]
    upper_pool = [l for l in significant if l['dist_pct'] >= GAMMA_WALL_MIN_DIRECTIONAL_DIST_PCT]
    lower_pool = [l for l in significant if l['dist_pct'] <= -GAMMA_WALL_MIN_DIRECTIONAL_DIST_PCT]

    upper = max(upper_pool, key=lambda l: (l['rank_score'], l['score'], -abs(l['dist_pct']))) if upper_pool else None
    lower = max(lower_pool, key=lambda l: (l['rank_score'], l['score'], -abs(l['dist_pct']))) if lower_pool else None
    pin_pool = [l for l in significant if abs(l['dist_pct']) <= 2.0]
    pin = max(pin_pool, key=lambda l: (l['score'], -abs(l['dist_pct']))) if pin_pool else None

    zero_gamma = None
    grid = [stock_price * (0.82 + i * (0.36 / 72)) for i in range(73)]
    profile = []
    for px in grid:
        total = 0.0
        for row in option_rows:
            years = max(row['dte'] / 365, 0.25 / 365)
            gamma = bs_gamma(px, row['strike'], years, row['iv'])
            sign = 1 if row['type'] == 'CALL' else -1
            total += sign * gamma * row['oi'] * 100 * px * px * 0.01
        profile.append(total)
    for i in range(1, len(grid)):
        prev, cur = profile[i - 1], profile[i]
        if prev == 0 or cur == 0 or (prev < 0 < cur) or (prev > 0 > cur):
            x0, x1 = grid[i - 1], grid[i]
            y0, y1 = prev, cur
            zero_gamma = x0 - y0 * (x1 - x0) / (y1 - y0) if y1 != y0 else x0
            break

    max_strength = max([l['score'] for l in [upper, lower, pin] if l] or [0])
    basis = 'MIXED'
    if len(basis_counts) == 1:
        basis = next(iter(basis_counts.keys()))
    return {
        'upper': clean(upper),
        'lower': clean(lower),
        'pin': clean(pin),
        'zero_gamma': round(zero_gamma, 2) if zero_gamma else None,
        'net_gex_mn': round(total_net / 1_000_000, 2),
        'max_strength': int(max_strength),
        'basis': basis,
        'sign_convention': 'OI proxy: calls contribute positive GEX and puts contribute negative GEX. Positive net GEX is treated as stabilizing dealer-long-gamma pressure; negative net GEX is treated as destabilizing dealer-short-gamma pressure. This is not observed dealer inventory.',
    }


def fmt_money(value):
    try:
        value = float(value)
    except Exception:
        return "-"
    if value >= 1_000_000:
        return f"${value/1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value/1_000:.0f}K"
    return f"${value:.0f}"


def option_mid(row):
    bid = safe_float(row.get('bid'), 0.0)
    ask = safe_float(row.get('ask'), 0.0)
    last = safe_float(row.get('lastPrice'), 0.0)
    if bid > 0 and ask > 0 and ask >= bid:
        return (bid + ask) / 2
    if last > 0:
        return last
    if ask > 0:
        return ask
    if bid > 0:
        return bid
    return 0.0


def gamma_score(dte, pct_otm, vol_oi, premium, stock_context):
    score = 20  # CALL-only setup
    tags = ['CALL']

    if dte <= 7:
        score += 20; tags.append('0-7DTE')
    elif dte <= 14:
        score += 15; tags.append('8-14DTE')
    elif dte <= 30:
        score += 5; tags.append('15-30DTE')

    if -2 <= pct_otm <= 10:
        score += 20; tags.append('near/OTM')
    elif 10 < pct_otm <= 20:
        score += 10; tags.append('far-OTM')
    elif pct_otm < -2:
        score += 5; tags.append('ITM')

    if vol_oi >= 10:
        score += 20; tags.append('Vol/OI>=10')
    elif vol_oi >= 5:
        score += 12; tags.append('Vol/OI>=5')

    if premium >= 1_000_000:
        score += 15; tags.append('Prem>$1M')
    elif premium >= 500_000:
        score += 10; tags.append('Prem>$500K')
    elif premium >= 250_000:
        score += 5; tags.append('Prem>$250K')

    if stock_context.get('near_high'):
        score += 10; tags.append('Near high')
    if stock_context.get('above_ma'):
        score += 10; tags.append('Above MA')

    return min(score, 100), ', '.join(tags)


def get_gamma_squeeze_for_ticker(ticker, stock_price, price_rows=None, history=None):
    """Find the best yfinance-visible gamma squeeze candidate for a stock.

    This is a radar only. yfinance does not reveal buy-at-ask, sweeps, BTO/STO,
    or real-time execution side.
    """
    empty = {'score': 0, 'display': '-', 'contract': '', 'tags': '', 'premium': 0, 'vol_oi': 0, 'contracts': []}
    symbol = yfinance_symbol(ticker)
    if not symbol or not stock_price or stock_price <= 0:
        return empty
    history = history or {'contracts': {}}

    stock_context = {'near_high': False, 'above_ma': False}
    if price_rows and len(price_rows) >= 20:
        closes = [r['close'] for r in price_rows if r.get('close')]
        highs = [r['high'] for r in price_rows[-20:] if r.get('high')]
        if closes:
            ma20 = sum(closes[-20:]) / min(len(closes), 20)
            stock_context['above_ma'] = stock_price >= ma20
        if highs:
            stock_context['near_high'] = stock_price >= max(highs) * 0.98

    try:
        t = yf.Ticker(symbol)
        expiries = list(t.options or [])[:GAMMA_MAX_EXPIRIES]
    except Exception:
        return empty

    today = pd.Timestamp.utcnow().normalize().tz_localize(None)
    candidates = []
    option_rows = []
    for exp in expiries:
        try:
            dte = int((pd.Timestamp(exp) - today).days)
        except Exception:
            continue
        if dte < 0 or dte > GAMMA_MAX_DTE:
            continue
        try:
            chain = t.option_chain(exp)
            calls = chain.calls
            puts = chain.puts
        except Exception:
            continue

        for opt_type, options_df, sign in [('CALL', calls, 1), ('PUT', puts, -1)]:
            if options_df is None or options_df.empty:
                continue
            for _, option_row in options_df.iterrows():
                strike = safe_float(option_row.get('strike'), 0.0)
                oi = safe_float(option_row.get('openInterest'), 0.0)
                volume = safe_float(option_row.get('volume'), 0.0)
                iv = safe_float(option_row.get('impliedVolatility'), 0.0)
                exposure_contracts = oi
                basis = 'OI'
                if exposure_contracts < GAMMA_WALL_MIN_OI and oi <= 0 and volume >= GAMMA_WALL_MIN_OI:
                    exposure_contracts = volume
                    basis = 'VOL'
                if strike <= 0 or exposure_contracts < GAMMA_WALL_MIN_OI or iv <= 0:
                    continue
                years = max(dte / 365, 0.25 / 365)
                unit_gamma = bs_gamma(stock_price, strike, years, iv)
                gex = sign * unit_gamma * exposure_contracts * 100 * stock_price * stock_price * 0.01
                if gex == 0:
                    continue
                option_rows.append({
                    'type': opt_type,
                    'strike': strike,
                    'oi': exposure_contracts,
                    'iv': iv,
                    'dte': dte,
                    'expiry': exp,
                    'gex': gex,
                    'basis': basis,
                })

        if calls is None or calls.empty:
            continue
        for _, row in calls.iterrows():
            vol = safe_float(row.get('volume'), 0.0)
            oi = safe_float(row.get('openInterest'), 0.0)
            if vol < GAMMA_MIN_VOLUME or oi < GAMMA_MIN_OI:
                continue
            vol_oi = vol / oi if oi > 0 else 0
            if vol_oi < GAMMA_MIN_VOL_OI:
                continue
            mid = option_mid(row)
            premium = vol * mid * 100
            if premium < GAMMA_MIN_PREMIUM:
                continue
            strike = safe_float(row.get('strike'), 0.0)
            if strike <= 0:
                continue
            pct_otm = (strike / stock_price - 1) * 100
            score, tags = gamma_score(dte, pct_otm, vol_oi, premium, stock_context)
            candidate = {
                'score': int(score),
                'expiry': exp,
                'strike': strike,
                'contract': f"{exp} ${strike:g}C",
                'type': 'CALL',
                'volume': int(vol),
                'call_share_equiv': int(vol * 100),
                'openInterest': int(oi),
                'vol_oi': round(vol_oi, 2),
                'mid': round(mid, 2),
                'premium': round(premium, 0),
                'premium_fmt': fmt_money(premium),
                'dte': int(dte),
                'pct_otm': round(pct_otm, 1),
                'iv': round(safe_float(row.get('impliedVolatility'), 0.0) * 100, 1),
                'last': round(safe_float(row.get('lastPrice'), 0.0), 2),
                'bid': round(safe_float(row.get('bid'), 0.0), 2),
                'ask': round(safe_float(row.get('ask'), 0.0), 2),
                'last_trade_time': fmt_yfinance_datetime(row.get('lastTradeDate')),
                'tags': tags,
            }
            candidate = verify_contract(symbol, candidate, history)
            candidates.append(candidate)

    gamma_map = summarize_gamma_walls(option_rows, stock_price, average_dollar_volume(price_rows))

    if not candidates:
        empty['map'] = gamma_map
        return empty

    candidates = sorted(candidates, key=lambda c: (c['premium'], c['score']), reverse=True)[:12]
    primary = candidates[0]
    best_score = max(c['score'] for c in candidates)
    return {
        'score': best_score,
        'display': f"{best_score} / {primary['contract']} / {primary['vol_oi']:.1f}x / {primary['premium_fmt']}",
        'contract': primary['contract'],
        'tags': primary['tags'],
        'premium': primary['premium'],
        'premium_fmt': primary['premium_fmt'],
        'vol_oi': primary['vol_oi'],
        'dte': primary['dte'],
        'pct_otm': primary['pct_otm'],
        'contract_call_volume': int(primary.get('volume') or 0),
        'call_share_equiv': int(primary.get('call_share_equiv') or ((primary.get('volume') or 0) * 100)),
        'contracts': candidates,
        'map': gamma_map,
    }



def gamma_data_from_history(history):
    grouped = {}
    for item in history.get('contracts', {}).values():
        ticker = item.get('ticker')
        contract = item.get('contract')
        if not ticker or not contract:
            continue
        expiry = item.get('expiry') or ''
        try:
            dte = int((pd.Timestamp(expiry) - pd.Timestamp.utcnow().normalize().tz_localize(None)).days)
        except Exception:
            dte = None
        if dte is not None and dte < 0:
            continue
        c = {
            'score': int(item.get('score') or 0),
            'expiry': expiry,
            'strike': item.get('strike'),
            'contract': contract,
            'type': item.get('type', 'CALL'),
            'volume': item.get('volume'),
            # Always recompute from this contract's volume; older history stored chain-level call_share_equiv.
            'call_share_equiv': (item.get('volume') or 0) * 100,
            'openInterest': item.get('openInterest'),
            'vol_oi': item.get('vol_oi') or 0,
            'mid': item.get('mid'),
            'premium': item.get('premium') or 0,
            'premium_fmt': fmt_money(item.get('premium') or 0),
            'dte': dte,
            'pct_otm': item.get('pct_otm') or 0,
            'iv': item.get('iv'),
            'last': item.get('last'),
            'bid': item.get('bid'),
            'ask': item.get('ask'),
            'tags': item.get('tags') or 'historical fallback',
            'first_seen_at': item.get('first_seen_at') or item.get('updated_at') or '-',
            'first_seen_date': item.get('first_seen_date') or item.get('seen_date'),
            'last_trade_time': item.get('last_trade_time') or '-',
            'verification': 'Stale fallback',
            'verification_note': f"Using last saved record from {item.get('seen_date', 'history')}; live yfinance options returned no candidates",
            'prev_oi': item.get('openInterest'),
            'oi_change': None,
            'oi_change_pct': None,
        }
        grouped.setdefault(ticker, []).append(c)
    out = {}
    for ticker, contracts in grouped.items():
        contracts = sorted(contracts, key=lambda c: (c.get('premium') or 0, c.get('score') or 0), reverse=True)[:12]
        if not contracts:
            continue
        primary = contracts[0]
        best_score = max(c.get('score') or 0 for c in contracts)
        contract_call_volume = int(primary.get('volume') or 0)
        call_share_equiv = contract_call_volume * 100
        out[ticker] = {
            'score': best_score,
            'display': f"{best_score} / {primary.get('contract')} / stale / {primary.get('premium_fmt')}",
            'contract': primary.get('contract'),
            'tags': primary.get('tags'),
            'premium': primary.get('premium'),
            'premium_fmt': primary.get('premium_fmt'),
            'vol_oi': primary.get('vol_oi'),
            'dte': primary.get('dte'),
            'pct_otm': primary.get('pct_otm'),
            'contract_call_volume': contract_call_volume,
            'call_share_equiv': call_share_equiv,
            'contracts': contracts,
        }
    return out

def get_iv_for_ticker(ticker):
    symbol = yfinance_symbol(ticker)
    if not symbol:
        return None
    # Retry logic for rate limiting
    for attempt in range(5):
        try:
            t = yf.Ticker(symbol)
            stock_price = t.info.get('regularMarketPrice', 0)
            if stock_price <= 0:
                return None
            opt = t.option_chain()
            if opt.calls is None or len(opt.calls) == 0:
                return None
            active = opt.calls
            if len(active) == 0:
                return None
            active = active.copy()
            active['dist'] = abs(active['strike'] - stock_price)
            atm_idx = active['dist'].idxmin()
            iv = active.loc[atm_idx].get('impliedVolatility', 0)
            return iv * 100 if iv > 0 else None
        except Exception as e:
            if attempt < 2:
                time.sleep(2)  # Wait before retry
                continue
            return None
    return None


def normalize_short_pct(value):
    value = safe_float(value, None)
    if value is None or value <= 0:
        return None
    return value * 100 if value <= 1 else value


def fmt_date_from_epoch(value):
    value = safe_float(value, 0)
    if value <= 0:
        return '-'
    try:
        return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).strftime('%Y-%m-%d')
    except Exception:
        return '-'


def gamma_wall_line(label, wall, adr):
    if not wall:
        return f'<div class="gamma-wall-row gamma-wall-empty"><span>{label}</span><strong>-</strong><em>-</em></div>'
    score = int(wall.get('score') or 0)
    score_class = gamma_wall_score_class(score)
    dist_pct = safe_float(wall.get('dist_pct'), 0.0)
    adr_mult = abs(dist_pct) / adr if adr and adr > 0 else None
    adr_text = f"{adr_mult:.1f} ADR" if adr_mult is not None else "ADR -"
    role = html.escape(str(wall.get('role') or 'wall'))
    expiry = html.escape(str(wall.get('expiry') or '-'))
    basis = html.escape(str(wall.get('basis') or '-'))
    rel_score = wall.get('relative_score')
    rel_text = f" | relative {int(rel_score)}/100" if rel_score is not None else ""
    impact_bps = wall.get('impact_bps')
    impact_text = f" | {safe_float(impact_bps, 0):.1f} bps ADV" if impact_bps is not None else ""
    title = html.escape(
        f"{label}: {wall.get('strike')} | strength {score}/100{rel_text}{impact_text} | "
        f"{dist_pct:+.1f}% | {adr_text} | {role} | {wall.get('abs_gex_mn')}M abs GEX | Basis {basis} | {expiry}",
        quote=True,
    )
    return (
        f'<div class="gamma-wall-row" title="{title}">'
        f'<span>{label}</span>'
        f'<strong>{safe_float(wall.get("strike"), 0):g}</strong>'
        f'<em>{dist_pct:+.1f}% / {adr_text}</em>'
        f'<b class="gamma-wall-score gamma-wall-{score_class}">{score}</b>'
        f'</div>'
    )


def render_gamma_wall_card(gamma_map, adr):
    if not gamma_map or not gamma_map.get('max_strength'):
        return ''
    zero = gamma_map.get('zero_gamma')
    zero_text = f"ZG {safe_float(zero, 0):g}" if zero else "ZG -"
    net = safe_float(gamma_map.get('net_gex_mn'), 0.0)
    net_display = fmt_gex_mn(gamma_map.get('net_gex_mn'))
    net_class = net_gex_class(net)
    basis = html.escape(str(gamma_map.get('basis') or 'OI'))
    convention = html.escape(str(gamma_map.get('sign_convention') or ''), quote=True)
    return f"""<div class="gamma-wall-card" title="{convention}">
        <div class="gamma-wall-head">
            <span>Gamma Wall</span>
            <div class="gamma-wall-head-metrics">
                <b class="gamma-wall-net gex-{net_class}">GEX {net_display}</b>
                <strong>{int(gamma_map.get('max_strength') or 0)}</strong>
            </div>
        </div>
        {gamma_wall_line('PIN', gamma_map.get('pin'), adr)}
        {gamma_wall_line('UP', gamma_map.get('upper'), adr)}
        {gamma_wall_line('DN', gamma_map.get('lower'), adr)}
        <div class="gamma-wall-foot">{zero_text} · Basis {basis} · ADV adj</div>
    </div>"""



def get_benchmark_6m_perf(symbol='SPY'):
    try:
        hist = yf.Ticker(symbol).history(period='6mo', auto_adjust=True)
        if hist is None or hist.empty or len(hist) < 2:
            return 0.0
        start_close = safe_float(hist['Close'].iloc[0], 0.0)
        end_close = safe_float(hist['Close'].iloc[-1], 0.0)
        if start_close <= 0 or end_close <= 0:
            return 0.0
        return (end_close / start_close - 1) * 100
    except Exception as e:
        print(f"WARNING: failed to fetch {symbol} 6M benchmark: {e}")
        return 0.0


def fmt_yfinance_datetime(value):
    if value is None or value == '':
        return '-'
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize('UTC')
        ts = ts.tz_convert('Asia/Hong_Kong')
        return ts.strftime('%Y-%m-%d %H:%M HK')
    except Exception:
        return str(value)

def get_short_interest_for_ticker(ticker):
    empty = {'short_float_pct': None, 'short_ratio': None, 'shares_short': None, 'shares_short_prior': None, 'shares_short_change': None, 'shares_short_change_pct': None, 'float_shares': None, 'date_short_interest': '-', 'fuel': 'None', 'business_summary': ''}
    symbol = yfinance_symbol(ticker)
    if not symbol:
        return empty
    for attempt in range(3):
        try:
            info = yf.Ticker(symbol).info or {}
            short_float = normalize_short_pct(info.get('shortPercentOfFloat'))
            short_ratio = safe_float(info.get('shortRatio'), None)
            shares_short = safe_float(info.get('sharesShort'), None)
            shares_prior = safe_float(info.get('sharesShortPriorMonth'), None)
            float_shares = safe_float(info.get('floatShares'), None)
            change = None
            change_pct = None
            if shares_short is not None and shares_prior and shares_prior > 0:
                change = shares_short - shares_prior
                change_pct = change / shares_prior * 100
            fuel = 'None'
            if (short_float is not None and short_float >= 20) or (short_ratio is not None and short_ratio >= 5):
                fuel = 'Very High'
            elif (short_float is not None and short_float >= 10) or (short_ratio is not None and short_ratio >= 3):
                fuel = 'High'
            elif (short_float is not None and short_float >= 5) or (short_ratio is not None and short_ratio >= 2):
                fuel = 'Medium'
            return {
                'short_float_pct': round(short_float, 1) if short_float is not None else None,
                'short_ratio': round(short_ratio, 1) if short_ratio is not None else None,
                'shares_short': int(shares_short) if shares_short is not None else None,
                'shares_short_prior': int(shares_prior) if shares_prior is not None else None,
                'shares_short_change': int(change) if change is not None else None,
                'shares_short_change_pct': round(change_pct, 1) if change_pct is not None else None,
                'float_shares': int(float_shares) if float_shares is not None else None,
                'date_short_interest': fmt_date_from_epoch(info.get('dateShortInterest')),
                'fuel': fuel,
                'business_summary': str(info.get('longBusinessSummary') or '').strip(),
            }
        except Exception:
            if attempt < 2:
                time.sleep(1.5)
                continue
            return empty
    return empty

def get_price_and_adr(ticker, days=90, period=None):
    symbol = yfinance_symbol(ticker)
    if not symbol:
        return None, None
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period=period or f"{days}d")
        if hist.empty or len(hist) < 30:
            return None, None
        
        # Calculate ADR (20-day Average Daily Range)
        adr = 0
        if len(hist) >= 20:
            ranges = []
            for i in range(-20, 0):
                high = safe_float(hist.iloc[i].get('High'), float('nan'))
                low = safe_float(hist.iloc[i].get('Low'), float('nan'))
                close = safe_float(hist.iloc[i].get('Close'), float('nan'))
                if all(math.isfinite(v) for v in (high, low, close)) and close > 0:
                    daily_range = ((high - low) / close) * 100
                    if math.isfinite(daily_range):
                        ranges.append(daily_range)
            if ranges:
                adr = sum(ranges) / len(ranges)
        
        data = []
        for idx, row in hist.iterrows():
            open_px = safe_float(row.get('Open'), float('nan'))
            high_px = safe_float(row.get('High'), float('nan'))
            low_px = safe_float(row.get('Low'), float('nan'))
            close_px = safe_float(row.get('Close'), float('nan'))
            if not all(math.isfinite(v) and v > 0 for v in (open_px, high_px, low_px, close_px)):
                continue
            volume = safe_float(row.get('Volume'), 0.0)
            if not math.isfinite(volume) or volume < 0:
                volume = 0.0
            data.append({
                'time': int(idx.timestamp()),
                'open': open_px,
                'high': high_px,
                'low': low_px,
                'close': close_px,
                'volume': int(volume)
            })
        return data, adr
    except:
        return None, None

price_data = {}
adr_data = {}
business_summaries = {}

def load_pattern_bars(ticker):
    prices, adr = get_price_and_adr(ticker, period='2y')
    if not prices:
        return None
    price_data[ticker] = prices[-90:]
    if adr and adr > 0:
        adr_data[ticker] = adr
    time.sleep(0.15)
    return prices

print("Scanning liquid US common stocks...")
try:
    liquid, universe_stats = scan_liquid_universe()
    print("Scanning names within 0.1% of the 52-week high...")
    high52, high_stats = scan_near_52w()
except ScanError:
    raise
except Exception as exc:
    raise ScanError(f"Market scan failed: {exc}") from exc

if not liquid.empty and 'dist_52w' in liquid.columns:
    universe_near = liquid.loc[liquid['dist_52w'].notna() & (liquid['dist_52w'] <= NEAR_52W_MAX_DIST)]
else:
    universe_near = liquid.iloc[0:0]
print(
    f"52W cross-check: dedicated liquid matches={high_stats['liquid_near']} "
    f"universe liquid within {NEAR_52W_MAX_DIST}%={len(universe_near)}"
)
if high_stats['liquid_near'] == 0 and len(universe_near) >= 20:
    raise ScanError(
        "52-week scan returned no liquid names, but the universe has many within 0.1% of the 52-week high."
    )

assert_publishable(
    universe_total=universe_stats['scanner_total'],
    fetched=universe_stats['fetched'],
    liquid_count=universe_stats['liquid'],
    scored_rs=universe_stats['scored_rs'],
    board_count=None,
)

all_stocks, _class_stats = classify_board(liquid, high52, load_pattern_bars)
all_stocks = all_stocks[all_stocks['name'].notna()]
all_stocks = all_stocks[all_stocks['name'].astype(str).str.strip() != '']
all_stocks = all_stocks[~all_stocks['name'].astype(str).str.lower().isin(['nan', 'none'])]

spy_perf = get_benchmark_6m_perf('SPY')
print(f"SPY 6M benchmark (context only, not the RS figure): {spy_perf:.1f}%")

assert_publishable(
    universe_total=universe_stats['scanner_total'],
    fetched=universe_stats['fetched'],
    liquid_count=universe_stats['liquid'],
    scored_rs=universe_stats['scored_rs'],
    board_count=len(all_stocks),
)

print(f"Total unique stocks: {len(all_stocks)}")
vcp_count = int(all_stocks['is_vcp'].sum())
ql_count = int(all_stocks['is_ql'].sum())
htf_count = int(all_stocks['is_htf'].sum())
high52_count = int(all_stocks['is_52w_high'].sum())
print(f"Actual - VCP: {vcp_count}, Qullamaggie: {ql_count}, High Tight Flag: {htf_count}, 52W High: {high52_count}")

def clean_industry_label(value):
    text = str(value or '-').strip()
    if not text or text.lower() in ('nan', 'none'):
        return '-'
    return text


industry_counts = {}
for _, row in all_stocks.iterrows():
    sector_label = clean_industry_label(row.get('sector', '-'))
    industry_label = clean_industry_label(row.get('industry', '-'))
    key = (sector_label, industry_label)
    industry_counts[key] = industry_counts.get(key, 0) + 1

industry_options = ['<option value="all">All Industries</option>']
for (sector_label, industry_label), count in sorted(industry_counts.items(), key=lambda item: (item[0][0], item[0][1])):
    if sector_label == '-' and industry_label == '-':
        continue
    value = html.escape(f"{sector_label}||{industry_label}", quote=True)
    label = html.escape(f"{sector_label} - {industry_label} ({count})")
    industry_options.append(f'<option value="{value}">{label}</option>')
industry_filter_options = "\n        ".join(industry_options)

# Pattern checks already filled price_data / adr_data for VCP, QL, and HTF names.
print(f"Fetching price history for names not already loaded ({len(price_data)} cached)...")
for i, ticker in enumerate(all_stocks['ticker'].tolist()):
    if ticker in price_data:
        continue
    prices, adr = get_price_and_adr(ticker, 90)
    if prices:
        price_data[ticker] = prices
    if adr and adr > 0:
        adr_data[ticker] = adr
    if (i + 1) % 20 == 0:
        print(f"  {i+1}/{len(all_stocks)}...")
print(f"Got price data for {len(price_data)} stocks, ADR for {len(adr_data)} stocks")

print("Fetching IV data...")
iv_data = {}
for i, ticker in enumerate(all_stocks['ticker'].tolist()):
    iv = get_iv_for_ticker(ticker)
    if iv is not None:
        if iv is not None and iv > 0:
            iv_data[ticker] = iv
    if (i + 1) % 10 == 0:
        print(f"  IV: {i+1}/{len(all_stocks)} stocks...")
    time.sleep(0.5)  # Rate limiting - increased delay
print(f"Got IV for {len(iv_data)} stocks")


print("Fetching gamma squeeze candidates...")
gamma_history = load_gamma_history()
gamma_data = {}
for i, row in enumerate(all_stocks.itertuples()):
    ticker = getattr(row, 'ticker')
    close = safe_float(getattr(row, 'close', 0), 0.0)
    gamma = get_gamma_squeeze_for_ticker(ticker, close, price_data.get(ticker, []), gamma_history)
    if gamma and (gamma.get('score', 0) > 0 or (gamma.get('map') or {}).get('max_strength', 0) > 0):
        gamma_data[ticker] = gamma
    if (i + 1) % 10 == 0:
        print(f"  Gamma: {i+1}/{len(all_stocks)} stocks...")
    time.sleep(0.25)
gamma_count = sum(1 for g in gamma_data.values() if g.get('score', 0) >= 60)
gamma_wall_count = sum(1 for g in gamma_data.values() if (g.get('map') or {}).get('max_strength', 0) >= 60)
has_live_gamma_contracts = any((g.get('contracts') or []) for g in gamma_data.values())
if has_live_gamma_contracts:
    save_gamma_history(gamma_data)
    print(f"Saved gamma history: {GAMMA_HISTORY_PATH}")
elif gamma_data:
    print("No live gamma contracts; preserving saved gamma contract history")
else:
    gamma_data = gamma_data_from_history(gamma_history)
    gamma_count = sum(1 for g in gamma_data.values() if g.get('score', 0) >= 60)
    gamma_wall_count = sum(1 for g in gamma_data.values() if (g.get('map') or {}).get('max_strength', 0) >= 60)
    print("WARNING: no live gamma candidates; using saved gamma history fallback")
print(f"Got gamma candidates for {len(gamma_data)} stocks ({gamma_count} GS >= 60, {gamma_wall_count} wall strength >= 60)")

print("Fetching short interest data...")
short_data = {}
for i, ticker in enumerate(all_stocks['ticker'].tolist()):
    si = get_short_interest_for_ticker(ticker)
    if si.get('business_summary'):
        business_summaries[ticker] = si['business_summary']
    if si.get('short_float_pct') is not None or si.get('short_ratio') is not None:
        short_data[ticker] = si
    if (i + 1) % 10 == 0:
        print(f"  Short interest: {i+1}/{len(all_stocks)} stocks...")
    time.sleep(0.25)
short_fuel_count = sum(1 for s in short_data.values() if s.get('fuel') in ('High', 'Very High'))
print(f"Got short interest for {len(short_data)} stocks ({short_fuel_count} high/very high fuel)")

def make_row(row, price_data, anim_delay=0):
    ticker = str(row['ticker'])
    ticker_display = html.escape(ticker)
    symbol = ticker.split(':')[-1].upper()
    symbol_attr = html.escape(symbol, quote=True)
    raw_name = clean_industry_label(row.get('name', ''))
    if raw_name == '-':
        raw_name = symbol
    name = html.escape(raw_name)
    summary_full = clip_text(business_summaries.get(ticker, ''), 800)
    summary_short = clip_text(summary_full, 280)
    summary_html = ''
    if summary_short:
        summary_html = (
            f'<div class="stock-desc" title="{html.escape(summary_full, quote=True)}">{html.escape(summary_short)}</div>'
        )
    tv_href = html.escape(
        'https://www.tradingview.com/chart/?symbol=' + urllib.parse.quote(ticker, safe=''),
        quote=True,
    )
    ticker_link = (
        f'<a class="ticker-link" href="{tv_href}" target="_blank" rel="noopener noreferrer">{ticker_display}</a>'
    )
    close = float(row['close'])
    dist_52w = safe_float(row.get('dist_52w', row.get('dist_high')), 0.0)
    high_52w = safe_float(row.get('price_52_week_high'), 0.0)
    is_52w_high = bool(row.get('is_52w_high', False))
    perf_6m = safe_float(row['Perf.6M'], 0.0)
    tv_adr = safe_float(row.get('ADR'), 0.0)
    yf_adr = adr_data.get(ticker)
    adr = tv_adr if tv_adr > 0 else safe_float(yf_adr, 0.0)
    adr_title = f"TradingView ADR {tv_adr:.1f}%. Pattern gate is at least {ADR_MIN_PCT:.0f}%."
    if yf_adr:
        adr_title += f" yfinance 20-day ADR {safe_float(yf_adr, 0):.1f}%."
    adr_title = html.escape(adr_title, quote=True)
    rs_number = safe_float(row.get('rs_rating', row.get('RS')), None)
    if rs_number is None:
        rs_display = '-'
        rs_attr = 0.0
        rs_color = 'negative'
    else:
        rs_display = f'{rs_number:.0f}'
        rs_attr = rs_number
        rs_color = 'positive' if rs_number >= VCP_MIN_RS_PERCENTILE else 'negative'
    rs_title = html.escape(
        'Percentile RS proxy (1-99) of weighted 1-month, 3-month, 6-month, and 12-month performance '
        'inside the liquid universe. Not the IBD RS Rating, and not 6-month return minus SPY.',
        quote=True,
    )
    ql_pct = safe_float(row.get('ql_pct'), None)
    perf_title = 'TradingView 6-month percent change.'
    if ql_pct is not None:
        perf_title += (
            f' Qullamaggie blend percentile {ql_pct:.0f} '
            f'(top {QL_TOP_PERCENT:.0f}% starts at {QL_PERCENTILE_MIN:.0f}).'
        )
    perf_title = html.escape(perf_title, quote=True)
    chart_id = "chart_" + ticker.replace(':', '_')
    price_json = json.dumps(price_data.get(ticker, []), allow_nan=False)
    iv_val = iv_data.get(ticker)
    iv_attr = f'{iv_val:.4f}' if iv_val is not None else '0'
    if iv_val is not None and iv_val >= 1:
        iv_pct = iv_val  # already * 100
        if iv_pct >= 100:
            iv_class = "high"
        elif iv_pct >= 50:
            iv_class = "med"
        else:
            iv_class = "low"
        iv_display = f"{iv_pct:.0f}%"
    else:
        iv_class = "none"
        iv_display = "-"
    gamma = gamma_data.get(ticker, {'score': 0, 'display': '-', 'contract': '', 'tags': '', 'contracts': []})
    gamma_score_val = int(gamma.get('score', 0) or 0)
    gamma_class = "high" if gamma_score_val >= 80 else "med" if gamma_score_val >= 60 else "low" if gamma_score_val > 0 else "none"
    gamma_display = str(gamma_score_val) if gamma_score_val > 0 else "-"
    gamma_contracts = gamma.get('contracts', []) or []
    primary_contract = gamma_contracts[0] if gamma_contracts else {}
    gamma_title = html.escape(str(gamma.get('display', '-')) + (" | " + str(gamma.get('tags', '')) if gamma.get('tags') else ""), quote=True)
    gamma_json = html.escape(json.dumps(gamma_contracts), quote=False)
    gamma_map = gamma.get('map') or {}
    gamma_wall_score_val = int(gamma_map.get('max_strength', 0) or 0)
    gamma_wall_class = gamma_wall_score_class(gamma_wall_score_val)
    gamma_wall_display = str(gamma_wall_score_val) if gamma_wall_score_val > 0 else "-"
    gamma_wall_parts = []
    for wall_label, wall_key in (('PIN', 'pin'), ('UP', 'upper'), ('DN', 'lower')):
        wall = gamma_map.get(wall_key) or {}
        if wall:
            impact = wall.get('impact_bps')
            impact_text = f", {safe_float(impact, 0):.1f} bps ADV" if impact is not None else ""
            gamma_wall_parts.append(
                f"{wall_label} {safe_float(wall.get('strike'), 0):g} "
                f"({safe_float(wall.get('dist_pct'), 0):+.1f}%, score {int(wall.get('score') or 0)}{impact_text})"
            )
    if gamma_map:
        zero = gamma_map.get('zero_gamma')
        zero_part = f"ZG {safe_float(zero, 0):g}" if zero else "ZG -"
        gamma_wall_parts.append(f"{zero_part} | Basis {gamma_map.get('basis') or 'OI'}")
    gamma_wall_title = html.escape(" | ".join(gamma_wall_parts) if gamma_wall_parts else "No gamma wall", quote=True)
    gamma_wall_card = render_gamma_wall_card(gamma_map, adr)
    net_gex_mn = gamma_map.get('net_gex_mn') if gamma_map else None
    net_gex_value = safe_float(net_gex_mn, 0.0) if net_gex_mn is not None else 0.0
    if primary_contract:
        verify_text = str(primary_contract.get('verification', 'Pending'))
        verify_class = html.escape(verify_text.lower().split()[0])
        first_seen = html.escape(str(primary_contract.get('first_seen_at', '-')))
        gamma_card = f"""<button class=\"gamma-contract-card\" type=\"button\" onclick=\"openGammaDetails(this)\">
            <span class=\"gamma-card-kicker\">Largest GS Contract</span>
            <span class=\"gamma-card-main\">{html.escape(str(primary_contract.get('contract', '-')))}</span>
            <span class=\"gamma-card-stats\">Prem {html.escape(str(primary_contract.get('premium_fmt', '-')))} · Vol/OI {safe_float(primary_contract.get('vol_oi'), 0):.1f}x · DTE {int(primary_contract.get('dte', 0) or 0)} · {safe_float(primary_contract.get('pct_otm'), 0):+.1f}% OTM</span>
            <span class=\"gamma-card-verify verify-{verify_class}\">{html.escape(verify_text)}</span>
            <span class=\"gamma-card-time\">First seen: {first_seen}</span>
            <span class=\"gamma-card-hint\">Tap for {len(gamma_contracts)} records</span>
        </button><script type=\"application/json\" class=\"gamma-data\">{gamma_json}</script>"""
    else:
        gamma_card = '' 
    si = short_data.get(ticker, {})
    short_float = si.get('short_float_pct')
    short_ratio = si.get('short_ratio')
    short_fuel = si.get('fuel', 'None')
    short_display = f"{short_float:.1f}%" if short_float is not None else "-"
    short_ratio_display = f"DTC {short_ratio:.1f}" if short_ratio is not None else "DTC -"
    short_change = si.get('shares_short_change_pct')
    short_change_display = f"{short_change:+.1f}%" if short_change is not None else "-"
    short_date = si.get('date_short_interest', '-')
    short_class = "very-high" if short_fuel == 'Very High' else "high" if short_fuel == 'High' else "med" if short_fuel == 'Medium' else "none"
    short_title = html.escape(f"Short float: {short_display} | {short_ratio_display} | Change: {short_change_display} | Date: {short_date}", quote=True)
    float_shares = si.get('float_shares')
    contract_call_volume = int(primary_contract.get('volume') or gamma.get('contract_call_volume') or 0)
    call_share_equiv = int(primary_contract.get('call_share_equiv') or gamma.get('call_share_equiv') or contract_call_volume * 100)
    call_float_pct = (call_share_equiv / float_shares * 100) if float_shares and float_shares > 0 and primary_contract else None
    call_float_display = f"{call_float_pct:.2f}%" if call_float_pct is not None else "-"
    call_float_class = "extreme" if call_float_pct is not None and call_float_pct >= 10 else "high" if call_float_pct is not None and call_float_pct >= 5 else "med" if call_float_pct is not None and call_float_pct >= 2 else "low" if call_float_pct is not None and call_float_pct > 0 else "none"
    if float_shares:
        call_float_title = html.escape(f"Displayed contract volume share-equivalent: {call_share_equiv:,} shares ({contract_call_volume:,} contracts) / float {float_shares:,}. Not delta-adjusted.", quote=True)
    else:
        call_float_title = "Call/Float unavailable: missing float shares or no displayed gamma contract. Not delta-adjusted."
    sector_raw = clean_industry_label(row.get('sector', '-'))
    industry_raw = clean_industry_label(row.get('industry', '-'))
    sector = html.escape(sector_raw)
    industry = html.escape(industry_raw)
    sector_attr = html.escape(sector_raw, quote=True)
    industry_attr = html.escape(industry_raw, quote=True)
    
    dist_color = "positive" if dist_52w <= VCP_MAX_DIST_52W else "negative"
    perf_color = "positive" if perf_6m > 0 else "negative"
    ath = safe_float(row.get('High.All'), 0.0)
    ath_bit = ''
    if ath > 0 and close > 0:
        ath_bit = f' All-time-high distance is {(ath - close) / ath * 100:.1f}% and is not the gate.'
    dist_title = html.escape(
        f'Percent below the 52-week high (${high_52w:.2f}).{ath_bit}',
        quote=True,
    )
    high52_title = html.escape(
        f"Close ${close:.2f} is within {NEAR_52W_MAX_DIST:.1f}% of TradingView's 52-week high ${high_52w:.2f}. All-time high is not the gate.",
        quote=True,
    )

    def note_title(text, fallback):
        raw = str(text or '').strip()
        if raw.lower() in ('', 'nan', 'none'):
            raw = fallback
        return html.escape(raw, quote=True)

    # Build strategy badges and classes
    badges = []
    classes = []
    strat_list = []
    if row.get('is_vcp', False):
        badges.append(
            f'<span class="strategy-badge strategy-vcp" title="{note_title(row.get("vcp_note"), "VCP: trend template plus contraction and volume dry-up. Not a SEPA earnings screen.")}">VCP</span>'
        )
        classes.append('strategy-vcp')
        strat_list.append('VCP')
    if row.get('is_ql', False):
        badges.append(
            f'<span class="strategy-badge strategy-qullamaggie" title="{note_title(row.get("ql_note"), "Qullamaggie: liquid-universe leadership percentile, ADR floor, and a tight pause near the 10/20-day averages.")}">Qullamaggie</span>'
        )
        classes.append('strategy-qullamaggie')
        strat_list.append('Qullamaggie')
    if row.get('is_htf', False):
        badges.append(
            f'<span class="strategy-badge strategy-htf" title="{note_title(row.get("htf_note"), "HTF means High Tight Flag: an 80%+ pole and a short flag. It does not mean higher timeframe.")}">HTF</span>'
        )
        classes.append('strategy-htf')
        strat_list.append('HTF')
    if is_52w_high:
        badges.append(f'<span class="strategy-badge strategy-high52" title="{high52_title}">52W High</span>')
        classes.append('strategy-high52')
        strat_list.append('High52')
    if gamma_score_val >= 60:
        badges.append(f'<span class="strategy-badge strategy-gamma">GS {gamma_score_val}</span>')
        classes.append('strategy-gamma')
        strat_list.append('Gamma')
    if short_fuel in ('High', 'Very High'):
        badge_text = 'SI VH' if short_fuel == 'Very High' else 'SI High'
        badges.append(f'<span class="strategy-badge strategy-short">{badge_text}</span>')
        classes.append('strategy-short')
        strat_list.append('ShortFuel')
    if call_float_pct is not None and call_float_pct >= 5:
        badges.append(f'<span class="strategy-badge strategy-callfloat">CF {call_float_pct:.1f}%</span>')
        classes.append('strategy-callfloat')
        strat_list.append('CallFloat')
    if gamma_wall_score_val >= 60:
        badges.append(f'<span class="strategy-badge strategy-gammawall">GW {gamma_wall_score_val}</span>')
        classes.append('strategy-gammawall')
        strat_list.append('GammaWall')
    
    badges_str = ''.join(badges)
    classes_str = ' '.join(classes)
    data_strategies = ','.join(strat_list)
    optional_cards = "\n".join(card for card in (gamma_card, gamma_wall_card) if card)
    
    return f'''
    <div class="stock-row {classes_str}" data-symbol="{symbol_attr}" data-sector="{sector_attr}" data-industry="{industry_attr}" data-strategies="{data_strategies}" data-rs="{rs_attr:.1f}" data-iv="{iv_attr}" data-gamma="{gamma_score_val}" data-wall="{gamma_wall_score_val}" data-gexnet="{net_gex_value:.2f}" data-short="{short_float if short_float is not None else 0}" data-callfloat="{call_float_pct if call_float_pct is not None else 0}" data-price="{close}" data-dist="{dist_52w:.1f}">
        <div class="stock-header">
            <div class="stock-name">{name}</div>
            <div class="stock-ticker">{ticker_link} {badges_str}</div>
            <div class="stock-sector">{sector} - {industry}</div>
            {summary_html}
        </div>
        <div class="mobile-primary-metrics">
            <div class="stock-price">${close:.2f}</div>
            <div class="metric iv-metric">IV<br><span class="iv-value iv-{iv_class}">{iv_display}</span></div>
            <div class="metric gamma-metric" title="{gamma_title}">GS<br><span class="gamma-value gamma-{gamma_class}">{gamma_display}</span></div>
            <div class="metric wall-metric" title="{gamma_wall_title}">GW<br><span class="gamma-wall-value gamma-wall-{gamma_wall_class}">{gamma_wall_display}</span></div>
        </div>
{optional_cards}
        <div class="secondary-metrics">
            <div class="metric" title="{dist_title}">52W<br><span class="{dist_color}">{dist_52w:.1f}%</span></div>
            <div class="metric" title="{perf_title}">6M<br><span class="{perf_color}">{perf_6m:.1f}%</span></div>
            <div class="metric" title="{rs_title}">RS<br><span class="{rs_color}">{rs_display}</span></div>
            <div class="metric" title="{adr_title}">ADR<br>{adr:.1f}%</div>
            <div class="metric short-metric" title="{short_title}">Short<br><span class="short-value short-{short_class}">{short_display}</span></div>
            <div class="metric callfloat-metric" title="{call_float_title}">Call/Float<br><span class="callfloat-value callfloat-{call_float_class}">{call_float_display}</span></div>
        </div>
        <div class="chart-cell" id="{chart_id}"></div>
        <script type="application/json" class="chart-data">{price_json}</script>
    </div>'''

all_rows = ''.join([make_row(row, price_data, i*0.05) for i, (_, row) in enumerate(all_stocks.iterrows())])

strength = sector_strength(all_stocks['sector'].tolist())
pills = []
for sector_label, count in strength:
    pills.append(f'<span class="sector-pill">{html.escape(sector_label)} <b>{count}</b></span>')
if not pills:
    pills.append('<span class="sector-pill">No sector data</span>')
sector_summary_html = (
    '<div class="sector-summary">'
    '<div class="sector-summary-row"><span class="filter-label">Sector strength</span>'
    f'<div class="sector-pills">{"".join(pills)}</div></div>'
    '<p class="sector-note">Counts of names on this board (VCP, Qullamaggie, High Tight Flag, 52-week high). No pinned watchlist. 52W is distance to the 52-week high.</p>'
    '</div>'
)

html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Trading Screener</title>
<script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
@import url('https://api.fontshare.com/v2/css?f[]=satoshi@400,500,700&display=swap');

*{{box-sizing:border-box;margin:0;padding:0}}

:root{{
    --bg-primary: #0a0b0f;
    --bg-secondary: #12141a;
    --bg-card: #1a1d26;
    --accent: #00ff88;
    --accent-dim: #00ff8833;
    --text-primary: #f0f2f5;
    --text-secondary: #b7bdc9;
    --text-muted: #858b98;
    --border: #2a2e3a;
    --red: #ff4757;
    --orange: #ff9f43;
    --blue: #3b82f6;
    --purple: #a855f7;
}}

body{{
    font-family:'Satoshi',-apple-system,BlinkMacSystemFont,sans-serif;
    background:var(--bg-primary);
    color:var(--text-primary);
    min-height:100vh;
    line-height:1.5;
}}

/* Grain overlay */
body::before{{
    content:'';
    position:fixed;
    top:0;left:0;width:100%;height:100%;
    background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E");
    opacity:0.03;
    pointer-events:none;
    z-index:9999;
}}

.header{{
    background:linear-gradient(135deg, var(--bg-secondary) 0%, var(--bg-primary) 100%);
    padding:12px 24px;
    position:sticky;
    top:0;
    z-index:101;
    display:flex;
    justify-content:space-between;
    align-items:center;
    border-bottom:1px solid var(--border);
    box-shadow:0 2px 12px rgba(0,0,0,0.3);
}}

.header::before{{
    content:'';
    position:absolute;
    left:0;top:0;bottom:0;
    width:3px;
    background:var(--accent);
    box-shadow:0 0 12px var(--accent);
}}

.header h1{{
    font-size:16px;
    font-weight:700;
    color:var(--text-primary);
    letter-spacing:-0.3px;
}}

.header h1 span{{
    color:var(--accent);
}}

.header-meta{{
    display:flex;
    flex-direction:row;
    align-items:center;
    gap:16px;
}}

.header-meta p{{
    font-size:10px;
    color:var(--text-secondary);
}}

.header-meta p span{{
    color:var(--accent);
}}

.info-btn{{
    background:transparent;
    color:var(--accent);
    border:1px solid var(--accent);
    padding:6px 12px;
    cursor:pointer;
    border-radius:4px;
    font-size:11px;
    font-weight:600;
    font-family:inherit;
    transition:all 0.2s;
}}

.info-btn:hover{{
    background:var(--accent);
    color:var(--bg-primary);
    box-shadow:0 0 20px var(--accent-dim);
}}

.filter-section{{
    background:var(--bg-secondary);
    padding:16px 24px;
    position:sticky;
    top:0;
    z-index:100;
    border-bottom:1px solid var(--border);
    display:flex;
    gap:12px;
    flex-wrap:wrap;
    align-items:center;
}}

.filter-label{{
    color:var(--text-muted);
    font-size:11px;
    font-weight:600;
    text-transform:uppercase;
    letter-spacing:1px;
}}

.filter-select{{
    background:var(--bg-card);
    color:var(--text-primary);
    border:1px solid var(--border);
    padding:10px 36px 10px 14px;
    cursor:pointer;
    border-radius:6px;
    font-size:13px;
    font-weight:500;
    font-family:inherit;
    transition:all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    appearance:none;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238b919e' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
    background-repeat:no-repeat;
    background-position:right 12px center;
    min-width:160px;
}}

.filter-select:hover{{
    border-color:var(--accent);
}}

.filter-select:focus{{
    outline:none;
    border-color:var(--accent);
    box-shadow:0 0 16px var(--accent-dim);
}}

.filter-input{{
    background:var(--bg-card);
    color:var(--text-primary);
    border:1px solid var(--border);
    padding:10px 14px;
    border-radius:6px;
    font-size:13px;
    font-weight:500;
    font-family:inherit;
    min-width:180px;
    transition:all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}}

.filter-input::placeholder{{
    color:var(--text-muted);
}}

.filter-input:hover{{
    border-color:var(--accent);
}}

.filter-input:focus{{
    outline:none;
    border-color:var(--accent);
    box-shadow:0 0 16px var(--accent-dim);
}}

.result-count{{
    color:var(--text-muted);
    font-size:12px;
    margin-left:auto;
    white-space:nowrap;
}}

.content{{padding:20px 24px}}

.col-header{{
    display:none;
}}

.stock-row{{
    display:none;
    background:var(--bg-card);
    border:1px solid var(--border);
    border-radius:12px;
    padding:16px 20px;
    margin-bottom:12px;
    gap:16px;
    align-items:center;
    flex-wrap:wrap;
    transition:all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    position:relative;
    overflow:visible;
}}

.stock-row.visible{{
    display:flex;
    animation:slideIn 0.4s cubic-bezier(0.4, 0, 0.2, 1) forwards;
}}

.stock-row::before{{
    content:'';
    position:absolute;
    left:0;top:0;bottom:0;
    width:3px;
    background:var(--border);
    transition:background 0.2s;
}}

.stock-row:hover{{
    border-color:var(--accent);
    transform:translateX(4px);
    box-shadow:0 4px 24px rgba(0,0,0,0.3);
}}

.stock-row:hover::before{{
    background:var(--accent);
}}

@keyframes slideIn{{
    from{{
        opacity:0;
        transform:translateX(-20px);
    }}
    to{{
        opacity:1;
        transform:translateX(0);
    }}
}}

.mobile-primary-metrics{{
    display:flex;
    align-items:center;
    gap:14px;
    flex:0 0 auto;
}}

.secondary-metrics{{
    display:flex;
    align-items:center;
    gap:14px;
    flex:0 0 auto;
}}

.stock-header{{
    flex:1 1 170px;
    min-width:160px;
}}

.stock-info{{
    flex:1.5;
    min-width:180px;
}}

.stock-name{{
    font-weight:700;
    font-size:16px;
    color:#ffffff;
    margin-bottom:4px;
}}

.stock-ticker{{
    color:var(--text-secondary);
    font-size:12px;
    display:flex;
    align-items:center;
    gap:6px;
    flex-wrap:wrap;
}}

.stock-sector{{
    color:var(--text-muted);
    font-size:10px;
    margin-top:4px;
}}

.stock-desc{{
    color:var(--text-secondary);
    font-size:11px;
    line-height:1.35;
    margin-top:6px;
    display:-webkit-box;
    -webkit-line-clamp:2;
    -webkit-box-orient:vertical;
    overflow:hidden;
}}

.ticker-link{{
    color:var(--accent);
    font-weight:700;
    text-decoration:none;
}}

.ticker-link:hover{{
    text-decoration:underline;
}}

.sector-summary{{
    background:var(--bg-secondary);
    border-bottom:1px solid var(--border);
    padding:4px 24px 12px;
}}

.sector-summary-row{{
    display:flex;
    gap:10px;
    align-items:flex-start;
    flex-wrap:wrap;
}}

.sector-pills{{
    display:flex;
    flex-wrap:wrap;
    gap:6px;
    flex:1;
}}

.sector-pill{{
    background:var(--bg-card);
    border:1px solid var(--border);
    border-radius:999px;
    padding:3px 8px;
    font-size:11px;
    color:var(--text-secondary);
}}

.sector-pill b{{
    color:var(--accent);
    margin-left:4px;
}}

.sector-note{{
    color:var(--text-muted);
    font-size:11px;
    margin-top:6px;
}}

.stock-price{{
    font-size:20px;
    font-weight:700;
    color:var(--text-primary);
    min-width:80px;
    text-align:right;
}}

.metric{{
    text-align:center;
    min-width:60px;
}}

.metric-label{{
    font-size:9px;
    color:var(--text-muted);
    text-transform:uppercase;
    letter-spacing:0.5px;
    margin-bottom:2px;
}}

.metric-value{{
    font-size:14px;
    font-weight:600;
    color:var(--text-primary);
}}

.iv-value{{
    font-size:15px;
    font-weight:700;
    padding:4px 10px;
    border-radius:4px;
    display:inline-block;
}}

.iv-high{{background:var(--red);color:#fff}}
.iv-med{{background:var(--orange);color:#000}}
.iv-low{{background:var(--accent);color:#000}}
.iv-none{{color:var(--text-muted)}}

.positive{{color:var(--accent)}}
.negative{{color:var(--red)}}

.strategy-badge{{
    padding:3px 8px;
    border-radius:4px;
    font-size:9px;
    font-weight:700;
    text-transform:uppercase;
    letter-spacing:0.5px;
}}

.strategy-badge.strategy-vcp{{background:var(--blue)}}
.strategy-badge.strategy-qullamaggie{{background:var(--red)}}
.strategy-badge.strategy-htf{{background:var(--accent);color:#000}}
.strategy-badge.strategy-high52{{background:#f8fafc;color:#0f172a}}
.strategy-badge.strategy-gamma{{background:var(--purple);color:#fff}}
.strategy-badge.strategy-gammawall{{background:#22c55e;color:#001307}}
.strategy-badge.strategy-short{{background:#f59e0b;color:#000}}
.strategy-badge.strategy-callfloat{{background:#14b8a6;color:#001311}}

.gamma-value,.gamma-wall-value,.gex-value{{
    font-size:15px;
    font-weight:700;
    padding:4px 10px;
    border-radius:4px;
    display:inline-block;
}}
.gamma-high{{background:var(--purple);color:#fff}}
.gamma-med{{background:var(--blue);color:#fff}}
.gamma-low{{background:var(--bg-secondary);color:var(--text-secondary);border:1px solid var(--border)}}
.gamma-none{{color:var(--text-muted)}}

.gex-positive{{background:rgba(34,197,94,0.18);color:#86efac;border:1px solid rgba(34,197,94,0.35)}}
.gex-negative{{background:rgba(255,71,87,0.16);color:#ff9aa5;border:1px solid rgba(255,71,87,0.35)}}
.gex-none{{color:var(--text-muted)}}

.short-value{{
    font-size:13px;
    font-weight:800;
    padding:3px 8px;
    border-radius:4px;
    display:inline-block;
}}
.short-very-high{{background:var(--red);color:#fff}}
.short-high{{background:var(--orange);color:#000}}
.short-med{{background:rgba(255,159,67,0.18);color:var(--orange);border:1px solid rgba(255,159,67,0.35)}}
.short-none{{color:var(--text-muted)}}

.callfloat-value{{
    font-size:13px;
    font-weight:800;
    padding:3px 8px;
    border-radius:4px;
    display:inline-block;
}}
.callfloat-extreme{{background:var(--red);color:#fff}}
.callfloat-high{{background:#14b8a6;color:#001311}}
.callfloat-med{{background:rgba(20,184,166,0.18);color:#5eead4;border:1px solid rgba(20,184,166,0.35)}}
.callfloat-low{{color:var(--text-secondary)}}
.callfloat-none{{color:var(--text-muted)}}

.gamma-contract-card{{
    width:260px;
    min-width:240px;
    background:linear-gradient(135deg, rgba(168,85,247,0.16), rgba(59,130,246,0.10));
    border:1px solid rgba(168,85,247,0.55);
    color:var(--text-primary);
    border-radius:12px;
    padding:10px 12px;
    text-align:left;
    cursor:pointer;
    font-family:inherit;
    display:flex;
    flex-direction:column;
    gap:4px;
    transition:all 0.2s;
}}
.gamma-contract-card:hover{{
    border-color:var(--purple);
    box-shadow:0 0 22px rgba(168,85,247,0.20);
    transform:translateY(-1px);
}}
.gamma-card-kicker{{
    font-size:9px;
    text-transform:uppercase;
    letter-spacing:1px;
    color:#c4b5fd;
    font-weight:800;
}}
.gamma-card-main{{
    font-size:14px;
    font-weight:900;
    color:#ffffff;
    letter-spacing:-0.2px;
}}
.gamma-card-stats{{
    font-size:11px;
    line-height:1.35;
    color:#d8dce6;
}}
.gamma-card-hint{{
    font-size:10px;
    color:var(--accent);
    font-weight:800;
}}
.gamma-card-time{{
    font-size:10px;
    color:#d8dce6;
    font-weight:700;
}}
.gamma-card-verify{{
    align-self:flex-start;
    font-size:10px;
    font-weight:900;
    padding:4px 8px;
    border-radius:999px;
    background:rgba(255,255,255,0.08);
    color:#eef1f7;
}}
.verify-confirmed{{background:rgba(0,255,136,0.18);color:var(--accent);border:1px solid rgba(0,255,136,0.35)}}
.verify-partial{{background:rgba(255,159,67,0.18);color:var(--orange);border:1px solid rgba(255,159,67,0.35)}}
.verify-pending{{background:rgba(59,130,246,0.18);color:#93c5fd;border:1px solid rgba(59,130,246,0.35)}}
.verify-unconfirmed,.verify-failed{{background:rgba(255,71,87,0.16);color:#ff9aa5;border:1px solid rgba(255,71,87,0.35)}}
.gamma-detail-list{{
    display:flex;
    flex-direction:column;
    gap:10px;
}}
.gamma-detail-row{{
    background:rgba(255,255,255,0.035);
    border:1px solid var(--border);
    border-radius:12px;
    padding:12px;
}}
.gamma-detail-top{{
    display:flex;
    justify-content:space-between;
    gap:10px;
    align-items:center;
    margin-bottom:8px;
}}
.gamma-detail-contract{{
    font-size:15px;
    font-weight:900;
    color:#fff;
}}
.gamma-detail-score{{
    background:var(--purple);
    color:#fff;
    border-radius:999px;
    padding:4px 8px;
    font-size:11px;
    font-weight:900;
}}
.gamma-detail-grid{{
    display:grid;
    grid-template-columns:repeat(3, 1fr);
    gap:8px;
}}
.gamma-detail-cell{{
    background:rgba(0,0,0,0.18);
    border-radius:8px;
    padding:7px 8px;
}}
.gamma-detail-label{{
    display:block;
    font-size:9px;
    color:var(--text-muted);
    text-transform:uppercase;
    letter-spacing:0.6px;
}}
.gamma-detail-value{{
    display:block;
    font-size:12px;
    color:var(--text-primary);
    font-weight:800;
    margin-top:2px;
}}

.gamma-wall-card{{
    width:260px;
    min-width:240px;
    background:linear-gradient(135deg, rgba(34,197,94,0.13), rgba(168,85,247,0.08));
    border:1px solid rgba(34,197,94,0.45);
    border-radius:12px;
    padding:10px 12px;
    display:flex;
    flex-direction:column;
    gap:6px;
}}
.gamma-wall-head{{
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:8px;
    font-size:9px;
    text-transform:uppercase;
    letter-spacing:1px;
    color:#bbf7d0;
    font-weight:900;
}}
.gamma-wall-head-metrics{{
    display:flex;
    align-items:center;
    gap:6px;
    letter-spacing:0;
    text-transform:none;
}}
.gamma-wall-net{{
    font-size:10px;
    font-weight:900;
    padding:2px 7px;
    border-radius:999px;
    white-space:nowrap;
}}
.gamma-wall-head strong{{
    background:#22c55e;
    color:#001307;
    border-radius:999px;
    padding:2px 7px;
    font-size:11px;
}}
.gamma-wall-row{{
    display:grid;
    grid-template-columns:32px 58px 1fr 34px;
    gap:6px;
    align-items:center;
    font-size:11px;
    color:#d8dce6;
}}
.gamma-wall-row span{{
    color:var(--text-muted);
    font-size:9px;
    font-weight:900;
    letter-spacing:0.7px;
}}
.gamma-wall-row strong{{
    color:#fff;
    font-size:14px;
    font-weight:900;
}}
.gamma-wall-row em{{
    font-style:normal;
    color:var(--text-secondary);
    white-space:nowrap;
}}
.gamma-wall-score{{
    text-align:center;
    border-radius:999px;
    padding:2px 6px;
    font-size:10px;
    font-weight:900;
}}
.gamma-wall-strong{{background:#22c55e;color:#001307}}
.gamma-wall-med{{background:rgba(34,197,94,0.18);color:#86efac;border:1px solid rgba(34,197,94,0.35)}}
.gamma-wall-low{{background:rgba(255,255,255,0.08);color:var(--text-secondary)}}
.gamma-wall-none{{color:var(--text-muted)}}
.gamma-wall-empty strong,.gamma-wall-empty em{{color:var(--text-muted)}}
.gamma-wall-foot{{
    font-size:10px;
    color:var(--text-muted);
    font-weight:800;
    border-top:1px solid rgba(255,255,255,0.08);
    padding-top:5px;
}}

.chart-cell{{
    flex:1;
    min-width:140px;
    height:60px;
    border-radius:6px;
    overflow:hidden;
    border:1px solid var(--border);
    touch-action:pan-y;
}}
.chart-cell canvas{{
    pointer-events:none;
}}

/* Modal */
.modal{{
    display:none;
    position:fixed;
    top:0;left:0;width:100%;height:100%;
    background:rgba(0,0,0,0.85);
    z-index:1000;
    justify-content:center;
    align-items:center;
    backdrop-filter:blur(8px);
}}

.modal.show{{display:flex}}

.modal-content{{
    background:linear-gradient(145deg, var(--bg-secondary), var(--bg-card));
    border:1px solid var(--border);
    border-radius:16px;
    padding:32px;
    max-width:520px;
    width:90%;
    max-height:85vh;
    overflow-y:auto;
    box-shadow:0 24px 64px rgba(0,0,0,0.5);
}}

.modal-title{{
    font-size:22px;
    font-weight:700;
    color:var(--text-primary);
    margin-bottom:24px;
    display:flex;
    align-items:center;
    gap:10px;
}}

.modal-title::before{{
    content:'📊';
}}

.modal-section{{
    margin-bottom:20px;
}}

.modal-section h3{{
    font-size:14px;
    font-weight:700;
    color:var(--accent);
    margin-bottom:8px;
    text-transform:uppercase;
    letter-spacing:1px;
}}

.modal-section p{{
    font-size:13px;
    color:var(--text-secondary);
    line-height:1.6;
}}

.modal-close{{
    background:var(--accent);
    color:var(--bg-primary);
    border:none;
    padding:14px 24px;
    border-radius:8px;
    cursor:pointer;
    font-size:14px;
    font-weight:700;
    font-family:inherit;
    margin-top:24px;
    width:100%;
    transition:all 0.2s;
}}

.modal-close:hover{{
    box-shadow:0 0 24px var(--accent-dim);
    transform:translateY(-2px);
}}

/* Mobile responsive */
@media (max-width:768px){{
    body{{
        background:#07080b;
    }}

    .header{{
        position:relative;
        flex-direction:row;
        gap:10px;
        text-align:left;
        padding:10px 14px;
        align-items:center;
    }}
    .header::before{{width:100%;height:2px;top:0}}
    .header h1{{font-size:15px;white-space:nowrap}}
    .header-meta{{
        margin-left:auto;
        gap:4px;
        align-items:flex-end;
        flex-direction:column;
    }}
    .header-meta p{{font-size:9px;line-height:1.1}}
    .info-btn{{
        padding:6px 8px;
        font-size:0;
        min-width:34px;
    }}
    .info-btn::before{{
        content:'ℹ️';
        font-size:14px;
    }}

    .filter-section{{
        position:sticky;
        top:0;
        z-index:100;
        padding:10px 12px;
        gap:8px;
        display:grid;
        grid-template-columns:1fr 1fr;
        background:rgba(18,20,26,0.96);
        backdrop-filter:blur(14px);
    }}
    .filter-label{{
        grid-column:1 / -1;
        font-size:9px;
        letter-spacing:1.6px;
        color:var(--accent);
    }}
    .filter-select{{
        width:100%;
        min-width:0;
        padding:10px 28px 10px 10px;
        font-size:12px;
        border-radius:10px;
    }}
    .filter-input{{
        width:100%;
        min-width:0;
        padding:10px;
        font-size:12px;
        border-radius:10px;
    }}
    .result-count{{
        grid-column:1 / -1;
        margin-left:0;
        font-size:11px;
    }}

    .content{{padding:12px}}
    .sector-summary{{padding:8px 12px 10px}}
    .sector-note{{font-size:10px}}

    .stock-row{{
        flex-wrap:wrap;
        display:none;
        padding:14px;
        margin-bottom:10px;
        gap:10px;
        border-radius:16px;
        background:linear-gradient(145deg, rgba(26,29,38,0.98), rgba(14,16,22,0.98));
    }}
    .stock-row.visible{{
        display:grid;
        grid-template-columns:1fr;
        animation:none;
    }}
    .stock-row:hover{{
        transform:none;
    }}

    .stock-header{{
        min-width:0;
        width:100%;
    }}
    .stock-name{{
        font-size:16px;
        line-height:1.15;
        margin-bottom:6px;
        white-space:nowrap;
        overflow:hidden;
        text-overflow:ellipsis;
    }}
    .stock-ticker{{
        font-size:12px;
        gap:5px;
    }}
    .stock-sector{{
        font-size:10px;
        white-space:nowrap;
        overflow:hidden;
        text-overflow:ellipsis;
    }}

    .mobile-primary-metrics{{
        width:100%;
        display:grid;
        grid-template-columns:1fr 68px 68px 68px;
        gap:8px;
        align-items:stretch;
        order:2;
    }}
    .stock-price{{
        min-width:0;
        text-align:left;
        font-size:22px;
        display:flex;
        align-items:center;
        padding:8px 10px;
        background:rgba(255,255,255,0.035);
        border:1px solid var(--border);
        border-radius:12px;
    }}
    .iv-metric,.gamma-metric,.wall-metric{{
        min-width:0;
        padding:7px 6px;
        background:rgba(255,255,255,0.035);
        border:1px solid var(--border);
        border-radius:12px;
    }}
    .iv-value,.gamma-value,.gamma-wall-value,.gex-value{{
        font-size:13px;
        padding:3px 6px;
        margin-top:2px;
    }}

    .secondary-metrics{{
        width:100%;
        order:3;
        display:grid;
        grid-template-columns:repeat(6, 1fr);
        gap:7px;
    }}
    .metric{{
        min-width:0;
        font-size:10px;
        color:var(--text-muted);
        padding:7px 5px;
        background:rgba(255,255,255,0.025);
        border:1px solid rgba(42,46,58,0.8);
        border-radius:10px;
    }}
    .metric span{{
        font-size:12px;
        font-weight:700;
        color:inherit;
    }}

    .strategy-badge{{
        padding:3px 6px;
        font-size:8px;
        border-radius:999px;
    }}

    .gamma-contract-card{{
        order:4;
        width:100%;
        min-width:0;
        padding:12px;
        border-radius:14px;
        background:linear-gradient(135deg, rgba(168,85,247,0.22), rgba(59,130,246,0.12));
    }}
    .gamma-card-kicker{{font-size:10px}}
    .gamma-card-main{{font-size:16px}}
    .gamma-card-stats{{font-size:12px;color:#eef1f7}}
    .gamma-card-hint{{font-size:11px}}
    .gamma-card-time{{font-size:11px}}
    .gamma-card-verify{{font-size:11px}}

    .gamma-wall-card{{
        order:5;
        width:100%;
        min-width:0;
        padding:12px;
        border-radius:14px;
    }}
    .gamma-wall-row{{
        grid-template-columns:34px 64px 1fr 38px;
        font-size:12px;
    }}
    .gamma-wall-row strong{{font-size:15px}}

    .chart-cell{{
        order:6;
        flex:1 1 100%;
        width:100%;
        min-width:0;
        height:72px;
        margin-top:0;
        border-radius:12px;
    }}

    .modal-content{{
        width:94%;
        padding:22px;
        border-radius:18px;
    }}
}}

@media (max-width:380px){{
    .mobile-primary-metrics{{
        grid-template-columns:1fr 64px 64px;
    }}
    .stock-price{{font-size:19px}}
    .filter-section{{grid-template-columns:1fr}}
    .result-count{{grid-column:1}}
}}
</style>
</head>
<body>
<div class="header">
    <h1>Trading <span>Screener</span></h1>
    <div class="header-meta">
        <p>SPY 6M context: <span>{spy_perf:.1f}%</span> | <span>{len(all_stocks)}</span> stocks</p>
        <p>Updated: {last_updated}</p>
    </div>
    <button class="info-btn" onclick="showInfo()">ℹ️ Info</button>
</div>
<div class="filter-section">
    <span class="filter-label">Filters:</span>
    <select class="filter-select" id="strategyFilter" onchange="filterChanged()">
        <option value="all">All ({len(all_stocks)})</option>
        <option value="VCP">VCP ({vcp_count})</option>
        <option value="Qullamaggie">Qullamaggie ({ql_count})</option>
        <option value="HTF">High Tight Flag ({htf_count})</option>
        <option value="High52">52W High ({high52_count})</option>
        <option value="Gamma">Gamma Squeeze ({gamma_count})</option>
        <option value="GammaWall">Gamma Wall ({gamma_wall_count})</option>
        <option value="ShortFuel">Short Fuel ({short_fuel_count})</option>
        <option value="CallFloat">Call/Float</option>
    </select>
    <select class="filter-select" id="industryFilter" onchange="filterChanged()">
        {industry_filter_options}
    </select>
    <select class="filter-select" id="sortFilter" onchange="sortChanged()">
        <option value="rs-desc">RS ↓ (High to Low)</option>
        <option value="rs-asc">RS ↑ (Low to High)</option>
        <option value="iv-desc">IV ↓ (High to Low)</option>
        <option value="iv-asc">IV ↑ (Low to High)</option>
        <option value="gamma-desc">GS ↓ (Gamma Score)</option>
        <option value="wall-desc">GW ↓ (Wall Strength)</option>
        <option value="gex-abs-desc">|GEX| ↓ (Net GEX)</option>
        <option value="short-desc">Short Float ↓</option>
        <option value="callfloat-desc">Call/Float ↓</option>
        <option value="price-desc">Price ↓</option>
        <option value="price-asc">Price ↑</option>
        <option value="dist-asc">52W ↑ (Near high)</option>
    </select>
    <input class="filter-input" id="symbolSearch" type="search" placeholder="Search symbol..." autocomplete="off" spellcheck="false" oninput="filterChanged()">
    <span class="result-count" id="resultCount">Showing {len(all_stocks)} / {len(all_stocks)}</span>
</div>
{sector_summary_html}
<div class="col-header">
    <div style="flex:1;min-width:150px">Stock</div>
    <div style="width:80px">Price</div>
    <div style="width:60px">IV</div>
    <div style="width:60px">52W</div>
    <div style="width:60px">6M</div>
    <div style="width:60px">RS</div>
    <div style="width:60px">ADR</div>
    <div style="flex:1;min-width:150px">Chart</div>
</div>
<div class="modal" id="infoModal">
    <div class="modal-content">
        <div class="modal-title">📊 Strategy Criteria</div>
        <div class="modal-section">
            <h3>VCP (Volatility Contraction Pattern)</h3>
            <p>Price-only Minervini trend template plus contraction. Not a SEPA earnings screen.<br>
            • Close ≥ ${MIN_PRICE:.0f} and ADV$ ≥ ${ADV_MIN_DOLLARS:,.0f} (30-day average share volume × close)<br>
            • TradingView ADR ≥ {ADR_MIN_PCT:.0f}%<br>
            • Close &gt; SMA50 &gt; SMA150 &gt; SMA200, and at least {VCP_MIN_ABOVE_LOW_PCT:.0f}% above the 52-week low<br>
            • Within {VCP_MAX_DIST_52W:.0f}% of the 52-week high. All-time high is not the gate<br>
            • SMA200 higher than it was about a month ago, from yfinance daily bars<br>
            • RS proxy ≥ {VCP_MIN_RS_PERCENTILE:.0f}: percentile of 20% 1M + 40% 3M + 20% 6M + 20% 12M inside the liquid universe. Not the IBD RS Rating<br>
            • Last 10 sessions' range at least {100 - VCP_RANGE_TIGHTEN * 100:.0f}% tighter than the range 20 sessions earlier, and 10-session volume ≤ {VCP_DRYUP_RATIO * 100:.0f}% of the prior 20 sessions<br>
            Skipped in v1: earnings growth, EPS acceleration, sponsorship.</p>
        </div>
        <div class="modal-section">
            <h3>Qullamaggie</h3>
            <p>Leadership is a universe percentile, not a fixed 6-month return.<br>
            • Same price, ADV$, and ADR ≥ {ADR_MIN_PCT:.0f}% floor. No ADR ceiling<br>
            • Blended 45% 1M + 35% 3M + 20% 6M performance at or above the {QL_PERCENTILE_MIN:.0f}th percentile of the liquid universe (top {QL_TOP_PERCENT:.0f}%)<br>
            • Within {QL_MAX_DIST_52W:.0f}% of the 52-week high<br>
            • Close within 5% of the 10- or 20-day average, above the 20-day, with the 20-day rising and the 10-day not rolling over<br>
            • 15-session high-low range ≤ {QL_MAX_RANGE_15D:.0f}% and not more than 15% wider than the previous 15 sessions</p>
        </div>
        <div class="modal-section">
            <h3>HTF (High Tight Flag)</h3>
            <p>Pole plus a short flag. This is not a higher-time-frame filter, and there is no 6-month return cap.<br>
            • Same price, ADV$, and ADR ≥ {ADR_MIN_PCT:.0f}% floor<br>
            • Pole ≥ {HTF_POLE_MIN_PCT:.0f}% from the lowest close of the first 5 sessions of a {HTF_POLE_MIN_DAYS}–{HTF_POLE_MAX_DAYS} session thrust to that thrust's high. The thrust has to finish near its high<br>
            • The most recent {HTF_FLAG_MIN_DAYS}–{HTF_FLAG_MAX_DAYS} sessions are the flag. Drawdown from the pole high ≤ {HTF_MAX_DRAWDOWN_PCT:.0f}%, and the flag does not run more than 5% through the pole high<br>
            • Flag average volume ≤ {HTF_MAX_FLAG_VOL_RATIO * 100:.0f}% of pole average volume<br>
            • Close near the 10- or 20-day average and within {HTF_MAX_DIST_52W:.0f}% of the 52-week high</p>
        </div>
        <div class="modal-section">
            <h3>52W High</h3>
            <p>Close is within {NEAR_52W_MAX_DIST:.1f}% of TradingView's 52-week high (at least 99.9% of that high). The scanner applies that test directly, then this job pages through every match and sorts by distance. It does not take the largest 1,000 names by market cap and then check the high. Liquidity is close ≥ ${MIN_PRICE:.0f} and ADV$ ≥ ${ADV_MIN_DOLLARS:,.0f}. OTC names are excluded. ADR is not required here, so quieter large caps can still show up.</p>
        </div>
        <div class="modal-section">
            <h3>52W distance and RS</h3>
            <p>The 52W figure is percent below the 52-week high. All-time high is shown only as a note and is not a gate for VCP, Qullamaggie, or High Tight Flag.<br>
            RS is the 1–99 percentile proxy above. SPY's 6-month change in the header is market context, not the RS calculation.</p>
        </div>
        <div class="modal-section">
            <h3>Names, links, and sectors</h3>
            <p>The title is TradingView's company name (`description`). The paragraph under it is yfinance `longBusinessSummary` from the same info request as short interest, when Yahoo returns one. The ticker opens the TradingView chart.<br>
            Sector strength counts names on this board. There is no pinned watchlist.</p>
        </div>
        <div class="modal-section">
            <h3>Field limits</h3>
            <p>ADV$ is last close times TradingView `average_volume_30d_calc`, not a true average of daily dollar volume. SMA levels come from TradingView. SMA200 slope, the VCP contraction, the Qullamaggie pause, and the flag geometry use yfinance daily bars; a name with no usable history does not get that badge. TradingView has no 9-month performance field, so the RS proxy uses 12-month for the oldest weight. A loose 1M/3M/6M floor only limits which High Tight Flag names get a history download. It has no upper bound.</p>
        </div>
        <div class="modal-section">
            <h3>GS (Gamma Squeeze Candidate)</h3>
            <p>Short-dated CALL Vol/OI spike with near/OTM strike, estimated premium, and stock momentum context.<br>First Seen is scanner detection time; Last Trade is yfinance contract lastTradeDate. yfinance cannot confirm buy-at-ask, sweeps, BTO/STO, or whale intent — use UW/flow data to confirm.</p>
        </div>
        <div class="modal-section">
            <h3>Gamma Wall</h3>
            <p>Major upper/lower high-gamma strike and current pin candidate from 30DTE option chains. Wall strength is 0-100 using abs-GEX as basis points of the stock's 20D average dollar volume, then adjusted for data basis and expiry. This makes 100 much harder to reach and avoids showing a nearby low-quality strike as a wall. Basis OI uses open interest; Basis VOL is an intraday volume-gamma fallback when yfinance returns zero OI. Zero gamma is a regime boundary, not a magnet. yfinance data is a proxy and does not reveal true dealer inventory.</p>
        </div>
        <div class="modal-section">
            <h3>GEX (Net Gamma Exposure)</h3>
            <p>OI-based net gamma proxy in millions of dollars per 1% move. Calls are counted as positive GEX and puts as negative GEX. Positive net GEX is treated as stabilizing dealer-long-gamma pressure, where hedging tends to sell rallies and buy dips. Negative net GEX is treated as destabilizing dealer-short-gamma pressure, where hedging can chase moves and increase volatility. This is not observed dealer inventory; confirm with trade-side flow when available.</p>
        </div>
        <div class="modal-section">
            <h3>Short Fuel</h3>
            <p>Short Float % and days-to-cover from yfinance. High fuel = Short Float ≥ 10% or DTC ≥ 3; Very High = Short Float ≥ 20% or DTC ≥ 5.<br>Data can be delayed/stale, so treat it as squeeze context, not a real-time covering signal.</p>
        </div>
        <div class="modal-section">
            <h3>Call/Float</h3>
            <p>Displayed gamma contract volume × 100 ÷ float shares. This explains how large the shown contract is relative to float; it is not an independent signal and is not delta-adjusted. ≥2% is notable, ≥5% high, ≥10% extreme.</p>
        </div>
        <button class="modal-close" onclick="closeInfo()">Got it ✓</button>
    </div>
</div>
<div class="modal" id="gammaModal">
    <div class="modal-content gamma-modal-content">
        <div class="modal-title" id="gammaModalTitle">Gamma Contracts</div>
        <div class="gamma-detail-list" id="gammaDetailList"></div>
        <button class="modal-close" onclick="closeGammaDetails()">Close ✓</button>
    </div>
</div>
<div class="content">
{all_rows}
</div>
<script>
var chartInstances = {{}};

function createChart(container, data) {{
    if (!data || data.length === 0) return null;
    var chart = LightweightCharts.createChart(container, {{
        width: container.clientWidth || 180,
        height: 60,
        layout: {{ background: {{ type: 'solid', color: '#1e222d' }}, textColor: '#d1d4dc' }},
        grid: {{ vertLines: {{ color: '#2a2e39' }}, horzLines: {{ color: '#2a2e39' }} }},
        timeScale: {{ visible: false, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true }},
        rightPriceScale: {{ visible: false }},
        handleScroll: false,
        handleScale: false,
        kineticScroll: {{ touch: false, mouse: false }},
        trackingMode: {{ exitMode: 1 }},
        crosshair: {{
            mode: 2,
            vertLine: {{ visible: false, labelVisible: false }},
            horzLine: {{ visible: false, labelVisible: false }}
        }}
    }});
    var candleSeries = chart.addCandlestickSeries({{
        upColor: '#26a69a', downColor: '#ef5350',
        borderUpColor: '#26a69a', borderDownColor: '#ef5350',
        wickUpColor: '#26a69a', wickDownColor: '#ef5350'
    }});
    candleSeries.setData(data);
    
    // Add volume histogram
    var volData = data.map(function(d) {{ return {{ time: d.time, value: d.volume || 0, color: d.close >= d.open ? '#26a69a80' : '#ef535080' }}; }});
    var volSeries = chart.addHistogramSeries({{
        priceFormat: {{ type: 'volume' }},
        priceScaleId: ''
    }});
    volSeries.setData(volData);
    volSeries.priceScale().applyOptions({{ scaleMargins: {{ top: 0.85, bottom: 0 }} }});
    
    chart.timeScale().fitContent();
    return chart;
}}

function createChartForCell(cell) {{
    var chartId = cell.id;
    if (chartInstances[chartId]) return;
    
    var dataEl = cell.nextElementSibling;
    if (!dataEl || !dataEl.classList.contains('chart-data')) return;
    
    try {{
        var data = JSON.parse(dataEl.textContent);
        if (!data || data.length === 0) return;
        
        var chart = createChart(cell, data);
        if (chart) {{
            chartInstances[chartId] = chart;
        }}
    }} catch(e) {{}}
}}


var currentSort = 'rs-desc';

function sortChanged() {{
    currentSort = document.getElementById('sortFilter').value;
    showAllRows();
}}

function getSymbolQuery() {{
    var input = document.getElementById('symbolSearch');
    return input ? input.value.trim().toUpperCase() : '';
}}

function getIndustryFilter() {{
    var select = document.getElementById('industryFilter');
    return select ? select.value : 'all';
}}

function getRowIndustry(row) {{
    var sector = row.getAttribute('data-sector') || '-';
    var industry = row.getAttribute('data-industry') || '-';
    return {{
        key: sector + '||' + industry,
        label: sector + ' - ' + industry
    }};
}}

function rowMatchesBaseFilters(row, strategyFilter, symbolQuery) {{
    var strategies = row.getAttribute('data-strategies') || '';
    var stratList = strategies.split(',').map(function(s) {{ return s.trim(); }});
    var matchesStrategy = strategyFilter === 'all' || stratList.indexOf(strategyFilter) !== -1;
    var symbol = (row.getAttribute('data-symbol') || '').toUpperCase();
    var matchesSymbol = !symbolQuery || symbol.indexOf(symbolQuery) !== -1;
    return matchesStrategy && matchesSymbol;
}}

function refreshIndustryOptions(strategyFilter, symbolQuery) {{
    var select = document.getElementById('industryFilter');
    if (!select) return 'all';

    var selected = select.value || 'all';
    var counts = {{}};
    var labels = {{}};
    var allRows = document.querySelectorAll('.stock-row');

    allRows.forEach(function(row) {{
        if (!rowMatchesBaseFilters(row, strategyFilter, symbolQuery)) return;
        var info = getRowIndustry(row);
        if (info.key === '-||-') return;
        counts[info.key] = (counts[info.key] || 0) + 1;
        labels[info.key] = info.label;
    }});

    var keys = Object.keys(counts).sort(function(a, b) {{
        return labels[a].localeCompare(labels[b]);
    }});

    select.innerHTML = '';
    var allOption = document.createElement('option');
    allOption.value = 'all';
    allOption.textContent = 'All Industries';
    select.appendChild(allOption);

    keys.forEach(function(key) {{
        var option = document.createElement('option');
        option.value = key;
        option.textContent = labels[key] + ' (' + counts[key] + ')';
        select.appendChild(option);
    }});

    if (selected !== 'all' && counts[selected]) {{
        select.value = selected;
    }} else {{
        select.value = 'all';
    }}
    return select.value;
}}

function rowMatchesFilters(row, strategyFilter, industryFilter, symbolQuery) {{
    if (!rowMatchesBaseFilters(row, strategyFilter, symbolQuery)) return false;
    var info = getRowIndustry(row);
    var matchesIndustry = industryFilter === 'all' || industryFilter === info.key;
    return matchesIndustry;
}}

function updateResultCount(count, total) {{
    var counter = document.getElementById('resultCount');
    if (counter) {{
        counter.textContent = 'Showing ' + count + ' / ' + total;
    }}
}}

function showAllRows() {{
    var filter = document.getElementById('strategyFilter').value;
    var symbolQuery = getSymbolQuery();
    var industryFilter = refreshIndustryOptions(filter, symbolQuery);
    var rows = [];
    var allRows = document.querySelectorAll('.stock-row');
    allRows.forEach(function(row) {{
        if (rowMatchesFilters(row, filter, industryFilter, symbolQuery)) {{
            rows.push(row);
            row.classList.add('visible');
            var chartCell = row.querySelector('.chart-cell');
            if (chartCell) {{
                createChartForCell(chartCell);
            }}
        }} else {{
            row.classList.remove('visible');
        }}
    }});
    
    rows.sort(function(a, b) {{
        if (currentSort === 'rs-desc') {{
            return parseFloat(b.getAttribute('data-rs') || 0) - parseFloat(a.getAttribute('data-rs') || 0);
        }} else if (currentSort === 'rs-asc') {{
            return parseFloat(a.getAttribute('data-rs') || 0) - parseFloat(b.getAttribute('data-rs') || 0);
        }} else if (currentSort === 'iv-desc') {{
            return parseFloat(b.getAttribute('data-iv') || 0) - parseFloat(a.getAttribute('data-iv') || 0);
        }} else if (currentSort === 'iv-asc') {{
            return parseFloat(a.getAttribute('data-iv') || 0) - parseFloat(b.getAttribute('data-iv') || 0);
        }} else if (currentSort === 'gamma-desc') {{
            return parseFloat(b.getAttribute('data-gamma') || 0) - parseFloat(a.getAttribute('data-gamma') || 0);
        }} else if (currentSort === 'wall-desc') {{
            return parseFloat(b.getAttribute('data-wall') || 0) - parseFloat(a.getAttribute('data-wall') || 0);
        }} else if (currentSort === 'gex-abs-desc') {{
            return Math.abs(parseFloat(b.getAttribute('data-gexnet') || 0)) - Math.abs(parseFloat(a.getAttribute('data-gexnet') || 0));
        }} else if (currentSort === 'short-desc') {{
            return parseFloat(b.getAttribute('data-short') || 0) - parseFloat(a.getAttribute('data-short') || 0);
        }} else if (currentSort === 'callfloat-desc') {{
            return parseFloat(b.getAttribute('data-callfloat') || 0) - parseFloat(a.getAttribute('data-callfloat') || 0);
        }} else if (currentSort === 'price-desc') {{
            return parseFloat(b.getAttribute('data-price') || 0) - parseFloat(a.getAttribute('data-price') || 0);
        }} else if (currentSort === 'price-asc') {{
            return parseFloat(a.getAttribute('data-price') || 0) - parseFloat(b.getAttribute('data-price') || 0);
        }} else if (currentSort === 'dist-asc') {{
            return parseFloat(a.getAttribute('data-dist') || 0) - parseFloat(b.getAttribute('data-dist') || 0);
        }}
        return 0;
    }});
    
    var parent = rows[0] ? rows[0].parentNode : null;
    if (parent) {{
        rows.forEach(function(row) {{ parent.appendChild(row); }});
    }}
    updateResultCount(rows.length, allRows.length);
}}

function filterChanged() {{
    showAllRows();
}}

showAllRows();

// Copy ticker on click
document.querySelectorAll('.stock-row').forEach(function(row) {{
    row.addEventListener('click', function(e) {{
        // Don't copy if clicking on chart
        if (e.target.closest('.chart-cell') || e.target.closest('.gamma-contract-card') || e.target.closest('.modal') || e.target.closest('a')) return;
        
        var ticker = row.querySelector('.stock-ticker');
        if (ticker) {{
            var text = ticker.textContent.trim().split(' ')[0];  // Get first word (ticker)
            navigator.clipboard.writeText(text).then(function() {{
                // Brief visual feedback
                var badge = row.querySelector('.strategy-badge');
                if (badge) {{
                    var original = badge.textContent;
                    badge.textContent = 'Copied!';
                    setTimeout(function() {{
                        badge.textContent = original;
                    }}, 1000);
                }}
            }});
        }}
    }});
}});

function moneyFmt(value) {{
    value = Number(value || 0);
    if (value >= 1000000) return '$' + (value / 1000000).toFixed(1) + 'M';
    if (value >= 1000) return '$' + Math.round(value / 1000) + 'K';
    return '$' + Math.round(value);
}}

function oiChangeFmt(c) {{
    if (c.oi_change === null || c.oi_change === undefined) return 'Pending';
    var sign = Number(c.oi_change) > 0 ? '+' : '';
    var pct = (c.oi_change_pct === null || c.oi_change_pct === undefined) ? '' : ' / ' + sign + c.oi_change_pct + '%';
    return sign + c.oi_change + pct;
}}

function openGammaDetails(btn) {{
    var row = btn.closest('.stock-row');
    var dataEl = row ? row.querySelector('.gamma-data') : null;
    var list = document.getElementById('gammaDetailList');
    var title = document.getElementById('gammaModalTitle');
    list.innerHTML = '';
    if (!dataEl) return;
    var contracts = [];
    try {{ contracts = JSON.parse(dataEl.textContent || '[]'); }} catch(e) {{ contracts = []; }}
    var ticker = row && row.querySelector('.stock-ticker') ? row.querySelector('.stock-ticker').textContent.trim().split(' ')[0] : '';
    title.textContent = ticker ? ticker + ' Gamma Contracts' : 'Gamma Contracts';
    if (!contracts.length) {{
        list.innerHTML = '<div class="modal-section"><p>No gamma contract records available.</p></div>';
    }} else {{
        contracts.forEach(function(c, idx) {{
            var div = document.createElement('div');
            div.className = 'gamma-detail-row';
            div.innerHTML = `
                <div class="gamma-detail-top">
                    <div class="gamma-detail-contract">${{idx === 0 ? '⭐ ' : ''}}${{c.contract || '-'}}</div>
                    <div class="gamma-detail-score">GS ${{c.score || '-'}}</div>
                </div>
                <div class="gamma-detail-grid">
                    <div class="gamma-detail-cell"><span class="gamma-detail-label">Premium</span><span class="gamma-detail-value">${{c.premium_fmt || moneyFmt(c.premium)}}</span></div>
                    <div class="gamma-detail-cell"><span class="gamma-detail-label">Vol/OI</span><span class="gamma-detail-value">${{Number(c.vol_oi || 0).toFixed(1)}}x</span></div>
                    <div class="gamma-detail-cell"><span class="gamma-detail-label">DTE</span><span class="gamma-detail-value">${{c.dte ?? '-'}}</span></div>
                    <div class="gamma-detail-cell"><span class="gamma-detail-label">Vol / OI</span><span class="gamma-detail-value">${{c.volume || '-'}} / ${{c.openInterest || '-'}}</span></div>
                    <div class="gamma-detail-cell"><span class="gamma-detail-label">Bid / Ask</span><span class="gamma-detail-value">${{c.bid ?? '-'}} / ${{c.ask ?? '-'}}</span></div>
                    <div class="gamma-detail-cell"><span class="gamma-detail-label">Mid / IV</span><span class="gamma-detail-value">${{c.mid ?? '-'}} / ${{c.iv ?? '-'}}%</span></div>
                    <div class="gamma-detail-cell"><span class="gamma-detail-label">Moneyness</span><span class="gamma-detail-value">${{Number(c.pct_otm || 0).toFixed(1)}}%</span></div>
                    <div class="gamma-detail-cell"><span class="gamma-detail-label">Verification</span><span class="gamma-detail-value">${{c.verification || 'Pending'}}</span></div>
                    <div class="gamma-detail-cell"><span class="gamma-detail-label">OI Change</span><span class="gamma-detail-value">${{oiChangeFmt(c)}}</span></div>
                    <div class="gamma-detail-cell"><span class="gamma-detail-label">First Seen</span><span class="gamma-detail-value">${{c.first_seen_at || '-'}}</span></div>
                    <div class="gamma-detail-cell"><span class="gamma-detail-label">Last Trade</span><span class="gamma-detail-value">${{c.last_trade_time || '-'}}</span></div>
                    <div class="gamma-detail-cell" style="grid-column:span 3"><span class="gamma-detail-label">Note</span><span class="gamma-detail-value">${{c.verification_note || '-'}}</span></div>
                    <div class="gamma-detail-cell" style="grid-column:span 3"><span class="gamma-detail-label">Tags</span><span class="gamma-detail-value">${{c.tags || '-'}}</span></div>
                </div>`;
            list.appendChild(div);
        }});
    }}
    document.getElementById('gammaModal').classList.add('show');
}}

function closeGammaDetails() {{
    document.getElementById('gammaModal').classList.remove('show');
}}

document.getElementById('gammaModal').addEventListener('click', function(e) {{
    if (e.target === this) closeGammaDetails();
}});

function showInfo() {{
    document.getElementById('infoModal').classList.add('show');
}}
function closeInfo() {{
    document.getElementById('infoModal').classList.remove('show');
}}
document.getElementById('infoModal').addEventListener('click', function(e) {{
    if (e.target === this) closeInfo();
}});
</script>
</body>
</html>'''

with open('screener.html', 'w') as f:
    f.write(html)
print(f"Done in {time.time()-start:.1f}s: screener.html")
