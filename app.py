"""
本地小说聚合阅读器
从 Legado 书源 JSON 加载规则，聚合多个网站的小说内容，浏览器打开即用。
"""
import json, re, sys, os, subprocess, ast
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait

# Windows 控制台 UTF-8
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
from urllib.parse import urljoin, quote, urlencode
from flask import Flask, request, jsonify, render_template
import requests as http_requests
from bs4 import BeautifulSoup


def _join_url(base, path):
    """安全拼接 URL，处理 path 缺 / 前缀的情况（如纯 ID '9161264'）"""
    if not path:
        return ''
    if path.startswith('http'):
        return path
    return urljoin(base.rstrip('/') + '/', path.lstrip('/'))


def _parse_header(raw):
    """解析源 JSON 中的 header 字段——可能是 dict 或字符串"""
    if isinstance(raw, dict):
        return raw
    if not raw or not isinstance(raw, str):
        return {}
    raw = raw.strip()
    if not raw:
        return {}
    # 尝试 JSON 解析（双引号）
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, Exception):
        pass
    # 尝试 Python dict 解析（单引号，如 Legado 格式）
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError, Exception):
        pass
    return {}

# ── 全局 HTTP 会话 ──────────────────────────────────────────────
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
session = http_requests.Session()
session.verify = False
session.headers.update({
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
})
session.timeout = 5

# ── Legado JS 执行器 ────────────────────────────────────────────
JS_RUNNER = Path(__file__).parent / 'js_runner.js'

def run_legado_js(js_code, result_value='', source_url=''):
    """调用 Node.js 执行 Legado 书源中的 <js> 和 @js: 代码块"""
    runner = Path(JS_RUNNER) if isinstance(JS_RUNNER, str) else JS_RUNNER
    if not runner.exists():
        return str(result_value)
    try:
        proc = subprocess.run(
            ['node', str(runner)],
            input=json.dumps({
                'code': str(js_code),
                'result': str(result_value),
                'sourceUrl': str(source_url),
                'key': '',
                'page': 1,
                'headers': {},
                'store': {},
            }),
            capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            res = json.loads(proc.stdout)
            if 'error' not in res:
                val = res.get('result', result_value)
                return str(val) if val is not None else str(result_value)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception):
        pass
    return str(result_value)


def _split_rule(rule):
    """拆分 Legado 规则为三部分: (json_path, js_code, url_template)

    Legado 规则格式：
      $.path                     → 纯 JSON 路径取值
      $.path <js>code</js>       → 取值 + JS 转换
      $.path <js>code</js> URL   → 取值 + JS + URL 模板({{result}})
      $.path @js: code           → 取值 + JS 块
    """
    if not rule:
        return '', '', ''
    # 去掉 ## 后处理标记 和 @get: 标记
    clean = re.sub(r'##[^#\n]*(?:##[^#\n]*)*$', '', rule)
    clean = re.sub(r'@get:\{[^}]*\}', '', clean).strip()
    if not clean:
        return '', '', ''

    json_path, js_code, url_tpl = '', '', ''

    # 匹配 <js>...</js> 块
    js_inline = re.search(r'<js>([\s\S]*?)</js>', clean)
    if js_inline:
        js_code = js_inline.group(1).strip()
        before = clean[:js_inline.start()].strip()
        after = clean[js_inline.end():].strip()
        if before.startswith('$.'):
            json_path = before
        elif before:
            json_path = before
        if after:
            url_tpl = after
    elif '@js:' in clean:
        parts = clean.split('@js:', 1)
        if parts[0].strip().startswith('$.'):
            json_path = parts[0].strip()
        js_code = parts[1].strip() if len(parts) > 1 else ''
    else:
        if clean.startswith('$.'):
            json_path = clean
        else:
            url_tpl = clean

    return json_path, js_code, url_tpl


def _resolve_rule(rule, data_item, base_url=''):
    """解析完整规则并返回最终值"""
    if not rule:
        return ''
    json_path, js_code, url_tpl = _split_rule(rule)

    # Step 1: JSON 路径/CSS 选择器取值
    value = ''
    if json_path and isinstance(data_item, dict):
        key = json_path[2:] if json_path.startswith('$.') else json_path
        val = walk_path(data_item, key) if '.' in key else data_item.get(key)
        if val is not None:
            value = str(val).strip()
    elif json_path and isinstance(data_item, BeautifulSoup):
        sel = css_conv(json_path)
        el = data_item.select_one(sel)
        if el:
            value = el.get_text(strip=True)

    # Step 2: 执行 JS 转换
    if js_code and value:
        value = run_legado_js(js_code, value, base_url)

    # Step 3: 填充 URL 模板
    if url_tpl and value:
        result = url_tpl.replace('{{result}}', value)
        result = resolve_tpl(result, data_item, base_url)
        return result

    # 如果只有 URL 模板（无 JSON 路径无 JS），直接处理模板
    if url_tpl and not value:
        return resolve_tpl(url_tpl, data_item, base_url)

    return value


