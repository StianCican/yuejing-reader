"""
本地小说聚合阅读器
从 Legado 书源 JSON 加载规则，聚合多个网站的小说内容，浏览器打开即用。
"""
import json, re, sys, os, ast
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait

# Windows 控制台 UTF-8
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
from urllib.parse import urljoin, quote, urlencode, urlparse
from flask import Flask, request, jsonify, render_template, Response
import requests as http_requests
from bs4 import BeautifulSoup
import socket, ipaddress


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

# ── Legado JS 执行器（双路径引擎）──────────────────────────────
from js_runtime import get_runtime

def run_legado_js(js_code, result_value='', source_url=''):
    """调用双路径 JS 引擎执行 Legado 书源中的 <js> 和 @js: 代码块
    简单变换走 PyMiniRacer（~1ms），需要 ajax 的走持久 NodeWorker（~5ms）"""
    try:
        return get_runtime().run_legado_js(js_code, result_value, source_url)
    except Exception:
        return str(result_value)


# ── Legado @put/@get 变量系统 ──
# 全局变量存储（线程局部）。Legado 源中常用 @put:{key:selector} 存值，@get:{key} 取值。
import threading
_var_store = threading.local()


def _get_vars():
    """获取当前线程的变量字典"""
    if not hasattr(_var_store, 'data'):
        _var_store.data = {}
    return _var_store.data


def _set_var(key, value):
    """存变量"""
    _get_vars()[key] = str(value) if value is not None else ''


def _read_var(key):
    """取变量"""
    return _get_vars().get(key, '')


def _resolve_get_vars(rule):
    """将规则中的 @get:{key} 替换为变量值"""
    if not rule or '@get:' not in rule:
        return rule
    def _repl(m):
        key = m.group(1).strip()
        return _read_var(key)
    return re.sub(r'@get:\{([^}]+)\}', _repl, rule)


def _process_put_vars(rule, ctx, base_url=''):
    """处理 @put:{key:selector, key2:selector2}，把提取到的值存入变量，
    返回剥离 @put 后的规则字符串"""
    if not rule or '@put:' not in rule:
        return rule
    matches = list(re.finditer(r'@put:\{([^}]+)\}', rule))
    for m in matches:
        body = m.group(1)
        # 解析 key:selector 对（支持逗号分隔多个）
        for pair in body.split(','):
            pair = pair.strip()
            if ':' not in pair:
                continue
            var_key, selector = pair.split(':', 1)
            var_key, selector = var_key.strip(), selector.strip()
            if not var_key or not selector:
                continue
            try:
                val = _resolve_rule(selector, ctx, base_url)
                _set_var(var_key, val)
            except Exception:
                pass
    # 剥离所有 @put 块
    return re.sub(r'@put:\{[^}]*\}', '', rule).strip()


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
    # 去掉 ## 后处理标记（@get:{key} 不再剥离，由 _resolve_rule 解析替换）
    clean = re.sub(r'##[^#\n]*(?:##[^#\n]*)*$', '', rule)
    # @put:{key:selector} 由 _resolve_rule 处理，此处先剥离以简化后续解析
    clean = re.sub(r'@put:\{[^}]*\}', '', clean).strip()
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
    # 先替换 @get:{key} 为变量值
    rule = _resolve_get_vars(rule)
    # 处理 @put:{key:selector}，把提取的值存入变量后剥离
    rule = _process_put_vars(rule, data_item, base_url)
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
    # 先解析 @get:{key} 变量
    sel = _resolve_get_vars(sel)
    # 处理 @put:{key:selector}
    sel = _process_put_vars(sel, ctx, base_url)
    if not sel:
        return default
    # dict 上下文 + 复杂规则
    if isinstance(ctx, dict):
        if '<js>' in sel or '@js:' in sel or '{{' in sel:
            val = _resolve_rule(sel, ctx, base_url)
            return val if val else default
        # || 回退（任一成功即返回）
        parts = sel.split('||')
        for part in parts:
            p = part.strip()
            if not p:
                continue
            # && 链：每个子规则独立提取后拼接
            if '&&' in p:
                pieces = []
                for sub in p.split('&&'):
                    sub = sub.strip()
                    if not sub:
                        continue
                    base_p, hash_rules = _parse_hash_rules(sub)
                    base_p = base_p.strip()
                    if not base_p:
                        continue
                    key = base_p[2:] if base_p.startswith('$.') else base_p
                    val = walk_path(ctx, key) if '.' in key else ctx.get(key)
                    if val is not None:
                        pieces.append(_apply_hash_rules(str(val).strip(), hash_rules))
                if pieces:
                    return ' '.join(pieces)
                continue
            base_p, hash_rules = _parse_hash_rules(p)
            base_p = base_p.strip()
            if not base_p:
                continue
            key = base_p[2:] if base_p.startswith('$.') else base_p
            val = walk_path(ctx, key) if '.' in key else ctx.get(key)
            if val is not None:
                return _apply_hash_rules(str(val).strip(), hash_rules)
        return default
    # BeautifulSoup 上下文：|| 回退 + ## 后处理
    if isinstance(ctx, BeautifulSoup):
        parts = sel.split('||')
        val = _try_css_select(ctx, parts, base_url, 'text')
        return val if val else default
    return default

