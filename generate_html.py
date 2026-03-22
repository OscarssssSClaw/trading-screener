#!/usr/bin/env python3
"""TradingView Screener HTML Generator with Inline Charts"""

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

def get_company_info_for_ticker(ticker):
    if ':' not in ticker:
        return {}
    symbol = ticker.split(':')[1]
    if ticker.startswith('OTC:'):
        return {}
    try:
        t = yf.Ticker(symbol)
        info = t.info
        return {
            'sector': info.get('sector', ''),
            'industry': info.get('industry', ''),
            'longBusinessSummary': info.get('longBusinessSummary', '')
        }
    except:
        return {}

def get_price_history(ticker, days=90):
    """Get 90 days of OHLCV data"""
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
        
        # Convert to format for lightweight-charts with volume
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

def make_card(row, iv_data, company_data, price_data):
    ticker = str(row['ticker'])
    name = str(row['name']).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
    close = float(row['close'])
    dist_high = float(row['dist_high'])
    perf_6m = float(row['Perf.6M'])
    adr = float(row['ADR'])
    rs = float(row.get('RS', 0))
    iv = iv_data.get(ticker)
    info = company_data.get(ticker, {})
    sector = info.get('sector', '')
    industry = info.get('industry', '')
    desc = info.get('longBusinessSummary', '')
    if desc:
        desc = desc[:150] + '...'
    
    # Price data for chart
    chart_id = "chart_" + ticker.replace(':', '_')
    price_json = json.dumps(price_data.get(ticker, []))
    
    # IV
    if iv:
        iv_str = "{:.0f}%".format(iv * 100)
        iv_color_class = "positive" if iv * 100 < 30 else "negative"
        iv_badge_class = "iv-low" if iv * 100 < 30 else ("iv-mid" if iv * 100 < 50 else "iv-high")
        iv_badge = '<span class="iv-badge {}">IV {}</span>'.format(iv_badge_class, iv_str)
    else:
        iv_str = "N/A"
        iv_color_class = ""
        iv_badge = ""
    
    # Sector
    sector_html = ""
    if sector:
        industry_part = " / " + industry if industry else ""
        sector_html = '<div class="sector">{}{}</div>'.format(sector, industry_part)
    
    # Desc
    desc_html = ""
    if desc:
        desc_html = '<div class="company-desc">{}</div>'.format(desc)
    
    # Colors
    dist_color = "positive" if dist_high <= 20 else "negative"
    perf_color = "positive" if perf_6m > 0 else "negative"
    rs_color = "positive" if rs > 0 else "negative"
    
    
    card = '''<div class="stock-card">
        <div class="stock-header">
            <div class="stock-info">
                <div class="stock-name">{}</div>
                <div class="stock-ticker">{}</div>
                {}
            </div>
            <div class="stock-price">${:.2f}</div>
        </div>
        {}
        {}
        <div class="stock-metrics">
            <div class="metric">
                <div class="metric-label">Dist</div>
                <div class="metric-value {}">{:.1f}%</div>
            </div>
            <div class="metric">
                <div class="metric-label">6M</div>
                <div class="metric-value {}">{:.1f}%</div>
            </div>
            <div class="metric">
                <div class="metric-label">RS</div>
                <div class="metric-value {}">{:.1f}%</div>
            </div>
            <div class="metric">
                <div class="metric-label">ADR</div>
                <div class="metric-value">{:.1f}%</div>
            </div>
            <div class="metric">
                <div class="metric-label">IV</div>
                <div class="metric-value {}">{}</div>
            </div>
        </div>
        <div class="chart-container" id="{}"></div>
        <script type="text/json" class="chart-data">{}</script>
    </div>'''.format(
        name, ticker, iv_badge, close,
        sector_html, desc_html,
        dist_color, dist_high,
        perf_color, perf_6m,
        rs_color, rs,
        adr,
        iv_color_class, iv_str,
        chart_id,
        price_json
    )
    return card

print("Fetching VCP stocks directly...")
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
    vcp = vcp_raw[vcp_raw['dist_high'] <= 25].copy().sort_values('volume', ascending=False)
except Exception as e:
    print("VCP query failed: {}".format(e))
    vcp = pd.DataFrame()

print("VCP: {}".format(len(vcp)))

print("Fetching QL stocks directly...")
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
    ql = ql_raw[ql_raw['dist_high'] <= 15].copy().sort_values('volume', ascending=False)
except Exception as e:
    print("QL query failed: {}".format(e))
    ql = pd.DataFrame()

print("QL: {}".format(len(ql)))

print("Fetching HTF stocks directly...")
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
    htf = htf_raw[htf_raw['dist_high'] <= 20].copy().sort_values('volume', ascending=False)
