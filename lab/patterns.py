"""「このパターンなら上がる／下がる」を大量に作って、本当に通用するものだけ残す研究（お金は使わない）。

  python -m lab.patterns          # lab/data/*.csv.gz が必要（lab.fetch で集める）

考え方
- 1時間足から「くせ」の候補を作る：値動き・RSI・移動平均からの距離・荒さ・出来高・高値安値の位置・
  連続陽線/陰線・下ヒゲ・時間帯・曜日・日付・BTC（他銘柄）の動き。1つの条件と、2つの組み合わせ。
- 持つ時間は 1・4・12・24・72時間。買い（現物）と売り（信用の空売り。参考）。
- 注文の出し方は2通りで計算：
    taker = 成行（手数料＋ズレ。BTC往復0.30%、ETH/XRP往復0.34%）
    maker = 指値（直前の終値に置き、次の1時間で価格がそこを割ったら約定＝不利な約定も含めて再現）
            手数料 BTC 0%、ETH/XRP −0.02%（受け取り）。決済も指値、ダメなら成行。
- 「たまたま」を消すため：
    1) 作る期間で統計的に強いものだけ（何万通り試したかを考慮＝BH法で誤発見率5%）
    2) 次の期間（答え合わせ1）でもプラスのものだけ採用
    3) 採用したルールで、まだ一度も見ていない期間（答え合わせ2）を実際に売買して評価
  さらに「毎年、それより前のデータだけでルールを選び直してその年を売買」を繰り返す（ウォークフォワード）。
  こちらは全期間が“未来を知らない”成績になる。
"""
import json
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
JST = timezone(timedelta(hours=9))
PAIRS = ["btc_jpy", "eth_jpy", "xrp_jpy"]
HORIZONS = [1, 4, 12, 24, 72]
TAKER_RT = {"btc_jpy": 0.0030, "eth_jpy": 0.0034, "xrp_jpy": 0.0034}   # 往復：手数料＋ズレ
TAKER_1 = {k: v / 2 for k, v in TAKER_RT.items()}
MAKER_FEE = {"btc_jpy": 0.0, "eth_jpy": -0.0002, "xrp_jpy": -0.0002}    # 片道（マイナス＝受け取り）
FILL_BUF = 0.0002      # 指値は「価格がわずかに突き抜けた」時だけ約定とみなす（順番待ちの分）
BORROW_DAY = 0.0004    # 信用の空売り：1日0.04%（参考用）
MIN_N = 150


def ts_ms(y, m=1, d=1):
    return int(datetime(y, m, d, tzinfo=JST).timestamp() * 1000)


def to_ms(idx):
    return np.asarray((idx - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1), dtype=np.int64)


# ---------------- データ ----------------
def load(pair):
    df = pd.read_csv(DATA / f"{pair}.csv.gz")
    df = df.drop_duplicates("ts").sort_values("ts")
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    full = pd.date_range(df.index[0], df.index[-1], freq="1h")
    df = df.reindex(full)
    df["gap"] = df["close"].isna()
    df["close"] = df["close"].ffill()
    for k in ("open", "high", "low"):
        df[k] = df[k].fillna(df["close"])
    df["volume"] = df["volume"].fillna(0.0)
    df["ts"] = to_ms(df.index)
    return df


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def raw_features(df, other):
    c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]
    lr = np.log(c).diff()
    f = {}
    for k in (1, 4, 24, 72, 168):
        f[f"ret{k}h"] = np.log(c / c.shift(k))
    f["rsi14"] = rsi(c)
    f["ma24_dist"] = c / c.rolling(24).mean() - 1
    f["ma168_dist"] = c / c.rolling(168).mean() - 1
    f["vol_regime"] = lr.rolling(24).std() / lr.rolling(720).std()
    f["volume_ratio"] = v.rolling(4).sum() / (v.rolling(168).mean() * 4)
    lo, hi = l.rolling(168).min(), h.rolling(168).max()
    f["range_pos7d"] = (c - lo) / (hi - lo)
    rng = (h - l).replace(0, np.nan)
    f["lower_wick"] = (np.minimum(o, c) - l) / rng
    f["upper_wick"] = (h - np.maximum(o, c)) / rng
    for name, s in other.items():
        oc = s.reindex(df.index).ffill()
        f[f"{name}_ret1h"] = np.log(oc / oc.shift(1))
        f[f"{name}_ret24h"] = np.log(oc / oc.shift(24))
    return pd.DataFrame(f, index=df.index)


