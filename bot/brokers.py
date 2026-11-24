"""注文の実行役。PaperBroker＝仮想売買、LiveBroker＝bitbankで本当に売買。

どちらも同じ形の関数を持ち、engine はどちらを使っているか気にしない。
  buy / sell            … 売買
  stop_hit              … 損切りに引っかかったか
  place_stop / move_stop … 損切り注文を置く・引き上げる（本番だけ取引所に置く）
"""
import math
import sys
import time

from . import config as C


class PaperBroker:
    SLIPPAGE = 0.0005  # 0.05% 不利な価格で約定したと仮定（保守的）

    def cash(self, state):
        return state["cash"]

    def min_order_jpy(self, pair):
        return 1000

    def equity(self, state, market):
        eq = state["cash"]
        for pair, pos in state["positions"].items():
            px = market[pair]["bid"] if pair in market else pos["entry"]
            eq += pos["amount"] * px
        return eq

    def buy(self, state, pair, jpy, ask):
        px = ask * (1 + self.SLIPPAGE)
        fee = jpy * C.TAKER_FEE[pair]
        amt = (jpy - fee) / px
        state["cash"] -= jpy
        return amt, px, fee

    def sell(self, state, pair, pos, bid, hit=None):
        px = (hit["px"] if hit else bid) * (1 - self.SLIPPAGE)
        gross = pos["amount"] * px
        fee = gross * C.TAKER_FEE[pair]
        state["cash"] += gross - fee
        return pos["amount"], px, fee

    # 仮想売買では「取引所に逆指値を置いていた」のと同じ動きをまねる
    def stop_hit(self, pair, pos, bar=None, low=None, bid=None):
        stop = pos["stop"]
        if bar is not None:
            if bar[5] < pos["opened_at"] or bar[2] > stop:   # 買う前から始まった足は使わない
                return None
            return {"px": min(stop, bar[0])}                 # 窓を開けて下げたら始値で約定
        if low is not None and low <= stop:
            return {"px": min(stop, bid)}
        return None

    def place_stop(self, state, pair, pos):
        pass

    def move_stop(self, state, pair, pos):
        pass


