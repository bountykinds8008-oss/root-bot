// 資産一覧：つなぐ先（ウォレット・取引所）を選んで追加し、まとめて円換算で表示する。
// つなぐ先の設定はこの端末の localStorage（rb_conns）だけに保存する。
const $ = id => document.getElementById(id);
const store = {
  get(k, d = null) { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  json(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
};
const yen = n => (n < 0 ? "−¥" : "¥") + Math.abs(Math.round(n)).toLocaleString("ja-JP");
const hm = t => new Date(t).toLocaleTimeString("ja-JP", { timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit" });
const qtyFmt = q => q.toLocaleString("ja-JP", { maximumSignificantDigits: 8 });
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const shortAddr = a => a.slice(0, 6) + "…" + a.slice(-4);

// ---- つなぐ先の種類（アイコンは文字バッジ。各社のロゴは使わない） ----
const WALLETS = {
  metamask: { name: "MetaMask", color: "#f6851b", ab: "MM" },
  rabby: { name: "Rabby", color: "#7084ff", ab: "Ra" },
  trust: { name: "Trust Wallet", color: "#3375bb", ab: "TW" },
  okx: { name: "OKX Wallet", color: "#d9d9d9", ab: "OK" },
  binance: { name: "Binance Web3", color: "#f0b90b", ab: "BW" },
  phantom: { name: "Phantom", color: "#ab9ff2", ab: "Ph", sol: true },
  solflare: { name: "Solflare", color: "#fc7227", ab: "Sf", sol: true },
  other: { name: "その他", color: "#8b5cff", ab: "0x" },
};
const EXS = {
  bitbank: { name: "bitbank", color: "#2fa6e8", ab: "bb" },
  bitflyer: { name: "bitFlyer", color: "#5ec6f2", ab: "bF" },
  coincheck: { name: "Coincheck", color: "#18b7a4", ab: "cc" },
  gmo: { name: "GMOコイン", color: "#ff8a3d", ab: "GM" },
};
const NETS = { eth: "Ethereum", bsc: "BNB Chain", polygon: "Polygon", arbitrum: "Arbitrum", base: "Base" };
const SYMCOL = { JPY: "#c9c2d6", BTC: "#ff9f2e", ETH: "#8b9cff", XRP: "#55d6ff", BNB: "#f0b90b", USDT: "#26a17b",
  USDC: "#2775ca", POL: "#8247e5", SOL: "#14f195", DOGE: "#c2a633", ADA: "#3468d1", DOT: "#e6007a", LTC: "#a6a9aa" };
const PALETTE = ["#ff5fc7", "#3ff2b5", "#ffc65c", "#55d6ff", "#8b5cff", "#ff8a5c", "#b8f25c"];
const colorOf = s => SYMCOL[s] || PALETTE[[...s].reduce((a, c) => a + c.charCodeAt(0), 0) % PALETTE.length];
const brandOf = c => c.kind === "wallet" ? WALLETS[c.brand] || WALLETS.other : EXS[c.ex];

// ---- 保存データ（以前の設定からの引っ越しも） ----
let conns = store.json("rb_conns", null);
if (!Array.isArray(conns)) {
  conns = [];
  const addr = store.get("rb_eth_address");
  if (addr) conns.push({ id: "w" + Date.now(), kind: "wallet", brand: "metamask", address: addr,
    chains: [store.get("rb_chain") === "eth" ? "eth" : "bsc"] });
  if (store.get("rb_pass")) conns.push({ id: "x" + Date.now(), kind: "exchange", ex: "bitbank", server: true });
  save();
}
function save() { store.set("rb_conns", JSON.stringify(conns)); }
let last = store.json("rb_pf_last", { results: {}, quotes: null, at: 0 });   // 前回の結果（鍵は含まない）
let busy = false, shown = 0, openId = null;

// ---------------- 取得 ----------------
async function refresh() {
  if (busy) return;
  if (!conns.length) { render(); $("status").textContent = ""; return; }
  busy = true; document.body.classList.add("busy"); $("refresh").disabled = true;
  $("status").textContent = "残高と価格を取りに行っています…";
  try {
    const r = await fetch("/api/portfolio", {
      method: "POST", cache: "no-store",
      headers: { "Content-Type": "application/json", "x-dash-pass": store.get("rb_pass", "") },
      body: JSON.stringify({ conns }),
    });
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    for (const [id, v] of Object.entries(d.results || {})) {
      // 失敗したときは、前回の成功値を残して「前回値」と表示する
      if (v.rows) last.results[id] = v;
      else last.results[id] = { ...(last.results[id]?.rows ? last.results[id] : {}), state: v.state };
    }
    if (d.quotes && Object.keys(d.quotes.rates || {}).length) last.quotes = d.quotes;
    last.at = Date.now();
    store.set("rb_pf_last", JSON.stringify(last));
    const ng = conns.filter(c => last.results[c.id]?.state).length;
    if (!ng) record();
    $("status").textContent = `更新 ${hm(last.at)}・60秒ごとに自動更新` + (ng ? `・${ng}件うまく取れませんでした` : "");
  } catch {
    $("status").textContent = "通信できませんでした。表示は前回の値です。";
  } finally {
    busy = false; document.body.classList.remove("busy"); $("refresh").disabled = false;
    render();
  }
}

// ---------------- 表示 ----------------
function render() {
  const rates = last.quotes?.rates || {};
  const price = s => s === "JPY" ? 1 : rates[s];
  const hold = new Map(); let total = 0, missing = false;
  $("accN").textContent = conns.length ? `${conns.length}件` : "";
  const box = $("accounts"); box.replaceChildren();
  if (!conns.length) box.innerHTML = `<div class="empty">まだ何もつないでいません。<br>下のボタンから、MetaMaskなどのウォレットや<br>bitbank・bitFlyerなどの取引所を選んでつなげます。</div>`;

  for (const c of conns) {
    const b = brandOf(c), res = last.results[c.id] || {};
    let sum = 0, miss = false;
    for (const r of res.rows || []) {
      const p = price(r.symbol);
      if (p) sum += r.qty * p; else miss = true;
      const h = hold.get(r.symbol) || { qty: 0, where: new Set() };
      h.qty += r.qty; h.where.add(c.kind === "wallet" ? (c.label || b.name) : b.name); hold.set(r.symbol, h);
    }
    total += sum; missing ||= miss;
    const chip = res.state && res.rows ? `<span class="chip ng">前回値</span>` : res.state ? `<span class="chip ng">エラー</span>`
      : res.rows ? `<span class="chip ok">✓</span>` : `<span class="chip wait">取得中</span>`;
    const detail = c.kind === "wallet" ? `${shortAddr(c.address)}・${c.net === "sol" ? "Solana" : c.chains.map(k => NETS[k]).join("/")}`
      : c.server ? "サーバー保存の鍵" : "APIキー（参照のみ）";
    const el = document.createElement("div");
    el.className = "acc" + (openId === c.id ? " open" : "");
    el.innerHTML = `<div class="hd"><div class="ic" style="background:${b.color}">${b.ab}</div>
      <div style="min-width:0"><div class="nm">${esc(c.label || b.name)}${chip}</div><div class="dt">${esc(detail)}</div></div>
      <div class="v num">${res.rows ? yen(sum) : "—"}<small class="mute">${res.time ? hm(res.time) : ""}${miss ? "・一部未評価" : ""}</small></div></div>
      <div class="bd">
        ${res.state ? `<div class="err">⚠ ${esc(res.state)}</div>` : ""}${res.note ? `<div class="err" style="color:var(--gold)">${esc(res.note)}</div>` : ""}
        ${(res.rows || []).sort((a, z) => z.qty * (price(z.symbol) || 0) - a.qty * (price(a.symbol) || 0)).map(r => `<div class="r"><span>${esc(r.symbol)} <span class="mute num" style="font-size:12px">${qtyFmt(r.qty)}</span>${r.where ? `<span class="mute" style="font-size:11px">・${esc(r.where)}</span>` : ""}</span><span class="num">${price(r.symbol) ? yen(r.qty * price(r.symbol)) : "価格なし"}</span></div>`).join("") || (res.state ? "" : `<div class="mute" style="padding:6px 0">残高はありません</div>`)}
        <div class="ops"><button data-act="del">このつなぎ先を外す</button></div></div>`;
    el.querySelector(".hd").onclick = () => { openId = openId === c.id ? null : c.id; render(); };
    el.querySelector("[data-act=del]").onclick = e => {
      e.stopPropagation();
      if (!confirm(`「${c.label || b.name}」を外しますか？（この端末に保存したキーも消えます）`)) return;
      conns = conns.filter(x => x !== c); delete last.results[c.id]; save(); store.set("rb_pf_last", JSON.stringify(last)); render();
    };
    box.append(el);
  }

  // 合計
  const okAny = conns.some(c => last.results[c.id]?.rows);
  animateTotal(okAny ? total : null);
  const errs = conns.filter(c => last.results[c.id]?.state).length;
  $("label").textContent = errs || missing ? "合計（取れた分の小計）" : "合計（円換算）";
  $("sub").textContent = !conns.length ? "口座をつなぐと合計が出ます" :
    `${conns.length}件の口座・${hold.size}銘柄` + (last.at ? `・${hm(last.at)} 時点` : "");

  // 銘柄別
  const list = [...hold].map(([s, h]) => ({ s, ...h, jpy: price(s) ? h.qty * price(s) : null }))
    .sort((a, z) => (z.jpy ?? -1) - (a.jpy ?? -1));
  $("assetCard").hidden = !list.length;
  $("assets").innerHTML = list.map(a => {
    const pc = a.jpy != null && total > 0 ? a.jpy / total : 0;
    return `<div class="asset"><div><b>${esc(a.s)}</b> <span class="q num">${qtyFmt(a.qty)}</span><div class="q">${esc([...a.where].join("・"))}</div></div>
      <div class="num" style="text-align:right">${a.jpy != null ? yen(a.jpy) : "価格なし"}<div class="q">${a.jpy != null ? (pc * 100).toFixed(1) + "%" : ""}</div></div>
      <div class="bar"><i style="width:${(pc * 100).toFixed(2)}%;background:${colorOf(a.s)}"></i></div></div>`;
  }).join("");

  // 円グラフ（上位5つ＋その他）
  const top = list.filter(a => a.jpy > 0).slice(0, 5), rest = list.filter(a => a.jpy > 0).slice(5).reduce((s, a) => s + a.jpy, 0);
  const parts = top.map(a => ({ s: a.s, v: a.jpy, c: colorOf(a.s) }));
  if (rest > 0) parts.push({ s: "その他", v: rest, c: "#4a3f66" });
  donut.to(parts);
  renderHist();
  $("legend").innerHTML = parts.map(p => `<span><i style="background:${p.c}"></i>${esc(p.s)} ${total ? (p.v / total * 100).toFixed(1) : 0}%</span>`).join("");
}

// ---------------- 資産の推移（この端末に記録） ----------------
// 全部の口座が取れたときだけ記録する（一部だけの合計を混ぜない）。
// つなぐ先が変わると合計の意味が変わるので、つなぎ先の組み合わせ（sig）も一緒に残す。
let hist = store.json("rb_pf_hist", []);          // [[時刻, 合計, sig], ...]
let range = +store.get("rb_pf_rng", "30");
const sigOf = () => conns.map(c => c.id).sort().join(",");
function totalNow() {
  const rates = last.quotes?.rates || {}; let t = 0;
  for (const c of conns) for (const r of last.results[c.id]?.rows || []) t += r.qty * (r.symbol === "JPY" ? 1 : rates[r.symbol] || 0);
  return t;
}
function record() {
  const now = Date.now(), t = totalNow(), sig = sigOf();
  if (!(t > 0)) return;
  const prev = hist[hist.length - 1];
  if (prev && now - prev[0] < 10 * 60e3 && prev[2] === sig) hist[hist.length - 1] = [now, t, sig];   // 10分以内は上書き
  else hist.push([now, t, sig]);
  // 2日より前は1日1点（その日の最後）に間引く。最大3年分
  const cut = now - 2 * 864e5, keep = [], seen = new Map();
  for (const h of hist) {
    if (h[0] >= cut) { keep.push(h); continue; }
    const day = new Date(h[0] + 9 * 36e5).toISOString().slice(0, 10);
    seen.set(day, h);
  }
  hist = [...seen.values(), ...keep].sort((a, b) => a[0] - b[0]).slice(-1500);
  store.set("rb_pf_hist", JSON.stringify(hist));
}
function renderHist() {
  const sig = sigOf(), now = Date.now();
  $("histCard").hidden = hist.length < 1 || !conns.length;
  if ($("histCard").hidden) return;
  const cur = totalNow() || hist[hist.length - 1][1];
  const ref = since => { let r = null; for (const h of hist) if (h[2] === sig && h[0] <= since) r = h; return r; };
  const firstSame = hist.find(h => h[2] === sig);
  const d1 = ref(now - 864e5), mStart = new Date(new Date(now + 9 * 36e5).toISOString().slice(0, 8) + "01T00:00:00+09:00").getTime();
  const m1 = ref(mStart) || (firstSame && firstSame[0] >= mStart ? firstSame : null);
  const cell = (k, base) => {
    if (!base) return `<div><small>${k}</small><b class="mute">記録中</b></div>`;
    const d = cur - base[1], p = d / base[1];
    return `<div><small>${k}</small><b class="num ${d > 0 ? "up" : d < 0 ? "down" : ""}">${d >= 0 ? "+" : ""}${yen(d)}</b><small class="num">${(p >= 0 ? "+" : "") + (p * 100).toFixed(2)}%</small></div>`;
  };
  $("hk").innerHTML = cell("前日比", d1) + cell("今月", m1) + cell("記録してから", firstSame && firstSame !== hist[hist.length - 1] ? firstSame : null);
  const from = range ? now - range * 864e5 : 0;
  const pts = hist.filter(h => h[0] >= from);
  drawHist(pts, sig);
  const days = hist.length ? Math.max(1, Math.round((now - hist[0][0]) / 864e5)) : 0;
  $("hn").textContent = `この端末で開いたときに記録しています（${days}日分・${hist.length}点）。` +
    (pts.some(h => h[2] !== sig) ? "点線の区切りは、つなぐ先を変えた時点です。" : "");
}
const HC = $("hist");
function drawHist(pts, sig) {
  const d = Math.min(4, devicePixelRatio || 1), r = HC.getBoundingClientRect(), x = HC.getContext("2d");
  if (HC.width !== Math.round(r.width * d) || HC.height !== Math.round(r.height * d)) { HC.width = Math.round(r.width * d); HC.height = Math.round(r.height * d); }
  x.setTransform(d, 0, 0, d, 0, 0); const W = r.width, H = r.height; x.clearRect(0, 0, W, H);
  x.lineJoin = x.lineCap = "round";
  if (pts.length < 2) { x.fillStyle = "#9a8fb0"; x.font = "12px -apple-system,sans-serif"; x.fillText("記録がたまるとグラフになります", 6, H / 2); return; }
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0], v = pts.map(p => p[1]);
  let lo = Math.min(...v), hi = Math.max(...v); const pd = (hi - lo || hi * .01) * .12; lo -= pd; hi += pd;
  const X = t => 4 + (t - t0) / (t1 - t0 || 1) * (W - 70), Y = q => 6 + (1 - (q - lo) / (hi - lo)) * (H - 20);
  x.font = "10px ui-monospace,monospace"; x.fillStyle = "#6d6385";
  for (let k = 0; k <= 3; k++) { const q = lo + (hi - lo) * k / 3, y = Math.round(Y(q) * d) / d + .5 / d;
    x.strokeStyle = "rgba(139,92,255,.16)"; x.lineWidth = 1 / d; x.beginPath(); x.moveTo(0, y); x.lineTo(W - 64, y); x.stroke();
    x.fillText(yen(q).replace("¥", ""), W - 60, y + 3); }
  const P = pts.map(p => [X(p[0]), Y(p[1])]);
  const curve = (a, move = true) => { move ? x.moveTo(a[0][0], a[0][1]) : x.lineTo(a[0][0], a[0][1]);
    for (let i = 1; i < a.length - 1; i++) x.quadraticCurveTo(a[i][0], a[i][1], (a[i][0] + a[i + 1][0]) / 2, (a[i][1] + a[i + 1][1]) / 2);
    x.lineTo(a[a.length - 1][0], a[a.length - 1][1]); };
  const up = v[v.length - 1] >= v[0], col = up ? "#3ff2b5" : "#ff5577", rgb = up ? "63,242,181" : "255,85,119";
  const g = x.createLinearGradient(0, 0, 0, H); g.addColorStop(0, `rgba(${rgb},.28)`); g.addColorStop(1, `rgba(${rgb},0)`);
  x.beginPath(); curve(P); x.lineTo(P[P.length - 1][0], H); x.lineTo(P[0][0], H); x.fillStyle = g; x.fill();
  x.beginPath(); curve(P); x.strokeStyle = col; x.lineWidth = 1.8; x.shadowColor = col; x.shadowBlur = 8; x.stroke(); x.shadowBlur = 0;
  for (let i = 1; i < pts.length; i++) if (pts[i][2] !== pts[i - 1][2]) {        // つなぐ先を変えた所
    x.setLineDash([3, 4]); x.strokeStyle = "rgba(255,198,92,.6)"; x.lineWidth = 1; x.beginPath(); x.moveTo(P[i][0], 4); x.lineTo(P[i][0], H - 12); x.stroke(); x.setLineDash([]); }
  const l = P[P.length - 1]; x.fillStyle = "#fff"; x.beginPath(); x.arc(l[0], l[1], 3, 0, 7); x.fill();
  x.fillStyle = "#6d6385"; x.font = "10px ui-monospace,monospace";
  const lab = t => range === 1 ? hm(t) : new Date(t + 9 * 36e5).toISOString().slice(5, 10).replace("-", "/");
  x.fillText(lab(t0), 4, H - 2); x.textAlign = "right"; x.fillText(lab(t1), W - 66, H - 2); x.textAlign = "left";
}
document.querySelectorAll("#rng button").forEach(b => {
  b.classList.toggle("on", +b.dataset.r === range);
  b.onclick = () => { range = +b.dataset.r; store.set("rb_pf_rng", String(range));
    document.querySelectorAll("#rng button").forEach(z => z.classList.toggle("on", z === b)); renderHist(); };
});
addEventListener("resize", () => renderHist());

// 合計の数字はなめらかに数え上げる
let raf = 0;
function animateTotal(target) {
  cancelAnimationFrame(raf);
  if (target == null) { $("total").textContent = "—"; return; }
  const from = shown || target * .96, t0 = performance.now(), dur = 700;
  const step = now => {
    const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    shown = from + (target - from) * e; $("total").textContent = yen(shown);
    if (k < 1) raf = requestAnimationFrame(step);
  };
  raf = requestAnimationFrame(step);
}

// 円グラフ：端末の実ピクセルで描き、変化はアニメーション
const donut = (() => {
  const cv = $("donut"), x = cv.getContext("2d"); let cur = [], tgt = [], t0 = 0;
  function fit() {
    const d = Math.min(4, devicePixelRatio || 1), r = cv.getBoundingClientRect();
    if (cv.width !== Math.round(r.width * d)) { cv.width = Math.round(r.width * d); cv.height = Math.round(r.height * d); }
    x.setTransform(d, 0, 0, d, 0, 0); return r.width;
  }
  function draw(parts, k) {
    const W = fit(), c = W / 2, R = W / 2 - 3, sum = parts.reduce((s, p) => s + p.v, 0);
    x.clearRect(0, 0, W, W);
    x.lineWidth = 13; x.lineCap = "butt";
    x.strokeStyle = "#24193a"; x.beginPath(); x.arc(c, c, R - 7, 0, Math.PI * 2); x.stroke();
    if (!sum) return;
    let a = -Math.PI / 2; const full = Math.PI * 2 * k;
    for (const p of parts) {
      const da = p.v / sum * full; if (da <= 0) continue;
      x.strokeStyle = p.c; x.shadowColor = p.c; x.shadowBlur = 6;
      x.beginPath(); x.arc(c, c, R - 7, a + .012, a + da - .012); x.stroke(); a += da;
    }
    x.shadowBlur = 0;
    x.fillStyle = "#f1e9ff"; x.font = "700 13px -apple-system,'Hiragino Sans',sans-serif"; x.textAlign = "center";
    x.fillText(parts[0] ? parts[0].s : "", c, c + 1);
    x.fillStyle = "#9a8fb0"; x.font = "11px ui-monospace,monospace";
    x.fillText(parts[0] ? (parts[0].v / sum * 100).toFixed(0) + "%" : "", c, c + 16);
  }
  return {
    to(parts) {
      const same = JSON.stringify(parts.map(p => [p.s, Math.round(p.v)])) === JSON.stringify(tgt.map(p => [p.s, Math.round(p.v)]));
      tgt = parts; if (same && cur.length) return draw(tgt, 1);
      t0 = performance.now(); cur = parts;
      const step = now => { const k = Math.min(1, (now - t0) / 800); draw(tgt, 1 - Math.pow(1 - k, 3)); if (k < 1) requestAnimationFrame(step); };
      requestAnimationFrame(step);
    },
    redraw() { draw(tgt, 1); },
  };
})();
addEventListener("resize", () => donut.redraw());

// ---------------- つなぐシート ----------------
let tab = "wallet", sel = null;
function openSheet() { $("sheet").classList.add("show"); sel = null; drawPick(); drawForm(); }
function closeSheet() { $("sheet").classList.remove("show"); }
$("addBtn").onclick = openSheet;
$("shX").onclick = closeSheet;
$("sheet").onclick = e => { if (e.target === $("sheet")) closeSheet(); };
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => {
  tab = b.dataset.tab; sel = null;
  document.querySelectorAll(".tabs button").forEach(x => x.classList.toggle("on", x === b));
  drawPick(); drawForm();
});
function drawPick() {
  const src = tab === "wallet" ? WALLETS : EXS;
  $("pick").innerHTML = Object.entries(src).map(([k, b]) =>
    `<button data-k="${k}" class="${sel === k ? "on" : ""}"><span class="ic" style="background:${b.color}">${b.ab}</span>${esc(b.name)}</button>`).join("");
  $("pick").querySelectorAll("button").forEach(b => b.onclick = () => { sel = b.dataset.k; drawPick(); drawForm(); });
  $("shT").textContent = sel ? `${src[sel].name} をつなぐ` : "つなぐ先を選ぶ";
}
function drawForm() {
  const f = $("form");
  if (!sel) { f.innerHTML = `<div class="help">${tab === "wallet"
    ? "使っているウォレットを選んでください。<b>公開アドレス（0x…）だけ</b>で残高を読みます。秘密鍵や復元フレーズは<b>絶対に入力しません</b>。"
    : "使っている取引所を選んでください。取引所で作った<b>「残高を見るだけ」のAPIキー</b>でつなぎます。"}</div>`; return; }
  if (tab === "wallet") return walletForm(f);
  return exchangeForm(f);
}

function walletForm(f) {
  const nets = new Set(Object.keys(NETS));
  let sol = !!WALLETS[sel].sol;
  const eth = sol ? window.solana : window.ethereum;
  f.innerHTML = `
    <div class="help"><b>アドレスのコピー方法</b><br>${esc(WALLETS[sel].name)}のアプリを開く → いちばん上のアカウント名（または「受け取る」）をタップ → アドレスをコピー。<br>
      <span style="color:var(--gold)">秘密鍵・12個の英単語（復元フレーズ）は絶対に入れないでください。</span></div>
    ${sel === "other" ? `<div class="fld"><label>チェーン</label><div class="nets" id="wKind"><button type="button" data-k="evm" class="on">EVM（0x…）</button><button type="button" data-k="sol">Solana</button></div></div>` : ""}
    ${eth ? `<button class="alt" id="wRead">このブラウザのウォレットからアドレスを読み込む</button>` : ""}
    <div class="fld"><label id="wLab">公開アドレス</label>
      <div style="display:flex;gap:6px"><input id="wAddr" placeholder="0x…" autocomplete="off" autocapitalize="off" spellcheck="false" inputmode="text">
      <button class="tb" id="wPaste" type="button">貼り付け</button></div></div>
    <div class="fld" id="wNetBox"><label>見るネットワーク（あとで外すこともできます）</label><div class="nets" id="wNets">
      ${Object.entries(NETS).map(([k, n]) => `<button type="button" data-k="${k}" class="on">${n}</button>`).join("")}</div></div>
    <div class="fld"><label>表示名（なくてもOK）</label><input id="wName" placeholder="${esc(WALLETS[sel].name)}" maxlength="20"></div>
    <p class="msg" id="wMsg"></p>
    <button class="go" id="wGo">つなぐ</button>`;
  const syncKind = () => {
    $("wNetBox").hidden = sol;
    $("wLab").textContent = sol ? "公開アドレス（Solana：32〜44文字の英数字）" : "公開アドレス（0xから始まる42文字）";
    $("wAddr").placeholder = sol ? "例：7xKX…" : "0x…";
  };
  if ($("wKind")) $("wKind").querySelectorAll("button").forEach(b => b.onclick = () => {
    sol = b.dataset.k === "sol"; $("wKind").querySelectorAll("button").forEach(z => z.classList.toggle("on", z === b)); syncKind(); });
  syncKind();
  $("wNets").querySelectorAll("button").forEach(b => b.onclick = () => {
    nets.has(b.dataset.k) ? nets.delete(b.dataset.k) : nets.add(b.dataset.k); b.classList.toggle("on", nets.has(b.dataset.k)); });
  $("wPaste").onclick = async () => { try { $("wAddr").value = (await navigator.clipboard.readText()).trim(); } catch { $("wMsg").textContent = "貼り付けできませんでした。入力欄を長押しして「ペースト」を選んでください。"; } };
  if (eth) $("wRead").onclick = async () => {
    try {
      if (sol) { const r = await eth.connect({ onlyIfTrusted: false }); $("wAddr").value = String(r.publicKey); return; }
      const [a] = await eth.request({ method: "eth_requestAccounts" }); if (a) $("wAddr").value = a; }
    catch { $("wMsg").textContent = "読み込みがキャンセルされました。"; }
  };
  $("wGo").onclick = () => {
    const a = $("wAddr").value.trim();
    if (sol && !/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(a)) return $("wMsg").textContent = "Solanaのアドレスの形が違います（32〜44文字の英数字）。";
    if (!sol && !/^0x[0-9a-fA-F]{40}$/.test(a)) return $("wMsg").textContent = "アドレスの形が違います（0x＋40文字の英数字）。";
    if (!sol && !nets.size) return $("wMsg").textContent = "ネットワークを1つ以上選んでください。";
    if (conns.some(c => c.kind === "wallet" && c.address.toLowerCase() === a.toLowerCase()))
      return $("wMsg").textContent = "このアドレスは、もうつないであります。";
    const c = sol ? { id: "w" + Date.now(), kind: "wallet", brand: sel, net: "sol", address: a, chains: [] }
      : { id: "w" + Date.now(), kind: "wallet", brand: sel, address: a, chains: [...nets] };
    if ($("wName").value.trim()) c.label = $("wName").value.trim();
    added(c);
  };
}

function exchangeForm(f) {
  const isBB = sel === "bitbank", hasPass = !!store.get("rb_pass");
  f.innerHTML = `
    <div class="help warn"><b>APIキーは「残高を見る」だけの権限で作ってください</b><br>
      ${esc(EXS[sel].name)}のサイト（パソコンが楽です）にログイン → 「API」の設定画面で新しいキーを作成 → 権限は<b>参照・残高照会だけ</b>にチェック。<b>取引・出金・送金には絶対にチェックしない</b>でください。</div>
    ${isBB ? `<label class="toggle"><input type="checkbox" id="xSrv" ${hasPass ? "checked" : ""}>サーバーに保存してあるbitbankの鍵を使う（合言葉）</label>` : ""}
    <div id="xKeys">
      <div class="fld"><label>APIキー</label><input id="xKey" autocomplete="off" autocapitalize="off" spellcheck="false"></div>
      <div class="fld"><label>シークレット（秘密のほう）</label><input id="xSec" type="password" autocomplete="off" autocapitalize="off" spellcheck="false"></div>
      <div class="help">キーはこの端末の中だけに保存し、残高を取りに行くときだけ使います（サーバーには保存しません）。</div>
    </div>
    <div id="xPassBox" hidden><div class="fld"><label>合言葉</label><input id="xPass" type="password" value="${esc(store.get("rb_pass", ""))}"></div></div>
    <p class="msg" id="xMsg"></p>
    <button class="go" id="xGo">つなぐ</button>`;
  const sync = () => { const srv = isBB && $("xSrv").checked; $("xKeys").hidden = srv; $("xPassBox").hidden = !srv; };
  if (isBB) { $("xSrv").onchange = sync; sync(); }
  $("xGo").onclick = () => {
    const srv = isBB && $("xSrv").checked;
    if (conns.some(c => c.kind === "exchange" && c.ex === sel)) return $("xMsg").textContent = "この取引所は、もうつないであります。";
    if (srv) {
      if (!$("xPass").value) return $("xMsg").textContent = "合言葉を入れてください。";
      store.set("rb_pass", $("xPass").value);
      return added({ id: "x" + Date.now(), kind: "exchange", ex: sel, server: true });
    }
    const key = $("xKey").value.trim(), secret = $("xSec").value.trim();
    if (key.length < 8 || secret.length < 8) return $("xMsg").textContent = "APIキーとシークレットを両方入れてください。";
    added({ id: "x" + Date.now(), kind: "exchange", ex: sel, key, secret });
  };
}

function added(c) {
  conns.push(c); save(); closeSheet(); openId = c.id; render(); refresh();
}

// ---------------- 5大コイン（bitbankの価格・7日の動き） ----------------
const TOP5 = [["btc_jpy", "BTC", "ビットコイン"], ["xrp_jpy", "XRP", "リップル"], ["sol_jpy", "SOL", "ソラナ"],
  ["eth_jpy", "ETH", "イーサリアム"], ["bnb_jpy", "BNB", "BNB"]];
const spark = {};
const pxFmt = (p, v) => "¥" + (p === "xrp_jpy" ? v.toFixed(3) : p === "sol_jpy" ? v.toLocaleString("ja-JP", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : Math.round(v).toLocaleString("ja-JP"));
async function loadTop5(withSpark) {
  try {
    const d = await (await fetch("/api/market", { cache: "no-store" })).json();
    if (withSpark) await Promise.all(TOP5.map(async ([p]) => {
      try { const r = await (await fetch(`/api/market?pair=${p}&tf=1hour`)).json(); if (r.ohlc) spark[p] = r.ohlc.slice(-168).map(o => o[4]); } catch {}
    }));
    renderTop5(d.tickers || {});
  } catch { $("t5t").textContent = "価格を取得できませんでした"; }
}
function renderTop5(t) {
  const hold = new Map();
  for (const c of conns) for (const r of last.results[c.id]?.rows || []) hold.set(r.symbol, (hold.get(r.symbol) || 0) + r.qty);
  $("t5").innerHTML = TOP5.map(([p, s, n]) => {
    const k = t[p]; if (!k) return "";
    const ch = k.last / k.open - 1, mine = hold.get(s);
    return `<div class="c5"><div class="ic" style="background:${colorOf(s)}">${s}</div>
      <div class="nm">${n}<small>${s}/JPY${mine ? `<span class="own">・保有 ${qtyFmt(mine)}</span>` : ""}</small></div>
      <canvas data-p="${p}"></canvas>
      <div class="px num">${pxFmt(p, k.last)}<small class="${ch > 0 ? "up" : ch < 0 ? "down" : ""}">24h ${(ch >= 0 ? "+" : "") + (ch * 100).toFixed(2)}%</small></div></div>`;
  }).join("");
  $("t5").querySelectorAll("canvas").forEach(cv => {
    const v = spark[cv.dataset.p]; if (!v || v.length < 2) return;
    const d = Math.min(4, devicePixelRatio || 1), W = 72, H = 32, x = cv.getContext("2d");
    cv.width = W * d; cv.height = H * d; x.setTransform(d, 0, 0, d, 0, 0); x.lineJoin = x.lineCap = "round";
    const lo = Math.min(...v), hi = Math.max(...v), X = i => 1 + i / (v.length - 1) * (W - 2), Y = q => 2 + (1 - (q - lo) / (hi - lo || 1)) * (H - 4);
    const up = v[v.length - 1] >= v[0], col = up ? "#3ff2b5" : "#ff5577";
    const g = x.createLinearGradient(0, 0, 0, H); g.addColorStop(0, up ? "rgba(63,242,181,.25)" : "rgba(255,85,119,.25)"); g.addColorStop(1, "rgba(0,0,0,0)");
    x.beginPath(); v.forEach((q, i) => i ? x.lineTo(X(i), Y(q)) : x.moveTo(X(i), Y(q))); x.lineTo(W, H); x.lineTo(0, H); x.fillStyle = g; x.fill();
    x.beginPath(); v.forEach((q, i) => i ? x.lineTo(X(i), Y(q)) : x.moveTo(X(i), Y(q))); x.strokeStyle = col; x.lineWidth = 1.4; x.stroke();
  });
  $("t5t").textContent = "bitbankの価格・7日の動き";
}
loadTop5(true);
setInterval(() => { if (!document.hidden) loadTop5(false); }, 15000);
setInterval(() => { if (!document.hidden) loadTop5(true); }, 10 * 60000);

$("refresh").onclick = () => { refresh(); loadTop5(true); };
setInterval(() => { if (!document.hidden) refresh(); }, 60000);
document.addEventListener("visibilitychange", () => { if (!document.hidden && Date.now() - last.at > 30000) refresh(); });
render();
refresh();