# ── 工具函数 ────────────────────────────────────────────────────
def _has_garbled(text):
    """检查文本是否包含乱码（UTF-8 解码后的 mojibake）"""
    garbled_chars = 0
    total_chars = 0
    for ch in text:
        if ch in '��':
            return True
        if '一' <= ch <= '鿿':
            total_chars += 1
        elif '' <= ch <= 'ÿ':
            garbled_chars += 1
    return garbled_chars > 5 and total_chars == 0

def safe_json(resp):
    # 检查 Content-Type 中的编码
    ct = resp.headers.get('content-type', '')
    charset = None
    if 'charset=' in ct.lower():
        charset = ct.lower().split('charset=')[-1].split(';')[0].strip()
    # 尝试多种编码，优先使用 Content-Type 指定的编码
    encodings = [charset] if charset else []
    for enc in ['utf-8', 'gbk', 'gb18030', 'gb2312']:
        if enc not in encodings:
            encodings.append(enc)
    best = None
    for enc in encodings:
        try:
            text = resp.content.decode(enc)
            data = json.loads(text)
            if not _has_garbled(text):
                return data
            if best is None:
                best = data
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return best

def build_url(base, kw, page=1):
    tpl = base.replace('{{key}}', quote(kw)).replace('{{page}}', str(page))
    return tpl.split(',{')[0]

def resolve_tpl(tpl, data, base_url=''):
    def repl(m):
        expr = m.group(1).strip()
        # 支持 {{baseUrl.match(/regex/)[n]}} 格式
        match_match = re.match(r'baseUrl\.match\(/(.+?)/\)\[(\d+)\]', expr)
        if match_match and base_url:
            pattern = match_match.group(1)
            idx = int(match_match.group(2))
            bm = re.search(pattern, base_url)
            if bm:
                try:
                    return str(bm.group(idx))  # idx 直接对应捕获组（JS [1] = Python group(1)）
                except IndexError:
                    return ''
            return ''
        # 标准 $.path 取值
        val = jpath(expr, data)
        return str(val) if val is not None else ''
    # 使用非贪婪匹配以支持嵌套的 {}（如正则中的 {n}）
    return re.sub(r'\{\{(.+?)\}\}', repl, tpl)

def jpath(expr, data):
    if not expr or data is None:
        return data
    if expr.startswith('$.'):
        expr = expr[2:]
    for part in re.split(r'\.(?![^\[]*\])', expr):
        if data is None:
            return None
        if part.endswith('[*]'):
            key = part[:-3]
            arr = data.get(key, []) if isinstance(data, dict) else []
            data = arr if isinstance(arr, list) else []
        elif re.match(r'.+\[\d+\]$', part):
            key, idx = re.search(r'(.+)\[(\d+)\]$', part).groups()
            arr = data.get(key, []) if isinstance(data, dict) else []
            data = arr[int(idx)] if isinstance(arr, list) and len(arr) > int(idx) else None
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data

def walk_path(data, path):
    if not path or data is None:
        return data
    if path.startswith('$.'):
        path = path[2:]
    parts = path.split('.')
    cur = data
    for p in parts:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur

def extract_val(ctx, sel, default='', base_url=''):
    if not sel:
        return default
    # dict 上下文 + 复杂规则：包含 <js>、@js: 或 {{...}} 模板
    if isinstance(ctx, dict):
        if '<js>' in sel or '@js:' in sel or '{{' in sel:
            val = _resolve_rule(sel, ctx, base_url)
            return val if val else default
    clean = re.sub(r'##[^#]*(?:##[^#]*)*$', '', sel)
    clean = re.sub(r'@get:\{[^}]*\}', '', clean).strip()
    if not clean:
        return default
    if isinstance(ctx, dict):
        # 去掉 $. 前缀
        key = clean[2:] if clean.startswith('$.') else clean
        # 处理嵌套路径如 $.categoryNames.className
        val = walk_path(ctx, key) if '.' in key else ctx.get(key)
        if val is not None:
            return str(val).strip()
    if isinstance(ctx, BeautifulSoup):
        sel_fixed = re.sub(r'^@css:', '', clean)
        sel_fixed = re.sub(r'@text$|@src$|@href$|@html$', '', sel_fixed)
        sel_fixed = css_conv(sel_fixed)
        el = ctx.select_one(sel_fixed)
        if el:
            return el.get_text(strip=True)
    return default

def extract_img(ctx, sel, base_url=''):
    if not sel:
        return ''
    if '<js>' in sel or '@js:' in sel:
        val = _resolve_rule(sel, ctx, base_url)
        if val:
            return urljoin(base_url, val) if not val.startswith('http') else val
        return ''
    clean = re.sub(r'##[^#]*(?:##[^#]*)*$', '', sel).strip()
    if isinstance(ctx, dict):
        key = clean[2:] if clean.startswith('$.') else clean
        val = walk_path(ctx, key) if '.' in key else ctx.get(key)
        if val:
            return urljoin(base_url, str(val)) if not str(val).startswith('http') else str(val)
    if isinstance(ctx, BeautifulSoup):
        sel_fixed = re.sub(r'^@css:', '', clean)
        sel_fixed = re.sub(r'@src$|@href$|@html$', '', sel_fixed)
        sel_fixed = css_conv(sel_fixed)
        el = ctx.select_one(sel_fixed)
        if el:
            src = el.get('data-src') or el.get('src') or el.get('data-original') or ''
            return urljoin(base_url, src) if src else ''
    return ''

