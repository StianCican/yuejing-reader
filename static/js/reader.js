/* ════════════════════════════════════════════════════════════════
   reader.js — 阅读器：章节加载、正文渲染、进度、快捷键
   ════════════════════════════════════════════════════════════════ */

// ── 段落式正文渲染 ──
function renderContent(text) {
  if (!text) return '<p>（内容为空）</p>';
  const paragraphs = text.split(/\n\s*\n/);
  return paragraphs
    .map(p => `<p>${esc(p.trim()).replace(/\n/g, '<br>')}</p>`)
    .join('');
}

// ── 章节加载 ──
async function readChapter(idx) {
  if (idx < 0 || idx >= chapters.length) return;
  currentChapterIdx = idx;
  const ch = chapters[idx];
  showView('loading');
  document.getElementById('loadingText').textContent = `正在加载「${ch.name}」...`;
  try {
    const params = new URLSearchParams({source: currentBook.source_url, url: ch.url});
    const resp = await fetch(`/api/chapter?${params}`);
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    document.getElementById('chapterTitle').textContent = ch.name;
    document.getElementById('readerContent').innerHTML = renderContent(data.content);
    document.getElementById('prevChapter').disabled = idx <= 0;
    document.getElementById('nextChapter').disabled = idx >= chapters.length - 1;
    // 更新进度条
    const progressFill = document.getElementById('progressFill');
    if (progressFill) {
      progressFill.style.width = ((idx + 1) / chapters.length * 100) + '%';
    }
    showView('reader');
    document.getElementById('content').scrollTop = 0;
    // 保存阅读进度
    if (currentBook) {
      const bk = getBookKey(currentBook);
      saveProgress(bk, idx);
    }
    // 阅读模式：隐藏侧边栏
    if (window.innerWidth <= 768) {
      document.getElementById('sidebar').classList.add('hidden');
    }
  } catch (e) {
    toast('加载章节失败：' + e.message);
    showView('detail');
  }
}

function navChapter(dir) {
  readChapter(currentChapterIdx + dir);
}

// ── 键盘快捷键 ──
document.addEventListener('keydown', (e) => {
  if (currentView !== 'reader') return;
  // 输入框内不拦截
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  switch (e.key) {
    case 'ArrowLeft':  navChapter(-1); break;
    case 'ArrowRight': navChapter(1);  break;
    case 'Escape':     showDetail();   break;
    case 't':          cycleTheme();   break;
  }
});
