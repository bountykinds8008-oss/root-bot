// 暗号資産ニュースの見出しを集める（GET /api/news）。
// 見出し・時刻・リンクだけを返し、本文は載せない（タップで元の記事を開く）。
// 取得元が落ちていても、取れた分だけ返す。CDNに2分キャッシュして取得元に負担をかけない。
const SOURCES = [
  // CoinPost：Googleニュース用のサイトマップ（見出し・公開時刻・URL）
  { name: "CoinPost", url: "https://coinpost.jp/news_sitemap.php", kind: "sitemap" },
  // ここから下は、トップページに書かれているRSSの住所を自動で見つけて読む（住所の変更にも強い）
  { name: "CoinDesk JAPAN", home: "https://www.coindeskjapan.com/", guess: ["feed/", "rss/", "feed"] },
  { name: "あたらしい経済", home: "https://www.neweconomy.jp/", guess: ["feed/", "rss", "feed"] },
  { name: "コインテレグラフ", home: "https://jp.cointelegraph.com/", guess: ["rss", "feed"] },
];
const UA = { "User-Agent": "Mozilla/5.0 (compatible; ROOT-BOT-dashboard/1.0; +https://root-bot-dashboard.vercel.app)" };
const found = {};   // 見つけたRSSの住所（関数が温まっている間は使い回す）

async function get(url) {
  const r = await fetch(url, { headers: UA, redirect: "follow", signal: AbortSignal.timeout(6000) });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.text();
}
const isFeed = t => /<rss[\s>]|<feed[\s>]|<rdf:RDF/i.test(t.slice(0, 2000));
async function readSource(s) {
  if (s.url) return parse(await get(s.url), s);
  if (found[s.name]) { try { const t = await get(found[s.name]); if (isFeed(t)) return parse(t, { ...s, kind: "rss" }); } catch {} }
  const tries = [];
  try {   // トップページの <link rel="alternate" type="application/rss+xml" href="…">
    const html = await get(s.home);
    for (const m of html.matchAll(/<link[^>]+type=["']application\/(?:rss|atom)\+xml["'][^>]*>/gi)) {
      const h = m[0].match(/href=["']([^"']+)["']/i); if (h) tries.push(new URL(h[1], s.home).href);
    }
  } catch {}
  for (const g of s.guess) tries.push(new URL(g, s.home).href);
  for (const u of [...new Set(tries)]) {
    try { const t = await get(u); if (isFeed(t)) { found[s.name] = u; return parse(t, { ...s, kind: "rss" }); } } catch {}
  }
  throw new Error("feed not found");
}

// 5大コインに関係する見出しに印をつける
const TAGS = {
  btc_jpy: /ビットコイン|\bBTC\b|Bitcoin/i,
  xrp_jpy: /リップル|\bXRP\b|Ripple/i,
  sol_jpy: /ソラナ|\bSOL\b|Solana/i,
  eth_jpy: /イーサリアム|\bETH\b|Ethereum|イーサ/i,
  bnb_jpy: /\bBNB\b|バイナンス|Binance/i,
};

const decode = s => s.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
  .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
  .replace(/&#0?39;|&apos;/g, "'").replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n)).replace(/<[^>]+>/g, "").trim();
const pick = (xml, tag) => { const m = xml.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`)); return m ? decode(m[1]) : ""; };

export function parse(xml, src) {
  const out = [];
  if (src.kind === "sitemap") {
    for (const u of xml.split(/<url>/).slice(1)) {
      const title = pick(u, "news:title"), link = pick(u, "loc"), date = Date.parse(pick(u, "news:publication_date"));
      if (title && link && date) out.push({ title, link, ts: date, src: src.name });
    }
  } else {
    const parts = /<item[\s>]/.test(xml) ? xml.split(/<item[\s>]/).slice(1) : xml.split(/<entry[\s>]/).slice(1);   // RSS / Atom
    for (const it of parts) {
      let link = pick(it, "link"); if (!link) { const m = it.match(/<link[^>]+href=["']([^"']+)["']/); if (m) link = m[1]; }
      const title = pick(it, "title"), date = Date.parse(pick(it, "pubDate") || pick(it, "dc:date") || pick(it, "updated") || pick(it, "published"));
      if (title && link && date) out.push({ title, link, ts: date, src: src.name });
    }
  }
  return out;
}

export default async function handler(req, res) {
  const got = await Promise.allSettled(SOURCES.map(readSource));
  const seen = new Set(), titles = new Set(), items = [];
  for (const g of got) if (g.status === "fulfilled") for (const n of g.value) {
    const key = n.title.replace(/\s/g, "").slice(0, 24);
    if (seen.has(n.link) || titles.has(key) || Date.now() - n.ts > 3 * 864e5) continue; seen.add(n.link); titles.add(key);
    n.coins = Object.keys(TAGS).filter(k => TAGS[k].test(n.title));
    if (/^https?:\/\//.test(n.link)) items.push(n);
  }
  items.sort((a, b) => b.ts - a.ts);
  res.setHeader("Cache-Control", "public, s-maxage=120, stale-while-revalidate=300");
  res.status(200).json({
    items: items.slice(0, 60),
    sources: SOURCES.map((s, i) => ({ name: s.name, ok: got[i].status === "fulfilled" })),
    time: Date.now(),
  });
}
