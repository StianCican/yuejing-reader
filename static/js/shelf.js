/* ════════════════════════════════════════════════════════════════
   shelf.js — 书架管理：加载、渲染、添加/移除
   ════════════════════════════════════════════════════════════════ */

// ── 书架加载 ──
async function loadShelf() {
  try {
    const resp = await fetch('/api/shelf');
    shelf = await resp.json();
    renderShelf();
    renderHomeShelf();
  } catch (e) { shelf = []; }
}

// ── 侧边栏书架 ──
function renderShelf() {
  const el = document.getElementById('shelfPanel');
  if (!shelf.length) {
    el.innerHTML = '<div class="empty" style="padding:20px"><p>书架空空如也</p></div>';
    return;
  }
  el.innerHTML = '<h3>我的书架</h3>' + shelf.map(b => {
    const bk = getBookKey(b);
    const idx = readingProgress[bk];
    const progressText = idx !== undefined ? `<div class="progress-badge">已读 ${idx+1} 章</div>` : '';
    return `<div class="shelf-item" onclick='openBook(${JSON.stringify(b).replace(/'/g,"&#39;")})'>
      <div class="cover">${b.cover ? `<img src="${esc(b.cover)}" onerror="this.parentElement.innerHTML='📕'">` : '📕'}</div>
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
  if (!shelf.length) {
    el.innerHTML = '<div class="empty"><div class="icon">📖</div><p>还没有收藏，搜索一本书试试</p></div>';
    return;
  }
  el.innerHTML = shelf.map((b, i) => bookCard(b, i)).join('');
}

// ── 收藏/取消 ──
async function toggleShelf() {
  if (!currentBook) return;
  try {
    const resp = await fetch('/api/shelf', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(currentBook)
    });
    const data = await resp.json();
    await loadShelf();
    renderDetail();
    toast(data.action === 'added' ? '❤️ 已加入书架' : '💔 已取消收藏');
  } catch (e) { toast('操作失败'); }
}
