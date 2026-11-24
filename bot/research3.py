"""作戦の強化案を検証する（お金は使わない）。  python -m bot.research3 2021 2026

今の作戦（4時間足ブレイクアウト）を土台に、3つの改善案を「作る期間（〜2024）」で比べ、
「答え合わせ期間（2025〜）」で本当に良くなるか確かめる。
  A. 手数料：買い・手じまいを指値（メイカー）で出す。約定は厳しめに判定
     （次の足の安値が指値より下＝本当に売り手が来たときだけ約定。来なければ次の始値で成行）
  B. 日足の作戦を半分まぜる（4時間足50%＋日足ブレイクアウト50%）
  C. 銘柄を増やす（bitbankの円建てで、2024年の売買代金が多い順に追加。答え合わせ期間は見ないで選ぶ）
採用の条件：作る期間と答え合わせ期間の両方で、成績（Calmar＝年率÷最大の落ち込み）が今の作戦より良いこと。
結果は data/research3.json。
"""
import itertools
import json
import sys
from datetime import date, datetime
from pathlib import Path

from . import bitbank, config as C
from .indicators import atr
from .research import JST, fetch, stats

ROOT = Path(__file__).resolve().parent.parent
SPLIT = int(datetime(2025, 1, 1, tzinfo=JST).timestamp() * 1000)
Y2024 = (int(datetime(2024, 1, 1, tzinfo=JST).timestamp() * 1000), SPLIT)
TAKER = {"btc_jpy": 0.0010}
MAKER = {"btc_jpy": 0.0}
SLIP = 0.0005
ONLY = {"今の作戦（成行）", "A 指値で手数料を減らす"} if "--quick" in sys.argv else None
BPD = {"4hour": 6, "1day": 1}


def sim(bars, pair, tf, din, dout, k, vt, maker, maker_exit=True):
    """long専用。判断は足の確定、実行は次の足。maker=True なら指値（厳しめの約定判定）。"""
    tk, mk = TAKER.get(pair, 0.0012), MAKER.get(pair, -0.0002)
    o = [b[0] for b in bars]; h = [b[1] for b in bars]; l = [b[2] for b in bars]; c = [b[3] for b in bars]
    a = atr(bars, 14)
    cash, coin, stop, entry, high = 1.0, 0.0, 0.0, 0.0, 0.0
    curve, trades, pend = [], [], None   # pend = ("buy"/"sell", 指値価格, 経過本数)
    warm = max(din, 30) + 2
    for i in range(len(bars)):
        if pend:
            side, lim, age = pend
            px = fee = None
            if not maker or (side == "sell" and not maker_exit):
                px, fee = o[i] * (1 + SLIP if side == "buy" else 1 - SLIP), tk
            elif side == "buy" and l[i] < lim:
                px, fee = min(lim, o[i]), mk            # 窓を開けて下げたら始値で約定
            elif side == "sell" and h[i] > lim:
                px, fee = max(lim, o[i]), mk
            elif age >= 1:                              # 1本待って約定しなければ成行
                px, fee = o[i] * (1 + SLIP if side == "buy" else 1 - SLIP), tk
            if px is not None:
                if side == "buy" and coin == 0:
                    frac = 1.0
                    if vt:
                        rets = [c[j] / c[j - 1] - 1 for j in range(max(1, i - 30), i)]
                        if len(rets) > 5:
                            m = sum(rets) / len(rets)
                            vol = (sum((r - m) ** 2 for r in rets) / len(rets)) ** .5 * (365 * BPD[tf]) ** .5
                            frac = max(.1, min(1., vt / vol)) if vol > 0 else 1.
                    spend = cash * frac
                    coin, cash = spend * (1 - fee) / px, cash - spend
                    entry = high = px; stop = px - k * a[i - 1]
                elif side == "sell" and coin > 0:
                    cash += coin * px * (1 - fee); trades.append(px / entry - 1); coin = 0.0
                pend = None
            else:
                pend = (side, lim, age + 1)
        if coin > 0 and l[i] <= stop:                    # 損切りは成行（取引所の逆指値）
            px = min(o[i], stop) * (1 - SLIP)
            cash += coin * px * (1 - tk); trades.append(px / entry - 1); coin = 0.0; pend = None
        curve.append((bars[i][5], cash + coin * c[i]))
        if i < warm or pend:
            continue
        if coin > 0:
            high = max(high, c[i]); stop = max(stop, high - k * a[i])
            if c[i] < min(l[i - dout:i]):
                pend = ("sell", c[i], 0)
        elif c[i] > max(h[i - din:i]) and i + 1 < len(bars):
            pend = ("buy", c[i], 0)
    return curve, trades


