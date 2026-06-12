# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

A local novel aggregation reader — a Flask web app that loads **Legado-format book source JSON**, aggregates novel content from multiple websites, and presents it in a browser-based reading UI.

## How to run

```bash
cd D:\AI\novel-reader
.venv\Scripts\activate
python app.py [path-to-book-sources.json]
```

If no source file is given on the command line, it defaults to a hardcoded path. The app starts on `http://localhost:5000`.

To test the API without a browser:

```bash
curl -s "http://localhost:5000/api/search?q=<keyword>"
```

Ad-hoc test scripts (require the app to be running):

```bash
python test_search.py    # searches 12 keywords, checks detail+chapters
python test_sources.py   # iterates all enabled sources with random keywords
```

## Stack

- **Python 3** + Flask (backend)
- **PyMiniRacer** (V8 in-process) for simple `<js>inline</js>` transforms (~1ms)
- **Node.js** persistent worker (`runtime/js_worker.js`) for full `@js:` blocks with `java.ajax()` (~5ms)
- No database — bookshelf state is persisted to `shelf.json`
- Virtual env at `.venv/`; dependencies: `flask`, `requests`, `beautifulsoup4`, `lxml`, `py-mini-racer`

## Architecture

```
novel-reader/
├── app.py                  # Flask routes only (~228 lines)
├── sources/
│   ├── base.py             # BaseSource — shared logic for all source types
│   ├── json_api.py         # JsonApiSource — JSON API search sources
│   ├── css.py              # CssSource — HTML/CSS selector sources
│   ├── js.py               # JsSource — @js: dynamic URL sources
│   └── manager.py          # SourceManager — loading, search fan-out, health checks
├── rules/
│   ├── parser.py           # Legado DSL: _split_rule, _resolve_rule, resolve_tpl, jpath
│   ├── extractors.py       # extract_val, extract_img, extract_link, parse_search_results
│   ├── css_conv.py         # Legado CSS → standard CSS selector conversion
│   └── variables.py        # @put/@get variable system (contextvars, thread-safe)
├── runtime/
│   ├── js_engine.py        # LegadoRuntime: SimpleRuntime (MiniRacer) + NodeWorker (persistent)
│   └── js_worker.js        # Node.js persistent worker (line-delimited JSON on stdin/stdout)
├── utils/
│   ├── http.py             # Global session, safe_json, _join_url, _parse_header, build_url
│   ├── text.py             # clean_text, _apply_replace_regex, _fallback_content
│   └── images.py           # Image URL extraction for comic chapters
├── static/
│   ├── css/
│   │   └── style.css       # Merged single-file CSS
│   └── js/
│       ├── app.js          # Global State object, search, source management, event delegation
│       ├── reader.js       # Text reader + comic router
│       ├── comic_reader.js # Comic scroll reader (lazy loading)
│       ├── shelf.js        # Bookshelf management
│       └── settings.js     # Reading settings panel
├── templates/
│   └── index.html
└── shelf.json
```

### Source abstraction (3 types)

Every book source parsed from the Legado JSON gets one of three classes, all inheriting from `BaseSource`:

| Class | When used | Data format |
|---|---|---|
| `JsonApiSource` | `ruleSearch.bookList` starts with `$`, `[`, or `[*]` | JSON API responses |
| `CssSource` | `ruleSearch.bookList` is a CSS selector (everything else) | HTML pages parsed with BeautifulSoup |
| `JsSource` | `ruleSearch.bookList` or `searchUrl` starts with `@js` | URL constructed by executing JS in Node.js, then JSON/HTML |

`BaseSource` provides the shared interface: `search(kw, page)`, `detail(book_url, search_data)`, `chapter_content(ch_url)`, `chapter_images(ch_url)`. Subclasses only implement `_fetch_raw(url)` → `(data, base_url)` and `_fetch_html(url)` → `BeautifulSoup`.

