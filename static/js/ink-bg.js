/* ════════════════════════════════════════════════════════════════
   ink-bg.js — Canvas 水墨粒子背景
   模拟墨滴在宣纸上晕染扩散的效果

   v2: IntersectionObserver 暂停 · MutationObserver 主题重绘 · 30fps
   ════════════════════════════════════════════════════════════════ */

class InkBackground {
  constructor(container) {
    this.container = container;
    this.canvas = null;
    this.ctx = null;
    this.particles = [];
    this.width = 0;
    this.height = 0;
    this.rafId = null;
    this.maxParticles = window.innerWidth <= 768 ? 4 : 8;
    this.spawnInterval = null;
    this.visible = true;          // IntersectionObserver 控制
    this.lastFrameTime = 0;
    this.fpsInterval = 1000 / 30; // 30fps 上限，节省 GPU

    this.init();
  }

  init() {
    this.canvas = document.createElement('canvas');
    this.canvas.style.cssText = 'position:absolute;inset:0;z-index:0;pointer-events:none;opacity:0.6';
    this.container.style.position = 'relative';
    this.container.insertBefore(this.canvas, this.container.firstChild);
    this.ctx = this.canvas.getContext('2d');
    this.resize();
    this.setupVisibilityObserver();
    this.setupThemeObserver();
    this.start();

    window.addEventListener('resize', () => this.resize());
  }

  resize() {
    const rect = this.container.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.width = rect.width;
    this.height = rect.height;
    this.canvas.width = rect.width * dpr;
    this.canvas.height = rect.height * dpr;
    this.canvas.style.width = rect.width + 'px';
    this.canvas.style.height = rect.height + 'px';
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  getMutedColor() {
    const style = getComputedStyle(document.documentElement);
    const muted = style.getPropertyValue('--muted').trim();
    return muted || '#8a7d6b';
  }

  // ── IntersectionObserver：hero 不可见时暂停 ──
  setupVisibilityObserver() {
    if (!('IntersectionObserver' in window)) return;
    this._visibilityObserver = new IntersectionObserver((entries) => {
      this.visible = entries[0].isIntersecting;
    }, { threshold: 0 });
    this._visibilityObserver.observe(this.container);
  }

  // ── MutationObserver：主题切换时更新粒子颜色 ──
  setupThemeObserver() {
    this._themeObserver = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.attributeName === 'data-theme' || m.attributeName === 'class') {
          // 更新所有现有粒子的颜色为新主题色
          const newColor = this.getMutedColor();
          for (const p of this.particles) {
            p.color = newColor;
          }
          break;
        }
      }
    });
    this._themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] });
  }

  spawnParticle() {
    if (this.particles.length >= this.maxParticles) return;
    const color = this.getMutedColor();
    this.particles.push({
      x: Math.random() * this.width,
      y: Math.random() * this.height,
      radius: 0,
      maxRadius: 80 + Math.random() * 160,
      alpha: 0.12 + Math.random() * 0.1,
      speed: 0.3 + Math.random() * 0.5,
      color,
    });
  }

  update() {
    this.ctx.clearRect(0, 0, this.width, this.height);
    const color = this.getMutedColor();

    for (let i = this.particles.length - 1; i >= 0; i--) {
      const p = this.particles[i];
      p.radius += p.speed;
      p.alpha -= 0.0006;

      if (p.alpha <= 0 || p.radius >= p.maxRadius) {
        this.particles.splice(i, 1);
        continue;
      }

      const gradient = this.ctx.createRadialGradient(p.x, p.y, p.radius * 0.2, p.x, p.y, p.radius);
      gradient.addColorStop(0, this.hexToRgba(p.color, p.alpha));
      gradient.addColorStop(0.5, this.hexToRgba(p.color, p.alpha * 0.4));
      gradient.addColorStop(1, this.hexToRgba(p.color, 0));

      this.ctx.beginPath();
      this.ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
      this.ctx.fillStyle = gradient;
      this.ctx.fill();
    }
  }

  hexToRgba(hex, alpha) {
    hex = hex.replace('#', '');
    if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);
    return `rgba(${r},${g},${b},${Math.max(0, Math.min(1, alpha))})`;
  }

  animate(timestamp) {
    // 不可见时跳过绘制，但仍保持 rAF 以便恢复（低频轮询）
    if (this.visible && timestamp - this.lastFrameTime >= this.fpsInterval) {
      this.lastFrameTime = timestamp;
      this.update();
    }
    this.rafId = requestAnimationFrame((t) => this.animate(t));
  }

  start() {
    // 跳帧驱动：传入 timestamp 进行节流
    this.lastFrameTime = performance.now();
    this.rafId = requestAnimationFrame((t) => this.animate(t));
    this.spawnParticle();
    this.spawnInterval = setInterval(() => { if (this.visible) this.spawnParticle(); }, 3500);
  }

  destroy() {
    if (this.rafId) cancelAnimationFrame(this.rafId);
    if (this.spawnInterval) clearInterval(this.spawnInterval);
    if (this._visibilityObserver) this._visibilityObserver.disconnect();
    if (this._themeObserver) this._themeObserver.disconnect();
    if (this.canvas && this.canvas.parentNode) {
      this.canvas.parentNode.removeChild(this.canvas);
    }
    this.particles = [];
  }
}

// ── Auto-init ──
let _inkInstance = null;

function initInkBg() {
  const hero = document.querySelector('.hero');
  if (hero) _inkInstance = new InkBackground(hero);
}

// 暴露 destroy 给外部
window._destroyInkBg = () => { if (_inkInstance) { _inkInstance.destroy(); _inkInstance = null; } };

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initInkBg);
} else {
  initInkBg();
}
