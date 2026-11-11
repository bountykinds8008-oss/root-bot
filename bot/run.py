"""1回分の実行（GitHub Actions が15分ごとに呼ぶ）。

  python -m bot.run
状態は data/state.json、画面用データは data/status.json に保存する。
"""
import json
import os
import sys
import time
from pathlib import Path

from . import bitbank, config as C
from .brokers import LiveBroker, PaperBroker
from .engine import new_state, step

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "state.json"
STATUS = ROOT / "data" / "status.json"
PUBLIC = ROOT / "dashboard" / "data" / "status.json"  # 画面（Vercel）が読む公開用コピー
PUBLISH_EVERY_MS = 55 * 60 * 1000  # 約1時間ごと（Vercelの公開回数を抑える）


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return None


def save(state, summary, market):
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    status = {
        "updated_at": int(time.time() * 1000), "mode": state["mode"],
        "equity": summary["equity"], "month": state["month"],
        "month_start_equity": state["month_start_equity"], "month_return": summary["month_ret"],
        "target": C.MONTHLY_TARGET, "max_loss": C.MONTHLY_MAX_LOSS,
        "halted": summary["halted"], "defensive": summary["defensive"],
        "total_start_equity": state["total_start_equity"],
        "cash": state["cash"] if state["mode"] == "paper" else None,
        "positions": [{"pair": p, **v, "price": market[p]["bid"] if p in market else None}
                      for p, v in state["positions"].items()],
        "equity_history": state["equity_history"], "trades": state["trades"][-100:],
        "monthly": state["monthly"], "log": state["log"][-30:],
        "bot_stats": state.get("bot_stats", {}),
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False))
    # 公開用コピー：前回から約1時間たったとき、または売買があったときだけ更新
    prev = json.loads(PUBLIC.read_text()) if PUBLIC.exists() else {}
    traded = len(prev.get("trades", [])) != len(status["trades"]) or prev.get("halted") != status["halted"]
    if traded or status["updated_at"] - prev.get("updated_at", 0) >= PUBLISH_EVERY_MS:
        PUBLIC.parent.mkdir(parents=True, exist_ok=True)
        PUBLIC.write_text(json.dumps(status, ensure_ascii=False))


def main():
    live = C.MODE == "live" and os.environ.get("LIVE_TRADING") == "yes"
    state = load_state()

    if live:
        priv = bitbank.Private(os.environ["BITBANK_API_KEY"], os.environ["BITBANK_API_SECRET"])
        broker = LiveBroker(priv)
        if state is None or state["mode"] != "live":
            jpy = broker.cash(None)
            state = new_state(jpy, "live")
            state["cash"] = jpy
    else:
        broker = PaperBroker()
        if state is None or state["mode"] != "paper":
            state = new_state(C.PAPER_START_JPY, "paper")

    market, prev_low = {}, state.get("last_price", {})
    for pair in C.PAIRS:
        try:
            t = bitbank.ticker(pair)
            bars = bitbank.candles(pair, C.CANDLE, days=5)
        except Exception as e:  # 1ペアの失敗で全体を止めない
            print(f"[warn] {pair}: {e}", file=sys.stderr)
            continue
        low = min(t["buy"], prev_low.get(pair, t["buy"]))
        market[pair] = {"bars": bars, "bid": t["buy"], "ask": t["sell"], "low": low}
    state["last_price"] = {p: m["bid"] for p, m in market.items()}

    now = int(time.time() * 1000)
    summary = step(state, broker, market, now)
    save(state, summary, market)
    print(json.dumps({"mode": state["mode"], "equity": round(summary["equity"]),
                      "month_return": f"{summary['month_ret']:.2%}",
                      "halted": summary["halted"], "positions": list(state["positions"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
