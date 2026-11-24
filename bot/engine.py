"""売買判断とリスク管理の中心。本番・仮想売買・過去検証で同じコードを使う。"""
from datetime import datetime, timedelta, timezone

from . import config as C
from .indicators import atr

JST = timezone(timedelta(hours=9))


def month_key(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, JST).strftime("%Y-%m")


def new_state(start_equity, mode):
    return {
        "mode": mode, "cash": float(start_equity), "positions": {}, "cooldown_until": {},
        "last_bar_ts": {}, "month": None, "month_start_equity": None, "halted_month": None,
        "total_start_equity": float(start_equity), "total_halted": False,
        "equity_history": [], "trades": [], "monthly": {}, "log": [],
    }


# ---------------- シグナル（v2：4時間足ブレイクアウト） ----------------
def signal(bars):
    """確定足のリストから売買判断を返す（research.py の検証と同じルール）。"""
    if len(bars) < C.DON_IN + 2:
        return None
    h = [b[1] for b in bars]; l = [b[2] for b in bars]; c = [b[3] for b in bars]
    a = atr(bars, C.ATR_LEN)
    hi = max(h[-C.DON_IN - 1:-1])        # 直前までの最高値（今の足は含めない）
    lo = max(0.0, min(l[-C.DON_OUT - 1:-1]))
    rets = [c[k] / c[k - 1] - 1 for k in range(len(c) - 30, len(c))]
    m = sum(rets) / len(rets)
    vol = (sum((r - m) ** 2 for r in rets) / len(rets)) ** 0.5 * (365 * C.BARS_PER_DAY) ** 0.5
    frac = max(0.1, min(1.0, C.VOL_TARGET / vol)) if vol > 0 else 1.0
    return {"entry": c[-1] > hi, "exit_trend": c[-1] < lo, "atr": a[-1], "close": c[-1],
            "break_level": hi, "exit_level": lo, "size": frac, "vol": vol}


# ---------------- 安全フィルター ----------------
FILTER_NAMES = {
    "spread": "板が薄い（スプレッド広い）",
    "surge": "急騰しすぎ（飛び乗り防止）",
    "volatile": "値動きが荒すぎ",
    "volume": "出来高不足",
    "chase": "直前に価格が逃げた",
    "pause": "連敗中の休憩",
    "funds": "資金不足",
}


def risk_filter(bars, sig, m, pair_stats):
    """買い候補を危険度でふるいにかける。見送る理由のキー（無ければ None）を返す。"""
    c = sig["close"]
    if C.USE_LOSS_PAUSE and pair_stats.get("loss_streak", 0) >= C.LOSS_STREAK_PAUSE and \
            bars[-1][5] < pair_stats.get("pause_until", 0):
        return "pause"
    mid = (m["bid"] + m["ask"]) / 2
    if mid and (m["ask"] - m["bid"]) / mid > C.MAX_SPREAD:
        return "spread"
    if C.USE_SURGE_FILTER and len(bars) > 4 and c / bars[-5][3] - 1 > C.SURGE_4H:
        return "surge"
    if C.USE_VOLATILE_FILTER and sig["atr"] / c > C.MAX_ATR_PCT:
        return "volatile"
    if sum(b[4] * b[3] for b in bars[-C.BARS_PER_DAY:]) < C.MIN_VOL_JPY_24H:
        return "volume"
    if m["ask"] > c * (1 + C.CHASE_LIMIT):   # 二次確認：注文直前の価格
        return "chase"
    return None


def _funnel(state, mk):
    return state.setdefault("funnel", {}).setdefault(
        mk, {"checked": 0, "no_setup": 0, "filtered": {}, "entered": 0})


