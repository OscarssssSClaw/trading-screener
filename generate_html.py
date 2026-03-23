#!/usr/bin/env python3
"""TradingView Screener HTML Generator - Single Page with Dropdown Filter"""

import time
import yfinance as yf
from tradingview_screener import Query, Column
import pandas as pd
import json

start = time.time()

def get_iv_for_ticker(ticker):
    if ':' not in ticker:
        return None
    symbol = ticker.split(':')[1]
    if ticker.startswith('OTC:'):
        return None
    try:
        t = yf.Ticker(symbol)
        stock_price = t.info.get('regularMarketPrice', 0)
        if stock_price <= 0:
            return None
        opt = t.option_chain()
        if opt.calls is None or len(opt.calls) == 0:
            return None
        active = opt.calls[opt.calls['bid'] > 0]
        if len(active) == 0:
            return None
        active = active.copy()
        active['dist'] = abs(active['strike'] - stock_price)
        atm_idx = active['dist'].idxmin()
        iv = active.loc[atm_idx].get('impliedVolatility', 0)
        return iv if iv > 0 else None
    except:
        return None

def get_price_history(ticker, days=90):
    if ':' not in ticker:
        return None
    symbol = ticker.split(':')[1]
    if ticker.startswith('OTC:'):
        return None
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period=f"{days}d")
        if hist.empty or len(hist) < 30:
            return None
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
        return data
    except:
        return None

print("Fetching VCP stocks...")
try:
    total_vcp, vcp_raw = (
        Query()
        .select('name', 'close', 'volume', 'ADR', 'Perf.6M', 'SMA20', 'SMA50', 'High.All', 'RSI')
        .where(
            Column('volume') > 1_000_000,
            Column('Perf.6M') >= 50,
            Column('close') > Column('SMA50')
        )
        .limit(100)
        .get_scanner_data()
    )
    vcp_raw['dist_high'] = (vcp_raw['High.All'] - vcp_raw['close']) / vcp_raw['High.All'] * 100
    vcp = vcp_raw[vcp_raw['dist_high'] <= 25].copy().sort_values('RS', ascending=False)
    vcp['strategy'] = 'VCP'
except:
    vcp = pd.DataFrame()

print(f"VCP: {len(vcp)}")

print("Fetching QL stocks...")
try:
    total_ql, ql_raw = (
        Query()
        .select('name', 'close', 'volume', 'ADR', 'Perf.6M', 'SMA20', 'SMA50', 'High.All', 'RSI')
        .where(
            Column('volume') > 1_000_000,
            Column('Perf.6M') >= 50,
            Column('close') > Column('SMA20')
        )
        .limit(100)
        .get_scanner_data()
    )
    ql_raw['dist_high'] = (ql_raw['High.All'] - ql_raw['close']) / ql_raw['High.All'] * 100
    ql = ql_raw[ql_raw['dist_high'] <= 15].copy().sort_values('RS', ascending=False)
    ql['strategy'] = 'Qullamaggie'
except:
    ql = pd.DataFrame()

print(f"QL: {len(ql)}")

print("Fetching HTF stocks...")
try:
    total_htf, htf_raw = (
        Query()
        .select('name', 'close', 'volume', 'ADR', 'Perf.6M', 'SMA20', 'SMA50', 'High.All', 'RSI')
        .where(
            Column('volume') > 1_000_000,
            Column('Perf.6M') >= 50,
            Column('Perf.6M') <= 150,
            Column('ADR') >= 3,
            Column('ADR') <= 15,
            Column('close') > Column('SMA50')
        )
        .limit(100)
        .get_scanner_data()
    )
    htf_raw['dist_high'] = (htf_raw['High.All'] - htf_raw['close']) / htf_raw['High.All'] * 100
    htf = htf_raw[htf_raw['dist_high'] <= 20].copy().sort_values('RS', ascending=False)
    htf['strategy'] = 'HTF'
except:
    htf = pd.DataFrame()

print(f"HTF: {len(htf)}")

# Get SPY performance
spy_perf = 0
try:
    spy_result, spy_df = Query().select('Perf.6M').where(Column('name') == 'SPY').limit(1).get_scanner_data()
    if len(spy_df) > 0:
        spy_perf = float(spy_df['Perf.6M'].iloc[0])
