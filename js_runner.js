/**
 * Legado JS 执行器
 * 接收 JSON 参数，执行 JS 代码，返回结果
 * 用法: node js_runner.js '{"code":"...","key":"...","page":1,"sourceUrl":"...","headers":{},"store":{}}'
 */

const https = require('https');
const http = require('http');
const crypto = require('crypto');
const { URL } = require('url');

// 从 stdin 读取输入
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', () => {
    try {
        const params = JSON.parse(input);
        const result = execute(params);
        process.stdout.write(JSON.stringify(result));
    } catch (e) {
        process.stdout.write(JSON.stringify({ error: e.message, stack: e.stack }));
    }
});

function execute(params) {
    const { code, key, page, sourceUrl, headers: reqHeaders, store, result: inputResult } = params;

    // 存储空间（模拟 java.put/get 和 source.put/get）
    const sessionStore = store || {};
    const responseHeaders = {};

    // ── java.* API 模拟 ──
    const java = {
        // HTTP 请求
        ajax(urlOrOpts) {
            let url, opts = {};
            if (typeof urlOrOpts === 'string') {
                // 解析 "url,{headers:{...}}" 格式
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

            // 同步 HTTP 请求（使用 child_process.execSync）
            const { execSync } = require('child_process');
            const method = (opts.method || 'GET').toUpperCase();
            const headers = { ...reqHeaders, ...(opts.headers || {}) };

            let curlCmd = `curl -s -L --max-time 10 -X ${method}`;
            for (const [k, v] of Object.entries(headers)) {
                curlCmd += ` -H "${k}: ${v}"`;
            }
            if (opts.body) {
                curlCmd += ` -d '${opts.body.replace(/'/g, "\\'")}'`;
            }
            curlCmd += ` "${url}"`;

            try {
                return execSync(curlCmd, { encoding: 'utf8', timeout: 120000, windowsHide: true });
            } catch (e) {
                return '';
            }
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

        // AES 解密（Legado: java.aesBase64DecodeToString）
        aesBase64DecodeToString(data, key, algorithm, iv) {
            try {
                const keyBuf = Buffer.from(String(key), 'utf8');
                const ivBuf = Buffer.from(String(iv), 'utf8');
                // 根据 key 长度选择 AES 变体
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

        // AES 解密到 byte array（Legado: java.aesBase64DecodeToByteArray）
        aesBase64DecodeToByteArray(data, key, algorithm, iv) {
            try {
                const keyBuf = Buffer.from(String(key), 'utf8');
                const ivBuf = Buffer.from(String(iv), 'utf8');
                const keyLen = keyBuf.length;
                let algo = 'aes-128-cbc';
                if (keyLen >= 32) algo = 'aes-256-cbc';
                else if (keyLen >= 24) algo = 'aes-192-cbc';
                else if (keyLen >= 16) algo = 'aes-128-cbc';
                const decipher = crypto.createDecipheriv(algo, keyBuf.subarray(0, keyLen >= 32 ? 32 : keyLen >= 24 ? 24 : 16), ivBuf.subarray(0, 16));
                let decrypted = decipher.update(Buffer.from(String(data), 'base64'));
                decrypted = Buffer.concat([decrypted, decipher.final()]);
                return decrypted.toString('base64');  // Legado 返回 base64 编码的 byte array
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

        // 日志（静默）
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

        // JSONPath（简化版）
        getString(path) {
            return '';
        },
        getStringList(path) {
            return [];
        },

        // 其他
        androidId() { return '0000000000000000'; },
        getWebViewUA() { return 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36'; },
        t2s(text) { return String(text); }, // 繁转简，简化为直接返回
        openUrl() {},
        startBrowser() {},
        startBrowserAwait() {},
        refreshTocUrl() {},
        webView() { return ''; },
        connect() { return { raw: () => ({ request: () => ({ url: () => '' }) }) }; },
        post() { return { header: () => '' }; },
        get() { return ''; },
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
        // 自动为不含顶层 return 的代码添加 return（Legado <js> 块的最后表达式即返回值）
        let wrappedCode = code;
        const lines = code.split('\n');
        let lastIdx = lines.length - 1;
        while (lastIdx >= 0 && lines[lastIdx].trim() === '') lastIdx--;
        if (lastIdx >= 0 && !lines[lastIdx].trim().startsWith('return ')) {
            lines[lastIdx] = 'return ' + lines[lastIdx];
            wrappedCode = lines.join('\n');
        }
        // 构造执行上下文
        const fn = new Function('java', 'cookie', 'source', 'key', 'page', 'Packages', 'result', wrappedCode);
        const execResult = fn(java, cookie, source, key || '', page || 1, Packages, inputResult || '');

        // 收集需要传递给后续请求的 headers
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
