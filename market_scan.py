"""TradingView scans for the liquid universe and the near-52-week-high set.

The near-high test is a scanner filter (`close` at least 99.9% of `price_52_week_high`),
then every match is paged in. Distance is sorted locally because the scanner can only
sort by a field, not by an expression.
"""

from __future__ import annotations

import pandas as pd
from tradingview_screener import Column, Query

from screen_rules import (
    ADR_MIN_PCT,
    ADV_MIN_DOLLARS,
    MIN_PRICE,
    NEAR_52W_MAX_DIST,
    ScanError,
    add_leadership_scores,
)

SCAN_COLUMNS = (
    'name',
    'description',
    'close',
    'volume',
    'average_volume_30d_calc',
    'ADR',
    'Perf.1M',
    'Perf.3M',
    'Perf.6M',
    'Perf.Y',
    'SMA10',
    'SMA20',
    'SMA50',
    'SMA150',
    'SMA200',
    'price_52_week_high',
    'price_52_week_low',
    'High.All',
    'sector',
    'industry',
    'exchange',
    'market_cap_basic',
)

NUMERIC_COLUMNS = (
    'close',
    'volume',
    'average_volume_30d_calc',
    'ADR',
    'Perf.1M',
    'Perf.3M',
    'Perf.6M',
    'Perf.Y',
    'SMA10',
    'SMA20',
    'SMA50',
    'SMA150',
    'SMA200',
    'price_52_week_high',
    'price_52_week_low',
    'High.All',
    'market_cap_basic',
)


def base_filters(near_52w=False):
    filters = [
        Column('is_primary') == True,
        Column('close') >= MIN_PRICE,
        Column('exchange').isin(['NASDAQ', 'NYSE', 'AMEX']),
        Column('type') == 'stock',
        Column('typespecs').has(['common']),
        Column('typespecs').has_none_of(['etf', 'etn']),
        Column('average_volume_30d_calc') >= 100,
    ]
    if near_52w:
        # 0.999 means the close is at least 99.9% of the 52-week high (within 0.1%).
        filters.append(Column('close').above_pct('price_52_week_high', 0.999))
    return filters


def fetch_scanner_page(offset, limit, near_52w=False):
    # TradingView range is [start, end), and Query.limit() writes the end index, not a page length.
    query = (
        Query()
        .set_markets('america')
        .select(*SCAN_COLUMNS)
        .where(*base_filters(near_52w=near_52w))
        .order_by('market_cap_basic', ascending=False)
        .offset(offset)
        .limit(offset + limit)
    )
    return query.get_scanner_data(timeout=60)


def paginate_scan(fetch_page, *, page_size=1000, max_rows=20000, label='scan'):
    offset = 0
    frames = []
    total = None
    while offset <= max_rows:
        try:
            page_total, frame = fetch_page(offset, page_size)
        except ScanError:
            raise
        except Exception as exc:
            raise ScanError(f'{label} scan failed at offset {offset}: {exc}') from exc
        if page_total is None:
            raise ScanError(f'{label} scan returned no total at offset {offset}')
        page_total = int(page_total)
        if total is None or page_total > total:
            total = page_total
        if frame is None or len(frame) == 0:
            if offset == 0:
                print(f'{label}: scanner_total={total} fetched=0')
                empty = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()
                return empty, total
            if offset < total:
                raise ScanError(f'{label} scan stopped early at offset {offset} of scanner total {total}')
            break
        frames.append(frame)
        fetched = offset + len(frame)
        print(f'{label}: offset={offset} page_rows={len(frame)} cumulative={fetched} scanner_total={total}')
        if fetched >= total:
            break
        if fetched <= offset:
            raise ScanError(f'{label} scan made no progress at offset {offset}')
        offset = fetched
    else:
        raise ScanError(f'{label} scan exceeded {max_rows} rows (scanner total {total}); refusing to truncate')

    if not frames:
        return pd.DataFrame(), int(total or 0)
    raw_count = sum(len(frame) for frame in frames)
    out = pd.concat(frames, ignore_index=True)
    if 'ticker' in out.columns:
        out = out.drop_duplicates(subset='ticker', keep='first')
    if total and raw_count < total:
        raise ScanError(f'{label} incomplete: fetched {raw_count} of scanner total {total}')
    if total and len(out) < total * 0.5 and raw_count >= min(total, max_rows):
        raise ScanError(f'{label} pagination looks stuck: {len(out)} unique tickers vs scanner total {total}')
    print(f'{label}: complete unique={len(out)} raw={raw_count} scanner_total={total}')
    return out, int(total)