# ---------------- 1回分の処理 ----------------
def step(state, broker, market, now_ms):
    """market[pair] = {"bars": 確定足, "bid": 売れる価格, "ask": 買える価格, "low": 前回からの安値}

    GitHubの定期実行は遅れ・間引きが多く、4時間足を何本も飛ばすことがある。
    そこで「前回処理した足の次から最新の確定足まで」を古い順に全部たどり、
    損切り・追従損切りの引き上げ・手じまいを見逃さないようにしている（取りこぼし防止）。
    """
    log = []
    equity = broker.equity(state, market)
    mk = month_key(now_ms)

    # 月替わり：基準資産を更新し、停止を解除
    if state["month"] != mk:
        if state["month"]:
            state["monthly"].setdefault(state["month"], {})["end"] = equity
        state["month"], state["month_start_equity"] = mk, equity
        state["monthly"].setdefault(mk, {})["start"] = equity
        if state["halted_month"] and state["halted_month"] != mk:
            state["halted_month"] = None
            log.append("新しい月に入ったので売買を再開")

    base = state["month_start_equity"]
    month_ret = equity / base - 1 if base else 0.0
    total_ret = equity / state["total_start_equity"] - 1

    # ---- 非常ブレーキ ----
    if C.TOTAL_STOP is not None and total_ret <= -C.TOTAL_STOP and not state["total_halted"]:
        state["total_halted"] = True
        log.append(f"通算 {total_ret:.1%} で非常ブレーキ作動。全決済して完全停止")
        _close_all(state, broker, market, now_ms, "非常ブレーキ")
    if month_ret <= -C.MONTHLY_MAX_LOSS and state["halted_month"] != mk:
        state["halted_month"] = mk
        log.append(f"今月 {month_ret:.1%} で月間損失上限に到達。全決済して今月は停止")
        _close_all(state, broker, market, now_ms, "月間損失上限")

    halted = state["total_halted"] or state["halted_month"] == mk
    defensive = month_ret >= C.MONTHLY_TARGET
    size_mult = C.DEFENSIVE_SIZE if defensive else 1.0

    for pair in C.PAIRS:
        m = market.get(pair)
        if not m or not m["bars"]:
            continue
        bars = m["bars"]
        last_done = state["last_bar_ts"].get(pair)
        if last_done is None:
            todo = [len(bars) - 1]                       # 初回は最新の足だけ
        else:
            todo = [i for i, b in enumerate(bars) if b[5] > last_done]
        state["last_bar_ts"][pair] = bars[-1][5]
        if len(todo) > 1:
            state["catchup_bars"] = state.get("catchup_bars", 0) + len(todo) - 1

        # ① 持っている銘柄：見逃した足も含めて、古い順に損切り・追従・手じまいを確認
        for i in todo:
            pos = state["positions"].get(pair)
            if not pos:
                break
            b = bars[i]
            hit = broker.stop_hit(pair, pos, bar=b)
            if hit:
                _sell(state, broker, pair, market, now_ms, "損切り", hit=hit)
                break
            sig = signal(bars[:i + 1])
            if not sig:
                continue
            pos["high"] = max(pos["high"], sig["close"])
            new_stop = pos["high"] - C.TRAIL_ATR * sig["atr"]
            if new_stop > pos["stop"]:
                pos["stop"] = new_stop
                pos["stop_dirty"] = True
            if sig["exit_trend"]:
                late = "" if i == len(bars) - 1 else "（実行の遅れを後追い）"
                _sell(state, broker, pair, market, now_ms, "トレンド終了" + late)
                break

        pos = state["positions"].get(pair)
        if pos:
            # 形成中の足：前回からの安値（ティッカー）でも確認
            hit = broker.stop_hit(pair, pos, low=m["low"], bid=m["bid"])
            if hit:
                _sell(state, broker, pair, market, now_ms, "損切り", hit=hit)
            elif pos.pop("stop_dirty", False):
                broker.move_stop(state, pair, pos)      # 本番：取引所の逆指値を引き上げ
            continue

        # ①' 指値の買い注文を出している銘柄：約定したか・待ちすぎたかを確認
        order = state.setdefault("orders", {}).get(pair)
        if order:
            _check_order(state, broker, pair, order, bars, todo, m, now_ms, halted, log)
            continue

        # ② 持っていない銘柄：最新の確定足で買い判断（＋見逃した足で出た買いの後追い）
        if not todo or halted or state["cooldown_until"].get(pair, 0) >= bars[-1][5]:
            continue
        sig = signal(bars)
        if not sig:
            continue
        fn = _funnel(state, mk)
        fn["checked"] += 1
        plan = None
        if sig["entry"]:
            plan = {"stop": None, "high": sig["close"], "label": "トレンド買い"}
        elif len(todo) > 1:
            plan = _late_entry(bars, todo[:-1], m["ask"])
        if not plan:
            fn["no_setup"] += 1
            continue
        reason = risk_filter(bars, sig, m, state.get("bot_stats", {}).get(pair, {}))
        if reason:
            fn["filtered"][reason] = fn["filtered"].get(reason, 0) + 1
            state.setdefault("last_reject", {})[pair] = {"ts": now_ms, "reason": FILTER_NAMES[reason]}
            log.append(f"{pair} 買い条件はそろったが見送り：{FILTER_NAMES[reason]}")
            continue
        budget = equity * C.ALLOC_PER_PAIR * size_mult * sig["size"]
        budget = min(budget, broker.cash(state) * 0.995)
        if budget < broker.min_order_jpy(pair):
            fn["filtered"]["funds"] = fn["filtered"].get("funds", 0) + 1
            continue
        label = ("守りモード" if defensive else "") + plan["label"]
        if C.MAKER_ENTRY and plan["stop"] is None:
            # 指値：今の値段（確定足の終値と、いま買える気配の安いほう）で待つ
            limit = min(sig["close"], m["bid"])
            o = broker.place_limit_buy(state, pair, budget, limit)
            if o:
                state["orders"][pair] = {**o, "limit": limit, "placed_at": now_ms, "budget": budget,
                                         "deadline": now_ms + C.MAKER_WAIT_BARS * C.BAR_MS,
                                         "atr": sig["atr"], "label": label}
                fn["entered"] += 1
                log.append(f"{pair} 指値で買い注文 {limit:,.3f}（{C.MAKER_WAIT_BARS * 4}時間まで待つ）")
                continue
        amt, px, fee = broker.buy(state, pair, budget, m["ask"])
        if amt <= 0:
            continue
        stop = px - C.STOP_ATR * sig["atr"]
        if plan["stop"] is not None:                    # 後追い：その時の損切り位置を引き継ぐ
            stop = min(plan["stop"], px - sig["atr"])   # ただし買値より最低ATR1本ぶん下
        pos = {"amount": amt, "entry": px, "high": max(px, plan["high"]),
               "opened_at": now_ms, "stop": stop}
        state["positions"][pair] = pos
        _trade(state, now_ms, pair, "buy", amt, px, fee, None, label)
        fn["entered"] += 1
        broker.place_stop(state, pair, pos)             # 本番：取引所に逆指値の損切りを置く

    equity = broker.equity(state, market)
    state["equity_history"].append([now_ms, round(equity, 2)])
    state["equity_history"] = state["equity_history"][-C.MAX_EQUITY_POINTS:]
    state["monthly"][mk]["end"] = equity
    state["log"] = (state["log"] + [[now_ms, x] for x in log])[-100:]
    return {"equity": equity, "month_ret": equity / base - 1 if base else 0,
            "halted": halted, "defensive": defensive}


