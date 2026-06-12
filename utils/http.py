"""HTTP 工具：全局会话、编码检测、URL 拼接、Header 解析"""
import json, ast, re
from urllib.parse import urljoin, quote
import requests as http_requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── 全局 HTTP 会话 ──
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


def _join_url(base, path):
    """安全拼接 URL，处理 path 缺 / 前缀的情况（如纯 ID '9161264'）"""
    if not path:
        return ''
    if path.startswith('http'):
        return path
    return urljoin(base.rstrip('/') + '/', path.lstrip('/'))


def _has_garbled(text):
    """检查文本是否包含乱码（UTF-8 解码后的 mojibake）"""
    garbled_chars = 0
    total_chars = 0
    for ch in text:
        if ch in '�':
            return True
        if '一' <= ch <= '鿿':
            total_chars += 1
        elif '' <= ch <= 'ÿ':
            garbled_chars += 1
    return garbled_chars > 5 and total_chars == 0


def safe_json(resp):
    """安全解析 JSON 响应，自动处理编码问题。
    返回 (data, raw_text) 元组；解析失败时 data 为 None。"""
    ct = resp.headers.get('content-type', '')
    charset = None
    if 'charset=' in ct.lower():
        charset = ct.lower().split('charset=')[-1].split(';')[0].strip()
    encodings = [charset] if charset else []
    for enc in ['utf-8', 'gbk', 'gb18030', 'gb2312']:
        if enc not in encodings:
            encodings.append(enc)
    best = None
    best_text = ''
    for enc in encodings:
        try:
            text = resp.content.decode(enc)
            data = json.loads(text)
            if not _has_garbled(text):
                return data, text
            if best is None:
                best = data
                best_text = text
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return best, best_text


def _parse_header(raw):
    """解析源 JSON 中的 header 字段——可能是 dict 或字符串"""
    if isinstance(raw, dict):
        return raw
    if not raw or not isinstance(raw, str):
        return {}
    raw = raw.strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, Exception):
        pass
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError, Exception):
        pass
    return {}


def _parse_inline_header(text):
    """解析 Legado 内联 @Header:{key:value, ...} 语法，返回 (cleaned_text, headers_dict)"""
    if not text or ('@Header' not in text and '@header' not in text):
        return text, {}
    for keyword in ('@Header:', '@header:'):
        idx = text.find(keyword)
        if idx == -1:
            continue
        start = idx + len(keyword)
        while start < len(text) and text[start] in ' \t':
            start += 1
        if start >= len(text) or text[start] != '{':
            continue
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if depth != 0:
            continue
        header_str = text[start + 1:end]
        clean = (text[:idx] + text[end + 1:]).strip()
        headers = {}
        for part in _split_header_pairs(header_str):
            kv = part.split(':', 1)
            if len(kv) == 2:
                headers[kv[0].strip()] = kv[1].strip()
        return clean, headers
    return text, {}


def _split_header_pairs(text):
    """拆分 @Header 中的 key:value 对，处理值中含逗号/引号/花括号的情况"""
    pairs = []
    current = ''
    brace_depth = 0
    in_quote = None
    for ch in text:
        if in_quote:
            if ch == in_quote:
                in_quote = None
        else:
            if ch in '\'"':
                in_quote = ch
            elif ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
        if ch == ',' and brace_depth == 0 and in_quote is None:
            pairs.append(current.strip())
            current = ''
        else:
            current += ch
    if current.strip():
        pairs.append(current.strip())
    return pairs


def build_url(base, kw, page=1):
    """构建搜索 URL，先解析内联 @Header:{...}"""
    clean_base, inline_headers = _parse_inline_header(base)
    tpl = clean_base.replace('{{key}}', quote(kw)).replace('{{page}}', str(page))
    url = tpl.split(',{')[0]
    return url, inline_headers


def _parse_post_url(url):
    """解析可能包含 POST 规范和内联 @Header 的 URL。
    返回 (actual_url, post_body_or_None, extra_headers_dict)"""
    actual_url, inline_headers = _parse_inline_header(url)
    extra_headers = dict(inline_headers)
    post_body = None
    if ',{' in actual_url and '"method"' in actual_url:
        idx = actual_url.index(',{')
        post_spec_str = actual_url[idx + 1:]
        actual_url = actual_url[:idx]
        try:
            spec = json.loads(post_spec_str)
            if spec.get('method', 'GET').upper() == 'POST':
                post_body = spec.get('body', {})
            extra_headers.update(spec.get('headers', {}))
        except (json.JSONDecodeError, Exception):
            pass
    return actual_url, post_body, extra_headers
