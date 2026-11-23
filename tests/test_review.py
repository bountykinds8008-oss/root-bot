"""③成績レビュアーと、実行の間隔が空いたときの損切り判定のテスト:  python -m unittest -v"""
import json
import math
import unittest
from unittest import mock

from bot import bitbank, config as C, review
from bot.backtest import run
from bot.engine import new_state
from tests.test_engine import T0, data_from

H = 3600_000
RESEARCH = {"generated": "2026-11-11T22:30:17+09:00", "n_variants": 128, "all": [{
    "params": {"tf": "4hour", "entry": "donchian", "fast": 20, "slow": 50, "don_in": 120, "don_out": 40,
               "atr_k": 4.0, "regime": False, "voltarget": 0.6, "fee": "taker"},
    "trades": 176,
    "in": {"total": 2.94, "cagr": 0.41, "mdd": -0.286, "hit5": 8, "months": 48, "avg_m": 0.035},
    "out": {"total": 0.2046, "cagr": 0.105, "mdd": -0.253, "hit5": 3, "months": 23, "avg_m": 0.0105}}]}


def market_of(data):
    return {p: {"bars": b[-200:], "bid": b[-1][3], "ask": b[-1][3], "low": b[-1][2]} for p, b in data.items()}


class TradeStatsTest(unittest.TestCase):
    def test_breakeven_and_payoff(self):
        tr = [{"pair": "btc_jpy", "side": "buy", "fee": 10, "pnl": None},
              {"pair": "btc_jpy", "side": "sell", "fee": 10, "pnl": 310},     # 正味 +300
              {"pair": "eth_jpy", "side": "buy", "fee": 10, "pnl": None},
              {"pair": "eth_jpy", "side": "sell", "fee": 10, "pnl": -90},     # 正味 -100
              {"pair": "xrp_jpy", "side": "buy", "fee": 10, "pnl": None},
              {"pair": "xrp_jpy", "side": "sell", "fee": 10, "pnl": -90}]     # 正味 -100
        s = review.trade_stats(tr)
        self.assertEqual((s["closed"], s["wins"], s["losses"], s["entries"]), (3, 1, 2, 3))
        self.assertAlmostEqual(s["avg_win"], 300)
        self.assertAlmostEqual(s["avg_loss"], 100)
        self.assertAlmostEqual(s["payoff"], 3.0)
        self.assertAlmostEqual(s["breakeven_win_rate"], 0.25)     # 100 /(300+100)
        self.assertAlmostEqual(s["expectancy"], 100 / 3)
        self.assertAlmostEqual(s["fees_total"], 60)

    def test_win_rate_51_can_still_lose(self):
        # 勝率51%でも、平均損失が平均利益より大きければ損益分岐を下回る
        tr = []
        for i in range(100):
            tr += [{"pair": "btc_jpy", "side": "buy", "fee": 0, "pnl": None},
                   {"pair": "btc_jpy", "side": "sell", "fee": 0, "pnl": 100 if i < 51 else -120}]
        s = review.trade_stats(tr)
        self.assertAlmostEqual(s["win_rate"], 0.51)
        self.assertGreater(s["breakeven_win_rate"], s["win_rate"])
        self.assertLess(s["expectancy"], 0)

    def test_no_trades(self):
        s = review.trade_stats([])
        self.assertEqual(s["closed"], 0)
        self.assertIsNone(s["win_rate"])
        self.assertIsNone(s["breakeven_win_rate"])