def streak(c):
    s = np.sign(c.diff().fillna(0)).values
    out = np.zeros(len(s))
    for i in range(1, len(s)):
        if s[i] == 0:
            out[i] = 0
        elif np.sign(out[i - 1]) == s[i]:
            out[i] = out[i - 1] + s[i]
        else:
            out[i] = s[i]
    return pd.Series(out, index=c.index)


def conditions(df, feat, train_end):
    """条件（名前 → True/False の配列）。区切り値は「作る期間」だけで決める（未来を見ない）。"""
    conds, groups = {}, {}
    tr = df["ts"] < train_end
    for col in feat.columns:
        x = feat[col]
        qs = np.nanquantile(x[tr], [0.1, 0.3, 0.7, 0.9])
        b = np.digitize(x, qs)                 # 0..4：下位10%｜10-30｜30-70｜70-90｜上位10%
        b = np.where(x.isna(), -1, b)
        names = ["最下位10%", "下位10-30%", "中間", "上位10-30%", "最上位10%"]
        for k in (0, 1, 3, 4):
            conds[f"{col}:{names[k]}"] = b == k
            groups[f"{col}:{names[k]}"] = col
    st = streak(df["close"]).values
    for k, m in (("陰線3本以上", st <= -3), ("陰線5本以上", st <= -5), ("陽線3本以上", st >= 3), ("陽線5本以上", st >= 5)):
        conds[f"streak:{k}"] = m; groups[f"streak:{k}"] = "streak"
    jst = df.index.tz_convert(JST)
    hour, wd, dom = jst.hour.values, jst.weekday.values, jst.day.values
    for a in range(0, 24, 4):
        conds[f"時間帯:{a}-{a+3}時"] = (hour >= a) & (hour < a + 4); groups[f"時間帯:{a}-{a+3}時"] = "hour4"
    for w in range(7):
        conds[f"曜日:{'月火水木金土日'[w]}"] = wd == w; groups[f"曜日:{'月火水木金土日'[w]}"] = "weekday"
    for a, b2 in ((1, 3), (4, 10), (11, 20), (21, 24), (25, 27), (28, 31)):
        conds[f"日付:{a}-{b2}日"] = (dom >= a) & (dom <= b2); groups[f"日付:{a}-{b2}日"] = "dom"
    singles_only = {}
    for hh in range(24):
        singles_only[f"時刻:{hh}時"] = hour == hh
    return conds, groups, singles_only


# ---------------- 1回ごとの損益（注文の出し方別） ----------------
def trade_returns(df, pair, H, side, model):
    """各時刻 t に判断したときの、手数料後の損益と「約定したか」。"""
    c, h, l = df["close"].values, df["high"].values, df["low"].values
    n = len(c)
    ret = np.full(n, np.nan)
    sgn = 1 if side == "long" else -1
    borrow = BORROW_DAY * H / 24 if side == "short" else 0.0
    if model == "taker":
        # 判断の直後に成行（次の足の始値≒今の終値）→ H時間後に成行で決済
        e = c[:n - H]; x = c[H:]
        ret[:n - H] = sgn * (x / e - 1) - TAKER_RT[pair] - borrow
        filled = ~np.isnan(ret)
        return ret, filled
    # maker：終値 c[t] に指値。次の足 t+1 で突き抜けたら約定。決済は t+1+H の終値に指値、ダメなら次の足で成行。
    fee = MAKER_FEE[pair]
    ok = np.zeros(n, bool)
    idx = np.arange(n - H - 3)
    lim = c[idx]
    if side == "long":
        hit = l[idx + 1] <= lim * (1 - FILL_BUF)
    else:
        hit = h[idx + 1] >= lim * (1 + FILL_BUF)
    xp = c[idx + 1 + H]
    if side == "long":
        xhit = h[idx + 2 + H] >= xp * (1 + FILL_BUF)
    else:
        xhit = l[idx + 2 + H] <= xp * (1 - FILL_BUF)
    x_taker = c[idx + 2 + H]
    exit_px = np.where(xhit, xp, x_taker)
    exit_fee = np.where(xhit, fee, TAKER_1[pair])
    r = sgn * (exit_px / lim - 1) - fee - exit_fee - borrow
    ret[idx] = np.where(hit, r, np.nan)
    ok[idx] = hit
    return ret, ok


