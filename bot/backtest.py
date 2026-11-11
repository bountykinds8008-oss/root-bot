"""過去データで検証する（実際のお金は使わない）。

  python -m bot.backtest 2025 2026
bitbankの1時間足を日ごとに取得し、本番と同じ engine.step で1本ずつ再生する。
結果は data/backtest.json に保存（画面でも表示できる）。
"""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from . import bitbank, config as C
from .brokers import PaperBroker
from .engine import new_state, step

ROOT = Path(__file__).resolve().parent.parent


def fetch_bars(pair, start, end):
    bars = {}
    for y in range(start.year, end.year + 1):
        try:
            for r in bitbank.candles_year(pair, C.CANDLE, y):
                bars[r[5]] = r
        except Exception as e:
            print(f"[skip] {pair} {y}: {e}", file=sys.stderr)
        time.sleep(0.2)
    s0 = int(time.mktime(start.timetuple()) * 1000)
    return [bars[t] for t in sorted(bars) if t >= s0 - 200 * C.BAR_MS]


def run(data, start_jpy=C.PAPER_START_JPY):
    """data[pair] = 1時間足リスト。全ペア共通の時刻で1本ずつ進める。"""
    C.MAX_EQUITY_POINTS = C.MAX_TRADES_KEPT = 10**7  # 検証では全履歴を残す
    state, broker = new_state(start_jpy, "paper"), PaperBroker()
    idx = {p: {b[5]: i for i, b in enumerate(bs)} for p, bs in data.items()}
    times = sorted(set().union(*[set(i) for i in idx.values()]))
    for ts in times:
        market = {}
        for p, bs in data.items():
            i = idx[p].get(ts)
            if i is None:
                continue
            b = bs[i]
            market[p] = {"bars": bs[max(0, i - 200): i + 1], "bid": b[3], "ask": b[3], "low": b[2]}
        step(state, broker, market, ts + C.BAR_MS)
    return state


def summarize(state):
    rows = []
    for m, v in sorted(state["monthly"].items()):
        s, e = v.get("start"), v.get("end")
        if s and e:
            rows.append({"month": m, "return": e / s - 1})
    sells = [t for t in state["trades"] if t["side"] == "sell"]
    wins = [t for t in sells if (t["pnl"] or 0) > 0]
    eq = [e for _, e in state["equity_history"]]
    peak, mdd = 0, 0
    for e in eq:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    return {
        "start": state["total_start_equity"], "end": eq[-1] if eq else None,
        "total_return": (eq[-1] / state["total_start_equity"] - 1) if eq else None,
        "max_drawdown": mdd, "trades": len(sells),
        "win_rate": len(wins) / len(sells) if sells else None,
        "months": rows,
        "months_hit_target": sum(r["return"] >= C.MONTHLY_TARGET for r in rows),
        "months_negative": sum(r["return"] < 0 for r in rows),
        "funnel": _sum_funnel(state.get("funnel", {})),
    }


def _sum_funnel(f):
    out = {"checked": 0, "no_setup": 0, "filtered": {}, "entered": 0}
    for v in f.values():
        for k in ("checked", "no_setup", "entered"):
            out[k] += v[k]
        for r, n in v["filtered"].items():
            out["filtered"][r] = out["filtered"].get(r, 0) + n
    return out


def main():
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else date.today().year - 1
    y1 = int(sys.argv[2]) if len(sys.argv) > 2 else date.today().year
    start, end = date(y0, 1, 1), min(date(y1, 12, 31), date.today() - timedelta(days=1))
    data = {p: fetch_bars(p, start, end) for p in C.PAIRS}
    state = run(data)
    result = summarize(state)
    result["period"] = f"{start} 〜 {end}"
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "backtest.json").write_text(json.dumps(
        {**result, "equity_history": state["equity_history"][::6]}, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
