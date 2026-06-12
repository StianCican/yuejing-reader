/* ════════════════════════════════════════════════════════════════
   app.js — 全局状态、导航、搜索、详情、源管理
   ════════════════════════════════════════════════════════════════ */

// ── 集中状态 ──
const S = localStorage;
const State = {
  currentView: 'home',
  currentBook: null,
  chapters: [],
  currentChapterIdx: -1,
  shelf: [],
  sources: [],
  readingProgress: {},
  searchResults: [],   // 搜索结果缓存（供事件委托使用）
  currentSearchType: '',
};

// ── 图片代理 ──
function proxyUrl(url, referer) {
  if (!url) return url;
  // 归一化：协议相对 → https
  if (url.startsWith('//')) url = 'https:' + url;
  // 非 HTTP 或本站资源不代理
  if (!url.startsWith('http')) return url;
  if (url.startsWith(window.location.origin)) return url;
  let proxy = '/api/proxy?url=' + encodeURIComponent(url);
  if (referer) proxy += '&referer=' + encodeURIComponent(referer);
  return proxy;
}

// ── 阅读进度存储 ──
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

// ── 滚动位置 ──
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

// ── Init ──
window.addEventListener('load', () => {
  loadShelf();
  loadSources();
  loadProgress();
  applyReadingSettings();
  setupEventDelegation();
});

// ── 事件委托 ──
function setupEventDelegation() {
  // 搜索结果/书架 点击事件委托
  document.getElementById('searchResults').addEventListener('click', (e) => {
    const card = e.target.closest('.book-card');
    if (!card) return;
    const idx = parseInt(card.dataset.index);
    if (!isNaN(idx) && State.searchResults[idx]) {
      openBook(State.searchResults[idx]);
    }
  });
  document.getElementById('homeShelf').addEventListener('click', (e) => {
    const card = e.target.closest('.book-card');
    if (!card) return;
    const idx = parseInt(card.dataset.index);
    if (!isNaN(idx) && State.shelf[idx]) {
      openBook(State.shelf[idx]);
    }
  });
}

// ── Navigation ──
function showView(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const el = document.getElementById(id + 'View');
  if (el) el.classList.add('active');
  State.currentView = id;
}

function showHome() {
  showView('home');
  renderHomeShelf();
}

function showDetail() {
  if (State.currentBook) showView('detail');
}

function goBack() {
  if (State.currentView === 'reader') {
    showDetail();
  } else if (State.currentView === 'detail') {
    showView('search');
  } else if (State.currentView === 'search') {
    showHome();
  } else {
    showHome();
  }
}

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('show');
}

function switchTab(tab, e) {
  document.querySelectorAll('.sidebar-nav button').forEach(b => b.classList.remove('active'));
  (e || window.event).target.classList.add('active');
  document.getElementById('shelfPanel').style.display = tab === 'shelf' ? '' : 'none';
  document.getElementById('sourcesPanel').style.display = tab === 'sources' ? '' : 'none';
}

// ── Search ──
function setSearchType(t, e) {
  State.currentSearchType = t;
  document.querySelectorAll('.type-tab').forEach(b => b.classList.remove('active'));
  if (e && e.target) e.target.classList.add('active');
  const kw = document.getElementById('searchInput').value.trim();
  if (kw) doSearch();
}

async function doSearch() {
  const kw = document.getElementById('searchInput').value.trim();
  if (!kw) return;
  showView('search');
  document.getElementById('resultCount').textContent = '搜索中...';
  document.getElementById('searchResults').innerHTML = Array(4).fill(0).map(() =>
    `<div class="skeleton-card">
      <div class="skeleton-cover"></div>
      <div class="skeleton-lines">
        <div class="skeleton-line w-60"></div>
        <div class="skeleton-line w-40"></div>
        <div class="skeleton-line w-80"></div>
      </div>
    </div>`
  ).join('');
  try {
    let url = `/api/search?q=${encodeURIComponent(kw)}`;
    if (State.currentSearchType) url += `&type=${State.currentSearchType}`;
    const resp = await fetch(url);
    State.searchResults = await resp.json();
    document.getElementById('resultCount').textContent = `共 ${State.searchResults.length} 条结果`;
    renderSearchResults(State.searchResults);
  } catch (e) {
    document.getElementById('searchResults').innerHTML = '<div class="empty"><div class="icon">❌</div><p>搜索失败，请检查后端是否运行</p></div>';
  }
}

