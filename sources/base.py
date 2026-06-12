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
                          _find_image_urls_in_json)
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
        if self._is_css_detail():
            result = self._detail_css(book_url, search_data)
            if result.get('chapters') or result.get('name'):
                return result
            return self._detail_json(book_url, search_data)

        result = self._detail_json(book_url, search_data)
        if (not result.get('chapters') and not result.get('name')) or self._is_css_detail():
            css_result = self._detail_css(book_url, search_data)
            if css_result.get('chapters'):
                return css_result
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
            toc_url = resolve_tpl(toc_url, data, self.http_base) if '{{' in toc_url else (
                toc_url if toc_url.startswith('http') else extract_val(data, toc_url, toc_url, base_url=self.http_base))
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
        try:
            soup = self._fetch_html(url)
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
        if toc_url and '{{' in toc_url:
            toc_url = resolve_tpl(toc_url, {})
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
        try:
            data, _ = self._fetch_raw(url)
            if data is None:
                return []
        except Exception:
            return []
        cl = self.toc_r.get('chapterList', '')
        cn = self.toc_r.get('chapterName', '')
        cu = self.toc_r.get('chapterUrl', '')
        if not cl or not cn:
            return []
        if '||' in cl:
            chs = None
            for p in cl.split('||'):
                chs = jpath(p.strip(), data)
                if chs:
                    break
            if chs is None:
                chs = []
        elif cl.endswith('[*]'):
            chs = jpath(cl, data)
        else:
            chs = walk_path(data, cl)
        if chs is None:
            return []
        if not isinstance(chs, list):
            chs = [chs]
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
        if toc_url and toc_url != '#':
            url = _join_url(self.http_base, toc_url)
            try:
                soup = self._fetch_html(url)
            except Exception:
                return []
        if soup is None:
            return []
        cl_sel_raw = self.toc_r.get('chapterList', '')
        cn_sel = self.toc_r.get('chapterName', '')
        cu_sel = self.toc_r.get('chapterUrl', '')
        if not cl_sel_raw or not cn_sel:
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
        """获取章节图片列表（漫画等），返回 URL 列表"""
        return _fetch_chapter_images(self, ch_url)


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


def _fetch_chapter_images(source, ch_url):
    """漫画章节图片提取（统一入口）
    通过 source._fetch_raw() 获取数据，不再绕过源类直接做 HTTP 请求。
    """
    cr = source.content_r
    content_rule = cr.get('content', '')
    http_base = source.http_base

    try:
        data, base_url = source._fetch_raw(_join_url(http_base, ch_url))

        # 路径 1: 用 content_rule 从数据中提取
        if content_rule and data is not None:
            raw = _resolve_rule(content_rule, data, base_url)
            if raw:
                # 检查是否为图片 URL 列表（JSON 数组）
                if isinstance(raw, list):
                    img_urls = [str(u) for u in raw if isinstance(u, str) and u.startswith('http')]
                    if img_urls:
                        return img_urls
                raw_text = str(raw)
                # 从文本中提取图片
                if '<img' in raw_text:
                    soup = BeautifulSoup(raw_text, 'lxml')
                    imgs = _extract_images_from_soup(soup, '', base_url)
                    if imgs:
                        return imgs
                imgs = _extract_images_from_text(raw_text, http_base)
                if imgs:
                    return imgs

        # 路径 2: 数据本身就是列表
        if isinstance(data, list):
            list_urls = [str(u) for u in data if isinstance(u, str) and u.startswith('http')]
            if list_urls:
                return list_urls

        # 路径 3: BeautifulSoup 上下文
        if hasattr(data, 'select_one'):
            return _extract_images_from_soup(data, content_rule, base_url)

        # 路径 4: JSON 递归查找
        if data is not None and isinstance(data, dict):
            found = _find_image_urls_in_json(data)
            if found:
                return found

        return []
    except Exception:
        return []
