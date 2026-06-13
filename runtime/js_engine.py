"""
Legado JS 双路径执行引擎
- PyMiniRacer: 简单 <js>inline</js> 变换（无 ajax 依赖），~1ms
- NodeWorker:  需要 java.ajax() 的完整 @js: 块，~5ms（持久进程）
"""
import json
import subprocess
import sys
import threading
import hashlib
import base64
import uuid
import logging
from pathlib import Path

from py_mini_racer import MiniRacer

logger = logging.getLogger(__name__)

# ── PyMiniRacer 最小 shims ──
# 只包含 btoa/atob、java.put/get、source.put/get 等不需要 ajax 的 API
# 完整 API（ajax、md5、HMAC 等）在 js_worker.js 中有 Node.js 原生实现

SIMPLE_SHIMS_JS = r"""
// ── btoa / atob polyfill ──
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

// ── 存储 ──
var __py_store = {};

var java = {
    ajax: function() { throw new Error('java.ajax not available in simple mode, use full mode'); },
    ajaxAll: function() { throw new Error('java.ajaxAll not available in simple mode'); },
    base64Encode: function(s) { return btoa(utf8Encode(String(s))); },
    base64Decode: function(s) { return utf8Decode(atob(String(s))); },
    encodeURI: function(s) { return encodeURIComponent(String(s)); },
    randomUUID: function() { return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) { var r = Math.random()*16|0; return (c==='x'?r:(r&0x3|0x8)).toString(16); }); },
    put: function(k, v) { __py_store[k] = v; return v; },
    get: function(k) { return __py_store[k] || ''; },
    log: function() {},
    toast: function() {},
    longToast: function() {},
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
            self._ctx.eval('__py_store = {};')
            self._ctx.eval(f'source.key = {json.dumps(source_url)};')

            wrapped = self._wrap_js_code(code)

            fn_code = f"""
            (function() {{
                var result = {json.dumps(str(result_value))};
                var key = '';
                var page = 1;
                try {{
                    var fn = new Function('java', 'cookie', 'source', 'key', 'page', 'result', {json.dumps(wrapped)});
                    return fn(java, cookie, source, key, page, result);
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
    def _wrap_js_code(code):
        """包装 JS 代码以正确捕获返回值

        原 _auto_return 在首行加 return 会把多语句代码（如 a=...; b=...; expr）
        变成 return a=...; b=...（后半截死代码）。改为 eval() 方式：
        eval("stmt1; stmt2; lastExpr") 返回 lastExpr 的值，所有语句都会执行。
        """
        import re as _re
        code = code.strip()
        if not code:
            return 'return ' + json.dumps(code)
        # 已有显式 return → 直接使用
        if _re.search(r'(?:^|;|\n)\s*return\s', code):
            return code
        # 用 eval 捕获 completion value
        return 'return eval(' + json.dumps(code) + ')'


def _resolve_node_executable():
    """
    定位 node 可执行文件，优先级：
      1. PyInstaller 解包目录 (sys._MEIPASS) 下的 runtime/bin/node[.exe]
      2. 项目内便携版 runtime/bin/node[.exe]
      3. 系统 PATH 中的 node（开发兜底）
    """
    exe_name = 'node.exe' if sys.platform == 'win32' else 'node'
    candidates = []
    # PyInstaller 解包目录
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        candidates.append(Path(meipass) / 'runtime' / 'bin' / exe_name)
    # 项目内便携版（开发模式）
    candidates.append(Path(__file__).parent / 'bin' / exe_name)
    for p in candidates:
        if p.exists():
            return str(p)
    return 'node'  # 系统兜底


class NodeWorker:
    """持久 Node.js 子进程，行分隔 JSON 通信"""

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._request_count = 0
        self._max_requests = 2000
        self._node_cmd = _resolve_node_executable()
        self._start()

    def _start(self):
        worker_path = Path(__file__).parent / 'js_worker.js'
        # PyInstaller 模式下 js_worker.js 也在 _MEIPASS/runtime/ 下
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass and not worker_path.exists():
            worker_path = Path(meipass) / 'runtime' / 'js_worker.js'
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        try:
            self._proc = subprocess.Popen(
                [self._node_cmd, str(worker_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding='utf-8',
                creationflags=flags,
            )
        except FileNotFoundError:
            logger.error(
                "无法启动 Node.js worker：未找到 node 可执行文件 (%s)。"
                "若为开发模式请确保系统已安装 Node.js，"
                "若为发布版请检查 runtime/bin/node%s 是否存在。",
                self._node_cmd, '.exe' if sys.platform == 'win32' else ''
            )
            raise
        self._request_count = 0
        logger.info("NodeWorker started (pid=%s, exe=%s)", self._proc.pid, self._node_cmd)

    def call(self, params_dict):
        """发送一个请求，获取一个响应。线程安全。"""
        with self._lock:
            return self._call_unsafe(params_dict)

    def _call_unsafe(self, params_dict):
        self._request_count += 1
        if self._request_count > self._max_requests:
            self._restart_unsafe()
        if self._proc.poll() is not None:
            logger.warning("NodeWorker died, restarting...")
            self._restart_unsafe()
        try:
            line = json.dumps(params_dict, ensure_ascii=False) + '\n'
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
            result_line = self._proc.stdout.readline()
            if not result_line:
                logger.warning("NodeWorker returned empty, restarting...")
                self._restart_unsafe()
                return {'error': 'Worker restarted, please retry'}
            return json.loads(result_line.strip())
        except (BrokenPipeError, OSError, json.JSONDecodeError) as e:
            logger.warning("NodeWorker communication error: %s, restarting...", e)
            self._restart_unsafe()
            return {'error': f'Worker error: {e}'}

    def _restart_unsafe(self):
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
        with self._lock:
            self._restart_unsafe()

    def is_alive(self):
        return self._proc is not None and self._proc.poll() is None


class LegadoRuntime:
    """双路径 JS 执行引擎"""

    _AJAX_KEYWORDS = ('ajax', 'ajaxAll', 'getString', 'getStringList', 'getElements',
                      'connect(', 'startBrowser', 'webView(', 'require(',
                      'md5Encode', 'HMacHex', 'desEncode', 'aesBase64')

    def __init__(self):
        self._simple = SimpleRuntime()
        self._worker = NodeWorker()

    def _needs_full_mode(self, code):
        code_lower = code.lower()
        return any(kw.lower() in code_lower for kw in self._AJAX_KEYWORDS)

    def execute_simple(self, code, result_value='', source_url=''):
        return self._simple.execute(code, result_value, source_url)

    def execute_full(self, code, key='', page=1, source_url='',
                     headers=None, store=None, result_value=''):
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
        """执行 Legado <js> 或 @js: 代码块"""
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
        """执行完整 JS 代码（@js: 块级别）"""
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
