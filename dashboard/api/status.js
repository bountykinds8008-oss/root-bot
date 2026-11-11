// Vercel サーバーレス関数：非公開リポジトリから status.json / backtest.json を読み、パスワード一致時だけ返す。
// 必要な環境変数（Vercelの設定画面で登録）:
//   GH_TOKEN      … GitHubの読み取り専用トークン（このリポジトリの Contents: Read のみ）
//   GH_REPO       … 例 bountykinds8008-oss/root-bot
//   DASH_PASSWORD … 画面を開くときの合言葉
export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  const pass = req.headers["x-dash-pass"] || "";
  if (!process.env.DASH_PASSWORD || pass !== process.env.DASH_PASSWORD) {
    return res.status(401).json({ error: "unauthorized" });
  }
  const file = req.query.file === "backtest" ? "backtest.json" : "status.json";
  const url = `https://api.github.com/repos/${process.env.GH_REPO}/contents/data/${file}`;
  const r = await fetch(url, {
    headers: {
      Authorization: `Bearer ${process.env.GH_TOKEN}`,
      Accept: "application/vnd.github.raw+json",
      "User-Agent": "root-bot-dashboard",
    },
  });
  if (r.status === 404) return res.status(404).json({ error: "not_yet" });
  if (!r.ok) return res.status(502).json({ error: `github ${r.status}` });
  res.setHeader("Content-Type", "application/json");
  return res.status(200).send(await r.text());
}
