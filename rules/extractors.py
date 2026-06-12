"""Legado 值提取器：从 JSON/HTML 上下文中提取文本、图片、链接"""
import re
from urllib.parse import urljoin
from .parser import _resolve_rule, walk_path, jpath
from .css_conv import css_conv
from .variables import _resolve_get_vars, _process_put_vars


def _parse_hash_rules(sel):
    """从选择器中提取 ##pattern##replacement 后处理规则
    返回 (clean_sel, [(pattern, replacement), ...])
    """
    if not sel or '##' not in sel:
        return sel, []
    idx = sel.index('##')
    base = sel[:idx]
    tail = sel[idx:]
    rules = []
    parts = tail.split('##')[1:]
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
            pass
    return value


# ── 公共提取器 ──

def extract_val(ctx, sel, default='', base_url=''):
    """从上下文中提取文本值"""
    if not sel:
        return default
    sel = _resolve_get_vars(sel)
    sel = _process_put_vars(sel, ctx, base_url)
    if not sel:
        return default
    # dict 上下文 + 复杂规则
    if isinstance(ctx, dict):
        if '<js>' in sel or '@js:' in sel or '{{' in sel:
            val = _resolve_rule(sel, ctx, base_url)
            return val if val else default
        # || 回退
        parts = sel.split('||')
        for part in parts:
            p = part.strip()
            if not p:
                continue
            # && 链
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
    # BeautifulSoup 上下文
    if hasattr(ctx, 'select_one'):
        parts = sel.split('||')
        val = _try_css_select(ctx, parts, base_url, 'text')
        return val if val else default
    return default


def extract_img(ctx, sel, base_url=''):
    """从上下文中提取图片 URL"""
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
    if hasattr(ctx, 'select_one'):
        parts = sel.split('||')
        src = _try_css_select(ctx, parts, base_url, 'src')
        return urljoin(base_url, src) if src else ''
    return ''


def extract_link(ctx, sel, base_url=''):
    """从上下文中提取链接 URL"""
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
    if hasattr(ctx, 'select_one'):
        parts = sel.split('||')
        href = _try_css_select(ctx, parts, base_url, 'href')
        return urljoin(base_url, href) if href else ''
    return ''


# ── CSS 选择器提取 ──

def _try_css_select(soup, sel_parts, base_url='', extract='text'):
    """对多段 CSS 选择器逐段尝试（支持 || 回退、&& 链拼接）"""
    for part in sel_parts:
        part = part.strip()
        if not part:
            continue
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


# ── 搜索结果解析（JsonApiSource 和 JsSource 共用）──

def parse_search_results(data, sr, http_base, source_name, source_url):
    """从搜索响应数据中提取书列表（JSON API 和 JS 源共用）"""
    bl = sr.get('bookList', '')
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
        name = extract_val(item, sr.get('name', ''), base_url=http_base)
        if not name:
            continue
        book_url = extract_val(item, sr.get('bookUrl', ''), base_url=http_base)
        results.append({
            'name': name,
            'author': extract_val(item, sr.get('author', ''), base_url=http_base),
            'cover': extract_img(item, sr.get('coverUrl', ''), http_base),
            'intro': extract_val(item, sr.get('intro', ''), base_url=http_base),
            'kind': extract_val(item, sr.get('kind', ''), base_url=http_base),
            'book_url': book_url,
            'source_name': source_name,
            'source_url': source_url,
            'word_count': extract_val(item, sr.get('wordCount', ''), base_url=http_base),
            'last_chapter': extract_val(item, sr.get('lastChapter', ''), base_url=http_base),
        })
    return results