# ---------------- 何万通りを一気に集計 ----------------
def scan(X, y, valid, period_mask):
    """X: 条件(T×K)。2条件の組み合わせ (i,j) ごとの 件数・合計・二乗和 を行列計算で出す。"""
    m = (valid & period_mask).astype(np.float32)
    yy = np.where(valid & period_mask, y, 0.0).astype(np.float32)
    Xf = X.astype(np.float32)
    N = Xf.T @ (Xf * m[:, None])
    S = Xf.T @ (Xf * yy[:, None])
    Q = Xf.T @ (Xf * (yy ** 2)[:, None])
    return N, S, Q


def stats(N, S, Q, H, model="taker"):
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = S / N
        var = (Q - N * mean ** 2) / (N - 1)
        t = mean / np.sqrt(var / N)
    hold = H + 2 if model == "maker" else H   # 指値は約定待ち・決済待ちの分だけ長く持つ
    t = t / math.sqrt(hold)   # 持ち時間が重なる分を保守的に割り引く（重なる最悪ケース）
    return mean, t


def bh_select(p, alpha=0.05):
    p = np.asarray(p)
    if len(p) == 0:
        return np.zeros(0, bool)
    o = np.argsort(p)
    thr = alpha * np.arange(1, len(p) + 1) / len(p)
    ok = p[o] <= thr
    k = np.max(np.where(ok)[0]) + 1 if ok.any() else 0
    sel = np.zeros(len(p), bool)
    sel[o[:k]] = True
    return sel


def norm_sf(z):
    return 0.5 * math.erfc(z / math.sqrt(2))


def mine(data, feats, train_rng, val_rng, models, sides, alpha=0.05, min_n=MIN_N):
    """作る期間で選び、答え合わせ1で確認。残ったルールのリストと、試した数を返す。"""
    cands = []
    tested = 0
    for pair in PAIRS:
        df = data[pair]
        conds, groups, singles = feats[pair]
        names = list(conds) + list(singles)
        X = np.column_stack([conds[k] for k in conds] + [singles[k] for k in singles])
        K1 = len(conds)
        g = [groups.get(k, "hour24") for k in names]
        ts = df["ts"].values
        trm = (ts >= train_rng[0]) & (ts < train_rng[1])
        vam = (ts >= val_rng[0]) & (ts < val_rng[1])
        # 組み合わせの有効マスク：同じ種類どうしは組まない／1時間単位の時刻は単独のみ
        K = len(names)
        allow = np.zeros((K, K), bool)
        for i in range(K):
            for j in range(i, K):
                if i == j:
                    allow[i, j] = True
                elif i < K1 and j < K1 and g[i] != g[j]:
                    allow[i, j] = True
        for H in HORIZONS:
            for side in sides:
                for model in models:
                    y, ok = trade_returns(df, pair, H, side, model)
                    valid = ok & ~np.isnan(y) & ~df["gap"].values
                    N, S, Q = scan(X, y, valid, trm)
                    mean, t = stats(N, S, Q, H, model)
                    good = allow & (N >= min_n) & np.isfinite(t)
                    ii, jj = np.where(good)
                    tested += len(ii)
                    for i, j, mu, tt, nn in zip(ii, jj, mean[ii, jj], t[ii, jj], N[ii, jj]):
                        if mu > 0 and tt > 2.5:
                            cands.append({"pair": pair, "H": H, "side": side, "model": model,
                                          "i": int(i), "j": int(j),
                                          "rule": names[i] if i == j else f"{names[i]} ＋ {names[j]}",
                                          "train_n": int(nn), "train_mean": float(mu), "train_t": float(tt)})
                    if not cands:
                        continue
                    Nv, Sv, Qv = scan(X, y, valid, vam)
                    mv, tv = stats(Nv, Sv, Qv, H, model)
                    for cd in cands:
                        if cd["pair"] == pair and cd["H"] == H and cd["side"] == side and cd["model"] == model and "val_n" not in cd:
                            cd["val_n"] = int(Nv[cd["i"], cd["j"]])
                            cd["val_mean"] = float(mv[cd["i"], cd["j"]]) if Nv[cd["i"], cd["j"]] > 1 else float("nan")
                            cd["val_t"] = float(tv[cd["i"], cd["j"]]) if Nv[cd["i"], cd["j"]] > 2 else float("nan")
    # 何通り試したかを考慮（BH法）
    p = [norm_sf(cd["train_t"]) for cd in cands]
    # 試した総数で補正：候補以外は p=1 とみなす
    pfull = np.ones(max(tested, len(p)))
    pfull[:len(p)] = p
    sel = bh_select(pfull, alpha)[:len(p)]
    passed_train = [cd for cd, s in zip(cands, sel) if s]
    survivors = [cd for cd in passed_train
                 if cd.get("val_n", 0) >= 30 and cd.get("val_mean", -1) > 0 and cd.get("val_t", 0) > 1.5]
    return survivors, passed_train, tested


