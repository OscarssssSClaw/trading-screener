#!/usr/bin/env python3
"""TradingView Screener HTML Generator - Multi-strategy badges on merged stocks"""

import time
import datetime
import yfinance as yf
from tradingview_screener import Query, Column
import pandas as pd
import json

start = time.time()
now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
last_updated = now.strftime("%Y-%m-%d %H:%M") + " HK"

def get_iv_for_ticker(ticker):
    if ':' not in ticker:
        return None
    symbol = ticker.split(':')[1]
    if symbol.startswith('OTC'):
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

def get_price_and_adr(ticker, days=90):
    if ':' not in ticker:
        return None, None
    symbol = ticker.split(':')[1]
    if ticker.startswith('OTC'):
        return None, None
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period=f"{days}d")
        if hist.empty or len(hist) < 30:
            return None, None
        
        # Calculate ADR (20-day Average Daily Range)
        adr = 0
        if len(hist) >= 20:
            ranges = []
            for i in range(-20, 0):
                high = hist.iloc[i]['High']
                low = hist.iloc[i]['Low']
                close = hist.iloc[i]['Close']
                if close > 0:
                    daily_range = ((high - low) / close) * 100
                    ranges.append(daily_range)
            if ranges:
                adr = sum(ranges) / len(ranges)
        
        data = []
        for idx, row in hist.iterrows():
            data.append({
                'time': int(idx.timestamp()),
                'open': float(row['Open']),
                'high': float(row['High']),
                'low': float(row['Low']),
                'close': float(row['Close']),
                'volume': int(row['Volume']) if 'Volume' in row else 0
            })
        return data, adr
    except:
        return None, None

print("Fetching VCP stocks...")
try:
    total_vcp, vcp_raw = (
        Query()
        .select('name', 'close', 'volume', 'ADR', 'Perf.6M', 'SMA20', 'SMA50', 'High.All', 'RSI', 'sector', 'industry')
        .where(
            Column('volume') > 1_000_000,
            Column('Perf.6M') >= 50,
            Column('close') > Column('SMA50')
        )
        .limit(500)
        .get_scanner_data()
    )
    vcp_raw['dist_high'] = (vcp_raw['High.All'] - vcp_raw['close']) / vcp_raw['High.All'] * 100
    vcp = vcp_raw[vcp_raw['dist_high'] <= 25].copy()
    vcp['is_vcp'] = True
except:
    vcp = pd.DataFrame()

print(f"VCP: {len(vcp)}")

print("Fetching QL stocks...")
try:
    total_ql, ql_raw = (
        Query()
        .select('name', 'close', 'volume', 'ADR', 'Perf.6M', 'SMA20', 'SMA50', 'High.All', 'RSI', 'sector', 'industry')
        .where(
            Column('volume') > 1_000_000,
            Column('Perf.6M') >= 50,
            Column('close') > Column('SMA20')
        )
        .limit(500)
        .get_scanner_data()
    )
    ql_raw['dist_high'] = (ql_raw['High.All'] - ql_raw['close']) / ql_raw['High.All'] * 100
    ql = ql_raw[ql_raw['dist_high'] <= 15].copy()
    ql['is_ql'] = True
except:
    ql = pd.DataFrame()

print(f"QL: {len(ql)}")

print("Fetching HTF stocks...")
try:
    total_htf, htf_raw = (
        Query()
        .select('name', 'close', 'volume', 'ADR', 'Perf.6M', 'SMA20', 'SMA50', 'High.All', 'RSI', 'sector', 'industry')
        .where(
            Column('volume') > 1_000_000,
            Column('Perf.6M') >= 50,
            Column('Perf.6M') <= 150,
            Column('ADR') >= 3,
            Column('ADR') <= 15,
            Column('close') > Column('SMA50')
        )
        .limit(500)
        .get_scanner_data()
    )
    htf_raw['dist_high'] = (htf_raw['High.All'] - htf_raw['close']) / htf_raw['High.All'] * 100
    htf = htf_raw[htf_raw['dist_high'] <= 20].copy()
    htf['is_htf'] = True
except:
    htf = pd.DataFrame()

print(f"HTF: {len(htf)}")

spy_perf = 0
try:
    spy_result, spy_df = Query().select('Perf.6M').where(Column('name') == 'SPY').limit(1).get_scanner_data()
    if len(spy_df) > 0:
        spy_perf = float(spy_df['Perf.6M'].iloc[0])
except:
    pass

print(f"SPY 6M: {spy_perf:.1f}%")

# Merge all three datasets on ticker to get multi-strategy stocks
all_stocks = vcp.merge(ql[['ticker', 'is_ql']], on='ticker', how='outer')
all_stocks = all_stocks.merge(htf[['ticker', 'is_htf']], on='ticker', how='outer')
all_stocks['is_vcp'] = all_stocks['is_vcp'].fillna(False).astype(bool)
all_stocks['is_ql'] = all_stocks['is_ql'].fillna(False).astype(bool)
all_stocks['is_htf'] = all_stocks['is_htf'].fillna(False).astype(bool)