def extract_link(ctx, sel, base_url=''):
    if not sel:
        return ''
    if '<js>' in sel or '@js:' in sel:
        val = _resolve_rule(sel, ctx, base_url)
        if val:
            return urljoin(base_url, val) if not val.startswith('http') else val
        return ''
    clean = re.sub(r'##[^#]*(?:##[^#]*)*$', '', sel).strip()
    if isinstance(ctx, dict):
        key = clean[2:] if clean.startswith('$.') else clean
        val = walk_path(ctx, key) if '.' in key else ctx.get(key)
        if val:
            return str(val)
        val = ctx.get(clean)
        if val:
            return str(val)
    if isinstance(ctx, BeautifulSoup):
        sel_fixed = re.sub(r'^@css:', '', clean)
        sel_fixed = re.sub(r'@text$|@src$|@href$|@html$', '', sel_fixed)
        sel_fixed = css_conv(sel_fixed)
        el = ctx.select_one(sel_fixed)
        if el:
            href = el.get('href') or el.get('data-href') or ''
            return urljoin(base_url, href) if href else ''
    return ''

def css_conv(sel):
    """Legado CSS 选择器 → 标准 CSS"""
    if not sel:
        return sel
    s = sel.strip()
    # 去掉 Legado 倒序标记
    s = re.sub(r'^-', '', s)
    s = re.sub(r'^@css:', '', s)
    s = re.sub(r'!0+$', '', s)
    s = re.sub(r'!([1-9]\d*)$', lambda m: f':nth-child(n+{int(m.group(1))+1})', s)
    # 去掉 Legado 索引后缀 .N（如 .li.0 → .li）
    s = re.sub(r'\.(\d+)(?=\s|$|@|\.|#)', '', s)
    s = s.replace('@', ' > ')
    s = re.sub(r'(?<!\.)\bclass\.', '.', s)
    s = re.sub(r'(?<!#)\bid\.', '#', s)
    s = re.sub(r'\btag\b', '', s)
    for suf in ['@text', '@src', '@href', '@html']:
        s = s.replace(suf, '')
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def clean_text(raw):
    if not raw:
        return ''
    text = re.sub(r'<br\s*/?>|<p>|</p>', '\n', str(raw))
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&amp;', '&', text)
    lines = [ln.strip() for ln in text.split('\n')]
    out, prev_blank = [], False
    for ln in lines:
        if not ln:
            if not prev_blank:
                out.append('')
                prev_blank = True
        else:
            out.append(ln)
            prev_blank = False
    return '\n'.join(out).strip()

def merge(base, extra):
    if not extra:
        return base
    if not base:
        return extra
    if isinstance(base, dict) and isinstance(extra, dict):
        base.update(extra)
    return base