def extract_img(ctx, sel, base_url=''):
    if not sel:
        return ''
    if '<js>' in sel or '@js:' in sel:
        val = _resolve_rule(sel, ctx, base_url)
        if val:
            return urljoin(base_url, val) if not val.startswith('http') else val
        return ''
    if isinstance(ctx, dict):
        parts = sel.split('||')
        for part in parts:
            p = part.strip()
            if not p:
                continue
            base_p, hash_rules = _parse_hash_rules(p)
            base_p = base_p.strip()
            if not base_p:
                continue
            key = base_p[2:] if base_p.startswith('$.') else base_p
            val = walk_path(ctx, key) if '.' in key else ctx.get(key)
            if val:
                result = _apply_hash_rules(str(val), hash_rules)
                return urljoin(base_url, result) if not result.startswith('http') else result
        return ''
    if isinstance(ctx, BeautifulSoup):
        parts = sel.split('||')
        src = _try_css_select(ctx, parts, base_url, 'src')
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
    if isinstance(ctx, dict):
        parts = sel.split('||')
        for part in parts:
            p = part.strip()
            if not p:
                continue
            base_p, hash_rules = _parse_hash_rules(p)
            base_p = base_p.strip()
            if not base_p:
                continue
            key = base_p[2:] if base_p.startswith('$.') else base_p
            val = walk_path(ctx, key) if '.' in key else ctx.get(key)
            if val:
                result = _apply_hash_rules(str(val), hash_rules)
                return result if result.startswith('http') else urljoin(base_url, result)
        return ''
    if isinstance(ctx, BeautifulSoup):
        parts = sel.split('||')
        href = _try_css_select(ctx, parts, base_url, 'href')
        return urljoin(base_url, href) if href else ''
    return ''

def _parse_hash_rules(sel):
    """从选择器中提取 ##pattern##replacement 后处理规则
    返回 (clean_sel, [(pattern, replacement), ...])
    Legado 格式: selector##regex1##repl1##regex2##repl2
    """
    if not sel or '##' not in sel:
        return sel, []
    # 找到第一个 ## 的位置（选择器和后处理的分界）
    idx = sel.index('##')
    base = sel[:idx]
    tail = sel[idx:]
    rules = []
    # 按 ## 分割，交替作为 pattern 和 replacement
    parts = tail.split('##')[1:]  # 去掉开头的空串
    for i in range(0, len(parts) - 1, 2):
        rules.append((parts[i], parts[i + 1]))
    return base, rules

def _apply_hash_rules(value, rules):
    """对提取到的值依次应用 ## 后处理规则"""
    if not rules or not value:
        return value
    for pattern, replacement in rules:
        try:
            value = re.sub(pattern, replacement, value)
        except re.error:
            pass  # 不合法的正则跳过
    return value

def _try_css_select(soup, sel_parts, base_url='', extract='text'):
    """对多段 CSS 选择器逐段尝试（支持 || 回退、&& 链拼接）
    sel_parts: ['selector1', 'selector2', ...]
    extract: 'text' | 'src' | 'href'
    """
    for part in sel_parts:
        part = part.strip()
        if not part:
            continue
        # && 链：每个子选择器独立提取后拼接
        if '&&' in part:
            pieces = []
            for sub in part.split('&&'):
                sub = sub.strip()
                if not sub:
                    continue
                v = _single_css_extract(soup, sub, extract)
                if v:
                    pieces.append(v)
            if pieces:
                return ' '.join(pieces)
            continue
        v = _single_css_extract(soup, part, extract)
        if v:
            return v
    return ''


