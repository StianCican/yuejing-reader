"""Legado 规则 DSL 解析器"""
import re
from .variables import _resolve_get_vars, _process_put_vars


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
    # 去掉 ## 后处理标记
    clean = re.sub(r'##[^#\n]*(?:##[^#\n]*)*$', '', rule)
    # @put:{key:selector} 由 _resolve_rule 处理，此处先剥离以简化后续解析
    clean = re.sub(r'@put:\{[^}]*\}', '', clean).strip()
    if not clean:
        return '', '', ''

    json_path, js_code, url_tpl = '', '', ''

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
    elif json_path and hasattr(data_item, 'select_one'):
        from .css_conv import css_conv
        sel = css_conv(json_path)
        el = data_item.select_one(sel)
        if el:
            value = el.get_text(strip=True)
    elif not json_path and js_code:
        # @js: 规则无前置 JSON 路径 → 将整个响应体作为 result 传给 JS
        if hasattr(data_item, 'select_one'):
            value = str(data_item)
        elif isinstance(data_item, (dict, list)):
            import json as _json
            value = _json.dumps(data_item, ensure_ascii=False)
        elif isinstance(data_item, str):
            value = data_item

    # Step 2: 执行 JS 转换
    if js_code and value:
        try:
            from runtime import get_runtime
            value = get_runtime().run_legado_js(js_code, value, base_url)
        except Exception:
            pass

    # Step 3: 填充 URL 模板
    if url_tpl and value:
        result = url_tpl.replace('{{result}}', value)
        result = resolve_tpl(result, data_item, base_url)
        return result

    # 如果只有 URL 模板（无 JSON 路径无 JS），直接处理模板
    if url_tpl and not value:
        return resolve_tpl(url_tpl, data_item, base_url)

    return value


def resolve_tpl(tpl, data, base_url=''):
    """处理 {{...}} 模板插值"""
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
                    return str(bm.group(idx))
                except IndexError:
                    return ''
            return ''
        if expr == 'baseUrl':
            return base_url or ''
        # 标准 $.path 取值
        val = jpath(expr, data)
        return str(val) if val is not None else ''
    return re.sub(r'\{\{(.+?)\}\}', repl, tpl)


def jpath(expr, data):
    """JSONPath 风格取值（支持 [*]、[N]、.. 递归查找）"""
    if not expr or data is None:
        return data

    # Strip @put:{...} / @get:{...} / @Header:{...} suffixes
    expr = re.sub(r'@\w+:\{[^}]*\}', '', expr).strip()
    # Strip leading '-' (Legado CSS negation prefix, harmless on JSON paths)
    expr = re.sub(r'^-', '', expr).strip()

    # Handle .. recursive descent: search all levels for the remaining path
    if expr.startswith('$..'):
        remaining = expr[3:]  # path after $..
        if remaining.startswith('.'):
            remaining = remaining[1:]
        result = _recursive_jpath(remaining, data)
        return result

    if expr.startswith('$.'):
        expr = expr[2:]
    # Strip leading dot (from .. paths)
    expr = expr.lstrip('.')

    for part in re.split(r'\.(?![^\[]*\])', expr):
        if not part:
            continue
        if data is None:
            return None
        if part.endswith('[*]'):
            key = part[:-3]
            if not key:
                # Bare [*] — keep data if it's already a list (e.g. $.data.[*])
                if isinstance(data, list):
                    continue
                return data if isinstance(data, list) else None
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


def _recursive_jpath(path, data, depth=0):
    """递归搜索 JSON 树中匹配 path 的数组（用于 $.. 语法）
    支持 key[*]、key[N] 数组索引。
    """
    if depth > 20 or data is None:
        return None

    # Parse path into parts, handling [*] and [N] suffixes
    raw_parts = path.split('.')
    parts = []
    for rp in raw_parts:
        if not rp:
            continue
        if rp.endswith('[*]'):
            parts.append((rp[:-3], '[*]', None))
        elif re.match(r'.+\[\d+\]$', rp):
            key, idx = re.search(r'(.+)\[(\d+)\]$', rp).groups()
            parts.append((key, '[N]', int(idx)))
        else:
            parts.append((rp, None, None))

    if isinstance(data, dict):
        # Try direct match at current level
        cur = data
        found = True
        for key, suffix, idx in parts:
            if isinstance(cur, dict):
                cur = cur.get(key)
            else:
                found = False
                break
            if suffix == '[*]':
                if isinstance(cur, list):
                    pass  # keep the list as-is
                else:
                    found = False
                    break
            elif suffix == '[N]' and isinstance(cur, list):
                try:
                    cur = cur[idx]
                except (IndexError, TypeError):
                    found = False
                    break
        if found and cur is not None:
            return cur

        # Recurse into dict values
        for v in data.values():
            result = _recursive_jpath(path, v, depth + 1)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _recursive_jpath(path, item, depth + 1)
            if result is not None:
                return result
    return None


def walk_path(data, path):
    """简化版路径遍历（不支持 [*]、[N]）"""
    if not path or data is None:
        return data
    # Strip leading '-' (Legado CSS negation prefix)
    path = re.sub(r'^-', '', path).strip()
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
