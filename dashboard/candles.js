const chart = document.querySelector('#candles');
const label = document.querySelector('#marketStatus');
const chain = document.querySelector('#chain');
const nodes = new Map();
const svg = name => document.createElementNS('http://www.w3.org/2000/svg', name);
let generation = 0;
function render(rows) {
  const low = Math.min(...rows.map(r => r[3]));
  const high = Math.max(...rows.map(r => r[2]));
  const y = price => high === low ? 65 : 122 - (price-low)/(high-low)*114;
  const times = new Set(rows.map(r => r[0]));
  for (const [time,node] of nodes) if (!times.has(time)) {node.remove(); nodes.delete(time);}
  rows.forEach(([time,open,high,low,close], i) => {
    let node = nodes.get(time);
    if (!node) {
      node = svg('g');
      node.append(svg('path'),svg('path'));
      chart.append(node);
      nodes.set(time,node);
    }
    node.style.transform = 'translateX('+(5+i*10)+'px)';
    node.setAttribute('stroke',close >= open ? '#62e4bb' : '#ff8398');
    const [wick,body] = node.children;
    wick.setAttribute('d','M0 '+y(high)+' L0 '+y(low));
    body.setAttribute('stroke-width','5');
    body.setAttribute('stroke-linecap','round');
    body.setAttribute('d','M0 '+y(open)+' L0 '+y(close));
  });
}
async function update() {
  const ticket = ++generation;
  const symbol = chain.value === 'eth' ? 'ETH' : 'BNB';
  try {
    const r = await fetch('/api/chart?chain='+chain.value,{cache:'no-store'});
    if (!r.ok) throw Error();
    const {candles} = await r.json();
    if (ticket !== generation) return;
    render(candles);
    label.textContent = symbol+' / USDT　'+candles.at(-1)[4].toLocaleString('ja-JP')+' USDT';
  } catch {
    if (ticket === generation) label.textContent = symbol+' / USDT　相場を更新できませんでした';
  }
}
chain.addEventListener('change',()=>{nodes.clear();chart.replaceChildren();update();});
document.querySelector('#refresh').addEventListener('click',update);
setInterval(()=>{if (!document.hidden) update();},15000);
setTimeout(update,0);
