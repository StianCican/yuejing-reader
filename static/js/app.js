/* ════════════════════════════════════════════════════════════════
   app.js — Alpine 全局状态、导航、搜索、详情、源管理、Toast
   ════════════════════════════════════════════════════════════════ */

// ── 启动诊断探针：把 JS 异常显示到页面上 ──
window.addEventListener('error', (e) => {
  try {
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#c62828;color:#fff;padding:12px;z-index:99999;font:12px/1.4 monospace;white-space:pre-wrap;max-height:40vh;overflow:auto';
    box.textContent = '[JS 错误] ' + (e.message || e.error?.message || 'unknown') + '\n' + (e.filename || '') + ':' + (e.lineno || 0) + '\n' + (e.error?.stack || '');
    document.body.appendChild(box);
  } catch (_) {}
});
window.addEventListener('unhandledrejection', (e) => {
  try {
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#ef6c00;color:#fff;padding:12px;z-index:99999;font:12px/1.4 monospace;white-space:pre-wrap;max-height:40vh;overflow:auto';
    box.textContent = '[Promise 错误] ' + (e.reason?.message || e.reason || 'unknown') + '\n' + (e.reason?.stack || '');
    document.body.appendChild(box);
  } catch (_) {}
});

const S = (() => {
  try {
    const t = '__test__';
    localStorage.setItem(t, t);
    localStorage.removeItem(t);
    return localStorage;
  } catch (_) {
    // Edge 严格模式 / 隐私模式 / 沙箱可能拒绝 localStorage 访问 —— 用内存兜底
    console.warn('[probe] localStorage 不可用，启用内存兜底');
    const m = new Map();
    return {
      getItem: k => m.has(k) ? m.get(k) : null,
      setItem: (k, v) => m.set(k, String(v)),
      removeItem: k => m.delete(k),
      clear: () => m.clear(),
    };
  }
})();
let toastId = 0;

