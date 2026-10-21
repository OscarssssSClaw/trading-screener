import unittest

import pandas as pd

from market_scan import paginate_scan, prepare_quotes
from screen_rules import (
    ADV_MIN_DOLLARS,
    HTF_MAX_DRAWDOWN_PCT,
    HTF_POLE_MIN_PCT,
    MIN_MARKET_CAP,
    QL_PERCENTILE_MIN,
    ScanError,
    add_leadership_scores,
    assert_publishable,
    classify_board,
    clip_text,
    contraction_stats,
    dist_from_high_pct,
    evaluate_htf,
    evaluate_ql,
    evaluate_vcp,
    htf_coarse_mask,
    is_near_52w_high,
    passes_liquidity,
    ql_coarse_mask,
    sector_strength,
    sma200_is_rising,
    trend_template_checks,
    vcp_coarse_mask,
)


def bar(close, volume=1_000_000, high=None, low=None):
    return {
        'open': close,
        'high': close if high is None else high,
        'low': close if low is None else low,
        'close': close,
        'volume': volume,
    }


def htf_bars(pole_days=20, flag_days=10, start=10.0, end=18.0, flag_close=17.6, flag_low=None,
             pole_volume=1_000_000, flag_volume=400_000):
    bars = []
    step = (end - start) / (pole_days - 1)
    for i in range(pole_days):
        close = start + step * i
        bars.append(bar(close, pole_volume))
    low = flag_close if flag_low is None else flag_low
    for _ in range(flag_days):
        bars.append({
            'open': flag_close,
            'high': max(flag_close, end * 0.99),
            'low': low,
            'close': flag_close,
            'volume': flag_volume,
        })
    return bars


def vcp_bars(last_close=130.0, widths=(10.0, 7.0, 4.0), volumes=(1_500_000, 1_200_000, 800_000)):
    bars = [bar(100.0, 1_000_000) for _ in range(200)]
    for width, volume in zip(widths, volumes):
        for _ in range(10):
            bars.append({
                'open': last_close,
                'high': last_close + width / 2.0,
                'low': last_close - width / 2.0,
                'close': last_close,
                'volume': volume,
            })
    return bars


def ql_bars(end=30.0):
    bars = []
    for i in range(25):
        close = 20 + (end - 20) * i / 24
        bars.append(bar(close, 1_000_000, high=close * 1.01, low=close * 0.99))
    for _ in range(15):
        bars.append(bar(end, 800_000, high=end + 0.4, low=end - 0.3))
    return bars


def quote(**overrides):
    row = {
        'ticker': 'NASDAQ:TEST',
        'close': 100.0,
        'SMA10': 99.0,
        'SMA20': 97.0,
        'SMA50': 90.0,
        'SMA150': 80.0,
        'SMA200': 70.0,
        'price_52_week_high': 110.0,
        'price_52_week_low': 50.0,
        'High.All': 500.0,
        'ADR': 5.0,
        'liquid': True,
        'adr_ok': True,
        'dist_52w': dist_from_high_pct(100.0, 110.0),
        'rs_pct': 80.0,
        'ql_pct': 97.0,
        'Perf.1M': 20.0,
        'Perf.3M': 40.0,
        'Perf.6M': 220.0,
        'Perf.Y': 80.0,
        'adv_dollar': 5_000_000.0,
        'average_volume_30d_calc': 50_000.0,
        'market_cap_basic': 20_000_000_000.0,
    }
    row.update(overrides)
    return row


