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

# ---- 安全フィルター（買いの条件がそろっても、危ない場面は見送る）----
# 「厳しくふるいにかけ、危ないものには一切参加しない」考え方を bitbank 3銘柄向けにしたもの
MAX_SPREAD = 0.003          # 売値と買値の差が0.3%超 → 板が薄いので見送り
SURGE_4H = 0.05             # 直近4時間で+5%超の急騰 → 飛び乗り防止で見送り
MAX_ATR_PCT = 0.025         # 1時間の平均値幅が価格の2.5%超 → 値動きが荒すぎるので見送り
MIN_VOL_JPY_24H = 30_000_000  # 直近24時間の売買代金が3000万円未満 → 出来高不足で見送り
CHASE_LIMIT = 0.006         # 注文直前の価格が判断時より0.6%超高い → 価格が逃げたので見送り（二次確認）
LOSS_STREAK_PAUSE = 2       # 同じ銘柄で2連敗したら…
PAUSE_BARS = 12             # …12時間はその銘柄を休む
