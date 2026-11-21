"""③ 成績レビュアー：ボット自身の成績を毎回検証する（お金は動かさない・判断ルールは変えない）。

  python -m bot.review        … 最新の data/review.json を文章で表示

4人体制のうちの「検証役」。売買はせず、次を数字で確かめて data/review.json に残す。
  - 稼働：ちゃんと動いているか（実行の間隔）
  - 成績：資産・落ち込み・ただ持ち続けた場合との比較
  - 取引：勝率だけでなく「平均利益÷平均損失」と、損益がゼロになる勝率（損益分岐）
  - 照合：過去検証（research.json）で見込まれる売買回数・落ち込みの範囲に収まっているか
  - 本番に進む前の確認表（目安）
ここで出す判定は「正常／注意／要確認」の3つだけ。売買の指示や利益の予想は出さない。
"""
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as C
from .engine import signal

ROOT = Path(__file__).resolve().parent.parent
REVIEW = ROOT / "data" / "review.json"
RESEARCH = ROOT / "data" / "research.json"
BACKTEST = ROOT / "data" / "backtest.json"
JST = timezone(timedelta(hours=9))
HOUR, DAY = 3600_000, 86_400_000
RANK = {"ok": 0, "wait": 1, "warn": 2, "alert": 3}
VERDICT = {0: "正常", 1: "正常（データ待ち）", 2: "注意", 3: "要確認"}
SYM = {"btc_jpy": "BTC", "eth_jpy": "ETH", "xrp_jpy": "XRP"}


def _jst(ts):
    return datetime.fromtimestamp(ts / 1000, JST).strftime("%m/%d %H:%M")


def _yen(v):
    return ("−" if v < 0 else "") + f"¥{abs(round(v)):,}"


def _pct(v, d=1):
    if abs(v) * 100 < 0.5 * 10 ** -d:
        return f"{0:.{d}f}%"
    return f"{v * 100:+.{d}f}%".replace("-", "−")


# ---------------- 過去検証（②モデルビルダーの成果物）から、今の作戦の見込みを取り出す ----------------
def expectation(research):
    """research.json から、config の作戦と同じ設定の成績を探す。無ければ None。"""
    if not research or C.STRATEGY != "donchian_4h":
        return None
    for r in research.get("all", []):
        p = r["params"]
        if (p["tf"] == C.CANDLE and p["entry"] == "donchian" and p["don_in"] == C.DON_IN
                and p["don_out"] == C.DON_OUT and p["atr_k"] == C.STOP_ATR and not p["regime"]
                and p["voltarget"] == C.VOL_TARGET and p["fee"] == "taker"):
            months = r["in"]["months"] + r["out"]["months"]
            return {"trades_per_month": r["trades"] / months if months else None,
                    "months": months, "trades": r["trades"],
                    "worst_mdd": min(r["in"]["mdd"], r["out"]["mdd"]),
                    "out_avg_month": r["out"]["avg_m"], "out_total": r["out"]["total"],
                    "out_months": r["out"]["months"], "out_hit5": r["out"]["hit5"],
                    "generated": research.get("generated"), "n_variants": research.get("n_variants")}
    return None


def backtest_matches_strategy(backtest):
    """backtest.json が今の作戦で作られたものか。v2で無効にしたふるいの記録があれば旧作戦の結果。"""
    if not backtest:
        return None
    old = set((backtest.get("funnel") or {}).get("filtered", {})) & {"pause", "volatile", "surge"}
    off = not (C.USE_LOSS_PAUSE or C.USE_VOLATILE_FILTER or C.USE_SURGE_FILTER)
    return not (old and off)


# ---------------- 取引の検証 ----------------
def trade_stats(trades):
    """決済ごとの損益（買いの手数料も引いた正味）から、勝率・損益比・損益分岐の勝率を出す。"""
    last_buy_fee, nets, fees = {}, [], 0.0
    for t in trades:
        fees += t.get("fee") or 0.0
        if t["side"] == "buy":
            last_buy_fee[t["pair"]] = t.get("fee") or 0.0
        elif t.get("pnl") is not None:
            nets.append(t["pnl"] - last_buy_fee.pop(t["pair"], 0.0))
    wins, losses = [x for x in nets if x > 0], [x for x in nets if x <= 0]
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = -sum(losses) / len(losses) if losses else None
    out = {"closed": len(nets), "wins": len(wins), "losses": len(losses),
           "win_rate": len(wins) / len(nets) if nets else None,
           "avg_win": avg_win, "avg_loss": avg_loss,
           "payoff": avg_win / avg_loss if avg_win and avg_loss else None,
           # 損益がちょうどゼロになる勝率 = 平均損失 ÷（平均利益＋平均損失）
           "breakeven_win_rate": avg_loss / (avg_win + avg_loss) if avg_win and avg_loss else None,
           "expectancy": sum(nets) / len(nets) if nets else None,
           "net_total": sum(nets), "fees_total": fees,
           "entries": sum(1 for t in trades if t["side"] == "buy")}
    return out


