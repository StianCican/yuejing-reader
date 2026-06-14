"""BaseSource — 三种源类型的公共基类

统一了 __init__、_req_headers、detail、chapter_content、chapter_images 等逻辑，
子类只需覆盖 _fetch_raw() 和 _fetch_html() 提供各自的获取方式。
"""
import re, json
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests as http_requests

from utils.http import (session, _join_url, safe_json, _parse_header,
                        _parse_inline_header, build_url, _parse_post_url)
from utils.text import (clean_text, _apply_replace_regex, _fallback_content)
from utils.images import (_extract_images_from_text, _extract_images_from_soup,
                          _find_image_urls_in_json, _normalize_image_url,
                          _filter_junk_images, _filter_junk_images_with_stats,
                          _extract_images_from_packer)
from utils.http import inspect_anti_bot
from rules.parser import _resolve_rule, resolve_tpl, jpath, walk_path
from rules.extractors import (extract_val, extract_img, extract_link,
                               _try_css_select, parse_search_results)
from rules.css_conv import css_conv
from rules.variables import _resolve_get_vars
from runtime import get_runtime


class BaseSource:
    """书源基类——子类只需实现 _fetch_raw() 和 _fetch_html()"""

    def __init__(self, src, sources_map=None):
        self.src = src
        self.base = src['bookSourceUrl'].rstrip('/')
        self.name = src.get('bookSourceName', self.base)
        self.group = src.get('bookSourceGroup', '')
        self.search_url = src.get('searchUrl', '').split(',{')[0]
        self.sr = src.get('ruleSearch', {})
        self.toc_r = src.get('ruleToc', {})
        self.content_r = src.get('ruleContent', {})
        self.bi_r = src.get('ruleBookInfo', {})
        self.sources_map = sources_map or {}
        self._headers = _parse_header(src.get('header', ''))
        # Legado bookSourceType: 0=小说 1=听书 2=漫画 3=文件 4=影视
        self.source_type = int(src.get('bookSourceType', 0) or 0)
        # 计算真实 HTTP base
        if self.base.startswith('http'):
            self.http_base = self.base
        else:
            m = re.match(r'https?://[^/]+', self.search_url)
            self.http_base = m.group(0) if m else self.base

    # ── 子类必须实现 ──

    def _fetch_raw(self, url):
        """获取原始数据，返回 (data, base_url)
        data: dict (JSON) 或 BeautifulSoup (HTML)
        base_url: 请求的实际 URL
        """
        raise NotImplementedError

    def _fetch_html(self, url):
        """获取 HTML 并解析为 BeautifulSoup"""
        raise NotImplementedError

    def search(self, kw, page=1):
        """搜索——子类实现"""
        raise NotImplementedError

    # ── 公共方法 ──

    def _req_headers(self):
        """合并全局 UA 和源专属 header"""
        h = dict(session.headers)
        h.update(self._headers)
        return h

    def _is_css_detail(self):
        """检测详情/目录规则是否使用 CSS 选择器（混合型源）"""
        for rules in [self.bi_r, self.toc_r]:
            for v in rules.values():
                if isinstance(v, str) and ('@css:' in v or 'class.' in v or '@tag' in v):
                    return True
        return False

    # ── 详情 ──

    def detail(self, book_url, search_data=None):
        """获取书籍详情 + 章节目录（模板方法，自动做 JSON/CSS 回退）"""
        self._last_detail_diag = None  # 重置
        # 空 URL 早期拦截：bookUrl 规则失效时避免 Invalid URL 异常
        if not book_url or not book_url.strip():
            sd = search_data or {}
            self._last_detail_diag = {
                'fetch_ok': False,
                'data_type': 'none',
                'chapter_list_rule': self.toc_r.get('chapterList', ''),
                'rule_match': False,
                'fetch_error': 'book_url 为空（搜索结果的 bookUrl 规则未能提取到链接）',
            }
            return {
                'name': sd.get('name', ''),
                'author': sd.get('author', ''),
                'cover': sd.get('cover', ''),
                'intro': sd.get('intro', ''),
                'chapters': [],
                '_detail_diag': self._last_detail_diag,
            }
        if self._is_css_detail():
            result = self._detail_css(book_url, search_data)
            # 有章节直接返回；无章节时继续尝试 JSON 路径回退
            if result.get('chapters'):
                return result
            # CSS 未提取到章节 —— 尝试 JSON 方式（有些源 JSON 和 CSS 混合）
            result2 = self._detail_json(book_url, search_data)
            if result2.get('chapters'):
                return result2
            # 两种方式都没有章节，返回 CSS 结果（至少保留了 name 等信息）+ 诊断
            if not result.get('chapters'):
                result['_detail_diag'] = getattr(self, '_last_detail_diag', None)
            return result

        result = self._detail_json(book_url, search_data)
        if not result.get('chapters') or self._is_css_detail():
            css_result = self._detail_css(book_url, search_data)
            if css_result.get('chapters'):
                return css_result
            if not result.get('chapters'):
                result['_detail_diag'] = getattr(self, '_last_detail_diag', None)
        if not result.get('chapters'):
            result['_detail_diag'] = getattr(self, '_last_detail_diag', None)
        return result

    def _detail_json(self, book_url, search_data=None):
        """纯 JSON API 详情解析"""
        init = self.bi_r.get('init', '').strip() if self.bi_r else ''
        if init and not init.startswith('@js'):
            full = _join_url(self.http_base, book_url)
            try:
                data, _ = self._fetch_raw(full)
                if data:
                    if init.startswith('$.'):
                        data = jpath(init, data)
                    elif init:
                        data = walk_path(data, init)
                    if not isinstance(data, dict):
                        data = search_data or {}
            except Exception:
                data = search_data or {}
        else:
            data = search_data or {}
        book = {
            'name': extract_val(data, self.bi_r.get('name', '') or self.sr.get('name', ''),
                               search_data.get('name', '') if search_data else '', base_url=self.http_base),
            'author': extract_val(data, self.bi_r.get('author', '') or self.sr.get('author', ''),
                                  search_data.get('author', '') if search_data else '', base_url=self.http_base),
            'cover': extract_img(data, self.bi_r.get('coverUrl', '') or self.sr.get('coverUrl', ''),
                                 self.http_base) or (search_data.get('cover', '') if search_data else ''),
            'intro': extract_val(data, self.bi_r.get('intro', '') or self.sr.get('intro', ''),
                                 search_data.get('intro', '') if search_data else '', base_url=self.http_base),
            'kind': extract_val(data, self.bi_r.get('kind', '') or self.sr.get('kind', ''),
                                base_url=self.http_base),
            'last_chapter': extract_val(data, self.bi_r.get('lastChapter', '') or self.sr.get('lastChapter', ''),
                                        base_url=self.http_base),
            'word_count': extract_val(data, self.bi_r.get('wordCount', '') or self.sr.get('wordCount', ''),
                                      base_url=self.http_base),
        }
        toc_url = self.toc_r.get('tocUrl', '') or self.bi_r.get('tocUrl', '')
        if toc_url:
            toc_url = _resolve_toc_url(toc_url, str(data) if data else '', self.http_base,
                                       full if init else book_url, data=data)
            if toc_url and not toc_url.startswith('http') and not toc_url.startswith('/'):
                toc_url = book_url
        else:
            toc_url = book_url
        book['chapters'] = self._chapters(toc_url)
        return book

    def _detail_css(self, book_url, search_data=None):
        """CSS/HTML 混合型源的详情解析"""
        url = _join_url(self.http_base, book_url)
        book = {}
        soup = None
        page_source = ''
        try:
            soup = self._fetch_html(url)
            if soup:
                page_source = str(soup)
        except Exception:
            pass
        bi = self.bi_r
        if soup:
            book = {
                'name': extract_val(soup, bi.get('name', '')) or (search_data.get('name', '') if search_data else ''),
                'author': extract_val(soup, bi.get('author', '')) or (search_data.get('author', '') if search_data else ''),
                'cover': extract_img(soup, bi.get('coverUrl', ''), self.http_base) or (search_data.get('cover', '') if search_data else ''),
                'intro': extract_val(soup, bi.get('intro', '')) or (search_data.get('intro', '') if search_data else ''),
                'kind': extract_val(soup, bi.get('kind', '')),
                'last_chapter': extract_val(soup, bi.get('lastChapter', '')),
            }
        if not book.get('name') and search_data:
            book = {
                'name': search_data.get('name', ''),
                'author': search_data.get('author', ''),
                'cover': search_data.get('cover', ''),
                'intro': search_data.get('intro', ''),
                'kind': '',
                'last_chapter': '',
            }
        toc_url = self.toc_r.get('tocUrl', '') or bi.get('tocUrl', '')
        # 支持 <js>...</js> 块的 tocUrl（漫画源常见）
        toc_url = _resolve_toc_url(toc_url, page_source, self.http_base, url, data=search_data or {})
        chapters = self._chapters(toc_url or book_url, soup)
        if not chapters:
            chapters = self._chapters_json(toc_url or book_url)
        book['chapters'] = chapters
        return book

    # ── 章节列表 ──

    def _chapters(self, toc_url, soup=None):
        """路由：有 soup 或标记为 CSS 详情 → CSS 选择器解析"""
        if soup is not None or self._is_css_detail():
            return self._chapters_css(toc_url, soup)
        return self._chapters_json(toc_url)

    def _chapters_json(self, toc_url):
        """纯 JSON API 目录解析"""
        url = _join_url(self.http_base, toc_url) if toc_url else ''

        # 诊断信息
        diag = {'fetch_ok': False, 'data_type': 'none', 'data_sample': '',
                'chapter_list_rule': '', 'rule_match': False, 'anti_bot': None}

        try:
            data, _ = self._fetch_raw(url)
            diag['fetch_ok'] = data is not None
        except Exception as e:
            diag['fetch_ok'] = False
            diag['fetch_error'] = str(e)[:100]
            self._last_detail_diag = diag
            return []

        if data is None:
            self._last_detail_diag = diag
            return []

        # 捕获数据类型和样本
        if hasattr(data, 'select_one'):
            diag['data_type'] = 'bs4'
            diag['data_sample'] = str(data)[:300]
        elif isinstance(data, dict):
            diag['data_type'] = 'dict'
            diag['data_sample'] = str(data)[:300]
        elif isinstance(data, list):
            diag['data_type'] = 'list'
            diag['data_sample'] = f'list[{len(data)}]'
        else:
            diag['data_type'] = type(data).__name__
            diag['data_sample'] = str(data)[:200]

        # CssSource._fetch_raw 返回 BeautifulSoup，但 tocUrl 可能是 JSON API
        if hasattr(data, 'select_one'):
            try:
                import requests as _req
                actual_url, post_body, extra_headers = _parse_post_url(url)
                headers = self._req_headers()
                headers.update(extra_headers)
                if post_body is not None:
                    resp = _req.post(actual_url, json=post_body, headers=headers, timeout=15)
                else:
                    resp = _req.get(actual_url, headers=headers, timeout=15)
                json_data, _ = safe_json(resp)
                if json_data is not None:
                    data = json_data
                    diag['data_type'] = 'dict (json retry)'
                    diag['data_sample'] = str(data)[:300]
            except Exception:
                pass

        cl = self.toc_r.get('chapterList', '')
        cn = self.toc_r.get('chapterName', '')
        cu = self.toc_r.get('chapterUrl', '')
        diag['chapter_list_rule'] = cl[:80] if cl else ''

        if not cl or not cn:
            diag['rule_match'] = False
            self._last_detail_diag = diag
            return []

        # 数据是 BeautifulSoup 但规则看起来像 CSS 选择器时，走 CSS 提取而非 JSON 路径
        if hasattr(data, 'select_one') and any(kw in cl for kw in ('@', 'class.', 'id.', 'tag.')):
            chs = _chapters_css_extract(data, cl, diag)
            if chs is not None:
                diag['rule_match'] = len(chs) > 0
                if not chs:
                    self._last_detail_diag = diag
                    return []
                result = []
                for i, ch in enumerate(chs):
                    name = extract_val(ch, cn, base_url=self.http_base) or f'第{i+1}章'
                    curl = _resolve_rule(cu, ch, url)
                    curl = _join_url(self.http_base, curl) if curl else ''
                    result.append({'name': name, 'url': curl, 'index': i})
                result.sort(key=lambda x: x.get('index', 0))
                return result
        # 标准 JSON 路径
        if '||' in cl:
            chs = None
            for p in cl.split('||'):
                chs = jpath(p.strip(), data)
                if chs:
                    break
            if chs is None:
                chs = []
        else:
            chs = jpath(cl, data)
            if chs is None:
                chs = walk_path(data, cl)

        if chs is None:
            diag['rule_match'] = False
            self._last_detail_diag = diag
            return []

        if not isinstance(chs, list):
            chs = [chs]

        diag['rule_match'] = len(chs) > 0
        if not chs:
            # 规则没匹配到任何章节 → 反爬检测
            diag['anti_bot'] = inspect_anti_bot(data, cl, url)
            self._last_detail_diag = diag
            return []

        # 成功提取到章节
        result = []
        for i, ch in enumerate(chs):
            if not isinstance(ch, dict):
                continue
            name = extract_val(ch, cn, base_url=self.http_base) or f'第{i+1}章'
            curl = _resolve_rule(cu, ch, url)
            curl = _join_url(self.http_base, curl) if curl else ''
            result.append({'name': name, 'url': curl, 'index': i})
        result.sort(key=lambda x: x.get('index', 0))
        return result

    def _chapters_css(self, toc_url, soup=None):
        """CSS 混合型源的目录解析"""
        diag = {'fetch_ok': False, 'data_type': 'bs4', 'data_sample': '',
                'chapter_list_rule': '', 'rule_match': False, 'anti_bot': None}

        if toc_url and toc_url != '#':
            url = _join_url(self.http_base, toc_url)
            try:
                soup = self._fetch_html(url)
                diag['fetch_ok'] = soup is not None
                if soup:
                    diag['data_sample'] = str(soup)[:300]
            except Exception as e:
                diag['fetch_error'] = str(e)[:100]
                self._last_detail_diag = diag
                return []
        if soup is None:
            self._last_detail_diag = diag
            return []

        cl_sel_raw = self.toc_r.get('chapterList', '')
        cn_sel = self.toc_r.get('chapterName', '')
        cu_sel = self.toc_r.get('chapterUrl', '')
        diag['chapter_list_rule'] = cl_sel_raw[:80] if cl_sel_raw else ''

        if not cl_sel_raw or not cn_sel:
            self._last_detail_diag = diag
            return []

        items = []
        for cl_part in cl_sel_raw.split('||'):
            cl_part = cl_part.strip()
            if not cl_part:
                continue
            try:
                cl_sel = css_conv(cl_part)
                if cl_sel:
                    items = soup.select(cl_sel)
                    if items:
                        break
            except Exception:
                continue

        diag['rule_match'] = len(items) > 0
        if not items:
            # 主要规则失败 → 启发式回退：搜索含大量链接的区域
            items = _heuristic_find_chapters(soup)
            if items:
                diag['rule_match'] = True
                diag['chapter_list_rule'] += ' (heuristic fallback)'
        if not items:
            diag['anti_bot'] = inspect_anti_bot(soup, cl_sel_raw, url)
            self._last_detail_diag = diag
            return []

        result = []
        for i, item in enumerate(items):
            name = extract_val(item, cn_sel) or f'第{i+1}章'
            curl = extract_link(item, cu_sel, self.http_base) if cu_sel else ''
            result.append({'name': name, 'url': curl, 'index': i})
        result.sort(key=lambda x: x.get('index', 0))
        return result

    # ── 章节内容 ──

    def chapter_content(self, ch_url):
        """获取章节正文文本"""
        return _fetch_full_content(self, ch_url)

    def chapter_images(self, ch_url):
        """获取章节图片列表（漫画等），返回 {images: [...], diagnostics: {...}}"""
        return _fetch_chapter_images(self, ch_url)


