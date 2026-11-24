// 資産一覧のデータを集める Vercel サーバーレス関数。
//
// POST /api/portfolio   本文 {conns:[...]}（画面で選んだ「つなぐ先」）
//   ウォレット : {id, kind:"wallet", address:"0x…", chains:["eth","bsc","polygon","arbitrum","base"]}
//   取引所     : {id, kind:"exchange", ex:"bitbank"|"bitflyer"|"coincheck"|"gmo", key, secret}
//               {id, kind:"exchange", ex:"bitbank", server:true}  … Vercelに保存した鍵を使う（合言葉が必要）
// 返り値 {results:{[id]:{rows:[{symbol,qty,where}],time} | {state}}, quotes:{rates:{SYMBOL:円},time}}
//
// 取引所のAPIキーはこの関数の中で署名に使うだけで、どこにも保存・記録しない。
// 必ず「残高を見る（参照）」だけの権限のキーを使うこと（取引・出金の権限は付けない）。
//
// GET は以前の画面（古いキャッシュ）向けに残している。
import { createHmac } from "node:crypto";

const hmac = (secret, text) => createHmac("sha256", secret).update(text).digest("hex");
const TIMEOUT = 7000;

async function fetchJson(url, options = {}) {
  const r = await fetch(url, { ...options, signal: AbortSignal.timeout(TIMEOUT) });
  const text = await r.text();
  let body;
  try { body = JSON.parse(text); } catch { throw new Error(`応答が読めません（${r.status}）`); }
  if (!r.ok) {
    if (r.status === 401 || r.status === 403) throw new Error("キーが正しくないか、残高を見る権限がありません");
    throw new Error(`取引所の応答エラー（${r.status}）`);
  }
  return body;
}
const num = v => { const n = Number(v); return Number.isFinite(n) ? n : 0; };

// ---------------- 取引所 ----------------
// それぞれ公式ドキュメントの署名方式どおり（2026-11 に確認）
const EXCHANGES = {
  // bitbank: HMAC(secret, nonce + path)
  async bitbank(key, secret) {
    const path = "/v1/user/assets", nonce = String(Date.now());
    const d = await fetchJson("https://api.bitbank.cc" + path, { headers: {
      "ACCESS-KEY": key, "ACCESS-NONCE": nonce, "ACCESS-SIGNATURE": hmac(secret, nonce + path) } });
    if (d.success !== 1) throw new Error(d.data?.code === 20001 ? "キーが正しくありません" : `bitbankエラー ${d.data?.code ?? ""}`);
    return d.data.assets.map(a => ({ symbol: a.asset.toUpperCase(), qty: num(a.onhand_amount) }));
  },
  // bitFlyer: HMAC(secret, timestamp + method + path + body)
  async bitflyer(key, secret) {
    const path = "/v1/me/getbalance", ts = String(Date.now());
    const d = await fetchJson("https://api.bitflyer.com" + path, { headers: {
      "ACCESS-KEY": key, "ACCESS-TIMESTAMP": ts, "ACCESS-SIGN": hmac(secret, ts + "GET" + path) } });
    if (!Array.isArray(d)) throw new Error(d.error_message || "bitFlyerエラー");
    return d.map(a => ({ symbol: String(a.currency_code).toUpperCase(), qty: num(a.amount) }));
  },
  // Coincheck: HMAC(secret, nonce + URL + body)
  async coincheck(key, secret) {
    const url = "https://coincheck.com/api/accounts/balance", nonce = String(Date.now());
    const d = await fetchJson(url, { headers: {
      "ACCESS-KEY": key, "ACCESS-NONCE": nonce, "ACCESS-SIGNATURE": hmac(secret, nonce + url) } });
    if (!d.success) throw new Error(d.error || "Coincheckエラー");
    // 通貨ごとに「残高・注文中・貸暗号資産・積立」を足し、借入を引く
    const plus = ["", "_reserved", "_lending", "_lend_in_use", "_lent", "_tsumitate"];
    const coins = new Set(Object.keys(d).filter(k => k !== "success" && !k.includes("_")));
    return [...coins].map(c => ({
      symbol: c.toUpperCase(),
      qty: plus.reduce((s, suf) => s + num(d[c + suf]), 0) - num(d[c + "_debt"]),
    }));
  },
  // GMOコイン: HMAC(secret, timestamp + method + path)、署名の path は /private を含まない
  async gmo(key, secret) {
    const path = "/v1/account/assets", ts = String(Date.now());
    const d = await fetchJson("https://api.coin.z.com/private" + path, { headers: {
      "API-KEY": key, "API-TIMESTAMP": ts, "API-SIGN": hmac(secret, ts + "GET" + path) } });
    if (d.status !== 0) throw new Error(d.messages?.[0]?.message_string || "GMOコインエラー");
    return d.data.map(a => ({ symbol: a.symbol.toUpperCase(), qty: num(a.amount), rate: num(a.conversionRate) }));
  },
};

