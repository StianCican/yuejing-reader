/**
 * Legado JS 持久工作进程
 * 行分隔 JSON 通信：每行一个请求，每行一个响应
 * 用法: node js_worker.js （由 Python 端通过 stdin/stdout 管道通信）
 *
 * 相比 js_runner.js（单次执行退出），本文件是长驻进程：
 * - 避免每次调用的 Node.js 启动开销（~200ms → ~5ms）
 * - 保持 V8 引擎热启动状态
 * - 逐行读取请求，逐行写回响应
 */

const https = require('https');
const http = require('http');
const crypto = require('crypto');
const { URL } = require('url');
const readline = require('readline');

// ── 行分隔 JSON 通信 ──
const rl = readline.createInterface({ input: process.stdin, terminal: false });

rl.on('line', (line) => {
    try {
        const params = JSON.parse(line);
        const result = execute(params);
        process.stdout.write(JSON.stringify(result) + '\n');
    } catch (e) {
        process.stdout.write(JSON.stringify({ error: e.message, stack: e.stack }) + '\n');
    }
});

rl.on('close', () => {
    process.exit(0);
});

// 心跳：如果 5 分钟无输入则退出（防止僵尸进程）
let lastActivity = Date.now();
setInterval(() => {
    if (Date.now() - lastActivity > 5 * 60 * 1000) {
        process.exit(0);
    }
}, 30000);

// 标记活跃
rl.on('line', () => { lastActivity = Date.now(); });