# ════════════════════════════════════════════════════════════════
# 辅助函数
# ════════════════════════════════════════════════════════════════

def _heuristic_find_chapters(soup):
    """当 Legado 规则未匹配时，启发式搜索页面中的章节列表。

    策略：找到包含最多 <a> 标签的容器元素，返回这些 <a> 作为候选项。
    通常章节列表就是页面中链接密度最高的区域。
    返回 list[Tag] 或空列表。
    """
    candidates = []
    # 遍历所有块级容器
    for tag in soup.find_all(['ul', 'ol', 'div', 'section', 'nav']):
        links = tag.find_all('a', href=True)
        if len(links) >= 3:
            # 过滤掉明显的导航/页脚链接
            valid = [a for a in links if not any(
                kw in (a.get_text().strip().lower() or '')
                for kw in ['首页', '上一页', '下一页', '登录', '注册', '首页', '末页']
            )]
            if len(valid) >= 3:
                candidates.append((len(valid), tag, valid))
    if not candidates:
        # 策略 2：找任何包含 3+ 链接且关键词含 chapter/list/toc 的元素
        for tag in soup.find_all(True):
            cls_id = str(tag.get('class', '')) + str(tag.get('id', ''))
            if any(kw in cls_id.lower() for kw in ('chapter', 'list', 'catalog', 'toc', 'menu', 'ml')):
                links = tag.find_all('a', href=True)
                if len(links) >= 3:
                    candidates.append((len(links), tag, links))
    if not candidates:
        return []
    # 返回链接最多的容器中的 <a> 元素
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [{'tag': a} for a in candidates[0][2]]