# ════════════════════════════════════════════════════════════════
# JSON API 源（纯 JSON 接口，最简单）
# ════════════════════════════════════════════════════════════════
class JsonApiSource:
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
        # 计算真实 HTTP base（有些源的 bookSourceUrl 是占位符非真实 URL）
        if self.base.startswith('http'):
            self.http_base = self.base
        else:
            m = re.match(r'https?://[^/]+', self.search_url)
            self.http_base = m.group(0) if m else self.base

    def _req_headers(self):
        """合并全局 UA 和源专属 header（如 device/brand/auth token）"""
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

    def _fetch_html(self, url):
        """以 HTML 方式请求并解析为 BeautifulSoup"""
        resp = http_requests.get(url, headers=self._req_headers(), timeout=15)
        enc = resp.encoding
        if not enc or enc.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding or 'utf-8'
        return BeautifulSoup(resp.text, 'lxml')

    def search(self, kw, page=1):
        if self.search_url.startswith('http'):
            url = build_url(self.search_url, kw, page)
        else:
            url = build_url(self.http_base + self.search_url, kw, page)
        try:
            resp = http_requests.get(url, headers=self._req_headers(), timeout=10)
            data = safe_json(resp)
            if data is None:
                return []
        except Exception:
            return []
        bl = self.sr.get('bookList', '')
        if not bl:
            items = data if isinstance(data, list) else [data]
        elif '||' in bl:
            items = None
            for p in bl.split('||'):
                p = p.strip()
                items = jpath(p, data) if p.startswith('$') else walk_path(data, p)
                if items:
                    break
            if items is None:
                items = []
            if not isinstance(items, list):
                items = [items]
        elif bl == '[*]' or bl == '$':
            items = data if isinstance(data, list) else [data]
        elif bl.startswith('[') and bl.endswith(']'):
            key = bl[1:-1]
            items = data.get(key, []) if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = [items]
        else:
            items = jpath(bl, data)
            if items is None:
                items = walk_path(data, bl)
            if items is None:
                return []
            if not isinstance(items, list):
                items = [items]
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = extract_val(item, self.sr.get('name', ''), base_url=self.http_base)
            if not name:
                continue
            book_url = extract_val(item, self.sr.get('bookUrl', ''), base_url=self.http_base)
            results.append({
                'name': name,
                'author': extract_val(item, self.sr.get('author', ''), base_url=self.http_base),
                'cover': extract_img(item, self.sr.get('coverUrl', ''), self.http_base),
                'intro': extract_val(item, self.sr.get('intro', ''), base_url=self.http_base),
                'kind': extract_val(item, self.sr.get('kind', ''), base_url=self.http_base),
                'book_url': book_url,
                'source_name': self.name,
                'source_url': self.base,
                'word_count': extract_val(item, self.sr.get('wordCount', ''), base_url=self.http_base),
                'last_chapter': extract_val(item, self.sr.get('lastChapter', ''), base_url=self.http_base),
            })
        return results

    def detail(self, book_url, search_data=None):
        # 混合型源：详情走 HTML/CSS 解析
        if self._is_css_detail():
            return self._detail_css(book_url, search_data)

        init = self.bi_r.get('init', '').strip() if self.bi_r else ''
        if init and not init.startswith('@js'):
            full = _join_url(self.http_base, book_url)
            try:
                resp = http_requests.get(full, headers=self._req_headers(), timeout=15)
                data = safe_json(resp)
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
            'name': extract_val(data, self.bi_r.get('name', '') or self.sr.get('name', ''), search_data.get('name', '') if search_data else '', base_url=self.http_base),
            'author': extract_val(data, self.bi_r.get('author', '') or self.sr.get('author', ''), search_data.get('author', '') if search_data else '', base_url=self.http_base),
            'cover': extract_img(data, self.bi_r.get('coverUrl', '') or self.sr.get('coverUrl', ''), self.http_base) or (search_data.get('cover', '') if search_data else ''),
            'intro': extract_val(data, self.bi_r.get('intro', '') or self.sr.get('intro', ''), search_data.get('intro', '') if search_data else '', base_url=self.http_base),
            'kind': extract_val(data, self.bi_r.get('kind', '') or self.sr.get('kind', ''), base_url=self.http_base),
            'last_chapter': extract_val(data, self.bi_r.get('lastChapter', '') or self.sr.get('lastChapter', ''), base_url=self.http_base),
            'word_count': extract_val(data, self.bi_r.get('wordCount', '') or self.sr.get('wordCount', ''), base_url=self.http_base),
        }
        toc_url = self.toc_r.get('tocUrl', '') or self.bi_r.get('tocUrl', '')
        if toc_url:
            toc_url = resolve_tpl(toc_url, data, self.http_base) if '{{' in toc_url else (toc_url if toc_url.startswith('http') else extract_val(data, toc_url, toc_url, base_url=self.http_base))
            if toc_url and not toc_url.startswith('http') and not toc_url.startswith('/'):
                toc_url = book_url
        else:
            toc_url = book_url
        book['chapters'] = self._chapters(toc_url)
        return book

    def _detail_css(self, book_url, search_data=None):
        """CSS/HTML 混合型源的详情解析（类似 CssSource）"""
        url = _join_url(self.http_base, book_url)
        try:
            soup = self._fetch_html(url)
        except Exception:
            return search_data or {}
        bi = self.bi_r
        book = {
            'name': extract_val(soup, bi.get('name', '')) or (search_data.get('name', '') if search_data else ''),
            'author': extract_val(soup, bi.get('author', '')) or (search_data.get('author', '') if search_data else ''),
            'cover': extract_img(soup, bi.get('coverUrl', ''), self.http_base) or (search_data.get('cover', '') if search_data else ''),
            'intro': extract_val(soup, bi.get('intro', '')) or (search_data.get('intro', '') if search_data else ''),
            'kind': extract_val(soup, bi.get('kind', '')),
            'last_chapter': extract_val(soup, bi.get('lastChapter', '')),
        }
        toc_url = self.toc_r.get('tocUrl', '') or bi.get('tocUrl', '')
        if toc_url and '{{' in toc_url:
            toc_url = resolve_tpl(toc_url, {})
        book['chapters'] = self._chapters(toc_url or book_url, soup)
        return book

    def _chapters(self, toc_url, soup=None):
        # 混合型源：使用 CSS 选择器解析 HTML
        if soup is not None or self._is_css_detail():
            return self._chapters_css(toc_url, soup)

        url = _join_url(self.http_base, toc_url) if toc_url else ''
        try:
            resp = http_requests.get(url, headers=self._req_headers(), timeout=15)
            data = safe_json(resp)
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
            # 使用 _resolve_rule 统一处理 <js>、@js:、{{}} 模板
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
        cl_sel = css_conv(self.toc_r.get('chapterList', ''))
        cn_sel = self.toc_r.get('chapterName', '')
        cu_sel = self.toc_r.get('chapterUrl', '')
        if not cl_sel or not cn_sel:
            return []
        items = soup.select(cl_sel)
        result = []
        for i, item in enumerate(items):
            name = extract_val(item, cn_sel) or f'第{i+1}章'
            curl = extract_link(item, cu_sel, self.http_base) if cu_sel else ''
            result.append({'name': name, 'url': curl, 'index': i})
        result.sort(key=lambda x: x.get('index', 0))
        return result

    def chapter_content(self, ch_url):
        # 处理 Legado 的 POST 请求格式：url,{"method":"POST","body":{...}}
        post_body = None
        extra_req_headers = {}
        if ',{' in ch_url and '"method"' in ch_url:
            idx = ch_url.index(',{')
            url_part = ch_url[:idx]
            try:
                spec = json.loads(ch_url[idx + 1:])
                if spec.get('method', 'GET').upper() == 'POST':
                    post_body = spec.get('body', {})
                extra_req_headers = spec.get('headers', {})
            except (json.JSONDecodeError, Exception):
                pass
        else:
            url_part = ch_url

        url = _join_url(self.http_base, url_part)
        req_headers = self._req_headers()
        req_headers.update(extra_req_headers)
        try:
            if post_body is not None:
                resp = http_requests.post(url, json=post_body, headers=req_headers, timeout=15)
            else:
                resp = http_requests.get(url, headers=req_headers, timeout=15)
            data = safe_json(resp)
            if data is None:
                return '（无法解析章节内容）'
        except Exception:
            return '（获取章节失败）'
        cr = self.content_r.get('content', '')
        if cr:
            # 使用 _resolve_rule 统一处理 <js>、@js:、{{}} 模板
            val = _resolve_rule(cr, data, url)
            if val:
                return clean_text(val)
        return clean_text(str(data))