def _single_css_extract(soup, part, extract='text'):
    """单段 CSS 选择器提取（不含 || / &&），含 ## 后处理"""
    base_part, hash_rules = _parse_hash_rules(part)
    base_part = base_part.strip()
    if not base_part:
        return ''
    sel_fixed = re.sub(r'^@css:', '', base_part)
    sel_fixed = css_conv(sel_fixed)
    if not sel_fixed:
        return ''
    try:
        el = soup.select_one(sel_fixed)
        if el:
            if extract == 'src':
                val = el.get('data-src') or el.get('src') or el.get('data-original') or ''
            elif extract == 'href':
                val = el.get('href') or el.get('data-href') or ''
            else:
                val = el.get_text(strip=True)
            if val:
                return _apply_hash_rules(val, hash_rules)
    except Exception:
        pass
    return ''


def css_conv(sel):
    """Legado CSS 选择器 → 标准 CSS（修复多项缺陷）

    支持转换：
    - @@ → 空格（后代选择器，注意：必须在 @ 之前处理）
    - @  → ' > '（直接子选择器）
    - <  → ' ' （Legado 父选择器；BS4 无父选择器，降级为后代）
    - !N → :nth-child(n+N+1)（1-based 偏移）
    - !-N → :nth-last-child(N)（逆序）
    - :eq(N) → :nth-child(N+1)
    - :lt(N) → :nth-child(-n+N)
    - :gt(N) → :nth-child(n+N+2)
    - class.x → .x；id.x → #x；tag.x → x
    - @text / @src / @href / @html / @textNodes / @outerHtml 等属性后缀剥离
    """
    if not sel:
        return sel
    s = sel.strip()
    # 去掉 Legado 倒序标记
    s = re.sub(r'^-', '', s)
    s = re.sub(r'^@css:', '', s)

    # 先剥离属性提取后缀（必须在 @→> 转换之前，否则会被破坏）
    for suf in ['@text', '@src', '@href', '@html', '@textNodes',
                '@outerHtml', '@innerHtml', '@data', '@all',
                '@ownText', '@attr']:
        # 末尾或后跟空格/||/##/&&
        s = re.sub(re.escape(suf) + r'(?=\s|$|\||#|&)', '', s)

    # 必须先处理 @@（后代）再处理 @（直接子）
    s = s.replace('@@', ' ')
    s = s.replace('@', ' > ')

    # Legado 父选择器 < —— BS4 不支持，降级为后代（剥离）
    s = re.sub(r'\s*<\s*', ' ', s)

    # 末尾索引：!0 → 剥离，!N → :nth-child(n+N+1)，!-N → :nth-last-child(N)
    s = re.sub(r'!0+$', '', s)
    s = re.sub(r'!-(\d+)', lambda m: f':nth-last-child({m.group(1)})', s)
    s = re.sub(r'!([1-9]\d*)$', lambda m: f':nth-child(n+{int(m.group(1))+1})', s)

    # jQuery 风格伪类 → 标准 nth-child
    s = re.sub(r':eq\((\d+)\)', lambda m: f':nth-child({int(m.group(1))+1})', s)
    s = re.sub(r':lt\((\d+)\)', lambda m: f':nth-child(-n+{m.group(1)})', s)
    s = re.sub(r':gt\((\d+)\)', lambda m: f':nth-child(n+{int(m.group(1))+2})', s)

    # 去掉 Legado 索引后缀 .N（如 .li.0 → .li）
    s = re.sub(r'\.(\d+)(?=\s|$|>|\.|#|\[|:)', '', s)

    # class./id./tag. 前缀转换
    s = re.sub(r'(?<!\.)\bclass\.', '.', s)
    s = re.sub(r'(?<!#)\bid\.', '#', s)
    s = re.sub(r'\btag\.', '', s)
    s = re.sub(r'\btag\b', '', s)

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


# ── 多页章节内容 + replaceRegex ──
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

    return text


