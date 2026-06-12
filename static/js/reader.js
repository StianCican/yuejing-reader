/* ════════════════════════════════════════════════════════════════
   reader.js — 阅读器：章节加载、正文渲染、快捷键
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
  if (idx < 0 || idx >= State.chapters.length) return;
  State.currentChapterIdx = idx;
  const ch = State.chapters[idx];
  showView('loading');
  document.getElementById('loadingText').textContent = `正在加载「${ch.name}」...`;
  try {
    const params = new URLSearchParams({source: State.currentBook.source_url, url: ch.url});
    const resp = await fetch(`/api/chapter?${params}`);
    const data = await resp.json();
    if (data.error) throw new Error(data.error);

    const readerContent = document.getElementById('readerContent');
    readerContent.className = 'reader-content';
    document.getElementById('chapterTitle').textContent = ch.name;

    // 按内容类型路由
    const ctype = data.content_type || 'text';
    if (ctype === 'comic') {
      renderComicReader(data.images || [], ch.name, data.source_url || State.currentBook.source_url);
    } else {
      readerContent.innerHTML = renderContent(data.content);
    }

    const isFirst = idx <= 0;
    const isLast = idx >= State.chapters.length - 1;
    document.getElementById('prevChapter').disabled = isFirst;
    document.getElementById('nextChapter').disabled = isLast;
    const pb = document.getElementById('prevChapterBottom');
    const nb = document.getElementById('nextChapterBottom');
    if (pb) pb.disabled = isFirst;
    if (nb) nb.disabled = isLast;
    // 更新进度条
    const progressFill = document.getElementById('progressFill');
    if (progressFill) {
      progressFill.style.width = ((idx + 1) / State.chapters.length * 100) + '%';
    }
    showView('reader');
    document.getElementById('content').scrollTop = 0;
    // 保存阅读进度
    if (State.currentBook) {
      const bk = getBookKey(State.currentBook);
      saveProgress(bk, idx, State.chapters.length);
      restoreScrollPos(bk);
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
  if (State.currentBook) saveScrollPos(getBookKey(State.currentBook));
  readChapter(State.currentChapterIdx + dir);
}

// ── 键盘快捷键 ──
document.addEventListener('keydown', (e) => {
  if (State.currentView !== 'reader') return;
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  switch (e.key) {
    case 'ArrowLeft':  navChapter(-1); break;
    case 'ArrowRight': navChapter(1);  break;
    case 'Escape':
      if (State.currentBook) saveScrollPos(getBookKey(State.currentBook));
      showDetail();
      break;
    case 't':          cycleTheme();   break;
    // 漫画模式：上下翻页
    case 'ArrowDown':  comicScrollPage(1);  break;
    case 'ArrowUp':    comicScrollPage(-1); break;
    case ' ':          comicScrollPage(1); e.preventDefault(); break;
  }
});
