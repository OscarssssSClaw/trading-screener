# Trading Screener

Automated stock screener tracking VCP, Qullamaggie, and HTF patterns.

## Live Version
https://OscarssssSClaw.github.io/trading-screener/screener.html

## Strategies Tracked

### VCP (Volatility Contraction Pattern)
- Distance to 52W High ≤ 25%
- 6M Return ≥ 50%
- Price above 50-day MA

### Qullamaggie
- Distance to 52W High ≤ 15%
- 6M Return ≥ 50%
- Price above 20-day MA

### HTF (High Tight Flag)
- 6M Return: 50-150%
- Distance to 52W High ≤ 20%
- ADR: 3-15%

## Auto-Update
Runs weekdays at 9:30 AM HK time via GitHub Actions.
