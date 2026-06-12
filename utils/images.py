"""图片 URL 提取工具——用于漫画章节"""
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup


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
    # <img src="..."> 标签
    for m in re.finditer(r'<img[^>]+(?:src|data-src|data-original)\s*=\s*["\']([^"\']+)["\']', text, re.IGNORECASE):
        url = m.group(1).strip()
        if url and not url.startswith('data:'):
            urls.append(url)
    # 直接的图片URL（无img标签）
    if not urls:
        for m in re.finditer(r'https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|webp|gif|bmp)(?:\?[^\s"\'<>]*)?', text, re.IGNORECASE):
            urls.append(m.group(0))
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
