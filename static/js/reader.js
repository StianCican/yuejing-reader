/* ════════════════════════════════════════════════════════════════
   reader.js — 阅读器：章节加载、正文渲染、3D 翻页、进度
   ════════════════════════════════════════════════════════════════ */

// ── 段落式正文渲染 ──
// 诊断面板 HTML（独立函数，无论内容是否为空都可追加到内容底部）
function diagPanelHTML(diagnostics) {
  if (!diagnostics) return '';
  let h = '<div class="diagnostics-panel" style="margin-top:24px;padding:12px;background:var(--surface2);border-radius:8px;font-size:12px;color:var(--muted);line-height:1.6;border-left:3px solid var(--amber)">';
  h += '<div style="font-weight:600;margin-bottom:4px;color:var(--amber)"><iconify-icon icon="ph:warning" inline></iconify-icon> 诊断信息</div>';
  if (diagnostics.reason) h += '<div>' + esc(diagnostics.reason) + '</div>';
  if (diagnostics.raw_rule) h += '<div>规则：<code>' + esc(diagnostics.raw_rule) + '</code></div>';
  if (diagnostics.returned_as_content) h += '<div>返回内容预览：<code>' + esc(diagnostics.returned_as_content) + '</code></div>';
  h += '<div style="margin-top:4px">URL：<code style="word-break:break-all;font-size:11px">' + esc(diagnostics.ch_url || '') + '</code></div>';
  h += '</div>';
  return h;
}

function renderContent(text, diagnostics) {
  if (!text) {
    return '<div class="empty"><div class="icon"><iconify-icon icon="ph:warning-circle" inline></iconify-icon></div><p>该章节内容为空<br><small>可能是源站限制或章节已下架</small></p>' + diagPanelHTML(diagnostics) + '</div>';
  }
  const paragraphs = text.split(/\n\s*\n/);
  return paragraphs
    .map(p => `<p>${esc(p.trim()).replace(/\n/g, '<br>')}</p>`)
    .join('');
}

