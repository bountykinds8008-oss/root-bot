"""bitbank API クライアント（標準ライブラリのみ）。

公式仕様: https://github.com/bitbankinc/bitbank-api-docs
- Public:  https://public.bitbank.cc
- Private: https://api.bitbank.cc/v1
- 署名: HMAC-SHA256(secret, ACCESS-NONCE + path[+query]) ※GET
        HMAC-SHA256(secret, ACCESS-NONCE + JSONボディ)   ※POST
"""
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

PUBLIC = "https://public.bitbank.cc"
PRIVATE = "https://api.bitbank.cc"


class BitbankError(Exception):
    def __init__(self, code, msg=""):
        super().__init__(f"bitbank error {code} {msg}")
        self.code = code


def _http(req, retries=3):
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                body = json.loads(r.read().decode())
            if body.get("success") != 1:
                raise BitbankError(body.get("data", {}).get("code"))
            return body["data"]
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


# ---------------- Public ----------------
def ticker(pair):
    d = _http(urllib.request.Request(f"{PUBLIC}/{pair}/ticker"))
    return {"buy": float(d["buy"]), "sell": float(d["sell"]), "last": float(d["last"])}


YEARLY = {"4hour", "8hour", "12hour", "1day", "1week", "1month"}
DUR_MS = {"1min": 60_000, "5min": 300_000, "15min": 900_000, "30min": 1_800_000, "1hour": 3_600_000,
          "4hour": 14_400_000, "8hour": 28_800_000, "12hour": 43_200_000, "1day": 86_400_000}


def candles(pair, candle_type="4hour", days=5):
    """直近のローソク足（確定済みのみ）を古い順で返す。"""
    now = datetime.now(timezone.utc)
    if candle_type in YEARLY:
        keys = [str(now.year - 1), str(now.year)]
    else:
        keys = [(now - timedelta(days=k)).strftime("%Y%m%d") for k in range(days - 1, -1, -1)]
    out = {}
    for key in keys:
        try:
            d = _http(urllib.request.Request(f"{PUBLIC}/{pair}/candlestick/{candle_type}/{key}"))
        except BitbankError:
            continue  # 年明け直後・日付が変わった直後などは無いことがある
        for row in d["candlestick"][0]["ohlcv"]:
            o, h, l, c, v, ts = row
            out[int(ts)] = (float(o), float(h), float(l), float(c), float(v), int(ts))
    bars = [out[t] for t in sorted(out)]
    # 最後の足が形成中なら除外
    if bars and bars[-1][5] + DUR_MS.get(candle_type, 3_600_000) > now.timestamp() * 1000:
        bars = bars[:-1]
    return bars


def candles_year(pair, candle_type, year):
    d = _http(urllib.request.Request(f"{PUBLIC}/{pair}/candlestick/{candle_type}/{year}"))
    return [(float(o), float(h), float(l), float(c), float(v), int(ts))
            for o, h, l, c, v, ts in d["candlestick"][0]["ohlcv"]]


# ---------------- Private ----------------
class Private:
    def __init__(self, key, secret):
        self.key, self.secret = key, secret.encode()

    def _headers(self, message_tail):
        nonce = str(int(time.time() * 1000))
        sig = hmac.new(self.secret, (nonce + message_tail).encode(), hashlib.sha256).hexdigest()
        return {"ACCESS-KEY": self.key, "ACCESS-NONCE": nonce,
                "ACCESS-SIGNATURE": sig, "Content-Type": "application/json"}

    def get(self, path):
        full = "/v1" + path
        req = urllib.request.Request(PRIVATE + full, headers=self._headers(full))
        return _http(req, retries=2)

    def post(self, path, payload):
        body = json.dumps(payload, separators=(",", ":"))
        req = urllib.request.Request(PRIVATE + "/v1" + path, data=body.encode(),
                                     headers=self._headers(body), method="POST")
        return _http(req, retries=1)  # 注文は二重発注を避けるため再試行しない

    def balances(self):
        return {a["asset"]: float(a["free_amount"]) for a in self.get("/user/assets")["assets"]}

    def pairs_info(self):
        d = _http(urllib.request.Request(PRIVATE + "/v1/spot/pairs"))
        return {p["name"]: p for p in d["pairs"]}

    def order(self, pair, side, amount, ref_price):
        payload = {"pair": pair, "side": side, "type": "market", "amount": amount}
        try:
            return self.post("/user/spot/order", payload)
        except BitbankError as e:
            if str(e.code) != "70020":  # 成行が制限されているときだけ指値に切替
                raise
            px = ref_price * (1.003 if side == "buy" else 0.997)
            payload.update(type="limit", price=str(round(px)))
            return self.post("/user/spot/order", payload)