# ---------------- 実際に売買してみる（重ならないように1銘柄1ポジション） ----------------
def simulate(data, feats, rules, start, end, alloc=1 / 3):
    """rules を使って [start, end) を売買。銘柄ごとに資金を1/3ずつ、複利。"""
    trades = []
    curves = {}
    for pair in PAIRS:
        df = data[pair]
        conds, groups, singles = feats[pair]
        names = list(conds) + list(singles)
        X = np.column_stack([conds[k] for k in conds] + [singles[k] for k in singles])
        ts = df["ts"].values
        rs = [r for r in rules if r["pair"] == pair]
        eq = np.ones(len(ts))
        cur = 1.0
        if not rs:
            curves[pair] = pd.Series(eq, index=df.index)
            continue
        sigs = []
        for r in rs:
            fire = X[:, r["i"]] & X[:, r["j"]] & ~df["gap"].values
            y, ok = trade_returns(df, pair, r["H"], r["side"], r["model"])
            sigs.append((r, fire, y, ok))
        busy_until = -1
        i0 = np.searchsorted(ts, start)
        i1 = np.searchsorted(ts, end)
        for t in range(i0, i1):
            eq[t] = cur
            if t <= busy_until:
                continue
            best = None
            for r, fire, y, ok in sigs:
                if fire[t]:
                    if best is None or r["train_t"] > best[0]["train_t"]:
                        best = (r, y, ok)
            if best is None:
                continue
            r, y, ok = best
            H = r["H"]
            if r["model"] == "maker":
                if not ok[t] or np.isnan(y[t]):
                    busy_until = t + 1          # 指値が約定しなかった → 1時間待って取り消し
                    continue
                end_t = t + 2 + H
            else:
                if np.isnan(y[t]):
                    continue
                end_t = t + H
            if end_t >= i1:
                break
            cur *= 1 + y[t]
            trades.append({"pair": pair, "ts": int(ts[t]), "ret": float(y[t]), "H": H, "side": r["side"], "rule": r["rule"]})
            busy_until = end_t
        eq[i1:] = cur
        eq[:i0] = 1.0
        curves[pair] = pd.Series(eq, index=df.index)
    idx = curves["btc_jpy"].index
    total = sum(curves[p].reindex(idx).ffill().fillna(1.0) * alloc for p in PAIRS)
    sl = (to_ms(total.index) >= start) & (to_ms(total.index) < end)
    return total[sl], trades


def summarize(curve, trades):
    if len(curve) == 0:
        return {}
    c = curve / curve.iloc[0]
    dd = (c / c.cummax() - 1).min()
    m = c.resample("ME").last()
    mret = m.pct_change()
    mret.iloc[0] = m.iloc[0] - 1
    rets = [t["ret"] for t in trades]
    return {"total": float(c.iloc[-1] - 1), "mdd": float(dd), "trades": len(trades),
            "win_rate": float(np.mean([r > 0 for r in rets])) if rets else 0.0,
            "avg_trade": float(np.mean(rets)) if rets else 0.0,
            "months": int(len(mret)), "months_plus": int((mret > 0).sum()), "months_hit5": int((mret >= 0.05).sum()),
            "monthly": {k.strftime("%Y-%m"): round(float(v), 4) for k, v in mret.items()}}


