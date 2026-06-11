"""
Legado JS 双路径执行引擎
- PyMiniRacer: 简单 <js>inline</js> 变换（无 ajax 依赖），~1ms
- NodeWorker:  需要 java.ajax() 的完整 @js: 块，~5ms（持久进程，无需每次 fork）

替代原来的 subprocess.run(["node", js_runner.js]) 单次执行模式。
"""

import json
import os
import subprocess
import sys
import threading
import time
import hashlib
import base64
import uuid
import re
import logging
from pathlib import Path

from py_mini_racer import MiniRacer

logger = logging.getLogger(__name__)

# ── PyMiniRacer 简单 shims（注入到 V8 上下文） ──

SIMPLE_SHIMS_JS = r"""
// ── btoa / atob polyfill（V8 纯上下文无此函数）──
var btoa = function(s) {
    var chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=';
    var r = '';
    for (var i = 0; i < s.length; i += 3) {
        var a = s.charCodeAt(i), b = s.charCodeAt(i+1), c = s.charCodeAt(i+2);
        r += chars[a >> 2] + chars[((a & 3) << 4) | (b >> 4)];
        r += (isNaN(b) ? '=' : chars[((b & 15) << 2) | (c >> 6)]);
        r += (isNaN(c) ? '=' : chars[c & 63]);
    }
    return r;
};
var atob = function(s) {
    var chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=';
    var r = '';
    s = s.replace(/=+$/, '');
    for (var i = 0; i < s.length; i += 4) {
        var a = chars.indexOf(s[i]), b = chars.indexOf(s[i+1]);
        var c = chars.indexOf(s[i+2]), d = chars.indexOf(s[i+3]);
        r += String.fromCharCode((a << 2) | (b >> 4));
        if (c !== -1) r += String.fromCharCode(((b & 15) << 4) | (c >> 2));
        if (d !== -1) r += String.fromCharCode(((c & 3) << 6) | d);
    }
    return r;
};

// ── UTF-8 安全的 btoa/atob ──
function utf8Encode(s) {
    var r = '';
    for (var i = 0; i < s.length; i++) {
        var c = s.charCodeAt(i);
        if (c < 128) r += String.fromCharCode(c);
        else if (c < 2048) { r += String.fromCharCode(192|(c>>6)); r += String.fromCharCode(128|(c&63)); }
        else { r += String.fromCharCode(224|(c>>12)); r += String.fromCharCode(128|((c>>6)&63)); r += String.fromCharCode(128|(c&63)); }
    }
    return r;
}
function utf8Decode(s) {
    var r = '', i = 0;
    while (i < s.length) {
        var c = s.charCodeAt(i);
        if (c < 128) { r += String.fromCharCode(c); i++; }
        else if (c < 224) { r += String.fromCharCode(((c&31)<<6)|(s.charCodeAt(i+1)&63)); i+=2; }
        else { r += String.fromCharCode(((c&15)<<12)|((s.charCodeAt(i+1)&63)<<6)|(s.charCodeAt(i+2)&63)); i+=3; }
    }
    return r;
}

// ── Buffer shim ──
var Buffer = {
    from: function(data, encoding) {
        if (typeof data !== 'string') return { toString: function() { return String(data); }, subarray: function() { return Buffer.from(data, encoding); } };
        if (encoding === 'base64') {
            var decoded = utf8Decode(atob(data));
            return { toString: function(enc) { return decoded; }, subarray: function(s, e) { return Buffer.from(decoded.substring(s, e)); } };
        }
        if (encoding === 'hex') {
            var r = '';
            for (var i = 0; i < data.length; i += 2) r += String.fromCharCode(parseInt(data.substr(i, 2), 16));
            return { toString: function() { return r; }, subarray: function() { return Buffer.from(r); } };
        }
        // 默认：字符串本身
        return {
            toString: function(enc) {
                if (enc === 'base64') return btoa(utf8Encode(data));
                if (enc === 'hex') { var h=''; for(var i=0;i<data.length;i++) h+=data.charCodeAt(i).toString(16).padStart(2,'0'); return h; }
                return data;
            },
            subarray: function(s, e) { return Buffer.from(data.substring(s, e)); }
        };
    }
};

// ── crypto stub ──
var crypto = {
    createHash: function() { return { update: function() { return this; }, digest: function() { return ''; } }; },
    createHmac: function() { return { update: function() { return this; }, digest: function() { return ''; } }; },
    createCipheriv: function() { throw new Error('Not available in simple mode'); },
    createDecipheriv: function() { throw new Error('Not available in simple mode'); },
    randomUUID: function() { return '00000000-0000-0000-0000-000000000000'; }
};

// ── 存储 ──
var __py_store = {};

var java = {
    ajax: function() { throw new Error('java.ajax not available in simple mode, use full mode'); },
    ajaxAll: function() { throw new Error('java.ajaxAll not available in simple mode'); },
    md5Encode: function(s) { return ''; },
    base64Encode: function(s) { return btoa(utf8Encode(String(s))); },
    base64Decode: function(s) { return utf8Decode(atob(String(s))); },
    hexDecodeToString: function(hex) {
        var r = '';
        for (var i = 0; i < hex.length; i += 2) r += String.fromCharCode(parseInt(hex.substr(i, 2), 16));
        return utf8Decode(r);
    },
    encodeURI: function(s) { return encodeURIComponent(String(s)); },
    HMacHex: function() { return ''; },
    desEncodeToBase64String: function() { return ''; },
    aesBase64DecodeToString: function() { return ''; },
    aesBase64DecodeToByteArray: function() { return ''; },
    randomUUID: function() { return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) { var r = Math.random()*16|0; return (c==='x'?r:(r&0x3|0x8)).toString(16); }); },
    put: function(k, v) { __py_store[k] = v; return v; },
    get: function(k) { return __py_store[k] || ''; },
    log: function() {},
    toast: function() {},
    longToast: function() {},
    timeFormat: function(ts) { return new Date(ts).toLocaleString('zh-CN'); },
    timeFormatUTC: function(ts) { return new Date(ts).toISOString(); },
    getString: function() { return ''; },
    getStringList: function() { return []; },
    getElements: function() { return []; },
    androidId: function() { return '0000000000000000'; },
    getWebViewUA: function() { return 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36'; },
    t2s: function(t) { return String(t); },
    openUrl: function() {},
    startBrowser: function() {},
    startBrowserAwait: function() {},
    refreshTocUrl: function() {},
    webView: function() { return ''; },
    connect: function() { return { raw: function() { return { request: function() { return { url: function() { return ''; } } } } } }; },
    post: function() { return { header: function() { return ''; } }; },
    get: function() { return ''; },
    setCookie: function() {},
    getCookie: function() { return ''; },
};

var cookie = {
    getKey: function() { return ''; },
    getCookie: function() { return ''; },
    setCookie: function() {},
    removeCookie: function() {},
};

var source = {
    key: '', getKey: function() { return ''; }, header: {},
    getVariable: function() { return __py_store.__sourceVar || '{}'; },
    setVariable: function(v) { __py_store.__sourceVar = v; },
    getLoginInfoMap: function() { return null; },
    loginUrl: '', bookSourceComment: '',
    put: function(k, v) { __py_store['src_' + k] = v; },
    get: function(k) { return __py_store['src_' + k] || ''; },
};

var Packages = {
    java: { util: { UUID: { randomUUID: function() { return java.randomUUID(); } } } },
    android: {
        os: { Build: { MODEL: 'PC', MANUFACTURER: 'Unknown' } },
        text: { TextUtils: { isEmpty: function(s) { return !s || s.length === 0; } } },
    },
};
"""


