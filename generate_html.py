#!/usr/bin/env python3
"""TradingView Screener HTML Generator - Optimized with direct queries"""

import time
import yfinance as yf
from tradingview_screener import Query, Column
import pandas as pd

start = time.time()

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
    print(f"VCP query failed: {e}")
    vcp = pd.DataFrame()

print(f"VCP: {len(vcp)}")

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
    print(f"QL query failed: {e}")
    ql = pd.DataFrame()

print(f"QL: {len(ql)}")

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
    print(f"HTF query failed: {e}")
    htf = pd.DataFrame()

print(f"HTF: {len(htf)}")

# Get SPY performance for reference
spy_perf = 0
try:
    spy_result, spy_df = Query().select('Perf.6M').where(Column('name') == 'SPY').limit(1).get_scanner_data()
    if len(spy_df) > 0:
        spy_perf = spy_df['Perf.6M'].iloc[0]
except:
    pass

print(f"SPY 6M: {spy_perf:.1f}%")

# Add RS vs SPY
for df in [vcp, ql, htf]:
    df['RS'] = df['Perf.6M'] - spy_perf

# Combine all for IV fetching
all_stocks = pd.concat([vcp, ql, htf]).drop_duplicates(subset='ticker')

def get_iv_for_stocks(tickers):
    print(f"Fetching IV for {len(tickers)} stocks...")
    iv_data = {}
    for i, ticker in enumerate(tickers):
        if ':' not in ticker:
            continue
        symbol = ticker.split(':')[1]
        if ticker.startswith('OTC:'):
            continue
        try:
            t = yf.Ticker(symbol)
            info = t.info
            stock_price = info.get('regularMarketPrice', 0)
            if stock_price <= 0:
                continue
            opt = t.option_chain()
            if opt.calls is None or len(opt.calls) == 0:
                continue
            active = opt.calls[opt.calls['bid'] > 0]
            if len(active) == 0:
                continue
            active = active.copy()
            active['dist'] = abs(active['strike'] - stock_price)
            atm_idx = active['dist'].idxmin()
            iv = active.loc[atm_idx].get('impliedVolatility', 0)
            if iv > 0:
                iv_data[ticker] = iv
        except:
            pass
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(tickers)}...")
    print(f"Got IV for {len(iv_data)} stocks")
    return iv_data

def get_company_info(tickers):
    print(f"Fetching company info for {len(tickers)} stocks...")
    company_data = {}
    for i, ticker in enumerate(tickers):
        if ':' not in ticker:
            continue
        symbol = ticker.split(':')[1]
        if ticker.startswith('OTC:'):
            continue
        try:
            t = yf.Ticker(symbol)
            info = t.info
            if info:
                company_data[ticker] = {
                    'sector': info.get('sector', ''),
                    'industry': info.get('industry', ''),
                    'longBusinessSummary': info.get('longBusinessSummary', '')
                }
        except:
            pass
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(tickers)}...")
    print(f"Got info for {len(company_data)} companies")
    return company_data

iv_data = get_iv_for_stocks(all_stocks['ticker'].tolist())
company_data = get_company_info(all_stocks['ticker'].tolist())

def make_card(row, iv_data, company_data):
    ticker = row['ticker']
    name = row['name']
    close = row['close']
    dist_high = row['dist_high']
    perf_6m = row['Perf.6M']
    adr = row['ADR']
    rs = row.get('RS', 0)
    iv = iv_data.get(ticker, None)
    info = company_data.get(ticker, {})
    sector = info.get('sector', '')
    industry = info.get('industry', '')
    desc = info.get('longBusinessSummary', '')[:200] + '...' if info.get('longBusinessSummary') else ''
    
    iv_str = f"{iv*100:.0f}%" if iv else "N/A"
    iv_color = "positive" if iv and iv*100 < 30 else "negative"
    
    iv_badge = ""
    if iv:
        iv_class = "iv-low" if iv*100 < 30 else ("iv-mid" if iv*100 < 50 else "iv-high")
        iv_badge = f'<span class="iv-badge {iv_class}">IV {iv*100:.0f}%</span>'
    
    sector_html = f'<div class="sector">{sector}{" / " + industry if industry else ""}</div>' if sector else ""
    desc_html = f'<div class="company-desc">{desc}</div>' if desc else ""
    
    return f'''
    <div class="stock-card" onclick="openChart('{ticker}', '{name}')">
        <div class="stock-header">
            <div class="stock-info">
                <div class="stock-name">{name}</div>
                <div class="stock-ticker">{ticker}</div>
                {iv_badge}
            </div>
            <div class="stock-price">${close:.2f}</div>
        </div>
        {sector_html}
        {desc_html}
        <div class="stock-metrics">
            <div class="metric">
                <div class="metric-label">Dist</div>
                <div class="metric-value {"positive" if dist_high <= 20 else "negative"}">{dist_high:.1f}%</div>
            </div>
            <div class="metric">
                <div class="metric-label">6M</div>
                <div class="metric-value {"positive" if perf_6m > 0 else "negative"}">{perf_6m:.1f}%</div>
            </div>
            <div class="metric">
                <div class="metric-label">RS</div>
                <div class="metric-value {"positive" if rs > 0 else "negative"}">{rs:.1f}%</div>
            </div>
            <div class="metric">
                <div class="metric-label">ADR</div>
                <div class="metric-value">{adr:.1f}%</div>
            </div>
            <div class="metric">
                <div class="metric-label">IV</div>
                <div class="metric-value {iv_color}">{iv_str}</div>
            </div>
        </div>
    </div>'''

