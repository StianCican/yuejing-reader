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

    const readerContent = document.getElementById('readerContent');
    readerContent.className = 'reader-content';  // reset class
    document.getElementById('chapterTitle').textContent = ch.name;

    // 按内容类型路由
    const ctype = data.content_type || 'text';
    if (ctype === 'comic') {
      renderComicReader(data.images || [], ch.name, data.source_url || currentBook.source_url);
    } else if (ctype === 'audio') {
      renderAudioPlayer(data.audio_url, data.content || '', ch.name);
    } else {
      readerContent.innerHTML = renderContent(data.content);
    }

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

// ── 音频播放器 ──
function renderAudioPlayer(audioUrl, fallbackText, title) {
  const el = document.getElementById('readerContent');
  el.className = 'audio-reader';

  const hasAudio = audioUrl && audioUrl.startsWith('http');
  let html = `<div class="audio-player">
    <div class="audio-cover">🎧</div>
    <div class="audio-info">
      <div class="audio-title">${esc(title || '')}</div>
      <div class="audio-sub">听书 · 音频</div>
    </div>`;

  if (hasAudio) {
    html += `<audio controls autoplay class="audio-ctrl" src="${esc(audioUrl)}">
      您的浏览器不支持音频播放
    </audio>`;
  } else {
    html += `<div class="audio-no-src">⚠️ 未检测到音频地址</div>`;
  }

  html += `</div>`;

  // 如果有文本内容（歌词/文案），也展示出来
  if (fallbackText && fallbackText.trim()) {
    html += `<details class="audio-text-wrap">
      <summary>📄 文本内容</summary>
      <div class="reader-content" style="margin-top:12px">${renderContent(fallbackText)}</div>
    </details>`;
  }

  el.innerHTML = html;
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
    // 漫画模式：上下翻页
    case 'ArrowDown':  comicScrollPage(1);  break;
    case 'ArrowUp':    comicScrollPage(-1); break;
    case ' ':          comicScrollPage(1); e.preventDefault(); break;  // 空格翻页
  }
});