// ── Alpine 全局状态 ──
// 关键：不能用 alpine:init 监听器 —— alpinejs.min.js 是 defer，按文档顺序在 app.js
// 之后执行，所以等浏览器解析到这一行时，Alpine 还没加载、还没派发过任何事件；
// 但 Alpine 一旦加载就会立刻 dispatch 'alpine:init'，listener 错过这次再也没机会
// 触发。改成定义一个 alpineRegister 函数，等下面 typeof Alpine 检查到对象后立刻
// 同步调用，把 appState 数据工厂注册上去。
function registerAlpineComponents() {
  console.log('[probe] registering Alpine components');
  Alpine.data('appState', () => ({
    currentView: 'home',
    sidebarOpen: false,
    sidebarTab: 'shelf',
    settingsOpen: false,
    toasts: [],

    // 当前阅读上下文 & 列表数据（由 Alpine.reactive State 驱动）
    get currentBook() { return window.State?.currentBook; },
    get chapters() { return window.State?.chapters || []; },
    get currentChapterIdx() { return window.State?.currentChapterIdx ?? -1; },
    get searchResults() { return window.State?.searchResults || []; },
    get shelf() { return window.State?.shelf || []; },
    get sources() { return window.State?.sources || []; },

    // 书卡 HTML 桥接（供 x-for + x-html 使用）
    bookCardHTML(b, i) {
      if (typeof bookCard === 'function') return bookCard(b, i);
      return '';
    },

    // 章节列表辅助（供 x-for 使用）
    get chapterDiagHTML() { return window.State?._chapterDiagHTML || ''; },
    get chapterSavedIdx() {
      const b = window.State?.currentBook;
      if (!b) return -1;
      const bk = getBookKey(b);
      const saved = window.State?.readingProgress[bk];
      return (saved !== undefined && saved >= 0) ? saved : -1;
    },
    isChapterRead(i) { return this.chapterSavedIdx >= 0 && i < this.chapterSavedIdx; },
    isChapterCurrent(i) { return this.chapterSavedIdx >= 0 && i === this.chapterSavedIdx; },
    readChapter(idx) { if (typeof window.readChapter === 'function') window.readChapter(idx); },

    // 侧边栏书架辅助（供 x-for 使用）
    shelfProgressHTML(b) {
      const bk = getBookKey(b);
      const progress = window.State?.readingProgress[bk];
      if (progress === undefined) return '';
      const ch = typeof progress === 'object' ? progress.chapter : progress;
      const total = typeof progress === 'object' ? progress.total : null;
      if (total && total > 0) {
        const pct = Math.round((ch + 1) / total * 100);
        return icon('ph:book-open-text') + ' ' + pct + '%（' + (ch+1) + '/' + total + '章）';
      }
      return icon('ph:book-open-text') + ' 已读 ' + (ch+1) + ' 章';
    },
    shelfCoverHTML(b) {
      if (b.cover) return '<img src="' + esc(proxyUrl(b.cover, b.source_url)) + '" onerror="this.parentElement.innerHTML=&quot;📕&quot;">';
      return icon('ph:book');
    },

    // 源列表辅助（供 x-for 使用）
    sourceFilterQuery: '',
    sourceFlaggedOnly: false,
    get sourceDeadCount() {
      return (window.State?.sources || []).filter(s => s.status === 'dead').length;
    },
    get groupedSources() {
      const sources = window.State?.sources || [];
      const q = (this.sourceFilterQuery || '').toLowerCase();
      const flaggedOnly = this.sourceFlaggedOnly;
      let filtered = sources;
      if (flaggedOnly) filtered = filtered.filter(s => s.flagged);
      if (q) filtered = filtered.filter(s => (s.name || '').toLowerCase().includes(q));
      const groups = {};
      filtered.forEach(s => {
        const g = s.group || '其他';
        if (!groups[g]) groups[g] = [];
        groups[g].push(s);
      });
      return Object.entries(groups).map(([name, items]) => ({ name, items }));
    },
    sourceDotClass(s) {
      if (s.status === 'ok') return 'on';
      if (s.status === 'partial') return 'partial';
      if (s.status === 'dead') return 'off';
      return 'unknown';
    },
    sourceStatusTitle(s) {
      if (s.status === 'ok') return '可用：搜索有结果';
      if (s.status === 'partial') return '部分：域名通但搜索状态不明';
      if (s.status === 'dead') return '失效：域名不可达';
      return '未检测';
    },
    sourceFlagIcon(s) { return s.flagged ? icon('ph:circle-fill') : icon('ph:circle'); },
    sourceFlagTitle(s) { return s.flagged ? (s.flag_notes ? '已标记: ' + esc(s.flag_notes) : '已标记（点击取消）') : '点击标记此源'; },
    toggleSource(url) { if (typeof window.toggleSource === 'function') window.toggleSource(url); },
    flagSource(url) { if (typeof window.flagSource === 'function') window.flagSource(url); },
    runHealthCheck() { if (typeof window.runHealthCheck === 'function') window.runHealthCheck(); },
    batchDisableDead() { if (typeof window.batchDisableDead === 'function') window.batchDisableDead(); },
    toggleFlaggedFilter() { this.sourceFlaggedOnly = !this.sourceFlaggedOnly; },

    init() {
      window._alpine = this;
      // app.js 自身定义的函数，可直接调用
      loadSources();
      loadProgress();
      setupTopbarScroll();
      setupRippleEffect();
      setupBookCardTilt();
      // 跨文件函数（shelf.js / settings.js 在 app.js 之后加载），
      // 等 DOMContentLoaded 时所有 defer 脚本已就绪再调
      if (document.readyState === 'loading') {
        window.addEventListener('DOMContentLoaded', () => {
          if (typeof loadShelf === 'function') loadShelf();
          if (typeof applyReadingSettings === 'function') applyReadingSettings();
        });
      } else {
        if (typeof loadShelf === 'function') loadShelf();
        if (typeof applyReadingSettings === 'function') applyReadingSettings();
      }

      // $watch: 搜索/书架数据变化后触发 Motion One stagger
      this.$watch('searchResults', () => {
        this.$nextTick(() => {
          if (typeof motionStaggerCards === 'function') motionStaggerCards('#searchResults');
        });
      });
      this.$watch('shelf', () => {
        this.$nextTick(() => {
          if (typeof motionStaggerCards === 'function') motionStaggerCards('#homeShelf');
        });
      });
    },

    // ── Navigation ──
    showHome() {
      this.currentView = 'home';
      renderHomeShelf();
    },
    showView(id) {
      State.currentView = id;
      this.currentView = id;
      if (id !== 'reader') {
        document.querySelectorAll('.diagnostics-trigger,.diagnostics-panel').forEach(d => d.remove());
        if (typeof hideComicModeToggle === 'function') hideComicModeToggle();
      }
    },
    showDetail() {
      if (State.currentBook) this.currentView = 'detail';
    },
    openBook(b) { window.openBook(b); },
    goBack() {
      if (this.currentView === 'reader') this.showDetail();
      else if (this.currentView === 'detail') this.currentView = 'search';
      else if (this.currentView === 'search') this.showHome();
      else this.showHome();
    },

    // ── Search ──
    setSearchType(t, el) {
      State.currentSearchType = t;
      document.querySelectorAll('.type-tab').forEach(b => b.classList.remove('active'));
      if (el) el.classList.add('active');
      const kw = document.getElementById('searchInput').value.trim();
      if (kw) window.doSearch();
    },
    doSearch() { window.doSearch(); },
    quickSearch(tag) {
      document.getElementById('searchInput').value = tag;
      window.doSearch();
    },

    // ── Reader helpers ──
    navChapter(dir) { window.navChapter(dir); },
    openSettings() { this.settingsOpen = true; },
    closeSettings() { this.settingsOpen = false; },

    // ── Settings hooks（delegate to settings.js）──
    setFontSize(v) { window._setFontSize(v); },
    setLineHeight(v) { window._setLineHeight(v); },
    setFontFamily(ff, el) { window._setFontFamily(ff, el); },
    setTheme(t) { window._setTheme(t); },
    setReadingWidth(w) { window._setReadingWidth?.(w); },
    toggleParagraphIndent() {
      const btn = document.getElementById('indentToggle');
      const on = !btn?.classList.contains('active');
      window._setParagraphIndent?.(on);
      const stateEl = document.getElementById('indentState');
      if (stateEl) stateEl.textContent = on ? '开' : '关';
    },
    setParagraphSpacing(v) { window._setParagraphSpacing?.(v); },
  }));
}

