"""Legado @put/@get 变量系统

使用 contextvars 替代 threading.local，避免线程池中的变量泄漏。
"""
import re
import contextvars

# 上下文变量，每次搜索入口处重置
_var_store: contextvars.ContextVar[dict] = contextvars.ContextVar('_var_store', default={})


def _get_vars():
    """获取当前上下文的变量字典"""
    return _var_store.get()


def _set_var(key, value):
    """存变量"""
    store = _var_store.get()
    store = dict(store)  # 复制以触发 ContextVar 更新
    store[key] = str(value) if value is not None else ''
    _var_store.set(store)


def _read_var(key):
    """取变量"""
    return _var_store.get().get(key, '')


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
    from .parser import _resolve_rule
    matches = list(re.finditer(r'@put:\{([^}]+)\}', rule))
    for m in matches:
        body = m.group(1)
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
    return re.sub(r'@put:\{[^}]*\}', '', rule).strip()


def reset_vars():
    """重置变量存储（在每次搜索入口调用）"""
    _var_store.set({})