def _chapters_css_extract(soup, rule, diag):
    """从 BeautifulSoup 中用 Legado CSS 选择器提取章节列表，返回 list[dict] 或 None"""
    from rules.css_conv import css_conv
    for p in (rule.split('||') if '||' in rule else [rule]):
        p = p.strip()
        if not p:
            continue
        css = css_conv(p)
        try:
            items = soup.select(css)
            if items:
                diag['rule_match'] = True
                return [{'tag': item} for item in items]
        except Exception:
            continue
    return None

# ════════════════════════════════════════════════════════════════
# 内容获取（非类方法，由 BaseSource.chapter_content 调用）
# ════════════════════════════════════════════════════════════════

def _fetch_full_content(source, ch_url, max_pages=10):
    """多页章节内容获取 —— 自动追踪 nextContentUrl，拼接多页，应用 replaceRegex"""
    cr = source.content_r
    all_texts = []
    visited = set()
    current_url = ch_url

    for _ in range(max_pages):
        if not current_url or current_url in visited:
            break
        visited.add(current_url)

        page_text, next_url = _extract_single_page(source, current_url, cr)
        if page_text:
            all_texts.append(page_text)
        if not next_url:
            break
        current_url = next_url

    text = '\n'.join(all_texts)

    # 应用 replaceRegex
    rr = cr.get('replaceRegex', '')
    if rr and text:
        text = _apply_replace_regex(text, rr)

    # CssSource 的回退选择器
    from .css import CssSource
    if isinstance(source, CssSource):
        if not text or text.startswith('（获取') or text.startswith('（无正文') or text.startswith('（未匹配'):
            try:
                soup = source._fetch_html(_join_url(source.http_base, ch_url))
                fallback = _fallback_content(soup)
                if fallback:
                    return fallback
            except Exception:
                pass

    return text


