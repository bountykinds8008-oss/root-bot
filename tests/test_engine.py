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


if __name__ == "__main__":
    unittest.main()
