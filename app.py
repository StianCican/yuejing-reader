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

mgr = SourceManager()

SHELF_FILE = Path(__file__).parent / 'shelf.json'


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

app = Flask(__name__)


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
    book['source_type'] = getattr(src, 'source_type', 0)
    return jsonify(book)


@app.route('/api/chapter')
def api_chapter():
    source_url = request.args.get('source', '')
    ch_url = request.args.get('url', '')
    src = mgr.get_source(source_url)
    if not src:
        return jsonify(error='源未找到'), 404
    stype = getattr(src, 'source_type', 0)
    # 漫画：返回图片列表
    if stype == 2:
        imgs = src.chapter_images(ch_url)
        return jsonify(content_type='comic', images=imgs,
                       source_url=source_url, count=len(imgs))
    # 默认：文本
    content = src.chapter_content(ch_url)
    return jsonify(content_type='text', content=content)


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
    headers = {
        'Referer': referer,
        'User-Agent': session.headers.get('User-Agent', ''),
        'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
    }
    try:
        resp = session.get(url, headers=headers, timeout=10, stream=True, verify=False)
        content_type = resp.headers.get('Content-Type', 'image/jpeg')
        data = resp.content[:5 * 1024 * 1024]
        return Response(data, content_type=content_type,
                        headers={'Cache-Control': 'public, max-age=3600',
                                 'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        return f'Fetch failed: {e}', 502


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
    print('╔══════════════════════════════════════╗')
    print('║   本地小说聚合阅读器                  ║')
    print('║   http://localhost:5000               ║')
    print('╚══════════════════════════════════════╝')
    app.run(host='0.0.0.0', port=5000, debug=False)
