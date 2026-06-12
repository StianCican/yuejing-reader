"""Comprehensive source adaptation test"""
import urllib.request, urllib.parse, json, random, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE = 'http://localhost:5000'

# Get all sources
resp = urllib.request.urlopen(f'{BASE}/api/sources')
sources = json.loads(resp.read())
enabled = [s for s in sources if s.get('enabled')]

# Test keywords - common enough to find on most sources
keywords = ['凡人', '都市', '重生', '系统', '修仙']

def test_source(source, kw):
    """Test one source with one keyword, return (search_count, detail_success, chapter_count)"""
    name = source['name']
    stype = source['type']
    src_url = source['url']

    search_url = f'{BASE}/api/search?q={urllib.parse.quote(kw)}&source={urllib.parse.quote(src_url)}'
    try:
        resp = urllib.request.urlopen(search_url, timeout=8)
        results = json.loads(resp.read())
    except:
        return None

    if not results:
        return {'name': name, 'type': stype, 'search_ok': False, 'results': 0}

    # Test first result's detail
    r = results[0]
    params = urllib.parse.urlencode({
        'source': r['source_url'], 'url': r['book_url'],
        'name': r.get('name',''), 'author': r.get('author',''),
        'cover': r.get('cover',''), 'intro': r.get('intro','')
    })
    detail_url = f'{BASE}/api/detail?{params}'
    try:
        resp2 = urllib.request.urlopen(detail_url, timeout=10)
        detail = json.loads(resp2.read())
    except Exception as e:
        return {'name': name, 'type': stype, 'search_ok': True, 'results': len(results),
                'detail_ok': False, 'detail_error': str(e)[:60]}

    if 'error' in detail:
        return {'name': name, 'type': stype, 'search_ok': True, 'results': len(results),
                'detail_ok': False, 'detail_error': detail['error'][:60]}

    ch_count = len(detail.get('chapters', []))
    first_ch = detail['chapters'][0]['name'][:30] if detail.get('chapters') else ''
    return {'name': name, 'type': stype, 'search_ok': True, 'results': len(results),
            'detail_ok': True, 'chapters': ch_count, 'first_ch': first_ch}

# Pick random sources (different types)
random.seed(123)
# Pick 20 json, 20 css, all js (there are fewer)
json_sources = [s for s in enabled if s['type'] == 'json']
css_sources = [s for s in enabled if s['type'] == 'css']
js_sources = [s for s in enabled if s['type'] == 'js']

test_targets = (
    random.sample(json_sources, min(20, len(json_sources))) +
    random.sample(js_sources, min(15, len(js_sources))) +
    random.sample(css_sources, min(20, len(css_sources)))
)

print('=' * 70)
print('  SOURCE ADAPTATION TEST')
print(f'  Testing {len(test_targets)} sources ({len(json_sources)} json + {len(js_sources)} js + {len(css_sources)} css available)')
print('=' * 70)

results = []
for i, s in enumerate(test_targets):
    # Try each keyword until we get search results, or use all
    best = None
    for kw in keywords:
        r = test_source(s, kw)
        if r and r.get('search_ok'):
            best = r
            break
        elif r and not best:
            best = r

    if not best:
        best = {'name': s['name'], 'type': s['type'], 'search_ok': False, 'results': 0}

    results.append(best)

    if best.get('detail_ok') and best.get('chapters', 0) > 0:
        status = f'OK ({best["chapters"]}ch)'
    elif best.get('detail_ok'):
        status = '0ch'
    elif best.get('search_ok'):
        err = best.get('detail_error', '?')
        status = f'FAIL: {err[:30]}'
    else:
        status = 'NO RESULTS'

    print(f'  [{i+1:2d}] [{best["type"]:5s}] {best["name"][:30]:30s} -> {status}')

# Summary
ok = [r for r in results if r.get('detail_ok') and r.get('chapters', 0) > 0]
zero_ch = [r for r in results if r.get('detail_ok') and r.get('chapters', 0) == 0]
fail = [r for r in results if r.get('search_ok') and not r.get('detail_ok')]
no_results = [r for r in results if not r.get('search_ok')]

print()
print('=' * 70)
print('  SUMMARY')
print(f'  Sources tested: {len(results)}')
print(f'  Working (has chapters): {len(ok)}')
print(f'  0 chapters:            {len(zero_ch)}')
print(f'  Detail failed:         {len(fail)}')
print(f'  No search results:     {len(no_results)}')
print(f'  Success rate:          {len(ok)}/{len(results)} = {len(ok)*100/len(results):.1f}%')

# By type
for stype in ['json', 'js', 'css']:
    total_t = [r for r in results if r['type'] == stype]
    ok_t = [r for r in total_t if r.get('detail_ok') and r.get('chapters', 0) > 0]
    print(f'  [{stype}] {len(ok_t)}/{len(total_t)} working ({len(ok_t)*100/max(1,len(total_t)):.0f}%)')

# Show working sources
if ok:
    print()
    print('  Working sources:')
    for r in ok:
        print(f'    [{r["type"]}] {r["name"][:35]} -> {r["chapters"]} chapters (first: {r.get("first_ch","?")[:30]})')
elif zero_ch:
    print()
    print('  Sample 0-chapter detail errors:')
    for r in fail[:5]:
        print(f'    [{r["type"]}] {r["name"][:35]} -> {r.get("detail_error","?")}')
print('=' * 70)