class LiveBroker:
    """本番用。残高は毎回bitbankから取得し、ボットが買った分だけを売る。

    損切りは取引所に「逆指値（stop）」として置いておくので、ボットの実行が何時間遅れても
    取引所側で確実に損切りされる。追従損切りが上がったら、注文を置き直す。
    """
    STOP_MOVE_MIN = 0.002   # 損切り位置が0.2%以上上がったときだけ置き直す（注文の出し過ぎ防止）

    def __init__(self, private):
        self.p = private
        self.bal = private.balances()
        self.info = private.pairs_info()
        self._checked = {}

    def cash(self, state):
        return self.bal.get("jpy", 0.0)

    def min_order_jpy(self, pair):
        return 1000

    def equity(self, state, market):
        eq = self.bal.get("jpy", 0.0)
        for pair, pos in state["positions"].items():
            px = market[pair]["bid"] if pair in market else pos["entry"]
            eq += pos["amount"] * px
        return eq

    # ---- 桁そろえ（指数表記 1e-05 などにならないよう文字列で渡す）----
    def _amt(self, pair, amount):
        d = int(self.info[pair]["amount_digits"])
        return math.floor(amount * 10 ** d + 1e-6) / 10 ** d   # 0.0029→0.0028 になる誤差を防ぐ

    def _amt_s(self, pair, amount):
        return f"{amount:.{int(self.info[pair]['amount_digits'])}f}"

    def _px_s(self, pair, price):
        return f"{price:.{int(self.info[pair]['price_digits'])}f}"

    def _min_amt(self, pair):
        return float(self.info[pair].get("unit_amount") or 0)

    def _fill(self, pair, res, ref):
        """注文の実際の約定量と平均価格を確かめる（すぐ反映されないことがあるので数回見る）。"""
        oid = res.get("order_id")
        for k in range(4):
            ex = float(res.get("executed_amount") or 0)
            avg = float(res.get("average_price") or 0)
            if res.get("status") in ("FULLY_FILLED", "CANCELED_UNFILLED", "CANCELED_PARTIALLY_FILLED"):
                break
            time.sleep(1.0 + k)
            try:
                res = self.p.get_order(pair, oid)
            except Exception as e:
                print(f"[warn] 約定確認 {pair}: {e}", file=sys.stderr)
        ex = float(res.get("executed_amount") or 0)
        avg = float(res.get("average_price") or 0)
        if res.get("status") in ("UNFILLED", "PARTIALLY_FILLED"):   # 指値に切り替わって残った分は取り消す
            try:
                self.p.cancel_order(pair, oid)
            except Exception as e:
                print(f"[warn] 残り取消 {pair}: {e}", file=sys.stderr)
        return ex, (avg if avg > 0 else ref)

    def buy(self, state, pair, jpy, ask):
        amt = self._amt(pair, jpy * (1 - C.TAKER_FEE[pair]) / ask * 0.995)
        if amt < self._min_amt(pair) or amt <= 0:
            return 0, ask, 0
        res = self.p.order(pair, "buy", self._amt_s(pair, amt), ask,
                           price_digits=int(self.info[pair]["price_digits"]))
        ex, px = self._fill(pair, res, ask)
        self.bal = self.p.balances()
        return ex, px, ex * px * C.TAKER_FEE[pair]

    def sell(self, state, pair, pos, bid, hit=None):
        if hit and hit.get("done"):              # 取引所の逆指値ですでに売れていた
            self.bal = self.p.balances()
            return hit["amount"], hit["px"], hit["amount"] * hit["px"] * C.TAKER_FEE[pair]
        done = self._cancel_stop(pair, pos)      # 先に逆指値を取り消す（コインが拘束されているため）
        if done:
            self.bal = self.p.balances()
            return done["amount"], done["px"], done["amount"] * done["px"] * C.TAKER_FEE[pair]
        self.bal = self.p.balances()
        coin = pair.split("_")[0]
        amt = self._amt(pair, min(pos["amount"], self.bal.get(coin, 0.0)))
        if amt <= 0:
            return 0, bid, 0
        res = self.p.order(pair, "sell", self._amt_s(pair, amt), bid,
                           price_digits=int(self.info[pair]["price_digits"]))
        ex, px = self._fill(pair, res, bid)
        self.bal = self.p.balances()
        return ex, px, ex * px * C.TAKER_FEE[pair]

    # ---- 取引所に置く損切り ----
    def place_stop(self, state, pair, pos):
        self.bal = self.p.balances()
        coin = pair.split("_")[0]
        amt = self._amt(pair, min(pos["amount"], self.bal.get(coin, 0.0)))
        pos["stop_order_id"] = None
        if amt <= 0 or amt < self._min_amt(pair):
            return
        try:
            res = self.p.stop_order(pair, "sell", self._amt_s(pair, amt), self._px_s(pair, pos["stop"]))
            pos["stop_order_id"] = res.get("order_id")
            pos["stop_placed"] = pos["stop"]
        except Exception as e:  # 置けなくても、ボットの実行ごとの確認で損切りはする
            print(f"[warn] 逆指値を置けませんでした {pair}: {e}", file=sys.stderr)

    def move_stop(self, state, pair, pos):
        placed = pos.get("stop_placed") or 0
        if pos.get("stop_order_id") and pos["stop"] < placed * (1 + self.STOP_MOVE_MIN):
            return
        if self._cancel_stop(pair, pos):         # 取り消そうとしたら既に売れていた → 次回 stop_hit で記録
            return
        self.place_stop(state, pair, pos)

    def _order_status(self, pair, pos):
        oid = pos.get("stop_order_id")
        if not oid:
            return None
        if oid not in self._checked:
            try:
                self._checked[oid] = self.p.get_order(pair, oid)
            except Exception as e:
                print(f"[warn] 逆指値の確認 {pair}: {e}", file=sys.stderr)
                return None
        return self._checked[oid]

    def _cancel_stop(self, pair, pos):
        """逆指値を取り消す。すでに約定していたらその結果を返す。"""
        oid = pos.get("stop_order_id")
        if not oid:
            return None
        try:
            res = self.p.cancel_order(pair, oid)
        except Exception:
            self._checked.pop(oid, None)
            res = self._order_status(pair, pos) or {}
        pos["stop_order_id"] = None
        self._checked.pop(oid, None)
        ex = float(res.get("executed_amount") or 0)
        if ex > 0:
            return {"amount": ex, "px": float(res.get("average_price") or pos["stop"]), "done": True}
        return None

    def stop_hit(self, pair, pos, bar=None, low=None, bid=None):
        o = self._order_status(pair, pos)
        if o is not None:
            st = o.get("status")
            if st == "FULLY_FILLED":
                return {"amount": float(o["executed_amount"]), "px": float(o["average_price"]), "done": True}
            if st in ("CANCELED_UNFILLED", "CANCELED_PARTIALLY_FILLED", "REJECTED"):
                pos["stop_order_id"] = None      # 誰かが取り消した等 → 置き直す
                pos["stop_dirty"] = True
            else:
                return None                      # 取引所が見張っている
        # 逆指値が無いときの予備：今の価格が損切り位置以下なら成行で売る
        if bar is None and bid is not None and bid <= pos["stop"]:
            return {"px": bid}
        return None