def _check_order(state, broker, pair, order, bars, todo, m, now_ms, halted, log):
    """指値の買い注文：約定していれば保有に、待ちすぎ・停止中なら取り消す（待ちすぎは成行で買う）。"""
    fill = broker.limit_filled(state, pair, order, bars=[bars[i] for i in todo], low=m["low"])
    if not fill and (halted or now_ms >= order["deadline"]):
        fill = broker.cancel_limit(state, pair, order)            # 一部だけ約定していればその分
        if not fill and not halted:
            budget = min(order["budget"], broker.cash(state) * 0.995)
            if budget >= broker.min_order_jpy(pair):
                amt, px, fee = broker.buy(state, pair, budget, m["ask"])
                if amt > 0:
                    fill = {"amount": amt, "price": px, "fee": fee, "late": True}
        if not fill:
            state["orders"].pop(pair, None)
            log.append(f"{pair} 指値の買い注文を取り消し" + ("（停止中）" if halted else ""))
            return
    if not fill:
        return
    state["orders"].pop(pair, None)
    px = fill["price"]
    pos = {"amount": fill["amount"], "entry": px, "high": px, "opened_at": now_ms,
           "stop": px - C.STOP_ATR * order["atr"]}
    state["positions"][pair] = pos
    _trade(state, now_ms, pair, "buy", fill["amount"], px, fill["fee"], None,
           order["label"] + ("（指値→成行）" if fill.get("late") else "（指値）"))
    broker.place_stop(state, pair, pos)


