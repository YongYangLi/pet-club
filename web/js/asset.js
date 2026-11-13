// 素材地址带上 build 号（/assets/roles/p1/idle.png?v=1.6.0-abc12345）。
//
// 为什么需要：图片是 max-age=3600 缓存的，换图后浏览器还会拿旧图看一小时。
// 把 build 挂到 query 上，build 一变 URL 就变，缓存自然失效 ——
// 不用把图片降级成 no-cache（那样每次开页面都要为 160 多个文件跑一轮 304）。
//
// build 从 HTML 的 meta 里同步取：网关渲染 index.html 时就填好了，不依赖网络。
// 直接双击文件打开（没走网关）时取不到，返回原地址，不影响本地看页面。

const BUILD = (() => {
  const el = document.querySelector('meta[name="petclub-build"]');
  return el ? (el.content || '') : '';
})();

export function assetUrl(path) {
  if (!BUILD) return path;
  return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'v=' + encodeURIComponent(BUILD);
}

export function buildId() {
  return BUILD;
}
