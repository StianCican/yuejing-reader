/* ════════════════════════════════════════════════════════════════
   settings.js — 阅读设置：字号、行高、主题、字体
   Alpine 桥接层 + 弹性动画
   ════════════════════════════════════════════════════════════════ */

// ── 初始化 ──
function applyReadingSettings() {
  const fs = S.getItem('fontSize') || '18';
  const lh = S.getItem('lineHeight') || '2';
  const theme = S.getItem('theme') || '';
  const ff = S.getItem('fontFamily') || 'serif';
  setFontSize(fs);
  setLineHeight(lh);
  setTheme(theme);
  setFontFamily(ff);
  const fontSizeVal = document.getElementById('fontSizeVal');
  const lineHeightVal = document.getElementById('lineHeightVal');
  if (fontSizeVal) fontSizeVal.textContent = fs;
  if (lineHeightVal) lineHeightVal.textContent = lh;
  // Sync theme buttons
  document.querySelectorAll('.settings-panel .theme-btns button[data-font]').forEach(b => {
    b.classList.toggle('active', b.dataset.font === ff);
  });
}

// ── 字号 ──
function setFontSize(v) {
  const el = document.getElementById('readerContent');
  if (el) el.style.fontSize = v + 'px';
  const valEl = document.getElementById('fontSizeVal');
  if (valEl) valEl.textContent = v;
  S.setItem('fontSize', v);
}
window._setFontSize = setFontSize;

// ── 行高 ──
function setLineHeight(v) {
  const lh = (v / 10).toFixed(1);
  const el = document.getElementById('readerContent');
  if (el) el.style.lineHeight = lh;
  const valEl = document.getElementById('lineHeightVal');
  if (valEl) valEl.textContent = lh;
  S.setItem('lineHeight', lh);
}
window._setLineHeight = setLineHeight;

// ── 主题切换 ──
function setTheme(t) {
  const r = document.documentElement;
  if (t === 'dark') r.setAttribute('data-theme', 'dark');
  else if (t === 'sepia') r.setAttribute('data-theme', 'sepia');
  else r.removeAttribute('data-theme');
  S.setItem('theme', t);
}
window._setTheme = setTheme;

// ── 循环切换主题 ──
function cycleTheme() {
  const cur = S.getItem('theme') || '';
  const themes = ['', 'sepia', 'dark'];
  const idx = themes.indexOf(cur);
  const next = themes[(idx + 1) % themes.length];
  setTheme(next);
  const names = {'': '宣纸', 'sepia': '青绿', 'dark': '墨韵'};
  showToast('主题：' + names[next], 'info');
}

// ── 设置面板 ──
function openSettings() {
  const alpine = window._alpine;
  if (alpine) {
    alpine.settingsOpen = true;
  } else {
    document.getElementById('settingsPanel').classList.add('show');
    document.getElementById('settingsOverlay').classList.add('show');
  }
}

function closeSettings() {
  const alpine = window._alpine;
  if (alpine) {
    alpine.settingsOpen = false;
  } else {
    document.getElementById('settingsPanel').classList.remove('show');
    document.getElementById('settingsOverlay').classList.remove('show');
  }
}

// ── 字体切换 ──
function setFontFamily(ff, el) {
  const reader = document.getElementById('readerContent');
  const fonts = {
    'serif': 'var(--font-serif)',
    'sans': 'var(--font-sans)',
    'kai': 'var(--font-calligraphy)',
  };
  if (reader) reader.style.fontFamily = fonts[ff] || fonts['serif'];
  S.setItem('fontFamily', ff);
  document.querySelectorAll('.font-btns button').forEach(b => {
    b.classList.toggle('active', b.dataset.font === ff);
  });
}
window._setFontFamily = setFontFamily;

// ── 移动端：点击阅读区边缘翻页 ──
document.addEventListener('click', (e) => {
  if (State.currentView !== 'reader') return;
  if (e.target.closest('button') || e.target.closest('input') || e.target.closest('.settings-panel')) return;
  if (window.getSelection()?.toString()) return;
  const x = e.clientX;
  const w = window.innerWidth;
  if (x < w * 0.15) { navChapter(-1); return; }
  if (x > w * 0.85) { navChapter(1); return; }
});
