"""作戦の比較研究（お金は使わない）。

  python -m bot.research 2021 2026

- 候補：研究で効果が報告されている「トレンドフォロー（時系列モメンタム）」系の型だけ
- 時間軸：4時間足・日足（売買回数を減らし、手数料負けを防ぐ）
- 判定：2021〜2024年で成績を比べて選び（作る期間）、2025〜2026年で答え合わせ（未知の期間）
結果は data/research.json に保存。
"""
import itertools
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import bitbank, config as C
from .indicators import atr, ema

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
SPLIT_YEAR = 2025          # これより前＝作る期間、以降＝答え合わせ
BARS_PER_DAY = {"4hour": 6, "1day": 1}

FEES = {   # 片道の手数料と、約定のズレ（不利側に見積もる）
    "taker": ({"btc_jpy": 0.0010, "eth_jpy": 0.0012, "xrp_jpy": 0.0012}, 0.0005),
    "maker": ({"btc_jpy": 0.0, "eth_jpy": -0.0002, "xrp_jpy": -0.0002}, 0.0002),
}


# ---------------- データ ----------------
def fetch(pair, tf, y0, y1):
    rows = {}
    for y in range(y0, y1 + 1):
        try:
            for r in bitbank.candles_year(pair, tf, y):
                rows[r[5]] = r
        except Exception as e:
            print(f"[skip] {pair} {tf} {y}: {e}", file=sys.stderr)
        time.sleep(0.2)
    return [rows[t] for t in sorted(rows)]


