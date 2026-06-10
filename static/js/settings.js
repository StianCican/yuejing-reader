/* ════════════════════════════════════════════════════════════════
   settings.js — 阅读设置：字号、行高、主题切换
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
  // 同步滑块和显示值
  const fontSizeVal = document.getElementById('fontSizeVal');
  const lineHeightVal = document.getElementById('lineHeightVal');
  if (fontSizeVal) fontSizeVal.textContent = fs;
  if (lineHeightVal) lineHeightVal.textContent = lh;
}

// ── 字号 ──
function setFontSize(v) {
  const el = document.getElementById('readerContent');
  if (el) el.style.fontSize = v + 'px';
  const valEl = document.getElementById('fontSizeVal');
  if (valEl) valEl.textContent = v;
  S.setItem('fontSize', v);
}

// ── 行高 ──
function setLineHeight(v) {
  const lh = (v / 10).toFixed(1);
  const el = document.getElementById('readerContent');
  if (el) el.style.lineHeight = lh;
  const valEl = document.getElementById('lineHeightVal');
  if (valEl) valEl.textContent = lh;
  S.setItem('lineHeight', lh);
}

// ── 主题切换 ──
function setTheme(t) {
  const r = document.documentElement;
  if (t === 'dark') {
    r.setAttribute('data-theme', 'dark');
  } else if (t === 'sepia') {
    r.setAttribute('data-theme', 'sepia');
  } else {
    r.removeAttribute('data-theme');
  }
  S.setItem('theme', t);
}

// ── 循环切换主题 ──
function cycleTheme() {
  const cur = S.getItem('theme') || '';
  const themes = ['', 'sepia', 'dark'];
  const idx = themes.indexOf(cur);
  const next = themes[(idx + 1) % themes.length];
  setTheme(next);
  const names = {'': '宣纸', 'sepia': '青绿', 'dark': '墨韵'};
  toast('主题：' + names[next]);
}

// ── 设置面板 ──
function openSettings() {
  document.getElementById('settingsPanel').classList.add('show');
  document.getElementById('settingsOverlay').classList.add('show');
}

function closeSettings() {
  document.getElementById('settingsPanel').classList.remove('show');
  document.getElementById('settingsOverlay').classList.remove('show');
}

// ── 字体切换 ──
function setFontFamily(ff) {
  const reader = document.getElementById('readerContent');
  const fonts = {
    'serif': 'var(--font-serif)',
    'sans': 'var(--font-sans)',
    'kai': 'var(--font-calligraphy)',
  };
  if (reader) reader.style.fontFamily = fonts[ff] || fonts['serif'];
  S.setItem('fontFamily', ff);
  // 更新按钮激活状态
  document.querySelectorAll('.font-btns button').forEach(b => {
    b.classList.toggle('active', b.dataset.font === ff);
  });
}

// 点击阅读区空白呼出设置（非文字选中时）
document.addEventListener('click', (e) => {
  if (currentView !== 'reader') return;
  const content = document.getElementById('readerContent');
  if (!content) return;
  // 判断点击在阅读区内但不是文字选中操作
  if (content.contains(e.target) && !window.getSelection()?.toString()) {
    // 双击才呼出面板，避免误触
  }
});
document.getElementById('readerContent')?.addEventListener('dblclick', (e) => {
  if (currentView === 'reader' && !window.getSelection()?.toString()) {
    openSettings();
  }
});
