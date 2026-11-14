"""パターン研究用：bitbankの1時間足を集める（お金は使わない）。

  python -m lab.fetch part btc_jpy 2021   # 1銘柄・1年分 → lab/data/parts/btc_jpy_2021.csv.gz
  python -m lab.fetch merge               # parts をまとめて lab/data/<pair>.csv.gz
"""
import csv
import gzip
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from bot import bitbank

OUT = Path(__file__).resolve().parent / "data"
PAIRS = ["btc_jpy", "eth_jpy", "xrp_jpy"]


def day(pair, d):
    for attempt in range(4):
        try:
            raw = bitbank._http(urllib.request.Request(
                f"{bitbank.PUBLIC}/{pair}/candlestick/1hour/{d.strftime('%Y%m%d')}"), retries=2)
            return [(int(ts), o, h, l, c, v) for o, h, l, c, v, ts in raw["candlestick"][0]["ohlcv"]]
        except bitbank.BitbankError:
            return []          # その日のデータなし（上場前など）
        except Exception:
            time.sleep(2 * (attempt + 1))
    print(f"[skip] {pair} {d}", file=sys.stderr)
    return []


def part(pair, year):
    (OUT / "parts").mkdir(parents=True, exist_ok=True)
    rows = []
    d, end = date(year, 1, 1), min(date(year, 12, 31), date.today() - timedelta(days=1))
    t0 = time.time()
    while d <= end:
        rows += day(pair, d)
        d += timedelta(days=1)
        time.sleep(0.05)
    with gzip.open(OUT / "parts" / f"{pair}_{year}.csv.gz", "wt", newline="") as f:
        csv.writer(f).writerows(rows)
    print(pair, year, len(rows), "bars", f"{time.time() - t0:.0f}s")


def merge():
    for pair in PAIRS:
        rows = {}
        for p in sorted((OUT / "parts").glob(f"{pair}_*.csv.gz")):
            with gzip.open(p, "rt") as f:
                for r in csv.reader(f):
                    if r:
                        rows[int(r[0])] = r
        with gzip.open(OUT / f"{pair}.csv.gz", "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "open", "high", "low", "close", "volume"])
            for t in sorted(rows):
                w.writerow(rows[t])
        print(pair, len(rows), "bars")


if __name__ == "__main__":
    if sys.argv[1] == "part":
        part(sys.argv[2], int(sys.argv[3]))
    else:
        merge()
