"""
本地小说聚合阅读器
从 Legado 书源 JSON 加载规则，聚合多个网站的小说内容，浏览器打开即用。
"""
import json, re, sys, os, socket, ipaddress
from pathlib import Path

# Windows 控制台 UTF-8
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from flask import Flask, request, jsonify, render_template, Response
from sources import SourceManager
from utils.http import _parse_inline_header, session
from utils.paths import shelf_file, user_data_path, resource_path

mgr = SourceManager()

# ── 旧版数据迁移：把项目根的 shelf.json 一次性挪到用户数据目录 ──
_legacy_shelf = Path(__file__).parent / 'shelf.json'
SHELF_FILE = shelf_file()
if _legacy_shelf.exists() and not SHELF_FILE.exists():
    try:
        SHELF_FILE.write_text(_legacy_shelf.read_text(encoding='utf-8'), encoding='utf-8')
        print(f'📦 已迁移旧版书架数据 → {SHELF_FILE}')
    except Exception as e:
        print(f'⚠ 书架数据迁移失败: {e}')


def load_shelf():
    try:
        return json.loads(SHELF_FILE.read_text(encoding='utf-8'))
    except Exception:
        return []


def save_shelf(data):
    SHELF_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


# ════════════════════════════════════════════════════════════════
# Flask 路由
# ════════════════════════════════════════════════════════════════

app = Flask(__name__,
            template_folder=str(resource_path('templates')),
            static_folder=str(resource_path('static')))

# 开发模式：禁用静态文件缓存，每次加载最新版本
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['TEMPLATES_AUTO_RELOAD'] = True


@app.after_request
def _add_no_cache_headers(response):
    """强制不缓存 HTML/JS/CSS，确保修改后浏览器立即加载最新版本"""
    if request.path.endswith(('.html', '.js', '.css')) or request.path == '/':
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/sources')
def api_sources():
    type_labels = {0: '小说', 1: '听书', 2: '漫画', 3: '文件', 4: '影视'}
    from sources.json_api import JsonApiSource
    from sources.js import JsSource
    sources = []
    for url, src in mgr.sources.items():
        h = mgr.health.get(url, {})
        stype = getattr(src, 'source_type', 0)
        status = h.get('status')
        if status is None:
            status = 'ok' if h.get('ok') else ('dead' if h.get('ok') is False else None)
        flag = mgr._flagged.get(url, {})
        sources.append({
            'url': url,
            'name': src.name,
            'group': src.group,
            'enabled': url in mgr.enabled,
            'type': 'js' if isinstance(src, JsSource) else ('json' if isinstance(src, JsonApiSource) else 'css'),
            'source_type': stype,
            'category': type_labels.get(stype, f'类型{stype}'),
            'healthy': h.get('ok'),
            'status': status,
            'latency': h.get('latency', 0),
            'tested': h.get('tested', False),
            'flagged': bool(flag),
            'flag_notes': flag.get('notes', ''),
        })
    return jsonify(sources)


@app.route('/api/health_check', methods=['POST'])
def api_health_check():
    result = mgr.run_health_check()
    return jsonify(result)


@app.route('/api/toggle_source', methods=['POST'])
def api_toggle():
    url = request.json.get('url', '')
    if url in mgr.enabled:
        mgr.enabled.discard(url)
    elif url in mgr.sources:
        mgr.enabled.add(url)
    return jsonify(ok=True)


@app.route('/api/flag_source', methods=['POST'])
def api_flag_source():
    url = request.json.get('url', '')
    notes = request.json.get('notes', '')
    if not url or url not in mgr.sources:
        return jsonify(error='源未找到'), 404
    flagged = mgr.flag_source(url, notes)
    return jsonify(ok=True, flagged=flagged, url=url)


@app.route('/api/search')
def api_search():
    kw = request.args.get('q', '').strip()
    if not kw:
        return jsonify([])
    page = int(request.args.get('page', 1))
    src_filter = request.args.get('source', '')
    stype_raw = request.args.get('type', '')
    source_type = None
    if stype_raw != '':
        try:
            source_type = int(stype_raw)
        except (ValueError, TypeError):
            source_type = None
    try:
        results = mgr.search(kw, page, src_filter or None, source_type=source_type)
        return jsonify(results)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(error=str(e)), 500