def _extract_single_page(source, url, content_rules):
    """提取单页内容 + 下一页URL，返回 (text, next_url)"""
    from urllib.parse import urljoin as _uj

    content_rule = content_rules.get('content', '')
    next_rule = content_rules.get('nextContentUrl', '')

    try:
        # 根据源类型获取数据
        if isinstance(source, CssSource):
            soup = source._fetch(url)
            data = soup
            base_url = url
        elif isinstance(source, JsSource):
            resp = http_requests.get(url, headers=source._req_headers(), timeout=10)
            data = safe_json(resp)
            if data is None:
                return ('（无法解析章节内容）', None)
            base_url = url
        else:  # JsonApiSource
            # 处理 POST 格式
            post_body = None
            extra_headers = {}
            actual_url = url
            if ',{' in url and '"method"' in url:
                idx = url.index(',{')
                actual_url = url[:idx]
                try:
                    spec = json.loads(url[idx + 1:])
                    if spec.get('method', 'GET').upper() == 'POST':
                        post_body = spec.get('body', {})
                    extra_headers = spec.get('headers', {})
                except (json.JSONDecodeError, Exception):
                    pass
            full_url = _join_url(source.http_base, actual_url)
            req_headers = source._req_headers()
            req_headers.update(extra_headers)
            if post_body is not None:
                resp = http_requests.post(full_url, json=post_body, headers=req_headers, timeout=15)
            else:
                resp = http_requests.get(full_url, headers=req_headers, timeout=15)
            data = safe_json(resp)
            if data is None:
                return ('（无法解析章节内容）', None)
            base_url = full_url

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
                if isinstance(source, JsonApiSource):
                    next_url = _uj(source.http_base, nu)
                elif isinstance(source, CssSource):
                    next_url = _uj(source.http_base, nu)
                else:
                    next_url = _uj(source.http_base, nu)

        return (text, next_url)
    except Exception:
        return ('（获取章节失败）', None)


def _apply_replace_regex(text, rr):
    """应用 Legado 的 replaceRegex 规则"""
    if not rr:
        return text
    if isinstance(rr, str):
        try:
            rr = json.loads(rr)
        except (json.JSONDecodeError, Exception):
            return text
    if not isinstance(rr, list):
        return text
    for rule in rr:
        if isinstance(rule, dict):
            pattern = rule.get('pattern', '') or rule.get('regex', '')
            replacement = rule.get('replacement', '') or rule.get('replacement', '')
        elif isinstance(rule, list) and len(rule) >= 2:
            pattern, replacement = rule[0], rule[1]
        else:
            continue
        try:
            text = re.sub(pattern, replacement, text)
        except (re.error, Exception):
            pass
    return text


# ── 通用CSS回退选择器 ──
GENERIC_CONTENT_SELECTORS = [
    '#content', '.content', '#booktxt', '#chaptercontent',
    '#TextContent', '.chapter-content', '#chapter-content',
    '.read-content', '#htmlContent', '#BookText',
    'article', '.post-content', '.entry-content', '.article-content',
]


def _fallback_content(soup):
    """Legado 规则失败时用通用 CSS 选择器兜底"""
    for sel in GENERIC_CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if len(text) > 100:
                return clean_text(text)
    return ''


# ── 漫画图片提取 ──
def _extract_images_from_text(text, base_url=''):
    """从富文本/HTML 中提取所有图片 URL"""
    if not text:
        return []
    urls = []
    # <img src="..."> 标签
    for m in re.finditer(r'<img[^>]+(?:src|data-src|data-original)\s*=\s*["\']([^"\']+)["\']', text, re.IGNORECASE):
        url = m.group(1).strip()
        if url and not url.startswith('data:'):
            urls.append(url)
    # 直接的图片URL（无img标签）
    if not urls:
        for m in re.finditer(r'https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|webp|gif|bmp)(?:\?[^\s"\'<>]*)?', text, re.IGNORECASE):
            urls.append(m.group(0))
    # 转绝对URL
    out = []
    for u in urls:
        if u.startswith('//'):
            u = 'https:' + u
        elif not u.startswith('http'):
            u = urljoin(base_url, u) if base_url else u
        if u not in out:
            out.append(u)
    return out


def _extract_images_from_soup(soup, content_rule='', base_url=''):
    """从 BeautifulSoup 中按内容规则或全部 img 提取图片 URL"""
    if not soup:
        return []
    urls = []
    # 优先按内容规则定位
    if content_rule:
        try:
            sel = css_conv(content_rule.split('||')[0])
            container = soup.select_one(sel)
            if container:
                for img in container.find_all('img'):
                    u = img.get('data-src') or img.get('src') or img.get('data-original') or ''
                    if u and not u.startswith('data:'):
                        urls.append(u)
        except Exception:
            pass
    # 兜底：全文 img
    if not urls:
        for img in soup.find_all('img'):
            u = img.get('data-src') or img.get('src') or img.get('data-original') or ''
            if u and not u.startswith('data:'):
                urls.append(u)
    out = []
    for u in urls:
        if u.startswith('//'):
            u = 'https:' + u
        elif not u.startswith('http'):
            u = urljoin(base_url, u) if base_url else u
        if u not in out:
            out.append(u)
    return out


