"""実行が遅れて足を飛ばしたときの「後追い処理」と、本番用の逆指値のテスト:  python -m unittest -v"""
import math
import random
import unittest

from bot import config as C
from bot.brokers import LiveBroker, PaperBroker
from bot.engine import new_state, step

B = C.BAR_MS
T0 = 1767225600000


def bars_from(closes, seed=1):
    rnd, out = random.Random(seed), []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        h, l = max(o, c) * (1 + rnd.random() * 0.002), min(o, c) * (1 - rnd.random() * 0.002)
        out.append((o, h, l, c, 1000.0, T0 + i * B))
        prev = c
    return out


def market_at(bars, i):
    b = bars[i]
    return {"bars": bars[max(0, i - 200): i + 1], "bid": b[3], "ask": b[3], "low": b[2]}


def replay(bars, every=1, pair="btc_jpy"):
    """every 本ごとにしかボットが動かなかった場合を再現する。"""
    state, br = new_state(100_000, "paper"), PaperBroker()
    for i in range(130, len(bars)):
        if (i - 130) % every and i != len(bars) - 1:
            continue
        step(state, br, {pair: market_at(bars, i)}, bars[i][5] + B)
    return state


def flat_then_up_then_crash(n_up=60, n_down=40):
    rnd = random.Random(3)
    c = [10_000_000 * math.exp(rnd.gauss(0, 0.004)) for _ in range(140)]
    for _ in range(n_up):
        c.append(c[-1] * (1.006 + rnd.gauss(0, 0.002)))
    for _ in range(n_down):
        c.append(c[-1] * (0.985 + rnd.gauss(0, 0.002)))
    return bars_from(c)


