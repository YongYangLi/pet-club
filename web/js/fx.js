// 纯视觉烟花：全屏 canvas，pointer-events: none，不拦任何点击。
// 不依赖角色素材，桌面端和网页端各画各的。

const COLORS = ['#ff5c8a', '#ffd166', '#6c7bff', '#4dd4ac', '#ff8c42',
  '#b388ff', '#ff6b6b', '#5ad1ff'];

let canvas = null;
let ctx = null;
let parts = [];
let raf = 0;
let started = 0;
let until = 0;
let bound = false;

function setup() {
  canvas = document.getElementById('fx-canvas');
  if (!canvas) return false;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(window.innerWidth * dpr));
  canvas.height = Math.max(1, Math.floor(window.innerHeight * dpr));
  ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!bound) {
    bound = true;
    window.addEventListener('resize', () => {
      if (canvas) setup();
    }, { passive: true });
  }
  return true;
}

function burst(cx, cy, count) {
  for (let i = 0; i < count; i++) {
    const angle = Math.random() * Math.PI * 2;
    const speed = 90 + Math.random() * 260;
    parts.push({
      x: cx,
      y: cy,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed - 40,
      life: 0,
      max: 1.1 + Math.random() * 1.1,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
      size: 1.5 + Math.random() * 2.6,
      spin: Math.random() * Math.PI,
    });
  }
}

export function playFireworks() {
  if (!canvas && !setup()) return;
  const now = performance.now();
  started = now;
  until = now + 2400;
  loop.last = 0;
  loop.second = false;
  const w = window.innerWidth;
  const h = window.innerHeight;
  burst(w * (0.22 + Math.random() * 0.16), h * (0.22 + Math.random() * 0.18), 70);
  burst(w * (0.55 + Math.random() * 0.2), h * (0.18 + Math.random() * 0.2), 80);
  burst(w * (0.38 + Math.random() * 0.24), h * (0.4 + Math.random() * 0.18), 60);
  if (!raf) {
    raf = requestAnimationFrame(loop);
  }
}

function loop(now) {
  if (!ctx) {
    raf = 0;
    return;
  }
  const w = window.innerWidth;
  const h = window.innerHeight;
  ctx.clearRect(0, 0, w, h);

  const dt = Math.min(0.05, (now - (loop.last || now)) / 1000);
  loop.last = now;

  // 开场后再补两朵，让画面不只是一下子
  if (now - started > 650 && !loop.second && now < until - 500) {
    loop.second = true;
    burst(w * (0.15 + Math.random() * 0.7), h * (0.2 + Math.random() * 0.35), 60);
  }

  const alive = [];
  for (const p of parts) {
    p.life += dt;
    if (p.life >= p.max) continue;
    p.vy += 260 * dt;
    p.vx *= 0.992;
    p.x += p.vx * dt;
    p.y += p.vy * dt;
    p.spin += dt * 6;
    const k = 1 - p.life / p.max;
    ctx.globalAlpha = Math.max(0, k);
    ctx.fillStyle = p.color;
    if (p.size > 3) {
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.spin);
      ctx.fillRect(-p.size, -p.size / 2, p.size * 2, p.size);
      ctx.restore();
    } else {
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      ctx.fill();
    }
    alive.push(p);
  }
  ctx.globalAlpha = 1;
  parts = alive;

  if (now < until || parts.length) {
    raf = requestAnimationFrame(loop);
  } else {
    raf = 0;
    loop.last = 0;
    loop.second = false;
    ctx.clearRect(0, 0, w, h);
  }
}
