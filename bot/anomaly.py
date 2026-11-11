"""「小さな穴」探し（アノマリー調査）。お金は使わない。

  python -m bot.anomaly 2023 2026

bitbankの1時間足で、次の“くせ”があるかを調べる。
  A. 時間帯（日本時間）  B. 曜日  C. 月末月初・給料日（25日）前後
  D. 大きく下げた後のリバウンド（行き過ぎの戻り）  E. BTCが動いた次の1時間にETH/XRPが追いかけるか
2023〜2024年で「穴」を選び（作る期間）、2025〜2026年で答え合わせ（未知の期間）。
手数料（往復0.34%）を引いた後でも残るかを見る。結果は data/anomaly.json。
"""
import json
import math
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import bitbank, config as C

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
SPLIT = int(datetime(2025, 1, 1, tzinfo=JST).timestamp() * 1000)
COST_RT = 0.0034      # 成行の往復コスト（手数料＋ズレ）
COST_RT_MAKER = 0.0   # 指値でうまく約定した場合（ETH/XRPは−0.02%×2の受け取りだが保守的に0）


def fetch_hourly(pair, y0, y1):
    rows = {}
    d, end = date(y0, 1, 1), min(date(y1, 12, 31), date.today() - timedelta(days=1))
    while d <= end:
        for attempt in range(3):
            try:
                raw = bitbank._http(bitbank.urllib.request.Request(
                    f"{bitbank.PUBLIC}/{pair}/candlestick/1hour/{d.strftime('%Y%m%d')}"))
                for o, h, l, c, v, ts in raw["candlestick"][0]["ohlcv"]:
                    rows[int(ts)] = (float(o), float(h), float(l), float(c), float(v), int(ts))
                break
            except Exception as e:
                if attempt == 2:
                    print(f"[skip] {pair} {d}: {e}", file=sys.stderr)
                time.sleep(1)
        d += timedelta(days=1)
        time.sleep(0.08)
    return [rows[t] for t in sorted(rows)]


def tstat(xs):
    n = len(xs)
    if n < 10:
        return 0.0, 0.0
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, (m / (sd / math.sqrt(n)) if sd > 0 else 0.0)


def summary(xs, cost):
    """1回ごとのリターン列 → 平均・t値・手数料後の合計（単利の目安）。"""
    m, t = tstat(xs)
    return {"n": len(xs), "mean": m, "t": t, "net_mean": m - cost,
            "net_total": sum(x - cost for x in xs), "win": sum(x > cost for x in xs) / len(xs) if xs else 0}


def split_eval(events, cost):
    """events=[(ts, ret)] を作る期間と答え合わせに分けて評価。"""
    ins = [r for t, r in events if t < SPLIT]
    out = [r for t, r in events if t >= SPLIT]
    return {"in": summary(ins, cost), "out": summary(out, cost)}


def jst(ts):
    return datetime.fromtimestamp(ts / 1000, JST)


def analyze(data):
    res = {}
    for pair, bars in data.items():
        c = [b[3] for b in bars]; ts = [b[5] for b in bars]
        r1 = [(ts[i], c[i] / c[i - 1] - 1) for i in range(1, len(c))]   # その1時間のリターン
        out = {}
        # A. 時間帯：作る期間で平均が最も高い4時間を選び、その時間だけ持つ（1日1往復）
        by_h = {h: [r for t, r in r1 if jst(t).hour == h and t < SPLIT] for h in range(24)}
        rank = sorted(range(24), key=lambda h: sum(by_h[h]) / max(1, len(by_h[h])), reverse=True)
        best = sorted(rank[:4])
        daily = {}
        for t, r in r1:
            if jst(t).hour in best:
                k = jst(t).date()
                daily.setdefault(k, [t, 0.0]); daily[k][1] += r
        out["A_hours"] = {"hours_jst": best,
                          "hour_means_in": {h: round(sum(v) / max(1, len(v)) * 1e4, 2) for h, v in by_h.items()},
                          **split_eval([(t, r) for t, r in daily.values()], COST_RT)}
        # B. 曜日：作る期間で最も強い曜日だけ持つ
        days = {}
        for t, r in r1:
            k = jst(t).date(); days.setdefault(k, [t, 0.0]); days[k][1] += r
        dv = list(days.values())
        by_w = {w: [r for t, r in dv if jst(t).weekday() == w and t < SPLIT] for w in range(7)}
        bw = max(range(7), key=lambda w: sum(by_w[w]) / max(1, len(by_w[w])))
        out["B_weekday"] = {"best_weekday": "月火水木金土日"[bw],
                            "weekday_means_in": {"月火水木金土日"[w]: round(sum(v) / max(1, len(v)) * 1e4, 1) for w, v in by_w.items()},
                            **split_eval([(t, r) for t, r in dv if jst(t).weekday() == bw], COST_RT)}
        # C. 給料日（24日の夜→27日の夜）と月末月初（最終日→3日）
        def window(pred):
            ev, cur = [], None
            for t, r in r1:
                if pred(jst(t)):
                    if cur is None:
                        cur = [t, 0.0]
                    cur[1] += r
                elif cur is not None:
                    ev.append(tuple(cur)); cur = None
            return ev
        out["C_payday"] = split_eval(window(lambda d: 25 <= d.day <= 27), COST_RT)
        out["C_turn_of_month"] = split_eval(window(lambda d: d.day >= 28 or d.day <= 3), COST_RT)
        # D. 24時間で大きく下げた後（作る期間の下位5%）→ 次の24時間
        r24 = [(i, c[i] / c[i - 24] - 1) for i in range(24, len(c) - 24)]
        thr = sorted(r for i, r in r24 if ts[i] < SPLIT)[int(0.05 * len([1 for i, r in r24 if ts[i] < SPLIT]))]
        ev, last = [], -999
        for i, r in r24:
            if r <= thr and i - last >= 24:
                ev.append((ts[i], c[i + 24] / c[i] - 1)); last = i
        out["D_rebound_after_drop"] = {"drop_threshold": thr, **split_eval(ev, COST_RT)}
        res[pair] = out
    # E. BTCの1時間の動き → 次の1時間のETH/XRP
    btc = {b[5]: b[3] for b in data["btc_jpy"]}
    for pair in ("eth_jpy", "xrp_jpy"):
        bars = data[pair]; ev = []
        for i in range(2, len(bars) - 1):
            t0, t1 = bars[i - 1][5], bars[i][5]
            if t0 in btc and t1 in btc:
                b_ret = btc[t1] / btc[t0] - 1
                if b_ret > 0.01:   # BTCが1時間で+1%超
                    ev.append((t1, bars[i + 1][3] / bars[i][3] - 1))
        res[pair]["E_follow_btc"] = split_eval(ev, COST_RT)
    return res


def main():
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
    y1 = int(sys.argv[2]) if len(sys.argv) > 2 else date.today().year
    data = {p: fetch_hourly(p, y0, y1) for p in C.PAIRS}
    for p, b in data.items():
        print(p, len(b), "hourly bars", file=sys.stderr)
    res = analyze(data)
    out = {"generated": datetime.now(JST).isoformat(), "years": [y0, y1], "split": "2025-01-01",
           "cost_round_trip": COST_RT, "results": res}
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "anomaly.json").write_text(json.dumps(out, ensure_ascii=False, default=str))
    print(json.dumps(out, ensure_ascii=False, default=str)[:3000])


if __name__ == "__main__":
    main()
