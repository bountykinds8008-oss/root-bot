// 以前のオフライン用の仕組みを外すための最後の版。
// 端末に古い仕組みが残っていても、これに置き換わって、保存した画面を消して自分を解除する。
// （画面の再読み込みはしない＝再読み込みの繰り返しを起こさない）
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) await caches.delete(k);
    await self.clients.claim();
    await self.registration.unregister();
  })());
});
// fetch は横取りしない（すべて普通にネットから読む）
