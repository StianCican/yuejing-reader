/* ════════════════════════════════════════════════════════════════
   comic_reader.js — 漫画阅读器：纵向滚动 + 懒加载
   ════════════════════════════════════════════════════════════════ */

let comicImages = [];
let comicLoaded = new Set();

function renderComicReader(images, title, sourceUrl) {
  comicImages = images || [];
  comicLoaded = new Set();
  const el = document.getElementById('readerContent');
  el.className = 'comic-reader';

  if (!comicImages.length) {
    el.innerHTML = '<div class="comic-empty"><span>🖼️</span><p>未提取到图片</p></div>';
    return;
  }

  // 构建图片占位 DOM
  let html = '';
  comicImages.forEach((url, i) => {
    html += `<div class="comic-page" id="comicPage${i}">
      <div class="comic-page-num">${i + 1} / ${comicImages.length}</div>
      <div class="comic-img-wrap">
        <div class="comic-placeholder">⏳</div>
        <img data-src="${esc(proxyUrl(url, sourceUrl))}" data-idx="${i}"
             class="comic-img lazy" onerror="this.style.display='none';this.previousElementSibling.textContent='❌'">
      </div>
    </div>`;
  });
  el.innerHTML = html;

  // 懒加载
  initComicLazyLoad();
}

function initComicLazyLoad() {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const img = entry.target;
      const idx = parseInt(img.dataset.idx);
      if (comicLoaded.has(idx)) return;
      comicLoaded.add(idx);

      const src = img.dataset.src;
      if (!src) return;
      img.onload = () => {
        img.classList.add('loaded');
        img.previousElementSibling.style.display = 'none';
      };
      img.src = src;
      observer.unobserve(img);
    });
  }, { rootMargin: '400px 0px' });

  document.querySelectorAll('.comic-img.lazy').forEach(img => observer.observe(img));
}

// ── 漫画滚动辅助：键盘翻页 ──
function comicScrollPage(dir) {
  const vh = window.innerHeight * 0.85;
  window.scrollBy({ top: dir * vh, behavior: 'smooth' });
}