@app.route('/api/detail')
def api_detail():
    source_url = request.args.get('source', '')
    book_url = request.args.get('url', '')
    src = mgr.get_source(source_url)
    if not src:
        return jsonify(error='源未找到'), 404
    search_data = {}
    for k in ['name', 'author', 'cover', 'intro']:
        v = request.args.get(k, '')
        if v:
            search_data[k] = v
    book = src.detail(book_url, search_data)
    book['source_url'] = source_url
    book['book_url'] = book_url
    stype = getattr(src, 'source_type', 0)
    book['source_type'] = stype
    # 所有类型：章节为空时附加诊断信息
    if not book.get('chapters'):
        raw_diag = book.pop('_detail_diag', None) or getattr(src, '_last_detail_diag', None)
        type_label = {0: '小说', 1: '听书', 2: '漫画', 3: '文件', 4: '影视'}.get(stype, f'类型{stype}')
        diag = {
            'level': 'detail',
            'warnings': [f'{type_label}源「{src.name}」返回 0 章节 — 可能反爬拦截或源配置失效'],
            'source_name': src.name,
            'source_type': stype,
            'source_group': getattr(src, 'group', ''),
        }
        if raw_diag:
            diag['fetch_ok'] = raw_diag.get('fetch_ok')
            diag['data_type'] = raw_diag.get('data_type', '')
            diag['data_sample'] = (raw_diag.get('data_sample') or '')[:250]
            diag['chapter_list_rule'] = raw_diag.get('chapter_list_rule', '')
            diag['rule_match'] = raw_diag.get('rule_match')
            diag['fetch_error'] = raw_diag.get('fetch_error', '')
            ab = raw_diag.get('anti_bot')
            if ab and ab.get('blocked'):
                diag['anti_bot'] = {
                    'block_type': ab.get('block_type'),
                    'evidence': (ab.get('evidence') or '')[:150],
                    'suggested_fix': ab.get('suggested_fix'),
                }
        book['diagnostics'] = diag
    return jsonify(book)


@app.route('/api/chapter')
def api_chapter():
    source_url = request.args.get('source', '')
    ch_url = request.args.get('url', '')
    src = mgr.get_source(source_url)
    if not src:
        return jsonify(error='源未找到'), 404
    stype = getattr(src, 'source_type', 0)
    # 漫画：返回图片列表 + 诊断信息
    if stype == 2:
        try:
            imgs = src.chapter_images(ch_url)
            if isinstance(imgs, dict):
                return jsonify(content_type='comic', images=imgs['images'],
                               source_url=source_url, count=len(imgs['images']),
                               diagnostics=imgs['diagnostics'])
            else:
                return jsonify(content_type='comic', images=imgs,
                               source_url=source_url, count=len(imgs))
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify(error=str(e), content_type='comic',
                           source_url=source_url, ch_url=ch_url), 500
    # 默认：文本
    try:
        content = src.chapter_content(ch_url)
        # 诊断信息
        diag = None
        raw_content_rule = getattr(src, 'content_r', {}).get('content', '')
        # HTML 反爬页检测
        if content and (content.strip().startswith('<!DOCTYPE') or content.strip().startswith('<html')):
            diag = {
                'reason': '源站返回了 HTML 页面（可能为反爬拦截或 CloudFlare 挑战）',
                'raw_rule': raw_content_rule,
                'returned_as_content': content[:200],
                'ch_url': ch_url,
            }
            content = ''
        elif content and (content.startswith('$.') or '<js>' in content):
            diag = {
                'reason': '规则未解析 — 源站返回格式可能与规则不匹配',
                'raw_rule': raw_content_rule,
                'returned_as_content': content[:200],
                'ch_url': ch_url,
            }
            content = ''
        elif not content or not content.strip():
            diag = {
                'reason': '内容为空 — 可能反爬、源站限制或章节不存在',
                'raw_rule': raw_content_rule,
                'ch_url': ch_url,
            }
        elif len(content) < 80:
            diag = {
                'reason': '内容异常短（' + str(len(content)) + ' 字符）— 可能为错误页或反爬拦截',
                'raw_rule': raw_content_rule,
                'returned_as_content': content[:200],
                'ch_url': ch_url,
            }
        return jsonify(content_type='text', content=content, diagnostics=diag)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify(error=str(e), content_type='text', ch_url=ch_url), 500