# ════════════════════════════════════════════════════════════════
# CSS 选择器源（HTML 页面解析）
# ════════════════════════════════════════════════════════════════
class CssSource:
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
        if self.base.startswith('http'):
            self.http_base = self.base
        else:
            m = re.match(r'https?://[^/]+', self.search_url)
            self.http_base = m.group(0) if m else self.base

    def _req_headers(self):
        """合并全局 UA 和源专属 header"""
        h = dict(session.headers)
        h.update(self._headers)
        return h

    def _fetch(self, url):
        resp = http_requests.get(url, headers=self._req_headers(), timeout=10)
        enc = resp.encoding
        if not enc or enc.lower() == 'iso-8859-1':
            resp.encoding = resp.apparent_encoding or 'utf-8'
        return BeautifulSoup(resp.text, 'lxml')

    def search(self, kw, page=1):
        if self.search_url.startswith('http'):
            url = build_url(self.search_url, kw, page)
        else:
            url = build_url(self.http_base + self.search_url, kw, page)
        try:
            soup = self._fetch(url)
        except Exception:
            return []
        bl_sel = css_conv(self.sr.get('bookList', ''))
        if not bl_sel:
            return []
        items = soup.select(bl_sel)
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

    def detail(self, book_url, search_data=None):
        url = _join_url(self.http_base, book_url)
        try:
            soup = self._fetch(url)
        except Exception:
            return search_data or {}
        bi = self.bi_r
        book = {
            'name': extract_val(soup, bi.get('name', '')) or (search_data.get('name', '') if search_data else ''),
            'author': extract_val(soup, bi.get('author', '')) or (search_data.get('author', '') if search_data else ''),
            'cover': extract_img(soup, bi.get('coverUrl', ''), self.http_base) or (search_data.get('cover', '') if search_data else ''),
            'intro': extract_val(soup, bi.get('intro', '')) or (search_data.get('intro', '') if search_data else ''),
            'kind': extract_val(soup, bi.get('kind', '')),
            'last_chapter': extract_val(soup, bi.get('lastChapter', '')),
        }
        toc_url = self.toc_r.get('tocUrl', '') or bi.get('tocUrl', '')
        if toc_url and '{{' in toc_url:
            toc_url = resolve_tpl(toc_url, {})
        book['chapters'] = self._chapters(toc_url or book_url, soup)
        return book

    def _chapters(self, toc_url, soup=None):
        if toc_url and toc_url != '#':
            url = _join_url(self.http_base, toc_url) if toc_url else ''
            try:
                soup = self._fetch(url)
            except Exception:
                return []
        if soup is None:
            return []
        cl_sel = css_conv(self.toc_r.get('chapterList', ''))
        cn_sel = self.toc_r.get('chapterName', '')
        cu_sel = self.toc_r.get('chapterUrl', '')
        if not cl_sel or not cn_sel:
            return []
        items = soup.select(cl_sel)
        result = []
        for i, item in enumerate(items):
            name = extract_val(item, cn_sel) or f'第{i+1}章'
            curl = extract_link(item, cu_sel, self.http_base) if cu_sel else ''
            result.append({'name': name, 'url': curl, 'index': i})
        result.sort(key=lambda x: x.get('index', 0))
        return result

    def chapter_content(self, ch_url):
        url = _join_url(self.http_base, ch_url)
        try:
            soup = self._fetch(url)
        except Exception:
            return '（获取章节失败）'
        cr = self.content_r.get('content', '')
        if not cr:
            return '（无正文规则）'
        # 带 <js> / @js: / {{}} 的规则走 _resolve_rule
        if '<js>' in cr or '@js:' in cr or '{{' in cr:
            val = _resolve_rule(cr, soup, url)
            if val:
                return clean_text(val)
            return '（正文为空）'
        # 纯 CSS 选择器
        sel = css_conv(cr)
        els = soup.select(sel)
        if els:
            paras = []
            for el in els:
                t = el.get_text(strip=True)
                if t:
                    paras.append(t)
            return clean_text('\n'.join(paras)) if paras else '（正文为空）'
        el = soup.select_one(sel)
        if el:
            return clean_text(el.get_text())
        return '（未匹配到正文）'