def generate_html(vcp, ql, htf, spy_perf, iv_data, company_data):
    vcp_html = ''.join([make_card(row, iv_data, company_data) for _, row in vcp.head(50).iterrows()])
    ql_html = ''.join([make_card(row, iv_data, company_data) for _, row in ql.head(50).iterrows()])
    htf_html = ''.join([make_card(row, iv_data, company_data) for _, row in htf.head(50).iterrows()])
    
    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"></meta><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Trading Screener</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#131722;color:#d1d4dc;min-height:100vh;padding-bottom:20px}}
.header{{background:#1e222d;padding:15px;text-align:center;position:sticky;top:0;z-index:100}}
.header h1{{font-size:18px;color:#2962ff}}
.header p{{font-size:11px;color:#787b86;margin-top:5px}}
.tabs{{display:flex;background:#1e222d;position:sticky;top:60px;z-index:99}}
.tab{{flex:1;padding:12px 8px;text-align:center;cursor:pointer;font-size:13px;font-weight:600;color:#787b86;border-bottom:2px solid transparent;transition:all .3s}}
.tab.active{{color:#2962ff;border-bottom:2px solid #2962ff}}
.count{{font-size:10px;color:#787b86;margin-top:3px}}
.content{{display:none;padding:10px}}
.content.active{{display:block}}
.stock-card{{background:#1e222d;border-radius:12px;padding:14px;margin-bottom:10px;cursor:pointer;transition:all .3s}}
.stock-card:hover{{background:#262d3f}}
.stock-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}}
.stock-info{{flex:1}}
.stock-name{{font-weight:600;font-size:15px;color:#fff}}
.stock-ticker{{color:#787b86;font-size:12px;margin-top:2px}}
.stock-price{{font-size:18px;font-weight:700;color:#fff}}
.stock-metrics{{display:grid;grid-template-columns:repeat(5,1fr);gap:6px}}
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
</style>
</head>
<body>
<div class="header">
    <h1>📈 Trading Screener</h1>
    <p>SPY 6M: {spy_perf:.1f}% | VCP | Qullamaggie | HTF</p>
</div>
<div class="tabs">
    <div class="tab active" onclick="showTab('vcp')">VCP<span class="count">{len(vcp)} stocks</span></div>
    <div class="tab" onclick="showTab('ql')">Qullamaggie<span class="count">{len(ql)} stocks</span></div>
    <div class="tab" onclick="showTab('htf')">HTF<span class="count">{len(htf)} stocks</span></div>
</div>
<div id="vcp" class="content active">{vcp_html}</div>
<div id="ql" class="content">{ql_html}</div>
<div id="htf" class="content">{htf_html}</div>
<script>
function showTab(name){{
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.content').forEach(c=>c.classList.remove('active'));
    document.querySelector(`.tab[onclick="showTab(\'${name}\')"]`).classList.add('active');
    document.getElementById(name).classList.add('active');
}}
function openChart(ticker, name){{
    window.open(`https://www.tradingview.com/chart/?symbol=${ticker}`, '_blank');
}}
</script>
</body>
</html>'''
    return html

html = generate_html(vcp, ql, htf, spy_perf, iv_data, company_data)
output = 'screener.html'
with open(output, 'w') as f:
    f.write(html)
print(f"Done in {time.time()-start:.1f}s: {output}")