def prepare_quotes(df):
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=['ticker', 'company', 'symbol', 'liquid', 'adr_ok', 'dist_52w', 'adv_dollar'])
    out = df.copy()
    if 'description' in out.columns:
        out = out.rename(columns={'description': 'company'})
    if 'name' in out.columns:
        out = out.rename(columns={'name': 'symbol'})
    if 'ticker' not in out.columns:
        raise ScanError('Scanner frame has no ticker column')
    out['ticker'] = out['ticker'].astype(str)
    for column in NUMERIC_COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA
        out[column] = pd.to_numeric(out[column], errors='coerce')
    otc = out['ticker'].str.startswith('OTC:')
    if 'exchange' in out.columns:
        otc = otc | out['exchange'].astype(str).str.upper().eq('OTC')
    out = out.loc[~otc].copy()
    out = out.drop_duplicates('ticker', keep='first')
    out['adv_dollar'] = out['close'] * out['average_volume_30d_calc']
    out['dist_52w'] = (out['price_52_week_high'] - out['close']) / out['price_52_week_high'] * 100.0
    out.loc[~(out['price_52_week_high'] > 0), 'dist_52w'] = pd.NA
    out['liquid'] = (
        out['close'].ge(MIN_PRICE).fillna(False) & out['adv_dollar'].ge(ADV_MIN_DOLLARS).fillna(False)
    )
    out['adr_ok'] = out['ADR'].ge(ADR_MIN_PCT).fillna(False)
    if 'company' not in out.columns:
        out['company'] = ''
    return out


def scan_liquid_universe():
    raw, total = paginate_scan(
        lambda offset, limit: fetch_scanner_page(offset, limit, near_52w=False),
        label='Universe',
    )
    prepared = prepare_quotes(raw)
    liquid = prepared.loc[prepared['liquid']].copy() if 'liquid' in prepared.columns else prepared
    print(
        f"Universe: scanner_total={total} fetched={0 if raw is None else len(raw)} "
        f"after_otc={len(prepared)} ADV$>=${ADV_MIN_DOLLARS:,.0f} close>=${MIN_PRICE:.0f}: {len(liquid)}"
    )
    if liquid.empty:
        return liquid, {
            'scanner_total': total,
            'fetched': 0 if raw is None else len(raw),
            'prepared': len(prepared),
            'liquid': 0,
            'scored_rs': 0,
        }
    liquid = add_leadership_scores(liquid)
    scored = int(liquid['rs_pct'].notna().sum())
    ql_scored = int(liquid['ql_pct'].notna().sum())
    print(f"Universe: RS proxy scored {scored} names; Qullamaggie blend scored {ql_scored}")
    return liquid, {
        'scanner_total': total,
        'fetched': len(raw),
        'prepared': len(prepared),
        'liquid': len(liquid),
        'scored_rs': scored,
    }


def scan_near_52w():
    raw, total = paginate_scan(
        lambda offset, limit: fetch_scanner_page(offset, limit, near_52w=True),
        label='52W',
    )
    prepared = prepare_quotes(raw)
    if prepared.empty or 'dist_52w' not in prepared.columns:
        near = prepared
        liquid_near = prepared
    else:
        near = prepared.loc[prepared['dist_52w'].notna() & (prepared['dist_52w'] <= NEAR_52W_MAX_DIST)].copy()
        liquid_near = near.loc[near['liquid']].sort_values(['dist_52w', 'adv_dollar'], ascending=[True, False])
    print(
        f"52W: scanner_total={total} fetched={0 if raw is None else len(raw)} "
        f"after_local_dist<={NEAR_52W_MAX_DIST}% {len(near)} after_ADV$={len(liquid_near)} "
        "sorted_by=Dist_52W. Query requires close >= 99.9% of price_52_week_high and pages through every match. "
        "Distance sort is local; the scanner cannot sort by an expression."
    )
    return liquid_near, {
        'scanner_total': total,
        'fetched': 0 if raw is None else len(raw),
        'near': len(near),
        'liquid_near': len(liquid_near),
    }