# ════════════════════════════════════════════════════════════════
# JS 源（通过 Node.js 子进程执行 @js: 规则）
# ════════════════════════════════════════════════════════════════
JS_RUNNER = str(Path(__file__).parent / 'js_runner.js')

def run_js(code, key='', page=1, source_url='', headers=None, store=None):
    """调用 Node.js 执行 JS 代码"""
    params = json.dumps({
        'code': code, 'key': key, 'page': page,
        'sourceUrl': source_url,
        'headers': headers or {},
        'store': store or {},
    }, ensure_ascii=False)
    try:
        result = subprocess.run(
            ['node', JS_RUNNER],
            input=params, capture_output=True, text=True,
            timeout=30, encoding='utf-8',
        )
        if result.returncode != 0:
            return {'error': result.stderr or 'node exit code ' + str(result.returncode)}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {'error': 'JS 执行超时'}
    except Exception as e:
        return {'error': str(e)}


class JsSource:
    def __init__(self, src, sources_map=None):
        self.src = src
        self.base = src['bookSourceUrl'].rstrip('/')
        self.name = src.get('bookSourceName', self.base)
        self.group = src.get('bookSourceGroup', '')
        self.search_code = src.get('searchUrl', '')
        self.js_lib = src.get('jsLib', '')
        self.sr = src.get('ruleSearch', {})
        self.toc_r = src.get('ruleToc', {})
        self.content_r = src.get('ruleContent', {})
        self.bi_r = src.get('ruleBookInfo', {})
        self.sources_map = sources_map or {}
        self._headers = _parse_header(src.get('header', ''))
        self.store = {}  # 会话级存储
        # 计算真实 HTTP base
        if self.base.startswith('http'):
            self.http_base = self.base
        else:
            m = re.match(r'https?://[^/]+', self.search_code)
            self.http_base = m.group(0) if m else self.base

    def _req_headers(self):
        """合并全局 UA 和源专属 header"""
        h = dict(session.headers)
        h.update(self._headers)
        return h

    def _exec_js(self, code, key='', page=1):
        """执行 JS 代码，返回结果"""
        full_code = self.js_lib + '\n' + code if self.js_lib else code
        return run_js(full_code, key, page, self.base, headers=self._req_headers(), store=self.store)

    def search(self, kw, page=1):
        # 提取 @js: 前缀的代码
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
            search_url = self.base + search_url
        elif not search_url.startswith('http'):
            search_url = self.base + '/' + search_url
        # 分离 URL 和 headers
        extra_headers = {}
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
            data = safe_json(resp)
            if data is None:
                return []
        except Exception:
            return []
        # 解析结果（复用 JsonApiSource 的逻辑）
        bl = self.sr.get('bookList', '')
        if not bl:
            items = data if isinstance(data, list) else [data]
        elif '||' in bl:
            items = None
            for p in bl.split('||'):
                items = jpath(p.strip(), data)
                if items:
                    break
            if items is None:
                items = []
            if not isinstance(items, list):
                items = [items]
        elif bl == '[*]' or bl == '$':
            items = data if isinstance(data, list) else [data]
        elif bl.startswith('[') and bl.endswith(']'):
            items = data.get(bl[1:-1], []) if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = [items]
        else:
            items = jpath(bl, data)
            if items is None:
                items = walk_path(data, bl)
            if items is None:
                return []
            if not isinstance(items, list):
                items = [items]
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = extract_val(item, self.sr.get('name', ''))
            if not name:
                continue
            book_url = extract_val(item, self.sr.get('bookUrl', ''), base_url=self.base)
            results.append({
                'name': name,
                'author': extract_val(item, self.sr.get('author', '')),
                'cover': extract_img(item, self.sr.get('coverUrl', ''), self.base),
                'intro': extract_val(item, self.sr.get('intro', '')),
                'kind': extract_val(item, self.sr.get('kind', '')),
                'book_url': book_url,
                'source_name': self.name,
                'source_url': self.base,
                'word_count': extract_val(item, self.sr.get('wordCount', '')),
                'last_chapter': extract_val(item, self.sr.get('lastChapter', '')),
            })
        return results

    def detail(self, book_url, search_data=None):
        # 对于 JS 源，详情页通常也是 JSON API，复用 JsonApiSource 的逻辑
        init = self.bi_r.get('init', '').strip() if self.bi_r else ''
        if init and not init.startswith('@js'):
            full = _join_url(self.http_base, book_url)
            try:
                resp = http_requests.get(full, headers=self._req_headers(), timeout=10)
                data = safe_json(resp)
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
            'name': extract_val(data, self.bi_r.get('name', '') or self.sr.get('name', ''), search_data.get('name', '') if search_data else ''),
            'author': extract_val(data, self.bi_r.get('author', '') or self.sr.get('author', ''), search_data.get('author', '') if search_data else ''),
            'cover': extract_img(data, self.bi_r.get('coverUrl', '') or self.sr.get('coverUrl', ''), self.base) or (search_data.get('cover', '') if search_data else ''),
            'intro': extract_val(data, self.bi_r.get('intro', '') or self.sr.get('intro', ''), search_data.get('intro', '') if search_data else ''),
            'kind': extract_val(data, self.bi_r.get('kind', '') or self.sr.get('kind', '')),
            'last_chapter': extract_val(data, self.bi_r.get('lastChapter', '') or self.sr.get('lastChapter', '')),
            'word_count': extract_val(data, self.bi_r.get('wordCount', '') or self.sr.get('wordCount', '')),
        }
        toc_url = self.toc_r.get('tocUrl', '') or self.bi_r.get('tocUrl', '')
        if toc_url:
            toc_url = resolve_tpl(toc_url, data, self.http_base) if '{{' in toc_url else (toc_url if toc_url.startswith('http') else extract_val(data, toc_url, toc_url, base_url=self.http_base))
            if toc_url and not toc_url.startswith('http') and not toc_url.startswith('/'):
                toc_url = book_url
        else:
            toc_url = book_url
        book['chapters'] = self._chapters(toc_url)
        return book

    def _chapters(self, toc_url):
        url = _join_url(self.http_base, toc_url) if toc_url else ''
        try:
            resp = http_requests.get(url, headers=self._req_headers(), timeout=10)
            data = safe_json(resp)
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
            name = extract_val(ch, cn) or f'第{i+1}章'
            # 使用 _resolve_rule 统一处理 <js>、@js:、{{}} 模板
            curl = _resolve_rule(cu, ch, url)
            if curl and not curl.startswith('http'):
                curl = _join_url(self.http_base, curl) if curl else ''
            result.append({'name': name, 'url': curl, 'index': i})
        result.sort(key=lambda x: x.get('index', 0))
        return result

    def chapter_content(self, ch_url):
        url = _join_url(self.http_base, ch_url)
        try:
            resp = http_requests.get(url, headers=self._req_headers(), timeout=10)
            data = safe_json(resp)
            if data is None:
                return '（无法解析章节内容）'
        except Exception:
            return '（获取章节失败）'
        cr = self.content_r.get('content', '')
        if cr:
            # 使用 _resolve_rule 统一处理 <js>、@js:、{{}} 模板
            val = _resolve_rule(cr, data, url)
            if val:
                return clean_text(val)
        return clean_text(str(data))