def _late_entry(bars, missed, ask):
    """見逃した足で買いサインが出ていたか。出ていて、今も生きている（損切り・手じまいに
    なっていない）なら、その時の損切り位置を引き継いで今買う。値段が逃げすぎていたら買わない。"""
    first = None
    for i in missed:
        s = signal(bars[:i + 1])
        if s and s["entry"]:
            first = i
            break
    if first is None:
        return None
    s = signal(bars[:first + 1])
    entry, high, stop = s["close"], s["close"], s["close"] - C.STOP_ATR * s["atr"]
    for i in range(first + 1, len(bars)):
        b = bars[i]
        if b[2] <= stop:
            return None
        s = signal(bars[:i + 1])
        high = max(high, s["close"])
        stop = max(stop, high - C.TRAIL_ATR * s["atr"])
        if s["exit_trend"]:
            return None
    if ask > entry + C.LATE_ENTRY_MAX_ATR * s["atr"] or ask <= stop:
        return None
    return {"stop": stop, "high": high, "label": "トレンド買い（後追い）"}


def _sell(state, broker, pair, market, now_ms, reason, hit=None):
    pos = state["positions"].pop(pair)
    amt, px, fee = broker.sell(state, pair, pos, market[pair]["bid"], hit=hit)
    pnl = (px - pos["entry"]) * amt - fee
    _trade(state, now_ms, pair, "sell", amt, px, fee, pnl, reason)
    # キャラ育成用の成績（画面のレベル表示に使う）
    st = state.setdefault("bot_stats", {}).setdefault(
        pair, {"trades": 0, "wins": 0, "streak": 0, "best_streak": 0, "pnl": 0.0})
    st["trades"] += 1
    st["pnl"] = round(st["pnl"] + pnl, 2)
    if pnl > 0:
        st["wins"] += 1
        st["streak"] += 1
        st["best_streak"] = max(st["best_streak"], st["streak"])
        st["loss_streak"] = 0
    else:
        st["streak"] = 0
        st["loss_streak"] = st.get("loss_streak", 0) + 1
        if st["loss_streak"] >= C.LOSS_STREAK_PAUSE:
            st["pause_until"] = market[pair]["bars"][-1][5] + C.PAUSE_BARS * C.BAR_MS
    bars = market[pair]["bars"]
    state["cooldown_until"][pair] = bars[-1][5] + C.COOLDOWN_BARS * C.BAR_MS


def _close_all(state, broker, market, now_ms, reason):
    for pair in list(state["positions"]):
        if pair in market:
            _sell(state, broker, pair, market, now_ms, reason)


def _trade(state, ts, pair, side, amt, px, fee, pnl, reason):
    state["trades"].append({"ts": ts, "pair": pair, "side": side, "amount": amt,
                            "price": px, "fee": round(fee, 2),
                            "pnl": None if pnl is None else round(pnl, 2), "reason": reason})
    state["trades"] = state["trades"][-C.MAX_TRADES_KEPT:]