def evaluate(data, pairs, sleeves, maker, maker_exit=True):
    curves, tr = {}, []
    for tf, din, dout, k, vt in sleeves:
        for p in pairs:
            if p not in data[tf] or len(data[tf][p]) < din + 40:
                continue
            cv, t = sim(data[tf][p], p, tf, din, dout, k, vt, maker, maker_exit)
            curves[f"{tf}:{p}"] = cv; tr += t
    ins, oos = stats(curves, None, SPLIT), stats(curves, SPLIT, None)
    slim = lambda s: {kk: s[kk] for kk in ("total", "cagr", "mdd", "calmar", "hit5", "months", "neg", "avg_m", "worst_m")}
    return {"trades": len(tr), "win_rate": sum(x > 0 for x in tr) / len(tr) if tr else None,
            "in": slim(ins), "out": slim(oos), "curve_out": oos["curve"], "monthly_out": oos["monthly"]}


def main():
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 2021
    y1 = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else date.today().year
    # 候補の銘柄：bitbankの円建てすべて → 2021年から4時間足があり、2024年の売買代金が多い順
    if ONLY:   # 3銘柄だけで速く回す
        allp = list(C.PAIRS)
    else:
        allp = None
    allp = allp or [t["pair"] for t in bitbank._http(bitbank.urllib.request.Request(f"{bitbank.PUBLIC}/tickers_jpy"))]
    data = {"4hour": {}, "1day": {}}
    liq = {}
    for p in allp:
        b4 = fetch(p, "4hour", y0, y1)
        if not b4 or b4[0][5] > int(datetime(y0, 3, 1, tzinfo=JST).timestamp() * 1000):
            continue
        data["4hour"][p] = b4
        liq[p] = sum(b[3] * b[4] for b in b4 if Y2024[0] <= b[5] < Y2024[1])
    ranked = sorted(liq, key=liq.get, reverse=True)
    extra = [p for p in ranked if p not in C.PAIRS]
    for p in (set(C.PAIRS) | set(extra[:5])) if not ONLY else C.PAIRS:
        data["1day"][p] = fetch(p, "1day", y0, y1)
    print("liquidity 2024 top:", ranked[:10], file=sys.stderr)

    base4 = ("4hour", C.DON_IN, C.DON_OUT, C.STOP_ATR, C.VOL_TARGET)
    day = ("1day", 20, 10, 3.5, 0.6)
    variants = {
        "今の作戦（成行）": (C.PAIRS, [base4], False),
        "A 指値で手数料を減らす": (C.PAIRS, [base4], True),
        "B 日足を半分まぜる": (C.PAIRS, [base4, day], False),
        "C 銘柄を5つに（+2）": (C.PAIRS + extra[:2], [base4], False),
        "C 銘柄を6つに（+3）": (C.PAIRS + extra[:3], [base4], False),
        "A+C 指値＋5銘柄": (C.PAIRS + extra[:2], [base4], True),
        "A+B 指値＋日足まぜ": (C.PAIRS, [base4, day], True),
    }
    res = {}
    variants = {k: v for k, v in variants.items() if not ONLY or k in ONLY}
    variants["A2 買いだけ指値"] = (C.PAIRS, [base4], "entry")
    for name, (pairs, sl, mk) in variants.items():
        r = evaluate(data, pairs, sl, bool(mk), mk != "entry")
        r["pairs"] = pairs
        res[name] = r
        print(name, {kk: round(r["in"][kk], 3) for kk in ("total", "mdd", "calmar")},
              {kk: round(r["out"][kk], 3) for kk in ("total", "mdd", "calmar")}, file=sys.stderr)
    base = res["今の作戦（成行）"]
    for name, r in res.items():
        r["better_in"] = r["in"]["calmar"] > base["in"]["calmar"]
        r["better_out"] = r["out"]["calmar"] > base["out"]["calmar"]
        r["adopt_ok"] = name != "今の作戦（成行）" and r["better_in"] and r["better_out"] and \
            r["out"]["mdd"] >= base["out"]["mdd"] * 1.2
    out = {"generated": datetime.now(JST).isoformat(), "years": [y0, y1], "split": "2025-01-01",
           "extra_ranked": extra[:5], "liquidity_2024": {p: round(liq[p]) for p in ranked[:10]}, "variants": res}
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / ("research3_quick.json" if ONLY else "research3.json")).write_text(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
