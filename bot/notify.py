"""スマホへの通知（ntfy）。ntfyアプリで config.NOTIFY_TOPIC を購読すると届く。

通知の失敗でボットの売買が止まらないよう、エラーはすべて無視する。
"""
import json
import sys
import urllib.request

from . import config as C

NAMES = {"btc_jpy": "BTC", "eth_jpy": "ETH", "xrp_jpy": "XRP"}


def send(title, message, tags=(), priority=3):
    if not C.NOTIFY_TOPIC:
        return
    body = {"topic": C.NOTIFY_TOPIC, "title": title, "message": message, "tags": list(tags),
            "priority": priority, "click": C.DASHBOARD_URL}
    try:
        req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"[warn] 通知を送れませんでした: {e}", file=sys.stderr)


def yen(v):
    return f"{'-' if v < 0 else ''}¥{abs(round(v)):,}"


def price(pair, px):
    return f"¥{px:,.3f}" if pair == "xrp_jpy" else f"¥{round(px):,}"


def trades(new, mode, equity):
    """新しい売買を1件ずつ通知する。"""
    tag = "（仮想）" if mode == "paper" else ""
    for t in new:
        sym = NAMES.get(t["pair"], t["pair"])
        if t["side"] == "buy":
            send(f"▲ 買い {sym}{tag}", f"{price(t['pair'], t['price'])} で買いました\n理由：{t['reason']}\n総資産 {yen(equity)}",
                 tags=["chart_with_upwards_trend"])
        else:
            pnl = t.get("pnl") or 0
            stop = "損切り" in t["reason"]
            send(f"{'🛑 損切り' if stop else '▼ 売り'} {sym}{tag}  {'+' if pnl >= 0 else ''}{yen(pnl)}",
                 f"{price(t['pair'], t['price'])} で売りました\n理由：{t['reason']}\n総資産 {yen(equity)}",
                 tags=["moneybag" if pnl >= 0 else "small_red_triangle_down"], priority=4 if stop else 3)


def events(before, state, summary):
    """停止・守りモードなど、状態が変わったときの通知。"""
    tag = "（仮想）" if state["mode"] == "paper" else ""
    if summary["halted"] and not before.get("halted"):
        send(f"⛔ 今月の売買を停止{tag}", f"月間の損失上限に届いたので全部決済しました。翌月に自動で再開します。\n総資産 {yen(summary['equity'])}",
             tags=["warning"], priority=5)
    if summary["defensive"] and not before.get("defensive"):
        send(f"🎯 今月の目標 +{C.MONTHLY_TARGET:.0%} を達成{tag}", f"ここからは守りモード（買う量を半分）で運用します。\n総資産 {yen(summary['equity'])}",
             tags=["tada"], priority=4)
