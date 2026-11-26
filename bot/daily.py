"""毎晩のまとめをスマホに通知（ntfy）。

日本時間の深夜1時をすぎて最初の実行で、1日に1回だけ送る（GitHubの実行は遅れることがあるので「1時ちょうど」ではない）。
内容：総資産と24時間の増減・今月・売買・保有・市場の気分。タップでボット画面。
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config as C, notify

JST = timezone(timedelta(hours=9))
MOOD_URL = "https://root-bot-dashboard.vercel.app/api/mood"


def _mood():
    try:
        req = urllib.request.Request(MOOD_URL, headers={"User-Agent": "root-bot"})
        d = json.loads(urllib.request.urlopen(req, timeout=10).read())
        return d if d.get("value") is not None else None
    except Exception as e:
        print(f"[warn] 市場の気分を読めませんでした: {e}", file=sys.stderr)
        return None


def build(state, summary, now_ms, mood=None):
    day = now_ms - 86_400_000
    eh = state.get("equity_history") or []
    past = next((v for t, v in eh if t >= day), eh[0][1] if eh else summary["equity"])
    eq = summary["equity"]
    ch = eq - past
    trades = [t for t in state["trades"] if t["ts"] >= day]
    sells = [t for t in trades if t["side"] == "sell"]
    pnl = sum(t.get("pnl") or 0 for t in sells)
    names = notify.NAMES
    lines = [f"総資産 {notify.yen(eq)}（24時間 {'+' if ch >= 0 else ''}{notify.yen(ch)}）",
             f"今月 {summary['month_ret']:+.2%}（目標 +{C.MONTHLY_TARGET:.0%}）"]
    if trades:
        lines.append(f"売買 {len(trades)}回：" + "・".join(f"{names.get(t['pair'], t['pair'])}{'買' if t['side'] == 'buy' else '売'}" for t in trades[-6:])
                     + (f"（決済 {'+' if pnl >= 0 else ''}{notify.yen(pnl)}）" if sells else ""))
    else:
        lines.append("売買なし（買う条件を待っています）")
    pos = list(state["positions"])
    lines.append("保有：" + ("・".join(names.get(p, p) for p in pos) if pos else "なし"))
    if mood:
        lines.append(f"市場の気分：{mood['label']}（{mood['value']}）")
    if summary.get("halted"):
        lines.append("⛔ 今月は停止中（翌月に自動で再開）")
    date = datetime.fromtimestamp(now_ms / 1000, JST) - timedelta(hours=2)   # 深夜は「前の日」のまとめ
    tag = "（仮想）" if state["mode"] == "paper" else ""
    return f"🌙 今日のまとめ {date.month}/{date.day}{tag}", "\n".join(lines)


def run(state, summary, now_ms):
    now = datetime.fromtimestamp(now_ms / 1000, JST)
    key = (now - timedelta(hours=1)).strftime("%Y-%m-%d")       # 1時から翌日の1時までを「1日」とする
    if now.hour < 1 and state.get("daily_sent") is None:
        return None
    if state.get("daily_sent") == key:
        return None
    if state.get("daily_sent") is None and now.hour >= 1:
        state["daily_sent"] = key                                # 入れた初日は送らない（夜まで待つ）
        return None
    title, body = build(state, summary, now_ms, _mood())
    notify.send(title, body, tags=["crescent_moon"], priority=3)
    state["daily_sent"] = key
    return title
