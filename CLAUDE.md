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

- **Python 3** + Flask (backend, `app.py`)
- **Node.js** (for executing Legado `<js>` / `@js:` rules via `js_runner.js`)
- No database — bookshelf state is persisted to `shelf.json`
- Virtual env at `.venv/`; dependencies: `flask`, `requests`, `beautifulsoup4`, `lxml`

## Architecture

`app.py` is a single-file backend (~1540 lines) containing three source-type classes, a source manager, utility functions, and Flask routes. No ORM, no migrations, no separate module structure. The frontend is split across `templates/index.html` (skeleton) and `static/` (4 CSS + 4 JS files).

### Source abstraction (3 types)

Every book source parsed from the Legado JSON gets one of three classes based on its search/rule structure:

| Class | When used | Data format |
|---|---|---|
| `JsonApiSource` | `ruleSearch.bookList` starts with `$`, `[`, or `[*]` | JSON API responses |
| `CssSource` | `ruleSearch.bookList` is a CSS selector (everything else) | HTML pages parsed with BeautifulSoup |
| `JsSource` | `ruleSearch.bookList` or `searchUrl` starts with `@js` | URL constructed by executing JS in Node.js, then JSON/HTML |

All three implement the same interface: `search(kw, page)`, `detail(book_url, search_data)`, `chapter_content(ch_url)`.

Each source class receives `sources_map` (the full dict of all raw source JSONs keyed by URL) at construction — this allows cross-source JS library lookups (`jsLib` resolution).

### Two JS execution paths

- **`run_legado_js()`** — for inline `<js>...</js>` transforms in rules. Takes code + a result value, returns the transformed string. Called via `subprocess.run` with a 20s timeout.
- **`run_js()`** — for `@js:` block-level code (mainly `JsSource`). Takes code + key/page/sourceUrl, returns a full JSON result dict (may include `store` updates for session state). Called via `subprocess.run` with a 30s timeout.

Both invoke `js_runner.js` via `node` on stdin/stdout. On Windows, `CREATE_NO_WINDOW` suppresses console flashes.

### Legado rule DSL

Book source rules use a mini-language from the Legado (开源阅读) Android app. The key rule format:

```
$.path.to.field <js>transformCode</js> https://tpl.com/{{result}}
```

- **`$.path`** — JSONPath-style extraction (parsed by `_split_rule()` / `_resolve_rule()`)
- **`<js>...</js>`** — inline JavaScript transform executed via Node.js subprocess
- **`@js:`** — block-level JS (search URL generation for `JsSource`)
- **`{{...}}`** — template interpolation with `$.path` lookups and `{{baseUrl.match(/regex/)[n]}}` patterns
- **`##`** — trailing discard markers (regex find-replace: `##pattern##replacement`)
- **`@get:{...}`** — HTTP method/header overrides (stripped during rule parsing)
- **`||`** — fallback separator: try each alternative in order until one returns a value

### CSS selector conversion (`css_conv()`)

Legado uses a non-standard CSS syntax that is normalized to standard CSS:
- `@` → ` > ` (descendant combinator)
- `class.x` → `.x`, `id.x` → `#x`
- `!N` → `:nth-child(n+N+1)` (1-based offset)
- `!0` suffix stripped, `tag` prefix stripped
- Suffixes like `@text`, `@src`, `@href`, `@html` stripped from selector end

### JS execution (`js_runner.js`)

Receives JSON on stdin, returns JSON on stdout. Simulates Legado's Android JS environment:
- `java.*` API (ajax via curl, md5, base64, HMAC, DES, UUID, storage via put/get)
- `source.*` API (source key, headers, login, per-source storage)
- `cookie.*` API (stubs)
- `Packages.*` (Android SDK stubs)
- The last line of JS code is automatically wrapped in `return` so expression-style Legado snippets work

### SourceManager

Loads book sources from JSON on startup, classifies them into the three source types, and maintains an `enabled` set. Key behaviors:

- **Tiered search**: `search()` prioritizes JSON API sources, then JS sources (capped at 30), then CSS sources (capped at 20), hard limit 120 sources per query.
- **Early return**: if ≥10 results arrive within 3s, returns immediately. Also returns at ≥30 results or when the timeout (4s + 4s buffer) is reached.
- **Dedup**: results are deduplicated by `(name, author)` with non-CJK/alphanumeric stripped.
- **Health check**: `run_health_check()` pings unique domains (not per-source) via HEAD/GET with a 30-worker thread pool. Runs in a background thread, uses a separate `requests.Session` to avoid disrupting user requests. Results stored in `mgr.health`.

### Routes

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serve the reader UI |
| `/api/sources` | GET | List all loaded sources with enable state + health |
| `/api/health_check` | POST | Trigger source health check (returns immediately, runs async) |
| `/api/toggle_source` | POST | Toggle a source on/off |
| `/api/search?q=&page=&source=` | GET | Fan-out search across sources |
| `/api/detail?source=&url=` | GET | Book detail + chapter list (accepts `name`, `author`, `cover`, `intro` as fallback) |
| `/api/chapter?source=&url=` | GET | Chapter text content |
| `/api/shelf` | GET | List bookshelf |
| `/api/shelf` | POST | Add/remove book from shelf (toggles by `source_url|book_url` key) |

### Frontend

`templates/index.html` is a 142-line HTML skeleton. Styles and logic are split into:

- **CSS**: `static/css/{base,components,reader,home}.css` — variables/reset/layout/theme, UI components, reader view, home/hero section
- **JS**: `static/js/{app,reader,shelf,settings}.js` — main logic & API calls, reader navigation, shelf management, reader settings (font/line-height/theme). Load order matters: `app.js` defines globals that the others extend.

Dark theme by default. Three-panel layout: sidebar (shelf + source manager), main content area (home/search/detail/reader views). Slide-up settings panel for reader customization. Vanilla JS — no framework.

## Key patterns

- **Response encoding**: `safe_json()` tries multiple encodings (charset → utf-8 → gbk → gb18030 → gb2312) with garbled-text detection (`_has_garbled()`) to handle Chinese novel sites that misreport encoding.
- **Session sharing**: A global `requests.Session` with `verify=False` (many sources use self-signed certs) and a consistent User-Agent is reused across all HTTP calls. Source-specific headers are merged via `_req_headers()`.
- **Partial results**: Search returns whatever sources responded within the timeout. Failed sources are silently skipped.
- **The bookshelf is a toggle**: POSTing the same `(source_url, book_url)` pair to `/api/shelf` removes it if present, adds it if absent.
- **No pagination in chapters**: Chapter lists are fetched in full and sorted by index.
- **CSS/JSON fallback**: `JsonApiSource.detail()` tries CSS parsing if JSON detail fails (and vice versa), since some sources are "mixed type" — JSON search but HTML detail pages.
- **POST chapter URLs**: Some sources encode POST requests in the chapter URL as `url,{"method":"POST","body":{...}}`. `chapter_content()` parses this format and issues POST requests when detected.