def buy_hold(data, start, end):
    out = []
    for p in PAIRS:
        c = data[p]["close"]
        ts = data[p]["ts"]
        s = c[(ts >= start) & (ts < end)]
        out.append(s / s.iloc[0] / 3)
    tot = sum(x.reindex(out[0].index).ffill() for x in out)
    return summarize(tot.dropna(), [])


def main():
    data = {p: load(p) for p in PAIRS}
    first = max(int(d["ts"].iloc[0]) for d in data.values())
    print("データ開始", datetime.fromtimestamp(first / 1000, JST).date(), file=sys.stderr)
    other = {"btc_jpy": {"eth": data["eth_jpy"]["close"]},
             "eth_jpy": {"btc": data["btc_jpy"]["close"]},
             "xrp_jpy": {"btc": data["btc_jpy"]["close"]}}
    end_all = min(int(d["ts"].iloc[-1]) for d in data.values())
    y0 = datetime.fromtimestamp(first / 1000, JST).year + 1   # 最初の年は助走（720時間の計算など）
    result = {"generated": datetime.now(JST).isoformat(), "data_from": y0, "models": {}}

    # ===== A. 固定分割：作る期間〜2022 ／ 答え合わせ1 2023-2024 ／ 答え合わせ2 2025〜 =====
    TR, VA, TE = (ts_ms(y0), ts_ms(2023)), (ts_ms(2023), ts_ms(2025)), (ts_ms(2025), end_all)
    feats = {}
    for p in PAIRS:
        f = raw_features(data[p], other[p])
        feats[p] = conditions(data[p], f, TR[1])
    for label, models, sides in (("taker_long", ["taker"], ["long"]), ("maker_long", ["maker"], ["long"]),
                                 ("maker_both", ["maker"], ["long", "short"])):
        surv, passed, tested = mine(data, feats, TR, VA, models, sides)
        surv = sorted(surv, key=lambda r: -r["train_t"])
        res = {"tested": tested, "passed_train": len(passed), "survivors": len(surv),
               "top_rules": [{k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items() if k not in ("i", "j")}
                             for r in surv[:25]]}
        for nm, (a, b) in (("train", TR), ("val", VA), ("test", TE)):
            curve, trades = simulate(data, feats, surv, a, b)
            s = summarize(curve, trades)
            s.pop("monthly", None) if nm != "test" else None
            res[nm] = s
        result["models"][label] = res
        print(label, tested, len(passed), len(surv), {k: res[k].get("total") for k in ("train", "val", "test")}, file=sys.stderr)
    result["buy_hold_test"] = buy_hold(data, *TE)
    result["split"] = {"train": [y0, 2022], "val": [2023, 2024], "test": [2025, "now"]}

    # ===== B. ウォークフォワード：毎年、前年までで選び直してその年を売買 =====
    wf = {}
    for label, models, sides in (("taker_long", ["taker"], ["long"]), ("maker_long", ["maker"], ["long"])):
        curves, all_trades, per_year = [], [], {}
        level = 1.0
        for Y in range(y0 + 3, 2027):
            tr, va, te = (ts_ms(y0), ts_ms(Y - 1)), (ts_ms(Y - 1), ts_ms(Y)), (ts_ms(Y), min(ts_ms(Y + 1), end_all))
            fz = {p: conditions(data[p], raw_features(data[p], other[p]), tr[1]) for p in PAIRS}
            surv, _, _ = mine(data, fz, tr, va, models, sides)
            curve, trades = simulate(data, fz, surv, *te)
            if len(curve):
                per_year[Y] = {"rules": len(surv), "return": float(curve.iloc[-1] / curve.iloc[0] - 1), "trades": len(trades)}
                curves.append(curve / curve.iloc[0] * level)
                level = float(curves[-1].iloc[-1])
                all_trades += trades
            print("wf", label, Y, per_year.get(Y), file=sys.stderr)
        full = pd.concat(curves) if curves else pd.Series(dtype=float)
        s = summarize(full, all_trades)
        s["per_year"] = per_year
        wf[label] = s
    result["walk_forward"] = wf
    result["buy_hold_wf"] = buy_hold(data, ts_ms(y0 + 3), end_all)

    out = ROOT / "patterns.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print("saved", out, file=sys.stderr)


if __name__ == "__main__":
    main()