// ── Legacy State（Alpine.reactive 驱动，与 appState 双向同步）──
console.log('[probe] before Alpine.reactive, typeof Alpine =', typeof Alpine);
if (typeof Alpine === 'undefined') {
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#c62828;color:#fff;padding:12px;z-index:99999;font:12px/1.4 monospace';
  box.textContent = '[致命] Alpine 未定义 — alpinejs.min.js 可能加载失败或顺序错';
  document.body && document.body.appendChild(box);
  throw new Error('Alpine 未加载');
}
// ── Legacy State（必须在 registerAlpineComponents / Alpine.start 之前定义，
//     否则 init() → loadProgress() 访问 State 时触发 TDZ ReferenceError）──
const State = Alpine.reactive({
  currentView: 'home',
  currentBook: null,
  chapters: [],
  currentChapterIdx: -1,
  shelf: [],
  sources: [],
  readingProgress: {},
  searchResults: [],
  currentSearchType: '',
});
// 暴露到 window：Alpine appState 的 getter 通过 window.State 桥接
window.State = State;

// Alpine 已就绪 —— 立刻同步注册组件，必须在 Alpine.start() 之前
// （alpine.min.js 已打补丁屏蔽了末尾的 queueMicrotask(Alpine.start) 自启）
registerAlpineComponents();

if (typeof Alpine !== 'undefined' && typeof Alpine.start === 'function') {
  console.log('[probe] manually starting Alpine');
  Alpine.start();
} else {
  console.error('[probe] Alpine.start 不可用，页面无法初始化');
}

// ── 图片代理 ──
function proxyUrl(url, referer) {
  if (!url) return url;
  if (url.startsWith('//')) url = 'https:' + url;
  if (!url.startsWith('http')) return url;
  if (url.startsWith(window.location.origin)) return url;
  let proxy = '/api/proxy?url=' + encodeURIComponent(url);
  if (referer) proxy += '&referer=' + encodeURIComponent(referer);
  return proxy;
}

// ── 阅读进度 ──
function getBookKey(b) {
  return b.source_url + '|' + b.book_url;
}
function loadProgress() {
  try { State.readingProgress = JSON.parse(S.getItem('readingProgress') || '{}'); } catch(e) { State.readingProgress = {}; }
}
function saveProgress(bookKey, chapterIdx, totalChapters) {
  State.readingProgress[bookKey] = totalChapters ? { chapter: chapterIdx, total: totalChapters } : chapterIdx;
  S.setItem('readingProgress', JSON.stringify(State.readingProgress));
}
function saveScrollPos(bookKey) {
  const el = document.getElementById('content');
  if (el) {
    try {
      const pos = { scrollTop: el.scrollTop, timestamp: Date.now() };
      S.setItem('scrollPos_' + bookKey, JSON.stringify(pos));
    } catch(e) {}
  }
}
function restoreScrollPos(bookKey) {
  try {
    const raw = S.getItem('scrollPos_' + bookKey);
    if (raw) {
      const pos = JSON.parse(raw);
      if (Date.now() - pos.timestamp < 86400000) {
        const el = document.getElementById('content');
        if (el) el.scrollTop = pos.scrollTop;
      }
    }
  } catch(e) {}
}

