"""图片 URL 提取工具——用于漫画章节"""
import re
import json as _json
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

# ── 垃圾图过滤 ──

# 明显是 UI 元素/图标/广告而非漫画内容的 URL 模式
_JUNK_PATTERNS = [
    # UI 图标/引导
    r'/images/(?:mobile|dlnew|common|icons?)/',
    r'/(?:win-)?(?:cross|close|arrow|prev|next|back|top|bottom|menu|share|search|home)',
    r'/(?:icon|logo|guide?|tip|badge|button|btn)[_-]',
    r'/(?:app|application)[_-]?(?:icon|logo|store|download|guid)',
    r'/h5/chapter/images/',       # 腾讯漫画 UI 引导图
    r'/buy-manga|pay-tip|pay-btn|cover-logo|detail-main',
    # 网站装饰
    r'favicon',
    r'/bg[_-]|/background',
    r'loading|spinner|skeleton|placeholder',
    # 社交媒体/统计
    r'qrcode|qr-code|wechat|weixin|share',
    r'/stats?/|/track(?:ing)?/|analytics|pixel',
]

# 动漫图片 CDN/域名特征（可能是漫画内容）
_MANGA_CDN_PATTERNS = [
    r'acimg\.cn',           # 腾讯漫画 CDN
    r'gtimgcdn\.ac\.qq',    # 腾讯图片 CDN
    r'cdndm5\.com',         # 动漫之家 CDN（UI + 内容都在这，需进一步判断）
    r'mhpress\.',           # 漫画压缩图
    r'/chapter|/manga|/comic|/manhua',
    r'/(?:img|image|pic|picture|photo)s?/\d',
]

def _is_junk_image_url(url):
    """判断图片 URL 是否可能是 UI/垃圾元素而非漫画内容"""
    if not url:
        return True
    url_lower = url.lower()
    for pat in _JUNK_PATTERNS:
        if re.search(pat, url_lower):
            return True
    return False

def _filter_junk_images(urls, keep_all_if_empty=True):
    """过滤图片 URL 列表中的垃圾图
    keep_all_if_empty: 如果过滤后为空，是否保留原始列表（避免全部丢弃）
    """
    if not urls:
        return urls
    filtered = [u for u in urls if not _is_junk_image_url(u)]
    if not filtered and keep_all_if_empty:
        return urls  # 避免全部误杀
    return filtered


def _filter_junk_images_with_stats(urls, keep_all_if_empty=True):
    """同 _filter_junk_images，但返回 (filtered_list, stats_dict)
    stats: {raw: 原始数量, kept: 保留数量, dropped: 被过滤数量, all_junk: 是否全部为垃圾}
    """
    if not urls:
        return urls, {'raw': 0, 'kept': 0, 'dropped': 0, 'all_junk': False}
    raw = len(urls)
    filtered = [u for u in urls if not _is_junk_image_url(u)]
    dropped = raw - len(filtered)
    all_junk = (dropped > 0 and len(filtered) == 0)
    if not filtered and keep_all_if_empty:
        return urls, {'raw': raw, 'kept': raw, 'dropped': dropped, 'all_junk': True}
    return filtered, {'raw': raw, 'kept': len(filtered), 'dropped': dropped, 'all_junk': all_junk}

# ── JS Packer 解码 ──

def _decode_js_packer(html_text):
    """从 HTML 文本中解码 JS packer 块 (eval(function(p,a,c,k,e,d){...}))

    许多漫画网站用 JS packer 混淆图片 URL 数据。
    返回解码后的文本，失败返回 None。
    """
    # 匹配 eval(function(p,a,c,k,e,d){...}(...))
    packer_re = re.compile(
        r'eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*d\s*\)'
        r'\s*\{([\s\S]*?)\}\s*\(\s*([\s\S]*?)\s*\)\s*\)',
        re.DOTALL
    )
    matches = packer_re.findall(html_text)

    results = []
    for fn_body, args_str in matches:
        try:
            decoded = _packer_decode_one(fn_body, args_str)
            if decoded:
                results.append(decoded)
        except Exception:
            continue

    return '\n'.join(results) if results else None


def _packer_decode_one(fn_body, args_str):
    """解码单个 packer 块"""
    # 析取参数: ('packed', 36, 390, 'k1|k2|...'.split('|'))
    # 找 split 参数
    split_match = re.search(r"'([^']*)'\.split\('([^']*)'\)", args_str)
    if not split_match:
        return None

    keywords_str = split_match.group(1)
    split_char = split_match.group(2)
    keywords = keywords_str.split(split_char)

    # 提取 packed code（第一个字符串参数）
    packed_match = re.match(r"\s*'([\s\S]*?)'\s*,", args_str)
    if not packed_match:
        return None
    packed = packed_match.group(1)

    # 提取 radix (a) 和 count (c)
    rest = args_str[packed_match.end():]
    nums = re.findall(r'(\d+)', rest)
    radix = int(nums[0]) if nums else 36
    # count 通常没用，keywords 长度即 key 数量

    # 标准 packer decode: 将数字替换为原文
    # e=function(c){return(c<a?"":e(parseInt(c/a)))+((c=c%a)>35?String.fromCharCode(c+29):c.toString(36))}
    def _decode_num(c):
        if c < radix:
            return _to_base36(c)
        high = c // radix
        low = c % radix
        result = _decode_num(high)
        if low > 35:
            result += chr(low + 29)
        else:
            result += _to_base36(low)
        return result

    def _to_base36(n):
        if n < 10:
            return str(n)
        return chr(n - 10 + ord('a'))

    # 解码 packed 中的数字 token
    result = []
    i = 0
    while i < len(packed):
        ch = packed[i]
        if ch.isdigit() or (ch.isalpha() and ch.islower()):
            # 尝试匹配一个数字 token
            j = i
            token = ''
            while j < len(packed) and (packed[j].isdigit() or (packed[j].isalpha() and packed[j].islower())):
                token += packed[j]
                j += 1
            if token:
                try:
                    num = int(token, 36)
                    if num < len(keywords):
                        result.append(keywords[num])
                    else:
                        result.append(token)
                    i = j
                    continue
                except ValueError:
                    pass
            result.append(ch)
            i += 1
        else:
            result.append(ch)
            i += 1

    decoded = ''.join(result)
    return decoded


