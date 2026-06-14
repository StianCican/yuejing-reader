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

// ── 侧边栏书架（Alpine x-for 已接管 #shelfPanel 渲染）──
function renderShelf() {
  // 不再使用 innerHTML 写入，避免覆盖 Alpine <template x-for>
  // 书架 DOM 由 Alpine 响应式自动维护
}

// ── 首页书架 ──
function renderHomeShelf() {
  // Alpine x-for 自动渲染，Motion One stagger 由 $watch 触发
  // 保留函数签名兼容旧调用（appState.showHome() 等）
  if (typeof motionStaggerCards === 'function') {
    // 延迟一帧确保 Alpine DOM 已更新
    requestAnimationFrame(() => motionStaggerCards('#homeShelf'));
  }
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
