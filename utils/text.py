"""文本处理工具：清洗、乱码检测、replaceRegex"""
import re, json


def clean_text(raw):
    """清洗 HTML 标签、实体，整理空白"""
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
            replacement = rule.get('replacement', '')
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