def _extract_single_page(source, url, content_rules):
    """提取单页内容 + 下一页URL，返回 (text, next_url)"""
    from urllib.parse import urljoin as _uj
    content_rule = content_rules.get('content', '')
    next_rule = content_rules.get('nextContentUrl', '')

    try:
        data, base_url = source._fetch_raw(url)

        # 提取正文
        text = ''
        if content_rule:
            val = _resolve_rule(content_rule, data, base_url)
            if val:
                text = clean_text(val)
        else:
            text = clean_text(str(data))

        # 提取下一页URL
        next_url = None
        if next_rule and text:
            nu = _resolve_rule(next_rule, data, base_url)
            if nu and nu.startswith(('http', '/')):
                next_url = _uj(source.http_base, nu)

        return (text, next_url)
    except Exception:
        return ('（获取章节失败）', None)


def _resolve_toc_url(toc_url, page_source, http_base='', fallback_url='', data=None):
    """解析 tocUrl：支持 <js>...</js> 块执行和 {{...}} 模板替换

    <js> 块中 result 变量 = page_source (详情页 HTML/JSON 文本)
    {{...}} 模板使用 data 字典解析（原 _detail_json 传入的 JSON 数据）
    返回值：解析后的 URL 或空字符串
    """
    import re as _re
    if not toc_url or not toc_url.strip():
        return toc_url

    ts = toc_url.strip()

    # 处理 <js>...</js> 块 —— 执行 JS 获取真实 URL
    if ts.startswith('<js>') or '<js>' in ts:
        js_match = _re.search(r'<js>(.*?)</js>', ts, _re.DOTALL)
        if js_match:
            js_code = js_match.group(1).strip()
            if js_code:
                try:
                    from runtime import get_runtime
                    rt = get_runtime()
                    result = rt.run_legado_js(js_code, result_value=page_source, source_url=http_base)
                    if result and isinstance(result, str) and result.startswith('http'):
                        toc_url = result.strip()
                    elif result and isinstance(result, str) and result.strip():
                        toc_url = result.strip()
                except Exception:
                    pass

    # 处理 {{...}} 模板 —— 使用实际 data 字典而不是空 dict
    if '{{' in toc_url:
        toc_url = resolve_tpl(toc_url, data or {}, http_base)

    return toc_url


