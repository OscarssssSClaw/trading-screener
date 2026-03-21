"""
Generate HTML screener with TradingView charts + IV from yfinance
"""

from tradingview_screener import Query, Column
import pandas as pd
import yfinance as yf
from datetime import datetime
import time

def get_all_stocks():
    total, df = (
        Query()
        .select('name', 'close', 'volume', 'ADR', 'Perf.6M', 'SMA20', 'SMA50', 'High.All', 'RSI')
        .where(Column('volume') > 1_000_000)
        .order_by('volume', ascending=False)
        .limit(1000)
        .get_scanner_data()
    )
    
    df = df.dropna(subset=['High.All', 'close', 'SMA20', 'SMA50', 'Perf.6M', 'ADR'])
    df['dist_high'] = (df['High.All'] - df['close']) / df['High.All'] * 100
    
    spy_perf = 0
    try:
        spy_result, spy_df = Query().select('Perf.6M').where(Column('name') == 'SPY').limit(1).get_scanner_data()
        if len(spy_df) > 0:
            spy_perf = spy_df['Perf.6M'].iloc[0]
    except:
        pass
    
    df['RS'] = df['Perf.6M'] - spy_perf
    return df, spy_perf

def get_company_info(df):
    print("Fetching company info...")
    tickers = df['ticker'].tolist()
    company_data = {}
    count = 0
    
    for ticker in tickers:
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
        
        count += 1
        if count % 30 == 0:
            print(f"  {count}/{len(tickers)}...")
    
    print(f"Got info for {len(company_data)} companies")
    return company_data

def get_iv_for_stocks(df):
    print("Fetching IV from yfinance options...")
    tickers = df['ticker'].tolist()
    iv_data = {}
    count = 0
    
    for ticker in tickers:
        if ':' not in ticker:  # Skip non-exchange tickers
            continue
            
        symbol = ticker.split(':')[1]
        
        # Skip OTC stocks
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
                
            active_calls = opt.calls[opt.calls['bid'] > 0]
            if len(active_calls) == 0:
                continue
                
            active_calls = active_calls.copy()
            active_calls['dist'] = abs(active_calls['strike'] - stock_price)
            atm_idx = active_calls['dist'].idxmin()
            atm = active_calls.loc[atm_idx]
            iv = atm.get('impliedVolatility', 0)
            
            if iv > 0:
                iv_data[ticker] = iv
                
        except Exception as e:
            pass
        
        count += 1
        if count % 30 == 0:
            print(f"  {count}/{len(tickers)}...")
    
    print(f"Got IV for {len(iv_data)} stocks")
    return iv_data

def screen_vcp(df):
    return df[(df['dist_high'] <= 25) & (df['Perf.6M'] >= 50) & (df['close'] > df['SMA50'])].copy().sort_values('volume', ascending=False)

def screen_qullamaggie(df):
    return df[(df['dist_high'] <= 15) & (df['Perf.6M'] >= 50) & (df['close'] > df['SMA20'])].copy().sort_values('volume', ascending=False)

def screen_htf(df):
    return df[(df['dist_high'] <= 20) & (df['Perf.6M'] >= 50) & (df['Perf.6M'] <= 150) & (df['ADR'] >= 3) & (df['ADR'] <= 15) & (df['Perf.6M'] <= 200) & (df['ADR'] >= 2) & (df['close'] > df['SMA50'])].copy().sort_values('volume', ascending=False)

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
    </div>'''''

def generate_html(vcp, ql, htf, spy_perf, iv_data, company_data):
    vcp_html = ''.join([make_card(row, iv_data, company_data) for _, row in vcp.head(50).iterrows()])
    ql_html = ''.join([make_card(row, iv_data, company_data) for _, row in ql.head(50).iterrows()])
    htf_html = ''.join([make_card(row, iv_data, company_data) for _, row in htf.head(50).iterrows()])
    
    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
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
.metric{{text-align:center;padding:8px 4px;background:#2a2e39;border-radius:8px}}
.metric-label{{font-size:9px;color:#787b86;text-transform:uppercase}}
.metric-value{{font-size:12px;font-weight:600;margin-top:3px}}
.positive{{color:#26a69a}}
.negative{{color:#ef5350}}
.iv-badge{{display:inline-block;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:600;margin-top:3px}}
.iv-low{{background:#26a69a;color:#fff}}
.iv-mid{{background:#ff9800;color:#fff}}
.iv-high{{background:#ef5350;color:#fff}}
.chart-modal{{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:#131722;z-index:1000;flex-direction:column}}
.chart-modal.active{{display:flex}}
.chart-header{{display:flex;justify-content:space-between;align-items:center;padding:12px 15px;background:#1e222d;border-bottom:1px solid #2a2e39}}
.chart-title{{font-weight:600;color:#fff}}
.close-btn{{background:#ef5350;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600}}
.chart-frame{{flex:1;width:100%;border:none}}
</style>
</head>
<body>
<div class="header">
<h1>📈 Trading Screener</h1>
<p>SPY 6M: {spy_perf:.1f}% | {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
</div>
<div class="tabs">
<div class="tab active" data-tab="vcp">VCP<div class="count">{len(vcp)}</div></div>
<div class="tab" data-tab="ql">Qullamaggie<div class="count">{len(ql)}</div></div>
<div class="tab" data-tab="htf">HTF<div class="count">{len(htf)}</div></div>
</div>
<div id="vcp" class="content active">{vcp_html}</div>
<div id="ql" class="content">{ql_html}</div>
<div id="htf" class="content">{htf_html}</div>
<div id="chartModal" class="chart-modal">
<div class="chart-header"><div class="chart-title" id="chartTitle">Chart</div><button class="close-btn" onclick="closeChart()">✕ Close</button></div>
<iframe id="chartFrame" class="chart-frame" src=""></iframe>
</div>
<script>
function switchTab(tab){{document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));document.querySelectorAll('.content').forEach(c=>c.classList.remove('active'));document.getElementById(tab).classList.add('active');document.querySelector('[data-tab="' + tab + '"]').classList.add('active');}}
document.querySelectorAll('.tab').forEach(tab=>{{tab.addEventListener('click',()=>switchTab(tab.dataset.tab));}});
function openChart(ticker,name){{document.getElementById('chartTitle').textContent=name+' ('+ticker+')';const symbol=ticker.includes(':')?ticker.split(':')[1]:ticker;document.getElementById('chartFrame').src='https://www.tradingview.com/widgetembed/?symbol='+symbol+'&interval=D&theme=dark';document.getElementById('chartModal').classList.add('active');}}
function closeChart(){{document.getElementById('chartModal').classList.remove('active');document.getElementById('chartFrame').src='';}}
document.addEventListener('keydown',e=>{{if(e.key==='Escape')closeChart();}});
</script>
</body>
</html>'''
    return html

if __name__ == "__main__":
    print("Fetching data...")
    start = time.time()
    df, spy_perf = get_all_stocks()
    print(f"Got {len(df)} stocks")
    
    vcp = screen_vcp(df)
    ql = screen_qullamaggie(df)
    htf = screen_htf(df)
    print(f"VCP:{len(vcp)} QL:{len(ql)} HTF:{len(htf)}")
    
    company_data = get_company_info(df)
    iv_data = get_iv_for_stocks(df)
    html = generate_html(vcp, ql, htf, spy_perf, iv_data, company_data)
    
    output = '/Users/oscarclaw/.openclaw/workspace-trading/tradingview/screener.html'
    with open(output, 'w') as f:
        f.write(html)
    print(f"Done in {time.time()-start:.0f}s: {output}")