# ════════════════════════════════════════════════════════════════
# 源管理器
# ════════════════════════════════════════════════════════════════
class SourceManager:
    def __init__(self):
        self.sources = {}
        self.enabled = set()
        self._load()

    def _load(self):
        src_path = sys.argv[1] if len(sys.argv) > 1 else r'F:\86135\下载\墨辰整理书源大全7.1（禁止倒卖）【最新完整】.json'
        try:
            with open(src_path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f'⚠ 书源加载失败: {e}')
            data = []
        all_src = {}
        for s in data:
            url = s.get('bookSourceUrl', '').rstrip('/')
            # 去掉 Legado 的 ##@ 后缀
            if '##' in url:
                url = url.split('##')[0].rstrip('/')
            s['bookSourceUrl'] = url
            search_url = s.get('searchUrl', '')
            has_rules = bool(s.get('ruleSearch', {}).get('bookList', ''))
            if url and (search_url or has_rules):
                all_src[url] = s
        json_count, css_count, js_count = 0, 0, 0
        for url, s in all_src.items():
            bl = s.get('ruleSearch', {}).get('bookList', '')
            search_url = s.get('searchUrl', '').strip()
            is_js = bl.startswith('@js') or search_url.startswith('@js')
            if is_js:
                src = JsSource(s, all_src)
                js_count += 1
            elif not bl:
                continue
            else:
                is_json = (bl.startswith('[') or bl.startswith('$') or bl == '[*]' or bl == '$')
                if is_json:
                    src = JsonApiSource(s, all_src)
                    json_count += 1
                else:
                    src = CssSource(s, all_src)
                    css_count += 1
            self.sources[url] = src
            self.enabled.add(url)
        print(f'✓ 已加载 {len(self.sources)} 个源（JSON API: {json_count}, CSS: {css_count}, JS: {js_count}）')

    def search(self, kw, page=1, source_filter=None, max_workers=20, max_sources=20, search_timeout=4):
        targets = [self.sources[u] for u in self.enabled if u in self.sources]
        if source_filter:
            targets = [s for s in targets if source_filter in s.name or source_filter in s.base]
        # 优先 JSON API 源（最快），JS 源次之，CSS 选最少
        json_targets = [s for s in targets if isinstance(s, JsonApiSource)]
        js_targets = [s for s in targets if isinstance(s, JsSource)]
        css_targets = [s for s in targets if isinstance(s, CssSource)]
        targets = json_targets + js_targets + css_targets
        targets = targets[:max_sources]
        all_results = []
        def _do(src):
            try:
                return src.search(kw, page)
            except Exception:
                return []
        pool = ThreadPoolExecutor(max_workers=max_workers)
        futs = [pool.submit(_do, s) for s in targets]
        from concurrent.futures import as_completed
        import time as _time
        deadline = _time.time() + search_timeout
        try:
            for fut in as_completed(futs, timeout=search_timeout + 2):
                try:
                    results = fut.result()
                    if results:
                        all_results.extend(results)
                except Exception:
                    pass
                # 有足够结果或超时就返回
                if _time.time() >= deadline or len(all_results) >= 15:
                    break
        except TimeoutError:
            pass
        pool.shutdown(wait=False)
        print(f'[search] results={len(all_results)}, targets={len(targets)}')
        return all_results

    def get_source(self, source_url):
        return self.sources.get(source_url)