def _fetch_chapter_images(source, ch_url):
    """漫画章节图片提取（统一入口）"""
    from urllib.parse import urljoin as _uj
    cr = source.content_r
    content_rule = cr.get('content', '')

    try:
        if isinstance(source, CssSource):
            url = _join_url(source.http_base, ch_url)
            soup = source._fetch(url)
            # 先尝试规则提取
            return _extract_images_from_soup(soup, content_rule, url)
        else:  # JsonApiSource / JsSource
            # 先用文本路径取出原始内容（可能含 <img> 标签或纯 URL 列表）
            text = _fetch_full_content(source, ch_url)
            # 漫画 JSON 源常返回纯 URL 串（用 \n 或 , 分隔），尝试解析
            if text:
                # 优先尝试每行就是一个URL
                line_urls = [line.strip() for line in text.split('\n') if line.strip().startswith('http')]
                if len(line_urls) >= 2:
                    return line_urls
                # 否则按文本中嵌入的URL/img提取
                return _extract_images_from_text(text, getattr(source, 'http_base', ''))
            return []
    except Exception:
        return []


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
        # Legado bookSourceType: 0=小说 1=听书 2=漫画 3=文件 4=影视
        self.source_type = int(src.get('bookSourceType', 0) or 0)
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
        # 标记为 CSS 详情 → 走 HTML 解析，失败则 JSON 兜底
        if self._is_css_detail():
            result = self._detail_css(book_url, search_data)
            if result.get('chapters') or result.get('name'):
                return result
            # CSS 失败，走 JSON API 兜底
            return self._detail_json(book_url, search_data)

        # 纯 JSON API 路径
        result = self._detail_json(book_url, search_data)
        # JSON 失败则尝试 CSS 兜底
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
        """CSS/HTML 混合型源的详情解析，CSS 失败时尝试 JSON 兜底"""
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

        # 兜底：HTML 解析无结果时用搜索数据
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

        # 优先 CSS 目录解析；失败时尝试 JSON API 目录
        chapters = self._chapters(toc_url or book_url, soup)
        if not chapters:
            # JSON API 兜底
            chapters = self._chapters_json(toc_url or book_url)
        book['chapters'] = chapters
        return book

    def _chapters(self, toc_url, soup=None):
        # 路由：有 soup 或标记为 CSS 详情 → CSS 选择器解析
        if soup is not None or self._is_css_detail():
            return self._chapters_css(toc_url, soup)
        return self._chapters_json(toc_url)

    def _chapters_json(self, toc_url):
        """纯 JSON API 目录解析（可独立调用作为兜底）"""
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
        return _fetch_full_content(self, ch_url)


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
        # Legado bookSourceType: 0=小说 1=听书 2=漫画 3=文件 4=影视
        self.source_type = int(src.get('bookSourceType', 0) or 0)
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
        # 处理 @js: 搜索 URL — 先执行 JS 得到真实 URL
        su = self.search_url
        if su.startswith('@js:') or su.startswith('@js'):
            code = su[4:].strip() if su.startswith('@js:') else su[3:].strip()
            if code:
                result = run_legado_js(code, kw, self.http_base)
                if result and (result.startswith('http') or result.startswith('/')):
                    su = result
                else:
                    return []
        if su.startswith('http'):
            url = build_url(su, kw, page)
        else:
            url = build_url(self.http_base + su, kw, page)
        try:
            soup = self._fetch(url)
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
        text = _fetch_full_content(self, ch_url)
        # 如果多页提取失败或只有错误信息，尝试回退选择器
        if not text or text.startswith('（获取') or text.startswith('（无正文') or text.startswith('（未匹配'):
            try:
                soup = self._fetch(_join_url(self.http_base, ch_url))
                fallback = _fallback_content(soup)
                if fallback:
                    return fallback
            except Exception:
                pass
        return text


# ════════════════════════════════════════════════════════════════
# JS 源（通过持久 Node.js 工作进程执行 @js: 规则）
# ════════════════════════════════════════════════════════════════

