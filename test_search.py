"""Random test of book source search and chapter fetching"""
import urllib.request
import urllib.parse
import json
import sys
import time

BASE = "http://localhost:5000"

def search(kw):
    url = f"{BASE}/api/search?q={urllib.parse.quote(kw)}"
    try:
        resp = urllib.request.urlopen(url, timeout=20)
        return json.loads(resp.read())
    except Exception as e:
        return []

def detail(source_url, book_url, name="", author="", cover="", intro=""):
    params = urllib.parse.urlencode({
        'source': source_url, 'url': book_url,
        'name': name, 'author': author, 'cover': cover, 'intro': intro
    })
    url = f"{BASE}/api/detail?{params}"
    try:
        resp = urllib.request.urlopen(url, timeout=15)
        return json.loads(resp.read())
    except Exception as e:
        return {'error': str(e)}

# Test keywords from different genres
keywords = [
    "斗罗大陆", "完美世界", "盗墓笔记", "凡人修仙传",
    "遮天", "诡秘之主", "雪中悍刀行", "大奉打更人",
    "剑来", "夜的命名术", "深空彼岸", "星门",
]

print("=" * 80)
print("  Book Source Adaptation Test Report")
print("=" * 80)

total_ok = 0
total_fail = 0
total_timeout = 0

for kw in keywords:
    results = search(kw)
    print(f"\n{'-'*60}")
    print(f"[SEARCH] [{kw}] -> {len(results)} results")

    if not results:
        total_fail += 1
        print("   [X] No results found")
        continue

    # Test detail for first 3 results
    ok_count = 0
    for i, r in enumerate(results[:3]):
        name = r.get('name', '?')
        src_name = r.get('source_name', '?')
        src_url = r['source_url']
        book_url = r['book_url']

        d = detail(src_url, book_url,
                   r.get('name',''), r.get('author',''),
                   r.get('cover',''), r.get('intro',''))

        if 'error' in d:
            err = d['error']
            print(f"   [{i+1}] {name[:25]} @{src_name[:18]} -> FAIL: {err}")
            if 'timed out' in err.lower():
                total_timeout += 1
            else:
                total_fail += 1
        else:
            ch_count = len(d.get('chapters', []))
            if ch_count > 0:
                ok_count += 1
                total_ok += 1
                first_ch = d['chapters'][0]['name'] if d['chapters'] else '?'
                print(f"   [{i+1}] {name[:25]} @{src_name[:18]} -> OK: {ch_count} chapters (first: {first_ch[:25]})")
            else:
                total_fail += 1
                print(f"   [{i+1}] {name[:25]} @{src_name[:18]} -> 0 chapters (source type mismatch)")

    if ok_count == 0:
        # Try deeper into results
        for i, r in enumerate(results[3:6], 4):
            name = r.get('name', '?')
            src_name = r.get('source_name', '?')
            d = detail(r['source_url'], r['book_url'],
                       r.get('name',''), r.get('author',''),
                       r.get('cover',''), r.get('intro',''))
            if 'error' not in d and len(d.get('chapters',[])) > 0:
                total_ok += 1
                ch_count = len(d['chapters'])
                first_ch = d['chapters'][0]['name'] if d['chapters'] else '?'
                print(f"   [{i}] {name[:25]} @{src_name[:18]} -> OK: {ch_count} chapters (first: {first_ch[:25]})")
                ok_count += 1
                break

print(f"\n{'='*80}")
print(f"  Summary: OK={total_ok} | 0-ch={total_fail} | Timeout={total_timeout}")
if total_ok + total_fail + total_timeout > 0:
    rate = total_ok * 100 / (total_ok + total_fail + total_timeout)
    print(f"  Success rate: {total_ok}/{total_ok+total_fail+total_timeout} = {rate:.1f}%")
print("=" * 80)