def sma(v, n):
    out, s = [], 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n:
            s -= v[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


# ---------------- 1銘柄のシミュレーション ----------------
def simulate(bars, p, pair, regime_ok, fee_kind):
    """long専用。シグナルは足の確定で判断し、次の足の始値で売買（未来を見ない）。
    返り値：[(時刻, この銘柄の資産)]・取引リスト"""
    fee_tbl, slip = FEES[fee_kind]
    fee = fee_tbl[pair]
    o = [b[0] for b in bars]; h = [b[1] for b in bars]; l = [b[2] for b in bars]; c = [b[3] for b in bars]
    a = atr(bars, 14)
    ef, es = ema(c, p["fast"]), ema(c, p["slow"])
    n_in, n_out = p["don_in"], p["don_out"]
    cash, coin, stop, entry_px, high = 1.0, 0.0, 0.0, 0.0, 0.0
    curve, trades = [], []
    warm = max(p["slow"], n_in, 30) + 2
    pending = None   # "buy"/"sell"：次の足の始値で実行
    for i in range(len(bars)):
        # 前の足で決めた注文を、この足の始値で実行
        if pending == "buy" and coin == 0:
            px = o[i] * (1 + slip)
            frac = 1.0
            if p["voltarget"]:
                rets = [c[k] / c[k - 1] - 1 for k in range(max(1, i - 30), i)]
                if len(rets) > 5:
                    m = sum(rets) / len(rets)
                    vol = (sum((r - m) ** 2 for r in rets) / len(rets)) ** 0.5 * (365 * BARS_PER_DAY[p["tf"]]) ** 0.5
                    frac = max(0.1, min(1.0, p["voltarget"] / vol)) if vol > 0 else 1.0
            spend = cash * frac
            coin = spend * (1 - fee) / px
            cash -= spend
            entry_px, high = px, px
            stop = px - p["atr_k"] * a[i - 1]
            trades.append({"ts": bars[i][5], "side": "buy", "px": px})
        elif pending == "sell" and coin > 0:
            px = o[i] * (1 - slip)
            cash += coin * px * (1 - fee)
            trades.append({"ts": bars[i][5], "side": "sell", "px": px, "ret": px / entry_px - 1})
            coin = 0.0
        pending = None
        # 足の途中で損切りに触れたら、その価格で決済
        if coin > 0 and l[i] <= stop:
            px = min(o[i], stop) * (1 - slip)
            cash += coin * px * (1 - fee)
            trades.append({"ts": bars[i][5], "side": "sell", "px": px, "ret": px / entry_px - 1})
            coin = 0.0
        curve.append((bars[i][5], cash + coin * c[i]))
        if i < warm:
            continue
        # この足の確定で、次の行動を決める
        if coin > 0:
            high = max(high, c[i])
            stop = max(stop, high - p["atr_k"] * a[i])
            if p["entry"] == "ema" and ef[i] < es[i]:
                pending = "sell"
            if p["entry"] == "donchian" and c[i] < min(l[i - n_out:i]):
                pending = "sell"
            if p["regime"] and not regime_ok(bars[i][5]):
                pending = "sell"
        else:
            ok = (not p["regime"]) or regime_ok(bars[i][5])
            if p["entry"] == "ema":
                sig = ef[i] > es[i] and c[i] > ef[i] and es[i] > es[i - 3]
            else:
                sig = c[i] > max(h[i - n_in:i])
            if ok and sig and i + 1 < len(bars):
                pending = "buy"
    return curve, trades


# ---------------- 3銘柄を合わせた成績 ----------------
def stats(curves, t0=None, t1=None):
    """3銘柄に資金を1/3ずつ配分したときの成績（期間を指定可）。"""
    times = sorted(set().union(*[set(t for t, _ in cv) for cv in curves.values()]))
    times = [t for t in times if (t0 is None or t >= t0) and (t1 is None or t < t1)]
    if len(times) < 10:
        return None
    last, idx = {}, {p: dict(cv) for p, cv in curves.items()}
    base = {}
    eq = []
    for t in times:
        tot = 0.0
        for p in curves:
            if t in idx[p]:
                last[p] = idx[p][t]
            if p in last:
                base.setdefault(p, last[p])
                tot += last[p] / base[p] / len(curves)
            else:
                tot += 1 / len(curves)
        eq.append((t, tot))
    peak, mdd = 0, 0
    for _, v in eq:
        peak = max(peak, v); mdd = min(mdd, v / peak - 1)
    months, cur, start = {}, None, None
    for t, v in eq:
        mk = datetime.fromtimestamp(t / 1000, JST).strftime("%Y-%m")
        if mk != cur:
            if cur:
                months[cur] = prev / start - 1
            cur, start = mk, (prev if cur else v)
        prev = v
    months[cur] = prev / start - 1
    mr = list(months.values())
    yrs = (times[-1] - times[0]) / (365.25 * 864e5)
    total = eq[-1][1] / eq[0][1] - 1
    cagr = (1 + total) ** (1 / yrs) - 1 if yrs > 0 and total > -1 else -1
    return {"total": total, "cagr": cagr, "mdd": mdd,
            "calmar": cagr / abs(mdd) if mdd < 0 else 0,
            "months": len(mr), "hit5": sum(r >= 0.05 for r in mr),
            "neg": sum(r < 0 for r in mr), "avg_m": sum(mr) / len(mr),
            "worst_m": min(mr), "best_m": max(mr),
            "monthly": {k: round(v, 4) for k, v in months.items()},
            "curve": [[t, round(v, 4)] for t, v in eq[:: max(1, len(eq) // 400)]]}


def grid():
    for tf, entry, regime, vt, fee in itertools.product(
            ["4hour", "1day"], ["ema", "donchian"], [False, True], [0, 0.6], ["taker", "maker"]):
        for atr_k in ([2.5, 4.0] if tf == "4hour" else [2.0, 3.5]):
            if entry == "ema":
                for fast, slow in ([(20, 50), (50, 150)] if tf == "4hour" else [(10, 30), (20, 60)]):
                    yield {"tf": tf, "entry": entry, "fast": fast, "slow": slow, "don_in": 20, "don_out": 10,
                           "atr_k": atr_k, "regime": regime, "voltarget": vt, "fee": fee}
            else:
                for din, dout in ([(55, 20), (120, 40)] if tf == "4hour" else [(20, 10), (55, 20)]):
                    yield {"tf": tf, "entry": entry, "fast": 20, "slow": 50, "don_in": din, "don_out": dout,
                           "atr_k": atr_k, "regime": regime, "voltarget": vt, "fee": fee}


def run_all(data, btc_day):
    """data[tf][pair]=足, btc_day=BTC日足。相場フィルター＝BTCが200日平均線より上。"""
    closes = [b[3] for b in btc_day]; s200 = sma(closes, 200)
    reg = sorted((b[5], s200[i] is not None and closes[i] > s200[i]) for i, b in enumerate(btc_day))
    import bisect
    keys = [k for k, _ in reg]

    def regime_ok(ts):
        j = bisect.bisect_right(keys, ts - 864e5) - 1   # 前日までに確定した日足で判断
        return reg[j][1] if j >= 0 else False

    split = int(datetime(SPLIT_YEAR, 1, 1, tzinfo=JST).timestamp() * 1000)
    results = []
    for p in grid():
        curves, ntr, wins = {}, 0, 0
        for pair in C.PAIRS:
            cv, tr = simulate(data[p["tf"]][pair], p, pair, regime_ok, p["fee"])
            curves[pair] = cv
            sells = [t for t in tr if t["side"] == "sell"]
            ntr += len(sells); wins += sum(t["ret"] > 0 for t in sells)
        ins, oos = stats(curves, None, split), stats(curves, split, None)
        if not ins or not oos:
            continue
        results.append({"params": p, "trades": ntr, "win_rate": wins / ntr if ntr else None,
                        "in": ins, "out": oos})
    # 比較の物差し：ただ持ち続けた場合（3銘柄均等）
    hold = {pair: [(b[5], b[3]) for b in data["1day"][pair]] for pair in C.PAIRS}
    bench = {"in": stats(hold, None, split), "out": stats(hold, split, None)}
    return results, bench


def main():
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 2021
    y1 = int(sys.argv[2]) if len(sys.argv) > 2 else date.today().year
    data = {tf: {p: fetch(p, tf, y0, y1) for p in C.PAIRS} for tf in ("4hour", "1day")}
    for tf in data:
        for p in data[tf]:
            print(tf, p, len(data[tf][p]), "bars", file=sys.stderr)
    results, bench = run_all(data, data["1day"]["btc_jpy"])
    # 作る期間（〜2024）の成績だけで順位を付ける（答え合わせ期間は見ない）
    taker = [r for r in results if r["params"]["fee"] == "taker" and r["in"]["mdd"] > -0.6]
    taker.sort(key=lambda r: r["in"]["calmar"], reverse=True)
    top = taker[:10]
    slim = lambda r: {**r, "in": {k: v for k, v in r["in"].items() if k != "curve"},
                      "out": {k: v for k, v in r["out"].items() if k != "curve"}}
    out = {"generated": datetime.now(JST).isoformat(), "years": [y0, y1], "split_year": SPLIT_YEAR,
           "n_variants": len(results), "benchmark_hold": bench,
           "top_by_insample": [slim(r) for r in top],
           "best_curve": top[0] if top else None,
           "all": [{"params": r["params"], "trades": r["trades"],
                    "in": {k: r["in"][k] for k in ("total", "cagr", "mdd", "hit5", "months", "avg_m")},
                    "out": {k: r["out"][k] for k in ("total", "cagr", "mdd", "hit5", "months", "avg_m")}}
                   for r in results]}
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "research.json").write_text(json.dumps(out, ensure_ascii=False))
    for r in top[:5]:
        print(json.dumps({"p": r["params"], "in": {k: round(r["in"][k], 3) for k in ("total", "mdd", "calmar")},
                          "out": {k: round(r["out"][k], 3) for k in ("total", "mdd", "calmar")}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
