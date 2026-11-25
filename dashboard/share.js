/* 共有：画面の成績を1枚の画像にして、LINE・X・メッセージなどに送る。
   iPhoneの共有シートは「タップした瞬間」でないと開けないので、
   先に画像を作ってプレビューを出し、「共有する」ボタンで送る。 */
(() => {
  const css = `
  .shx{position:fixed;inset:0;z-index:96;display:none;background:rgba(5,3,10,.78);backdrop-filter:blur(4px);align-items:center;justify-content:center}
  .shx.show{display:flex}
  .shx .cd{width:min(400px,94vw);max-height:94dvh;overflow:auto;background:#120c1d;border:1px solid #ff5fc7;border-radius:14px;padding:14px;
    box-shadow:0 0 40px rgba(255,95,199,.35);font:13px/1.6 -apple-system,"Hiragino Sans",sans-serif;color:#f1e9ff}
  .shx h2{font-size:15px;margin:0 0 8px;display:flex;justify-content:space-between;align-items:center}
  .shx h2 button{background:none;border:none;color:#9a8fb0;font-size:22px;line-height:1;cursor:pointer}
  .shx img{width:100%;border-radius:10px;border:1px solid rgba(154,143,176,.25);display:block;background:#0a0612;aspect-ratio:4/5;object-fit:cover}
  .shx .opt{display:flex;align-items:center;gap:8px;margin:10px 0 2px;font-size:13px}
  .shx .opt input{width:18px;height:18px;accent-color:#ff5fc7}
  .shx .bt{display:grid;gap:7px;margin-top:10px}
  .shx .bt button{width:100%;padding:12px;border-radius:10px;font:inherit;font-weight:700;font-size:14px;cursor:pointer;border:1px solid rgba(255,95,199,.5);background:rgba(255,95,199,.08);color:#f1e9ff}
  .shx .bt button.main{background:#ff5fc7;border-color:#ff5fc7;color:#1a0716;font-weight:800}
  .shx .msg{font-size:12px;color:#3ff2b5;min-height:1.4em;text-align:center;margin-top:6px}
  .shx .nt{font-size:11px;color:#9a8fb0;margin-top:6px}`;
  const st = document.createElement("style"); st.textContent = css; document.head.append(st);
  const box = document.createElement("div"); box.className = "shx";
  box.innerHTML = `<div class="cd"><h2><span>共有する</span><button aria-label="閉じる">×</button></h2>
    <img alt="共有する画像のプレビュー"><label class="opt" hidden><input type="checkbox"><span></span></label>
    <div class="bt"><button class="main" data-a="share">画像といっしょに共有</button><button data-a="link">リンクだけ共有</button><button data-a="save">画像を保存</button></div>
    <div class="msg"></div><div class="nt"></div></div>`;
  document.body.append(box);
  const $ = s => box.querySelector(s);
  let cur = null, file = null, url = "";
  const close = () => { box.classList.remove("show"); };
  $("h2 button").onclick = close;
  box.onclick = e => { if (e.target === box) close(); };

  // ---- 画像づくり（共通の部品） ----
  const W = 1080, H = 1350;
  const IOS = /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  function base(x, title, sub) {
    const g = x.createLinearGradient(0, 0, 0, H); g.addColorStop(0, "#160d26"); g.addColorStop(1, "#07040d");
    x.fillStyle = g; x.fillRect(0, 0, W, H);
    x.strokeStyle = "rgba(139,92,255,.10)"; x.lineWidth = 1;
    for (let i = 0; i <= W; i += 54) { x.beginPath(); x.moveTo(i, 0); x.lineTo(i, H); x.stroke(); }
    for (let i = 0; i <= H; i += 54) { x.beginPath(); x.moveTo(0, i); x.lineTo(W, i); x.stroke(); }
    x.strokeStyle = "#ff5fc7"; x.lineWidth = 4; x.shadowColor = "#ff5fc7"; x.shadowBlur = 24; rr(x, 24, 24, W - 48, H - 48, 34); x.stroke(); x.shadowBlur = 0;
    x.fillStyle = "#fff"; x.font = "800 54px ui-monospace,Menlo,monospace"; x.shadowColor = "#ff5fc7"; x.shadowBlur = 18;
    x.fillText("ROOT BOT", 80, 140); x.shadowBlur = 0;
    x.fillStyle = "#ff5fc7"; x.font = "700 34px -apple-system,'Hiragino Sans',sans-serif"; x.fillText(title, 80, 196);
    x.fillStyle = "#9a8fb0"; x.font = "28px -apple-system,'Hiragino Sans',sans-serif"; x.textAlign = "right"; x.fillText(sub, W - 80, 140); x.textAlign = "left";
    x.fillStyle = "#6d6385"; x.font = "26px -apple-system,'Hiragino Sans',sans-serif";
    x.fillText("root-bot-dashboard.vercel.app", 80, H - 72);
    x.textAlign = "right"; x.fillText(new Date().toLocaleString("ja-JP", { timeZone: "Asia/Tokyo", year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) + " 時点", W - 80, H - 72); x.textAlign = "left";
  }
  function rr(x, a, b, w, h, r) { x.beginPath(); x.moveTo(a + r, b); x.arcTo(a + w, b, a + w, b + h, r); x.arcTo(a + w, b + h, a, b + h, r); x.arcTo(a, b + h, a, b, r); x.arcTo(a, b, a + w, b, r); x.closePath(); }
  function line(x, v, a, b, w, h, col) {
    if (!v || v.length < 2) return;
    const lo = Math.min(...v), hi = Math.max(...v), X = i => a + i / (v.length - 1) * w, Y = q => b + (1 - (q - lo) / (hi - lo || 1)) * h;
    const g = x.createLinearGradient(0, b, 0, b + h); g.addColorStop(0, col + "55"); g.addColorStop(1, col + "00");
    x.beginPath(); v.forEach((q, i) => i ? x.lineTo(X(i), Y(q)) : x.moveTo(X(i), Y(q))); x.lineTo(a + w, b + h); x.lineTo(a, b + h); x.fillStyle = g; x.fill();
    x.beginPath(); v.forEach((q, i) => i ? x.lineTo(X(i), Y(q)) : x.moveTo(X(i), Y(q)));
    x.lineJoin = x.lineCap = "round"; x.strokeStyle = col; x.lineWidth = 5; x.shadowColor = col; x.shadowBlur = 14; x.stroke(); x.shadowBlur = 0;
  }

  async function build() {
    const c = document.createElement("canvas"); c.width = W; c.height = H;
    const x = c.getContext("2d");
    if (IOS) try { Object.defineProperty(x, "shadowBlur", { get() { return 0; }, set() {} }); } catch (e) {}   // iPhoneは光る効果なし（固まり防止）
    cur.draw(x, { W, H, base, rr, line, opt: $(".opt input").checked });
    const blob = await new Promise(r => c.toBlob(r, "image/png"));
    file = new File([blob], cur.fileName || "rootbot.png", { type: "image/png" });
    $("img").src = URL.createObjectURL(blob);
  }

  window.RBShare = {
    async open(o) {
      cur = o; url = o.url || location.origin + location.pathname;
      $(".opt").hidden = !o.option; if (o.option) { $(".opt span").textContent = o.option; $(".opt input").checked = !!o.optionDefault; }
      $(".nt").textContent = o.note || ""; $(".msg").textContent = "";
      box.classList.add("show"); $("img").removeAttribute("src"); file = null;
      say("画像を作っています…");
      await new Promise(r => setTimeout(r, 30));   // 先に画面を出してから作る
      await build(); say("");
    },
  };
  $(".opt input").onchange = () => build();
  const text = () => (typeof cur.text === "function" ? cur.text($(".opt input").checked) : cur.text) || "";
  const say = m => { $(".msg").textContent = m; };
  async function copyLink() {
    try { await navigator.clipboard.writeText(text() + "\n" + url); say("文章とリンクをコピーしました。LINEなどに貼り付けてください"); }
    catch { say("コピーできませんでした"); }
  }
  // iPhoneは「タップした直後」にしか共有メニューを開けないので、待ち時間を入れずにすぐ呼ぶ。
  // 失敗したら、もう一度タップしてもらう（2回目の共有はタップなしでは開けないため）。
  box.querySelector("[data-a=share]").onclick = () => {
    if (!file) return say("画像を作っています。少し待ってからもう一度押してください");
    const withFile = navigator.canShare && navigator.canShare({ files: [file] });
    if (!navigator.share) return copyLink();
    const data = withFile ? { files: [file], text: text() + "\n" + url } : { title: "ROOT BOT", text: text(), url };
    navigator.share(data).then(() => say("共有しました")).catch(e => {
      if (e.name === "AbortError") return;
      say(`共有メニューを開けませんでした（${e.name}: ${e.message}）。「リンクだけ共有」か「画像を保存」を試してください`);
    });
  };
  box.querySelector("[data-a=link]").onclick = () => {
    if (!navigator.share) return copyLink();
    navigator.share({ title: "ROOT BOT", text: text(), url }).catch(e => {
      if (e.name !== "AbortError") say("共有メニューを開けませんでした。もう一度押すと、文章とリンクをコピーします");
      box.querySelector("[data-a=link]").onclick = copyLink;
    });
  };
  box.querySelector("[data-a=save]").onclick = () => {
    if (!file) return say("画像を作っています。少し待ってください");
    if (IOS) return say("上の画像を長押し →「写真に保存」で保存できます");   // iPhoneはダウンロードより長押しが確実
    const a = document.createElement("a"); a.href = $("img").src; a.download = file.name; document.body.append(a); a.click(); a.remove();
    say("保存しました");
  };
})();