def _extract_images_from_packer(html_text, base_url=''):
    """从 HTML 中的 JS packer 块提取图片 URL

    用 JS 运行时执行 packer eval + 变量查询（必须在同一次调用中完成，
    因为 MiniRacer 每次调用使用独立的 V8 上下文）。
    """
    packer_re = re.compile(
        r'eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*d\s*\)'
        r'\s*\{[\s\S]*?return\s+p\s*;\s*\}\s*\([\s\S]*?\)\s*\)',
        re.DOTALL
    )
    matches = packer_re.findall(html_text)
    if not matches:
        return []

    urls = []
    img_var_names = ['newImgs', 'imgList', 'imgs', 'images', 'photos', 'pages',
                     'picList', 'imageList', 'pageImages', 'chapterImages',
                     'mhImgs', 'comicImgs', 'mangaImgs', 'arrImages']

    try:
        from runtime import get_runtime
        rt = get_runtime()

        for packer_block in matches:
            for var_name in img_var_names:
                combined_js = packer_block + ';\nJSON.stringify(' + var_name + ');'
                result = rt.run_legado_js(combined_js, result_value='', source_url=base_url)
                if result and result.startswith('['):
                    try:
                        arr = _json.loads(result)
                        if isinstance(arr, list) and len(arr) > 0:
                            for item in arr:
                                if isinstance(item, str):
                                    norm = _normalize_image_url(item, base_url)
                                    if norm and norm not in urls:
                                        urls.append(norm)
                            if urls:
                                return urls
                    except Exception:
                        continue
            if urls:
                break
    except Exception:
        pass

    return urls


def _normalize_image_url(url, base_url=''):
    """将任意格式的图片 URL 归一化为绝对 HTTP URL
    处理：协议相对(//...) → https://, 相对路径 → urljoin, data: → 过滤
    返回空字符串表示无效URL
    """
    if not url or not isinstance(url, str):
        return ''
    url = url.strip()
    if not url or url.startswith('data:'):
        return ''
    if url.startswith('http'):
        return url
    if url.startswith('//'):
        return 'https:' + url
    if url.startswith('/') or (not url.startswith('http') and base_url):
        return urljoin(base_url, url)
    return url


def _extract_images_from_text(text, base_url=''):
    """从富文本/HTML 中提取所有图片 URL"""
    if not text:
        return []
    urls = []
    # <img src="..."> 标签（含更多 lazy-load 属性）
    for m in re.finditer(r'<img[^>]+(?:src|data-src|data-original|data-url|data-lazy-src)\s*=\s*["\']([^"\']+)["\']', text, re.IGNORECASE):
        url = m.group(1).strip()
        if url and not url.startswith('data:'):
            urls.append(url)
    # 直接的图片URL（无img标签）—— 也作为 img 标签提取的补充
    for m in re.finditer(r'https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|webp|gif|bmp)(?:/[a-zA-Z0-9]*)?(?:\?[^\s"\'<>]*)?', text, re.IGNORECASE):
        u = m.group(0)
        if u not in urls:
            urls.append(u)
    # 归一化为绝对URL
    out = []
    for u in urls:
        norm = _normalize_image_url(u, base_url)
        if norm and norm not in out:
            out.append(norm)
    return out


def _extract_images_from_soup(soup, content_rule='', base_url=''):
    """从 BeautifulSoup 中按内容规则或全部 img 提取图片 URL"""
    if not soup:
        return []
    from rules.css_conv import css_conv
    urls = []
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
    if not urls:
        for img in soup.find_all('img'):
            u = img.get('data-src') or img.get('src') or img.get('data-original') or ''
            if u and not u.startswith('data:'):
                urls.append(u)
    out = []
    for u in urls:
        norm = _normalize_image_url(u, base_url)
        if norm and norm not in out:
            out.append(norm)
    return out


def _find_image_urls_in_json(data, base_url='', depth=0):
    """递归搜索 JSON 中的图片 URL（自动归一化相对/协议相对URL）"""
    if depth > 5:
        return []
    urls = []
    if isinstance(data, str):
        # 匹配绝对、协议相对、相对路径图片URL
        if re.match(r'(?:https?:)?//[^\s"\'<>]+\.(?:jpg|jpeg|png|webp|gif|bmp)', data, re.IGNORECASE):
            norm = _normalize_image_url(data, base_url)
            if norm:
                urls.append(norm)
        elif re.match(r'/[^\s"\'<>]+\.(?:jpg|jpeg|png|webp|gif|bmp)', data, re.IGNORECASE):
            norm = _normalize_image_url(data, base_url)
            if norm:
                urls.append(norm)
    elif isinstance(data, list):
        for item in data:
            urls.extend(_find_image_urls_in_json(item, base_url, depth + 1))
    elif isinstance(data, dict):
        for key in ('url', 'src', 'img', 'image', 'pic', 'picture', 'cover', 'thumb'):
            if key in data:
                v = data[key]
                if isinstance(v, str):
                    norm = _normalize_image_url(v, base_url)
                    if norm:
                        urls.append(norm)
        for v in data.values():
            urls.extend(_find_image_urls_in_json(v, base_url, depth + 1))
    return urls
