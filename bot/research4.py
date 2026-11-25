"""銘柄数と損切り幅の比べ直し（お金は使わない）。  python -m bot.research4 2021 2026

前回の洗い出しで出た2つの疑問を、本番と同じ条件（4時間足ブレイクアウト・買いだけ指値・
売買代金フィルターあり）でそろえて確かめる。
  1. 3銘柄（BTC・XRP・ETH）と5銘柄（＋SOL・BNB）、どちらが良いか
  2. 損切りを ATR×4（今）から ATR×2.5 に狭めると良くなるか
     → 2.5 だけが偶然良いのか確かめるため、2.0〜5.0 を並べて「なだらかな山」かを見る
  3. 銘柄ごとの成績（どの銘柄が足を引っ張っているか）
作る期間＝〜2024年、答え合わせ＝2025年〜。結果は data/research4.json。
"""
import json
import sys
from datetime import date, datetime

from . import config as C
from .indicators import atr
from .research import JST, fetch, stats
from .research3 import BPD, MAKER, ROOT, SLIP, SPLIT, TAKER

PAIRS3 = ["btc_jpy", "xrp_jpy", "eth_jpy"]
PAIRS5 = ["btc_jpy", "xrp_jpy", "eth_jpy", "sol_jpy", "bnb_jpy"]
KS = [2.0, 2.5, 3.0, 3.5, 4.0, 5.0]


def sim(bars, pair, din, dout, k, vt, min_vol):
    """research3.sim の「買いだけ指値」と同じ動き＋本番と同じ売買代金フィルター。"""
    tk, mk = TAKER.get(pair, 0.0012), MAKER.get(pair, -0.0002)
    o = [b[0] for b in bars]; h = [b[1] for b in bars]; l = [b[2] for b in bars]; c = [b[3] for b in bars]
    a = atr(bars, 14)
    cash, coin, stop, entry, high = 1.0, 0.0, 0.0, 0.0, 0.0
    curve, trades, pend, skipped = [], [], None, 0
    warm = max(din, 30) + 2
    for i in range(len(bars)):
        if pend:
            side, lim, age = pend
            px = fee = None
            if side == "sell":
                px, fee = o[i] * (1 - SLIP), tk
            elif l[i] < lim:
                px, fee = min(lim, o[i]), mk
            elif age >= 1:
                px, fee = o[i] * (1 + SLIP), tk
            if px is not None:
                if side == "buy" and coin == 0:
                    frac = 1.0
                    if vt:
                        rets = [c[j] / c[j - 1] - 1 for j in range(max(1, i - 30), i)]
                        if len(rets) > 5:
                            m = sum(rets) / len(rets)
                            vol = (sum((r - m) ** 2 for r in rets) / len(rets)) ** .5 * (365 * BPD["4hour"]) ** .5
                            frac = max(.1, min(1., vt / vol)) if vol > 0 else 1.
                    spend = cash * frac
                    coin, cash = spend * (1 - fee) / px, cash - spend
                    entry = high = px; stop = px - k * a[i - 1]
                elif side == "sell" and coin > 0:
                    cash += coin * px * (1 - fee); trades.append((bars[i][5], px / entry - 1)); coin = 0.0
                pend = None
            else:
                pend = (side, lim, age + 1)
        if coin > 0 and l[i] <= stop:
            px = min(o[i], stop) * (1 - SLIP)
            cash += coin * px * (1 - tk); trades.append((bars[i][5], px / entry - 1)); coin = 0.0; pend = None
        curve.append((bars[i][5], cash + coin * c[i]))
        if i < warm or pend:
            continue
        if coin > 0:
            high = max(high, c[i]); stop = max(stop, high - k * a[i])
            if c[i] < min(l[i - dout:i]):
                pend = ("sell", c[i], 0)
        elif c[i] > max(h[i - din:i]) and i + 1 < len(bars):
            if min_vol and sum(c[j] * bars[j][4] for j in range(i - 5, i + 1)) < min_vol:
                skipped += 1
                continue
            pend = ("buy", c[i], 0)
    return curve, trades, skipped