Each source class receives `sources_map` (the full dict of all raw source JSONs keyed by URL) at construction — this allows cross-source JS library lookups (`jsLib` resolution).

### Two JS execution paths

- **`SimpleRuntime` (PyMiniRacer/V8)** — for inline `<js>...</js>` transforms without ajax. In-process execution, ~1ms. Uses minimal shims (btoa/atob, java.put/get, source.put/get).
- **`NodeWorker` (persistent Node.js)** — for `@js:` block-level code that needs `java.ajax()`. Line-delimited JSON on stdin/stdout, ~5ms. Auto-restarts on crash or after 2000 requests.

Both are managed by `LegadoRuntime` in `runtime/js_engine.py`. `run_legado_js()` auto-detects which mode is needed by checking for ajax keywords in the code.

### Legado rule DSL

Book source rules use a mini-language from the Legado (开源阅读) Android app. The key rule format:

```
$.path.to.field <js>transformCode</js> https://tpl.com/{{result}}
```

- **`$.path`** — JSONPath-style extraction (parsed by `_split_rule()` / `_resolve_rule()`)
- **`<js>...</js>`** — inline JavaScript transform executed via PyMiniRacer or NodeWorker
- **`@js:`** — block-level JS (search URL generation for `JsSource`)
- **`{{...}}`** — template interpolation: `{{$.path}}` lookups, `{{baseUrl}}` (standalone or `.match(/regex/)[n]`), and `{{result}}` placeholders
- **`##`** — trailing discard markers (regex find-replace: `##pattern##replacement`)
- **`@get:{key}`** — variable retrieval: replaces `@get:{key}` with the stored value from `@put:{key:selector}`
- **`@put:{key:selector}`** — variable storage: extracts value via selector, stores in per-source variable dict
- **`@Header:{key:value,...}`** — inline custom HTTP headers: parsed from rule strings (esp. search URLs) and injected into requests
- **`||`** — fallback separator: try each alternative in order until one returns a value
- **`&&`** — chain separator: extract multiple selectors independently, join with spaces

### CSS selector conversion (`css_conv()`)

Legado uses a non-standard CSS syntax that is normalized to standard CSS:
- `@@` → ` ` (descendant space, processed before single `@`)
- `@` → ` > ` (direct child combinator)
- `class.x` → `.x`, `id.x` → `#x`, `tag.x` → `x`
- `!N` → `:nth-child(n+N+1)` (1-based offset), `!-N` → `:nth-last-child(N)`
- `:eq(N)` → `:nth-child(N+1)`, `:lt(N)` → `:nth-child(-n+N)`, `:gt(N)` → `:nth-child(n+N+2)`
- `<` parent selector → downgraded to space (BS4 limitation)
- `!0` suffix stripped, `tag` prefix stripped
- Suffixes like `@text`, `@src`, `@href`, `@html`, `@textNodes`, `@outerHtml`, `@innerHtml`, `@data`, `@all`, `@ownText`, `@attr` stripped from selector end

### JS execution (`runtime/js_worker.js`)

Persistent Node.js worker process. Receives JSON on stdin, returns JSON on stdout (line-delimited). Simulates Legado's Android JS environment:
- `java.*` API (ajax via curl, md5, base64, HMAC, DES, UUID, storage via put/get)
- `source.*` API (source key, headers, login, per-source storage)
- `cookie.*` API (stubs)
- `Packages.*` (Android SDK stubs)
- The last line of JS code is automatically wrapped in `return` so expression-style Legado snippets work
- Heartbeat auto-exit after 5 minutes idle

### SourceManager

Loads book sources from JSON on startup, classifies them into the three source types, and maintains an `enabled` set. Key behaviors:

- **Tiered search**: `search()` prioritizes JSON API sources, then JS sources, then CSS sources. Uses `concurrent.futures.wait()` + `FIRST_COMPLETED` pattern for incremental result collection.
- **Early return**: stops collecting at ≥30 results after minimum search time, or at timeout (5s + buffer).
- **Dedup**: results are deduplicated by `(name, author)` with non-CJK/alphanumeric stripped.
- **Fail counting**: exponential backoff — source is only marked dead after ≥5 consecutive failures (recovered from `source_status.json` on restart).
- **Variable isolation**: `contextvars.ContextVar` prevents variable leaks across threads in the search thread pool. `reset_vars()` is called at each search entry.
- **Health check**: `run_health_check()` pings unique domains (not per-source) via HEAD/GET with a 30-worker thread pool. Runs in a background thread, uses a separate `requests.Session`. Two-phase: domain ping → search test on a sample of 60 reachable sources. Results persisted to `source_status.json`.

### Routes

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serve the reader UI |
| `/api/sources` | GET | List all loaded sources with enable state + health |
| `/api/health_check` | POST | Trigger source health check (returns immediately, runs async) |
| `/api/toggle_source` | POST | Toggle a source on/off |
| `/api/flag_source` | POST | Flag/unflag a source with notes |
| `/api/search?q=&page=&source=&type=` | GET | Fan-out search across sources |
| `/api/detail?source=&url=` | GET | Book detail + chapter list (accepts `name`, `author`, `cover`, `intro` as fallback) |
| `/api/chapter?source=&url=` | GET | Chapter content — text for novels (stype 0), image list for comics (stype 2) |
| `/api/proxy?url=&referer=` | GET | Image proxy with Referer forgery + SSRF protection |
| `/api/shelf` | GET | List bookshelf |
| `/api/shelf` | POST | Add/remove book from shelf (toggles by `source_url|book_url` key) |

### Frontend

`templates/index.html` is the HTML skeleton. Styles in `static/css/style.css` (merged from base/comps/home/reader). JS in `static/js/`:

- **`app.js`** — `State` object (currentView, currentBook, chapters, shelf, sources, etc.), search logic, API calls, event delegation for book cards
- **`reader.js`** — text reader rendering, comic reader routing, keyboard navigation
- **`comic_reader.js`** — comic scroll reader with IntersectionObserver lazy loading
- **`shelf.js`** — shelf list rendering and management
- **`settings.js`** — font size, line height, theme cycling (dark/parchment/green)

Load order matters: `app.js` → `reader.js` → `comic_reader.js` → `shelf.js` → `settings.js`.

Dark theme by default. Three-panel layout: sidebar (shelf + source manager), main content area (home/search/detail/reader views). Slide-up settings panel for reader customization. Vanilla JS — no framework.

## Key patterns

- **Response encoding**: `safe_json()` tries multiple encodings (charset → utf-8 → gbk → gb18030 → gb2312) with garbled-text detection (`_has_garbled()`) to handle Chinese novel sites that misreport encoding. Returns `(data, text)` tuple.
- **Session sharing**: A global `requests.Session` with `verify=False` (many sources use self-signed certs) and a consistent User-Agent is reused across all HTTP calls. Source-specific headers are merged via `_req_headers()`.
- **Partial results**: Search returns whatever sources responded within the timeout. Failed sources are silently skipped.
- **The bookshelf is a toggle**: POSTing the same `(source_url, book_url)` pair to `/api/shelf` removes it if present, adds it if absent.
- **No pagination in chapters**: Chapter lists are fetched in full and sorted by index.
- **CSS/JSON fallback**: `BaseSource.detail()` tries CSS parsing if JSON detail fails (and vice versa), since some sources are "mixed type" — JSON search but HTML detail pages.
- **POST chapter URLs**: Some sources encode POST requests in the chapter URL as `url,{"method":"POST","body":{...}}`. `_parse_post_url()` in `utils/http.py` handles this.
- **Comic image pipeline**: `BaseSource.chapter_images()` → `_fetch_chapter_images()` → tries JSON arrays, HTML img tags, text regex, recursive JSON search. All image extraction utilities in `utils/images.py`.