except Exception as e:
    print("HTF query failed: {}".format(e))
    htf = pd.DataFrame()

print("HTF: {}".format(len(htf)))

# Get SPY performance
spy_perf = 0
try:
    spy_result, spy_df = Query().select('Perf.6M').where(Column('name') == 'SPY').limit(1).get_scanner_data()
    if len(spy_df) > 0:
        spy_perf = float(spy_df['Perf.6M'].iloc[0])
except:
    pass

print("SPY 6M: {:.1f}%".format(spy_perf))

# Add RS
for df in [vcp, ql, htf]:
    df['RS'] = df['Perf.6M'] - spy_perf

# Get all unique tickers
# Sort all stocks by RS (high to low)
all_df = pd.concat([vcp, ql, htf]).drop_duplicates(subset='ticker')
all_df = all_df.sort_values('RS', ascending=False)
all_tickers = all_df['ticker'].tolist()
# Re-sort each strategy by RS
vcp = vcp.sort_values('RS', ascending=False)
ql = ql.sort_values('RS', ascending=False)
htf = htf.sort_values('RS', ascending=False)
print("Total unique tickers: {}".format(len(all_tickers)))

# Fetch IV
print("Fetching IV...")
iv_data = {}
for i, ticker in enumerate(all_tickers):
    iv = get_iv_for_ticker(ticker)
    if iv:
        iv_data[ticker] = iv
    if (i + 1) % 10 == 0:
        print("  IV: {}/{}".format(i+1, len(all_tickers)))
print("Got IV for {} stocks".format(len(iv_data)))

# Fetch company info
print("Fetching company info...")
company_data = {}
for i, ticker in enumerate(all_tickers):
    info = get_company_info_for_ticker(ticker)
    if info:
        company_data[ticker] = info
    if (i + 1) % 10 == 0:
        print("  Info: {}/{}".format(i+1, len(all_tickers)))
print("Got info for {} companies".format(len(company_data)))

# Fetch price history for charts
print("Fetching 90-day price history for charts...")
price_data = {}
for i, ticker in enumerate(all_tickers):
    prices = get_price_history(ticker, 90)
    if prices:
        price_data[ticker] = prices
    if (i + 1) % 10 == 0:
        print("  Charts: {}/{}".format(i+1, len(all_tickers)))
print("Got price data for {} stocks".format(len(price_data)))

# Generate HTML
vcp_html = ''.join([make_card(row, iv_data, company_data, price_data) for _, row in vcp.head(50).iterrows()])
ql_html = ''.join([make_card(row, iv_data, company_data, price_data) for _, row in ql.head(50).iterrows()])
htf_html = ''.join([make_card(row, iv_data, company_data, price_data) for _, row in htf.head(50).iterrows()])