// ── Topbar 滚动毛玻璃 ──
function setupTopbarScroll() {
  const content = document.getElementById('content');
  const topbar = document.getElementById('topbar');
  if (!content || !topbar) return;
  content.addEventListener('scroll', () => {
    topbar.classList.toggle('scrolled', content.scrollTop > 20);
  }, { passive: true });
}

// ── 按钮涟漪效果 ──
function setupRippleEffect() {
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn, button:not([x-data])');
    if (!btn || btn.querySelector('.ripple')) return;
    const ripple = document.createElement('span');
    ripple.className = 'ripple';
    const rect = btn.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    ripple.style.width = ripple.style.height = size + 'px';
    ripple.style.left = (e.clientX - rect.left - size / 2) + 'px';
    ripple.style.top = (e.clientY - rect.top - size / 2) + 'px';
    btn.appendChild(ripple);
    ripple.addEventListener('animationend', () => ripple.remove());
  });
}

// ── 书卡 3D tilt ──
function setupBookCardTilt() {
  document.addEventListener('mouseover', (e) => {
    const card = e.target.closest('.book-card');
    if (!card) return;
    card.classList.add('tilt-active');
  });
  document.addEventListener('mouseout', (e) => {
    const card = e.target.closest('.book-card');
    if (!card) return;
    card.classList.remove('tilt-active');
    card.style.transform = '';
  });
  document.addEventListener('mousemove', (e) => {
    const card = e.target.closest('.book-card.tilt-active');
    if (!card) return;
    const rect = card.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width - 0.5;
    const y = (e.clientY - rect.top) / rect.height - 0.5;
    card.style.transform = `perspective(800px) rotateX(${-y * 6}deg) rotateY(${x * 8}deg) translateY(-4px)`;
  });
}

// ── 事件委托（Alpine @click 接管搜索/书架，保留此处仅作降级）──
function setupEventDelegation() {
  // 搜索 & 书架卡片点击现在由 Alpine x-for @click 处理
  // 保留函数签名以兼容旧调用，不再注册 DOM 事件
}

// ── View helpers ──
function showView(id) {
  console.log('[showView] switching to', id, 'alpine:', !!window._alpine);
  State.currentView = id;
  const alpine = window._alpine;
  if (alpine) {
    alpine.currentView = id;
  }
  // DOM 兜底：无论 Alpine getter 桥是否触发了 x-show 更新，直接确保目标视图可见
  // Alpine 的 x-show 最终也要操作 style.display，这里直接做更可靠
  document.querySelectorAll('.view').forEach(v => v.style.display = 'none');
  const el = document.getElementById(id + 'View');
  if (el) el.style.display = '';
}
function showHome() {
  showView('home');
  renderHomeShelf();
}
function showDetail() {
  if (State.currentBook) showView('detail');
}
function goBack() {
  const alpine = window._alpine;
  if (alpine) {
    alpine.goBack();
  } else {
    if (State.currentView === 'reader') showDetail();
    else if (State.currentView === 'detail') showView('search');
    else showHome();
  }
}

// ── Search ──
function setSearchType(t, e) {
  State.currentSearchType = t;
  document.querySelectorAll('.type-tab').forEach(b => b.classList.remove('active'));
  const el = (e && e.target) ? e.target : e;
  if (el && el.classList) el.classList.add('active');
  const kw = document.getElementById('searchInput').value.trim();
  if (kw) doSearch();
}

