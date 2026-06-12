"""JsonApiSource — 纯 JSON API 接口源"""
import requests as http_requests

from .base import BaseSource
from utils.http import (session, _join_url, safe_json, _parse_inline_header, build_url,
                        _parse_post_url)
from utils.text import clean_text
from rules.extractors import parse_search_results


class JsonApiSource(BaseSource):
    """JSON API 源——搜索返回 JSON，规则以 $. 开头"""

    def _fetch_raw(self, url):
        """获取 JSON 数据，返回 (data_dict_or_None, base_url)"""
        actual_url, post_body, extra_headers = _parse_post_url(url)
        full_url = _join_url(self.http_base, actual_url)
        req_headers = self._req_headers()
        req_headers.update(extra_headers)
        if post_body is not None:
            resp = http_requests.post(full_url, json=post_body, headers=req_headers, timeout=15)
        else:
            resp = http_requests.get(full_url, headers=req_headers, timeout=15)
        data, text = safe_json(resp)
        return data, full_url

    def _fetch_html(self, url):
        """以 HTML 方式请求并解析为 BeautifulSoup"""
        from bs4 import BeautifulSoup
        clean_url, inline_headers = _parse_inline_header(url)
        h = self._req_headers()
        if inline_headers:
            h.update(inline_headers)
        resp = http_requests.get(clean_url, headers=h, timeout=15)
        enc = resp.encoding
        if not enc or enc.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding or 'utf-8'
        return BeautifulSoup(resp.text, 'lxml')

    def search(self, kw, page=1):
        if self.search_url.startswith('http'):
            url, inline_headers = build_url(self.search_url, kw, page)
        else:
            url, inline_headers = build_url(self.http_base + self.search_url, kw, page)
        try:
            req_headers = self._req_headers()
            if inline_headers:
                req_headers.update(inline_headers)
            resp = http_requests.get(url, headers=req_headers, timeout=10)
            data, _ = safe_json(resp)
            if data is None:
                return []
        except Exception:
            return []
        return parse_search_results(data, self.sr, self.http_base, self.name, self.base)
