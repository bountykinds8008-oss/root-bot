"""root_bot の設定。数値を変えたいときはこのファイルだけ直せばよい。"""

# 取引する通貨ペア（bitbank）
PAIRS = ["btc_jpy", "eth_jpy", "xrp_jpy"]

# 運用モード: "paper"（仮想売買・お金は動かない） / "live"（本番）
# live にするには、さらに環境変数 LIVE_TRADING=yes が必要（二重ロック）
MODE = "paper"

# 仮想売買の開始資金（円）
PAPER_START_JPY = 100_000

# ---- 目標とリスク ----
MONTHLY_TARGET = 0.05       # 月+5% に届いたら守りモード（ポジションを半分に）
MONTHLY_MAX_LOSS = 0.20     # 月-20% に達したら全決済して翌月まで停止
TOTAL_STOP = None           # 通算の非常ブレーキ（例: 0.35 で元本の-35%で完全停止）。None で無効
DEFENSIVE_SIZE = 0.5        # 守りモード時のポジション倍率

# ---- 手数料（テイカー＝成行で計算。保守的に見積もる）----
# 出典: bitbank公式 2026-02-02改定 BTC/JPY テイカー0.1%、その他ペアはテイカー0.12%
TAKER_FEE = {"btc_jpy": 0.0010, "eth_jpy": 0.0012, "xrp_jpy": 0.0012}

# ---- 戦略（1時間足のトレンドフォロー）----
CANDLE = "1hour"
EMA_FAST = 20
EMA_SLOW = 50
RSI_LEN = 14
RSI_MIN = 50
RSI_MAX = 70
ATR_LEN = 14
STOP_ATR = 2.0        # 初期損切り = 買値 - 2.0×ATR
TRAIL_ATR = 2.5       # 追従損切り = 買ってからの最高値 - 2.5×ATR
COOLDOWN_BARS = 3     # 決済後、同じペアに再エントリーしない本数

# 1ペアあたりの配分（資産 ÷ ペア数 × この値）
ALLOC_PER_PAIR = 1.0 / len(PAIRS)

# 画面用に保存する資産推移の最大点数
MAX_EQUITY_POINTS = 3000
MAX_TRADES_KEPT = 300