def _fetch_chapter_images(source, ch_url, max_pages=10):
    """漫画章节图片提取（统一入口 + 多页追踪）—— 返回 {images, diagnostics}

    提取策略（每页按顺序尝试）：
    1. content_rule（JSON路径/JS代码/CSS选择器）
    2. JSON fetch 回退（混合型源）
    3. JS packer 解码（eval(function(p,a,c,k,e,d){...}) 混淆块）
    4. BeautifulSoup <img> 提取
    5. JSON 递归查找
    最后统一过滤 UI 垃圾图，记录诊断信息。

    多页支持：自动追踪 content_rules 中的 nextContentUrl，逐页提取图片并合并。
    """
    cr = source.content_r
    content_rule = cr.get('content', '')
    next_rule = cr.get('nextContentUrl', '')
    http_base = source.http_base
    source_name = getattr(source, 'name', '未知')

    # 无多页配置 → 直接单页提取，保持原始行为
    if not next_rule:
        return _extract_single_page_images(
            source, ch_url, cr, content_rule, http_base, source_name
        )

    # ── 多页追踪（有 nextContentUrl 配置时）──
    all_img_urls = []
    all_diagnostics_pages = []
    visited = set()
    current_url = ch_url
    final_path = None
    final_label = 'none'
    final_dtype = 'none'
    total_raw = 0
    total_pages = 0

    for page_idx in range(max_pages):
        if not current_url or current_url in visited:
            break
        visited.add(current_url)
        total_pages += 1

        page_result = _extract_single_page_images(
            source, current_url, cr, content_rule, http_base, source_name
        )
        imgs = page_result.get('images', [])
        diag = page_result.get('diagnostics', {})
        all_diagnostics_pages.append(diag)

        # 记录第一页的提取路径信息
        if page_idx == 0:
            final_path = diag.get('extraction_path')
            final_label = diag.get('path_label', 'none')
            final_dtype = diag.get('data_type', 'none')

        all_img_urls.extend(imgs)
        total_raw += diag.get('raw_count', 0)

        # 获取下一页 URL
        if next_rule:
            try:
                data, base_url = source._fetch_raw(current_url)
                next_url_raw = _resolve_rule(next_rule, data, base_url)
                if next_url_raw:
                    # _resolve_rule 可能返回列表，取第一个
                    if isinstance(next_url_raw, list):
                        next_url_raw = next_url_raw[0] if next_url_raw else ''
                    next_url_str = str(next_url_raw).strip()
                    if next_url_str and next_url_str != current_url:
                        from urllib.parse import urljoin as _uj
                        current_url = _uj(current_url, next_url_str)
                    else:
                        current_url = None
                else:
                    current_url = None
            except Exception:
                current_url = None
        else:
            current_url = None

    # 去重（保持顺序）
    seen = set()
    unique_imgs = []
    for u in all_img_urls:
        if u not in seen:
            seen.add(u)
            unique_imgs.append(u)

    # 合并警告
    all_warnings = []
    for pd in all_diagnostics_pages:
        all_warnings.extend(pd.get('warnings', []))
    # 去重警告
    seen_w = set()
    unique_warnings = []
    for w in all_warnings:
        if w not in seen_w:
            seen_w.add(w)
            unique_warnings.append(w)

    # 最终过滤
    filtered, stats = _filter_junk_images_with_stats(unique_imgs)

    if stats['raw'] > 0:
        # 用实际去重后数量修正
        stats['raw'] = len(unique_imgs)
        stats['dropped'] = stats['raw'] - len(filtered)
        stats['kept'] = len(filtered)
        stats['all_junk'] = (stats['dropped'] > 0 and len(filtered) == 0)

    # 生成警告
    final_warnings = list(unique_warnings)
    if stats['raw'] == 0:
        if content_rule:
            final_warnings.append("content_rule 存在但提取结果为 0（可能反爬/JS 挑战/请求头不足）")
        else:
            final_warnings.append("源未配置 content_rule，且自动提取也未找到图片")
    elif stats.get('all_junk'):
        final_warnings.append(f"全部 {stats['raw']} 张图片被识别为 UI 垃圾图已过滤")
    elif stats['dropped'] > 0:
        final_warnings.append(f"{stats['dropped']}/{stats['raw']} 张被过滤（UI 元素），保留 {stats['kept']} 张")

    # 多页提示
    if total_pages > 1:
        final_warnings.insert(0, f"📄 跨 {total_pages} 页提取（nextContentUrl 追踪）")

    # 反爬检测（0 图时用第一页数据）
    anti_bot = None
    if stats['raw'] == 0 and all_diagnostics_pages:
        anti_bot = all_diagnostics_pages[0].get('anti_bot')

    sample_raw = [u[:120] for u in (unique_imgs or [])[:3]]
    sample_filtered = [u[:120] for u in (filtered or [])[:3]]
    cr_snippet = content_rule[:200] if content_rule else ''
    cr_result = all_diagnostics_pages[0].get('content_rule_result') if all_diagnostics_pages else None

    diagnostics = {
        'extraction_path': final_path,
        'path_label': final_label,
        'page_count': total_pages,
        'raw_count': stats['raw'],
        'filtered_count': stats['kept'],
        'junk_dropped': stats['dropped'],
        'warnings': final_warnings,
        'content_rule_snippet': cr_snippet,
        'content_rule_result': cr_result,
        'data_type': final_dtype,
        'anti_bot': anti_bot,
        'sample_raw_urls': sample_raw,
        'sample_filtered_urls': sample_filtered,
        'retry_attempted': False,
        'retry_success': False,
    }
    return {'images': filtered, 'diagnostics': diagnostics}