class LiquidityTests(unittest.TestCase):
    def test_adv_dollar_not_share_volume(self):
        large = MIN_MARKET_CAP
        # $80 and 50k shares is $4M. The old share-volume > 1M gate would drop this.
        self.assertTrue(passes_liquidity(80, 50_000, large))
        self.assertGreaterEqual(80 * 50_000, ADV_MIN_DOLLARS)
        # 1.2M shares at $2 is $2.4M and under the $5 price floor. Share volume alone is not enough.
        self.assertFalse(passes_liquidity(2, 1_200_000, large))
        self.assertFalse(passes_liquidity(10, 200_000, large))
        self.assertTrue(passes_liquidity(10, 400_000, large))
        self.assertTrue(passes_liquidity(80, 50_000, 5_000_000_000))
        self.assertFalse(passes_liquidity(80, 50_000, 4_999_999_999))
        self.assertFalse(passes_liquidity(80, 50_000, None))

    def test_prepare_quotes_uses_dollar_volume_and_market_cap(self):
        frame = pd.DataFrame([
            {
                'ticker': 'NASDAQ:PRICEY',
                'name': 'PRICEY',
                'description': 'Pricey Inc.',
                'close': 80,
                'volume': 40_000,
                'average_volume_30d_calc': 50_000,
                'ADR': 6,
                'price_52_week_high': 82,
                'price_52_week_low': 40,
                'exchange': 'NASDAQ',
                'market_cap_basic': MIN_MARKET_CAP,
            },
            {
                'ticker': 'NASDAQ:KOD',
                'name': 'KOD',
                'description': 'Small Cap Inc.',
                'close': 40,
                'volume': 2_000_000,
                'average_volume_30d_calc': 2_000_000,
                'ADR': 8,
                'price_52_week_high': 42,
                'price_52_week_low': 10,
                'exchange': 'NASDAQ',
                'market_cap_basic': 1_200_000_000,
            },
            {
                'ticker': 'NASDAQ:NOCAP',
                'name': 'NOCAP',
                'description': 'Missing Cap Inc.',
                'close': 80,
                'volume': 50_000,
                'average_volume_30d_calc': 50_000,
                'ADR': 6,
                'price_52_week_high': 82,
                'price_52_week_low': 40,
                'exchange': 'NASDAQ',
                'market_cap_basic': None,
            },
        ])
        prepared = prepare_quotes(frame)
        pricey = prepared.loc[prepared['ticker'] == 'NASDAQ:PRICEY'].iloc[0]
        small = prepared.loc[prepared['ticker'] == 'NASDAQ:KOD'].iloc[0]
        missing = prepared.loc[prepared['ticker'] == 'NASDAQ:NOCAP'].iloc[0]
        self.assertTrue(bool(pricey['liquid']))
        self.assertEqual(pricey['company'], 'Pricey Inc.')
        self.assertAlmostEqual(pricey['adv_dollar'], 4_000_000)
        self.assertFalse(bool(small['liquid']))
        self.assertFalse(bool(missing['liquid']))

    def test_strategy_gates_reject_sub_five_billion(self):
        small = quote(ticker='NASDAQ:KOD', market_cap_basic=1_200_000_000, liquid=True)
        self.assertFalse(evaluate_vcp(small, vcp_bars())['passes'])
        self.assertIn('market_cap', evaluate_vcp(small, vcp_bars())['note'])
        self.assertFalse(evaluate_ql(small, ql_bars())['passes'])
        self.assertFalse(evaluate_htf(small, htf_bars())['passes'])
        frame = pd.DataFrame([small])
        self.assertFalse(bool(vcp_coarse_mask(frame).iloc[0]))
        self.assertFalse(bool(ql_coarse_mask(frame).iloc[0]))
        self.assertFalse(bool(htf_coarse_mask(frame).iloc[0]))
        bare = pd.DataFrame([{key: value for key, value in quote().items() if key != 'market_cap_basic'}])
        self.assertFalse(bool(vcp_coarse_mask(bare).iloc[0]))
        self.assertFalse(bool(ql_coarse_mask(bare).iloc[0]))
        self.assertFalse(bool(htf_coarse_mask(bare).iloc[0]))
        high = pd.DataFrame([quote(ticker='NASDAQ:KOD', dist_52w=0.05, liquid=True, market_cap_basic=800_000_000)])
        board, _stats = classify_board(
            pd.DataFrame([quote(ticker='NYSE:BIG', dist_52w=20, ql_pct=10, rs_pct=10, **{'Perf.1M': 1, 'Perf.3M': 1, 'Perf.6M': 1})]),
            high,
            lambda ticker: None,
        )
        self.assertTrue(board.empty or 'NASDAQ:KOD' not in set(board['ticker']))


class DistanceTests(unittest.TestCase):
    def test_primary_gate_is_52w_not_ath(self):
        dist = dist_from_high_pct(90, 100)
        self.assertAlmostEqual(dist, 10.0)
        self.assertAlmostEqual(dist_from_high_pct(90, 200), 55.0)
        checks = trend_template_checks(quote(close=90, price_52_week_high=100, dist_52w=10, **{'High.All': 200}))
        self.assertTrue(checks['within_25_of_52w_high'])

    def test_near_52w_boundary(self):
        self.assertTrue(is_near_52w_high(99.9, 100))
        self.assertFalse(is_near_52w_high(99.89, 100))
        self.assertTrue(is_near_52w_high(100.2, 100))


