import { createHmac } from "node:crypto";

async function json(url, options = {}) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(6500) });
  if (!response.ok) throw Error();
  return response.json();
}

async function exchange() {
  const key = process.env.PORTFOLIO_BITBANK_KEY;
  const secret = process.env.PORTFOLIO_BITBANK_SECRET;
  if (!key || !secret) return { state: "未設定" };
  const path = "/v1/user/assets";
  const nonce = String(Date.now());
  const signature = createHmac("sha256", secret).update(nonce + path).digest("hex");
  const result = await json("https://api.bitbank.cc" + path, { headers: {
    "ACCESS-KEY": key, "ACCESS-NONCE": nonce, "ACCESS-SIGNATURE": signature
  } });
  if (result.success !== 1) throw Error();
  const rows = result.data.assets.map(a => ({ symbol: a.asset.toUpperCase(), qty: Number(a.onhand_amount) }));
  if (rows.some(r => !Number.isFinite(r.qty) || r.qty < 0)) throw Error();
  return { rows: rows.filter(r => r.qty > 0), time: Date.now() };
}

async function wallet(address, bsc) {
  if (!address) return { state: "未設定" };
  if (!/^0x[0-9a-f]{40}$/i.test(address)) return { state: "アドレス設定エラー" };
  const rpc = await json(bsc ? "https://bsc-rpc.publicnode.com" : "https://ethereum-rpc.publicnode.com", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "eth_getBalance", params: [address, "latest"] })
  });
  if (!/^0x[0-9a-f]+$/i.test(rpc.result)) throw Error();
  return { rows: [{ symbol: bsc ? "BNB" : "ETH", qty: Number(BigInt(rpc.result)) / 1e18 }], time: Date.now() };
}

async function prices() {
  const data = await json("https://public.bitbank.cc/tickers_jpy");
  if (data.success !== 1) throw Error();
  return { rates: Object.fromEntries(data.data.filter(p => p.pair.endsWith("_jpy") && Number(p.last) > 0)
    .map(p => [p.pair.slice(0,-4).toUpperCase(), Number(p.last)])), time: Date.now() };
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "private, no-store");
  const owner = Boolean(process.env.DASH_PASSWORD && req.headers["x-dash-pass"] === process.env.DASH_PASSWORD);
  if (req.method !== "GET") return res.status(405).end();
  const bsc = req.query.chain === "bsc";
  const [bank, eth, quotes, bnbPrice] = await Promise.all([owner ? exchange : async () => ({state: "未接続"}),
    () => wallet(req.query.address || (owner && process.env.PORTFOLIO_ETH_ADDRESS), bsc), prices,
    async () => {
      if (!bsc) return null;
      const result = await json("https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=jpy");
      return Number(result.binancecoin?.jpy);
    }].map(fn =>
    fn().catch(() => ({ state: "取得失敗" }))));
  if (Number.isFinite(bnbPrice) && bnbPrice > 0) {
    quotes.rates ||= {};
    quotes.time ||= Date.now();
    quotes.rates.BNB = bnbPrice;
  }
  return res.status(200).json({ bank, eth, quotes });
}