def _extract_single_page_images(source, url, cr, content_rule, http_base, source_name):
    """从单页提取漫画图片 URLs，返回 {images, diagnostics}

    提取策略（按顺序尝试）：
    1. content_rule 2. JSON fetch 回退 3. JS packer 解码
    4. BeautifulSoup img 5. JSON 递归查找
    """
    # 诊断状态
    extraction_path = None
    path_label = 'none'
    data_type = 'none'

    # 捕获 content_rule 的原始输出（用于诊断）
    _cr_raw_output = None
    _captured_data = None  # 保存 data 引用供 _finalize 使用

    def _finalize(imgs, path, label, dtype, extra_warnings=None):
        """统一收尾：过滤 + 统计 + 诊断"""
        filtered, stats = _filter_junk_images_with_stats(imgs)
        warnings = list(extra_warnings or [])

        if stats['raw'] == 0:
            if content_rule:
                warnings.append(
                    f"content_rule 存在但提取结果为 0（可能反爬/JS 挑战/请求头不足）")
            else:
                warnings.append(
                    f"源未配置 content_rule，且自动提取也未找到图片")
        elif stats['all_junk']:
            warnings.append(
                f"全部 {stats['raw']} 张图片被识别为 UI 垃圾图已过滤")
        elif stats['dropped'] > 0:
            warnings.append(
                f"{stats['dropped']}/{stats['raw']} 张被过滤（UI 元素），保留 {stats['kept']} 张")

        # 反爬检测（0 图时）
        anti_bot = None
        if stats['raw'] == 0 and _captured_data is not None:
            anti_bot = inspect_anti_bot(_captured_data, content_rule, url)

        # 样本 URL
        sample_raw = [u[:120] for u in (imgs or [])[:3]]
        sample_filtered = [u[:120] for u in (filtered or [])[:3]]

        diagnostics = {
            'extraction_path': path,
            'path_label': label,
            'raw_count': stats['raw'],
            'filtered_count': stats['kept'],
            'junk_dropped': stats['dropped'],
            'warnings': warnings,
            'content_rule_snippet': content_rule[:200] if content_rule else '',
            'content_rule_result': _cr_raw_output[:200] if _cr_raw_output else None,
            'data_type': dtype,
            'anti_bot': anti_bot,
            'sample_raw_urls': sample_raw,
            'sample_filtered_urls': sample_filtered,
            'retry_attempted': False,
            'retry_success': False,
        }
        return {'images': filtered, 'diagnostics': diagnostics}

    # ── 主流程 ──
    try:
        data, base_url = source._fetch_raw(_join_url(http_base, url))
    except Exception:
        import traceback
        traceback.print_exc()
        return {
            'images': [],
            'diagnostics': {
                'extraction_path': None, 'path_label': 'fetch_failed',
                'raw_count': 0, 'filtered_count': 0, 'junk_dropped': 0,
                'warnings': ['_fetch_raw 抛出异常（网络错误/超时）'],
                'content_rule_snippet': content_rule[:80] if content_rule else '',
                'data_type': 'exception', 'anti_bot': None,
                'retry_attempted': False, 'retry_success': False,
            }
        }

    _captured_data = data

    # 判断数据类型
    if data is None:
        data_type = 'none'
    elif hasattr(data, 'select_one'):
        data_type = 'bs4'
    elif isinstance(data, dict):
        data_type = 'dict'
    elif isinstance(data, list):
        data_type = 'list'
    elif isinstance(data, str):
        data_type = 'str'
    else:
        data_type = type(data).__name__

    # 检查是否需要 JSON fetch（content_rule 是 JSON 路径但 fetch 返回了 HTML）
    content_is_json = (content_rule.strip().startswith('$.') or
                       content_rule.strip().startswith('[*]') or
                       content_rule.strip().startswith('['))
    if content_is_json and hasattr(data, 'select_one'):
        try:
            actual_url, post_body, extra_headers = _parse_post_url(_join_url(http_base, url))
            headers = source._req_headers()
            headers.update(extra_headers)
            if post_body is not None:
                resp = http_requests.post(actual_url, json=post_body, headers=headers, timeout=15)
            else:
                resp = http_requests.get(actual_url, headers=headers, timeout=15)
            json_data, _ = safe_json(resp)
            if json_data is not None:
                data = json_data
                _captured_data = data
                base_url = actual_url
                data_type = 'dict'  # 更新数据类型
        except Exception:
            pass

    img_urls = []

    # ── 路径 1: content_rule 提取 ──
    if content_rule and data is not None:
        raw = _resolve_rule(content_rule, data, base_url)
        _cr_raw_output = _safe_truncate(str(raw)) if raw is not None else None
        if raw:
            if isinstance(raw, list):
                img_urls = _list_to_image_urls(raw, base_url)
            else:
                raw_text = str(raw)
                if '<img' in raw_text:
                    soup = BeautifulSoup(raw_text, 'lxml')
                    img_urls = _extract_images_from_soup(soup, '', base_url)
                # 始终追加上下文中的裸 URL（JS 解码的图片可能在变量中而不在 img 标签里）
                text_urls = _extract_images_from_text(raw_text, http_base)
                for u in text_urls:
                    if u not in img_urls:
                        img_urls.append(u)
        if img_urls:
            return _finalize(img_urls, 1, 'content_rule', data_type)

    # ── 路径 2: data 本身是列表 ──
    if isinstance(data, list):
        img_urls = _list_to_image_urls(data, base_url)
        if not img_urls:
            for item in data:
                if isinstance(item, dict):
                    found = _find_image_urls_in_json(item, base_url)
                    for u in found:
                        if u not in img_urls:
                            img_urls.append(u)
        if img_urls:
            return _finalize(img_urls, 2, 'json_list', data_type)

    # ── 路径 3: JS packer 解码 ──
    if hasattr(data, 'select_one'):
        packer_imgs = _extract_images_from_packer(str(data), base_url)
        if packer_imgs:
            return _finalize(packer_imgs, 3, 'js_packer', data_type)

    # ── 路径 4: BeautifulSoup <img> 提取 ──
    if hasattr(data, 'select_one'):
        img_urls = _extract_images_from_soup(data, content_rule, base_url)

    # ── 路径 5: JSON 递归查找 ──
    if not img_urls and data is not None and isinstance(data, dict):
        img_urls = _find_image_urls_in_json(data, base_url)

    if img_urls:
        return _finalize(img_urls,
                         4 if hasattr(data, 'select_one') else 5,
                         'bs4_img' if hasattr(data, 'select_one') else 'json_recursive',
                         data_type)

    # ── 所有路径均未找到图片 ──
    result = _finalize([], None, 'none', data_type)

    # ── 自动重试绕过（0 图且有拦截时，最多 1 次）──
    anti_bot = result['diagnostics']['anti_bot']
    if anti_bot and anti_bot.get('blocked'):
        bt = anti_bot.get('block_type', '')
        retry_url = _join_url(http_base, url)

        # 判断是否值得重试（paywall 不重试——付费墙换了 UA 也没用）
        can_retry = bt in ('html_not_json', 'login_wall', 'api_error', 'rate_limit')
        if can_retry:
            result['diagnostics']['retry_attempted'] = True
            try:
                if bt == 'rate_limit':
                    import time
                    time.sleep(2)

                # 构建重试 headers
                retry_headers = source._req_headers()
                actual_url, post_body, extra_h = _parse_post_url(retry_url)
                retry_headers.update(extra_h)

                if bt in ('html_not_json', 'login_wall', 'api_error'):
                    # 尝试移动端 UA
                    retry_headers['User-Agent'] = (
                        'Mozilla/5.0 (Linux; Android 10; SM-G981B) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Mobile Safari/537.36'
                    )
                    # 用详情页（章节列表来源）做 Referer
                    if url:
                        retry_headers['Referer'] = source.http_base

                if post_body is not None:
                    resp = http_requests.post(actual_url, json=post_body,
                                             headers=retry_headers, timeout=15)
                else:
                    resp = http_requests.get(actual_url, headers=retry_headers, timeout=15)

                # 解析重试响应
                retry_data = None
                content_type = resp.headers.get('content-type', '')
                if 'json' in content_type:
                    retry_data, _ = safe_json(resp)
                    if retry_data is None:
                        retry_data = BeautifulSoup(resp.text, 'lxml')
                else:
                    retry_data = BeautifulSoup(resp.text, 'lxml')

                if retry_data is not None:
                    retry_imgs = []
                    if content_rule:
                        raw = _resolve_rule(content_rule, retry_data, actual_url)
                        if raw:
                            if isinstance(raw, list):
                                retry_imgs = _list_to_image_urls(raw, actual_url)
                            elif isinstance(raw, str) and '<img' in raw:
                                s = BeautifulSoup(raw, 'lxml')
                                retry_imgs = _extract_images_from_soup(s, '', actual_url)
                            if not retry_imgs and isinstance(raw, str):
                                retry_imgs = _extract_images_from_text(str(raw), http_base)
                    if not retry_imgs and hasattr(retry_data, 'select_one'):
                        retry_imgs = _extract_images_from_soup(retry_data, content_rule, actual_url)
                    if not retry_imgs and isinstance(retry_data, dict):
                        retry_imgs = _find_image_urls_in_json(retry_data, actual_url)

                    filtered_retry, retry_stats = _filter_junk_images_with_stats(retry_imgs)
                    if filtered_retry:
                        result['diagnostics']['retry_success'] = True
                        result['images'] = filtered_retry
                        result['diagnostics']['extraction_path'] = -1  # 标记为重试路径
                        result['diagnostics']['path_label'] = 'retry_bypass'
                        result['diagnostics']['raw_count'] = retry_stats['raw']
                        result['diagnostics']['filtered_count'] = retry_stats['kept']
                        result['diagnostics']['junk_dropped'] = retry_stats['dropped']
                        result['diagnostics']['warnings'] = [
                            f"绕过「{bt}」成功：{retry_stats['kept']} 张图"
                        ]
                    else:
                        result['diagnostics']['warnings'].append(
                            f"已尝试绕过「{bt}」（移动端 UA + Referer），但仍未提取到图片")
            except Exception:
                result['diagnostics']['warnings'].append(
                    f"绕过「{bt}」重试时发生异常")

    # ── 控制台日志（仅异常时）──
    diag = result['diagnostics']
    if diag['raw_count'] == 0 or diag['junk_dropped'] > 0:
        _print_comic_diag(source_name, diag, url)

    return result