class VcpTests(unittest.TestCase):
    def test_template_and_contraction_required(self):
        bars = vcp_bars()
        self.assertTrue(sma200_is_rising(bars))
        self.assertTrue(contraction_stats(bars)['passes'])
        self.assertTrue(evaluate_vcp(quote(), bars)['passes'])

        no_dry = vcp_bars(volumes=(1_000_000, 1_000_000, 2_000_000))
        self.assertFalse(contraction_stats(no_dry)['passes'])
        self.assertFalse(evaluate_vcp(quote(), no_dry)['passes'])

        mild = vcp_bars(widths=(10.0, 12.0, 9.1))
        self.assertFalse(contraction_stats(mild)['passes'])
        tightened = vcp_bars(widths=(10.0, 12.0, 8.5))
        self.assertTrue(contraction_stats(tightened)['passes'])

    def test_rs_floor_and_sma_slope(self):
        checks = trend_template_checks(quote(rs_pct=69.9))
        self.assertFalse(checks['rs_proxy_70'])
        checks = trend_template_checks(quote(rs_pct=70))
        self.assertTrue(checks['rs_proxy_70'])
        falling = vcp_bars(last_close=70)
        self.assertIs(sma200_is_rising(falling), False)
        self.assertFalse(evaluate_vcp(quote(), falling)['passes'])
        self.assertFalse(evaluate_vcp(quote(), vcp_bars()[:100])['passes'])

    def test_coarse_mask_ignores_ath(self):
        frame = pd.DataFrame([quote(), quote(ticker='NASDAQ:WEAK', rs_pct=50, ADR=3)])
        mask = vcp_coarse_mask(frame)
        self.assertTrue(bool(mask.iloc[0]))
        self.assertFalse(bool(mask.iloc[1]))


class QullamaggieTests(unittest.TestCase):
    def test_percentile_not_fixed_return(self):
        strong = quote(ql_pct=QL_PERCENTILE_MIN, **{'Perf.6M': 15})
        weak = quote(ql_pct=90, **{'Perf.6M': 180})
        frame = pd.DataFrame([strong, weak])
        mask = ql_coarse_mask(frame)
        self.assertTrue(bool(mask.iloc[0]))
        self.assertFalse(bool(mask.iloc[1]))
        self.assertTrue(evaluate_ql(strong, ql_bars())['passes'])
        self.assertFalse(evaluate_ql(weak, ql_bars())['passes'])

    def test_needs_tight_near_rising_mas(self):
        falling = []
        for i in range(40):
            close = 40 - i * 0.4
            falling.append(bar(close, 1_000_000, high=close * 1.02, low=close * 0.98))
        self.assertFalse(evaluate_ql(quote(), falling)['passes'])
        extended = ql_bars(end=30)
        extended[-1] = bar(40, 800_000, high=40.2, low=39.5)
        self.assertFalse(evaluate_ql(quote(close=40, dist_52w=2), extended)['passes'])
        far = quote(dist_52w=16)
        self.assertFalse(evaluate_ql(far, ql_bars())['passes'])


class HighTightFlagTests(unittest.TestCase):
    def _row(self, **overrides):
        return quote(dist_52w=5, **overrides)

    def test_pole_floor_and_flag_geometry(self):
        self.assertTrue(evaluate_htf(self._row(), htf_bars(end=18.0))['passes'])
        self.assertFalse(evaluate_htf(self._row(), htf_bars(end=17.9))['passes'])
        self.assertGreaterEqual(HTF_POLE_MIN_PCT, 80)
        self.assertFalse(evaluate_htf(self._row(), htf_bars(pole_days=19))['passes'])
        self.assertFalse(evaluate_htf(self._row(), htf_bars(flag_days=9))['passes'])
        self.assertTrue(evaluate_htf(self._row(), htf_bars(flag_low=18 * (1 - HTF_MAX_DRAWDOWN_PCT / 100.0)))['passes'])
        self.assertFalse(evaluate_htf(self._row(), htf_bars(flag_low=18 * 0.74))['passes'])
        self.assertFalse(evaluate_htf(self._row(), htf_bars(flag_volume=1_000_000))['passes'])

    def test_no_six_month_cap_and_no_adr_ceiling(self):
        row = self._row(**{'Perf.6M': 250, 'ADR': 22})
        result = evaluate_htf(row, htf_bars())
        self.assertTrue(result['passes'])
        self.assertIn('not capped', result['note'])
        frame = pd.DataFrame([
            row,
            self._row(ticker='NASDAQ:QUIET', **{'Perf.1M': 1, 'Perf.3M': 1, 'Perf.6M': 10}),
            self._row(ticker='NASDAQ:OLD_CAP', **{'Perf.6M': 180, 'Perf.1M': 1, 'Perf.3M': 1}),
        ])
        mask = htf_coarse_mask(frame)
        self.assertTrue(bool(mask.iloc[0]))
        self.assertFalse(bool(mask.iloc[1]))
        self.assertTrue(bool(mask.iloc[2]))


