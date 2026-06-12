"""JsSource — 通过 JS 执行获取搜索 URL 的源"""
import json
import requests as http_requests

from .base import BaseSource
from utils.http import (session, _join_url, safe_json, _parse_inline_header, build_url)
from rules.extractors import parse_search_results
from runtime import get_runtime


class JsSource(BaseSource):
    """JS 源——搜索 URL 由 @js: 代码块动态生成"""

    def __init__(self, src, sources_map=None):
        super().__init__(src, sources_map)
        self.search_code = src.get('searchUrl', '')
        self.js_lib = src.get('jsLib', '')
        self.store = {}  # 会话级存储

    def _fetch_raw(self, url):
        """获取 JSON 数据，返回 (data_or_None, base_url)"""
        actual_url, post_body, extra_headers = _parse_post_url(url)
        req_headers = self._req_headers()
        req_headers.update(extra_headers)
        if post_body is not None:
            resp = http_requests.post(actual_url, json=post_body, headers=req_headers, timeout=15)
        else:
            resp = http_requests.get(actual_url, headers=req_headers, timeout=15)
        data, text = safe_json(resp)
        if data is not None:
            return data, actual_url
        # JSON 解析失败，尝试作为 HTML
        from bs4 import BeautifulSoup
        if text:
            soup = BeautifulSoup(text, 'lxml')
            return soup, actual_url
        return None, actual_url

    def _fetch_html(self, url):
        """以 HTML 方式请求"""
        from bs4 import BeautifulSoup
        clean_url, inline_headers = _parse_inline_header(url)
        h = self._req_headers()
        if inline_headers:
            h.update(inline_headers)
        resp = http_requests.get(clean_url, headers=h, timeout=10)
        enc = resp.encoding
        if not enc or enc.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding or 'utf-8'
        return BeautifulSoup(resp.text, 'lxml')

    def _exec_js(self, code, key='', page=1):
        """执行 JS 代码，返回结果"""
        full_code = self.js_lib + '\n' + code if self.js_lib else code
        return get_runtime().run_js(full_code, key=key, page=page,
                                    source_url=self.base,
                                    headers=self._req_headers(),
                                    store=self.store)

    def search(self, kw, page=1):
        code = self.search_code
        if code.startswith('@js:'):
            code = code[4:].strip()
        elif code.startswith('@js'):
            code = code[3:].strip()
        if not code:
            return []
        result = self._exec_js(code, kw, page)
        if 'error' in result:
            return []
        # 更新存储和 headers
        if 'store' in result:
            self.store.update(result['store'])
        search_url = result.get('result', '')
        if not search_url or not isinstance(search_url, str):
            return []
        # 构造完整 URL
        if search_url.startswith('/'):
            search_url = self.http_base + search_url
        elif not search_url.startswith('http'):
            search_url = self.http_base + '/' + search_url
        # 解析内联 @Header 和 ,{...} 格式
        search_url, inline_headers = _parse_inline_header(search_url)
        extra_headers = dict(inline_headers)
        if ',{' in search_url:
            parts = search_url.split(',{', 1)
            search_url = parts[0]
            try:
                extra_headers = json.loads('{' + parts[1]).get('headers', {})
            except Exception:
                pass
        # 发起请求
        try:
            headers = self._req_headers()
            headers.update(extra_headers)
            resp = http_requests.get(search_url, headers=headers, timeout=10)
            data, _ = safe_json(resp)
            if data is None:
                return []
        except Exception:
            return []
        return parse_search_results(data, self.sr, self.http_base, self.name, self.base)