def _print_comic_diag(source_name, diag, ch_url):
    """打印漫画诊断信息到控制台"""
    lines = []
    has_issue = diag['raw_count'] == 0 or diag['junk_dropped'] > 0
    marker = '⚠' if has_issue else '✓'
    short_url = ch_url[:60] if ch_url else ''

    lines.append(f"[漫画 {marker}] 源「{source_name}」章节图片提取:")
    lines.append(f"  URL: {short_url}...")
    lines.append(f"  提取路径: {diag['path_label']} | 数据类型: {diag['data_type']}")
    if diag.get('content_rule_snippet'):
        lines.append(f"  Content规则: {diag['content_rule_snippet'][:120]}")
    if diag.get('content_rule_result'):
        lines.append(f"  Content输出: {diag['content_rule_result'][:150]}")
    elif diag.get('content_rule_result') is not None:
        lines.append(f"  Content输出: (空/null)")
    lines.append(f"  图片统计: 原始{diag['raw_count']} → 过滤{diag['filtered_count']} (垃圾:{diag['junk_dropped']})")

    for w in diag.get('warnings', []):
        lines.append(f"  → {w}")
    for u in diag.get('sample_raw_urls', [])[:2]:
        lines.append(f"  📷 原始: {u}")
    if diag.get('junk_dropped', 0) > 0:
        for u in diag.get('sample_filtered_urls', [])[:2]:
            lines.append(f"  ✅ 保留: {u}")

    ab = diag.get('anti_bot')
    if ab and ab.get('blocked'):
        lines.append(f"  🛡 反爬检测: {ab['block_type']} — {ab['evidence'][:100]}")
        if ab.get('suggested_fix'):
            lines.append(f"  💡 建议: {ab['suggested_fix']}")

    if diag.get('retry_attempted'):
        status = '成功 ✓' if diag.get('retry_success') else '失败 ✗'
        lines.append(f"  🔄 绕过重试: {status}")

    # Windows 控制台安全输出（避免 emoji/特殊 Unicode 导致的 GBK 编码错误）
    for line in lines:
        try:
            print(line)
        except UnicodeEncodeError:
            safe = line.encode('ascii', errors='replace').decode('ascii')
            print(safe)


def _safe_truncate(s, max_len=200):
    """安全截断字符串，避免 None 或异常"""
    if s is None:
        return None
    try:
        ss = str(s).strip()
        return ss[:max_len] if len(ss) > max_len else ss
    except Exception:
        return None


def _list_to_image_urls(items, base_url=''):
    """从列表中提取图片 URL（元素可为字符串或含 url/src/img 等键的 dict）"""
    img_urls = []
    for item in items:
        url = None
        if isinstance(item, str):
            url = item
        elif isinstance(item, dict):
            # 按优先级尝试常见图片 URL 键名
            for key in ('url', 'src', 'img', 'image', 'pic', 'path', 'href', 'link', 'uri'):
                v = item.get(key)
                if isinstance(v, str) and v.strip():
                    url = v
                    break
        if url:
            norm = _normalize_image_url(str(url).strip(), base_url)
            if norm and norm not in img_urls:
                img_urls.append(norm)
    return img_urls