async function doSearch() {
  const kw = document.getElementById('searchInput').value.trim();
  if (!kw) return;
  showView('search');
  State.searchResults = null;  // null → Alpine 显示加载中
  document.getElementById('resultCount').textContent = '搜索中...';
  try {
    let url = `/api/search?q=${encodeURIComponent(kw)}`;
    if (State.currentSearchType) url += `&type=${State.currentSearchType}`;
    const resp = await fetch(url);
    State.searchResults = await resp.json();
    document.getElementById('resultCount').textContent = `共 ${State.searchResults.length} 条结果`;
  } catch (e) {
    State.searchResults = [];
    document.getElementById('resultCount').textContent = '';
    document.getElementById('searchResults').innerHTML = '<div class="empty"><div class="icon"><iconify-icon icon="ph:x-circle" inline></iconify-icon></div><p>搜索失败，请检查后端是否运行</p></div>';
  }
}

function renderSearchResults(results) {
  // 更新响应式 State → Alpine x-for 自动渲染 DOM
  State.searchResults = results || [];
  // 空状态占位由 Alpine x-for + template 处理
  // Motion One stagger 由 appState.$watch('searchResults') 自动触发
}

function bookCard(b, i) {
  const typeLabels = {0: `${icon('ph:book-open-text')} 小说`, 1: `${icon('ph:headphones')} 听书`, 2: `${icon('ph:palette')} 漫画`, 3: `${icon('ph:folder')} 文件`, 4: `${icon('ph:film-strip')} 影视`};
  const typeBadge = b.source_type != null ? `<span class="type-badge">${typeLabels[b.source_type] || ''}</span>` : '';
  const cover = b.cover ? `<img src="${esc(proxyUrl(b.cover, b.source_url))}" onerror="this.parentElement.innerHTML='📕'">` : icon('ph:book');
  const sourceHtml = b.source_name ? `<div class="source"><span class="dot"></span>${esc(b.source_name)}${typeBadge}</div>` : '';
  return `<div class="book-card" data-index="${i}">
    <div class="cover"><div class="placeholder">${cover}</div></div>
    <div class="meta">
      <div class="name">${esc(b.name)}</div>
      <div class="author">${esc(b.author||'')}</div>
      ${sourceHtml}
      <div class="intro">${esc(b.intro||'')}</div>
    </div>
  </div>`;
}

// ── Book Detail ──
async function openBook(b) {
  State.currentBook = b;
  if (!b.book_url || !b.book_url.trim()) {
    document.getElementById('detailHeader').innerHTML = `
      <div class="cover"><div class="placeholder">${icon('ph:book')}</div></div>
      <div class="info"><div class="name">${esc(b.name)}</div><div class="author">${icon('ph:pencil-line')} ${esc(b.author||'未知')}</div></div>`;
    document.getElementById('chapterList').innerHTML = '';
    showView('detail');
    return;
  }
  showView('loading');
  document.getElementById('loadingText').textContent = '正在加载详情...';
  try {
    const params = new URLSearchParams({
      source: b.source_url, url: b.book_url,
      name: b.name||'', author: b.author||'',
      cover: b.cover||'', intro: b.intro||''
    });
    const resp = await fetch(`/api/detail?${params}`);
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    State.currentBook = {...b, ...data};
    State.chapters = data.chapters || [];
    renderDetail();
    showView('detail');
  } catch (e) {
    showToast('加载详情失败：' + e.message, 'error');
    showView('search');
  }
}

