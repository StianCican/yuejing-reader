"""图片 URL 提取工具——用于漫画章节"""
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup


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
        if u.startswith('//'):
            u = 'https:' + u
        elif not u.startswith('http'):
            u = urljoin(base_url, u) if base_url else u
        if u not in out:
            out.append(u)
    return out


def _find_image_urls_in_json(data, depth=0):
    """递归搜索 JSON 中的图片 URL"""
    if depth > 5:
        return []
    urls = []
    if isinstance(data, str):
        if re.match(r'https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|webp|gif|bmp)', data, re.IGNORECASE):
            urls.append(data)
    elif isinstance(data, list):
        for item in data:
            urls.extend(_find_image_urls_in_json(item, depth + 1))
    elif isinstance(data, dict):
        for key in ('url', 'src', 'img', 'image', 'pic', 'picture', 'cover', 'thumb'):
            if key in data:
                v = data[key]
                if isinstance(v, str) and v.startswith('http'):
                    urls.append(v)
        for v in data.values():
            urls.extend(_find_image_urls_in_json(v, depth + 1))
    return urls
