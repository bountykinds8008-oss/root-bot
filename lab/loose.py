"""比較用：「たまたま」対策をゆるめると何が起きるか（作る期間で強く見えたものが未知の期間でどうなるか）。

  python -m lab.loose
"""
import json, math, sys
import numpy as np
from lab import patterns as P

_orig = P.stats


def loose_stats(N, S, Q, H, model="taker"):
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = S / N
        var = (Q - N * mean ** 2) / (N - 1)
        t = mean / np.sqrt(var / N)
    return mean, t          # 重なりの割り引きなし


def main():
    data = {p: P.load(p) for p in P.PAIRS}
    other = {"btc_jpy": {"eth": data["eth_jpy"]["close"]}, "eth_jpy": {"btc": data["btc_jpy"]["close"]},
             "xrp_jpy": {"btc": data["btc_jpy"]["close"]}}
    end_all = min(int(d["ts"].iloc[-1]) for d in data.values())
    TR, VA, TE = (P.ts_ms(2019), P.ts_ms(2023)), (P.ts_ms(2023), P.ts_ms(2025)), (P.ts_ms(2025), end_all)
    feats = {p: P.conditions(data[p], P.raw_features(data[p], other[p]), TR[1]) for p in P.PAIRS}
    P.stats = loose_stats
    out = {}
    for label, models in (("taker_long", ["taker"]), ("maker_long", ["maker"])):
        surv, passed, tested = P.mine(data, feats, TR, VA, models, ["long"], alpha=1.0)
        # ゆるい採用：作る期間 t>2.5（mine内）＋答え合わせ1が平均プラス・t>1.5
        surv = sorted(surv, key=lambda r: -r["train_t"])[:30]
        res = {"tested": tested, "looked_good_in_train": len(passed), "kept_after_val": len(surv),
               "examples": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k not in ("i", "j")} for r in surv[:8]]}
        for nm, (a, b) in (("train", TR), ("val", VA), ("test", TE)):
            c, tr = P.simulate(data, feats, surv, a, b)
            s = P.summarize(c, tr); s.pop("monthly", None); res[nm] = s
        out[label] = res
        print(label, tested, len(passed), len(surv), {k: (round(res[k].get("total", 0), 3), res[k].get("trades"), round(res[k].get("win_rate", 0), 3)) for k in ("train", "val", "test")}, file=sys.stderr)
    out["buy_hold_test"] = P.buy_hold(data, *TE)
    (P.ROOT / "loose.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
