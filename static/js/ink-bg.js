/* ════════════════════════════════════════════════════════════════
   ink-bg.js — Canvas 水墨粒子背景
   v3: 三套主题专属氛围（宣纸/青绿/墨韵）+ Observer 优化

   宣纸(light)：墨滴晕染扩散 —— 经典水墨
   青绿(sepia)：浮翠微光升腾 —— 竹影山岚
   墨韵(dark) ：金粟星点流萤 —— 夜空墨韵
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
    this.spawnInterval = null;
    this.visible = true;
    this.lastFrameTime = 0;
    this.fpsInterval = 1000 / 30;
    this.currentTheme = 'light';

    this.init();
  }

  init() {
    this.canvas = document.createElement('canvas');
    this.canvas.style.cssText = 'position:absolute;inset:0;z-index:0;pointer-events:none;opacity:0.6';
    this.container.style.position = 'relative';
    this.container.insertBefore(this.canvas, this.container.firstChild);
    this.ctx = this.canvas.getContext('2d');
    this.resize();
    this.currentTheme = this.getTheme();
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

  getTheme() {
    return document.documentElement.dataset.theme || 'light';
  }

  getThemeColor() {
    const style = getComputedStyle(document.documentElement);
    const theme = this.currentTheme;
    if (theme === 'sepia') {
      // 青绿：使用 accent 色（青绿色调）
      return style.getPropertyValue('--accent').trim() || '#5b8c5a';
    }
    if (theme === 'dark') {
      // 墨韵：使用金色 / 浅色点缀
      const gold = style.getPropertyValue('--gold').trim();
      if (gold) return gold;
      return '#c9a96e';
    }
    // 宣纸：muted 水墨色
    const muted = style.getPropertyValue('--muted').trim();
    return muted || '#8a7d6b';
  }

  // ── 每主题配置 ──
  getThemeConfig() {
    switch (this.currentTheme) {
      case 'sepia':
        return { maxParticles: 14, spawnMs: 1800, type: 'float' };
      case 'dark':
        return { maxParticles: 22, spawnMs: 1200, type: 'star' };
      default: // light / 宣纸
        return { maxParticles: window.innerWidth <= 768 ? 4 : 8, spawnMs: 3500, type: 'ink' };
    }
  }

  // ── IntersectionObserver：hero 不可见时暂停 ──
  setupVisibilityObserver() {
    if (!('IntersectionObserver' in window)) return;
    this._visibilityObserver = new IntersectionObserver((entries) => {
      this.visible = entries[0].isIntersecting;
    }, { threshold: 0 });
    this._visibilityObserver.observe(this.container);
  }

  // ── MutationObserver：主题切换时重建氛围 ──
  setupThemeObserver() {
    this._themeObserver = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.attributeName === 'data-theme') {
          const newTheme = this.getTheme();
          if (newTheme !== this.currentTheme) {
            this.currentTheme = newTheme;
            this.particles = [];  // 清空旧粒子
            this.restartSpawning();
          }
          break;
        }
      }
    });
    this._themeObserver.observe(document.documentElement, {
      attributes: true, attributeFilter: ['data-theme']
    });
  }

  restartSpawning() {
    if (this.spawnInterval) clearInterval(this.spawnInterval);
    const cfg = this.getThemeConfig();
    this.spawnInterval = setInterval(() => {
      if (this.visible) this.spawnParticle();
    }, cfg.spawnMs);
    // 立即生成首波粒子
    for (let i = 0; i < cfg.maxParticles / 2; i++) this.spawnParticle();
  }

  // ── 粒子生成（按主题分支）──
  spawnParticle() {
    const cfg = this.getThemeConfig();
    if (this.particles.length >= cfg.maxParticles) return;
    const color = this.getThemeColor();

    switch (cfg.type) {
      case 'float':  // 青绿：微光浮升
        this.particles.push({
          type: 'float',
          x: Math.random() * this.width,
          y: this.height + 20,
          size: 1.5 + Math.random() * 3.5,
          alpha: 0.15 + Math.random() * 0.2,
          vx: (Math.random() - 0.5) * 0.3,
          vy: -(0.2 + Math.random() * 0.5),
          color,
          life: 300 + Math.random() * 400,
          age: 0,
        });
        break;

      case 'star':  // 墨韵：星点缓慢漂移
        this.particles.push({
          type: 'star',
          x: Math.random() * this.width,
          y: Math.random() * this.height,
          size: 0.5 + Math.random() * 2,
          alpha: 0.2 + Math.random() * 0.35,
          vx: (Math.random() - 0.5) * 0.15,
          vy: (Math.random() - 0.5) * 0.15,
          color,
          twinkle: Math.random() * Math.PI * 2,
          twinkleSpeed: 0.01 + Math.random() * 0.03,
        });
        break;

      default:  // 宣纸：墨滴晕染
        this.particles.push({
          type: 'ink',
          x: Math.random() * this.width,
          y: Math.random() * this.height,
          radius: 0,
          maxRadius: 80 + Math.random() * 160,
          alpha: 0.12 + Math.random() * 0.1,
          speed: 0.3 + Math.random() * 0.5,
          color,
        });
    }
  }

  // ── 帧更新（按主题分支渲染）──
  update() {
    this.ctx.clearRect(0, 0, this.width, this.height);

    for (let i = this.particles.length - 1; i >= 0; i--) {
      const p = this.particles[i];

      switch (p.type) {
        case 'float':
          p.x += p.vx;
          p.y += p.vy;
          p.age++;
          p.alpha -= 0.0004;
          if (p.alpha <= 0 || p.age >= p.life || p.y < -30) {
            this.particles.splice(i, 1);
            continue;
          }
          // 柔光小圆点 + halo
          this.ctx.beginPath();
          this.ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
          this.ctx.fillStyle = this.hexToRgba(p.color, p.alpha);
          this.ctx.fill();
          // halo
          const haloGrad = this.ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size * 4);
          haloGrad.addColorStop(0, this.hexToRgba(p.color, p.alpha * 0.3));
          haloGrad.addColorStop(1, this.hexToRgba(p.color, 0));
          this.ctx.beginPath();
          this.ctx.arc(p.x, p.y, p.size * 4, 0, Math.PI * 2);
          this.ctx.fillStyle = haloGrad;
          this.ctx.fill();
          break;

        case 'star':
          p.x += p.vx;
          p.y += p.vy;
          p.twinkle += p.twinkleSpeed;
          if (p.x < -10) p.x = this.width + 10;
          if (p.x > this.width + 10) p.x = -10;
          if (p.y < -10) p.y = this.height + 10;
          if (p.y > this.height + 10) p.y = -10;
          const flicker = 0.5 + 0.5 * Math.sin(p.twinkle);
          const starAlpha = p.alpha * flicker;
          // 星点核心
          this.ctx.beginPath();
          this.ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
          this.ctx.fillStyle = this.hexToRgba(p.color, starAlpha);
          this.ctx.fill();
          // 十字辉光（更亮时可见）
          if (flicker > 0.8) {
            const glow = this.ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.size * 8);
            glow.addColorStop(0, this.hexToRgba(p.color, p.alpha * 0.4));
            glow.addColorStop(1, this.hexToRgba(p.color, 0));
            this.ctx.beginPath();
            this.ctx.arc(p.x, p.y, p.size * 8, 0, Math.PI * 2);
            this.ctx.fillStyle = glow;
            this.ctx.fill();
          }
          break;

        default:  // ink
          p.radius += p.speed;
          p.alpha -= 0.0006;
          if (p.alpha <= 0 || p.radius >= p.maxRadius) {
            this.particles.splice(i, 1);
            continue;
          }
          const gradient = this.ctx.createRadialGradient(
            p.x, p.y, p.radius * 0.2,
            p.x, p.y, p.radius
          );
          gradient.addColorStop(0, this.hexToRgba(p.color, p.alpha));
          gradient.addColorStop(0.5, this.hexToRgba(p.color, p.alpha * 0.4));
          gradient.addColorStop(1, this.hexToRgba(p.color, 0));
          this.ctx.beginPath();
          this.ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
          this.ctx.fillStyle = gradient;
          this.ctx.fill();
      }
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
    if (this.visible && timestamp - this.lastFrameTime >= this.fpsInterval) {
      this.lastFrameTime = timestamp;
      this.update();
    }
    this.rafId = requestAnimationFrame((t) => this.animate(t));
  }

  start() {
    this.lastFrameTime = performance.now();
    this.rafId = requestAnimationFrame((t) => this.animate(t));
    this.restartSpawning();
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

window._destroyInkBg = () => {
  if (_inkInstance) { _inkInstance.destroy(); _inkInstance = null; }
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initInkBg);
} else {
  initInkBg();
}