def max_drawdown(history):
    peak, mdd = 0.0, 0.0
    for _, e in history:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, e / peak - 1)
    return mdd


def hold_benchmark(state, market, start_ms):
    """開始時点から3銘柄を均等に「ただ持ち続けた」場合の損益（手数料なしの物差し）。

    開始時の価格は最初に一度だけ足から求めて state["bench_start"] に残す（後で足の範囲から外れても比べられる）。
    """
    base = state.setdefault("bench_start", {})
    rets = {}
    for pair, m in market.items():
        if pair not in base:
            before = [b for b in (m.get("bars") or []) if b[5] + C.BAR_MS <= start_ms]
            if before:
                base[pair] = before[-1][3]
        if pair in base and m.get("bid"):
            rets[pair] = m["bid"] / base[pair] - 1
    if not rets:
        return None
    return {"return": sum(rets.values()) / len(rets), "by_pair": rets}


# ---------------- 本体 ----------------
def build(state, market, now_ms, research=None, backtest=None):
    hist = state.get("equity_history") or [[now_ms, state["total_start_equity"]]]
    ts = [t for t, _ in hist]
    start_ms, equity = ts[0], hist[-1][1]
    days = (now_ms - start_ms) / DAY
    checks = []

    def check(key, label, status, text):
        checks.append({"key": key, "label": label, "status": status, "text": text})

    # --- 稼働 ---
    week = [t for t in ts if t >= now_ms - 7 * DAY]
    gaps = [(b - a) / HOUR for a, b in zip(week, week[1:])]
    since_last = (now_ms - ts[-1]) / HOUR if len(ts) > 1 else 0.0   # 今回の記録が入る前の間隔は gaps 側で見る
    max_gap = max(gaps) if gaps else None
    med_gap = sorted(gaps)[len(gaps) // 2] if gaps else None
    ops = {"start": start_ms, "days_running": days, "ticks_24h": sum(t >= now_ms - DAY for t in ts),
           "max_gap_h_7d": max_gap, "median_gap_h_7d": med_gap, "last_tick": ts[-1]}
    bar_h = C.BAR_MS / HOUR
    if max_gap is None:
        check("ops", "稼働", "wait", "実行の記録がまだ少なく、間隔を判定できません")
    elif max_gap > C.REVIEW_STALE_GAP_H or since_last > C.REVIEW_STALE_GAP_H:
        check("ops", "稼働", "alert",
              f"直近7日で最長 {max_gap:.1f}時間 実行が空きました（{C.REVIEW_STALE_GAP_H}時間超）。止まっていた可能性があります")
    elif max_gap > bar_h:
        check("ops", "稼働", "warn",
              f"実行の間隔が最長 {max_gap:.1f}時間（中央 {med_gap:.1f}時間）。判断に使う足は{bar_h:.0f}時間なので、"
              "買いの判断が遅れて見送りになる場面があります。損切りは1時間足の安値で補っています")
    else:
        check("ops", "稼働", "ok", f"実行の間隔は最長 {max_gap:.1f}時間（{bar_h:.0f}時間以内）")

    # --- 成績 ---
    total_ret = equity / state["total_start_equity"] - 1
    base = state.get("month_start_equity") or equity
    month_ret = equity / base - 1 if base else 0.0
    mdd = max_drawdown(hist)
    bench = hold_benchmark(state, market, start_ms)
    perf = {"equity": equity, "total_return": total_ret, "month_return": month_ret,
            "max_drawdown": mdd, "hold_return": bench["return"] if bench else None,
            "hold_by_pair": bench["by_pair"] if bench else None,
            "to_target_jpy": base * (1 + C.MONTHLY_TARGET) - equity,
            "to_stop_jpy": equity - base * (1 - C.MONTHLY_MAX_LOSS)}

    # --- 取引 ---
    tr = trade_stats(state.get("trades") or [])
    if tr["closed"] == 0:
        check("trades", "取引の質", "wait", "決済した取引がまだありません。勝率・損益比は出せません")
    elif tr["payoff"] is None:
        check("trades", "取引の質", "wait",
              f"決済 {tr['closed']}回（{tr['wins']}勝{tr['losses']}敗）。勝ちと負けの両方がそろうまで損益比は出せません")
    else:
        few = tr["closed"] < C.REVIEW_MIN_TRADES_FOR_STATS
        ok = tr["win_rate"] > tr["breakeven_win_rate"]
        text = (f"決済 {tr['closed']}回・勝率 {tr['win_rate']:.0%}（損益分岐 {tr['breakeven_win_rate']:.0%}）・"
                f"平均利益÷平均損失 {tr['payoff']:.2f}・1回あたり {_yen(tr['expectancy'])}")
        if few:
            text += f"。ただし{C.REVIEW_MIN_TRADES_FOR_STATS}回未満なので、まだ偶然と区別できません"
        check("trades", "取引の質", "wait" if few else ("ok" if ok else "warn"), text)

    # --- 過去検証との照合 ---
    exp = expectation(research)
    fit = {"expectation": exp}
    if not exp or not exp.get("trades_per_month"):
        check("fit", "検証との照合", "wait", "今の作戦に合う比較研究の結果が見つかりません（②の研究をやり直すと照合できます）")
    else:
        lam = exp["trades_per_month"] * days / 30.44
        p_zero = math.exp(-lam)
        fit.update({"expected_entries": lam, "actual_entries": tr["entries"], "p_zero": p_zero})
        if tr["entries"] == 0 and p_zero < 0.05:
            check("fit", "検証との照合", "warn",
                  f"検証では月{exp['trades_per_month']:.1f}回ほど買う作戦ですが、{days:.0f}日で0回です"
                  f"（偶然こうなる見込み {p_zero:.0%}）。実行の間隔や見送りの理由を確認してください")
        elif tr["entries"] == 0:
            check("fit", "検証との照合", "ok",
                  f"買いは月{exp['trades_per_month']:.1f}回ほどの作戦です。開始{days:.0f}日で0回は普通の範囲です"
                  f"（こうなる見込み {p_zero:.0%}）")
        else:
            check("fit", "検証との照合", "ok",
                  f"買い {tr['entries']}回（検証からの見込み {lam:.1f}回）")
        if mdd < exp["worst_mdd"]:
            check("dd", "落ち込み", "alert",
                  f"最大の落ち込み {_pct(mdd)} が、検証で最も悪かった {_pct(exp['worst_mdd'])} を超えました")
        else:
            check("dd", "落ち込み", "ok",
                  f"最大の落ち込み {_pct(mdd)}（検証で最も悪かった時は {_pct(exp['worst_mdd'])}）")

    bt_ok = backtest_matches_strategy(backtest)
    if bt_ok is False:
        check("backtest", "検証データ", "warn",
              "画面の「過去データで検証」は前の作戦のときの結果です。今の作戦の成績ではありません（再検証が必要）")

    # --- 各銘柄：買い条件まであとどれくらいか ---
    setups = []
    for pair in C.PAIRS:
        m = market.get(pair)
        if not m or not m.get("bars"):
            continue
        sig = signal(m["bars"])
        if not sig:
            continue
        pos = (state.get("positions") or {}).get(pair)
        setups.append({"pair": pair, "close": sig["close"], "break_level": sig["break_level"],
                       "to_break": sig["break_level"] / sig["close"] - 1,
                       "holding": bool(pos),
                       "stop": pos["stop"] if pos else None,
                       "to_stop": pos["stop"] / m["bid"] - 1 if pos and m.get("bid") else None})

    # --- 本番に進む前の確認表（目安。決めるのは本人） ---
    ready = [
        {"label": f"仮想売買を{C.REVIEW_MIN_DAYS}日以上", "done": days >= C.REVIEW_MIN_DAYS,
         "now": f"{days:.0f}日"},
        {"label": f"決済を{C.REVIEW_MIN_TRADES}回以上", "done": tr["closed"] >= C.REVIEW_MIN_TRADES,
         "now": f"{tr['closed']}回"},
        {"label": "1回あたりの損益がプラス（手数料込み）", "done": bool(tr["expectancy"] and tr["expectancy"] > 0),
         "now": _yen(tr["expectancy"]) if tr["expectancy"] is not None else "まだ出せない"},
        {"label": f"実行の間隔が{bar_h:.0f}時間以内", "done": max_gap is not None and max_gap <= bar_h,
         "now": f"最長{max_gap:.1f}時間" if max_gap is not None else "まだ出せない"},
        {"label": "落ち込みが検証の範囲内", "done": bool(exp) and mdd >= exp["worst_mdd"],
         "now": _pct(mdd)},
    ]

    worst = max((RANK[c["status"]] for c in checks), default=0)
    halted = state.get("total_halted") or state.get("halted_month") == state.get("month")
    funnel = (state.get("funnel") or {}).get(state.get("month") or "", {})
    review = {
        "generated": now_ms, "mode": state["mode"], "verdict": VERDICT[worst], "level": worst,
        "ops": ops, "performance": perf, "trades": tr, "fit": fit, "setups": setups,
        "checks": checks, "readiness": ready, "ready_count": sum(r["done"] for r in ready),
        "funnel_month": funnel,
    }
    review["team"] = _team(review, state, market, exp, halted)
    return review


def _team(rv, state, market, exp, halted):
    """4人それぞれの「いま」を1行で。画面とレポートの共通部品。"""
    got = [SYM[p] for p in C.PAIRS if market.get(p, {}).get("bars")]
    near = min(rv["setups"], key=lambda s: s["to_break"]) if rv["setups"] else None
    r1 = (f"{'・'.join(got)} の価格と足を取得" if got else "価格を取得できませんでした")
    if near and not near["holding"]:
        r1 += f"。買い条件に一番近いのは {SYM[near['pair']]}（あと {_pct(max(0.0, near['to_break']))}）"
    if exp:
        r2 = (f"{exp['n_variants']}通りを比べて選んだ「4時間足の上抜け買い」。"
              f"答え合わせ期間は{exp['out_months']}か月で {_pct(exp['out_total'])}、"
              f"月+5%に届いたのは{exp['out_hit5']}か月")
    else:
        r2 = "4時間足の上抜け買い（比較研究の結果は未取得）"
    bad = [c for c in rv["checks"] if c["status"] in ("warn", "alert")]
    r3 = f"判定：{rv['verdict']}" + (f"（{bad[0]['label']}ほか{len(bad) - 1}件）" if len(bad) > 1
                                  else f"（{bad[0]['label']}）" if bad else "")
    p = rv["performance"]
    n = len(state.get("positions") or {})
    if halted:
        r4 = "停止中：損失の上限に達したため、新しい買いを止めています"
    else:
        r4 = (f"保有 {n}/{len(C.PAIRS)}・今月の停止ラインまで {_yen(p['to_stop_jpy'])}・"
              f"1銘柄は資産の最大{C.ALLOC_PER_PAIR:.0%}まで")
    return [
        {"no": 1, "role": "リサーチャー", "job": "価格・出来高・板を集める", "status": "ok" if len(got) == len(C.PAIRS) else "alert", "line": r1},
        {"no": 2, "role": "モデルビルダー", "job": "過去データで作戦を比べて選ぶ", "status": "ok" if exp else "wait", "line": r2},
        {"no": 3, "role": "成績レビュアー", "job": "ボット自身の成績を検証する", "status": ["ok", "wait", "warn", "alert"][rv["level"]], "line": r3},
        {"no": 4, "role": "リスクマネージャー", "job": "買う量・損切り・停止を決める", "status": "alert" if halted else "ok", "line": r4},
    ]


# ---------------- 文章のレポート ----------------
MARK = {"ok": "○", "wait": "…", "warn": "△", "alert": "×"}


def to_text(rv):
    p, tr = rv["performance"], rv["trades"]
    L = [f"root_bot レビュー（{_jst(rv['generated'])} 時点・{'本番' if rv['mode'] == 'live' else '仮想売買'}）",
         f"判定：{rv['verdict']}", "",
         "■ 4人の状況"]
    L += [f"{t['no']}. {t['role']} {MARK[t['status']]} {t['line']}" for t in rv["team"]]
    L += ["", "■ 成績",
          f"資産 {_yen(p['equity'])}（通算 {_pct(p['total_return'], 2)}・今月 {_pct(p['month_return'], 2)}）",
          f"最大の落ち込み {_pct(p['max_drawdown'])}"]
    if p["hold_return"] is not None:
        L.append(f"同じ期間ただ持ち続けた場合 {_pct(p['hold_return'])}（3銘柄均等・手数料なし）")
    L += ["", "■ 検証"] + [f"{MARK[c['status']]} {c['label']}：{c['text']}" for c in rv["checks"]]
    L += ["", f"■ 本番に進む前の確認（目安）{rv['ready_count']}/{len(rv['readiness'])}"]
    L += [f"{'☑' if r['done'] else '☐'} {r['label']}（いま {r['now']}）" for r in rv["readiness"]]
    L += ["", "※ 判定は検証結果の要約です。売買の指示や利益の見込みではありません。"]
    return "\n".join(L)


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def main():
    rv = load_json(REVIEW)
    if not rv:
        print("data/review.json がまだありません（ボットが1回動くと作られます）", file=sys.stderr)
        sys.exit(1)
    print(to_text(rv))


if __name__ == "__main__":
    main()
