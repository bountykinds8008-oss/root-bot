export default async function handler(req, res) {
  const symbol = req.query.chain === "eth" ? "ETHUSDT" : "BNBUSDT";
  try {
    const response = await fetch("https://data-api.binance.vision/api/v3/klines?interval=5m&limit=40&symbol=" + symbol, {signal: AbortSignal.timeout(6000)});
    if (!response.ok) throw Error();
    const candles = (await response.json()).map(row => row.slice(0, 5).map(Number));
    if (!candles.length || candles.some(row => row.length !== 5 || !row.every(Number.isFinite))) throw Error();
    res.setHeader("Cache-Control", "s-maxage=5");
    return res.status(200).json({symbol, candles});
  } catch {
    return res.status(502).json({error:"market unavailable"});
  }
}
