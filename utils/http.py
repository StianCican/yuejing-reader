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


# ════════════════════════════════════════════════════════════════
# 反爬/拦截检测
# ════════════════════════════════════════════════════════════════

def inspect_anti_bot(data, content_rule='', url=''):
    """检视 _fetch_raw 返回的 data，识别反爬/拦截模式。

    参数:
        data: dict | BeautifulSoup | str | list | None — 原始响应数据
        content_rule: str — 源的 content 规则（用于判断预期类型）
        url: str — 请求 URL（用于日志）

    返回:
        dict: {blocked, block_type, evidence, suggested_fix}
    """
    result = {'blocked': False, 'block_type': None,
              'evidence': '', 'suggested_fix': None}

    if data is None:
        result['blocked'] = True
        result['block_type'] = 'empty_body'
        result['evidence'] = '响应数据为 None'
        result['suggested_fix'] = '检查源 URL 是否可达，或添加 @Header:{Referer:...}'
        return result

    # ── 路径 A: BeautifulSoup → HTML 页面 ──
    if hasattr(data, 'select_one'):
        text = str(data)[:5000]
        text_lower = text.lower()

        # CF 五秒盾
        cf_patterns = [
            ('cf-browser-verify', 'CloudFlare 浏览器验证'),
            ('_cf_chl_opt', 'CloudFlare 挑战参数'),
            ('challenge-platform', 'CloudFlare 挑战平台'),
            ('cf-challenge', 'CloudFlare 挑战'),
            ('cf-wrapper', 'CloudFlare 包装页'),
        ]
        for pat, desc in cf_patterns:
            if pat in text_lower:
                result['blocked'] = True
                result['block_type'] = 'cloudflare'
                result['evidence'] = f'页面含 "{pat}" — {desc}'
                result['suggested_fix'] = '无法自动绕过，建议检查源是否仍存活'
                return result

        # JS 跳转挑战
        js_challenge_patterns = [
            (r'<script[^>]*>document\.location', 'JS document.location 跳转'),
            (r'<script[^>]*>window\.location\.href', 'JS window.location 跳转'),
            (r'<script[^>]*>location\.replace', 'JS location.replace 跳转'),
            (r'<script[^>]*>location\.href', 'JS location.href 跳转'),
        ]
        for pat, desc in js_challenge_patterns:
            if re.search(pat, text_lower):
                result['blocked'] = True
                result['block_type'] = 'js_challenge'
                result['evidence'] = f'页面含 {desc}'
                result['suggested_fix'] = '可能需 Cookie/Referer 支持，尝试用详情页 Referer 重试'
                return result

        # 验证码
        captcha_keywords = [
            'captcha', '验证码', '滑块验证', '请完成验证', '点击验证',
            'verifycode', 'validatecode', 'geetest', '极验', '请点击验证',
            '人机验证', '安全验证', '请输入验证码', 'verify_code',
        ]
        for kw in captcha_keywords:
            if kw.lower() in text_lower:
                result['blocked'] = True
                result['block_type'] = 'captcha'
                result['evidence'] = f'页面含验证码关键词: "{kw}"'
                result['suggested_fix'] = '无法自动绕过，需手动处理验证码'
                return result

        # 登录墙
        login_keywords = [
            '请登录', '立即登录', 'needlogin', 'login-form', 'signin',
            '请先登录', '登录后查看', 'user-login', 'member-login',
        ]
        for kw in login_keywords:
            if kw.lower() in text_lower:
                result['blocked'] = True
                result['block_type'] = 'login_wall'
                result['evidence'] = f'页面含登录关键词: "{kw}"'
                result['suggested_fix'] = '需配置源登录信息（loginUrl / header 中加 Cookie）'
                return result

        # 会员/付费墙
        vip_keywords = [
            '付费', '购买', '充值', '订阅', '会员', 'vip', '开通',
            'buy', 'pay', 'purchase', 'subscribe', 'premium',
            '付费章节', '付费漫画', 'vip章节', 'vip漫画', '付费阅读',
            '余额不足', '立即购买', '解锁', '本章为付费', '付费后可阅读',
            '成为会员', '开通会员', '续费', '点此购买',
        ]
        for kw in vip_keywords:
            if kw.lower() in text_lower:
                result['blocked'] = True
                result['block_type'] = 'paywall'
                result['evidence'] = f'页面含付费/会员关键词: "{kw}"'
                result['suggested_fix'] = '该章节需付费/会员，无法绕过。建议换一个书源重试'
                return result

        # 频率限制（HTML 页面中）
        rate_keywords = [
            '访问过于频繁', 'too frequent', 'rate limit', '请求过于频繁',
            '操作过于频繁', '稍后再试', 'too many requests',
        ]
        for kw in rate_keywords:
            if kw.lower() in text_lower:
                result['blocked'] = True
                result['block_type'] = 'rate_limit'
                result['evidence'] = f'页面含频率限制关键词: "{kw}"'
                result['suggested_fix'] = '等待 2-5 秒后重试'
                return result

        # HTML 页面但 content_rule 是 JSON 路径 → 可能被重定向
        if content_rule and (content_rule.strip().startswith('$.') or
                             content_rule.strip().startswith('[')):
            result['blocked'] = True
            result['block_type'] = 'html_not_json'
            page_title = ''
            try:
                t = data.select_one('title')
                if t:
                    page_title = t.get_text(strip=True)[:80]
            except Exception:
                pass
            result['evidence'] = (f'预期 JSON 但返回 HTML 页面'
                                  f'{": " + page_title if page_title else ""}')
            result['suggested_fix'] = '尝试用移动端 UA + 详情页 Referer 重试'
            return result

        return result

    # ── 路径 B: dict → JSON API 响应 ──
    if isinstance(data, dict):
        # 检查 API 错误码
        error_indicators = []

        # 检查 code/errno/status 等字段
        for field in ('code', 'errno', 'status', 'ret', 'error_code', 'result'):
            v = data.get(field)
            if v is not None:
                # 非 0、非 'ok'、非 200 都可能是错误
                if isinstance(v, int) and v != 0 and v != 200:
                    error_indicators.append(f'{field}={v}')
                elif isinstance(v, str) and v.lower() not in ('ok', 'success', '0'):
                    error_indicators.append(f'{field}="{v}"')

        # 检查 msg/message 字段
        msg = data.get('msg') or data.get('message') or data.get('err_msg') or ''
        if msg and isinstance(msg, str):
            msg_lower = msg.lower()
            # 频率限制
            rate_keywords = [
                'too frequent', 'rate limit', '访问过于频繁', '频率',
                'too many requests', '请求过快', '稍后再试',
            ]
            for kw in rate_keywords:
                if kw.lower() in msg_lower:
                    result['blocked'] = True
                    result['block_type'] = 'rate_limit'
                    result['evidence'] = f'API 返回: {msg[:120]}'
                    result['suggested_fix'] = '等待 2 秒后重试'
                    return result
            # 登录/鉴权
            auth_keywords = [
                'login', 'auth', 'token', '登录', '鉴权', 'unauthorized',
                '请登录', 'need login', 'not logged in',
            ]
            for kw in auth_keywords:
                if kw.lower() in msg_lower:
                    result['blocked'] = True
                    result['block_type'] = 'login_wall'
                    result['evidence'] = f'API 返回: {msg[:120]}'
                    result['suggested_fix'] = '需配置源登录 Cookie/Token'
                    return result

            # 会员/付费
            vip_msg_keywords = [
                '付费', '购买', '会员', 'vip', '订阅', '充值',
                'buy', 'pay', 'premium', 'subscribe', '余额',
            ]
            for kw in vip_msg_keywords:
                if kw.lower() in msg_lower:
                    result['blocked'] = True
                    result['block_type'] = 'paywall'
                    result['evidence'] = f'API 返回付费/会员提示: {msg[:120]}'
                    result['suggested_fix'] = '该章节需付费/会员，无法绕过。建议换书源'
                    return result

        if error_indicators:
            result['blocked'] = True
            result['block_type'] = 'api_error'
            result['evidence'] = f'API 错误: {", ".join(error_indicators[:3])}'
            if msg:
                result['evidence'] += f' — {msg[:80]}'
            result['suggested_fix'] = '尝试添加 @Header:{Referer:...} 或检查 API 参数'
            return result

        return result

    # ── 路径 C: str → 原始文本 ──
    if isinstance(data, str):
        text_lower = data.lower()[:3000]
        if 'captcha' in text_lower or '验证码' in text_lower:
            result['blocked'] = True
            result['block_type'] = 'captcha'
            result['evidence'] = '文本含验证码关键词'
            result['suggested_fix'] = '无法自动绕过'
        elif not data.strip():
            result['blocked'] = True
            result['block_type'] = 'empty_body'
            result['evidence'] = '响应文本为空'
            result['suggested_fix'] = '检查 URL 是否正确，或添加 Referer'
        return result

    # ── 路径 D: list → JSON 数组 ──
    if isinstance(data, list):
        if len(data) == 0:
            result['blocked'] = True
            result['block_type'] = 'empty_body'
            result['evidence'] = 'API 返回空数组 []'
            result['suggested_fix'] = '可能源配置有误或 API 参数不对'
        return result

    return result
