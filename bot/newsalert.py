"""大事なニュースをスマホに通知する（ボットの実行ごと＝約30分ごと）。

ダッシュボードの /api/news（CoinPost・CoinDesk JAPAN などの見出し）を読み、
5大コインに関係して、値動きにつながりやすい言葉が入った見出しだけを ntfy で知らせる。
通知の失敗で売買が止まらないよう、エラーはすべて無視する。
"""
import json
import re
import sys
import urllib.request

from . import config as C, notify

NEWS_URL = "https://root-bot-dashboard.vercel.app/api/news"
# 値動きにつながりやすい言葉
HOT = re.compile(r"急騰|急落|暴落|暴騰|最高値|最安値|高値更新|上場|上場廃止|ETF|承認|否決|ハッキング|流出|不正|破綻|倒産|"
                 r"提訴|訴訟|規制|禁止|SEC|金融庁|FRB|利下げ|利上げ|半減期|大型アップグレード|アップグレード|ハードフォーク|"
                 r"買収|提携|大量購入|追加購入|清算|売却")
# コインに関係なくても知らせる、とても大きな出来事
BIG = re.compile(r"ETF.{0,6}承認|ハッキング|流出|破綻|倒産|取引停止|緊急")
MAX_PER_RUN = 3


def important(n):
    t = n.get("title", "")
    return bool(BIG.search(t)) or (bool(n.get("coins")) and bool(HOT.search(t)))


def run(state, now_ms):
    try:
        req = urllib.request.Request(NEWS_URL, headers={"User-Agent": "root-bot"})
        items = json.loads(urllib.request.urlopen(req, timeout=15).read()).get("items", [])
    except Exception as e:
        print(f"[warn] ニュースを読めませんでした: {e}", file=sys.stderr)
        return []
    if not items:
        return []
    seen = state.get("news_seen_ts")
    sent = state.setdefault("news_sent", [])
    newest = max(n["ts"] for n in items)
    if seen is None:                       # 初回は「今までの分」を既読にするだけ（一気に通知しない）
        state["news_seen_ts"] = newest
        return []
    picks = [n for n in items if n["ts"] > seen and n["link"] not in sent and important(n)]
    picks.sort(key=lambda n: n["ts"])
    out = []
    for n in picks[-MAX_PER_RUN:]:
        coins = " ".join(notify.NAMES.get(c, c) for c in n.get("coins", []))
        notify.send(f"📰 {n['title'][:60]}", f"{n.get('src', '')}{'・' + coins if coins else ''}\nタップで記事を開く",
                    tags=["newspaper"], priority=4, click=n["link"])
        sent.append(n["link"])
        out.append(n)
    state["news_sent"] = sent[-100:]
    state["news_seen_ts"] = max(seen, newest)
    return out