html = '''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Trading Screener</title>
<script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#131722;color:#d1d4dc;min-height:100vh;padding-bottom:20px;overscroll-behavior:contain}}
.header{{background:#1e222d;padding:15px;text-align:center;position:sticky;top:0;z-index:100}}
.header h1{{font-size:18px;color:#2962ff}}
.header p{{font-size:11px;color:#787b86;margin-top:5px}}
.tabs{{display:flex;background:#1e222d;position:sticky;top:60px;z-index:99}}
.tab{{flex:1;padding:12px 8px;text-align:center;cursor:pointer;font-size:13px;font-weight:600;color:#787b86;border-bottom:2px solid transparent;transition:all .3s}}
.tab.active{{color:#2962ff;border-bottom:2px solid #2962ff}}
.count{{font-size:10px;color:#787b86;margin-top:3px}}
.content{{display:none;padding:10px;overscroll-behavior:contain}}
.content.active{{display:block}}
.stock-card{{background:#1e222d;border-radius:12px;padding:14px;margin-bottom:10px;cursor:pointer;transition:all .3s}}
.stock-card:hover{{background:#262d3f}}
.stock-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}}
.stock-info{{flex:1}}
.stock-name{{font-weight:600;font-size:15px;color:#fff}}
.stock-ticker{{color:#787b86;font-size:12px;margin-top:2px}}
.stock-price{{font-size:18px;font-weight:700;color:#fff}}
.stock-metrics{{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-bottom:8px}}
.metric{{text-align:center}}
.metric-label{{font-size:10px;color:#787b86;margin-bottom:2px}}
.metric-value{{font-size:13px;font-weight:600}}
.positive{{color:#26a69a}}
.negative{{color:#ef5350}}
.sector{{font-size:11px;color:#787b86;margin:4px 0}}
.company-desc{{font-size:11px;color:#aaa;margin:4px 0;line-height:1.4}}
.iv-badge{{display:inline-block;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:600;margin-top:3px}}
.iv-low{{background:#26a69a;color:#fff}}
.iv-mid{{background:#ef5350;color:#fff}}
.iv-high{{background:#b71c1c;color:#fff}}
.chart-container{{height:220px;margin-top:10px;background:#1e222d;border-radius:8px;overflow:hidden;contain:layout style;transform:translateZ(0);will-change:transform;touch-action:none}}
.chart-container.visible{{display:block}}
</style>
</head>
<body>
<div class="header">
    <h1>Trading Screener</h1>
    <p>SPY 6M: {spy_perf:.1f}% | Click to toggle 90-day chart</p>
</div>
<div class="tabs">
    <div class="tab active" onclick="showTab('vcp')">VCP<span class="count">{vcp_count} stocks</span></div>
    <div class="tab" onclick="showTab('ql')">Qullamaggie<span class="count">{ql_count} stocks</span></div>
    <div class="tab" onclick="showTab('htf')">HTF<span class="count">{htf_count} stocks</span></div>
</div>
<div id="vcp" class="content active">{vcp_html}</div>
<div id="ql" class="content">{ql_html}</div>
<div id="htf" class="content">{htf_html}</div>
<script>
function showTab(name){{
    document.querySelectorAll('.tab').forEach(function(t){{t.classList.remove('active')}});
    document.querySelectorAll('.content').forEach(function(c){{c.classList.remove('active')}});
    document.querySelector('.tab[onclick="showTab(\\''+name+'\\')"]').classList.add('active');
    document.getElementById(name).classList.add('active');
}}

var chartInstances = {{}};

function toggleChart(containerId){{
    var container = document.getElementById(containerId);
    if (!container) return;
    
    if (container.classList.contains('visible')) {{
        container.classList.remove('visible');
        return;
    }}
    
    container.classList.add('visible');
    
    if (chartInstances[containerId]) return;
    
    var dataEl = container.nextElementSibling;
    if (!dataEl || !dataEl.classList.contains('chart-data')) return;
    
    var data = JSON.parse(dataEl.textContent);
    if (!data || data.length === 0) return;
    
    var chart = LightweightCharts.createChart(container, {{
        width: container.clientWidth,
        height: 196,
        layout: {{
            background: {{ type: 'solid', color: '#1e222d' }},
            textColor: '#d1d4dc'
        }},
        grid: {{
            vertLines: {{ color: '#2a2e39' }},
            horzLines: {{ color: '#2a2e39' }}
        }},
        timeScale: {{
            borderColor: '#2a2e39'
        }},
        rightPriceScale: {{
            borderColor: '#2a2e39'
        }}
    }});
    
    var candleSeries = chart.addCandlestickSeries({{
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderUpColor: '#26a69a',
        borderDownColor: '#ef5350',
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350'
    }});
    
    candleSeries.setData(data);
    chart.timeScale().fitContent();
    
    chartInstances[containerId] = chart;
    
    // Handle resize
    var resizeObserver = new ResizeObserver(function() {{
        chart.applyOptions({{ width: container.clientWidth }});
    }});
    resizeObserver.observe(container);
}}

// Auto-load all charts on page load
window.addEventListener('load', function() {{
    document.querySelectorAll('.chart-container').forEach(function(container) {{
        var chartId = container.id;
        if (!chartInstances[chartId]) {{
            var dataEl = container.nextElementSibling;
            if (dataEl && dataEl.classList.contains('chart-data')) {{
                try {{
                    var data = JSON.parse(dataEl.textContent);
                    if (data && data.length > 0) {{
                        var chart = LightweightCharts.createChart(container, {{
                            width: container.clientWidth,
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
                        chart.timeScale().fitContent();
                        chartInstances[chartId] = chart;
                        // Block scroll/touch/zoom on chart
                        container.addEventListener('wheel', function(e) {{ e.preventDefault(); }}, {{passive: false}});
                        container.addEventListener('touchmove', function(e) {{ e.preventDefault(); }}, {{passive: false}});
                        new ResizeObserver(function() {{ chart.applyOptions({{ width: container.clientWidth }}); }}).observe(container);
                    }}
                }} catch(e) {{}}
            }}
        }}
    }});
}});
</script>
</body>
</html>'''.format(
    spy_perf=spy_perf,
    vcp_count=len(vcp),
    ql_count=len(ql),
    htf_count=len(htf),
    vcp_html=vcp_html,
    ql_html=ql_html,
    htf_html=htf_html
)

with open('screener.html', 'w') as f:
    f.write(html)
print("Done in {:.1f}s: screener.html".format(time.time()-start))