def run_js(code, key='', page=1, source_url='', headers=None, store=None):
    """调用持久 NodeWorker 执行 JS 代码（~5ms，替代原 subprocess ~200ms）"""
    try:
        return get_runtime().run_js(code, key=key, page=page,
                                   source_url=source_url,
                                   headers=headers, store=store)
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
        # Legado bookSourceType: 0=小说 1=听书 2=漫画 3=文件 4=影视
        self.source_type = int(src.get('bookSourceType', 0) or 0)
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
            search_url = self.http_base + search_url
        elif not search_url.startswith('http'):
            search_url = self.http_base + '/' + search_url
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
            book_url = extract_val(item, self.sr.get('bookUrl', ''), base_url=self.http_base)
            results.append({
                'name': name,
                'author': extract_val(item, self.sr.get('author', '')),
                'cover': extract_img(item, self.sr.get('coverUrl', ''), self.http_base),
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
        return _fetch_full_content(self, ch_url)


# ════════════════════════════════════════════════════════════════
# 源管理器
# ════════════════════════════════════════════════════════════════
class SourceManager:
    def __init__(self):
        self.sources = {}
        self.enabled = set()
        self.health = {}  # url → {'status': 'ok'|'partial'|'dead', 'latency': float, 'error': str}
        self._health_running = False
        self._status_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'source_status.json')
        self._load()
        self._restore_health()

    def _restore_health(self):
        """启动时从 source_status.json 恢复健康数据"""
        try:
            if os.path.exists(self._status_file):
                with open(self._status_file, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                restored = 0
                for url, data in saved.items():
                    if url in self.sources:
                        self.health[url] = data
                        restored += 1
                if restored:
                    ok = sum(1 for h in self.health.values() if h.get('status') == 'ok')
                    dead = sum(1 for h in self.health.values() if h.get('status') == 'dead')
                    print(f'📋 从文件恢复 {restored} 个源状态（{ok} ✓ / {dead} ✗）')
        except Exception as e:
            print(f'⚠ 恢复健康数据失败: {e}')

    def _save_health(self):
        """持久化健康数据到 source_status.json"""
        try:
            with open(self._status_file, 'w', encoding='utf-8') as f:
                json.dump(self.health, f, ensure_ascii=False, indent=1)
        except Exception as e:
            print(f'⚠ 保存健康数据失败: {e}')

    def run_health_check(self):
        """手动触发源健康检测（域名去重ping + 搜索测试，独立session，结果持久化）"""
        if self._health_running:
            return {'status': 'running'}
        self._health_running = True
        import time as _t, threading, requests as _req
        from urllib.parse import urlparse

        # 独立 session（不影响用户请求）
        hs = _req.Session()
        hs.verify = False
        hs.headers.update(session.headers)
        hs.timeout = 3

        # 测试关键词（小说、漫画常用中文搜索词）
        test_keywords = ['玄幻', '系统', '穿越', '都市', '仙侠', '重生']
        import random

        targets = list(self.sources.values())

        # 域名去重：同域名只 ping 一次
        domains = {}
        for src in targets:
            hb = getattr(src, 'http_base', src.base)
            if hb.startswith('http'):
                try:
                    d = urlparse(hb).netloc
                    if d not in domains:
                        domains[d] = hb
                except Exception:
                    pass

        def _ping(domain, url):
            try:
                t0 = _t.time()
                resp = hs.head(url, timeout=3, allow_redirects=True)
                return (domain, True, round(_t.time() - t0, 2))
            except Exception:
                try:
                    t0 = _t.time()
                    resp = hs.get(url, timeout=3, stream=True)
                    resp.close()
                    return (domain, True, round(_t.time() - t0, 2))
                except Exception:
                    return (domain, False, 0)

        def _test_search(src):
            """对单个源执行真实搜索测试，返回是否有结果"""
            kw = random.choice(test_keywords)
            try:
                results = src.search(kw, 1)
                return len(results) > 0
            except Exception:
                return False

        def _run():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            healthy_domains = set()

            # 阶段1：域名 ping
            print(f'🔍 阶段1: 检测 {len(domains)} 个唯一域名...')
            with ThreadPoolExecutor(max_workers=30) as pool:
                futs = [pool.submit(_ping, d, u) for d, u in domains.items()]
                try:
                    for fut in as_completed(futs, timeout=12):
                        try:
                            d, ok, lat = fut.result()
                            if ok:
                                healthy_domains.add(d)
                        except Exception:
                            pass
                except Exception:
                    pass

            # 映射回源 → 初始状态（域名通→partial，不通→dead）
            domain_status = {}  # src.base → 'partial'|'dead'
            ok_count = 0
            for src in targets:
                hb = getattr(src, 'http_base', src.base)
                if hb.startswith('http'):
                    try:
                        d = urlparse(hb).netloc
                        if d in healthy_domains:
                            domain_status[src.base] = 'partial'
                            ok_count += 1
                        else:
                            domain_status[src.base] = 'dead'
                    except Exception:
                        domain_status[src.base] = 'dead'
                else:
                    domain_status[src.base] = 'dead'
            print(f'🔍 阶段1完成: {ok_count}/{len(targets)} 个源可达')

            # 阶段2：对可达的源做搜索测试（限 sampled 个，避免太慢）
            reachable = [src for src in targets if domain_status.get(src.base) == 'partial' and src.base in self.enabled]
            import random as _random
            _random.shuffle(reachable)
            sample_size = min(60, len(reachable))
            test_sample = reachable[:sample_size] if sample_size > 0 else []
            search_ok = set()
            if test_sample:
                print(f'🔍 阶段2: 对 {len(test_sample)} 个源做搜索测试...')
                with ThreadPoolExecutor(max_workers=10) as pool:
                    futs = {pool.submit(_test_search, src): src for src in test_sample}
                    try:
                        for fut in as_completed(futs, timeout=30):
                            src = futs[fut]
                            try:
                                if fut.result():
                                    search_ok.add(src.base)
                            except Exception:
                                pass
                    except Exception:
                        pass
                print(f'🔍 阶段2完成: {len(search_ok)}/{len(test_sample)} 个源搜索有结果')

            # 最终状态判定
            for src in targets:
                if src.base in search_ok:
                    self.health[src.base] = {'status': 'ok', 'latency': 0, 'error': '', 'tested': True}
                elif domain_status.get(src.base) == 'partial':
                    self.health[src.base] = {'status': 'partial', 'latency': 0, 'error': '域名可达，搜索测试未覆盖或无结果', 'tested': src.base in {s.base for s in test_sample}}
                else:
                    self.health[src.base] = {'status': 'dead', 'latency': 0, 'error': '域名不可达', 'tested': True}

            # 持久化
            self._save_health()

            status_counts = {'ok': 0, 'partial': 0, 'dead': 0}
            for h in self.health.values():
                s = h.get('status', 'dead')
                status_counts[s] = status_counts.get(s, 0) + 1
            print(f'🔍 健康检测完成: {status_counts["ok"]}✓ / {status_counts["partial"]}~ / {status_counts["dead"]}✗（已保存）')
            self._health_running = False

        threading.Thread(target=_run, daemon=True).start()
        return {'status': 'started', 'domains': len(domains)}

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
        # 按 bookSourceType 索引（0=小说 1=听书 2=漫画 3=文件 4=影视）
        self.type_index = {0: [], 1: [], 2: [], 3: [], 4: []}
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
            stype = getattr(src, 'source_type', 0)
            if stype not in self.type_index:
                self.type_index[stype] = []
            self.type_index[stype].append(url)
        type_stats = ' | '.join(f'{["小说","听书","漫画","文件","影视"][t] if t<5 else f"类型{t}"}:{len(self.type_index[t])}'
                                 for t in sorted(self.type_index) if self.type_index[t])
        print(f'✓ 已加载 {len(self.sources)} 个源（JSON API: {json_count}, CSS: {css_count}, JS: {js_count}）')
        print(f'  分类: {type_stats}')

    def search(self, kw, page=1, source_filter=None, source_type=None,
               max_workers=30, max_sources=20, search_timeout=4):
        # 类型过滤：仅在指定类型时启用
        if source_type is not None:
            type_urls = set(self.type_index.get(source_type, []))
            targets = [self.sources[u] for u in self.enabled if u in self.sources and u in type_urls]
        else:
            targets = [self.sources[u] for u in self.enabled if u in self.sources]
        if source_filter:
            targets = [s for s in targets if source_filter in s.name or source_filter in s.base]

        # ── 智能分层：JSON优先 + JS次之 + CSS兜底 ──
        json_targets = [s for s in targets if isinstance(s, JsonApiSource)]
        js_targets   = [s for s in targets if isinstance(s, JsSource)]
        css_targets  = [s for s in targets if isinstance(s, CssSource)]
        tiered = json_targets + js_targets[:30] + css_targets[:20]
        tiered = tiered[:120]  # 硬上限 120 个源

        all_results = []
        seen = set()  # 去重：书名+作者

        def _dedup_key(r):
            name = re.sub(r'[^\u4e00-\u9fff\w]', '', r.get('name', ''))
            author = re.sub(r'[^\u4e00-\u9fff\w]', '', r.get('author', ''))
            return f'{name}|{author}'.lower()

        def _do(src):
            try:
                return src.search(kw, page)
            except Exception:
                return []

        pool = ThreadPoolExecutor(max_workers=max_workers)
        futs = [pool.submit(_do, s) for s in tiered]
        from concurrent.futures import as_completed
        import time as _time
        deadline = _time.time() + search_timeout + 4  # 给 +4s 缓冲
        early_deadline = _time.time() + 3  # 3秒内有 ≥10 条就提前返回

        try:
            for fut in as_completed(futs, timeout=search_timeout + 6):
                try:
                    results = fut.result()
                    for r in results:
                        key = _dedup_key(r)
                        if key not in seen:
                            seen.add(key)
                            all_results.append(r)
                except Exception:
                    pass
                now = _time.time()
                # 3s 内有 ≥10 条即返回；总超时或 ≥30 条也返回
                if len(all_results) >= 30 or (now >= deadline):
                    break
                if len(all_results) >= 10 and now >= early_deadline:
                    break
        except TimeoutError:
            pass
        pool.shutdown(wait=False)
        print(f'[search] results={len(all_results)}, deduped, sources_scanned={len(tiered)}')
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
    type_labels = {0: '小说', 1: '听书', 2: '漫画', 3: '文件', 4: '影视'}
    sources = []
    for url, src in mgr.sources.items():
        h = mgr.health.get(url, {})
        stype = getattr(src, 'source_type', 0)
        status = h.get('status')
        if status is None:
            # 兼容旧格式
            status = 'ok' if h.get('ok') else ('dead' if h.get('ok') is False else None)
        sources.append({
            'url': url,
            'name': src.name,
            'group': src.group,
            'enabled': url in mgr.enabled,
            'type': 'js' if isinstance(src, JsSource) else ('json' if isinstance(src, JsonApiSource) else 'css'),
            'source_type': stype,
            'category': type_labels.get(stype, f'类型{stype}'),
            'healthy': h.get('ok'),
            'status': status,          # 'ok' | 'partial' | 'dead' | null(未检测)
            'latency': h.get('latency', 0),
            'tested': h.get('tested', False),
        })
    return jsonify(sources)

@app.route('/api/health_check', methods=['POST'])
def api_health_check():
    result = mgr.run_health_check()
    return jsonify(result)

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
    # 按类型过滤：?type=0(小说) / 1(听书) / 2(漫画) / 4(影视)
    stype_raw = request.args.get('type', '')
    source_type = None
    if stype_raw != '':
        try:
            source_type = int(stype_raw)
        except (ValueError, TypeError):
            source_type = None
    try:
        results = mgr.search(kw, page, src_filter or None, source_type=source_type)
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
    book['source_type'] = getattr(src, 'source_type', 0)
    return jsonify(book)

@app.route('/api/chapter')
def api_chapter():
    source_url = request.args.get('source', '')
    ch_url = request.args.get('url', '')
    src = mgr.get_source(source_url)
    if not src:
        return jsonify(error='源未找到'), 404
    stype = getattr(src, 'source_type', 0)
    # 漫画：返回图片列表
    if stype == 2:
        imgs = _fetch_chapter_images(src, ch_url)
        return jsonify(content_type='comic', images=imgs,
                       source_url=source_url, count=len(imgs))
    # 听书：尝试提取音频URL
    if stype == 1:
        text = src.chapter_content(ch_url)
        audio_url = ''
        if text:
            m = re.search(r'https?://[^\s"\'<>]+\.(?:mp3|m4a|aac|ogg|flac|wav)(?:\?[^\s"\'<>]*)?', text, re.IGNORECASE)
            if m:
                audio_url = m.group(0)
        return jsonify(content_type='audio', audio_url=audio_url,
                       content=text, source_url=source_url)
    # 默认：文本
    content = src.chapter_content(ch_url)
    return jsonify(content_type='text', content=content)


@app.route('/api/proxy')
def api_proxy():
    """图片/资源代理 —— 伪造 Referer 绕过防盗链，屏蔽私有 IP 防 SSRF"""
    url = request.args.get('url', '')
    if not url or not url.startswith('http'):
        return 'Invalid URL', 400

    # SSRF 防护：屏蔽私有 IP
    try:
        hostname = urlparse(url).hostname
        if hostname:
            ip = socket.gethostbyname(hostname)
            if ipaddress.ip_address(ip).is_private:
                return 'Blocked: private IP', 403
    except Exception:
        pass

    referer = request.args.get('referer', '') or request.args.get('source', '') or url
    headers = {
        'Referer': referer,
        'User-Agent': session.headers.get('User-Agent', ''),
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
    }
    try:
        resp = session.get(url, headers=headers, timeout=10, stream=True, verify=False)
        content_type = resp.headers.get('Content-Type', 'image/jpeg')
        data = resp.content[:5 * 1024 * 1024]  # 5MB 上限
        return Response(data, content_type=content_type,
                        headers={'Cache-Control': 'public, max-age=3600',
                                 'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        return f'Fetch failed: {e}', 502

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