# Add RS
all_stocks['RS'] = all_stocks['Perf.6M'] - spy_perf

# Filter out stocks with invalid names (NaN or empty)
all_stocks = all_stocks[all_stocks['name'].notna()]
all_stocks = all_stocks[all_stocks['name'] != '']
all_stocks = all_stocks[all_stocks['name'].astype(str) != 'nan']
all_stocks = all_stocks[all_stocks['name'].astype(str) != 'None']

print(f"Total unique stocks: {len(all_stocks)}")
vcp_count = int(all_stocks['is_vcp'].sum())
ql_count = int(all_stocks['is_ql'].sum())
htf_count = int(all_stocks['is_htf'].sum())
print(f"Actual - VCP: {vcp_count}, QL: {ql_count}, HTF: {htf_count}")

# Get price data for charts
print("Fetching price history...")
price_data = {}
adr_data = {}
for i, ticker in enumerate(all_stocks['ticker'].tolist()):
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

def make_row(row, price_data, anim_delay=0):
    ticker = str(row['ticker'])
    name = str(row['name']).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    close = float(row['close'])
    dist_high = float(row['dist_high'])
    perf_6m = float(row['Perf.6M'])
    # Use our calculated ADR from yfinance, fallback to TradingView if not available
    adr = adr_data.get(ticker, float(row.get('ADR', 0) or 0))
    rs = float(row.get('RS', 0))
    chart_id = "chart_" + ticker.replace(':', '_')
    price_json = json.dumps(price_data.get(ticker, []))
    iv_val = iv_data.get(ticker)
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
    sector = str(row.get('sector', '-'))
    industry = str(row.get('industry', '-'))
    
    dist_color = "positive" if dist_high <= 20 else "negative"
    perf_color = "positive" if perf_6m > 0 else "negative"
    rs_color = "positive" if rs > 0 else "negative"
    
    # Build strategy badges and classes
    badges = []
    classes = []
    strat_list = []
    if row.get('is_vcp', False):
        badges.append('<span class="strategy-badge strategy-vcp">VCP</span>')
        classes.append('strategy-vcp')
        strat_list.append('VCP')
    if row.get('is_ql', False):
        badges.append('<span class="strategy-badge strategy-qullamaggie">Qullamaggie</span>')
        classes.append('strategy-qullamaggie')
        strat_list.append('Qullamaggie')
    if row.get('is_htf', False):
        badges.append('<span class="strategy-badge strategy-htf">HTF</span>')
        classes.append('strategy-htf')
        strat_list.append('HTF')
    
    badges_str = ''.join(badges)
    classes_str = ' '.join(classes)
    data_strategies = ','.join(strat_list)
    
    return f'''
    <div class="stock-row {classes_str}" data-strategies="{data_strategies}" data-rs="{rs:.1f}" data-iv="{iv_val}" data-price="{close}" data-dist="{dist_high:.1f}">
        <div class="stock-header">
            <div class="stock-name">{name}</div>
            <div class="stock-ticker">{ticker} {badges_str}</div>
            <div class="stock-sector">{sector} - {industry}</div>
        </div>
        <div class="metric iv-metric">IV<br><span class="iv-value iv-{iv_class}">{iv_display}</span></div>
        <div class="stock-price">${close:.2f}</div>
        <div class="metric">Dist<br><span class="{dist_color}">{dist_high:.1f}%</span></div>
        <div class="metric">6M<br><span class="{perf_color}">{perf_6m:.1f}%</span></div>
        <div class="metric">RS<br><span class="{rs_color}">{rs:.1f}%</span></div>
        <div class="metric">ADR<br>{adr:.1f}%</div>
        <div class="chart-cell" id="{chart_id}"></div>
        <script type="application/json" class="chart-data">{price_json}</script>
    </div>'''

all_rows = ''.join([make_row(row, price_data, i*0.05) for i, (_, row) in enumerate(all_stocks.iterrows())])

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
    --text-secondary: #8b919e;
    --text-muted: #555a66;
    --border: #2a2e3a;
    --red: #ff4757;
    --orange: #ff9f43;
    --blue: #3b82f6;
}}

