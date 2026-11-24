const $ = id => document.getElementById(id);
const main = document.querySelector('main');
const yen = n => Math.round(n).toLocaleString('ja-JP') + ' 円';
const stamp = t => new Date(t).toLocaleString('ja-JP');
$('address').value = localStorage.getItem('rb_eth_address') || '';
const pass = localStorage.getItem('rb_pass') || '';
let cache = {}, busy = false;
function line(parent, title, detail) {
  const row = document.createElement('div');
  row.className = 'row';
  for (const value of [title, detail]) {
    const span = document.createElement('span');
    span.textContent = value;
    row.append(span);
  }
  parent.append(row);
}
function render(data) {
  let total = 0, partial = false, valued = false;
  const holdings = new Map();
  for (const [key, snapshot] of Object.entries(data)) {
    if (snapshot.time) cache[key] = snapshot;
    else if (snapshot.state !== '取得失敗') delete cache[key];
  }
  const price = symbol => symbol === 'JPY' ? 1 : cache.quotes?.rates[symbol];
  $('accounts').replaceChildren();
  for (const [key, name] of [['bank','bitbank'],['eth','MetaMask · ETH']]) {
    const snapshot = cache[key];
    if (!snapshot) { partial = true; line($('accounts'), name, data[key].state); continue; }
    let sum = 0, missing = false, hasValue = snapshot.rows.length === 0;
    for (const {symbol, qty} of snapshot.rows) {
      if (!qty) continue;
      holdings.set(symbol, (holdings.get(symbol) || 0) + qty);
      if (price(symbol)) { sum += qty * price(symbol); hasValue = true; }
      else missing = true;
    }
    if (!missing) hasValue = true;
    valued ||= hasValue;
    partial ||= missing;
    total += sum;
    line($('accounts'), name, (hasValue ? yen(sum) : '—') + (missing ? '（未評価あり）' : '') + ' · ' +
      (data[key].state ? '前回値 ' : '') + stamp(snapshot.time));
  }
  $('assets').replaceChildren();
  for (const [symbol,qty] of [...holdings].sort((a,b)=>b[1]*(price(b[0])||0)-a[1]*(price(a[0])||0))) {
    const amount = qty * price(symbol);
    line($('assets'), symbol + ' ' + qty.toLocaleString('ja-JP',{maximumSignificantDigits:9}),
      Number.isFinite(amount) ? yen(amount) + (total ? ' · '+(amount/total*100).toFixed(1)+'%' : '') : '価格未取得');
  }
  $('label').textContent = partial ? '取得できた資産の小計' : '実資産合計';
  $('total').textContent = valued ? yen(total) : '—';
  $('quote').textContent = cache.quotes ? (data.quotes.state ? '前回の価格 ' : '価格取得 ') + stamp(cache.quotes.time) : '価格未取得';
  main.hidden = false;
}
async function refresh() {
  if (busy) return;
  busy = true; $('wallet').querySelector('button').disabled = true; $('refresh').disabled = true;
  try {
    const response = await fetch('/api/portfolio?'+new URLSearchParams({address:localStorage.getItem('rb_eth_address') || ''}),{headers:{'x-dash-pass':pass}, cache:'no-store'});
    if (!response.ok) throw Error();
    const data = await response.json();
    render(data);
    $('status').textContent = '表示中は60秒ごとに更新';
  } catch { $('status').textContent = '通信できません。表示中の金額は前回取得時の値です。'; }
  finally { $('wallet').querySelector('button').disabled = false; busy = false; $('refresh').disabled = false; }
}
$('wallet').onsubmit = e => {
  e.preventDefault();
  if (busy) return;
  localStorage.setItem('rb_eth_address', $('address').value.trim());
  delete cache.eth;
  main.hidden = true;
  refresh();
};
$('refresh').onclick = refresh;
setInterval(()=>{ if (!document.hidden) refresh(); },60000);
refresh();
