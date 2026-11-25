// 暗号資産ニュースの見出しを集める（GET /api/news）。
// 見出し・時刻・リンクだけを返し、本文は載せない（タップで元の記事を開く）。
// 取得元が落ちていても、取れた分だけ返す。CDNに2分キャッシュして取得元に負担をかけない。
const SOURCES = [
  // CoinPost：Googleニュース用のサイトマップ（見出し・公開時刻・URL）
  { name: "CoinPost", url: "https://coinpost.jp/news_sitemap.php", kind: "sitemap" },
  // CoinDesk JAPAN：WordPressのRSS
  { name: "CoinDesk JAPAN", url: "https://www.coindeskjapan.com/feed/", kind: "rss" },
];

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
    for (const it of xml.split(/<item[\s>]/).slice(1)) {
      const title = pick(it, "title"), link = pick(it, "link"), date = Date.parse(pick(it, "pubDate") || pick(it, "dc:date"));
      if (title && link && date) out.push({ title, link, ts: date, src: src.name });
    }
  }
  return out;
}

export default async function handler(req, res) {
  const got = await Promise.allSettled(SOURCES.map(async s => {
    const r = await fetch(s.url, { headers: { "User-Agent": "ROOT-BOT-dashboard/1.0 (+https://root-bot-dashboard.vercel.app)" },
      signal: AbortSignal.timeout(6000) });
    if (!r.ok) throw new Error(`${s.name} ${r.status}`);
    return parse(await r.text(), s);
  }));
  const seen = new Set(), items = [];
  for (const g of got) if (g.status === "fulfilled") for (const n of g.value) {
    if (seen.has(n.link)) continue; seen.add(n.link);
    n.coins = Object.keys(TAGS).filter(k => TAGS[k].test(n.title));
    if (/^https?:\/\//.test(n.link)) items.push(n);
  }
  items.sort((a, b) => b.ts - a.ts);
  res.setHeader("Cache-Control", "public, s-maxage=120, stale-while-revalidate=300");
  res.status(200).json({
    items: items.slice(0, 40),
    sources: SOURCES.map((s, i) => ({ name: s.name, ok: got[i].status === "fulfilled" })),
    time: Date.now(),
  });
}