body{{
    font-family:'Satoshi',-apple-system,BlinkMacSystemFont,sans-serif;
    background:var(--bg-primary);
    color:var(--text-primary);
    min-height:100vh;
    line-height:1.4;
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
    transition:all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    position:relative;
    overflow:hidden;
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

.stock-info{{
    flex:1.5;
    min-width:180px;
}}

.stock-name{{
    font-weight:700;
    font-size:15px;
    color:var(--text-primary);
    margin-bottom:4px;
}}

.stock-ticker{{
    color:var(--text-secondary);
    font-size:11px;
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

.chart-cell{{
    flex:1;
    min-width:140px;
    height:60px;
    border-radius:6px;
    overflow:hidden;
    border:1px solid var(--border);
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
    .header{{flex-direction:column;gap:12px;text-align:center}}
    .header::before{{width:100%;height:3px;top:0}}
    .header-meta{{align-items:center}}
    .filter-section{{top:48px}}
    .stock-row{{flex-wrap:wrap;padding:16px}}
    .stock-info{{flex:1 1 100%;margin-bottom:8px}}
    .chart-cell{{flex:1 1 100%;margin-top:8px}}
}}
</style>
</head>
<body>
<div class="header">
    <h1>Trading <span>Screener</span></h1>
    <div class="header-meta">
        <p>SPY 6M: <span>{spy_perf:.1f}%</span> | <span>{len(all_stocks)}</span> stocks</p>
        <p>Updated: {last_updated}</p>
    </div>
    <button class="info-btn" onclick="showInfo()">ℹ️ Info</button>
</div>
<div class="filter-section">
    <span class="filter-label">Strategy:</span>
    <select class="filter-select" id="strategyFilter" onchange="filterChanged()">
        <option value="all">All ({len(all_stocks)})</option>
        <option value="VCP">VCP ({vcp_count})</option>
        <option value="Qullamaggie">Qullamaggie ({ql_count})</option>
        <option value="HTF">HTF ({htf_count})</option>
    </select>
    <select class="filter-select" id="sortFilter" onchange="sortChanged()">
        <option value="rs-desc">RS ↓ (High to Low)</option>
        <option value="rs-asc">RS ↑ (Low to High)</option>
        <option value="iv-desc">IV ↓ (High to Low)</option>
        <option value="iv-asc">IV ↑ (Low to High)</option>
        <option value="price-desc">Price ↓</option>
        <option value="price-asc">Price ↑</option>
        <option value="dist-asc">Dist ↑ (Near High)</option>
    </select>
</div>
<div class="col-header">
    <div style="flex:1;min-width:150px">Stock</div>
    <div style="width:80px">Price</div>
    <div style="width:60px">IV</div>
    <div style="width:60px">Dist</div>
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
            <p>• Volume &gt; 1M<br>• 6M Return ≥ 50%<br>• Close &gt; SMA50<br>• Distance from High ≤ 25%</p>
        </div>
        <div class="modal-section">
            <h3>Qullamaggie Breakout</h3>
            <p>• Volume &gt; 1M<br>• 6M Return ≥ 50%<br>• Close &gt; SMA20<br>• Distance from High ≤ 15%</p>
        </div>
        <div class="modal-section">
            <h3>HTF (High Tight Flag)</h3>
            <p>• Volume &gt; 1M<br>• 6M Return 50-150%<br>• ADR 3-15%<br>• Close &gt; SMA50<br>• Distance from High ≤ 20%</p>
        </div>
        <div class="modal-section">
            <h3>RS (Relative Strength)</h3>
            <p>Stock's 6M return minus SPY's 6M return.<br>Positive = outperforming market.</p>
        </div>
        <button class="modal-close" onclick="closeInfo()">Got it ✓</button>
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
        timeScale: {{ visible: false }},
        rightPriceScale: {{ visible: false }},
        crosshair: {{ mode: 0 }}
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

function showAllRows() {{
    var filter = document.getElementById('strategyFilter').value;
    var rows = [];
    document.querySelectorAll('.stock-row').forEach(function(row) {{
        var strategies = row.getAttribute('data-strategies') || '';
        var stratList = strategies.split(',').map(function(s) {{ return s.trim(); }});
        if (filter === 'all' || stratList.indexOf(filter) !== -1) {{
            rows.push(row);
            row.classList.add('visible');
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
}}

function filterChanged() {{
    showAllRows();
}}

showAllRows();

function showRowsForFilter(filter) {{
    document.querySelectorAll('.stock-row').forEach(function(row) {{
        var strategies = row.getAttribute('data-strategies') || '';
        var stratList = strategies.split(',').map(function(s) {{ return s.trim(); }});
        if (filter === 'all' || stratList.indexOf(filter) !== -1) {{
            row.classList.add('visible');
            var chartCell = row.querySelector('.chart-cell');
            if (chartCell) {{
                createChartForCell(chartCell);
            }}
        }} else {{
            row.classList.remove('visible');
        }}
    }});
}}

function filterChanged() {{
    showAllRows();
}}

showRowsForFilter('all');

// Copy ticker on click
document.querySelectorAll('.stock-row').forEach(function(row) {{
    row.addEventListener('click', function(e) {{
        // Don't copy if clicking on chart
        if (e.target.closest('.chart-cell')) return;
        
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
