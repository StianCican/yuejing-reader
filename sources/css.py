"""CssSource — HTML 页面 CSS 选择器源"""
import requests as http_requests

from .base import BaseSource
from utils.http import (session, _join_url, _parse_inline_header, build_url)
from rules.css_conv import css_conv
from rules.extractors import extract_val, extract_img, extract_link


class CssSource(BaseSource):
    """CSS 选择器源——搜索返回 HTML，规则以 CSS 选择器为主"""

    def _fetch_raw(self, url):
        """获取 HTML 并解析为 BeautifulSoup，返回 (soup, base_url)"""
        soup = self._fetch_html(url)
        return soup, url

    def _fetch_html(self, url, extra_headers=None):
        """以 HTML 方式请求并解析为 BeautifulSoup"""
        from bs4 import BeautifulSoup
        clean_url, inline_headers = _parse_inline_header(url)
        h = self._req_headers()
        if inline_headers:
            h.update(inline_headers)
        if extra_headers:
            h.update(extra_headers)
        resp = http_requests.get(clean_url, headers=h, timeout=10)
        enc = resp.encoding
        if not enc or enc.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding or 'utf-8'
        return BeautifulSoup(resp.text, 'lxml')

    def search(self, kw, page=1):
        # 处理 @js: 搜索 URL
        su = self.search_url
        if su.startswith('@js:') or su.startswith('@js'):
            code = su[4:].strip() if su.startswith('@js:') else su[3:].strip()
            if code:
                from runtime import get_runtime
                result = get_runtime().run_legado_js(code, kw, self.http_base)
                if result and (result.startswith('http') or result.startswith('/')):
                    su = result
                else:
                    return []
        if su.startswith('http'):
            url, inline_headers = build_url(su, kw, page)
        else:
            url, inline_headers = build_url(self.http_base + su, kw, page)
        try:
            soup = self._fetch_html(url, extra_headers=inline_headers)
        except Exception:
            return []
        # || 回退：bookList 选择器也可能有多段
        bl_raw = self.sr.get('bookList', '')
        items = []
        for bl_part in bl_raw.split('||'):
            bl_sel = css_conv(bl_part.strip())
            if not bl_sel:
                continue
            items = soup.select(bl_sel)
            if items:
                break
        results = []
        for item in items:
            name = extract_val(item, self.sr.get('name', ''))
            if not name:
                continue
            book_url = extract_link(item, self.sr.get('bookUrl', ''), self.http_base)
            cover = extract_img(item, self.sr.get('coverUrl', ''), self.http_base)
            results.append({
                'name': name,
                'author': extract_val(item, self.sr.get('author', '')),
                'cover': cover,
                'intro': extract_val(item, self.sr.get('intro', '')),
                'kind': extract_val(item, self.sr.get('kind', '')),
                'book_url': book_url,
                'source_name': self.name,
                'source_url': self.base,
                'word_count': extract_val(item, self.sr.get('wordCount', '')),
                'last_chapter': extract_val(item, self.sr.get('lastChapter', '')),
            })
        return results
