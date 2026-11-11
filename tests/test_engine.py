"""合成データでの動作テスト:  python -m unittest -v"""
import math
import random
import unittest

from bot import config as C
from bot.backtest import run, summarize
from bot.engine import month_key

H = 3600_000
T0 = 1767225600000  # 2026-01-01 00:00 UTC


def series(fn, n, base, seed=1):
    rnd, bars, p = random.Random(seed), [], base
    for i in range(n):
        o = p
        p = max(base * 0.01, fn(i, p, rnd))
        h, l = max(o, p) * (1 + rnd.random() * 0.002), min(o, p) * (1 - rnd.random() * 0.002)
        bars.append((o, h, l, p, 1.0, T0 + i * H))
    return bars


def data_from(fn, n=24 * 60, seeds=(1, 2, 3)):
    bases = {"btc_jpy": 15_000_000, "eth_jpy": 500_000, "xrp_jpy": 300}
    return {p: series(fn, n, bases[p], s) for p, s in zip(C.PAIRS, seeds)}


class EngineTest(unittest.TestCase):
    def test_uptrend_makes_money(self):
        st = run(data_from(lambda i, p, r: p * (1.0015 + r.gauss(0, 0.003))))
        s = summarize(st)
        self.assertGreater(s["trades"] + len(st["positions"]), 0)
        self.assertGreater(s["end"], s["start"])

    def test_crash_respects_monthly_loss_cap(self):
        # 上昇 → 急落。月間の損失が上限(-20%)を大きく超えないこと
        def fn(i, p, r):
            return p * (1.002 if i < 24 * 20 else 0.985) * (1 + r.gauss(0, 0.002))
        st = run(data_from(fn))
        for m, v in st["monthly"].items():
            if v.get("start") and v.get("end"):
                self.assertGreater(v["end"] / v["start"] - 1, -C.MONTHLY_MAX_LOSS - 0.05, m)

    def test_halt_blocks_new_entries_until_next_month(self):
        def fn(i, p, r):
            return p * (1.003 if (i // 48) % 2 == 0 else 0.99) * (1 + r.gauss(0, 0.002))
        st = run(data_from(fn, n=24 * 75))
        halts = [x for _, x in st["log"] if "月間損失上限" in x]
        for t in st["trades"]:
            if t["side"] == "buy" and st["halted_month"] == month_key(t["ts"]):
                halt_ts = min(ts for ts, x in st["log"] if "月間損失上限" in x
                              and month_key(ts) == month_key(t["ts"]))
                self.assertLess(t["ts"], halt_ts)
        self.assertIsInstance(halts, list)

    def test_random_walk_no_crash_and_fees_counted(self):
        st = run(data_from(lambda i, p, r: p * math.exp(r.gauss(0, 0.006)), seeds=(7, 8, 9)))
        s = summarize(st)
        self.assertTrue(all(t["fee"] >= 0 for t in st["trades"]))
        self.assertGreater(s["end"], 0)
        self.assertGreaterEqual(st["cash"], 0)

    def test_bot_stats_match_trades(self):
        st = run(data_from(lambda i, p, r: p * math.exp(r.gauss(0.0003, 0.006)), seeds=(4, 5, 6)))
        for pair, v in st.get("bot_stats", {}).items():
            sells = [t for t in st["trades"] if t["pair"] == pair and t["side"] == "sell"]
            self.assertEqual(v["trades"], len(sells))
            self.assertEqual(v["wins"], sum(1 for t in sells if t["pnl"] > 0))

    def test_filters_reject_surge_and_record_funnel(self):
        from bot.engine import risk_filter, signal
        base = series(lambda i, p, r: p * 1.002, 200, 15_000_000)
        surge = base[:-4] + [(b[0], b[1] * 1.03 ** (k + 1), b[2], b[3] * 1.03 ** (k + 1), 1.0, b[5])
                             for k, b in enumerate(base[-4:])]
        sig = signal(surge)
        m = {"bid": sig["close"], "ask": sig["close"]}
        C.USE_SURGE_FILTER = True
        try:
            self.assertEqual(risk_filter(surge, sig, m, {}), "surge")
        finally:
            C.USE_SURGE_FILTER = False
        m2 = {"bid": sig["close"] * 0.99, "ask": sig["close"] * 1.01}
        self.assertEqual(risk_filter(base, signal(base), m2, {}), "spread")

    def test_breakout_signal(self):
        from bot.engine import signal
        flat = series(lambda i, p, r: p * (1 + r.gauss(0, 0.002)), 180, 1_000_000)
        up = flat + [(flat[-1][3], flat[-1][3] * 1.2, flat[-1][3], flat[-1][3] * 1.15, 1.0, flat[-1][5] + H)]
        self.assertTrue(signal(up)["entry"])
        down = flat + [(flat[-1][3], flat[-1][3], flat[-1][3] * 0.8, flat[-1][3] * 0.85, 1.0, flat[-1][5] + H)]
        self.assertTrue(signal(down)["exit_trend"])

    def test_funnel_counts_add_up(self):
        st = run(data_from(lambda i, p, r: p * math.exp(r.gauss(0.0004, 0.006)), seeds=(11, 12, 13)))
        for mk, f in st.get("funnel", {}).items():
            self.assertEqual(f["checked"], f["no_setup"] + sum(f["filtered"].values()) + f["entered"], mk)


if __name__ == "__main__":
    unittest.main()
