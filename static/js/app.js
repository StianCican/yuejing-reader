/* ════════════════════════════════════════════════════════════════
   app.js — 全局状态、导航、搜索、详情、书源管理
   ════════════════════════════════════════════════════════════════ */

// ── State ──
let currentView = 'home';
let currentBook = null;
let chapters = [];
let currentChapterIdx = -1;
let shelf = [];
let sources = [];
let readingProgress = {}; // { bookKey: chapterIdx }
const S = localStorage;

// ── 图片代理 ──
function proxyUrl(url, referer) {
  if (!url || !url.startsWith('http')) return url;
  // 本地/相对路径无需代理
  if (url.startsWith('/') || url.startsWith(window.location.origin)) return url;
  let proxy = '/api/proxy?url=' + encodeURIComponent(url);
  if (referer) proxy += '&referer=' + encodeURIComponent(referer);
  return proxy;
}

// ── 阅读进度存储 ──
function getBookKey(b) {
  return b.source_url + '|' + b.book_url;
}
function loadProgress() {
  try { readingProgress = JSON.parse(S.getItem('readingProgress') || '{}'); } catch(e) { readingProgress = {}; }
}
function saveProgress(bookKey, chapterIdx) {
  readingProgress[bookKey] = chapterIdx;
  S.setItem('readingProgress', JSON.stringify(readingProgress));
}

// ── Init ──
window.addEventListener('load', () => {
  loadShelf();
  loadSources();
  loadProgress();
  applyReadingSettings();
});

// ── Navigation ──
function showView(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const el = document.getElementById(id + 'View');
  if (el) el.classList.add('active');
  currentView = id;
}

function showHome() {
  showView('home');
  renderHomeShelf();
}

function showDetail() {
  if (currentBook) showView('detail');
}

function goBack() {
  if (currentView === 'reader') {
    showDetail();
  } else if (currentView === 'detail') {
    showView('search');
  } else if (currentView === 'search') {
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
let currentSearchType = '';  // '' / '0' / '1' / '2' / '4'

function setSearchType(t, e) {
  currentSearchType = t;
  document.querySelectorAll('.type-tab').forEach(b => b.classList.remove('active'));
  if (e && e.target) e.target.classList.add('active');
  const kw = document.getElementById('searchInput').value.trim();
  if (kw) doSearch();
}

async function doSearch() {
  const kw = document.getElementById('searchInput').value.trim();
  if (!kw) return;
  // 先切到搜索视图，显示骨架屏
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
    if (currentSearchType) url += `&type=${currentSearchType}`;
    const resp = await fetch(url);
    const results = await resp.json();
    document.getElementById('resultCount').textContent = `共 ${results.length} 条结果`;
    renderSearchResults(results);
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
  const cover = b.cover ? `<img src="${esc(proxyUrl(b.cover, b.source_url))}" onerror="this.parentElement.innerHTML='📕'">` : '📕';
  const sourceHtml = b.source_name ? `<div class="source"><span class="dot"></span>${esc(b.source_name)}</div>` : '';
  return `<div class="book-card" onclick='openBook(${JSON.stringify(b).replace(/'/g,"&#39;")})'>
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
  currentBook = b;
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
    currentBook = {...b, ...data};
    chapters = data.chapters || [];
    renderDetail();
    showView('detail');
  } catch (e) {
    toast('加载详情失败：' + e.message);
    showView('search');
  }
}

function renderDetail() {
  const b = currentBook;
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
  const inShelf = shelf.some(s => s.source_url===b.source_url && s.book_url===b.book_url);
  const bk = getBookKey(b);
  const savedIdx = readingProgress[bk];
  const hasProgress = savedIdx !== undefined && savedIdx >= 0 && savedIdx < chapters.length;
  document.getElementById('detailActions').innerHTML = `
    ${chapters.length ? `<button class="btn btn-primary" onclick="readChapter(${hasProgress ? savedIdx : 0})">${hasProgress ? '📖 继续阅读（第'+(savedIdx+1)+'章）' : '📖 开始阅读'}</button>` : ''}
    <button class="btn btn-outline" onclick="toggleShelf()">${inShelf ? '💔 取消收藏' : '❤️ 加入书架'}</button>`;
  document.getElementById('chapterCount').textContent = `📑 章节目录（${chapters.length} 章）`;
  document.getElementById('chapterList').innerHTML = chapters.map((ch, i) => {
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
    sources = await resp.json();
    renderSources();
  } catch (e) {
    document.getElementById('sourceCount').textContent = '加载源失败';
  }
}

function renderSources() {
  const total = sources.length;
  const enabled = sources.filter(s => s.enabled).length;
  const okCount = sources.filter(s => s.status === 'ok').length;
  const partialCount = sources.filter(s => s.status === 'partial').length;
  const deadCount = sources.filter(s => s.status === 'dead').length;
  const untested = sources.filter(s => !s.status).length;

  let statusHtml = `${enabled}/${total} 源已启用`;
  if (okCount > 0) statusHtml += ` · <span style="color:var(--green)">${okCount} 可用</span>`;
  if (partialCount > 0) statusHtml += ` · <span style="color:var(--amber)">${partialCount} 部分</span>`;
  if (deadCount > 0) statusHtml += ` · <span style="color:var(--red)">${deadCount} 失效</span>`;
  if (untested > 0) statusHtml += ` · <span style="color:var(--muted)">${untested} 未测</span>`;
  document.getElementById('sourceCount').innerHTML = statusHtml;

  const el = document.getElementById('sourcesPanel');
  const groups = {};
  sources.forEach(s => {
    const g = s.group || '其他';
    if (!groups[g]) groups[g] = [];
    groups[g].push(s);
  });
  let html = `<button class="btn btn-outline" style="width:100%;margin-bottom:12px" onclick="runHealthCheck()">🔍 检测源可用性</button>`;
  for (const [g, list] of Object.entries(groups)) {
    html += `<h3 style="margin:12px 0 8px;font-size:13px;color:var(--muted)">${esc(g)}（${list.length}）</h3>`;
    html += list.map(s => {
      // status: 'ok'(绿) / 'partial'(黄) / 'dead'(红) / null(灰)
      let dot = 'unknown';
      let title = '未检测';
      if (s.status === 'ok')      { dot = 'on';  title = '可用：搜索有结果'; }
      else if (s.status === 'partial') { dot = 'partial'; title = '部分：域名通但搜索状态不明'; }
      else if (s.status === 'dead')  { dot = 'off'; title = '失效：域名不可达'; }
      return `<div class="source-item" onclick="toggleSource('${esc(s.url)}')" title="${title}">
        <span class="dot ${dot}"></span>
        <span class="name">${esc(s.name)}</span>
        <span class="badge">${s.type}</span>
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
    // 阶段1：域名 ping（~5秒）
    await new Promise(r => setTimeout(r, 5000));
    await loadSources();
    // 阶段2：搜索测试（~15-20秒）
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
  } catch (e) { toast('操作失败'); }
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