function quickSearch(tag) {
  document.getElementById('searchInput').value = tag;
  doSearch();
}

function renderSearchResults(results) {
  const el = document.getElementById('searchResults');
  if (!results.length) {
    el.innerHTML = '<div class="empty"><div class="icon">📭</div><p>没有找到相关书籍</p></div>';
    return;
  }
  el.innerHTML = results.map((b, i) => bookCard(b, i)).join('');
}

function bookCard(b, i) {
  const typeLabels = {0: '📖 小说', 1: '🎧 听书', 2: '🎨 漫画', 3: '📁 文件', 4: '🎬 影视'};
  const typeBadge = b.source_type != null ? `<span class="type-badge">${typeLabels[b.source_type] || ''}</span>` : '';
  const cover = b.cover ? `<img src="${esc(proxyUrl(b.cover, b.source_url))}" onerror="this.parentElement.innerHTML='📕'">` : '📕';
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
    toast('加载详情失败：' + e.message);
    showView('search');
  }
}

function renderDetail() {
  const b = State.currentBook;
  const cover = b.cover ? `<img src="${esc(proxyUrl(b.cover, b.source_url))}" onerror="this.parentElement.innerHTML='📕'">` : '📕';
  document.getElementById('detailHeader').innerHTML = `
    <div class="cover"><div class="placeholder">${cover}</div></div>
    <div class="info">
      <div class="name">${esc(b.name)}</div>
      <div class="author">✍ ${esc(b.author||'未知')}</div>
      <div class="meta-row">
        ${b.kind ? `<span>📂 ${esc(b.kind)}</span>` : ''}
        ${b.word_count ? `<span>📝 ${esc(b.word_count)}</span>` : ''}
        <span>📖 来源：${esc(b.source_name||'')}</span>
      </div>
      ${b.last_chapter ? `<div class="meta-row"><span>🔄 最新：${esc(b.last_chapter)}</span></div>` : ''}
      <div class="intro">${esc(b.intro||'暂无简介')}</div>
    </div>`;
  const inShelf = State.shelf.some(s => s.source_url===b.source_url && s.book_url===b.book_url);
  const bk = getBookKey(b);
  const savedIdx = State.readingProgress[bk];
  const hasProgress = savedIdx !== undefined && savedIdx >= 0 && savedIdx < State.chapters.length;
  document.getElementById('detailActions').innerHTML = `
    ${State.chapters.length ? `<button class="btn btn-primary" onclick="readChapter(${hasProgress ? savedIdx : 0})">${hasProgress ? '📖 继续阅读（第'+(savedIdx+1)+'章）' : '📖 开始阅读'}</button>` : ''}
    <button class="btn btn-outline" onclick="toggleShelf()">${inShelf ? '💔 取消收藏' : '❤️ 加入书架'}</button>`;
  document.getElementById('chapterCount').textContent = `📑 章节目录（${State.chapters.length} 章）`;
  document.getElementById('chapterList').innerHTML = State.chapters.map((ch, i) => {
    const isCurrent = hasProgress && i === savedIdx;
    const isRead = hasProgress && i < savedIdx;
    let cls = 'chapter-item';
    if (isCurrent) cls += ' current';
    else if (isRead) cls += ' read';
    return `<div class="${cls}" onclick="readChapter(${i})">${esc(ch.name)}</div>`;
  }).join('');
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
  const total = State.sources.length;
  const enabled = State.sources.filter(s => s.enabled).length;
  const okCount = State.sources.filter(s => s.status === 'ok').length;
  const partialCount = State.sources.filter(s => s.status === 'partial').length;
  const deadCount = State.sources.filter(s => s.status === 'dead').length;
  const untested = State.sources.filter(s => !s.status).length;

  let statusHtml = `${enabled}/${total} 源已启用`;
  if (okCount > 0) statusHtml += ` · <span style="color:var(--green)">${okCount} 可用</span>`;
  if (partialCount > 0) statusHtml += ` · <span style="color:var(--amber)">${partialCount} 部分</span>`;
  if (deadCount > 0) statusHtml += ` · <span style="color:var(--red)">${deadCount} 失效</span>`;
  if (untested > 0) statusHtml += ` · <span style="color:var(--muted)">${untested} 未测</span>`;
  document.getElementById('sourceCount').innerHTML = statusHtml;

  const el = document.getElementById('sourcesPanel');
  const groups = {};
  State.sources.forEach(s => {
    const g = s.group || '其他';
    if (!groups[g]) groups[g] = [];
    groups[g].push(s);
  });
  let html = `
    <div style="display:flex;gap:8px;margin-bottom:12px">
      <input type="text" id="sourceFilterInput" placeholder="🔍 筛选源..."
        style="flex:1;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--surface);color:var(--text);font-size:13px"
        oninput="filterSources()">
      <button id="filterFlaggedBtn" class="btn btn-outline" style="white-space:nowrap;font-size:12px" onclick="filterFlagged()" title="只显示已标记源">🚩</button>
      <button class="btn btn-outline" style="white-space:nowrap;font-size:12px" onclick="runHealthCheck()" title="检测源可用性">🔍 检测</button>
    </div>`;
  if (deadCount > 0) {
    html += `<button class="btn btn-outline" style="width:100%;margin-bottom:12px;color:var(--red);border-color:var(--red);font-size:12px" onclick="batchDisableDead()">⚠️ 一键禁用 ${deadCount} 个失效源</button>`;
  }
  for (const [g, list] of Object.entries(groups)) {
    html += `<h3 style="margin:12px 0 8px;font-size:13px;color:var(--muted)">${esc(g)}（${list.length}）</h3>`;
    html += list.map(s => {
      let dot = 'unknown';
      let title = '未检测';
      if (s.status === 'ok')      { dot = 'on';  title = '可用：搜索有结果'; }
      else if (s.status === 'partial') { dot = 'partial'; title = '部分：域名通但搜索状态不明'; }
      else if (s.status === 'dead')  { dot = 'off'; title = '失效：域名不可达'; }
      const flagIcon = s.flagged ? '🔴' : '⚪';
      const flagTitle = s.flagged ? (s.flag_notes ? `已标记: ${esc(s.flag_notes)}` : '已标记（点击取消）') : '点击标记此源';
      return `<div class="source-item ${s.flagged ? 'flagged' : ''}" title="${title}">
        <span class="dot ${dot}"></span>
        <span class="name" onclick="event.stopPropagation();toggleSource('${esc(s.url)}')">${esc(s.name)}</span>
        <span class="badge">${s.type}</span>
        <span class="flag-btn" onclick="event.stopPropagation();flagSource('${esc(s.url)}')" title="${flagTitle}">${flagIcon}</span>
      </div>`;
    }).join('');
  }
  el.innerHTML = html;
}