# ════════════════════════════════════════════════════════════════
# Flask 应用
# ════════════════════════════════════════════════════════════════
app = Flask(__name__)
mgr = SourceManager()

SHELF_FILE = Path(__file__).parent / 'shelf.json'

def load_shelf():
    try:
        return json.loads(SHELF_FILE.read_text(encoding='utf-8'))
    except Exception:
        return []

def save_shelf(data):
    SHELF_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/sources')
def api_sources():
    sources = []
    for url, src in mgr.sources.items():
        sources.append({
            'url': url,
            'name': src.name,
            'group': src.group,
            'enabled': url in mgr.enabled,
            'type': 'js' if isinstance(src, JsSource) else ('json' if isinstance(src, JsonApiSource) else 'css'),
        })
    return jsonify(sources)

@app.route('/api/toggle_source', methods=['POST'])
def api_toggle():
    url = request.json.get('url', '')
    if url in mgr.enabled:
        mgr.enabled.discard(url)
    elif url in mgr.sources:
        mgr.enabled.add(url)
    return jsonify(ok=True)

@app.route('/api/search')
def api_search():
    kw = request.args.get('q', '').strip()
    if not kw:
        return jsonify([])
    page = int(request.args.get('page', 1))
    src_filter = request.args.get('source', '')
    try:
        results = mgr.search(kw, page, src_filter or None)
        return jsonify(results)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(error=str(e)), 500

@app.route('/api/detail')
def api_detail():
    source_url = request.args.get('source', '')
    book_url = request.args.get('url', '')
    src = mgr.get_source(source_url)
    if not src:
        return jsonify(error='源未找到'), 404
    search_data = {}
    for k in ['name', 'author', 'cover', 'intro']:
        v = request.args.get(k, '')
        if v:
            search_data[k] = v
    book = src.detail(book_url, search_data)
    book['source_url'] = source_url
    book['book_url'] = book_url
    return jsonify(book)

@app.route('/api/chapter')
def api_chapter():
    source_url = request.args.get('source', '')
    ch_url = request.args.get('url', '')
    src = mgr.get_source(source_url)
    if not src:
        return jsonify(error='源未找到'), 404
    content = src.chapter_content(ch_url)
    return jsonify(content=content)

@app.route('/api/shelf')
def api_shelf_list():
    return jsonify(load_shelf())

@app.route('/api/shelf', methods=['POST'])
def api_shelf_add():
    book = request.json
    shelf = load_shelf()
    key = f"{book.get('source_url','')}|{book.get('book_url','')}"
    for i, b in enumerate(shelf):
        if f"{b.get('source_url','')}|{b.get('book_url','')}" == key:
            shelf.pop(i)
            save_shelf(shelf)
            return jsonify(ok=True, action='removed')
    shelf.insert(0, {
        'name': book.get('name', ''),
        'author': book.get('author', ''),
        'cover': book.get('cover', ''),
        'book_url': book.get('book_url', ''),
        'source_url': book.get('source_url', ''),
        'source_name': book.get('source_name', ''),
    })
    save_shelf(shelf)
    return jsonify(ok=True, action='added')

if __name__ == '__main__':
    print('╔══════════════════════════════════════╗')
    print('║   本地小说聚合阅读器                  ║')
    print('║   http://localhost:5000               ║')
    print('╚══════════════════════════════════════╝')
    app.run(host='0.0.0.0', port=5000, debug=False)
