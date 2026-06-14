"""SourceManager — 书源加载、搜索、健康检测"""
import json, re, sys, os, time, threading, random
from concurrent.futures import ThreadPoolExecutor, wait, as_completed
from urllib.parse import urlparse
import requests as _req

from .json_api import JsonApiSource
from .css import CssSource
from .js import JsSource
from utils.paths import (
    source_status_file, flagged_sources_file, book_sources_file,
    user_data_path, resource_path,
)


class SourceManager:
    def __init__(self):
        self.sources = {}
        self.enabled = set()
        self.health = {}
        self._flagged = {}
        self._health_running = False
        # 用户可写数据 —— 不再写在项目根
        self._status_file = str(source_status_file())
        self._flagged_file = str(flagged_sources_file())
        # 旧版数据自动迁移：项目根的两份 JSON 第一次启动搬到用户目录
        self._migrate_legacy_data()
        self._fail_count = {}
        self.type_index = {0: [], 1: [], 2: [], 3: [], 4: []}
        self._load()
        self._restore_health()
        self._restore_flagged()

    def _migrate_legacy_data(self):
        """把项目根历史遗留的 source_status.json / flagged_sources.json 挪到用户目录"""
        from pathlib import Path
        proj_root = Path(__file__).resolve().parent.parent
        for legacy_name, target_path in (
            ('source_status.json', self._status_file),
            ('flagged_sources.json', self._flagged_file),
        ):
            legacy = proj_root / legacy_name
            target = Path(target_path)
            if legacy.exists() and not target.exists():
                try:
                    target.write_text(legacy.read_text(encoding='utf-8'), encoding='utf-8')
                    print(f'📦 已迁移旧版数据 {legacy_name} → {target}')
                except Exception as e:
                    print(f'⚠ 迁移 {legacy_name} 失败: {e}')

    def _restore_health(self):
        try:
            status_path = os.path.abspath(self._status_file)
            if os.path.exists(status_path):
                with open(status_path, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                restored = 0
                for url, data in saved.items():
                    if url in self.sources:
                        self.health[url] = data
                        restored += 1
                if restored:
                    ok = sum(1 for h in self.health.values() if h.get('status') == 'ok')
                    dead = sum(1 for h in self.health.values() if h.get('status') == 'dead')
                    print(f'📋 从文件恢复 {restored} 个源状态（{ok} ✓ / {dead} ✗）')
        except Exception as e:
            print(f'⚠ 恢复健康数据失败: {e}')

    def _restore_flagged(self):
        try:
            flag_path = os.path.abspath(self._flagged_file)
            if os.path.exists(flag_path):
                with open(flag_path, 'r', encoding='utf-8') as f:
                    self._flagged = json.load(f)
                if self._flagged:
                    print(f'🚩 已恢复 {len(self._flagged)} 个标记源')
        except Exception as e:
            print(f'⚠ 恢复标记数据失败: {e}')

    def _save_flagged(self):
        try:
            with open(os.path.abspath(self._flagged_file), 'w', encoding='utf-8') as f:
                json.dump(self._flagged, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'⚠ 保存标记数据失败: {e}')

    def flag_source(self, url, notes=''):
        import time as _time
        if url in self._flagged:
            del self._flagged[url]
            self._save_flagged()
            return False
        else:
            self._flagged[url] = {'notes': notes.strip() or '', 'timestamp': _time.time()}
            self._save_flagged()
            return True

    def _save_health(self):
        try:
            with open(os.path.abspath(self._status_file), 'w', encoding='utf-8') as f:
                json.dump(self.health, f, ensure_ascii=False, indent=1)
        except Exception as e:
            print(f'⚠ 保存健康数据失败: {e}')

    def run_health_check(self):
        if self._health_running:
            return {'status': 'running'}
        self._health_running = True

        hs = _req.Session()
        hs.verify = False
        from utils.http import session
        hs.headers.update(session.headers)
        hs.timeout = 3
        test_keywords = ['玄幻', '系统', '穿越', '都市', '仙侠', '重生']
        targets = list(self.sources.values())

        domains = {}
        for src in targets:
            hb = getattr(src, 'http_base', src.base)
            if hb.startswith('http'):
                try:
                    d = urlparse(hb).netloc
                    if d not in domains:
                        domains[d] = hb
                except Exception:
                    pass

        def _ping(domain, url):
            try:
                t0 = time.time()
                resp = hs.head(url, timeout=3, allow_redirects=True)
                return (domain, True, round(time.time() - t0, 2))
            except Exception:
                try:
                    t0 = time.time()
                    resp = hs.get(url, timeout=3, stream=True)
                    resp.close()
                    return (domain, True, round(time.time() - t0, 2))
                except Exception:
                    return (domain, False, 0)

        def _test_search(src):
            kw = random.choice(test_keywords)
            try:
                results = src.search(kw, 1)
                return len(results) > 0
            except Exception:
                return False

        def _run():
            healthy_domains = set()
            print(f'🔍 阶段1: 检测 {len(domains)} 个唯一域名...')
            with ThreadPoolExecutor(max_workers=30) as pool:
                futs = [pool.submit(_ping, d, u) for d, u in domains.items()]
                try:
                    for fut in as_completed(futs, timeout=12):
                        try:
                            d, ok, lat = fut.result()
                            if ok:
                                healthy_domains.add(d)
                        except Exception:
                            pass
                except Exception:
                    pass

            domain_status = {}
            ok_count = 0
            for src in targets:
                hb = getattr(src, 'http_base', src.base)
                if hb.startswith('http'):
                    try:
                        d = urlparse(hb).netloc
                        if d in healthy_domains:
                            domain_status[src.base] = 'partial'
                            ok_count += 1
                        else:
                            domain_status[src.base] = 'dead'
                    except Exception:
                        domain_status[src.base] = 'dead'
                else:
                    domain_status[src.base] = 'dead'
            print(f'🔍 阶段1完成: {ok_count}/{len(targets)} 个源可达')

            reachable = [src for src in targets if domain_status.get(src.base) == 'partial' and src.base in self.enabled]
            random.shuffle(reachable)
            sample_size = min(60, len(reachable))
            test_sample = reachable[:sample_size]
            search_ok = set()
            if test_sample:
                print(f'🔍 阶段2: 对 {len(test_sample)} 个源做搜索测试...')
                with ThreadPoolExecutor(max_workers=10) as pool:
                    futs = {pool.submit(_test_search, src): src for src in test_sample}
                    try:
                        for fut in as_completed(futs, timeout=30):
                            src = futs[fut]
                            try:
                                if fut.result():
                                    search_ok.add(src.base)
                            except Exception:
                                pass
                    except Exception:
                        pass
                print(f'🔍 阶段2完成: {len(search_ok)}/{len(test_sample)} 个源搜索有结果')

            for src in targets:
                if src.base in search_ok:
                    self.health[src.base] = {'status': 'ok', 'latency': 0, 'error': '', 'tested': True}
                elif domain_status.get(src.base) == 'partial':
                    self.health[src.base] = {'status': 'partial', 'latency': 0, 'error': '域名可达，搜索测试未覆盖或无结果', 'tested': src.base in {s.base for s in test_sample}}
                else:
                    self.health[src.base] = {'status': 'dead', 'latency': 0, 'error': '域名不可达', 'tested': True}

            self._save_health()
            status_counts = {'ok': 0, 'partial': 0, 'dead': 0}
            for h in self.health.values():
                s = h.get('status', 'dead')
                status_counts[s] = status_counts.get(s, 0) + 1
            print(f'🔍 健康检测完成: {status_counts["ok"]}✓ / {status_counts["partial"]}~ / {status_counts["dead"]}✗（已保存）')
            self._health_running = False

        threading.Thread(target=_run, daemon=True).start()
        return {'status': 'started', 'domains': len(domains)}

    def _load(self):
        # 解析书源 JSON 路径，优先级：
        #   1. 命令行参数 sys.argv[1]
        #   2. 用户数据目录 book_sources.json（首次启动会从内置 default_sources.json 拷贝）
        #   3. 历史硬编码路径（开发兜底，分发版不会用到）
        if len(sys.argv) > 1:
            src_path = sys.argv[1]
        else:
            user_book_sources = book_sources_file()
            if user_book_sources.exists():
                src_path = str(user_book_sources)
            else:
                # 兜底：项目内置 default_sources.json（开发模式 + 首次启动）
                from utils.paths import resource_path
                src_path = str(resource_path('default_sources.json'))
        try:
            with open(src_path, encoding='utf-8') as f:
                data = json.load(f)
            print(f'📚 书源文件: {src_path}')
        except Exception as e:
            print(f'⚠ 书源加载失败: {e}')
            data = []
        all_src = {}
        for s in data:
            url = s.get('bookSourceUrl', '').rstrip('/')
            if '##' in url:
                url = url.split('##')[0].rstrip('/')
            s['bookSourceUrl'] = url
            search_url = s.get('searchUrl', '')
            has_rules = bool(s.get('ruleSearch', {}).get('bookList', ''))
            if url and (search_url or has_rules):
                all_src[url] = s
        json_count, css_count, js_count = 0, 0, 0
        self.type_index = {0: [], 1: [], 2: [], 3: [], 4: []}
        for url, s in all_src.items():
            bl = s.get('ruleSearch', {}).get('bookList', '')
            search_url = s.get('searchUrl', '').strip()
            is_js = bl.startswith('@js') or search_url.startswith('@js')
            if is_js:
                src = JsSource(s, all_src)
                js_count += 1
            elif not bl:
                continue
            else:
                is_json = (bl.startswith('[') or bl.startswith('$') or bl == '[*]' or bl == '$')
                if is_json:
                    src = JsonApiSource(s, all_src)
                    json_count += 1
                else:
                    src = CssSource(s, all_src)
                    css_count += 1
            self.sources[url] = src
            self.enabled.add(url)
            stype = getattr(src, 'source_type', 0)
            if stype not in self.type_index:
                self.type_index[stype] = []
            self.type_index[stype].append(url)
        type_stats = ' | '.join(f'{["小说","听书","漫画","文件","影视"][t] if t<5 else f"类型{t}"}:{len(self.type_index[t])}'
                                 for t in sorted(self.type_index) if self.type_index[t])
        print(f'✓ 已加载 {len(self.sources)} 个源（JSON API: {json_count}, CSS: {css_count}, JS: {js_count}）')
        print(f'  分类: {type_stats}')

    def search(self, kw, page=1, source_filter=None, source_type=None,
               max_workers=50, search_timeout=4):
        # 重置变量存储（避免跨搜索泄漏）
        from rules.variables import reset_vars
        reset_vars()

        # 类型过滤
        if source_type is not None:
            type_urls = set(self.type_index.get(source_type, []))
            targets = [self.sources[u] for u in self.enabled if u in self.sources and u in type_urls]
        else:
            targets = [self.sources[u] for u in self.enabled if u in self.sources]

        if source_filter:
            targets = [s for s in targets if source_filter in s.name or source_filter in s.base]

        # 过滤已知失效源
        dead_urls = {url for url, h in self.health.items() if h.get('status') == 'dead'}
        live = [s for s in targets if s.base not in dead_urls]
        if len(live) >= 10:
            targets = live

        # 分层排序
        json_targets = [s for s in targets if isinstance(s, JsonApiSource)]
        js_targets   = [s for s in targets if isinstance(s, JsSource)]
        css_targets  = [s for s in targets if isinstance(s, CssSource)]

        ok_urls = {url for url, h in self.health.items() if h.get('status') == 'ok'}
        def _sort_key(src):
            s = self.health.get(src.base, {}).get('status')
            return 2 if s == 'ok' else (1 if s is None else 0)
        json_targets.sort(key=_sort_key, reverse=True)
        js_targets.sort(key=_sort_key, reverse=True)
        css_targets.sort(key=_sort_key, reverse=True)

        tiered = json_targets + js_targets + css_targets

        all_results = []
        seen = set()

        def _dedup_key(r):
            name = re.sub(r'[^一-鿿\w]', '', r.get('name', ''))
            author = re.sub(r'[^一-鿿\w]', '', r.get('author', ''))
            return f'{name}|{author}'.lower()

        def _do(src):
            try:
                results = src.search(kw, page)
                st = getattr(src, 'source_type', 0)
                for r in results:
                    r['source_type'] = st
                if results:
                    self._fail_count[src.base] = 0
                return results
            except Exception:
                # 指数退避：失败计数+1，≥5 次才标 dead
                self._fail_count[src.base] = self._fail_count.get(src.base, 0) + 1
                if self._fail_count[src.base] >= 5:
                    self.health[src.base] = {'status': 'dead', 'latency': 0, 'error': '连续搜索失败', 'tested': True}
                return []

        pool = ThreadPoolExecutor(max_workers=max_workers)
        try:
            futs = [pool.submit(_do, s) for s in tiered]
            deadline = time.time() + search_timeout + 2     # 硬截止
            min_search = time.time() + 0.8                  # 0.8s 后 ≥30 条就返回
            early_deadline = time.time() + 2                # 2s 后 ≥10 条就返回

            try:
                for fut in as_completed(futs, timeout=search_timeout + 6):
                    try:
                        results = fut.result()
                        for r in results:
                            key = _dedup_key(r)
                            if key not in seen:
                                seen.add(key)
                                all_results.append(r)
                    except Exception:
                        pass
                    now = time.time()
                    if now >= deadline:
                        break
                    if len(all_results) >= 30 and now >= min_search:
                        break
                    if len(all_results) >= 10 and now >= early_deadline:
                        break
            except TimeoutError:
                pass
        finally:
            pool.shutdown(wait=False)

        # 结果排序
        if all_results:
            kw_lower = kw.strip().lower()
            def _result_rank(r):
                score = 0
                name = r.get('name', '')
                if name.strip() == kw.strip():
                    score += 1000
                elif kw_lower in name.lower():
                    score += 500
                h = self.health.get(r.get('source_url', ''), {})
                hs = h.get('status')
                if hs == 'ok':
                    score += 200
                elif hs is None:
                    score += 100
                if source_type is None:
                    src = self.sources.get(r.get('source_url', ''))
                    if src and getattr(src, 'source_type', 0) == 0:
                        score += 50
                return -score
            all_results.sort(key=_result_rank)
            all_results = all_results[:100]

        print(f'[search] results={len(all_results)}, deduped, sources_scanned={len(tiered)}')
        return all_results

    def get_source(self, source_url):
        return self.sources.get(source_url)