let healthCheckRunning = false;
async function runHealthCheck() {
  if (healthCheckRunning) return;
  healthCheckRunning = true;
  toast('🔍 阶段1：检测域名连通性...');
  try {
    await fetch('/api/health_check', { method: 'POST' });
    await new Promise(r => setTimeout(r, 5000));
    await loadSources();
    toast('🔍 阶段2：测试搜索能力...');
    await new Promise(r => setTimeout(r, 15000));
    await loadSources();
    toast('✅ 检测完成——查看源列表状态点');
  } catch(e) { toast('检测失败'); }
  healthCheckRunning = false;
}

async function toggleSource(url) {
  try {
    await fetch('/api/toggle_source', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({url})
    });
    await loadSources();
    filterSources();
  } catch (e) { toast('操作失败'); }
}

// ── 源筛选 ──
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

// ── 源标记 ──
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
    toast(data.flagged ? '🚩 已标记' : '✅ 已取消标记');
  } catch(e) { toast('操作失败'); }
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

// ── 批量禁用失效源 ──
async function batchDisableDead() {
  const deadSources = State.sources.filter(s => s.status === 'dead' && s.enabled);
  if (!deadSources.length) { toast('没有可禁用的失效源'); return; }
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
  toast(`✅ 已禁用 ${count} 个失效源`);
}

// ── Utils ──
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2500);
}