// ── 章节加载（含 3D 翻页动效）──
async function readChapter(idx, direction) {
  if (idx < 0 || idx >= State.chapters.length) return;
  const oldIdx = State.currentChapterIdx;
  State.currentChapterIdx = idx;
  const ch = State.chapters[idx];

  // 翻页方向：未指定时根据新旧 idx 推断
  if (direction === undefined) direction = idx > oldIdx ? 1 : -1;
  if (oldIdx < 0) direction = 1;

  const readerContent = document.getElementById('readerContent');

  // 3D 翻页：先翻出（纸张投影）
  if (oldIdx >= 0 && window._motionAnimate) {
    readerContent.classList.add('flipping');
    try {
      await window._motionAnimate(
        readerContent,
        { transform: `rotateY(${direction * 90}deg)`, opacity: [1, 0.3] },
        { duration: 0.2, easing: [0.65, 0, 0.35, 1] }
      ).finished;
    } catch(e) { /* motion not available, proceed */ }
    readerContent.classList.remove('flipping');
  }

  // 加载内容
  document.getElementById('loadingText').textContent = `正在加载「${ch.name}」...`;
  try {
    const params = new URLSearchParams({source: State.currentBook.source_url, url: ch.url});
    const resp = await fetch(`/api/chapter?${params}`);
    const data = await resp.json();
    if (data.error) throw new Error(data.error);

    readerContent.className = 'reader-content';
    if (typeof hideComicModeToggle === 'function') hideComicModeToggle();
    document.getElementById('chapterTitle').textContent = ch.name;

    const ctype = data.content_type || 'text';
    if (ctype === 'comic') {
      renderComicReader(data.images || [], ch.name, data.source_url || State.currentBook.source_url, data.diagnostics);
    } else {
      const contentLen = (data.content || '').length;
      console.log('[reader] content length:', contentLen, 'has_diag:', !!data.diagnostics);
      readerContent.innerHTML = renderContent(data.content || '', data.diagnostics) + diagPanelHTML(data.diagnostics);
    }

    window._isFirst = idx <= 0;
    window._isLast = idx >= State.chapters.length - 1;
    document.getElementById('prevChapter').disabled = window._isFirst;
    document.getElementById('nextChapter').disabled = window._isLast;
    const pb = document.getElementById('prevChapterBottom');
    const nb = document.getElementById('nextChapterBottom');
    if (pb) pb.disabled = window._isFirst;
    if (nb) nb.disabled = window._isLast;

    // 进度条 + 章节刻度
    const pct = Math.round((idx + 1) / State.chapters.length * 100);
    const progressFill = document.getElementById('progressFill');
    if (progressFill) {
      progressFill.style.width = pct + '%';
      // 生成章节刻度（首次或章节数变化时重建）
      const ticksId = 'progressTicks';
      let ticksEl = document.getElementById(ticksId);
      if (!ticksEl || parseInt(ticksEl.dataset.total) !== State.chapters.length) {
        if (!ticksEl) {
          ticksEl = document.createElement('div');
          ticksEl.id = ticksId;
          ticksEl.className = 'reader-progress-ticks';
          progressFill.appendChild(ticksEl);
        }
        ticksEl.dataset.total = State.chapters.length;
        const total = State.chapters.length;
        const tickCount = Math.min(total, 20);
        let ticksHTML = '';
        for (let t = 1; t <= tickCount; t++) {
          ticksHTML += '<span class="reader-progress-tick" style="left:' + Math.round(t / tickCount * 100) + '%"></span>';
        }
        ticksEl.innerHTML = ticksHTML;
      }
    }
    const progressPct = document.getElementById('progressPct');
    if (progressPct) {
      progressPct.textContent = pct + '%';
      progressPct.classList.add('visible');
      clearTimeout(progressPct._hideTimer);
      progressPct._hideTimer = setTimeout(() => progressPct.classList.remove('visible'), 2000);
    }

    showView('reader');
    document.getElementById('content').scrollTop = 0;

    if (State.currentBook) {
      const bk = getBookKey(State.currentBook);
      saveProgress(bk, idx, State.chapters.length);
      restoreScrollPos(bk, idx);  // 按章节恢复滚动位置
    }
    if (window.innerWidth <= 768) {
      document.getElementById('sidebar').classList.add('hidden');
    }

  } catch (e) {
    showToast('加载章节失败：' + e.message, 'error');
    State.currentChapterIdx = oldIdx;
    showView('detail');
    return;
  }

  // 3D 翻页：翻入（纸张投影）—— 从翻出状态回到正常
  if (window._motionAnimate) {
    readerContent.style.transform = `rotateY(${-direction * 90}deg)`;
    readerContent.style.opacity = '0.3';
    readerContent.classList.add('flip-in');
    try {
      await window._motionAnimate(
        readerContent,
        { transform: [`rotateY(${-direction * 90}deg)`, 'rotateY(0deg)'], opacity: [0.3, 1] },
        { duration: 0.3, easing: [0.16, 1, 0.3, 1] }
      ).finished;
    } catch(e) {}
    readerContent.classList.remove('flip-in');
    // 确保动画结束后内容完全可见（某些 Motion 版本不会自动清除 inline style）
    readerContent.style.transform = '';
    readerContent.style.opacity = '';
  }

  // 章节读完标记
  if (window._isLast) {
    const done = document.getElementById('chapterDone');
    if (done) {
      done.classList.add('show');
      setTimeout(() => done.classList.remove('show'), 3000);
    }
  }
}

function navChapter(dir) {
  if (State.currentBook) saveScrollPos(getBookKey(State.currentBook), State.currentChapterIdx);
  readChapter(State.currentChapterIdx + dir, dir);
}

// ── 键盘快捷键 ──
document.addEventListener('keydown', (e) => {
  if (State.currentView !== 'reader') return;
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  switch (e.key) {
    case 'ArrowLeft':
      if (typeof comicMode !== 'undefined' && comicMode === 'page') comicPrevPage();
      else navChapter(-1);
      break;
    case 'ArrowRight':
      if (typeof comicMode !== 'undefined' && comicMode === 'page') comicNextPage();
      else navChapter(1);
      break;
    case 'Escape':
      if (State.currentBook) saveScrollPos(getBookKey(State.currentBook), State.currentChapterIdx);
      showDetail();
      break;
    case 't':          cycleTheme();   break;
    case 'ArrowDown':  comicScrollPage(1);  break;
    case 'ArrowUp':    comicScrollPage(-1); break;
    case ' ':          comicScrollPage(1); e.preventDefault(); break;
  }
});

// ── 初始化 Motion One ──
(async function initMotion() {
  try {
    const mod = await import('/static/js/motion-import.js');
    window._motionAnimate = mod.animate;
    window._motionStagger = mod.stagger;
    window._motionSpring = mod.spring;
  } catch(e) {
    // Motion One 不可用时降级为无翻页动画
    window._motionAnimate = null;
    window._motionStagger = null;
    window._motionSpring = null;
  }
})();
