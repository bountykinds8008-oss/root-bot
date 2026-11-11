"""売買判断とリスク管理の中心。本番・仮想売買・過去検証で同じコードを使う。"""
from datetime import datetime, timedelta, timezone

from . import config as C
from .indicators import atr, ema, rsi

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


# ---------------- シグナル ----------------
def signal(bars):
    """確定足のリストから {entry: bool, exit_trend: bool, atr: float} を返す。"""
    if len(bars) < C.EMA_SLOW + 6:
        return None
    closes = [b[3] for b in bars]
    ef, es = ema(closes, C.EMA_FAST), ema(closes, C.EMA_SLOW)
    r, a = rsi(closes, C.RSI_LEN), atr(bars, C.ATR_LEN)
    c = closes[-1]
    entry = (ef[-1] > es[-1] and c > ef[-1] and es[-1] > es[-6]
             and C.RSI_MIN <= r[-1] <= C.RSI_MAX)
    exit_trend = ef[-1] < es[-1]
    return {"entry": entry, "exit_trend": exit_trend, "atr": a[-1], "close": c}


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
    if pair_stats.get("loss_streak", 0) >= C.LOSS_STREAK_PAUSE and \
            bars[-1][5] < pair_stats.get("pause_until", 0):
        return "pause"
    mid = (m["bid"] + m["ask"]) / 2
    if mid and (m["ask"] - m["bid"]) / mid > C.MAX_SPREAD:
        return "spread"
    if len(bars) > 4 and c / bars[-5][3] - 1 > C.SURGE_4H:
        return "surge"
    if sig["atr"] / c > C.MAX_ATR_PCT:
        return "volatile"
    if sum(b[4] * b[3] for b in bars[-24:]) < C.MIN_VOL_JPY_24H:
        return "volume"
    if m["ask"] > c * (1 + C.CHASE_LIMIT):   # 二次確認：注文直前の価格
        return "chase"
    return None


def _funnel(state, mk):
    return state.setdefault("funnel", {}).setdefault(
        mk, {"checked": 0, "no_setup": 0, "filtered": {}, "entered": 0})


# ---------------- 1回分の処理 ----------------
def step(state, broker, market, now_ms):
    """market[pair] = {"bars": 確定足, "bid": 売れる価格, "ask": 買える価格, "low": 前回からの安値}"""
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
        pos = state["positions"].get(pair)

        # 損切り（毎回チェック）
        if pos:
            if m["low"] <= pos["stop"]:
                _sell(state, broker, pair, market, now_ms, "損切り", fill=min(pos["stop"], m["bid"]))
                continue

        # 足が確定したときだけ判断
        last_ts = m["bars"][-1][5]
        if state["last_bar_ts"].get(pair) == last_ts:
            continue
        state["last_bar_ts"][pair] = last_ts
        sig = signal(m["bars"])
        if not sig:
            continue

        if pos:
            pos["high"] = max(pos["high"], sig["close"])
            pos["stop"] = max(pos["stop"], pos["high"] - C.TRAIL_ATR * sig["atr"])
            if sig["exit_trend"]:
                _sell(state, broker, pair, market, now_ms, "トレンド終了")
        elif not halted and state["cooldown_until"].get(pair, 0) < last_ts:
            fn = _funnel(state, mk)
            fn["checked"] += 1
            if not sig["entry"]:
                fn["no_setup"] += 1
                continue
            reason = risk_filter(m["bars"], sig, m, state.get("bot_stats", {}).get(pair, {}))
            if reason:
                fn["filtered"][reason] = fn["filtered"].get(reason, 0) + 1
                state.setdefault("last_reject", {})[pair] = {"ts": now_ms, "reason": FILTER_NAMES[reason]}
                log.append(f"{pair} 買い条件はそろったが見送り：{FILTER_NAMES[reason]}")
                continue
            budget = equity * C.ALLOC_PER_PAIR * size_mult
            budget = min(budget, broker.cash(state) * 0.995)
            if budget < broker.min_order_jpy(pair):
                fn["filtered"]["funds"] = fn["filtered"].get("funds", 0) + 1
            else:
                amt, px, fee = broker.buy(state, pair, budget, m["ask"])
                if amt > 0:
                    state["positions"][pair] = {
                        "amount": amt, "entry": px, "high": px, "opened_at": now_ms,
                        "stop": px - C.STOP_ATR * sig["atr"]}
                    _trade(state, now_ms, pair, "buy", amt, px, fee, None,
                           "守りモード買い" if defensive else "トレンド買い")
                    fn["entered"] += 1

    equity = broker.equity(state, market)
    state["equity_history"].append([now_ms, round(equity, 2)])
    state["equity_history"] = state["equity_history"][-C.MAX_EQUITY_POINTS:]
    state["monthly"][mk]["end"] = equity
    state["log"] = (state["log"] + [[now_ms, x] for x in log])[-100:]
    return {"equity": equity, "month_ret": equity / base - 1 if base else 0,
            "halted": halted, "defensive": defensive}


def _sell(state, broker, pair, market, now_ms, reason, fill=None):
    pos = state["positions"].pop(pair)
    px_ref = fill if fill is not None else market[pair]["bid"]
    amt, px, fee = broker.sell(state, pair, pos["amount"], px_ref)
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
            st["pause_until"] = market[pair]["bars"][-1][5] + C.PAUSE_BARS * 3600_000
    bars = market[pair]["bars"]
    state["cooldown_until"][pair] = bars[-1][5] + C.COOLDOWN_BARS * 3600_000


def _close_all(state, broker, market, now_ms, reason):
    for pair in list(state["positions"]):
        if pair in market:
            _sell(state, broker, pair, market, now_ms, reason)


def _trade(state, ts, pair, side, amt, px, fee, pnl, reason):
    state["trades"].append({"ts": ts, "pair": pair, "side": side, "amount": amt,
                            "price": px, "fee": round(fee, 2),
                            "pnl": None if pnl is None else round(pnl, 2), "reason": reason})
    state["trades"] = state["trades"][-C.MAX_TRADES_KEPT:]