function renderDetail() {
  const b = State.currentBook;
  const cover = b.cover ? `<img src="${esc(proxyUrl(b.cover, b.source_url))}" onerror="this.parentElement.innerHTML='📕'">` : icon('ph:book');
  document.getElementById('detailHeader').innerHTML = `
    <div class="cover"><div class="placeholder">${cover}</div></div>
    <div class="info">
      <div class="name">${esc(b.name)}</div>
      <div class="author">${icon('ph:pencil-line')} ${esc(b.author||'未知')}</div>
      <div class="meta-row">
        ${b.kind ? `<span>${icon('ph:folder-open')} ${esc(b.kind)}</span>` : ''}
        ${b.word_count ? `<span>${icon('ph:note-pencil')} ${esc(b.word_count)}</span>` : ''}
        <span>${icon('ph:book-open-text')} 来源：${esc(b.source_name||'')}</span>
      </div>
      ${b.last_chapter ? `<div class="meta-row"><span>${icon('ph:arrows-clockwise')} 最新：${esc(b.last_chapter)}</span></div>` : ''}
      <div class="intro">${esc(b.intro||'暂无简介')}</div>
    </div>`;
  const inShelf = State.shelf.some(s => s.source_url===b.source_url && s.book_url===b.book_url);
  const bk = getBookKey(b);
  const savedIdx = State.readingProgress[bk];
  const hasProgress = savedIdx !== undefined && savedIdx >= 0 && savedIdx < State.chapters.length;
  document.getElementById('detailActions').innerHTML = `
    ${State.chapters.length ? '<button class="btn btn-primary" data-detail-action="read" data-idx="'+(hasProgress ? savedIdx : 0)+'">' + (hasProgress ? icon('ph:book-open-text') + ' 继续阅读（第'+(savedIdx+1)+'章）' : icon('ph:book-open-text') + ' 开始阅读') + '</button>' : ''}
    <button class="btn btn-outline" data-detail-action="shelf">${inShelf ? icon('ph:heart-break') + ' 取消收藏' : icon('ph:heart') + ' 加入书架'}</button>`;
  document.getElementById('chapterCount').innerHTML = `${icon('ph:bookmarks')} 章节目录（${State.chapters.length} 章）`;
  // 漫画源 0 章诊断 HTML —— 先计算，再写入响应式状态（TDZ 安全）
  let diagHtml = '';
  if (b.source_type === 2 && !State.chapters.length && b.diagnostics) {
    const d = b.diagnostics;
    diagHtml = '<div class="detail-diag-warning">';
    diagHtml += '<div class="detail-diag-title">' + icon('ph:warning') + ' 诊断：该漫画源未返回章节</div>';
    (d.warnings || []).forEach(w => { diagHtml += '<div class="detail-diag-item">' + esc(w) + '</div>'; });
    if (d.fetch_ok !== undefined) {
      diagHtml += '<div class="detail-diag-tech">';
      diagHtml += '<div class="diag-tech-row"><span>请求状态</span><b class="' + (d.fetch_ok ? 'ok' : 'fail') + '">' + (d.fetch_ok ? icon('ph:check') + ' 成功' : (d.fetch_error ? icon('ph:x') + ' ' + esc(d.fetch_error) : icon('ph:x') + ' 失败')) + '</b></div>';
      diagHtml += '<div class="diag-tech-row"><span>响应类型</span><b>' + esc(d.data_type || '?') + '</b></div>';
      if (d.chapter_list_rule) diagHtml += '<div class="diag-tech-row"><span>章节规则</span><code>' + esc(d.chapter_list_rule) + '</code></div>';
      diagHtml += '<div class="diag-tech-row"><span>规则匹配</span><b class="' + (d.rule_match ? 'ok' : 'fail') + '">' + (d.rule_match ? icon('ph:check') + ' 是' : icon('ph:x') + ' 否（规则未匹配到章节）') + '</b></div>';
      if (d.data_sample) diagHtml += '<div class="diag-tech-sample"><span>响应样本</span><pre>' + esc(d.data_sample) + '</pre></div>';
      diagHtml += '</div>';
    }
    if (d.anti_bot) {
      const ab = d.anti_bot;
      diagHtml += '<div class="detail-diag-antibot">';
      diagHtml += '<div class="diag-tech-row"><span>' + icon('ph:shield-check') + ' 拦截类型</span><b class="fail">' + esc(ab.block_type || '') + '</b></div>';
      diagHtml += '<div class="diag-tech-row"><span>证据</span><span class="dim">' + esc(ab.evidence || '') + '</span></div>';
      if (ab.suggested_fix) diagHtml += '<div class="diag-tech-row"><span>' + icon('ph:lightbulb') + ' 建议</span><span>' + esc(ab.suggested_fix) + '</span></div>';
      diagHtml += '</div>';
    }
    diagHtml += '<div class="detail-diag-meta">源：' + esc(d.source_name || '') + ' | 分组：' + esc(d.source_group || '') + '</div>';
    diagHtml += '</div>';
  }
  // 写入响应式状态 → Alpine x-for / x-html 自动渲染
  State._chapterDiagHTML = diagHtml;
  State.chapters = [...State.chapters];
  // 章节列表由 Alpine x-for 渲染（#chapterList 模板）
  // _chapterDiagHTML 存储诊断 HTML，chapters 数组驱动 x-for
  // isChapterCurrent / isChapterRead 控制 CSS class
  // @click="readChapter(i)" 替代 inline onclick
}

// ── Sources ──
async function loadSources() {
  try {
    const resp = await fetch('/api/sources');
    State.sources = await resp.json();
    renderSources();
  } catch (e) {
    document.getElementById('sourceCount').textContent = '加载源失败';
  }
}