except:
    pass

print(f"SPY 6M: {spy_perf:.1f}%")

# Add RS
for df in [vcp, ql, htf]:
    df['RS'] = df['Perf.6M'] - spy_perf

# Combine all stocks
all_stocks = pd.concat([vcp, ql, htf]).drop_duplicates(subset='ticker')
print(f"Total: {len(all_stocks)}")

# Get price data for charts
print("Fetching price history...")
price_data = {}
for i, ticker in enumerate(all_stocks['ticker'].tolist()):
    prices = get_price_history(ticker, 90)
    if prices:
        price_data[ticker] = prices
    if (i + 1) % 20 == 0:
        print(f"  {i+1}/{len(all_stocks)}...")
print(f"Got price data for {len(price_data)} stocks")

def make_card(row, price_data):
    ticker = str(row['ticker'])
    name = str(row['name']).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    close = float(row['close'])
    dist_high = float(row['dist_high'])
    perf_6m = float(row['Perf.6M'])
    adr = float(row['ADR'])
    rs = float(row.get('RS', 0))
    strategy = str(row.get('strategy', 'Unknown'))
    chart_id = "chart_" + ticker.replace(':', '_')
    price_json = json.dumps(price_data.get(ticker, []))
    
    dist_color = "positive" if dist_high <= 20 else "negative"
    perf_color = "positive" if perf_6m > 0 else "negative"
    rs_color = "positive" if rs > 0 else "negative"
    
    strategy_class = strategy.lower().replace(' ', '-')
    
    return f'''
    <div class="stock-card strategy-{strategy_class}" data-strategy="{strategy}">
        <div class="stock-header">
            <div class="stock-info">
                <div class="stock-name">{name}</div>
                <div class="stock-ticker">{ticker} <span class="strategy-badge">{strategy}</span></div>
            </div>
            <div class="stock-price">${close:.2f}</div>
        </div>
        <div class="chart-container" id="{chart_id}"></div>
        <script type="application/json" class="chart-data">{price_json}</script>
        <div class="stock-metrics">
            <div class="metric">
                <div class="metric-label">Dist</div>
                <div class="metric-value {dist_color}">{dist_high:.1f}%</div>
            </div>
            <div class="metric">
                <div class="metric-label">6M</div>
                <div class="metric-value {perf_color}">{perf_6m:.1f}%</div>
            </div>
            <div class="metric">
                <div class="metric-label">RS</div>
                <div class="metric-value {rs_color}">{rs:.1f}%</div>
            </div>
            <div class="metric">
                <div class="metric-label">ADR</div>
                <div class="metric-value">{adr:.1f}%</div>
            </div>
        </div>
    </div>
    '''

# Generate HTML for all cards
all_cards = ''.join([make_card(row, price_data) for _, row in all_stocks.iterrows()])