@app.route('/api/proxy')
def api_proxy():
    """图片/资源代理 —— 伪造 Referer 绕过防盗链，屏蔽私有 IP 防 SSRF"""
    url = request.args.get('url', '')
    if not url or not url.startswith('http'):
        return 'Invalid URL', 400
    try:
        hostname = __import__('urllib.parse', fromlist=['urlparse']).urlparse(url).hostname
        if hostname:
            ip = socket.gethostbyname(hostname)
            if ipaddress.ip_address(ip).is_private:
                return 'Blocked: private IP', 403
    except Exception:
        pass
    referer = request.args.get('referer', '') or request.args.get('source', '') or url
    # 全面克隆 session 头部（含 Accept-Encoding/Cookie 等 CDN 可能需要的字段）
    headers = dict(session.headers)
    headers['Referer'] = referer
    headers['Accept'] = 'image/webp,image/apng,image/*,*/*;q=0.8'

    import time as _time
    last_err = ''
    for attempt in range(2):
        try:
            resp = session.get(url, headers=headers, timeout=15, stream=True, verify=False)
            content_type = resp.headers.get('Content-Type', 'image/jpeg')
            data = resp.content[:5 * 1024 * 1024]
            return Response(data, content_type=content_type,
                            headers={'Cache-Control': 'public, max-age=3600',
                                     'Access-Control-Allow-Origin': '*'})
        except Exception as e:
            last_err = str(e)
            if attempt == 0:
                _time.sleep(0.5)  # 短暂等待后重试一次
    return f'Fetch failed: {last_err}', 502


@app.route('/api/shelf')
def api_shelf_list():
    return jsonify(load_shelf())


@app.route('/api/shelf', methods=['POST'])
def api_shelf_add():
    book = request.json
    shelf = load_shelf()
    key = f"{book.get('source_url','')}|{book.get('book_url','')}"
    for i, b in enumerate(shelf):
        if f"{b.get('source_url','')}|{b.get('book_url','')}" == key:
            shelf.pop(i)
            save_shelf(shelf)
            return jsonify(ok=True, action='removed')
    shelf.insert(0, {
        'name': book.get('name', ''),
        'author': book.get('author', ''),
        'cover': book.get('cover', ''),
        'book_url': book.get('book_url', ''),
        'source_url': book.get('source_url', ''),
        'source_name': book.get('source_name', ''),
    })
    save_shelf(shelf)
    return jsonify(ok=True, action='added')


if __name__ == '__main__':
    import threading, webbrowser, socket as _sk

    def _pick_port(preferred=5000, max_tries=20):
        """寻找可用端口：先试 preferred，被占就 +1 往后找"""
        for off in range(max_tries):
            port = preferred + off
            with _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM) as s:
                try:
                    s.bind(('127.0.0.1', port))
                    return port
                except OSError:
                    continue
        return preferred  # 实在不行还回原值，让 Flask 自己报错

    port = _pick_port(5000)
    url = f'http://localhost:{port}'

    # 是否在 PyInstaller 打包后的环境里运行
    _frozen = getattr(sys, 'frozen', False)

    print('╔══════════════════════════════════════╗')
    print('║   阅境 · 本地小说聚合阅读器           ║')
    print(f'║   {url:<35}║')
    print('╚══════════════════════════════════════╝')

    # 打包版自动打开浏览器；开发模式按需手动打开
    if _frozen:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    # 打包版禁用 debug + reloader（reloader 会启第二个进程，与 Node worker 冲突）
    app.run(
        host='127.0.0.1' if _frozen else '0.0.0.0',
        port=port,
        debug=not _frozen,
        use_reloader=not _frozen,
    )