function renderSources() {
  // 统计栏更新 — 源列表由 Alpine x-for + groupedSources 自动渲染
  const total = State.sources.length;
  const enabled = State.sources.filter(s => s.enabled).length;
  const okCount = State.sources.filter(s => s.status === 'ok').length;
  const partialCount = State.sources.filter(s => s.status === 'partial').length;
  const deadCount = State.sources.filter(s => s.status === 'dead').length;
  const untested = State.sources.filter(s => !s.status).length;
  let statusHtml = enabled + '/' + total + ' 源已启用';
  if (okCount > 0) statusHtml += ' · <span style="color:var(--green)">' + okCount + ' 可用</span>';
  if (partialCount > 0) statusHtml += ' · <span style="color:var(--amber)">' + partialCount + ' 部分</span>';
  if (deadCount > 0) statusHtml += ' · <span style="color:var(--red)">' + deadCount + ' 失效</span>';
  if (untested > 0) statusHtml += ' · <span style="color:var(--muted)">' + untested + ' 未测</span>';
  // 源管理面板的详细统计（id 重命名后专属）
  const panelEl = document.getElementById('sourcesPanelStats');
  if (panelEl) panelEl.innerHTML = statusHtml;
  // 侧边栏头部的简短计数（独立于详细统计）
  const headEl = document.getElementById('sourceCount');
  if (headEl) headEl.innerHTML = enabled + '/' + total + ' 源';
  // 触发 Alpine 重新计算 groupedSources
  State.sources = [...State.sources];
}

let healthCheckRunning = false;
let _healthPollTimer = null;

async function runHealthCheck() {
  if (healthCheckRunning) {
    showToast('健康检测已在运行中', 'info');
    return;
  }
  healthCheckRunning = true;

  const headEl = document.getElementById('sourceCount');
  const panelEl = document.getElementById('sourcesPanelStats');
  const pollStart = Date.now();
  let pollCount = 0;

  try {
    const resp = await fetch('/api/health_check', { method: 'POST' });
    const data = await resp.json();
    showToast(`🔍 健康检测已启动（${data.domains} 个域名），进行中...`, 'info');

    // 轮询源状态直到全部测试完毕（最多 3 分钟）
    await new Promise(resolve => {
      _healthPollTimer = setInterval(async () => {
        pollCount++;
        const elapsed = Math.round((Date.now() - pollStart) / 1000);
        try {
          const r = await fetch('/api/sources?_=' + pollCount);
          const sources = await r.json();
          const ok = sources.filter(s => s.status === 'ok').length;
          const dead = sources.filter(s => s.status === 'dead').length;
          const tested = ok + dead + sources.filter(s => s.status === 'partial').length;
          const total = sources.length;

          if (headEl) headEl.textContent = `检测中 ${elapsed}s · ${ok}可用`;
          if (panelEl) panelEl.innerHTML = `🔍 健康检测进行中... ${elapsed}s<br>${ok} 可用 · ${dead} 失效 · ${tested}/${total} 已测`;

          if (tested >= total) {
            clearInterval(_healthPollTimer);
            _healthPollTimer = null;
            resolve();
          }
        } catch(_) {}
      }, 3000);

      // 安全超时
      setTimeout(() => {
        if (_healthPollTimer) { clearInterval(_healthPollTimer); _healthPollTimer = null; resolve(); }
      }, 180000);
    });

    await loadSources();
    const elapsed = Math.round((Date.now() - pollStart) / 1000);
    showToast(`✅ 健康检测完成！耗时 ${elapsed}s`, 'success');
  } catch(e) {
    showToast('健康检测失败：' + e.message, 'error');
  }
  healthCheckRunning = false;
  if (_healthPollTimer) { clearInterval(_healthPollTimer); _healthPollTimer = null; }
}

async function toggleSource(url) {
  try {
    await fetch('/api/toggle_source', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({url})
    });
    await loadSources();
    filterSources();
  } catch (e) { showToast('操作失败', 'error'); }
}

function filterSources() {
  const q = (document.getElementById('sourceFilterInput')?.value || '').toLowerCase();
  document.querySelectorAll('#sourcesPanel .source-item').forEach(el => {
    const name = (el.querySelector('.name')?.textContent || '').toLowerCase();
    el.style.display = (!q || name.includes(q)) ? '' : 'none';
  });
  document.querySelectorAll('#sourcesPanel h3').forEach(h3 => {
    let hasVisible = false;
    let sibling = h3.nextElementSibling;
    while (sibling && sibling.tagName !== 'H3') {
      if (sibling.style.display !== 'none') { hasVisible = true; break; }
      sibling = sibling.nextElementSibling;
    }
    h3.style.display = hasVisible ? '' : 'none';
  });
}

