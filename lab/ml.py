"""別の角度：機械学習（勾配ブースティング）に「上がりそうか」を確率で出させて、高い時だけ指値で買う。

  python -m lab.ml

毎年「その前年までのデータだけ」で学習 → 前年で確率のしきい値を決める → その年を売買（ウォークフォワード）。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from lab import patterns as P


def features(df, other):
    f = P.raw_features(df, other)
    jst = df.index.tz_convert(P.JST)
    f["hour"] = jst.hour
    f["weekday"] = jst.weekday
    f["dom"] = jst.day
    f["streak"] = P.streak(df["close"]).values
    return f


def seq_trade(ts, fire, y, ok, H, model, i0, i1):
    """重ならないように順番に売買。戻り値：資産カーブ（1始まり）と1回ごとの損益。"""
    eq = np.ones(i1 - i0); cur = 1.0; rets = []; busy = -1
    for t in range(i0, i1):
        eq[t - i0] = cur
        if t <= busy or not fire[t]:
            continue
        if model == "maker":
            if not ok[t] or np.isnan(y[t]):
                busy = t + 1; continue
            end = t + 2 + H
        else:
            if np.isnan(y[t]):
                continue
            end = t + H
        if end >= i1:
            break
        cur *= 1 + y[t]; rets.append(float(y[t])); busy = end
    eq[t - i0:] = cur if i1 > i0 else 1.0
    return eq, rets


def main():
    data = {p: P.load(p) for p in P.PAIRS}
    other = {"btc_jpy": {"eth": data["eth_jpy"]["close"]},
             "eth_jpy": {"btc": data["btc_jpy"]["close"]},
             "xrp_jpy": {"btc": data["btc_jpy"]["close"]}}
    first = min(int(d["ts"].iloc[0]) for d in data.values())  # 銘柄ごとに始まりが違ってよい（ETHは2021〜）
    y0 = datetime.fromtimestamp(first / 1000, P.JST).year + 1
    end_all = min(int(d["ts"].iloc[-1]) for d in data.values())
    out = {"generated": datetime.now(P.JST).isoformat(), "runs": {}}
    for model in ("maker", "taker"):
        for H in (4, 12, 24):
            per_year, curves, all_rets = {}, [], []
            level = 1.0
            for Y in range(y0 + 3, 2027):
                a, b = P.ts_ms(Y), min(P.ts_ms(Y + 1), end_all)
                sleeves = []
                yr_rets = []
                for pair in P.PAIRS:
                    df = data[pair]; ts = df["ts"].values
                    F = features(df, other[pair]).values
                    y, ok = P.trade_returns(df, pair, H, "long", model)
                    valid = ok & ~np.isnan(y) & ~df["gap"].values & np.isfinite(F).all(1)
                    tr = valid & (ts < P.ts_ms(Y - 1)) & (ts >= P.ts_ms(y0))
                    va = valid & (ts >= P.ts_ms(Y - 1)) & (ts < P.ts_ms(Y))
                    # 学習データの最後H+3時間は答えが未来に食い込むので外す
                    tr &= ts < P.ts_ms(Y - 1) - (H + 3) * 3600_000
                    if tr.sum() < 2000 or va.sum() < 500:      # 学習データが足りない銘柄（ETHの初期）は見送り
                        i0, i1 = np.searchsorted(ts, a), np.searchsorted(ts, b)
                        sleeves.append(np.ones(max(0, i1 - i0))); continue
                    clf = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                                         min_samples_leaf=200, l2_regularization=1.0, random_state=0)
                    clf.fit(F[tr], (y[tr] > 0).astype(int))
                    pv = clf.predict_proba(np.nan_to_num(F[va]))[:, 1]
                    # しきい値：前年で「平均損益がプラスで最大」かつ 100回以上
                    best_thr, best_m = None, 0.0
                    for q in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
                        thr = np.quantile(pv, q)
                        sel = y[va][pv >= thr]
                        if len(sel) >= 100 and sel.mean() > best_m:
                            best_thr, best_m = thr, sel.mean()
                    i0, i1 = np.searchsorted(ts, a), np.searchsorted(ts, b)
                    if best_thr is None or i1 <= i0:
                        sleeves.append(np.ones(max(0, i1 - i0))); continue
                    p_all = np.zeros(len(ts))
                    fin = np.isfinite(F).all(1)
                    p_all[fin] = clf.predict_proba(F[fin])[:, 1]
                    fire = (p_all >= best_thr) & ~df["gap"].values
                    eq, rets = seq_trade(ts, fire, y, ok, H, model, i0, i1)
                    sleeves.append(eq); yr_rets += rets
                L = min(len(s) for s in sleeves)
                tot = sum(s[:L] for s in sleeves) / 3
                if L:
                    per_year[Y] = {"return": float(tot[-1] - 1), "trades": len(yr_rets),
                                   "win_rate": float(np.mean([r > 0 for r in yr_rets])) if yr_rets else 0.0}
                    level *= tot[-1]
                    all_rets += yr_rets
                print(model, H, Y, per_year.get(Y), file=sys.stderr)
            out["runs"][f"{model}_H{H}"] = {"total": level - 1, "per_year": per_year, "trades": len(all_rets),
                                            "win_rate": float(np.mean([r > 0 for r in all_rets])) if all_rets else 0.0,
                                            "avg_trade": float(np.mean(all_rets)) if all_rets else 0.0}
    (P.ROOT / "ml.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
