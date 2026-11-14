"""パターン研究用：bitbankの1時間足を全部集めて lab/data/<pair>.csv.gz に保存する（お金は使わない）。

  python -m lab.fetch 2018

既に保存済みの日はとばす（途中から再開できる）。
"""
import csv
import gzip
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from bot import bitbank

OUT = Path(__file__).resolve().parent / "data"
PAIRS = ["btc_jpy", "eth_jpy", "xrp_jpy"]


def day(pair, d):
    for attempt in range(4):
        try:
            raw = bitbank._http(urllib.request.Request(
                f"{bitbank.PUBLIC}/{pair}/candlestick/1hour/{d.strftime('%Y%m%d')}"))
            return [(int(ts), o, h, l, c, v) for o, h, l, c, v, ts in raw["candlestick"][0]["ohlcv"]]
        except bitbank.BitbankError:
            return []          # その日のデータなし（上場前など）
        except Exception:
            time.sleep(2 * (attempt + 1))
    print(f"[skip] {pair} {d}", file=sys.stderr)
    return []


def main():
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 2018
    OUT.mkdir(parents=True, exist_ok=True)
    end = date.today() - timedelta(days=1)
    for pair in PAIRS:
        path = OUT / f"{pair}.csv.gz"
        rows = {}
        if path.exists():
            with gzip.open(path, "rt") as f:
                for r in csv.reader(f):
                    if r and r[0] != "ts":
                        rows[int(r[0])] = r
        have = {date.fromtimestamp(t / 1000) for t in rows}
        days, d = [], date(y0, 1, 1)
        while d <= end:
            if d not in have or d >= end - timedelta(days=2):
                days.append(d)
            d += timedelta(days=1)
        with ThreadPoolExecutor(max_workers=4) as ex:
            for res in ex.map(lambda x: day(pair, x), days):
                for r in res:
                    rows[r[0]] = list(r)
        with gzip.open(path, "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "open", "high", "low", "close", "volume"])
            for t in sorted(rows):
                w.writerow(rows[t])
        print(pair, len(rows), "bars")


if __name__ == "__main__":
    main()