class SimpleRuntime:
    """PyMiniRacer 简单 JS 执行器，用于无 ajax 的 <js>inline</js> 变换"""

    def __init__(self):
        self._ctx = MiniRacer()
        self._ctx.eval(SIMPLE_SHIMS_JS)
        self._initialized = True

    @staticmethod
    def _py_md5(s):
        return hashlib.md5(s.encode('utf-8', errors='replace')).hexdigest()

    @staticmethod
    def _py_base64_encode(s):
        return base64.b64encode(s.encode('utf-8', errors='replace')).decode('ascii')

    @staticmethod
    def _py_base64_decode(s):
        return base64.b64decode(s).decode('utf-8', errors='replace')

    @staticmethod
    def _py_uuid():
        return str(uuid.uuid4())

    @staticmethod
    def _py_encode_uri(s):
        import urllib.parse
        return urllib.parse.quote(s, safe='')

    def execute(self, code, result_value='', source_url=''):
        """执行简单 JS 变换，返回字符串结果"""
        try:
            # 重置 store
            self._ctx.eval('__py_store = {};')
            self._ctx.eval(f'source.key = {json.dumps(source_url)};')

            # 自动为最后一行加 return
            wrapped = self._auto_return(code)

            # 构造执行函数
            fn_code = f"""
            (function() {{
                var result = {json.dumps(str(result_value))};
                var key = '';
                var page = 1;
                try {{
                    var fn = new Function('java', 'cookie', 'source', 'key', 'page', 'Packages', 'result', {json.dumps(wrapped)});
                    return fn(java, cookie, source, key, page, Packages, result);
                }} catch(e) {{
                    return result;
                }}
            }})()
            """
            val = self._ctx.eval(fn_code)
            if val is None:
                return str(result_value)
            return str(val)
        except Exception as e:
            logger.debug(f"SimpleRuntime error: {e}")
            return str(result_value)

    @staticmethod
    def _auto_return(code):
        """为不含 return 的代码最后一行自动加 return"""
        lines = code.split('\n')
        last_idx = len(lines) - 1
        while last_idx >= 0 and lines[last_idx].strip() == '':
            last_idx -= 1
        if last_idx >= 0 and not lines[last_idx].strip().startswith('return '):
            lines[last_idx] = 'return ' + lines[last_idx]
        return '\n'.join(lines)


