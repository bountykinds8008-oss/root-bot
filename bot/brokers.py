"""注文の実行役。PaperBroker＝仮想売買、LiveBroker＝bitbankで本当に売買。"""
import math

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

    def sell(self, state, pair, amount, bid):
        px = bid * (1 - self.SLIPPAGE)
        gross = amount * px
        fee = gross * C.TAKER_FEE[pair]
        state["cash"] += gross - fee
        return amount, px, fee


class LiveBroker:
    """本番用。残高は毎回bitbankから取得し、ボットが買った分だけを売る。"""

    def __init__(self, private):
        self.p = private
        self.bal = private.balances()
        self.info = private.pairs_info()

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

    def _round(self, pair, amount):
        digits = int(self.info[pair]["amount_digits"])
        f = 10 ** digits
        return math.floor(amount * f) / f

    def buy(self, state, pair, jpy, ask):
        amt = self._round(pair, jpy * (1 - C.TAKER_FEE[pair]) / ask * 0.995)
        if amt <= float(self.info[pair].get("unit_amount", 0)):
            return 0, ask, 0
        self.p.order(pair, "buy", str(amt), ask)
        self.bal = self.p.balances()
        return amt, ask, jpy * C.TAKER_FEE[pair]

    def sell(self, state, pair, amount, bid):
        coin = pair.split("_")[0]
        amt = self._round(pair, min(amount, self.bal.get(coin, 0.0)))
        if amt > 0:
            self.p.order(pair, "sell", str(amt), bid)
            self.bal = self.p.balances()
        return amt, bid, amt * bid * C.TAKER_FEE[pair]