function execute(params) {
    const { code, key, page, sourceUrl, headers: reqHeaders, store, result: inputResult } = params;

    // 存储空间（模拟 java.put/get 和 source.put/get）
    const sessionStore = store || {};
    const responseHeaders = {};

    // ── java.* API 模拟 ──
    const java = {
        // HTTP 请求（同步，用 curl）
        ajax(urlOrOpts) {
            let url, opts = {};
            if (typeof urlOrOpts === 'string') {
                const idx = urlOrOpts.indexOf(',{');
                if (idx > 0) {
                    url = urlOrOpts.substring(0, idx);
                    try { opts = JSON.parse(urlOrOpts.substring(idx + 1)); } catch(e) {}
                } else {
                    url = urlOrOpts;
                }
            } else if (typeof urlOrOpts === 'object') {
                url = urlOrOpts.url || '';
                opts = urlOrOpts;
            }
            if (!url) return '';

            const { execSync } = require('child_process');
            const method = (opts.method || 'GET').toUpperCase();
            const headers = { ...reqHeaders, ...(opts.headers || {}) };

            let curlCmd = `curl -s -L --max-time 15 -X ${method}`;
            for (const [k, v] of Object.entries(headers)) {
                const escaped = String(v).replace(/"/g, '\\"');
                curlCmd += ` -H "${k}: ${escaped}"`;
            }
            if (opts.body) {
                const bodyStr = typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body);
                const tmpFile = require('os').tmpdir() + '/legado_req_body_' + Date.now() + '.json';
                require('fs').writeFileSync(tmpFile, bodyStr, 'utf8');
                curlCmd += ` -d @${tmpFile}`;
                try {
                    const result = execSync(curlCmd + ` "${url}"`, { encoding: 'utf8', timeout: 15000, windowsHide: true });
                    require('fs').unlinkSync(tmpFile);
                    return result;
                } catch (e) {
                    try { require('fs').unlinkSync(tmpFile); } catch(_){}
                    return '';
                }
            }

            try {
                return execSync(curlCmd + ` "${url}"`, { encoding: 'utf8', timeout: 15000, windowsHide: true });
            } catch (e) {
                return '';
            }
        },

        // 并发 HTTP 请求
        ajaxAll(urls) {
            if (!Array.isArray(urls)) return [];
            return urls.map(u => java.ajax(u));
        },

        // MD5
        md5Encode(str) {
            return crypto.createHash('md5').update(String(str)).digest('hex');
        },

        // Base64
        base64Encode(str) {
            return Buffer.from(String(str)).toString('base64');
        },
        base64Decode(str) {
            return Buffer.from(String(str), 'base64').toString('utf8');
        },

        // Hex
        hexDecodeToString(hex) {
            return Buffer.from(String(hex), 'hex').toString('utf8');
        },

        // URL 编码
        encodeURI(str) {
            return encodeURIComponent(String(str));
        },

        // HMAC
        HMacHex(data, algorithm, key) {
            const algo = algorithm.toLowerCase().replace('hmac', '');
            return crypto.createHmac(algo, String(key)).update(String(data)).digest('hex');
        },

        // DES 加密
        desEncodeToBase64String(data, key, algo, iv) {
            try {
                const cipher = crypto.createCipheriv('des-ecb', Buffer.from(String(key).substring(0, 8)), null);
                let encrypted = cipher.update(String(data), 'utf8', 'base64');
                encrypted += cipher.final('base64');
                return encrypted;
            } catch (e) {
                return '';
            }
        },

        // AES 解密
        aesBase64DecodeToString(data, key, algorithm, iv) {
            try {
                const keyBuf = Buffer.from(String(key), 'utf8');
                const ivBuf = iv ? Buffer.from(String(iv), 'utf8') : Buffer.alloc(16, 0);
                const keyLen = keyBuf.length;
                let algo = 'aes-128-cbc';
                if (keyLen >= 32) algo = 'aes-256-cbc';
                else if (keyLen >= 24) algo = 'aes-192-cbc';
                else if (keyLen >= 16) algo = 'aes-128-cbc';
                const decipher = crypto.createDecipheriv(algo, keyBuf.subarray(0, keyLen >= 32 ? 32 : keyLen >= 24 ? 24 : 16), ivBuf.subarray(0, 16));
                let decrypted = decipher.update(Buffer.from(String(data), 'base64'));
                decrypted = Buffer.concat([decrypted, decipher.final()]);
                return decrypted.toString('utf8');
            } catch (e) {
                return '';
            }
        },

        aesBase64DecodeToByteArray(data, key, algorithm, iv) {
            try {
                const keyBuf = Buffer.from(String(key), 'utf8');
                const ivBuf = iv ? Buffer.from(String(iv), 'utf8') : Buffer.alloc(16, 0);
                const keyLen = keyBuf.length;
                let algo = 'aes-128-cbc';
                if (keyLen >= 32) algo = 'aes-256-cbc';
                else if (keyLen >= 24) algo = 'aes-192-cbc';
                else if (keyLen >= 16) algo = 'aes-128-cbc';
                const decipher = crypto.createDecipheriv(algo, keyBuf.subarray(0, keyLen >= 32 ? 32 : keyLen >= 24 ? 24 : 16), ivBuf.subarray(0, 16));
                let decrypted = decipher.update(Buffer.from(String(data), 'base64'));
                decrypted = Buffer.concat([decrypted, decipher.final()]);
                return decrypted.toString('base64');
            } catch (e) {
                return '';
            }
        },

        // UUID
        randomUUID() {
            return crypto.randomUUID();
        },

        // 存储
        put(key, value) {
            sessionStore[key] = value;
            return value;
        },
        get(key) {
            return sessionStore[key] || '';
        },

        // 日志
        log() {},
        toast() {},
        longToast() {},

        // 时间格式化
        timeFormat(ts) {
            return new Date(ts).toLocaleString('zh-CN');
        },
        timeFormatUTC(ts, fmt, offset) {
            return new Date(ts).toISOString();
        },

        // JSONPath（简化版，从运行时传入）
        getString(path) { return ''; },
        getStringList(path) { return []; },
        getElements(path) { return []; },

        // 其他
        androidId() { return '0000000000000000'; },
        getWebViewUA() { return 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36'; },
        t2s(text) { return String(text); },
        openUrl() {},
        startBrowser() {},
        startBrowserAwait() {},
        refreshTocUrl() {},
        webView() { return ''; },
        connect() { return { raw: () => ({ request: () => ({ url: () => '' }) }) }; },
        post() { return { header: () => '' }; },
        get() { return ''; },
        setCookie() {},
        getCookie() { return ''; },
    };

    // ── cookie.* API 模拟 ──
    const cookie = {
        getKey(domain, key) { return ''; },
        getCookie(url) { return ''; },
        setCookie() {},
        removeCookie() {},
    };

    // ── source.* API 模拟 ──
    const source = {
        key: sourceUrl || '',
        getKey() { return sourceUrl || ''; },
        header: {},
        getVariable() { return sessionStore.__sourceVar || '{}'; },
        setVariable(v) { sessionStore.__sourceVar = v; },
        getLoginInfoMap() { return null; },
        loginUrl: '',
        bookSourceComment: '',
        put(k, v) { sessionStore['src_' + k] = v; },
        get(k) { return sessionStore['src_' + k] || ''; },
    };

    // ── 其他全局对象 ──
    const Packages = {
        java: { util: { UUID: { randomUUID: () => crypto.randomUUID() } } },
        android: {
            os: { Build: { MODEL: 'PC', MANUFACTURER: 'Unknown' } },
            text: { TextUtils: { isEmpty: (s) => !s || s.length === 0 } },
        },
    };

    // ── 执行 JS 代码 ──
    try {
        let wrappedCode = code;
        const lines = code.split('\n');
        let lastIdx = lines.length - 1;
        while (lastIdx >= 0 && lines[lastIdx].trim() === '') lastIdx--;
        if (lastIdx >= 0 && !lines[lastIdx].trim().startsWith('return ')) {
            lines[lastIdx] = 'return ' + lines[lastIdx];
            wrappedCode = lines.join('\n');
        }
        const fn = new Function('java', 'cookie', 'source', 'key', 'page', 'Packages', 'result', wrappedCode);
        const execResult = fn(java, cookie, source, key || '', page || 1, Packages, inputResult || '');

        const storedHeaders = sessionStore.headers ? JSON.parse(sessionStore.headers) : {};

        return {
            result: execResult,
            store: sessionStore,
            headers: { ...reqHeaders, ...storedHeaders },
        };
    } catch (e) {
        return { error: e.message, stack: e.stack };
    }
}
