/* ════════════════════════════════════════════════════════════════
   shelf.js — 书架管理：加载、渲染、添加/移除
   ════════════════════════════════════════════════════════════════ */

// ── 书架加载 ──
async function loadShelf() {
  try {
    const resp = await fetch('/api/shelf');
    State.shelf = await resp.json();
    renderShelf();
    renderHomeShelf();
  } catch (e) { State.shelf = []; }
}

// ── 侧边栏书架 ──
function renderShelf() {
  const el = document.getElementById('shelfPanel');
  if (!State.shelf.length) {
    el.innerHTML = '<div class="empty" style="padding:20px"><p>书架空空如也</p></div>';
    return;
  }
  el.innerHTML = '<h3>我的书架</h3>' + State.shelf.map(b => {
    const bk = getBookKey(b);
    const progress = State.readingProgress[bk];
    let progressText = '';
    if (progress !== undefined) {
      const ch = typeof progress === 'object' ? progress.chapter : progress;
      const total = typeof progress === 'object' ? progress.total : null;
      if (total && total > 0) {
        const pct = Math.round((ch + 1) / total * 100);
        progressText = `<div class="progress-badge">${icon('ph:book-open-text')} ${pct}%（${ch+1}/${total}章）</div>`;
      } else {
        progressText = `<div class="progress-badge">${icon('ph:book-open-text')} 已读 ${ch+1} 章</div>`;
      }
    }
    return `<div class="shelf-item" onclick='openBook(${JSON.stringify(b).replace(/'/g,"&#39;")})'>
      <div class="cover">${b.cover ? `<img src="${esc(proxyUrl(b.cover, b.source_url))}" onerror="this.parentElement.innerHTML='📕'">` : icon('ph:book')}</div>
      <div class="info">
        <div class="name">${esc(b.name)}</div>
        <div class="author">${esc(b.author||'')}</div>
        ${progressText}
      </div>
    </div>`;
  }).join('');
}

// ── 首页书架 ──
function renderHomeShelf() {
  const el = document.getElementById('homeShelf');
  if (!State.shelf.length) {
    el.innerHTML = `<div class="empty"><div class="icon">${icon('ph:book-open-text')}</div><p>还没有收藏，搜索一本书试试</p></div>`;
    return;
  }
  el.innerHTML = State.shelf.map((b, i) => bookCard(b, i)).join('');
  // Motion One stagger 增强
  if (typeof motionStaggerCards === 'function') motionStaggerCards('#homeShelf');
}

// ── 收藏/取消 ──
async function toggleShelf() {
  if (!State.currentBook) return;
  try {
    const resp = await fetch('/api/shelf', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(State.currentBook)
    });
    const data = await resp.json();
    await loadShelf();
    renderDetail();
    toast(data.action === 'added' ? '已加入书架' : '已取消收藏');
  } catch (e) { toast('操作失败'); }
}