html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Trading Screener</title>
<script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#131722;color:#d1d4dc;min-height:100vh}}
.header{{background:#1e222d;padding:15px;position:sticky;top:0;z-index:100;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:18px;color:#2962ff}}
.header p{{font-size:11px;color:#787b86}}
.filter-section{{background:#1e222d;padding:10px 15px;position:sticky;top:60px;z-index:99;border-bottom:1px solid #2a2e39}}
.filter-label{{color:#787b86;font-size:12px;margin-right:10px;display:inline-block}}
.filter-btn{{background:#262d3f;color:#d1d4dc;border:1px solid #2a2e39;padding:8px 16px;margin-right:8px;cursor:pointer;border-radius:6px;font-size:13px;transition:all .2s}}
.filter-btn:hover{{background:#363d52}}
.filter-btn.active{{background:#2962ff;color:#fff;border-color:#2962ff}}
.content{{padding:15px}}
.stock-card{{background:#1e222d;border-radius:12px;padding:14px;margin-bottom:10px;display:none}}
.stock-card.visible{{display:block}}
.stock-card.strategy-vcp.visible{{display:block}}
.stock-card.strategy-qullamaggie.visible{{display:block}}
.stock-card.strategy-htf.visible{{display:block}}
.stock-header{{display:flex;justify-content:space-between;margin-bottom:10px}}
.stock-name{{font-weight:600;font-size:15px;color:#fff}}
.stock-ticker{{color:#787b86;font-size:12px;margin-top:2px}}
.stock-price{{font-size:18px;font-weight:700;color:#fff}}
.chart-container{{height:200px;margin:10px 0;border-radius:8px;overflow:hidden}}
.stock-metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}}
.metric{{text-align:center;background:#262d3f;padding:8px;border-radius:6px}}
.metric-label{{font-size:10px;color:#787b86}}
.metric-value{{font-size:13px;font-weight:600;margin-top:2px}}
.positive{{color:#26a69a}}
.negative{{color:#ef5350}}
.strategy-badge{{background:#2962ff;color:#fff;padding:2px 6px;border-radius:4px;font-size:10px;margin-left:8px}}
.strategy-vcp .strategy-badge{{background:#2962ff}}
.strategy-qullamaggie .strategy-badge{{background:#ef5350}}
.strategy-htf .strategy-badge{{background:#26a69a}}
</style>
</head>
<body>
<div class="header">
    <div>
        <h1>Trading Screener</h1>
        <p>SPY 6M: {spy_perf:.1f}% | {len(all_stocks)} stocks</p>
    </div>
</div>
<div class="filter-section">
    <span class="filter-label">Filter:</span>
    <button class="filter-btn active" data-filter="all">All ({len(all_stocks)})</button>
    <button class="filter-btn" data-filter="VCP">VCP ({len(vcp)})</button>
    <button class="filter-btn" data-filter="Qullamaggie">Qullamaggie ({len(ql)})</button>
    <button class="filter-btn" data-filter="HTF">HTF ({len(htf)})</button>
</div>
<div class="content">
{all_cards}
</div>
<script>
var chartInstances = {{}};

function createChart(container, data) {{
    if (!data || data.length === 0) return null;
    
    var chart = LightweightCharts.createChart(container, {{
        width: container.clientWidth || 400,
        height: 196,
        layout: {{ background: {{ type: 'solid', color: '#1e222d' }}, textColor: '#d1d4dc' }},
        grid: {{ vertLines: {{ color: '#2a2e39' }}, horzLines: {{ color: '#2a2e39' }} }},
        timeScale: {{ borderColor: '#2a2e39' }},
        rightPriceScale: {{ borderColor: '#2a2e39' }}
    }});
    
    var candleSeries = chart.addCandlestickSeries({{
        upColor: '#26a69a', downColor: '#ef5350',
        borderUpColor: '#26a69a', borderDownColor: '#ef5350',
        wickUpColor: '#26a69a', wickDownColor: '#ef5350'
    }});
    candleSeries.setData(data);
    
    // Volume histogram
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

function initCharts() {{
    document.querySelectorAll('.chart-container').forEach(function(container) {{
        var chartId = container.id;
        var dataEl = container.nextElementSibling;
        if (dataEl && dataEl.classList.contains('chart-data')) {{
            try {{
                var data = JSON.parse(dataEl.textContent);
                if (data && data.length > 0) {{
                    var chart = createChart(container, data);
                    if (chart) {{
                        chartInstances[chartId] = chart;
                    }}
                }}
            }} catch(e) {{}}
        }}
    }});
}}

// Filter functionality
document.querySelectorAll('.filter-btn').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
        // Update active button
        document.querySelectorAll('.filter-btn').forEach(function(b) {{ b.classList.remove('active'); }});
        btn.classList.add('active');
        
        var filter = btn.getAttribute('data-filter');
        var cards = document.querySelectorAll('.stock-card');
        
        cards.forEach(function(card) {{
            if (filter === 'all') {{
                card.classList.add('visible');
            }} else {{
                var strategy = card.getAttribute('data-strategy');
                if (strategy === filter) {{
                    card.classList.add('visible');
                }} else {{
                    card.classList.remove('visible');
                }}
            }}
        }});
        
        // Resize all charts
        setTimeout(function() {{
            Object.values(chartInstances).forEach(function(chart) {{
                chart.resize(chart.width + 1, chart.height);
                chart.resize(chart.width - 1, chart.height);
            }});
        }}, 100);
    }});
}});

// Initialize on load
window.addEventListener('load', initCharts);
</script>
</body>
</html>
'''

with open('screener.html', 'w') as f:
    f.write(html)
print(f"Done in {time.time()-start:.1f}s: screener.html")
