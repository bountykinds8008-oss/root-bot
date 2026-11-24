"""1回分の実行（GitHub Actions が30分ごとに呼ぶ。遅れても engine が後追い処理する）。

  python -m bot.run
状態は data/state.json、画面用データは data/status.json に保存する。
"""
import json
import os
import sys
import time
from pathlib import Path

from . import bitbank, config as C, notify
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
        "funnel": state.get("funnel", {}), "last_reject": state.get("last_reject", {}),
        "catchup_bars": state.get("catchup_bars", 0),   # 実行の遅れで後追い処理した足の数（通算）
        "version": "v3",
        "runs": state.get("runs", [])[-200:],               # 実行した時刻（今日の振り返り用）
        "started_at": state.get("started_at"),
        "orders": [{"pair": p, "limit": o["limit"], "placed_at": o["placed_at"], "deadline": o["deadline"]}
                   for p, o in state.get("orders", {}).items()],   # 指値で待っている買い注文              # 仮想売買を始めた時刻（本番準備の確認用）
        "live_max_jpy": C.LIVE_MAX_JPY,
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
            bars = bitbank.candles(pair, C.CANDLE)
        except Exception as e:  # 1ペアの失敗で全体を止めない
            print(f"[warn] {pair}: {e}", file=sys.stderr)
            continue
        low = min(t["buy"], prev_low.get(pair, t["buy"]))
        market[pair] = {"bars": bars, "bid": t["buy"], "ask": t["sell"], "low": low}
    state["last_price"] = {p: m["bid"] for p, m in market.items()}
    if not state.get("started_at"):
        eh = state.get("equity_history") or []
        state["started_at"] = eh[0][0] if eh else int(time.time() * 1000)

    now = int(time.time() * 1000)
    before = {"n_trades": len(state["trades"]), "last_ts": max([t["ts"] for t in state["trades"]] or [0]),
              "halted": state.get("was_halted", False), "defensive": state.get("was_defensive", False)}
    summary = step(state, broker, market, now)
    state["was_halted"], state["was_defensive"] = summary["halted"], summary["defensive"]
    runs = [r for r in state.get("runs", []) if r > now - 7 * 864e5] + [now]   # 実行の記録（振り返り用・7日分）
    state["runs"] = runs[-400:]
    save(state, summary, market)
    if os.environ.get("ROOT_BOT_NOTIFY", "yes") == "yes":
        notify.trades([t for t in state["trades"] if t["ts"] > before["last_ts"]], state["mode"], summary["equity"])
        notify.events(before, state, summary)
    print(json.dumps({"mode": state["mode"], "equity": round(summary["equity"]),
                      "month_return": f"{summary['month_ret']:.2%}",
                      "halted": summary["halted"], "positions": list(state["positions"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