class BuildTest(unittest.TestCase):
    def test_fresh_bot_is_waiting_not_alarming(self):
        data = data_from(lambda i, p, r: p * (1 + r.gauss(0, 0.002)), n=260)
        mk = market_of(data)
        now = data["btc_jpy"][-1][5] + C.BAR_MS
        st = new_state(100_000, "paper")
        st["month"], st["month_start_equity"] = "2026-01", 100_000.0
        st["equity_history"] = [[now - 5 * 24 * H + k * H, 100_000.0] for k in range(5 * 24 + 1)]
        rv = review.build(st, mk, now, RESEARCH)
        self.assertEqual(rv["level"], 1)                      # データ待ち。警告ではない
        self.assertEqual(len(rv["team"]), 4)
        self.assertEqual([t["no"] for t in rv["team"]], [1, 2, 3, 4])
        fit = next(c for c in rv["checks"] if c["key"] == "fit")
        self.assertEqual(fit["status"], "ok")                 # 月2.5回の作戦で5日0回は普通
        self.assertAlmostEqual(rv["fit"]["p_zero"], math.exp(-(176 / 71) * 5 / 30.44), places=6)
        self.assertEqual(rv["ready_count"], 2)                # 間隔と落ち込みだけ満たす
        json.dumps(rv)                                        # 画面に渡せる形であること
        self.assertIn("判定：", review.to_text(rv))

    def test_sparse_ticks_warn_and_stale_alert(self):
        data = data_from(lambda i, p, r: p * (1 + r.gauss(0, 0.002)), n=260)
        mk = market_of(data)
        now = data["btc_jpy"][-1][5] + C.BAR_MS
        st = new_state(100_000, "paper")
        st["month"], st["month_start_equity"] = "2026-01", 100_000.0
        st["equity_history"] = [[now - k * 7 * H, 100_000.0] for k in range(10, -1, -1)]   # 7時間おき
        ops = next(c for c in review.build(st, mk, now, RESEARCH)["checks"] if c["key"] == "ops")
        self.assertEqual(ops["status"], "warn")
        st["equity_history"] = [[now - 40 * H, 100_000.0], [now - 20 * H, 100_000.0], [now, 100_000.0]]
        ops = next(c for c in review.build(st, mk, now, RESEARCH)["checks"] if c["key"] == "ops")
        self.assertEqual(ops["status"], "alert")

    def test_zero_entries_after_long_time_is_flagged(self):
        data = data_from(lambda i, p, r: p * (1 + r.gauss(0, 0.002)), n=260)
        mk = market_of(data)
        now = data["btc_jpy"][-1][5] + C.BAR_MS
        st = new_state(100_000, "paper")
        st["month"], st["month_start_equity"] = "2026-01", 100_000.0
        st["equity_history"] = [[now - 60 * 24 * H + k * H, 100_000.0] for k in range(60 * 24 + 1)]
        rv = review.build(st, mk, now, RESEARCH)
        fit = next(c for c in rv["checks"] if c["key"] == "fit")
        self.assertEqual(fit["status"], "warn")               # 60日で0回は見込み1%未満
        self.assertEqual(rv["verdict"], "注意")

    def test_after_real_run_numbers_are_consistent(self):
        data = data_from(lambda i, p, r: p * (1.0015 + r.gauss(0, 0.003)))
        st = run(data)
        now = st["equity_history"][-1][0]
        mk = {p: {"bars": b, "bid": b[-1][3], "ask": b[-1][3], "low": b[-1][2]} for p, b in data.items()}
        rv = review.build(st, mk, now, RESEARCH)
        self.assertEqual(set(st["bench_start"]), set(C.PAIRS))   # 開始時の価格を一度だけ記録
        sells = [t for t in st["trades"] if t["side"] == "sell"]
        self.assertEqual(rv["trades"]["closed"], len(sells))
        self.assertAlmostEqual(rv["performance"]["equity"], st["equity_history"][-1][1])
        self.assertLessEqual(rv["performance"]["max_drawdown"], 0)
        self.assertIsNotNone(rv["performance"]["hold_return"])
        # 正味の合計 = 売りの損益合計 − 買い手数料合計（決済済みの分）
        self.assertLessEqual(rv["trades"]["net_total"], sum(t["pnl"] for t in sells) + 1e-6)

    def test_old_backtest_is_flagged(self):
        old = {"funnel": {"filtered": {"pause": 1266, "volatile": 38}}}
        self.assertFalse(review.backtest_matches_strategy(old))
        self.assertTrue(review.backtest_matches_strategy({"funnel": {"filtered": {"spread": 3}}}))
        self.assertIsNone(review.backtest_matches_strategy(None))

    def test_no_research_does_not_crash(self):
        data = data_from(lambda i, p, r: p * (1 + r.gauss(0, 0.002)), n=260)
        st = new_state(100_000, "paper")
        rv = review.build(st, market_of(data), T0 + 300 * C.BAR_MS, None)
        self.assertEqual(rv["team"][1]["status"], "wait")
        self.assertIn(rv["level"], (0, 1))


class LowSinceTest(unittest.TestCase):
    def test_uses_only_bars_after_last_tick(self):
        since = 1794800000000
        rows = [["100", "110", "50", "105", "1", since - H],      # 前回より前：使わない
                ["105", "108", "90", "100", "1", since],          # 使う
                ["100", "103", "95", "101", "1", since + H]]      # 使う

        def fake(req, retries=3):
            return {"candlestick": [{"ohlcv": rows}]}
        with mock.patch.object(bitbank, "_http", side_effect=fake):
            self.assertEqual(bitbank.low_since("btc_jpy", since), 90.0)

    def test_failure_returns_none(self):
        with mock.patch.object(bitbank, "_http", side_effect=TimeoutError()):
            self.assertIsNone(bitbank.low_since("btc_jpy", 1794800000000))


if __name__ == "__main__":
    unittest.main()
