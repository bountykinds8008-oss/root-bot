# root_bot

bitbank（BTC / ETH / XRP）の自動売買ボットとトレーディング画面。

- 目標：月 +5%（達成後は守りモード＝ポジション半分）
- 損失上限：月 −20% で全決済 → 翌月に自動再開
- 標準は **仮想売買**（お金は動かない）。本番は `bot/config.py` の `MODE="live"` と
  GitHub変数 `LIVE_TRADING=yes` の両方がそろったときだけ動く（二重ロック）
- 15分ごとに GitHub Actions で実行。状態は `data/` に保存
- 画面は `dashboard/`（Vercel、合言葉つき）

## 構成
| 場所 | 役割 |
|---|---|
| `bot/engine.py` | 売買判断・リスク管理（本番/仮想/検証で共通） |
| `bot/bitbank.py` | bitbank API |
| `bot/backtest.py` | 過去データ検証（Actions「過去データで検証」から実行） |
| `dashboard/` | トレーディング画面 |

## 注意
- 利益は保証されません。必ず仮想売買 → 少額の順で試すこと
- bitbankのAPIキーは **出金権限なし**（参照＋取引のみ）で作ること

## アプリとして使う（iPhone）
1. Safariで `/desk` を開く
2. 共有ボタン →「ホーム画面に追加」
3. ホーム画面の「ROOT BOT」から全画面で開ける（オフラインでも画面の枠は開く）
