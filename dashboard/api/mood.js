// 市場の気分（暗号資産の Fear & Greed Index、alternative.me）を日本語にして返す。GET /api/mood
// 0〜100。低いほど「怖がっている」、高いほど「欲張っている」。1日1回更新なので30分キャッシュ。
const JP = v => v < 25 ? "極度の恐怖" : v < 45 ? "恐怖" : v <= 55 ? "中立" : v <= 75 ? "強欲" : "極度の強欲";
export default async function handler(req, res) {
  try {
    const r = await fetch("https://api.alternative.me/fng/?limit=31", { signal: AbortSignal.timeout(6000) });
    const d = await r.json();
    const list = (d.data || []).map(x => ({ v: +x.value, ts: +x.timestamp * 1000 })).filter(x => x.v >= 0);
    if (!list.length) throw new Error("no data");
    res.setHeader("Cache-Control", "public, s-maxage=1800, stale-while-revalidate=3600");
    res.status(200).json({ value: list[0].v, label: JP(list[0].v), ts: list[0].ts,
      yesterday: list[1]?.v ?? null, week: list[7]?.v ?? null,
      history: list.slice().reverse().map(x => x.v), source: "alternative.me Crypto Fear & Greed Index" });
  } catch (e) {
    res.status(200).json({ error: "取得できませんでした" });
  }
}