class CatchUpTest(unittest.TestCase):
    def setUp(self):
        self._pairs = C.PAIRS
        C.PAIRS = ["btc_jpy"]

    def tearDown(self):
        C.PAIRS = self._pairs

    def test_skipped_bars_still_hit_stop(self):
        bars = flat_then_up_then_crash()
        every = replay(bars, 1)
        gappy = replay(bars, 3)    # 12時間に1回しか動かない
        for st in (every, gappy):
            self.assertEqual(st["positions"], {})
            self.assertTrue(any(t["side"] == "sell" for t in st["trades"]))
        # 損切り価格は取引所に置いた逆指値と同じ扱いなので、遅れても結果はほぼ同じ
        e1, e2 = every["equity_history"][-1][1], gappy["equity_history"][-1][1]
        self.assertLess(abs(e1 / e2 - 1), 0.03, (e1, e2))
        self.assertGreater(gappy.get("catchup_bars", 0), 0)

    def test_trailing_stop_rises_across_missed_bars(self):
        bars = flat_then_up_then_crash(n_up=60, n_down=0)
        st = replay(bars, 4)
        pos = st["positions"]["btc_jpy"]
        self.assertGreater(pos["stop"], pos["entry"])   # 上昇中に追従損切りが買値より上へ

    def test_late_entry_when_breakout_was_missed(self):
        rnd = random.Random(5)
        c = [10_000_000 * math.exp(rnd.gauss(0, 0.003)) for _ in range(140)]
        c += [c[-1] * 1.03, c[-1] * 1.031, c[-1] * 1.032]   # 140本目で上抜け、その後ほぼ横ばい
        bars = bars_from(c)
        state, br = new_state(100_000, "paper"), PaperBroker()
        step(state, br, {"btc_jpy": market_at(bars, 139)}, bars[139][5] + B)
        self.assertEqual(state["positions"], {})
        step(state, br, {"btc_jpy": market_at(bars, 142)}, bars[142][5] + B)   # 3本飛ばして実行
        self.assertIn("btc_jpy", state["positions"])
        self.assertIn("後追い", state["trades"][-1]["reason"])
        pos = state["positions"]["btc_jpy"]
        self.assertLess(pos["stop"], pos["entry"])

    def test_no_late_entry_if_price_ran_away(self):
        rnd = random.Random(5)
        c = [10_000_000 * math.exp(rnd.gauss(0, 0.003)) for _ in range(140)]
        c += [c[-1] * 1.03, c[-1] * 1.06, c[-1] * 1.04]
        bars = bars_from(c)
        bars[-1] = bars[-1][:3] + (bars[-1][3], 1000.0, bars[-1][5])
        state, br = new_state(100_000, "paper"), PaperBroker()
        step(state, br, {"btc_jpy": market_at(bars, 139)}, bars[139][5] + B)
        m = market_at(bars, 142)
        m["ask"] = m["bid"] = c[140] * 1.5           # 大きく逃げた後
        step(state, br, {"btc_jpy": m}, bars[142][5] + B)
        self.assertEqual(state["positions"], {})

    def test_bar_before_entry_is_not_used_for_stop(self):
        br = PaperBroker()
        pos = {"stop": 100.0, "opened_at": T0 + B // 2}
        self.assertIsNone(br.stop_hit("btc_jpy", pos, bar=(105, 106, 90, 104, 1, T0)))
        self.assertEqual(br.stop_hit("btc_jpy", pos, bar=(98, 106, 90, 104, 1, T0 + B))["px"], 98)
        self.assertEqual(br.stop_hit("btc_jpy", pos, bar=(105, 106, 99, 104, 1, T0 + B))["px"], 100)


class FakePrivate:
    """bitbank の代わり（本物には一切つながない）。"""
    def __init__(self):
        self.jpy, self.btc, self.locked = 100_000.0, 0.0, 0.0
        self.orders, self.next_id, self.calls = {}, 1, []

    def balances(self):
        return {"jpy": self.jpy, "btc": self.btc - self.locked}

    def pairs_info(self):
        return {"btc_jpy": {"amount_digits": 4, "price_digits": 0, "unit_amount": "0.0001"}}

    def _new(self, **o):
        oid = self.next_id
        self.next_id += 1
        self.orders[oid] = {"order_id": oid, **o}
        return dict(self.orders[oid])

    def order(self, pair, side, amount, ref, price_digits=0):
        self.calls.append(("order", side, amount))
        assert "e" not in amount
        a = float(amount)
        if side == "buy":
            self.jpy -= a * ref
            self.btc += a
        else:
            self.jpy += a * ref
            self.btc -= a
        return self._new(status="FULLY_FILLED", executed_amount=amount, average_price=str(ref))

    def stop_order(self, pair, side, amount, trigger):
        self.calls.append(("stop", amount, trigger))
        self.locked += float(amount)
        return self._new(status="INACTIVE", executed_amount="0", average_price="0", amount=amount)

    def get_order(self, pair, oid):
        return dict(self.orders[oid])

    def cancel_order(self, pair, oid):
        self.calls.append(("cancel", oid))
        o = self.orders[oid]
        if o["status"] == "INACTIVE":
            o["status"] = "CANCELED_UNFILLED"
            self.locked -= float(o["amount"])
        return dict(o)

    def trigger(self, oid, px):   # 取引所で逆指値が約定したことにする
        o = self.orders[oid]
        a = float(o["amount"])
        self.locked -= a
        self.btc -= a
        self.jpy += a * px
        o.update(status="FULLY_FILLED", executed_amount=o["amount"], average_price=str(px))


class LiveBrokerTest(unittest.TestCase):
    def test_stop_order_lifecycle(self):
        fp = FakePrivate()
        br = LiveBroker(fp)
        state = new_state(100_000, "live")
        amt, px, _ = br.buy(state, "btc_jpy", 30_000, 10_000_000)
        self.assertGreater(amt, 0)
        pos = {"amount": amt, "entry": px, "high": px, "opened_at": 0, "stop": 9_600_000}
        br.place_stop(state, "btc_jpy", pos)
        self.assertTrue(pos["stop_order_id"])
        self.assertEqual(fp.calls[-1], ("stop", f"{amt:.4f}", "9600000"))

        # 少しだけ上がった → 置き直さない／大きく上がった → 取消して置き直す
        first = pos["stop_order_id"]
        pos["stop"] = 9_605_000
        br.move_stop(state, "btc_jpy", pos)
        self.assertEqual(pos["stop_order_id"], first)
        pos["stop"] = 9_900_000
        br.move_stop(state, "btc_jpy", pos)
        self.assertNotEqual(pos["stop_order_id"], first)
        self.assertIn(("cancel", first), fp.calls)

        # 取引所で約定 → stop_hit が拾い、二重に売らない
        fp.trigger(pos["stop_order_id"], 9_890_000)
        br2 = LiveBroker(fp)
        hit = br2.stop_hit("btc_jpy", pos, low=9_950_000, bid=9_950_000)
        self.assertTrue(hit and hit["done"])
        n_orders = sum(1 for c in fp.calls if c[0] == "order")
        sold, spx, _ = br2.sell(state, "btc_jpy", pos, 9_950_000, hit=hit)
        self.assertEqual(sum(1 for c in fp.calls if c[0] == "order"), n_orders)
        self.assertAlmostEqual(sold, amt)
        self.assertEqual(spx, 9_890_000)

    def test_sell_cancels_stop_first(self):
        fp = FakePrivate()
        br = LiveBroker(fp)
        state = new_state(100_000, "live")
        amt, px, _ = br.buy(state, "btc_jpy", 30_000, 10_000_000)
        pos = {"amount": amt, "entry": px, "high": px, "opened_at": 0, "stop": 9_600_000}
        br.place_stop(state, "btc_jpy", pos)
        sold, _, _ = br.sell(state, "btc_jpy", pos, 10_100_000)
        self.assertAlmostEqual(sold, amt)          # 拘束が外れて全量売れる
        self.assertAlmostEqual(fp.btc, 0.0)
        self.assertEqual(fp.locked, 0.0)

    def test_live_cap_limits_budget(self):
        fp = FakePrivate()          # 口座には10万円
        br = LiveBroker(fp)
        state = new_state(0, "live")
        self.assertEqual(br.cash(state), C.LIVE_MAX_JPY)
        state["positions"]["btc_jpy"] = {"amount": 0.001, "entry": 10_000_000}
        self.assertEqual(br.cash(state), C.LIVE_MAX_JPY - 10_000)

    def test_small_amount_has_no_exponent(self):
        br = LiveBroker(FakePrivate())
        self.assertEqual(br._amt_s("btc_jpy", br._amt("btc_jpy", 0.00005678)), "0.0000")
        self.assertEqual(br._amt_s("btc_jpy", 0.0003), "0.0003")


if __name__ == "__main__":
    unittest.main()
