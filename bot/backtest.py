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


def fetch_hourly(pair, start, end):
    bars, d = {}, start
    while d <= end:
        try:
            raw = bitbank._http(bitbank.urllib.request.Request(
                f"{bitbank.PUBLIC}/{pair}/candlestick/1hour/{d.strftime('%Y%m%d')}"))
            for o, h, l, c, v, ts in raw["candlestick"][0]["ohlcv"]:
                bars[int(ts)] = (float(o), float(h), float(l), float(c), float(v), int(ts))
        except Exception as e:
            print(f"[skip] {pair} {d}: {e}", file=sys.stderr)
        d += timedelta(days=1)
        time.sleep(0.12)  # レート制限に配慮
    return [bars[t] for t in sorted(bars)]


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
            market[p] = {"bars": bs[max(0, i - 120): i + 1], "bid": b[3], "ask": b[3], "low": b[2]}
        step(state, broker, market, ts + 3600_000)
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
    }


def main():
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else date.today().year - 1
    y1 = int(sys.argv[2]) if len(sys.argv) > 2 else date.today().year
    start, end = date(y0, 1, 1), min(date(y1, 12, 31), date.today() - timedelta(days=1))
    data = {p: fetch_hourly(p, start, end) for p in C.PAIRS}
    state = run(data)
    result = summarize(state)
    result["period"] = f"{start} 〜 {end}"
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "backtest.json").write_text(json.dumps(
        {**result, "equity_history": state["equity_history"][::6]}, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