class BoardTests(unittest.TestCase):
    def test_percentiles_are_within_the_liquid_set(self):
        rows = []
        for index in range(5):
            rows.append(quote(
                ticker=f'NASDAQ:S{index}',
                **{'Perf.1M': index * 10, 'Perf.3M': index * 10, 'Perf.6M': index * 5, 'Perf.Y': index * 5},
            ))
        scored = add_leadership_scores(pd.DataFrame(rows))
        self.assertEqual(int(scored.loc[scored['ticker'] == 'NASDAQ:S4', 'rs_rating'].iloc[0]), 99)
        self.assertGreaterEqual(float(scored.loc[scored['ticker'] == 'NASDAQ:S4', 'ql_pct'].iloc[0]), 95)
        self.assertLess(float(scored.loc[scored['ticker'] == 'NASDAQ:S2', 'ql_pct'].iloc[0]), 95)

    def test_classify_keeps_strong_pole_and_fails_closed_without_history(self):
        row = quote(ticker='NASDAQ:POLE', dist_52w=4, ql_pct=10, rs_pct=10, **{'Perf.6M': 250})
        liquid = pd.DataFrame([row])
        high = pd.DataFrame([quote(ticker='NASDAQ:POLE', dist_52w=0.05, liquid=True)])
        board, stats = classify_board(liquid, high, lambda ticker: htf_bars())
        self.assertTrue(bool(board.iloc[0]['is_htf']))
        self.assertFalse(bool(board.iloc[0]['is_vcp']))
        self.assertEqual(float(board.iloc[0]['Perf.6M']), 250)
        self.assertEqual(stats['htf'], 1)
        with self.assertRaises(ScanError):
            classify_board(liquid, high, lambda ticker: None)

    def test_52w_names_do_not_require_pattern_history(self):
        row = quote(ticker='NYSE:HIGH', dist_52w=0.05, ql_pct=10, rs_pct=10, **{'Perf.1M': 1, 'Perf.3M': 1, 'Perf.6M': 1})
        liquid = pd.DataFrame([row])
        called = []

        def load(ticker):
            called.append(ticker)
            return None

        board, _stats = classify_board(liquid, pd.DataFrame([row]), load)
        self.assertEqual(called, [])
        self.assertTrue(bool(board.iloc[0]['is_52w_high']))
        self.assertEqual(board.iloc[0]['name'], board.iloc[0]['symbol'])

    def test_publish_guard(self):
        assert_publishable(universe_total=3000, fetched=3000, liquid_count=800, scored_rs=800, board_count=40)
        with self.assertRaises(ScanError):
            assert_publishable(universe_total=3000, fetched=1000, liquid_count=800, scored_rs=800, board_count=40)
        with self.assertRaises(ScanError):
            assert_publishable(universe_total=3000, fetched=3000, liquid_count=800, scored_rs=800, board_count=3)

    def test_sector_strength_orders_by_count(self):
        counts = sector_strength(['Technology', 'Energy', 'Technology', '', None, '-', 'Energy', 'Technology'])
        self.assertEqual(counts, [('Technology', 3), ('Energy', 2)])

    def test_pagination_refuses_partial_results(self):
        def one_page(offset, limit):
            self.assertEqual(offset, 0)
            return 3, pd.DataFrame({'ticker': ['A', 'B', 'C']})

        frame, total = paginate_scan(one_page, page_size=1000, label='ok')
        self.assertEqual(total, 3)
        self.assertEqual(len(frame), 3)

        def short(offset, limit):
            if offset == 0:
                return 2500, pd.DataFrame({'ticker': [f'T{i}' for i in range(1000)]})
            return 2500, pd.DataFrame({'ticker': []})

        with self.assertRaises(ScanError):
            paginate_scan(short, page_size=1000, label='partial')

        def stuck(offset, limit):
            return 5000, pd.DataFrame({'ticker': ['SAME']})

        with self.assertRaises(ScanError):
            paginate_scan(stuck, page_size=1, max_rows=5, label='stuck')

    def test_clip_text(self):
        self.assertEqual(clip_text('short text'), 'short text')
        clipped = clip_text('one two three four', 10)
        self.assertTrue(clipped.endswith('…'))
        self.assertLessEqual(len(clipped), 10)


if __name__ == '__main__':
    unittest.main()