async function flagSource(url) {
  const notes = prompt('标记备注（可选，留空仅切换标记状态）：');
  if (notes === null) return;
  try {
    const resp = await fetch('/api/flag_source', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({url, notes: notes || ''})
    });
    const data = await resp.json();
    await loadSources();
    filterSources();
    showToast(data.flagged ? '🚩 已标记' : '✅ 已取消标记', 'success');
  } catch(e) { showToast('操作失败', 'error'); }
}

function filterFlagged() {
  const btn = document.getElementById('filterFlaggedBtn');
  const active = btn.classList.toggle('active');
  document.querySelectorAll('#sourcesPanel .source-item').forEach(el => {
    if (active) {
      el.style.display = el.classList.contains('flagged') ? '' : 'none';
    } else {
      el.style.display = '';
    }
  });
  if (active) {
    const input = document.getElementById('sourceFilterInput');
    if (input) input.value = '';
  }
  filterSources();
}

async function batchDisableDead() {
  const deadSources = State.sources.filter(s => s.status === 'dead' && s.enabled);
  if (!deadSources.length) { showToast('没有可禁用的失效源', 'info'); return; }
  if (!confirm(`确定要禁用 ${deadSources.length} 个失效源吗？`)) return;
  let count = 0;
  for (const s of deadSources) {
    try {
      await fetch('/api/toggle_source', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({url: s.url})
      });
      count++;
    } catch(e) {}
  }
  await loadSources();
  filterSources();
  showToast(`✅ 已禁用 ${count} 个失效源`, 'success');
}

// ── Utils ──
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ── Iconify 图标辅助函数 ──
function icon(name) {
  return `<iconify-icon icon="${name}" inline></iconify-icon>`;
}

// ── Motion One stagger 入场动画（纯增量，不可用时退化为 CSS animation-delay）──
function motionStaggerCards(selector) {
  if (!window._motionAnimate || !window._motionStagger || !window._motionSpring) return;
  const container = document.querySelector(selector);
  if (!container) return;
  const cards = container.querySelectorAll('.book-card');
  if (!cards.length) return;
  // 暂停 CSS animation，设初始态
  cards.forEach(c => {
    c.style.animation = 'none';
    c.style.opacity = '0';
    c.style.transform = 'translateY(16px)';
  });
  // Motion One spring stagger
  window._motionAnimate(
    cards,
    { opacity: [0, 1], transform: ['translateY(16px)', 'translateY(0)'] },
    { delay: window._motionStagger(0.04), easing: window._motionSpring(), duration: 0.5 }
  );
}

// ── Toast（Alpine 管理）──
function showToast(message, type) {
  type = type || 'info';
  const icons = { success: icon('ph:check-circle'), error: icon('ph:x-circle'), info: icon('ph:info') };
  const alpine = window._alpine;
  if (!alpine) {
    // Fallback: legacy toast
    const el = document.getElementById('toast');
    if (el) { el.textContent = message; el.classList.add('show'); setTimeout(() => el.classList.remove('show'), 2500); }
    return;
  }
  const id = ++toastId;
  alpine.toasts.push({ id, message, type, icon: icons[type] || icons.info, show: true });
  setTimeout(() => {
    const idx = alpine.toasts.findIndex(t => t.id === id);
    if (idx > -1) {
      alpine.toasts[idx].show = false;
      setTimeout(() => {
        const idx2 = alpine.toasts.findIndex(t => t.id === id);
        if (idx2 > -1) alpine.toasts.splice(idx2, 1);
      }, 300);
    }
  }, 3000);
}

// Legacy compatibility
function toast(msg) { showToast(msg, 'info'); }

// ── 事件委托：详情页操作按钮（消除 inline onclick）──
// 在 document 上委托，因为 #detailActions 在详情页渲染前不存在
document.addEventListener('click', (e) => {
  const btn = e.target.closest('#detailActions button[data-detail-action]');
  if (!btn) return;
  if (btn.dataset.detailAction === 'read') readChapter(parseInt(btn.dataset.idx));
  else if (btn.dataset.detailAction === 'shelf') toggleShelf();
});
