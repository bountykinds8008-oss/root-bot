// Vercel サーバーレス関数：bitbankの公開価格（誰でも見られるデータ）を中継する。
// ?pair=btc_jpy で、そのペアの1分足（直近）も返す。
// ?pair=btc_jpy&tf=1hour で、ボットが判断に使う1時間足（直近6日分）を返す。
const PAIRS = ["btc_jpy", "eth_jpy", "xrp_jpy"];
const PUB = "https://public.bitbank.cc";

const ymd = d => d.toISOString().slice(0, 10).replace(/-/g, "");

async function j(url) {
  const r = await fetch(url, { headers: { "User-Agent": "root-bot-live" } });
  const b = await r.json();
  if (b.success !== 1) throw new Error("bitbank " + (b.data && b.data.code));
  return b.data;
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "s-maxage=2, stale-while-revalidate=5");
  try {
    const tickers = {};
    await Promise.all(PAIRS.map(async p => {
      const t = await j(`${PUB}/${p}/ticker`);
      tickers[p] = { last: +t.last, buy: +t.buy, sell: +t.sell, open: +t.open,
                     high: +t.high, low: +t.low, vol: +t.vol, ts: t.timestamp };
    }));
    let candles = null, ohlc = null;
    const pair = PAIRS.includes(req.query.pair) ? req.query.pair : null;
    if (pair) {
      const hourly = req.query.tf === "1hour";
      const days = hourly ? 6 : 2, now = Date.now(), rows = [];
      for (let k = days - 1; k >= 0; k--) {
        try {
          const c = await j(`${PUB}/${pair}/candlestick/${hourly ? "1hour" : "1min"}/${ymd(new Date(now - k * 864e5))}`);
          rows.push(...c.candlestick[0].ohlcv);
        } catch (_) { /* 日付が変わった直後などは当日分が無い */ }
      }
      const last = rows.slice(hourly ? -144 : -180);
      candles = last.map(r => [r[5], +r[3]]); // [時刻, 終値]
      ohlc = last.map(r => [r[5], +r[0], +r[1], +r[2], +r[3]]); // [時刻, 始, 高, 安, 終]
    }
    res.status(200).json({ tickers, candles, ohlc, pair, tf: req.query.tf === "1hour" ? "1hour" : "1min" });
  } catch (e) {
    res.status(502).json({ error: String(e.message || e) });
  }
}
