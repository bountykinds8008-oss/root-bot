// Vercel サーバーレス関数：bitbankの公開価格（誰でも見られるデータ）を中継する。
// ?pair=btc_jpy で、そのペアの1分足（直近）も返す。
// ?pair=btc_jpy&tf=4hour で、ボットが判断に使う4時間足（直近200本）を返す。
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
      const tf = ["1hour", "4hour"].includes(req.query.tf) ? req.query.tf : "1min";
      const now = Date.now(), rows = [];
      const keys = tf === "4hour"
        ? [String(new Date(now).getUTCFullYear() - 1), String(new Date(now).getUTCFullYear())]
        : Array.from({ length: tf === "1hour" ? 6 : 2 }, (_, i) => ymd(new Date(now - (tf === "1hour" ? 5 - i : 1 - i) * 864e5)));
      for (const k of keys) {
        try {
          const c = await j(`${PUB}/${pair}/candlestick/${tf}/${k}`);
          rows.push(...c.candlestick[0].ohlcv);
        } catch (_) { /* 年明け直後・日付が変わった直後などは無いことがある */ }
      }
      const last = rows.slice(tf === "1min" ? -180 : -200);
      candles = last.map(r => [r[5], +r[3]]); // [時刻, 終値]
      ohlc = last.map(r => [r[5], +r[0], +r[1], +r[2], +r[3]]); // [時刻, 始, 高, 安, 終]
    }
    res.status(200).json({ tickers, candles, ohlc, pair, tf: ["1hour", "4hour"].includes(req.query.tf) ? req.query.tf : "1min" });
  } catch (e) {
    res.status(502).json({ error: String(e.message || e) });
  }
}
