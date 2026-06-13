/* ════════════════════════════════════════════════════════════════
   comic_reader.js — 漫画阅读器：纵向滚动 + 懒加载
   ════════════════════════════════════════════════════════════════ */

let comicImages = [];
let comicLoaded = new Set();

function renderComicReader(images, title, sourceUrl, diagnostics) {
  comicImages = images || [];
  comicLoaded = new Set();
  const el = document.getElementById('readerContent');
  el.className = 'comic-reader';

  // 诊断面板
  renderDiagnosticsPanel(diagnostics);

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

// ── 诊断面板 ──
function renderDiagnosticsPanel(diagnostics) {
  // 移除旧面板
  const old = document.querySelector('.diagnostics-trigger,.diagnostics-panel');
  if (old) old.remove();

  if (!diagnostics) return;

  const warnings = diagnostics.warnings || [];
  const hasWarning = warnings.length > 0;
  const antiBot = diagnostics.anti_bot;
  const isBlocked = antiBot && antiBot.blocked;

  // 构建面板 HTML
  let panelHtml = '<div class="diagnostics-panel-inner">';
  panelHtml += '<div class="diagnostics-panel-header">';
  panelHtml += '<span>' + (isBlocked ? '🛡 诊断 · 异常' : (hasWarning ? '⚠ 诊断 · 警告' : 'ℹ 诊断'));
  panelHtml += '</span>';
  panelHtml += '<button class="diagnostics-close" onclick="closeDiagnostics()">✕</button>';
  panelHtml += '</div>';
  panelHtml += '<div class="diagnostics-panel-body">';

  // 统计
  panelHtml += '<div class="diag-section"><div class="diag-label">提取路径</div>';
  panelHtml += '<div class="diag-value">' + esc(diagnostics.path_label || '—') + '</div></div>';

  panelHtml += '<div class="diag-section"><div class="diag-label">数据类型</div>';
  panelHtml += '<div class="diag-value">' + esc(diagnostics.data_type || '—') + '</div></div>';

  if (diagnostics.content_rule_snippet) {
    panelHtml += '<div class="diag-section"><div class="diag-label">Content 规则</div>';
    panelHtml += '<div class="diag-value mono">' + esc(diagnostics.content_rule_snippet) + '</div></div>';
  }
  if (diagnostics.content_rule_result != null && diagnostics.content_rule_result !== undefined) {
    panelHtml += '<div class="diag-section"><div class="diag-label">Content 规则输出</div>';
    panelHtml += '<div class="diag-value mono ' + (diagnostics.content_rule_result ? '' : 'dim') + '">' +
      (diagnostics.content_rule_result ? esc(diagnostics.content_rule_result) : '(空 — JS 规则未匹配或返回 null)') +
      '</div></div>';
  }

  panelHtml += '<div class="diag-section"><div class="diag-label">图片统计</div>';
  panelHtml += '<div class="diag-value">原始 <b>' + diagnostics.raw_count + '</b> → 保留 <b>' +
    diagnostics.filtered_count + '</b>（垃圾: ' + (diagnostics.junk_dropped || 0) + '）</div></div>';

  // 原始图片 URL 样本
  var rawSamples = diagnostics.sample_raw_urls || [];
  if (rawSamples.length) {
    panelHtml += '<div class="diag-section"><div class="diag-label">原始图片 URL（前 ' + rawSamples.length + '）</div>';
    rawSamples.forEach(function(u) {
      panelHtml += '<div class="diag-value mono dim" style="font-size:10px;margin-top:2px">' + esc(u) + '</div>';
    });
    panelHtml += '</div>';
  }
  // 过滤后图片 URL 样本（仅当有过滤时显示）
  var filtSamples = diagnostics.sample_filtered_urls || [];
  if (filtSamples.length && (diagnostics.junk_dropped > 0 || diagnostics.raw_count === 0)) {
    panelHtml += '<div class="diag-section"><div class="diag-label">过滤后图片 URL（前 ' + filtSamples.length + '）</div>';
    filtSamples.forEach(function(u) {
      panelHtml += '<div class="diag-value mono" style="font-size:10px;margin-top:2px">' + esc(u) + '</div>';
    });
    panelHtml += '</div>';
  }

  // 警告
  if (warnings.length) {
    panelHtml += '<div class="diag-section diag-warnings"><div class="diag-label">⚠ 警告</div>';
    warnings.forEach(w => {
      panelHtml += '<div class="diag-warning-item">' + esc(w) + '</div>';
    });
    panelHtml += '</div>';
  }

  // 反爬
  if (isBlocked) {
    panelHtml += '<div class="diag-section diag-antibot"><div class="diag-label">🛡 拦截检测</div>';
    panelHtml += '<div class="diag-value"><b>' + esc(antiBot.block_type) + '</b></div>';
    panelHtml += '<div class="diag-value dim">' + esc(antiBot.evidence || '') + '</div>';
    if (antiBot.suggested_fix) {
      panelHtml += '<div class="diag-value fix">💡 ' + esc(antiBot.suggested_fix) + '</div>';
    }
    panelHtml += '</div>';
  }

  // 重试
  if (diagnostics.retry_attempted) {
    panelHtml += '<div class="diag-section"><div class="diag-label">🔄 绕过重试</div>';
    panelHtml += '<div class="diag-value">' + (diagnostics.retry_success ? '✅ 成功' : '❌ 失败') + '</div></div>';
  }

  panelHtml += '</div></div>';

  // 创建触发器按钮
  const trigger = document.createElement('div');
  trigger.className = 'diagnostics-trigger' + (hasWarning ? ' has-warning' : '') + (isBlocked ? ' is-blocked' : '');
  trigger.title = isBlocked ? '检测到反爬拦截' : (hasWarning ? '图片提取异常' : '诊断信息');
  trigger.innerHTML = isBlocked ? '🛡' : (hasWarning ? '⚠' : 'ℹ');
  trigger.onclick = toggleDiagnostics;

  // 创建面板
  const panel = document.createElement('div');
  panel.className = 'diagnostics-panel';
  panel.innerHTML = panelHtml;

  document.body.appendChild(trigger);
  document.body.appendChild(panel);
}

function toggleDiagnostics() {
  const panel = document.querySelector('.diagnostics-panel');
  if (panel) panel.classList.toggle('open');
}

function closeDiagnostics() {
  const panel = document.querySelector('.diagnostics-panel');
  if (panel) panel.classList.remove('open');
}

// 点击面板外部关闭
document.addEventListener('click', function(e) {
  if (document.querySelector('.diagnostics-panel.open')) {
    if (!e.target.closest('.diagnostics-panel') && !e.target.closest('.diagnostics-trigger')) {
      closeDiagnostics();
    }
  }
});