def tstats(tr, t0=None, t1=None):
    x = [r for t, r in tr if (t0 is None or t >= t0) and (t1 is None or t < t1)]
    if not x:
        return {"n": 0}
    w = [r for r in x if r > 0]; ls = [r for r in x if r <= 0]
    return {"n": len(x), "win": len(w) / len(x), "avg": sum(x) / len(x),
            "avg_win": sum(w) / len(w) if w else 0, "avg_loss": sum(ls) / len(ls) if ls else 0,
            "best": max(x), "worst": min(x)}


SLIM = ("total", "cagr", "mdd", "calmar", "hit5", "months", "neg", "avg_m", "worst_m", "best_m")


def run(data, pairs, k, min_vol=C.MIN_VOL_JPY_24H):
    curves, tr, sk = {}, [], 0
    for p in pairs:
        if p not in data:
            continue
        cv, t, s = sim(data[p], p, C.DON_IN, C.DON_OUT, k, C.VOL_TARGET, min_vol)
        curves[p] = cv; tr += t; sk += s
    if not curves:
        return None
    ins, oos = stats(curves, None, SPLIT), stats(curves, SPLIT, None)
    y26 = int(datetime(2026, 1, 1, tzinfo=JST).timestamp() * 1000)
    return {"pairs": [p for p in pairs if p in data], "skipped_volume": sk,
            "in": {kk: ins[kk] for kk in SLIM} if ins else None,
            "out": {kk: oos[kk] for kk in SLIM} if oos else None,
            "out_2025": {kk: v for kk, v in (stats(curves, SPLIT, y26) or {}).items() if kk in SLIM},
            "out_2026": {kk: v for kk, v in (stats(curves, y26, None) or {}).items() if kk in SLIM},
            "monthly_out": oos["monthly"] if oos else None,
            "trades_in": tstats(tr, None, SPLIT), "trades_out": tstats(tr, SPLIT, None)}


def main():
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 2021
    y1 = int(sys.argv[2]) if len(sys.argv) > 2 else date.today().year
    data, first = {}, {}
    for p in PAIRS5:
        b = fetch(p, "4hour", y0, y1)
        if b:
            data[p] = b
            first[p] = datetime.fromtimestamp(b[0][5] / 1000, JST).strftime("%Y-%m-%d")
    print("first bar:", first, file=sys.stderr)
    oos_start = SPLIT
    # SOL・BNB は上場が遅いので、答え合わせ期間に間に合っているかを記録しておく
    late = {p: d for p, d in first.items() if int(datetime.fromisoformat(d).replace(tzinfo=JST).timestamp() * 1000) > oos_start - 30 * 864e5}

    res = {"compare": {}, "atr_sweep_3": {}, "atr_sweep_5": {}, "per_pair": {}}
    for name, pairs, k in [("3銘柄・ATR×4", PAIRS3, 4.0), ("5銘柄・ATR×4（今の設定）", PAIRS5, 4.0),
                           ("3銘柄・ATR×2.5", PAIRS3, 2.5), ("5銘柄・ATR×2.5", PAIRS5, 2.5)]:
        res["compare"][name] = run(data, pairs, k)
    for k in KS:
        res["atr_sweep_3"][str(k)] = run(data, PAIRS3, k)
        res["atr_sweep_5"][str(k)] = run(data, PAIRS5, k)
    for p in data:
        res["per_pair"][p] = {str(k): run(data, [p], k) for k in (2.5, 4.0)}
    # 売買代金フィルターの影響（5銘柄・ATR×4で、フィルターなし）
    res["compare"]["5銘柄・ATR×4・代金フィルターなし"] = run(data, PAIRS5, 4.0, min_vol=0)
    for n, r in res["compare"].items():
        if r and r["out"]:
            print(n, round(r["in"]["total"], 3) if r["in"] else None, round(r["out"]["total"], 3),
                  round(r["out"]["mdd"], 3), file=sys.stderr)
    out = {"generated": datetime.now(JST).isoformat(), "years": [y0, y1], "split": "2025-01-01",
           "first_bar": first, "late_listed": late, "min_vol_jpy_24h": C.MIN_VOL_JPY_24H, **res}
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "research4.json").write_text(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