// ---------------- ウォレット（EVM：MetaMaskなど共通） ----------------
const CHAINS = {
  eth:      { name: "Ethereum",  rpc: "https://ethereum-rpc.publicnode.com",      native: "ETH",
              tokens: { USDT: "0xdAC17F958D2ee523a2206206994597C13D831ec7", USDC: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48" } },
  bsc:      { name: "BNB Chain", rpc: "https://bsc-rpc.publicnode.com",           native: "BNB",
              tokens: { USDT: "0x55d398326f99059fF775485246999027B3197955", USDC: "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d" } },
  polygon:  { name: "Polygon",   rpc: "https://polygon-bor-rpc.publicnode.com",   native: "POL",
              tokens: { USDT: "0xc2132D05D31c914a87C6611C10748AEb04B58e8F", USDC: "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359" } },
  arbitrum: { name: "Arbitrum",  rpc: "https://arbitrum-one-rpc.publicnode.com",  native: "ETH",
              tokens: { USDT: "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", USDC: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831" } },
  base:     { name: "Base",      rpc: "https://base-rpc.publicnode.com",          native: "ETH",
              tokens: { USDC: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913" } },
};

async function rpc(url, calls) {
  const body = calls.map((c, i) => ({ jsonrpc: "2.0", id: i, ...c }));
  const res = await fetchJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const out = [];
  for (const r of Array.isArray(res) ? res : [res]) out[r.id] = r.result;
  return out;
}

async function chainBalances(address, key) {
  const c = CHAINS[key];
  const pad = address.toLowerCase().replace("0x", "").padStart(64, "0");
  const toks = Object.entries(c.tokens);
  const calls = [{ method: "eth_getBalance", params: [address, "latest"] }];
  for (const [, t] of toks) {
    calls.push({ method: "eth_call", params: [{ to: t, data: "0x70a08231" + pad }, "latest"] }); // balanceOf
    calls.push({ method: "eth_call", params: [{ to: t, data: "0x313ce567" }, "latest"] });       // decimals（桁数もチェーンから読む）
  }
  const r = await rpc(c.rpc, calls);
  if (!/^0x[0-9a-f]*$/i.test(r[0] || "")) throw new Error(`${c.name} の残高を読めません`);
  const rows = [{ symbol: c.native, qty: Number(BigInt(r[0])) / 1e18, where: c.name }];
  toks.forEach(([sym], i) => {
    const bal = r[1 + i * 2], dec = r[2 + i * 2];
    if (!/^0x[0-9a-f]+$/i.test(bal || "") || !/^0x[0-9a-f]+$/i.test(dec || "")) return;
    const v = Number(BigInt(bal)) / 10 ** Number(BigInt(dec));
    if (v > 0) rows.push({ symbol: sym, qty: v, where: c.name });
  });
  return rows;
}

async function wallet(conn) {
  const address = String(conn.address || "").trim();
  if (!/^0x[0-9a-fA-F]{40}$/.test(address)) throw new Error("アドレスの形が正しくありません（0xから始まる42文字）");
  const keys = (conn.chains || []).filter(k => CHAINS[k]);
  if (!keys.length) throw new Error("ネットワークを1つ以上選んでください");
  const parts = await Promise.allSettled(keys.map(k => chainBalances(address, k)));
  const rows = [], failed = [];
  parts.forEach((p, i) => p.status === "fulfilled" ? rows.push(...p.value) : failed.push(CHAINS[keys[i]].name));
  if (!rows.length) throw new Error("どのネットワークからも残高を読めませんでした");
  return { rows: rows.filter(r => r.qty > 0), note: failed.length ? `${failed.join("・")} は読めませんでした` : undefined };
}

// ---------------- 円の価格 ----------------
async function quotes(extra) {
  const rates = {};
  const jobs = [
    fetchJson("https://public.bitbank.cc/tickers_jpy").then(d => {
      for (const p of d.data || []) if (p.pair.endsWith("_jpy") && Number(p.last) > 0)
        rates[p.pair.slice(0, -4).toUpperCase()] = Number(p.last);
    }),
    // bitbankに無いもの（BNB・POL・ステーブルコイン）
    fetchJson("https://api.coingecko.com/api/v3/simple/price?ids=binancecoin,polygon-ecosystem-token,tether,usd-coin&vs_currencies=jpy").then(d => {
      const m = { binancecoin: "BNB", "polygon-ecosystem-token": "POL", tether: "USDT", "usd-coin": "USDC" };
      for (const [id, sym] of Object.entries(m)) if (d[id]?.jpy > 0 && !rates[sym]) rates[sym] = d[id].jpy;
    }),
  ];
  await Promise.allSettled(jobs);
  for (const [sym, r] of Object.entries(extra)) if (!rates[sym] && r > 0) rates[sym] = r; // GMOの円換算レート
  return { rates, time: Date.now() };
}

// ---------------- 入口 ----------------
export default async function handler(req, res) {
  res.setHeader("Cache-Control", "private, no-store");
  const owner = Boolean(process.env.DASH_PASSWORD && req.headers["x-dash-pass"] === process.env.DASH_PASSWORD);
  if (req.method === "GET") return legacy(req, res, owner);
  if (req.method !== "POST") return res.status(405).end();

  let body = req.body;
  if (typeof body === "string") { try { body = JSON.parse(body); } catch { body = {}; } }
  const conns = Array.isArray(body?.conns) ? body.conns.slice(0, 20) : [];
  const results = {}, extraRates = {};

  await Promise.all(conns.map(async c => {
    const id = String(c.id || "");
    try {
      let rows, note;
      if (c.kind === "wallet") ({ rows, note } = await wallet(c));
      else if (c.kind === "exchange" && EXCHANGES[c.ex]) {
        let key = c.key, secret = c.secret;
        if (c.server && c.ex === "bitbank") {
          if (!owner) throw new Error("合言葉が違います");
          key = process.env.PORTFOLIO_BITBANK_KEY; secret = process.env.PORTFOLIO_BITBANK_SECRET;
          if (!key || !secret) throw new Error("サーバーにbitbankの鍵が保存されていません");
        }
        if (!key || !secret) throw new Error("APIキーとシークレットを入れてください");
        rows = (await EXCHANGES[c.ex](String(key).trim(), String(secret).trim())).filter(r => r.qty > 0);
        for (const r of rows) if (r.rate) { extraRates[r.symbol] = r.rate; delete r.rate; }
      } else throw new Error("つなぎ先の種類が不明です");
      results[id] = { rows, note, time: Date.now() };
    } catch (e) {
      results[id] = { state: e.name === "TimeoutError" ? "時間切れ（相手先が混んでいます）" : String(e.message || e) };
    }
  }));

  return res.status(200).json({ results, quotes: await quotes(extraRates) });
}

// 以前の画面向け（GET ?chain=bsc&address=0x…）
async function legacy(req, res, owner) {
  const bsc = req.query.chain === "bsc";
  const run = fn => fn().catch(() => ({ state: "取得失敗" }));
  const [bank, eth, q] = await Promise.all([
    run(async () => {
      if (!owner) return { state: "未接続" };
      const key = process.env.PORTFOLIO_BITBANK_KEY, secret = process.env.PORTFOLIO_BITBANK_SECRET;
      if (!key || !secret) return { state: "未設定" };
      return { rows: (await EXCHANGES.bitbank(key, secret)).filter(r => r.qty > 0), time: Date.now() };
    }),
    run(async () => {
      const address = req.query.address || (owner && process.env.PORTFOLIO_ETH_ADDRESS);
      if (!address) return { state: "未設定" };
      return { rows: (await wallet({ address, chains: [bsc ? "bsc" : "eth"] })).rows, time: Date.now() };
    }),
    quotes({}),
  ]);
  return res.status(200).json({ bank, eth, quotes: q });
}