class NodeWorker:
    """持久 Node.js 子进程，行分隔 JSON 通信"""

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._request_count = 0
        self._max_requests = 2000  # 每隔 N 次请求重启，防内存泄漏
        self._start()

    def _start(self):
        worker_path = Path(__file__).parent / 'js_worker.js'
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        self._proc = subprocess.Popen(
            ['node', str(worker_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding='utf-8',
            creationflags=flags,
        )
        self._request_count = 0
        logger.info("NodeWorker started (pid=%s)", self._proc.pid)

    def call(self, params_dict):
        """发送一个请求，获取一个响应。线程安全。"""
        with self._lock:
            return self._call_unsafe(params_dict)

    def _call_unsafe(self, params_dict):
        """内部调用，不加锁"""
        self._request_count += 1

        # 定期重启防止内存泄漏
        if self._request_count > self._max_requests:
            self._restart_unsafe()

        # 检查进程是否存活
        if self._proc.poll() is not None:
            logger.warning("NodeWorker died, restarting...")
            self._restart_unsafe()

        try:
            line = json.dumps(params_dict, ensure_ascii=False) + '\n'
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
            result_line = self._proc.stdout.readline()
            if not result_line:
                # 进程可能已死
                logger.warning("NodeWorker returned empty, restarting...")
                self._restart_unsafe()
                return {'error': 'Worker restarted, please retry'}
            return json.loads(result_line.strip())
        except (BrokenPipeError, OSError, json.JSONDecodeError) as e:
            logger.warning("NodeWorker communication error: %s, restarting...", e)
            self._restart_unsafe()
            return {'error': f'Worker error: {e}'}

    def _restart_unsafe(self):
        """杀死并重启工作进程（不加锁，调用者负责加锁）"""
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._start()

    def restart(self):
        """公开重启方法（加锁）"""
        with self._lock:
            self._restart_unsafe()

    def is_alive(self):
        return self._proc is not None and self._proc.poll() is None


class LegadoRuntime:
    """双路径 JS 执行引擎"""

    # 需要走 NodeWorker 的关键字
    _AJAX_KEYWORDS = ('ajax', 'ajaxAll', 'getString', 'getStringList', 'getElements',
                      'connect(', 'startBrowser', 'webView(', 'require(',
                      'md5Encode', 'HMacHex', 'desEncode', 'aesBase64')

    def __init__(self):
        self._simple = SimpleRuntime()
        self._worker = NodeWorker()

    def _needs_full_mode(self, code):
        """判断代码是否需要 ajax 等 Node.js 专属 API"""
        code_lower = code.lower()
        return any(kw.lower() in code_lower for kw in self._AJAX_KEYWORDS)

    def execute_simple(self, code, result_value='', source_url=''):
        """简单 JS 变换（PyMiniRacer，~1ms）"""
        return self._simple.execute(code, result_value, source_url)

    def execute_full(self, code, key='', page=1, source_url='',
                     headers=None, store=None, result_value=''):
        """完整 JS 执行（NodeWorker，~5ms）"""
        params = {
            'code': str(code),
            'key': str(key or ''),
            'page': page or 1,
            'sourceUrl': str(source_url or ''),
            'headers': headers or {},
            'store': store or {},
            'result': str(result_value or ''),
        }
        return self._worker.call(params)

    def run_legado_js(self, js_code, result_value='', source_url=''):
        """替换原 app.py 的 run_legado_js()"""
        if self._needs_full_mode(js_code):
            result = self.execute_full(js_code, source_url=source_url, result_value=result_value)
            if 'error' in result:
                logger.debug(f"Legado JS full mode error: {result['error']}")
                return str(result_value)
            val = result.get('result', result_value)
            return str(val) if val is not None else str(result_value)
        else:
            return self.execute_simple(js_code, result_value, source_url)

    def run_js(self, code, key='', page=1, source_url='', headers=None, store=None):
        """替换原 app.py 的 run_js()"""
        result = self.execute_full(code, key=key, page=page, source_url=source_url,
                                   headers=headers, store=store)
        return result


# ── 全局单例 ──
_runtime = None
_runtime_lock = threading.Lock()


def get_runtime():
    """获取全局 LegadoRuntime 单例"""
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = LegadoRuntime()
    return _runtime
